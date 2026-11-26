"""The columns of an imported table: plain names, types from their values, and their profiles."""

from __future__ import annotations

import re
from typing import Any

# Patterns that a value must have before it is tried as a number or a date. A value with a leading zero ("007") is a
# code and stays text; "NaN" and "Infinity", which PostgreSQL would accept as numbers, do not match.
INTEGER = r"^\s*[-+]?\d{1,18}\s*$"
NUMBER = r"^\s*[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?\s*$"
COMMA_NUMBER = r"^\s*[-+]?\d+,\d+\s*$"
THOUSANDS = r"^\s*[-+]?\d{1,3},\d{3}\s*$"
LONG_INTEGER = r"^\s*[-+]?\d{4,}\s*$"
LEADING_ZERO = r"^\s*[-+]?0\d+\s*$"
ISO_DATE = r"^\s*\d{4}-\d{2}-\d{2}\s*$"

NUMERIC_TYPES = ("int2", "int4", "int8", "float4", "float8", "numeric")
DATE_TYPES = ("date", "timestamp", "timestamptz")
#: A text column with more than this many different values gets no list filter.
LIST_MAX_VALUES = 60
#: A number column with more than this many different values gets a range filter instead of a list.
RANGE_MIN_VALUES = 10


def quote(name: str) -> str:
    """A table or column name as SQL: in double quotes, with every double quote in it doubled. The names of columns come
    from the file unchanged, so they may hold quotes and semicolons."""
    return '"' + str(name).replace('"', '""') + '"'


def qualified(schema: str, table: str) -> str:
    return f"{quote(schema)}.{quote(table)}"


def columns_of(cursor: Any, schema: str, table: str) -> list[tuple[str, str]]:
    cursor.execute(
        "SELECT column_name, udt_name FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
        [schema, table],
    )
    return [(name, kind) for name, kind in cursor.fetchall()]


def plain_name(original: str) -> str:
    """A name of small Latin letters, digits and underscores, that does not start with a digit."""
    safe = re.sub(r"_+", "_", re.sub(r"[^a-z0-9_]", "_", original.strip().lower())).strip("_")
    if not safe or safe[0].isdigit():
        safe = f"column_{safe}".rstrip("_")
    return safe[:55]


def tidy_names(cursor: Any, schema: str, table: str, reserved: frozenset[str]) -> dict[str, str]:
    """Gives every column a plain name and returns the original header of each renamed column, by its new name.

    The names that are already plain are kept, and the new names are chosen so that none of them is taken: a column
    "Height m" becomes "height_m_2" when another column is already called "height_m". `reserved` are the columns made by
    GDAL (fid, geom), which keep their names; their names are not given to other columns.
    """
    columns = [name for name, _ in columns_of(cursor, schema, table)]
    used = set(reserved) | {name for name in columns if name not in reserved and plain_name(name) == name}
    labels: dict[str, str] = {}
    for original in columns:
        if original in reserved or (plain_name(original) == original):
            continue
        base = plain_name(original)
        safe, number = base, 2
        while safe in used:
            safe, number = f"{base}_{number}", number + 1
        used.add(safe)
        cursor.execute(f"ALTER TABLE {qualified(schema, table)} RENAME COLUMN {quote(original)} TO {quote(safe)}")
        labels[safe] = original.strip()
    return labels


def _valid(cursor: Any) -> str:
    """A test that a value can be read as a given type without error: pg_input_is_valid on PostgreSQL 16 and later."""
    cursor.execute("SHOW server_version_num")
    return "pg_input_is_valid({value}, '{kind}')" if int(cursor.fetchone()[0]) >= 160000 else "true"


def set_types(cursor: Any, schema: str, table: str, reserved: frozenset[str]) -> list[str]:
    """Turns text columns into numbers or dates when every filled value is one, and returns notes for the person.

    A value must both look like a number or date and be accepted by PostgreSQL, so that "2023-02-30" or "1e999" keep
    the column as text instead of failing the upload. A decimal comma is read as such ("1,5"). A column written as with
    a thousands separator stays text, with a note: every value with a comma has one to three digits, the comma and three
    digits ("1,234"), and the values without a comma have at most three digits ("800").
    """
    valid = _valid(cursor)
    notes = []
    for name, kind in columns_of(cursor, schema, table):
        if kind not in ("varchar", "text") or name in reserved:
            continue
        column = quote(name)
        trimmed = f"btrim({column})"
        decimal = f"replace({trimmed}, ',', '.')"
        cursor.execute(
            f"SELECT count(*) FILTER (WHERE {trimmed} <> ''), "
            f"count(*) FILTER (WHERE {column} ~ %s AND {valid.format(value=trimmed, kind='bigint')}), "
            f"count(*) FILTER (WHERE {column} ~ %s AND {valid.format(value=trimmed, kind='double precision')}), "
            f"count(*) FILTER (WHERE {column} ~ %s AND {valid.format(value=decimal, kind='double precision')}), "
            f"count(*) FILTER (WHERE {column} ~ %s), "
            f"count(*) FILTER (WHERE {column} ~ %s), "
            f"count(*) FILTER (WHERE {column} ~ %s), "
            f"count(*) FILTER (WHERE {column} ~ %s AND {valid.format(value=trimmed, kind='date')}) "
            f"FROM {qualified(schema, table)}",
            [INTEGER, NUMBER, COMMA_NUMBER, THOUSANDS, LONG_INTEGER, LEADING_ZERO, ISO_DATE],
        )
        filled, integers, numbers, commas, thousands, long_integers, zeros, dates = cursor.fetchone()
        if not filled or zeros:
            continue
        value = f"NULLIF({trimmed}, '')"
        if integers == filled:
            new, using = "bigint", f"{value}::bigint"
        elif numbers == filled:
            new, using = "double precision", f"{value}::double precision"
        elif commas and commas + integers == filled:
            if thousands == commas and integers > 0 and long_integers == 0:
                notes.append(
                    f"The column “{name}” is kept as text: in values such as “1,234” the comma may separate thousands "
                    "or decimals. Write the numbers without a thousands separator to read them as numbers."
                )
                continue
            new, using = "double precision", f"replace({value}, ',', '.')::double precision"
        elif dates == filled:
            new, using = "date", f"{value}::date"
        else:
            continue
        cursor.execute(f"ALTER TABLE {qualified(schema, table)} ALTER COLUMN {column} TYPE {new} USING {using}")
    return notes


def label_of(name: str) -> str:
    return name.replace("_", " ").strip().capitalize()


def describe(cursor: Any, schema: str, table: str, labels: dict[str, str]) -> list[dict[str, Any]]:
    """The profile of every column: kind, number of records with a value, number of different values, range, the 60
    most frequent values and the filter that suits it. The keys are those that automatic-map-configuration reads."""
    described = []
    full = qualified(schema, table)
    for name, kind in columns_of(cursor, schema, table):
        if name in ("geom", "fid"):
            continue
        column = quote(name)
        # JSON values (nested properties of GeoJSON) have no equality; they are counted and listed as text.
        comparable = f"{column}::text" if kind in ("json", "jsonb") else column
        cursor.execute(f"SELECT count({column}), count(DISTINCT {comparable}) FROM {full}")
        filled, distinct = cursor.fetchone()
        item: dict[str, Any] = {"name": name, "label": labels.get(name) or label_of(name), "filled": filled, "distinct": distinct}
        if kind in NUMERIC_TYPES:
            cursor.execute(
                f"SELECT min({column})::double precision, max({column})::double precision, avg({column})::double precision FROM {full}"
            )
            low, high, mean = cursor.fetchone()
            item.update(
                kind="number",
                min=low,
                max=high,
                mean=float(mean) if mean is not None else None,
                filter="range" if distinct > RANGE_MIN_VALUES else "list",
            )
        elif kind in DATE_TYPES:
            cursor.execute(f"SELECT min({column})::text, max({column})::text FROM {full}")
            low, high = cursor.fetchone()
            item.update(kind="date", min=low, max=high, filter="range")
        elif kind == "bool":
            item.update(kind="yes/no", filter="list")
        else:
            item.update(kind="text", filter="list" if 1 < distinct <= LIST_MAX_VALUES else None)
        if item.get("filter") == "list":
            cursor.execute(
                f"SELECT {column}::text, count(*) FROM {full} WHERE {column} IS NOT NULL "
                f"GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {LIST_MAX_VALUES}"
            )
            item["values"] = [[value, count] for value, count in cursor.fetchall()]
        described.append(item)
    return described
