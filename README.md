# Secure Ingestion of Geospatial Uploads into PostGIS

A web application that lets people upload their own data has to read files it did not write: tables with coordinate
columns, GeoJSON, GeoPackage, KML, FlatGeobuf, shapefiles, often inside a ZIP archive, with column names and values
of every kind. GDAL reads all of them, but it was not made for files from strangers. A file can name another file of
the server, ask GDAL to fetch an address, or carry column names that break SQL.

This library reads such a file into a PostGIS table with GDAL, guards against those cases, and returns what it found:

- the number of records and of those without geometry;
- the kinds of geometry and the extent;
- a profile of every column (kind, counts, range, most frequent values, suggested filter);
- notes for the person who uploaded the file.

Every problem comes back as a sentence that the person can act on.

## Installation

```sh
pip install postgis-upload-ingestion
```

It requires **GDAL 3.6 or later** with its Python bindings and the `ogr2ogr` program, and **PostgreSQL 16 or later**
with PostGIS. GDAL is not installed by pip, since its bindings must match the GDAL library of the machine. Use the
system's packages, conda, or the official GDAL container images. The tests run with GDAL 3.13 and PostGIS 17, and with
GDAL 3.10 and PostGIS 16.

## Use

```python
import psycopg
from postgis_upload_ingestion import Database, IngestionProblem, Limits, drop, ingest

database = Database(host="localhost", dbname="maps", user="maps", password="…")
with psycopg.connect(host="localhost", dbname="maps", user="maps", password="…", autocommit=True) as connection:
    try:
        found = ingest("upload.zip", connection, database, "dataset_42", schema="data", limits=Limits(max_records=200_000))
    except IngestionProblem as problem:
        drop(connection, "dataset_42", schema="data")
        print(problem.code, problem.message)
    else:
        print(found.record_count, found.geometry_type, found.extent, found.notes)
```

`connection` is any DB-API connection to the same database: psycopg, or Django's `django.db.connection`. `ogr2ogr`
writes through its own connection, made from `database`; the password reaches it in its environment, not on its
command line, where other users of the machine could read it.

The column profiles have the keys `name`, `label`, `kind`, `filled`, `distinct`, `filter`, `min`, `max`, `mean` and
`values`. These are what
[automatic-map-configuration](https://github.com/AlexandrosLiaskos/automatic-map-configuration) reads to propose a
first web map. `found.to_dict()` gives the result as plain data.

## What it guards against

| Case                                                                                                                                                | Guard                                                                                                                                                |
| --------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| A file whose name says one kind and whose content is another, such as a GDAL virtual dataset named `x.geojson` that reads other files of the server | Each kind is opened only by its own GDAL reader, for both inspection and `ogr2ogr`.                                                                  |
| Column names that end an SQL statement (`a" TO "b"; DROP …`)                                                                                        | Every name is quoted with its double quotes doubled; columns are renamed to plain names, and the original header is kept as the label.               |
| An address in the file that GDAL would fetch, such as a GeoJSON `crs` of type `link`                                                                | GDAL's HTTP requests go to a proxy that does not exist, with no retries; a test checks that no request leaves.                                       |
| A layer named like an option of `ogr2ogr` (`-sql`)                                                                                                  | Refused with a message.                                                                                                                              |
| A ZIP archive made to fill the disk                                                                                                                 | Limits on the number of files, the unpacked size and the ratio of unpacked to packed size of each file.                                              |
| Values that look like numbers or dates but are not (`2023-02-30`, `1e999`)                                                                          | A column becomes a number or a date only when PostgreSQL accepts every value (`pg_input_is_valid`); otherwise it stays text, and the upload goes on. |
| Columns whose plain names would be the same ("Height m" and "height_m")                                                                             | The names are chosen so that none is taken twice.                                                                                                    |
| Nested properties of GeoJSON                                                                                                                        | Counted and listed as text.                                                                                                                          |
| A table with a column of its own named `geom`                                                                                                       | That column is renamed, and the points take the name.                                                                                                |

## How a file is read

1. **Kind.** The extension decides the GDAL reader: `.csv`, `.tsv`, `.txt`, `.xlsx`, `.geojson`, `.json`, `.gpkg`,
   `.kml`, `.fgb` or `.shp`. Inside a ZIP archive, the first file of a supported kind is read; a shapefile is read with
   its `.prj` and `.dbf`.
2. **Import.** `ogr2ogr` writes the first layer into `schema.table`, replacing it. Files with geometry are transformed
   to WGS 84, as multi-part geometries in two dimensions. An Excel workbook is read through CSV.
3. **Names.** Every column gets a name of small Latin letters, digits and underscores; the original header becomes
   its label.
4. **Types.** A text column becomes a whole number, a number or a date when every filled value is one.
   - A decimal comma is read as such: "1,5" is 1.5.
   - A value with a leading zero (`007`) is a code and stays text.
   - A column that looks written with a thousands separator ("1,234" beside "800") stays text, with a note.
5. **Points.** For a table, two coordinate columns are looked for by name: `longitude`/`lat`/`x`/`easting` and the
   like. Their coordinate system is recognised by the extent of their values, from `coordinate_systems`:
   - by default, WGS 84 in degrees, then GGRS87 / Greek Grid (EPSG:2100) in metres;
   - add your national grid with `CoordinateSystem(epsg, name, bounds)`.

   The points are made in WGS 84.

6. **Checks.** A file without records, with more records than `Limits.max_records`, or where no record has a
   geometry, is refused; records without geometry are counted in a note.
7. **Profile.** Every column is described for filters and charts.

## Problems

`IngestionProblem` has a `code`, a `message` for the person, and `details` with the values the message uses. An
application can show the message as it is, or word it in its own way from the code and details:

`unsupported_format`, `corrupted_archive`, `archive_too_large`, `archive_without_data`, `unreadable`, `layer_name`,
`no_coordinate_columns`, `empty_coordinates`, `unknown_coordinates`, `no_records`, `too_many_records`, `no_geometry`,
`gdal`.

After a problem the table may exist in part; `drop(connection, table, schema=…)` removes it.

## Origin

The library comes from the WebGIS Builder of the National and Kapodistrian University of Athens, where it reads the
datasets that research teams upload. Version 0.1.0 also guards against the cases that a security review of that
reader found: quotes in column names, fetched addresses, option-like layer names, impossible values, colliding names,
nested properties and thousands separators.

## Citation

The citation of the software is given in [CITATION.cff](CITATION.cff). Each release is archived on Zenodo with its own
DOI.

## Licence

Copyright © 2026 Alexandros Liaskos, National and Kapodistrian University of Athens.

This program is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General
Public License, version 3, as published by the Free Software Foundation ([LICENSE](LICENSE)). It is distributed in the
hope that it will be useful, but without any warranty; without even the implied warranty of merchantability or fitness
for a particular purpose.
