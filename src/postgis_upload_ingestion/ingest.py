"""Reads an uploaded file into a PostGIS table with GDAL, and describes what it found."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any

from . import columns as cols
from .coordinates import DEFAULT_COORDINATE_SYSTEMS, X_NAMES, Y_NAMES, CoordinateSystem, naming_advice, neither
from .problems import IngestionProblem, plain
from .sources import READERS, TABLES, Limits, readers_arguments, source_of

# GDAL reaches the network when a file asks for it (a GeoJSON "crs" of type "link" is fetched from its URL). During an
# upload it must not: its HTTP requests go to a proxy that does not exist and fail at once.
OFFLINE = {
    "GDAL_HTTP_PROXY": "127.0.0.1:9",
    "GDAL_HTTP_TIMEOUT": "2",
    "GDAL_HTTP_MAX_RETRY": "0",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".none",
}


@dataclass(frozen=True)
class Database:
    """The PostgreSQL database into which ogr2ogr writes. The password is passed to ogr2ogr in its environment, not on
    its command line, where other users of the machine could read it."""

    host: str
    dbname: str
    user: str
    password: str = ""
    port: int = 5432

    def target(self) -> tuple[str, dict[str, str]]:
        return (
            f"PG:host={self.host} port={self.port} dbname={self.dbname} user={self.user}",
            {**os.environ, **OFFLINE, "PGPASSWORD": self.password},
        )


@dataclass(frozen=True)
class IngestionResult:
    """What was found in the file."""

    record_count: int
    without_location: int
    #: The kinds of geometry of the records, as PostGIS names them ("POINT", "MULTIPOLYGON").
    geometry_types: tuple[str, ...]
    #: [west, south, east, north] in degrees, or None.
    extent: list[float] | None
    #: The profile of every column, with the keys that automatic-map-configuration reads.
    columns: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def geometry_type(self) -> str:
        """The kinds of geometry joined by commas: "MULTIPOINT, MULTIPOLYGON"."""
        return ", ".join(self.geometry_types)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature_count": self.record_count,
            "without_location": self.without_location,
            "geometry_type": self.geometry_type,
            "extent": self.extent,
            "columns": self.columns,
            "notes": self.notes,
        }


def _run(arguments: list[str], env: dict[str, str], limits: Limits) -> None:
    done = subprocess.run(arguments, env=env, capture_output=True, text=True, timeout=limits.timeout_seconds)
    if done.returncode != 0:
        raise plain(done.stderr)


def _first_layer(source: str, extension: str) -> str:
    from osgeo import gdal

    gdal.UseExceptions()
    for key, value in OFFLINE.items():
        gdal.SetConfigOption(key, value)
    try:
        opened = gdal.OpenEx(source, gdal.OF_VECTOR, allowed_drivers=list(READERS[extension]))
    except RuntimeError as error:
        raise plain(str(error)) from error
    finally:
        for key in OFFLINE:
            gdal.SetConfigOption(key, None)
    if opened is None or opened.GetLayerCount() == 0:
        raise IngestionProblem("unreadable", "The file could not be opened or is empty.")
    name = opened.GetLayer(0).GetName()
    if name.startswith("-"):
        # ogr2ogr would read the name as one of its options.
        raise IngestionProblem(
            "layer_name", f"The layer “{name}” cannot be read: its name starts with a hyphen. Rename it and upload again."
        )
    return name


def _points_from_columns(cursor: Any, schema: str, table: str, systems: tuple[CoordinateSystem, ...]) -> list[str]:
    """For a table: finds the two coordinate columns, recognises their coordinate system, and makes the points."""
    numeric = {name for name, kind in cols.columns_of(cursor, schema, table) if kind in cols.NUMERIC_TYPES}
    x = next((name for name in X_NAMES if name in numeric), None)
    y = next((name for name in Y_NAMES if name in numeric), None)
    if x is None or y is None:
        raise IngestionProblem(
            "no_coordinate_columns",
            f"The file has no numeric coordinate columns. Name them {naming_advice(systems)}, and upload the file again.",
        )
    full = cols.qualified(schema, table)
    cursor.execute(f"SELECT min({cols.quote(x)}), max({cols.quote(x)}), min({cols.quote(y)}), max({cols.quote(y)}) FROM {full}")
    west, east, south, north = cursor.fetchone()
    if west is None or south is None:
        raise IngestionProblem("empty_coordinates", f"The coordinate columns “{x}” and “{y}” contain no values.", x=x, y=y)
    system = next((s for s in systems if s.holds(west, south, east, north)), None)
    if system is None:
        raise IngestionProblem(
            "unknown_coordinates",
            f"The values of “{x}” and “{y}” are {neither(systems)}. Transform them to WGS 84 (EPSG:4326) and upload the file again.",
            x=x,
            y=y,
        )
    notes = []
    if system.epsg != 4326:
        notes.append(f"The coordinates were identified as {system.described} and transformed to WGS 84.")
    cursor.execute(f"ALTER TABLE {full} ADD COLUMN geom geometry(Point, 4326)")
    cursor.execute(
        f"UPDATE {full} SET geom = ST_Transform(ST_SetSRID(ST_MakePoint({cols.quote(x)}, {cols.quote(y)}), %s), 4326) "
        f"WHERE {cols.quote(x)} IS NOT NULL AND {cols.quote(y)} IS NOT NULL",
        [system.epsg],
    )
    cursor.execute(f"CREATE INDEX ON {full} USING gist (geom)")
    return notes


def ingest(
    path: str,
    connection: Any,
    database: Database,
    table: str,
    *,
    schema: str = "public",
    limits: Limits | None = None,
    coordinate_systems: tuple[CoordinateSystem, ...] = DEFAULT_COORDINATE_SYSTEMS,
) -> IngestionResult:
    """Reads the file at `path` into the table `schema.table` (replacing it) and returns what was found.

    `connection` is a DB-API connection to the same database as `database` (psycopg, or Django's `connection`), in
    autocommit mode or committed by the caller. A CSV, TSV, TXT or Excel file needs two coordinate columns; any other
    file keeps its geometry, transformed to WGS 84. Raises IngestionProblem with a message for the person who uploaded
    the file; the table may then exist in part, and the caller drops it (see drop).
    """
    limits = limits or Limits()
    source, extension = source_of(path, limits)
    target, env = database.target()
    notes: list[str] = []
    if extension == ".xlsx":
        layer = _first_layer(source, extension)
        converted = path + ".csv"
        _run(["ogr2ogr", *readers_arguments(extension), "-f", "CSV", converted, source, layer], env, limits)
        source, extension = converted, ".csv"
    layer = _first_layer(source, extension)
    common = [
        "ogr2ogr",
        *readers_arguments(extension),
        "-f",
        "PostgreSQL",
        target,
        source,
        layer,
        "-nln",
        f"{schema}.{table}",
        "-overwrite",
        "-lco",
        "FID=fid",
        "-lco",
        "LAUNDER=NO",
    ]
    is_table = extension in TABLES
    if is_table:
        _run([*common, "-oo", "EMPTY_STRING_AS_NULL=YES"], env, limits)
        reserved = frozenset({"fid"})  # a column of the file named "geom" is renamed; the points get that name
    else:
        _run([*common, "-lco", "GEOMETRY_NAME=geom", "-t_srs", "EPSG:4326", "-nlt", "PROMOTE_TO_MULTI", "-dim", "XY"], env, limits)
        reserved = frozenset({"fid", "geom"})
    with connection.cursor() as cursor:
        labels = cols.tidy_names(cursor, schema, table, reserved)
        notes += cols.set_types(cursor, schema, table, reserved | {"geom"})
        if is_table:
            notes += _points_from_columns(cursor, schema, table, coordinate_systems)
        full = cols.qualified(schema, table)
        cursor.execute(
            f"SELECT count(*), count(*) FILTER (WHERE geom IS NULL), "
            f"(SELECT array_agg(DISTINCT GeometryType(geom) ORDER BY GeometryType(geom)) FROM {full} WHERE geom IS NOT NULL), "
            f"ST_XMin(ST_Extent(geom)), ST_YMin(ST_Extent(geom)), ST_XMax(ST_Extent(geom)), ST_YMax(ST_Extent(geom)) FROM {full}"
        )
        total, missing, kinds, west, south, east, north = cursor.fetchone()
        if total == 0:
            raise IngestionProblem("no_records", "The file contains no records.")
        if limits.max_records is not None and total > limits.max_records:
            raise IngestionProblem(
                "too_many_records",
                f"The file contains {total:,} records; the limit is {limits.max_records:,}.",
                records=total,
                limit=limits.max_records,
            )
        if missing == total:
            raise IngestionProblem("no_geometry", "No record has a geometry, so nothing can be displayed on the map.")
        if missing:
            notes.append(f"{missing:,} of {total:,} records have no geometry and are not displayed on the map.")
        described = cols.describe(cursor, schema, table, labels)
    return IngestionResult(
        record_count=total,
        without_location=missing,
        geometry_types=tuple(kinds or ()),
        extent=[west, south, east, north] if west is not None else None,
        columns=described,
        notes=notes,
    )


def drop(connection: Any, table: str, *, schema: str = "public", pattern: str = r"[A-Za-z_][A-Za-z0-9_]*") -> None:
    """Drops the table `schema.table`, if its name matches `pattern` (a guard against dropping another table)."""
    if table and re.fullmatch(pattern, table):
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TABLE IF EXISTS {cols.qualified(schema, table)}")
