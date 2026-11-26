"""The tests read files into a PostGIS database given by the usual libpq variables (PGHOST, PGPORT, PGDATABASE, PGUSER,
PGPASSWORD), with GDAL installed. Each test reads into its own table of the schema "ingestion_tests"."""

import itertools
import os

import psycopg
import pytest

from postgis_upload_ingestion import Database, IngestionProblem, drop, ingest

SCHEMA = "ingestion_tests"
_numbers = itertools.count(1)


@pytest.fixture(scope="session")
def connection():
    with psycopg.connect(autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.execute(f"CREATE SCHEMA {SCHEMA}")
        yield conn


@pytest.fixture(scope="session")
def database():
    return Database(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", "5432")),
        dbname=os.environ.get("PGDATABASE", "postgres"),
        user=os.environ.get("PGUSER", "postgres"),
        password=os.environ.get("PGPASSWORD", ""),
    )


@pytest.fixture
def read(connection, database, tmp_path):
    """Reads a file with the given name and content; returns the result, or the problem when `fails` is true."""
    tables = []

    def run(name, content, fails=False, **options):
        path = tmp_path / name
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        table = f"t_{next(_numbers)}"
        tables.append(table)
        if fails:
            with pytest.raises(IngestionProblem) as raised:
                ingest(str(path), connection, database, table, schema=SCHEMA, **options)
            return raised.value
        return ingest(str(path), connection, database, table, schema=SCHEMA, **options)

    run.tables = tables  # the last one is the table of the last file read
    yield run
    for table in tables:
        drop(connection, table, schema=SCHEMA)


def column(result, name):
    return next(c for c in result.columns if c["name"] == name)
