"""What GDAL is allowed to open: the reader of the kind that the name of the file says, and the file inside a ZIP."""

from __future__ import annotations

import os
import zipfile
from dataclasses import dataclass

from .problems import IngestionProblem

TABLES = frozenset({".csv", ".tsv", ".txt", ".xlsx"})
GIS_FILES = frozenset({".geojson", ".json", ".gpkg", ".kml", ".fgb", ".shp"})
SUPPORTED = TABLES | GIS_FILES | {".zip"}

# GDAL recognises a file by its content, not by its name. A file is therefore opened only by the reader of the kind that
# its name says; otherwise a file named "x.geojson" could be a virtual dataset (VRT) that reads other files of the server.
READERS: dict[str, tuple[str, ...]] = {
    ".csv": ("CSV",),
    ".tsv": ("CSV",),
    ".txt": ("CSV",),
    ".xlsx": ("XLSX",),
    ".geojson": ("GeoJSON",),
    ".json": ("GeoJSON", "ESRIJSON", "TopoJSON"),
    ".gpkg": ("GPKG",),
    ".kml": ("KML", "LIBKML"),
    ".fgb": ("FlatGeobuf",),
    ".shp": ("ESRI Shapefile",),
}

# The kinds looked for inside a ZIP archive, in this order.
IN_ARCHIVES = (".shp", ".gpkg", ".geojson", ".json", ".fgb", ".kml", ".csv", ".tsv", ".txt", ".xlsx")


@dataclass(frozen=True)
class Limits:
    """The limits of an upload."""

    #: The most records a file may have; None for no limit.
    max_records: int | None = None
    #: The most bytes that the files of a ZIP archive may have when unpacked, and the most files it may hold.
    max_unpacked_bytes: int = 2 * 1024**3
    max_archive_entries: int = 2000
    #: The highest ratio of unpacked to packed size of a file in a ZIP archive. Ordinary data packs up to a few hundred
    #: times (a sparse table); an archive made to fill the disk packs a thousand times or more.
    max_compression_ratio: float = 1000.0
    #: The longest time, in seconds, that GDAL may take to read the file.
    timeout_seconds: int = 900


def source_of(path: str, limits: Limits) -> tuple[str, str]:
    """What GDAL should open for the file at `path`, and its kind (extension), looking inside a ZIP archive."""
    extension = os.path.splitext(path)[1].lower()
    if extension not in SUPPORTED:
        raise IngestionProblem("unsupported_format", "Unsupported file format.", extension=extension)
    if extension != ".zip":
        return path, extension
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile as error:
        raise IngestionProblem("corrupted_archive", "The ZIP archive is corrupted.") from error
    unpacked = sum(entry.file_size for entry in entries)
    swollen = any(entry.compress_size and entry.file_size / entry.compress_size > limits.max_compression_ratio for entry in entries)
    if len(entries) > limits.max_archive_entries or unpacked > limits.max_unpacked_bytes or swollen:
        raise IngestionProblem(
            "archive_too_large",
            "The uncompressed size of the ZIP archive exceeds the limit.",
            entries=len(entries),
            unpacked_bytes=unpacked,
        )
    names = [
        entry.filename
        for entry in entries
        if not entry.filename.endswith("/") and "__MACOSX" not in entry.filename and ".." not in entry.filename
    ]
    for wanted in IN_ARCHIVES:
        for name in names:
            if name.lower().endswith(wanted):
                return f"/vsizip/{path}/{name}", wanted
    raise IngestionProblem(
        "archive_without_data",
        "The ZIP archive contains no supported file (shapefile, GeoPackage, GeoJSON, FlatGeobuf, KML, CSV or Excel).",
    )


def readers_arguments(extension: str) -> list[str]:
    """The arguments that restrict ogr2ogr to the readers of this kind of file."""
    return [part for name in READERS[extension] for part in ("-if", name)]
