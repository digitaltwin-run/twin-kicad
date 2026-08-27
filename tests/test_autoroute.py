from __future__ import annotations

import pytest

from twin_kicad import Bounds, Box, NetTask, autoroute

BOUNDS = Bounds(0, 0, 100, 100)


def test_multi_net_router_preserves_width_and_rectilinear_geometry() -> None:
    result = autoroute(
        [
            NetTask(code=1, name="POWER", terminals=[(10, 20), (90, 80)], width=0.6),
            NetTask(code=2, name="SIGNAL", terminals=[(10, 80), (90, 20)], width=0.5),
        ],
        [Box(0, 45, 40, 55, 60)],
        BOUNDS,
        static_top=[],
    )

    assert result.unrouted == []
    expected = {1: 0.6, 2: 0.5}
    for code, track, width, _layer in result.tracks:
        assert width == expected[code]
        assert track.x0 == track.x1 or track.y0 == track.y1


def test_router_uses_the_declared_layer_first() -> None:
    result = autoroute(
        [NetTask(code=1, name="A", terminals=[(10, 10), (90, 10)], width=0.5, layer="F.Cu")],
        [],
        BOUNDS,
        static_top=[],
    )

    assert {layer for _code, _track, _width, layer in result.tracks} == {"F.Cu"}


def test_router_rejects_unsafe_or_ambiguous_jobs() -> None:
    with pytest.raises(ValueError, match="width"):
        NetTask(code=1, name="A", terminals=[(0, 0), (1, 1)], width=0)
    with pytest.raises(ValueError, match="unique net names"):
        autoroute(
            [NetTask(1, "A", [], 0.5), NetTask(2, "A", [], 0.5)],
            [],
            BOUNDS,
        )
