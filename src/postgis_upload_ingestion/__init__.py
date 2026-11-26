"""Secure Ingestion of Geospatial Uploads into PostGIS.

Reads an uploaded file (CSV, Excel, GeoJSON, GeoPackage, KML, FlatGeobuf, shapefile, or any of them in a ZIP archive)
into a PostGIS table with GDAL, safely, and describes what it found.

Copyright (C) 2026 Alexandros Liaskos
SPDX-License-Identifier: AGPL-3.0-only
"""

from .coordinates import DEFAULT_COORDINATE_SYSTEMS, GREEK_GRID, WGS84, CoordinateSystem
from .ingest import Database, IngestionResult, drop, ingest
from .problems import IngestionProblem
from .sources import READERS, SUPPORTED, TABLES, Limits

__all__ = [
    "DEFAULT_COORDINATE_SYSTEMS",
    "GREEK_GRID",
    "READERS",
    "SUPPORTED",
    "TABLES",
    "WGS84",
    "CoordinateSystem",
    "Database",
    "IngestionProblem",
    "IngestionResult",
    "Limits",
    "drop",
    "ingest",
]
__version__ = "0.1.0"
