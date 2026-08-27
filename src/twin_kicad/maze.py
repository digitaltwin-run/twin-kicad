"""Bounded two-layer maze routing over a conservative occupancy grid.

The router is geometry-only. It does not select project nets, write KiCad
files, run DRC or authorize a candidate. Foreign-net overlap becomes a blocked
cell rather than allowing the last rasterized object to erase earlier copper.
"""

from __future__ import annotations

import heapq
import math
from collections.abc import Iterable
from dataclasses import dataclass

from .copper import Bounds

FREE = 0
BLOCKED = -1
_LAYERS = ("B.Cu", "F.Cu")
_DIRECTIONS = ((1, 0), (-1, 0), (0, 1), (0, -1))

Cell = tuple[int, int, int]
Endpoint = tuple[float, float] | set[Cell]
SearchState = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class MazeSegment:
    net: int
    x0: float
    y0: float
    x1: float
    y1: float
    width: float
    layer: str


@dataclass(frozen=True, slots=True)
class MazeVia:
    net: int
    x: float
    y: float
    diameter: float
    drill: float


class MazeRouter:
    """A* routing with direction-aware bend cost and explicit via geometry."""

    LAYERS = _LAYERS

    def __init__(
        self,
        bounds: Bounds,
        pitch: float = 0.25,
        clearance: float = 0.2,
        route_width: float = 0.6,
        via_cost: float = 60.0,
        bend_cost: float = 2.0,
        via_diameter: float = 0.8,
        via_drill: float = 0.4,
        max_cells: int = 4_000_000,
    ) -> None:
        values = {
            "pitch": pitch,
            "clearance": clearance,
            "route_width": route_width,
            "via_cost": via_cost,
            "bend_cost": bend_cost,
            "via_diameter": via_diameter,
            "via_drill": via_drill,
        }
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError("maze routing dimensions and costs must be finite")
        bound_values = (bounds.x0, bounds.y0, bounds.x1, bounds.y1)
        if not all(math.isfinite(value) for value in bound_values):
            raise ValueError("maze routing bounds must be finite")
        if bounds.x0 >= bounds.x1 or bounds.y0 >= bounds.y1:
            raise ValueError("maze routing bounds must have positive area")
        if pitch <= 0 or route_width <= 0 or via_diameter <= 0 or via_drill <= 0:
            raise ValueError("maze routing pitch and copper dimensions must be positive")
        if clearance < 0 or via_cost < 0 or bend_cost < 0:
            raise ValueError("maze routing clearance and costs must be non-negative")
        if via_drill > via_diameter:
            raise ValueError("via drill cannot exceed via diameter")
        if max_cells < 1:
            raise ValueError("maze routing cell budget must be positive")

        self.bounds = bounds
        self.pitch = pitch
        self.clearance = clearance
        self.route_width = route_width
        self.halo = route_width / 2 + clearance
        self.via_cost = via_cost
        self.bend_cost = bend_cost
        self.via_diameter = via_diameter
        self.via_drill = via_drill
        self.columns = max(1, math.floor((bounds.x1 - bounds.x0) / pitch) + 1)
        self.rows = max(1, math.floor((bounds.y1 - bounds.y0) / pitch) + 1)
        cell_count = len(self.LAYERS) * self.columns * self.rows
        if cell_count > max_cells:
            raise ValueError(f"maze routing grid needs {cell_count} cells; limit is {max_cells}")
        self.owner = [[FREE] * (self.columns * self.rows) for _ in self.LAYERS]
        self.keepout: dict[Cell, frozenset[int]] = {}

    def _validate_point(self, x: float, y: float) -> None:
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError("maze routing coordinates must be finite")
        if not (self.bounds.x0 <= x <= self.bounds.x1 and self.bounds.y0 <= y <= self.bounds.y1):
            raise ValueError(f"point ({x}, {y}) lies outside routing bounds")

    def _clamped_cell(self, x: float, y: float) -> tuple[int, int]:
        column = round((x - self.bounds.x0) / self.pitch)
        row = round((y - self.bounds.y0) / self.pitch)
        return (
            min(self.columns - 1, max(0, column)),
            min(self.rows - 1, max(0, row)),
        )

    def cell(self, x: float, y: float) -> tuple[int, int]:
        self._validate_point(x, y)
        return self._clamped_cell(x, y)

    def point(self, column: int, row: int) -> tuple[float, float]:
        if not (0 <= column < self.columns and 0 <= row < self.rows):
            raise ValueError("maze routing cell lies outside the grid")
        return (
            self.bounds.x0 + column * self.pitch,
            self.bounds.y0 + row * self.pitch,
        )

    def _index(self, column: int, row: int) -> int:
        return row * self.columns + column

    def _layer_index(self, layer: str) -> int:
        try:
            return self.LAYERS.index(layer)
        except ValueError as exc:
            raise ValueError(f"unsupported copper layer: {layer}") from exc

    @staticmethod
    def _validate_net(net: int) -> None:
        if not isinstance(net, int) or isinstance(net, bool) or net <= 0:
            raise ValueError("maze routing requires a positive KiCad net code")

    def _claim(self, layer: int, column: int, row: int, net: int) -> None:
        index = self._index(column, row)
        current = self.owner[layer][index]
        if current == FREE:
            self.owner[layer][index] = net
        elif current != net:
            self.owner[layer][index] = BLOCKED

    def occupy_box(
        self,
        net: int,
        layer: str,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        margin: float = 0.0,
    ) -> None:
        self._validate_net(net)
        layer_index = self._layer_index(layer)
        values = (x0, y0, x1, y1, margin)
        if not all(math.isfinite(value) for value in values) or margin < 0:
            raise ValueError("occupied geometry must be finite with a non-negative margin")
        pad = margin + self.halo
        left_value = max(self.bounds.x0, min(x0, x1) - pad)
        right_value = min(self.bounds.x1, max(x0, x1) + pad)
        bottom_value = max(self.bounds.y0, min(y0, y1) - pad)
        top_value = min(self.bounds.y1, max(y0, y1) + pad)
        if left_value > right_value or bottom_value > top_value:
            return
        left, bottom = self._clamped_cell(left_value, bottom_value)
        right, top = self._clamped_cell(right_value, top_value)
        for row in range(bottom, top + 1):
            for column in range(left, right + 1):
                self._claim(layer_index, column, row, net)

    def occupy_segment(
        self,
        net: int,
        layer: str,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        width: float,
    ) -> None:
        if not math.isfinite(width) or width <= 0:
            raise ValueError("occupied track width must be positive and finite")
        self.occupy_box(net, layer, x0, y0, x1, y1, margin=width / 2)

    def add_keepout(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        allow_nets: Iterable[int] = (),
    ) -> None:
        """Block copper under a part, except explicitly allow-listed pad nets.

        Overlapping keep-outs intersect their allow-lists. The rasterized area
        is expanded by half the configured track width so a track edge cannot
        overlap the physical outline while its centre line remains outside.
        """
        values = (x0, y0, x1, y1)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("keep-out coordinates must be finite")
        allowed = frozenset(allow_nets)
        for net in allowed:
            self._validate_net(net)
        margin = self.route_width / 2
        left_value = max(self.bounds.x0, min(x0, x1) - margin)
        right_value = min(self.bounds.x1, max(x0, x1) + margin)
        bottom_value = max(self.bounds.y0, min(y0, y1) - margin)
        top_value = min(self.bounds.y1, max(y0, y1) + margin)
        if left_value > right_value or bottom_value > top_value:
            return
        left, bottom = self._clamped_cell(left_value, bottom_value)
        right, top = self._clamped_cell(right_value, top_value)
        for layer in range(len(self.LAYERS)):
            for row in range(bottom, top + 1):
                for column in range(left, right + 1):
                    cell = (layer, column, row)
                    previous = self.keepout.get(cell)
                    self.keepout[cell] = allowed if previous is None else previous & allowed

    def region(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        layers: Iterable[str],
    ) -> set[Cell]:
        values = (x0, y0, x1, y1)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("endpoint region coordinates must be finite")
        layer_indexes = [self._layer_index(layer) for layer in layers]
        if not layer_indexes:
            raise ValueError("endpoint region needs at least one copper layer")
        left_value = max(self.bounds.x0, min(x0, x1))
        right_value = min(self.bounds.x1, max(x0, x1))
        bottom_value = max(self.bounds.y0, min(y0, y1))
        top_value = min(self.bounds.y1, max(y0, y1))
        if left_value > right_value or bottom_value > top_value:
            raise ValueError("endpoint region lies outside routing bounds")
        left, bottom = self._clamped_cell(left_value, bottom_value)
        right, top = self._clamped_cell(right_value, top_value)
        return {
            (layer, column, row)
            for layer in layer_indexes
            for row in range(bottom, top + 1)
            for column in range(left, right + 1)
        }

    def _endpoint(self, endpoint: Endpoint) -> set[Cell]:
        if isinstance(endpoint, set):
            cells = endpoint
        else:
            cells = {(0, *self.cell(*endpoint))}
        for layer, column, row in cells:
            if not (0 <= layer < len(self.LAYERS)):
                raise ValueError("endpoint contains an unsupported layer index")
            if not (0 <= column < self.columns and 0 <= row < self.rows):
                raise ValueError("endpoint contains a cell outside the grid")
        return set(cells)

    def _open(self, net: int, layer: int, column: int, row: int) -> bool:
        if self.owner[layer][self._index(column, row)] not in (FREE, net):
            return False
        allowed = self.keepout.get((layer, column, row))
        return allowed is None or net in allowed

    def _via_is_clear(self, net: int, column: int, row: int) -> bool:
        extra = max(0.0, (self.via_diameter - self.route_width) / 2)
        reach = math.ceil(extra / self.pitch)
        for layer in range(len(self.LAYERS)):
            for candidate_row in range(max(0, row - reach), min(self.rows, row + reach + 1)):
                for candidate_column in range(
                    max(0, column - reach), min(self.columns, column + reach + 1)
                ):
                    if not self._open(net, layer, candidate_column, candidate_row):
                        return False
        return True

    @staticmethod
    def _heuristic(
        column: int,
        row: int,
        target_box: tuple[int, int, int, int],
        pitch: float,
    ) -> float:
        left, bottom, right, top = target_box
        dx = max(left - column, 0, column - right)
        dy = max(bottom - row, 0, row - top)
        return (dx + dy) * pitch

    def route(
        self,
        net: int,
        start: Endpoint,
        goal: Endpoint,
        budget: int = 400_000,
    ) -> list[Cell] | None:
        """Return a rectilinear cell path, or ``None`` within a bounded search."""
        self._validate_net(net)
        if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
            raise ValueError("maze routing search budget must be a positive integer")
        sources = {
            cell for cell in self._endpoint(start) if self._open(net, cell[0], cell[1], cell[2])
        }
        targets = {
            cell for cell in self._endpoint(goal) if self._open(net, cell[0], cell[1], cell[2])
        }
        if not sources or not targets:
            return None
        shared = sources & targets
        if shared:
            return [min(shared)]

        target_box = (
            min(cell[1] for cell in targets),
            min(cell[2] for cell in targets),
            max(cell[1] for cell in targets),
            max(cell[2] for cell in targets),
        )

        best: dict[SearchState, float] = {}
        came: dict[SearchState, SearchState] = {}
        queue: list[tuple[float, float, SearchState]] = []
        for layer, column, row in sorted(sources):
            state = (layer, column, row, -1)
            best[state] = 0.0
            priority = self._heuristic(column, row, target_box, self.pitch)
            heapq.heappush(queue, (priority, 0.0, state))

        steps = 0
        while queue:
            _priority, cost, state = heapq.heappop(queue)
            if cost != best.get(state):
                continue
            steps += 1
            if steps > budget:
                return None
            layer, column, row, direction = state
            if (layer, column, row) in targets:
                return self._path(came, state)

            for next_direction, (dx, dy) in enumerate(_DIRECTIONS):
                next_column, next_row = column + dx, row + dy
                if not (0 <= next_column < self.columns and 0 <= next_row < self.rows):
                    continue
                if not self._open(net, layer, next_column, next_row):
                    continue
                bend = self.bend_cost * self.pitch if direction not in (-1, next_direction) else 0.0
                candidate = cost + self.pitch + bend
                following = (layer, next_column, next_row, next_direction)
                if candidate < best.get(following, math.inf):
                    best[following] = candidate
                    came[following] = state
                    priority = candidate + self._heuristic(next_column, next_row, target_box, self.pitch)
                    heapq.heappush(queue, (priority, candidate, following))

            other_layer = 1 - layer
            if self._via_is_clear(net, column, row):
                candidate = cost + self.via_cost * self.pitch
                following = (other_layer, column, row, -1)
                if candidate < best.get(following, math.inf):
                    best[following] = candidate
                    came[following] = state
                    priority = candidate + self._heuristic(column, row, target_box, self.pitch)
                    heapq.heappush(queue, (priority, candidate, following))
        return None

    @staticmethod
    def _path(came: dict[SearchState, SearchState], state: SearchState) -> list[Cell]:
        path = [(state[0], state[1], state[2])]
        while state in came:
            state = came[state]
            path.append((state[0], state[1], state[2]))
        path.reverse()
        return path

    def to_segments(
        self,
        net: int,
        path: list[Cell],
        width: float | None = None,
    ) -> tuple[list[MazeSegment], list[MazeVia]]:
        """Merge a cell path into typed tracks and explicitly sized vias."""
        self._validate_net(net)
        selected_width = self.route_width if width is None else width
        if not math.isfinite(selected_width) or not math.isclose(
            selected_width, self.route_width, abs_tol=1e-9
        ):
            raise ValueError("output width must equal the width used to build the routing grid")
        if not path:
            raise ValueError("a non-empty maze path is required")
        segments: list[MazeSegment] = []
        vias: list[MazeVia] = []
        run_start = path[0]
        run_end = path[0]
        heading: tuple[int, int] | None = None
        for state in path[1:]:
            layer_delta = abs(state[0] - run_end[0])
            coordinate_delta = abs(state[1] - run_end[1]) + abs(state[2] - run_end[2])
            if layer_delta == 1 and coordinate_delta == 0:
                self._append_segment(segments, net, run_start, run_end, selected_width)
                x, y = self.point(run_end[1], run_end[2])
                vias.append(MazeVia(net, x, y, self.via_diameter, self.via_drill))
                run_start = run_end = state
                heading = None
                continue
            if layer_delta != 0 or coordinate_delta != 1:
                raise ValueError("maze path contains non-adjacent cells")
            step = (state[1] - run_end[1], state[2] - run_end[2])
            if heading is None or step == heading:
                heading = step
                run_end = state
                continue
            self._append_segment(segments, net, run_start, run_end, selected_width)
            run_start = run_end
            run_end = state
            heading = step
        self._append_segment(segments, net, run_start, run_end, selected_width)
        return segments, vias

    def _append_segment(
        self,
        output: list[MazeSegment],
        net: int,
        start: Cell,
        end: Cell,
        width: float,
    ) -> None:
        if start == end:
            return
        x0, y0 = self.point(start[1], start[2])
        x1, y1 = self.point(end[1], end[2])
        output.append(MazeSegment(net, x0, y0, x1, y1, width, self.LAYERS[start[0]]))
