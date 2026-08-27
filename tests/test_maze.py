from __future__ import annotations

import pytest

from twin_kicad import BLOCKED, Bounds, MazeRouter

AREA = Bounds(0, 0, 50, 50)


def _router(**kwargs) -> MazeRouter:
    return MazeRouter(AREA, pitch=0.5, route_width=0.5, **kwargs)


def test_route_bends_around_an_obstacle_and_emits_rectilinear_width() -> None:
    router = _router()
    for layer in MazeRouter.LAYERS:
        router.occupy_box(9, layer, 20, 5, 22, 45)

    path = router.route(1, (5, 25), (45, 25))
    assert path is not None
    segments, vias = router.to_segments(1, path)

    assert 2 <= len(segments) <= 6
    assert vias == []
    assert all(segment.width == 0.5 for segment in segments)
    assert all(segment.x0 == segment.x1 or segment.y0 == segment.y1 for segment in segments)


def test_single_layer_wall_requires_two_sized_vias_for_bottom_pads() -> None:
    router = _router(via_diameter=0.9, via_drill=0.45)
    router.occupy_box(9, "B.Cu", 20, 0, 22, 50)
    start = router.region(4.5, 24.5, 5.5, 25.5, ["B.Cu"])
    goal = router.region(44.5, 24.5, 45.5, 25.5, ["B.Cu"])

    path = router.route(1, start, goal)
    assert path is not None
    segments, vias = router.to_segments(1, path)

    assert path[0][0] == 0 and path[-1][0] == 0
    assert len(vias) == 2
    assert all(via.diameter == 0.9 and via.drill == 0.45 for via in vias)
    assert {segment.layer for segment in segments} == {"B.Cu", "F.Cu"}


def test_route_may_end_anywhere_inside_a_layer_restricted_pad() -> None:
    router = _router()
    goal = router.region(40, 20, 44, 30, ["B.Cu"])

    path = router.route(1, (5, 25), goal)

    assert path is not None
    assert path[-1] in goal


def test_foreign_overlap_becomes_blocked_instead_of_erasing_an_obstacle() -> None:
    router = _router()
    for layer in MazeRouter.LAYERS:
        router.occupy_box(9, layer, 20, 0, 22, 50)
        router.occupy_box(1, layer, 20, 0, 22, 50)

    column, row = router.cell(21, 25)
    assert all(grid[router._index(column, row)] == BLOCKED for grid in router.owner)
    assert router.route(1, (5, 25), (45, 25)) is None


def test_router_may_cross_only_uncontested_copper_of_its_own_net() -> None:
    router = _router()
    router.occupy_box(1, "B.Cu", 20, 0, 22, 50)

    path = router.route(1, (5, 25), (45, 25))
    assert path is not None
    segments, vias = router.to_segments(1, path)

    assert vias == []
    assert {segment.layer for segment in segments} == {"B.Cu"}


def test_wall_on_both_layers_returns_no_route_within_budget() -> None:
    router = _router()
    for layer in MazeRouter.LAYERS:
        router.occupy_box(9, layer, 20, 0, 22, 50)

    assert router.route(1, (5, 25), (45, 25)) is None


def test_output_cannot_claim_a_width_other_than_the_raster_width() -> None:
    router = _router()
    path = router.route(1, (5, 5), (45, 45))
    assert path is not None

    with pytest.raises(ValueError, match="output width"):
        router.to_segments(1, path, width=1.0)


def test_invalid_geometry_and_silent_coordinate_clamping_are_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        MazeRouter(AREA, pitch=0)
    with pytest.raises(ValueError, match="drill"):
        MazeRouter(AREA, via_diameter=0.4, via_drill=0.8)

    router = _router()
    with pytest.raises(ValueError, match="outside"):
        router.route(1, (-1, 5), (10, 5))
    with pytest.raises(ValueError, match="positive KiCad net"):
        router.route(0, (5, 5), (10, 5))
