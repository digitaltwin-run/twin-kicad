"""Deterministic rectilinear copper routing primitives.

The module operates only on geometry. It does not parse KiCad files, select
nets, authorize edits or decide whether a candidate may be promoted.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class Capsule:
    """A segment or via represented by a center line and radius."""

    net: int
    ax: float
    ay: float
    bx: float
    by: float
    radius: float


@dataclass(frozen=True, slots=True)
class Box:
    """A pad or keepout represented by its axis-aligned bounding box."""

    net: int
    x0: float
    y0: float
    x1: float
    y1: float


Obstacle = Capsule | Box


@dataclass(frozen=True, slots=True)
class Bounds:
    x0: float
    y0: float
    x1: float
    y1: float

    def contains(self, x: float, y: float, margin: float) -> bool:
        return (
            self.x0 + margin <= x <= self.x1 - margin
            and self.y0 + margin <= y <= self.y1 - margin
        )


@dataclass(frozen=True, slots=True)
class Track:
    x0: float
    y0: float
    x1: float
    y1: float


class RoutingError(RuntimeError):
    """A clear route was not found within the configured search budget."""


_EPS = 1e-6


def _clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def point_segment_distance(
    px: float, py: float, ax: float, ay: float, bx: float, by: float
) -> float:
    """Return the shortest centerline distance from a point to a segment."""
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq < _EPS:
        return math.hypot(px - ax, py - ay)
    t = _clamp(((px - ax) * dx + (py - ay) * dy) / length_sq, 0.0, 1.0)
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def segments_intersect(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    cx: float,
    cy: float,
    dx: float,
    dy: float,
) -> bool:
    """Return whether two segments cross at an interior point."""
    def cross(ox: float, oy: float, px: float, py: float, qx: float, qy: float) -> float:
        return (px - ox) * (qy - oy) - (py - oy) * (qx - ox)

    d1 = cross(cx, cy, dx, dy, ax, ay)
    d2 = cross(cx, cy, dx, dy, bx, by)
    d3 = cross(ax, ay, bx, by, cx, cy)
    d4 = cross(ax, ay, bx, by, dx, dy)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def segment_distance(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    cx: float,
    cy: float,
    dx: float,
    dy: float,
) -> float:
    """Return the shortest centerline distance between two segments."""
    if segments_intersect(ax, ay, bx, by, cx, cy, dx, dy):
        return 0.0
    return min(
        point_segment_distance(ax, ay, cx, cy, dx, dy),
        point_segment_distance(bx, by, cx, cy, dx, dy),
        point_segment_distance(cx, cy, ax, ay, bx, by),
        point_segment_distance(dx, dy, ax, ay, bx, by),
    )


def segment_box_distance(ax: float, ay: float, bx: float, by: float, box: Box) -> float:
    """Return the shortest distance from a segment centerline to a closed box."""
    inside_a = box.x0 <= ax <= box.x1 and box.y0 <= ay <= box.y1
    inside_b = box.x0 <= bx <= box.x1 and box.y0 <= by <= box.y1
    if inside_a or inside_b:
        return 0.0
    corners = (
        (box.x0, box.y0),
        (box.x1, box.y0),
        (box.x1, box.y1),
        (box.x0, box.y1),
    )
    return min(
        segment_distance(
            ax,
            ay,
            bx,
            by,
            corners[index][0],
            corners[index][1],
            corners[(index + 1) % 4][0],
            corners[(index + 1) % 4][1],
        )
        for index in range(4)
    )


class Field:
    """Copper obstacles with an axis-aligned bounding-box prefilter."""

    __slots__ = ("items",)

    def __init__(self, obstacles: Iterable[Obstacle]) -> None:
        self.items: list[tuple[float, float, float, float, Obstacle]] = []
        for obstacle in obstacles:
            if isinstance(obstacle, Capsule):
                box = (
                    min(obstacle.ax, obstacle.bx) - obstacle.radius,
                    min(obstacle.ay, obstacle.by) - obstacle.radius,
                    max(obstacle.ax, obstacle.bx) + obstacle.radius,
                    max(obstacle.ay, obstacle.by) + obstacle.radius,
                )
            else:
                box = (obstacle.x0, obstacle.y0, obstacle.x1, obstacle.y1)
            self.items.append((*box, obstacle))

    def is_clear(self, track: Track, net: int, half_width: float, clearance: float) -> bool:
        reach = half_width + clearance
        low_x = min(track.x0, track.x1) - reach
        high_x = max(track.x0, track.x1) + reach
        low_y = min(track.y0, track.y1) - reach
        high_y = max(track.y0, track.y1) + reach
        for box_x0, box_y0, box_x1, box_y1, obstacle in self.items:
            if box_x1 < low_x or box_x0 > high_x or box_y1 < low_y or box_y0 > high_y:
                continue
            if obstacle.net == net:
                continue
            if isinstance(obstacle, Capsule):
                gap = (
                    segment_distance(
                        track.x0,
                        track.y0,
                        track.x1,
                        track.y1,
                        obstacle.ax,
                        obstacle.ay,
                        obstacle.bx,
                        obstacle.by,
                    )
                    - half_width
                    - obstacle.radius
                )
            else:
                gap = segment_box_distance(
                    track.x0, track.y0, track.x1, track.y1, obstacle
                ) - half_width
            # Clearance equal to the requirement is valid. The tolerance also
            # protects candidate grids from ordinary floating-point rounding.
            if gap < clearance - _EPS:
                return False
        return True


def track_is_clear(
    track: Track,
    net: int,
    obstacles: Iterable[Obstacle] | Field,
    half_width: float,
    clearance: float,
) -> bool:
    """Return whether a track keeps clearance from every foreign-net obstacle."""
    field = obstacles if isinstance(obstacles, Field) else Field(obstacles)
    return field.is_clear(track, net, half_width, clearance)


def _obstacle_edges(field: Field) -> tuple[list[float], list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for box_x0, box_y0, box_x1, box_y1, _obstacle in field.items:
        xs += [box_x0, box_x1]
        ys += [box_y0, box_y1]
    return xs, ys


def _candidates(
    edges: list[float],
    anchors: tuple[float, float],
    low: float,
    high: float,
    offset: float,
    limit: int,
) -> list[float]:
    """Build a Hanan-style grid offset from obstacle edges."""
    values = {round(_clamp(value, low, high), 4) for value in anchors}
    for edge in edges:
        for candidate in (edge - offset, edge + offset):
            if low <= candidate <= high:
                values.add(round(candidate, 4))
    first, second = anchors
    cost = lambda value: abs(value - first) + abs(second - value)  # noqa: E731
    return sorted(values, key=cost)[:limit]


def route_edge(
    start: tuple[float, float],
    end: tuple[float, float],
    net: int,
    obstacles: list[Obstacle] | Field,
    bounds: Bounds,
    width: float,
    clearance: float,
    budget: int = 40_000,
) -> list[Track]:
    """Route one connection using rectilinear, clearance-checked legs."""
    px, py = start
    qx, qy = end
    field = obstacles if isinstance(obstacles, Field) else Field(obstacles)
    half_width = width / 2.0
    offset = half_width + clearance
    margin = half_width
    xs, ys = _obstacle_edges(field)
    x_values = _candidates(xs, (px, qx), bounds.x0 + margin, bounds.x1 - margin, offset, 260)
    y_values = _candidates(ys, (py, qy), bounds.y0 + margin, bounds.y1 - margin, offset, 260)

    def clear(x0: float, y0: float, x1: float, y1: float) -> bool:
        if abs(x0 - x1) <= _EPS and abs(y0 - y1) <= _EPS:
            return True
        return field.is_clear(Track(x0, y0, x1, y1), net, half_width, clearance)

    y_values = [y for y in y_values if clear(px, py, px, y)]
    x_values = [x for x in x_values if clear(x, qy, qx, qy)]
    pairs = sorted(
        (
            (abs(y - py) + abs(qy - y) + abs(x - px) + abs(qx - x), y, x)
            for y in y_values
            for x in x_values
        ),
        key=lambda item: item[0],
    )
    for index, (_cost, y, x) in enumerate(pairs):
        if index >= budget:
            break
        if not (clear(px, y, x, y) and clear(x, y, x, qy)):
            continue
        raw = [
            Track(px, py, px, y),
            Track(px, y, x, y),
            Track(x, y, x, qy),
            Track(x, qy, qx, qy),
        ]
        return [
            leg
            for leg in raw
            if abs(leg.x0 - leg.x1) > _EPS or abs(leg.y0 - leg.y1) > _EPS
        ]
    raise RoutingError(f"no clear rectilinear path from {start} to {end} on net {net}")


def route_net(
    terminals: list[tuple[float, float]],
    net: int,
    obstacles: list[Obstacle],
    bounds: Bounds,
    width: float,
    clearance: float,
) -> list[Track]:
    """Connect all net terminals using a Manhattan minimum-spanning strategy."""
    if len(terminals) < 2:
        return []
    field = Field(obstacles)
    pending = list(terminals[1:])
    tracks: list[Track] = []

    def attachments() -> list[tuple[float, float]]:
        return [terminals[0]] + [
            point
            for track in tracks
            for point in ((track.x0, track.y0), (track.x1, track.y1))
        ]

    while pending:
        best: tuple[float, int, tuple[float, float]] | None = None
        for index, target in enumerate(pending):
            for point in attachments():
                cost = abs(point[0] - target[0]) + abs(point[1] - target[1])
                if best is None or (cost, index) < (best[0], best[1]):
                    best = (cost, index, point)
            for track in tracks:
                tap = _closest_point(track, target)
                cost = abs(tap[0] - target[0]) + abs(tap[1] - target[1])
                if best is None or (cost, index) < (best[0], best[1]):
                    best = (cost, index, tap)
        assert best is not None
        _cost, index, source = best
        target = pending.pop(index)
        tracks += route_edge(source, target, net, field, bounds, width, clearance)
    return tracks


def _closest_point(track: Track, point: tuple[float, float]) -> tuple[float, float]:
    """Return the closest point on an axis-aligned track."""
    if abs(track.y0 - track.y1) <= _EPS:
        return (_clamp(point[0], min(track.x0, track.x1), max(track.x0, track.x1)), track.y0)
    if abs(track.x0 - track.x1) <= _EPS:
        return (track.x0, _clamp(point[1], min(track.y0, track.y1), max(track.y0, track.y1)))
    return (track.x0, track.y0)
