"""KLE geometry in keyboard units and pixel coordinates, independent of GTK.

KLE rotations turn around the cluster origin (rx, ry), not each key's center.
Use the same placed polygon for painting and hit testing to keep rotated split
keyboards accurate. No physical coordinate is used as a matrix address.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .definitions import Key

Point = tuple[float, float]


def rotate(point: Point, origin: Point, degrees: float) -> Point:
    angle = math.radians(degrees)
    cosine, sine = math.cos(angle), math.sin(angle)
    x, y = point[0] - origin[0], point[1] - origin[1]
    return origin[0] + x * cosine - y * sine, origin[1] + x * sine + y * cosine


def key_polygon(key: Key, inset=0.0) -> tuple[Point, ...]:
    inset = min(max(0.0, inset), min(key.w, key.h) / 2 - 0.001)
    x1, x2 = key.x + inset, key.x + key.w - inset
    y1, y2 = key.y + inset, key.y + key.h - inset
    return tuple(rotate(point, (key.rotation_x, key.rotation_y), key.rotation)
                 for point in ((x1, y1), (x2, y1), (x2, y2), (x1, y2)))


def polygon_bounds(points):
    if not points:
        return 0.0, 0.0, 0.0, 0.0
    return min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)


def bounds(keys):
    """Return (left, top, right, bottom) in keyboard units, after rotation."""
    return polygon_bounds([point for key in keys for point in key_polygon(key)])


def point_in_polygon(x, y, points):
    """Ray casting with boundary inclusion; safe for convex or concave polygons."""
    if len(points) < 3 or not math.isfinite(x) or not math.isfinite(y):
        return False
    inside = False
    previous = points[-1]
    for current in points:
        x1, y1 = previous
        x2, y2 = current
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if abs(cross) <= 1e-8 and min(x1, x2) - 1e-8 <= x <= max(x1, x2) + 1e-8 and min(y1, y2) - 1e-8 <= y <= max(y1, y2) + 1e-8:
            return True
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
        previous = current
    return inside


@dataclass(frozen=True)
class PlacedKey:
    key: Key
    points: tuple[Point, ...]
    center: Point
    angle: float
    unit: float
    width: float
    height: float

    @property
    def bounds(self):
        return polygon_bounds(self.points)

    def contains(self, x, y):
        return point_in_polygon(x, y, self.points)


def place_keys(keys, width, height, *, padding=16.0, gap=5.0, max_unit=62.0):
    """Fit/center keys and return pixel polygons with an identical hit transform.

    ``angle`` is radians for cairo.rotate; ``width``/``height`` are unrotated
    key faces in pixels. ``points`` are the rotated face vertices in pixels.
    ``center`` is suitable for centered text, including text rotated by angle.
    """
    keys = list(keys)
    values = (width, height, padding, gap, max_unit)
    if not all(type(value) in (int, float) and math.isfinite(value) for value in values):
        raise ValueError("Canvas dimensions must be finite numbers")
    if padding < 0 or gap < 0 or max_unit <= 0:
        raise ValueError("Canvas spacing must be nonnegative and maximum unit positive")
    if not keys or width <= 2 * padding or height <= 2 * padding:
        return []
    left, top, right, bottom = bounds(keys)
    span_x, span_y = right - left, bottom - top
    if span_x <= 0 or span_y <= 0:
        return []
    unit = min((width - padding * 2) / span_x, (height - padding * 2) / span_y, max_unit)
    offset_x, offset_y = (width - span_x * unit) / 2 - left * unit, (height - span_y * unit) / 2 - top * unit
    result = []
    for key in keys:
        inset = min(gap / (2 * unit), min(key.w, key.h) * .4)
        points = tuple((x * unit + offset_x, y * unit + offset_y) for x, y in key_polygon(key, inset))
        center = (sum(x for x, _ in points) / len(points), sum(y for _, y in points) / len(points))
        result.append(PlacedKey(key, points, center, math.radians(key.rotation), unit,
                                (key.w - 2 * inset) * unit, (key.h - 2 * inset) * unit))
    return result


def hit_test(placed_keys, x, y):
    """Return the topmost Key under a point, or None (including between keys)."""
    for placed in reversed(placed_keys):
        if placed.contains(x, y):
            return placed.key
    return None
