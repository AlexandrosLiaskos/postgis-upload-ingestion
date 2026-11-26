import http.server
import io
import json
import re
import socket
import subprocess
import threading
import zipfile

import pytest
from conftest import SCHEMA, column

from postgis_upload_ingestion import WGS84, CoordinateSystem, IngestionProblem, Limits


def point_feature(properties, lng=23.7, lat=37.9):
    return {"type": "Feature", "properties": properties, "geometry": {"type": "Point", "coordinates": [lng, lat]}}


def geojson(*features):
    return json.dumps({"type": "FeatureCollection", "features": list(features)})


# Tables


def test_a_table_with_longitude_and_latitude_becomes_points(read):
    found = read(
        "sites.csv",
        "Name,Type,Height m,longitude,latitude\nAlpha,beach,2.5,23.70,37.90\nBeta,beach,3.0,24.10,38.20\nGamma,cliff,,25.00,35.30\n",
    )
    assert (found.record_count, found.without_location, found.geometry_type) == (3, 0, "POINT")
    assert found.extent == [23.7, 35.3, 25.0, 38.2]
    assert column(found, "type")["values"] == [["beach", 2], ["cliff", 1]]
    height = column(found, "height_m")
    assert (height["kind"], height["min"], height["max"], height["filled"], height["label"]) == ("number", 2.5, 3.0, 2, "Height m")


def test_semicolons_and_decimal_commas_are_understood(read):
    found = read("greek.csv", "name;lon;lat;depth\nA;23,5;38,25;1,5\nB;24;37,75;2\n")
    assert found.extent == [23.5, 37.75, 24.0, 38.25]
    assert column(found, "depth")["max"] == 2.0


def test_a_comma_that_may_separate_thousands_keeps_the_column_as_text_with_a_note(read):
    found = read("areas.csv", 'name,area,longitude,latitude\nA,"1,234",23.7,37.9\nB,800,24.1,38.2\n')
    assert column(found, "area")["kind"] == "text"
    assert any("“area” is kept as text" in note for note in found.notes)


def test_greek_grid_coordinates_are_converted_to_degrees(read):
    found = read("egsa.csv", "name,x,y\nAthens,476000,4204000\nPatras,300000,4234000\n")
    west, south, east, north = found.extent
    assert 21.5 < west < 22.0 and 23.5 < east < 24.0 and 37.8 < south < 38.4 and 37.8 < north < 38.4
    assert found.notes == ["The coordinates were identified as GGRS87 / Greek Grid (EPSG:2100) and transformed to WGS 84."]


def test_another_coordinate_system_can_be_recognised(read):
    mercator = CoordinateSystem(3857, "WGS 84 / Pseudo-Mercator", (-20037508.0, -20048966.0, 20037508.0, 20048966.0))
    content = "name,x,y\nAthens,2638000,4579000\nPatras,2421000,4609000\n"
    refused = read("mercator.csv", content, fails=True)
    assert refused.code == "unknown_coordinates"
    found = read("mercator.csv", content, coordinate_systems=(WGS84, mercator))
    assert 21.5 < found.extent[0] < 22.0 and 23.5 < found.extent[2] < 24.0
    assert found.notes == ["The coordinates were identified as WGS 84 / Pseudo-Mercator (EPSG:3857) and transformed to WGS 84."]


def test_a_file_without_coordinate_columns_is_refused(read):
    problem = read("plain.csv", "name,value\nA,1\nB,2\n", fails=True)
    assert problem.code == "no_coordinate_columns"
    assert problem.message == (
        "The file has no numeric coordinate columns. Name them “longitude” and “latitude” (WGS 84) "
        "or “x” and “y” (GGRS87 / Greek Grid, EPSG:2100), and upload the file again."
    )


def test_records_without_location_are_counted(read):
    found = read("partial.csv", "name,longitude,latitude\nA,23.7,37.9\nB,,\n")
    assert (found.record_count, found.without_location) == (2, 1)
    assert found.notes == ["1 of 2 records have no geometry and are not displayed on the map."]


def test_impossible_dates_and_numbers_keep_their_column_as_text(read):
    found = read("odd.csv", "name,day,size,longitude,latitude\nA,2023-02-30,1e999,23.7,37.9\nB,2023-03-01,5,24.1,38.2\n")
    assert column(found, "day")["kind"] == "text" and column(found, "size")["kind"] == "text"


def test_a_valid_date_column_becomes_dates(read):
    found = read("days.csv", "name,day,longitude,latitude\nA,2023-02-28,23.7,37.9\nB,2023-03-01,24.1,38.2\n")
    day = column(found, "day")
    assert (day["kind"], day["min"], day["max"], day["filter"]) == ("date", "2023-02-28", "2023-03-01", "range")


def test_a_code_with_leading_zeros_stays_text(read):
    found = read("codes.csv", "name,code,longitude,latitude\nA,007,23.7,37.9\nB,012,24.1,38.2\n")
    assert column(found, "code")["kind"] == "text"


def test_names_that_become_equal_are_kept_apart(read):
    # "height_m" is already a plain name and keeps it; "Height m" would become the same and takes the next free one.
    found = read("heights.csv", "Height m,height_m,longitude,latitude\n1,2,23.7,37.9\n")
    assert (column(found, "height_m_2")["label"], column(found, "height_m")["filled"]) == ("Height m", 1)


def test_a_column_of_the_file_named_geom_does_not_stop_the_points(read):
    found = read("withgeom.csv", "name,geom,longitude,latitude\nA,POINT(1 2),23.7,37.9\n")
    assert found.geometry_type == "POINT"
    assert column(found, "geom_2")["label"] == "geom"


HOSTILE = 'a" TO "b"; CREATE TABLE ingestion_tests.injected (x int); --'


def test_a_column_name_with_quotes_is_only_a_name(read, connection):
    for name, content in [
        ("sites.csv", 'name,a,"' + HOSTILE.replace('"', '""') + '",lon,lat\nAlpha,1,2,23.7,37.9\n'),
        ("sites.geojson", geojson(point_feature({"name": "Alpha", "a": 1, HOSTILE: 2}))),
    ]:
        found = read(name, content)
        assert connection.execute(f"SELECT to_regclass('{SCHEMA}.injected')").fetchone()[0] is None
        renamed = next(c for c in found.columns if c["label"] == HOSTILE)
        assert re.fullmatch(r"[a-z0-9_]+", renamed["name"]), renamed["name"]
        assert {"a"} <= {c["name"] for c in found.columns} and "b" not in {c["name"] for c in found.columns}


def test_the_limit_of_records(read):
    problem = read("many.csv", "name,longitude,latitude\n" + "A,23.7,37.9\n" * 1200, fails=True, limits=Limits(max_records=2))
    assert (problem.code, problem.details) == ("too_many_records", {"records": 1200, "limit": 2})


# Files with geometry


def test_polygons_of_a_geojson_file_are_read(read):
    lake = {
        "type": "Feature",
        "properties": {"name": "Lake", "area_km2": 1.5},
        "geometry": {"type": "Polygon", "coordinates": [[[23, 38], [23.1, 38], [23.1, 38.1], [23, 38.1], [23, 38]]]},
    }
    found = read("lakes.geojson", geojson(lake))
    assert found.geometry_type == "MULTIPOLYGON"
    assert column(found, "area_km2")["kind"] == "number"


def test_points_and_polygons_together_name_both_kinds(read):
    lake = {
        "type": "Feature",
        "properties": {"n": 1},
        "geometry": {"type": "Polygon", "coordinates": [[[23, 38], [23.1, 38], [23, 38.1], [23, 38]]]},
    }
    found = read("mixed.geojson", geojson(point_feature({"n": 2}), lake))
    assert found.geometry_types == ("MULTIPOINT", "MULTIPOLYGON")
    assert found.geometry_type == "MULTIPOINT, MULTIPOLYGON"


def test_nested_properties_are_counted_as_text(read):
    found = read(
        "nested.geojson",
        geojson(point_feature({"name": "A", "extra": {"depth": 1}}), point_feature({"name": "B", "extra": {"depth": 2}}, 24, 38)),
    )
    extra = column(found, "extra")
    assert (extra["kind"], extra["distinct"]) == ("text", 2)


def test_a_file_is_opened_only_as_the_kind_its_name_says(read, tmp_path):
    secret = tmp_path / "secret.csv"
    secret.write_text("name,longitude,latitude\nSECRET-OF-THE-SERVER,23.7,37.9\n")
    hostile = f'<OGRVRTDataSource><OGRVRTLayer name="secret"><SrcDataSource>{secret}</SrcDataSource></OGRVRTLayer></OGRVRTDataSource>'
    problem = read("sites.geojson", hostile, fails=True)
    assert "SECRET" not in problem.message


def test_a_layer_whose_name_is_an_option_is_refused(read):
    # The layer of a GeoJSON file takes the "name" of its collection; ogr2ogr would read "-sql" as one of its options.
    content = json.dumps({"type": "FeatureCollection", "name": "-sql", "features": [point_feature({"n": 1})]})
    problem = read("layers.geojson", content, fails=True)
    assert problem.code == "layer_name"


def test_gdal_reaches_no_address_named_in_the_file(read, tmp_path):
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            requests.append(self.path)
            self.send_response(404)
            self.end_headers()

        def log_message(self, *arguments):
            pass

    host = socket.gethostbyname(socket.gethostname())
    server = http.server.HTTPServer((host, 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    link = {"type": "link", "properties": {"href": f"http://{host}:{server.server_port}/crs", "type": "proj4"}}
    content = json.dumps({"type": "FeatureCollection", "crs": link, "features": [point_feature({"n": 1})]})
    try:
        # Without the protection GDAL fetches the address: this shows that the server sees such a request.
        control = tmp_path / "control.geojson"
        control.write_text(content)
        subprocess.run(["ogrinfo", "-ro", "-so", "-al", str(control)], capture_output=True, timeout=60)
        if not requests:
            pytest.skip("this version of GDAL does not fetch the address of a crs link")
        requests.clear()
        try:
            read("linked.geojson", content)
        except IngestionProblem:
            pass
    finally:
        server.shutdown()
    assert requests == []


# Archives


def zipped(files, method=zipfile.ZIP_DEFLATED):
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w", method) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return packed.getvalue()


def test_a_table_in_a_zip_archive_is_read(read):
    found = read("sites.zip", zipped({"readme.txt.md": "notes", "data/sites.csv": "name,longitude,latitude\nA,23.7,37.9\n"}))
    assert found.record_count == 1


def test_the_problems_of_archives(read):
    assert read("broken.zip", b"not a zip", fails=True).code == "corrupted_archive"
    assert read("notes.zip", zipped({"notes.md": "nothing to map"}), fails=True).code == "archive_without_data"
    large = zipped({"sites.csv": "name,longitude,latitude\n" + "A,23.7,37.9\n" * 200})
    assert read("large.zip", large, fails=True, limits=Limits(max_unpacked_bytes=100)).code == "archive_too_large"
    swollen = zipped({"sites.csv": "name,longitude,latitude\n" + "A,23.7,37.9\n" + " " * 5_000_000})
    assert read("swollen.zip", swollen, fails=True).code == "archive_too_large"


def test_unsupported_kinds_are_refused(read):
    assert read("program.exe", b"MZ", fails=True).code == "unsupported_format"
