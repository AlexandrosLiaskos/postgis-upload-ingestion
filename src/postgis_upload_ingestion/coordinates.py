"""The coordinate systems recognised in the coordinate columns of a table, by the extent of their values."""

from __future__ import annotations

from dataclasses import dataclass

# The names of coordinate columns, in the order in which they are looked for.
X_NAMES = ("longitude", "lon", "lng", "long", "x", "centroid_lng", "easting", "x_coord", "xcoord")
Y_NAMES = ("latitude", "lat", "y", "centroid_lat", "northing", "y_coord", "ycoord")


@dataclass(frozen=True)
class CoordinateSystem:
    """A coordinate system recognised when every coordinate of a table lies within `bounds` (west, south, east, north,
    in the units of the system). `in_degrees` is true for geographic systems."""

    epsg: int
    name: str
    bounds: tuple[float, float, float, float]
    in_degrees: bool = False

    def holds(self, west: float, south: float, east: float, north: float) -> bool:
        low_x, low_y, high_x, high_y = self.bounds
        return low_x <= west and east <= high_x and low_y <= south and north <= high_y

    @property
    def described(self) -> str:
        return self.name if self.epsg == 4326 else f"{self.name} (EPSG:{self.epsg})"


WGS84 = CoordinateSystem(4326, "WGS 84", (-180.0, -90.0, 180.0, 90.0), in_degrees=True)
GREEK_GRID = CoordinateSystem(2100, "GGRS87 / Greek Grid", (0.0, 3_700_000.0, 1_100_000.0, 4_800_000.0))
#: The systems tried by default, in this order: WGS 84 in degrees, then the Greek Grid in metres. An application for
#: another country adds its national grid, for example CoordinateSystem(25832, "ETRS89 / UTM zone 32N", (...)).
DEFAULT_COORDINATE_SYSTEMS = (WGS84, GREEK_GRID)


def _alternatives(words: list[str], last: str) -> str:
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} {last} {words[-1]}"


def naming_advice(systems: tuple[CoordinateSystem, ...]) -> str:
    """How to name coordinate columns: “longitude” and “latitude” (WGS 84) or “x” and “y” (GGRS87 / Greek Grid, EPSG:2100)."""
    advice = []
    for system in systems:
        names = "“longitude” and “latitude”" if system.in_degrees else "“x” and “y”"
        label = system.name if system.epsg == 4326 else f"{system.name}, EPSG:{system.epsg}"
        advice.append(f"{names} ({label})")
    return _alternatives(advice, "or")


def neither(systems: tuple[CoordinateSystem, ...]) -> str:
    """“neither WGS 84 coordinates in degrees nor GGRS87 / Greek Grid (EPSG:2100) coordinates”."""
    named = [f"{system.described} coordinates in degrees" if system.in_degrees else f"{system.described} coordinates" for system in systems]
    if len(named) == 2:
        return f"neither {named[0]} nor {named[1]}"
    return f"not {_alternatives(named, 'or')}"
