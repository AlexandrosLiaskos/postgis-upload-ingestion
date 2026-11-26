"""The problems of an uploaded file, in words that the person who uploaded it can act on."""

from __future__ import annotations

from typing import Any


class IngestionProblem(Exception):
    """A problem of the file. `code` names the kind of problem, so that an application can word it in its own way;
    `message` is a sentence for the person who uploaded the file; `details` holds the values the message uses.

    Codes: unsupported_format, corrupted_archive, archive_too_large, archive_without_data, unreadable, empty,
    layer_name, no_coordinate_columns, empty_coordinates, unknown_coordinates, no_records, too_many_records,
    no_geometry, gdal.
    """

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def plain(stderr: str | None) -> IngestionProblem:
    """The problem that GDAL reported, in words that the person can act on."""
    text = (stderr or "").strip()
    if "no coordinate system" in text or "Can't transform coordinates" in text:
        return IngestionProblem(
            "gdal",
            "The file does not define a coordinate reference system. "
            "Add the .prj file to the ZIP archive, or export the data in WGS 84 (EPSG:4326), and upload the file again.",
        )
    if "Unable to open datasource" in text or "not recognized as" in text:
        return IngestionProblem("unreadable", "The file could not be opened. It may be corrupted or in an unsupported format.")
    last = [line for line in text.splitlines() if line.strip()][-1:] or ["no message"]
    return IngestionProblem("gdal", f"The file could not be processed. GDAL reported: {last[0][:300]}", reported=last[0])
