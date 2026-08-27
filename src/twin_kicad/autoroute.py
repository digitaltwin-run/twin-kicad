"""Bounded multi-net rip-up-and-retry routing over rectilinear primitives.

This module is geometry-only. Consumers select nets, widths and layers and
remain responsible for candidate creation, DRC and human approval.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable

from .copper import Bounds, Box, Field, RoutingError, Track, route_edge


@dataclass
class NetTask:
    code: int
    name: str
    terminals: list[tuple[float, float]]
    width: float
    layer: str = "B.Cu"

    def __post_init__(self) -> None:
        if self.code < 0 or not self.name.strip():
            raise ValueError("a net needs a non-negative code and a non-empty name")
        if not math.isfinite(self.width) or self.width <= 0:
            raise ValueError(f"net {self.name}: width must be positive")
        if self.layer not in {"F.Cu", "B.Cu"}:
            raise ValueError(f"net {self.name}: unsupported layer {self.layer}")
        if any(not all(math.isfinite(value) for value in point) for point in self.terminals):
            raise ValueError(f"net {self.name}: terminal coordinates must be finite")


@dataclass
class AutorouteResult:
    tracks: list[tuple[int, Track, float, str]] = field(default_factory=list)
    vias: list[tuple[int, float, float]] = field(default_factory=list)
    unrouted: list[str] = field(default_factory=list)
    rounds: int = 0
    ripped: dict[str, int] = field(default_factory=dict)


def _box(code: int, track: Track, width: float) -> Box:
    half = width / 2
    return Box(
        code,
        min(track.x0, track.x1) - half,
        min(track.y0, track.y1) - half,
        max(track.x0, track.x1) + half,
        max(track.y0, track.y1) + half,
    )


def _corridor(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float, float, float]:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


def _intersects(track: Track, corridor: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = corridor
    return not (
        max(track.x0, track.x1) < x0
        or min(track.x0, track.x1) > x1
        or max(track.y0, track.y1) < y0
        or min(track.y0, track.y1) > y1
    )


def autoroute(
    tasks: list[NetTask],
    static: Iterable[Box],
    bounds: Bounds,
    static_top: Iterable[Box] | None = None,
    clearance: float = 0.2,
    max_rounds: int = 6,
    max_rips: int = 3,
) -> AutorouteResult:
    """Route multiple nets, retrying nets displaced by a blocked connection."""
    fixed = list(static)
    if len({task.name for task in tasks}) != len(tasks):
        raise ValueError("routing tasks need unique net names")
    if len({task.code for task in tasks}) != len(tasks):
        raise ValueError("routing tasks need unique net codes")
    if not math.isfinite(clearance) or clearance < 0:
        raise ValueError("clearance must be non-negative")
    if max_rounds < 1 or max_rips < 0:
        raise ValueError("routing budget is invalid")

    top = list(static_top or [])
    laid: dict[str, list[tuple[Track, float, str]]] = {}
    result = AutorouteResult()
    pending = [task.name for task in tasks]
    by_name = {task.name: task for task in tasks}

    for round_index in range(max_rounds):
        result.rounds = round_index + 1
        if not pending:
            break
        pending.sort(key=lambda name: (-result.ripped.get(name, 0), len(by_name[name].terminals)))
        stalled: list[str] = []
        ripped_this_round = False
        for name in pending:
            task = by_name[name]
            obstacles = fixed + [
                _box(by_name[other].code, track, width)
                for other, tracks in laid.items()
                if other != name
                for track, width, layer in tracks
                if layer == "B.Cu"
            ]
            obstacles_top = top + [
                _box(by_name[other].code, track, width)
                for other, tracks in laid.items()
                if other != name
                for track, width, layer in tracks
                if layer == "F.Cu"
            ]
            routed, blockers = _route_one(task, obstacles, obstacles_top, laid, bounds, clearance)
            if routed is not None:
                laid[name] = routed
                continue
            stalled.append(name)
            for blocker in blockers:
                if result.ripped.get(blocker, 0) >= max_rips or blocker not in laid:
                    continue
                del laid[blocker]
                result.ripped[blocker] = result.ripped.get(blocker, 0) + 1
                ripped_this_round = True
                stalled.append(blocker)
        pending = sorted(set(stalled), key=stalled.index)
        if pending and not ripped_this_round:
            break

    for name, tracks in laid.items():
        code = by_name[name].code
        result.tracks += [(code, track, width, layer) for track, width, layer in tracks]
        for track, _width, layer in tracks:
            if layer != "F.Cu":
                continue
            for point in ((track.x0, track.y0), (track.x1, track.y1)):
                if not any(
                    other == "B.Cu"
                    and (
                        abs(other_track.x0 - point[0]) < 1e-6
                        and abs(other_track.y0 - point[1]) < 1e-6
                        or abs(other_track.x1 - point[0]) < 1e-6
                        and abs(other_track.y1 - point[1]) < 1e-6
                    )
                    for other_track, _other_width, other in tracks
                ):
                    continue
                if (code, point[0], point[1]) not in result.vias:
                    result.vias.append((code, point[0], point[1]))
    result.unrouted = sorted(set(pending))
    return result


def _route_one(
    task: NetTask,
    obstacles: list[Box],
    obstacles_top: list[Box],
    laid: dict[str, list[tuple[Track, float, str]]],
    bounds: Bounds,
    clearance: float,
) -> tuple[list[tuple[Track, float, str]] | None, list[str]]:
    terminals = sorted(set(task.terminals))
    if len(terminals) < 2:
        return [], []
    connected = [terminals[0]]
    pending = list(terminals[1:])
    tracks: list[tuple[Track, float, str]] = []
    while pending:
        progress = False
        ordered = sorted(
            pending,
            key=lambda point: min(
                abs(point[0] - anchor[0]) + abs(point[1] - anchor[1]) for anchor in connected
            ),
        )
        for point in ordered:
            anchor = min(connected, key=lambda item: abs(point[0] - item[0]) + abs(point[1] - item[1]))
            here = obstacles + [
                _box(task.code, track, width) for track, width, layer in tracks if layer == "B.Cu"
            ]
            here_top = obstacles_top + [
                _box(task.code, track, width) for track, width, layer in tracks if layer == "F.Cu"
            ]
            layers = (
                (task.layer, here if task.layer == "B.Cu" else here_top),
                ("F.Cu" if task.layer == "B.Cu" else "B.Cu", here_top if task.layer == "B.Cu" else here),
            )
            for layer, field_boxes in layers:
                try:
                    legs = route_edge(
                        anchor, point, task.code, Field(field_boxes), bounds, task.width, clearance
                    )
                except RoutingError:
                    continue
                tracks += [(leg, task.width, layer) for leg in legs]
                connected.append(point)
                connected += [(leg.x1, leg.y1) for leg in legs]
                pending.remove(point)
                progress = True
                break
            if progress:
                break
        if progress:
            continue
        target = pending[0]
        anchor = min(connected, key=lambda item: abs(target[0] - item[0]) + abs(target[1] - item[1]))
        corridor = _corridor(anchor, target)
        blockers = [
            name
            for name, entries in laid.items()
            if name != task.name and any(_intersects(track, corridor) for track, _width, _layer in entries)
        ]
        return None, blockers
    return tracks, []
