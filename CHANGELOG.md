# Changelog

All notable changes to this library are recorded in this file. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

### Added

- `ingest(path, connection, database, table, schema, limits, coordinate_systems)`: reads an uploaded file into a
  PostGIS table with GDAL and returns the counts, kinds of geometry, extent, column profiles and notes.
- `IngestionProblem` with a code, a message for the person and details; `drop` for a table left by a failed upload.
- Guards against files of another kind than their name says, column names that break SQL, addresses fetched by GDAL,
  option-like layer names and ZIP archives made to fill the disk.
- Types from values that PostgreSQL accepts, decimal commas, codes with leading zeros, and a note for values that may
  use a thousands separator.
- Coordinate systems of tables recognised by extent, WGS 84 and the Greek Grid by default, others as options.
