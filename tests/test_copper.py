from __future__ import annotations

import pytest

from twin_kicad.copper import (
    Bounds,
    Box,
    RoutingError,
    route_edge,
    route_net,
    segment_box_distance,
    segment_distance,
    track_is_clear,
)


def test_route_edge_returns_clear_path_around_foreign_pad() -> None:
    obstacle = Box(net=2, x0=4.0, y0=-1.0, x1=6.0, y1=1.0)
    path = route_edge(
        (0.0, 0.0),
        (10.0, 0.0),
        net=1,
        obstacles=[obstacle],
        bounds=Bounds(-2.0, -3.0, 12.0, 3.0),
        width=0.2,
        clearance=0.2,
    )

    assert path[0].x0 == 0.0 and path[0].y0 == 0.0
    assert path[-1].x1 == 10.0 and path[-1].y1 == 0.0
    assert all(track_is_clear(track, 1, [obstacle], 0.1, 0.2) for track in path)
    assert any(abs(track.y0) > 1.0 or abs(track.y1) > 1.0 for track in path)


def test_route_edge_fails_when_foreign_copper_blocks_bounds() -> None:
    wall = Box(net=2, x0=4.0, y0=-3.0, x1=6.0, y1=3.0)

    with pytest.raises(RoutingError):
        route_edge(
            (0.0, 0.0),
            (10.0, 0.0),
            net=1,
            obstacles=[wall],
            bounds=Bounds(-2.0, -3.0, 12.0, 3.0),
            width=0.2,
            clearance=0.2,
        )


def _length(tracks: list) -> float:
    return sum(abs(track.x1 - track.x0) + abs(track.y1 - track.y0) for track in tracks)


def test_branch_taps_into_routed_copper_instead_of_returning_to_a_pad() -> None:
    tracks = route_net(
        [(0.0, 0.0), (0.0, 100.0), (20.0, 50.0)],
        net=1,
        obstacles=[],
        bounds=Bounds(-20.0, -20.0, 120.0, 130.0),
        width=0.2,
        clearance=0.2,
    )

    assert _length(tracks) == pytest.approx(120.0)
    assert all(track_is_clear(track, 1, [], 0.1, 0.2) for track in tracks)


def test_collinear_pads_are_unaffected_by_the_tap_in_rule() -> None:
    tracks = route_net(
        [(101.59, 84.0), (229.2, 95.75), (229.2, 97.25)],
        net=1,
        obstacles=[],
        bounds=Bounds(-20.0, -20.0, 260.0, 130.0),
        width=0.2,
        clearance=0.2,
    )

    assert _length(tracks) == pytest.approx(140.86, abs=0.01)


def test_public_distance_primitives_measure_centerlines_and_box_edges() -> None:
    assert segment_distance(0, 0, 4, 0, 2, 3, 2, 5) == 3
    assert segment_distance(0, 0, 4, 0, 2, -1, 2, 1) == 0
    assert segment_box_distance(0, 0, 4, 0, Box(1, 1, 2, 3, 4)) == 2
