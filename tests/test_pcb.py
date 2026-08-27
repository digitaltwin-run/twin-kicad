from __future__ import annotations

import pytest

from twin_kicad.pcb import inspect_pcb
from twin_kicad.sexp import SexpError, parse

PCB = '''(kicad_pcb
  (version 20240108)
  (net 1 "GND")
  (net 2 "+3V3")
  (gr_line (start 0 0) (end 50 0) (layer "Edge.Cuts") (uuid edge-1))
  (gr_line (start 50 0) (end 50 40) (layer "Edge.Cuts"))
  (gr_line (start 50 40) (end 0 40) (layer "Edge.Cuts"))
  (gr_line (start 0 40) (end 0 0) (layer "Edge.Cuts"))
  (footprint "Button:SW" (layer "F.Cu") (at 10.5 20 90)
    (uuid fp-1)
    (property "Reference" "SW1")
    (property "Value" "Button")
    (fp_line (start -2 -3) (end 2 3) (layer "F.SilkS"))
    (pad "1" thru_hole circle
      (at 1.5 -2 90)
      (size 1 2)
      (layers "*.Cu" "*.Mask")
      (net 1 "GND")
      (uuid pad-1))
    (pad "2" thru_hole circle (at -1.5 -2) (size 1 1) (layers "*.Cu"))
  )
  (footprint "Legacy:R" (layer "B.Cu") (at 30 40)
    (tstamp fp-2)
    (fp_text reference "R1")
    (fp_text value "10k")
    (pad "1" smd rect (net 2 "+3V3")))
  (segment (start 1 2) (end 3 2) (width 0.5) (layer "B.Cu") (net 1) (uuid track-1))
  (via (at 3 2) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net 1) (uuid via-1))
)'''


def test_inspection_returns_typed_board_connectivity_and_placement() -> None:
    board = inspect_pcb(PCB)

    assert board.version == 20240108
    assert [(net.code, net.name) for net in board.nets] == [(1, "GND"), (2, "+3V3")]
    assert board.footprints[0].reference == "SW1"
    assert board.footprints[0].uuid == "fp-1"
    assert board.footprints[0].rotation == 90
    assert board.footprints[0].pads[0].uuid == "pad-1"
    assert board.footprints[0].pads[0].rotation == 90
    assert (board.footprints[0].pads[0].x, board.footprints[0].pads[0].y) == (1.5, -2)
    assert board.footprints[0].pads[0].copper_layers == ("F.Cu", "B.Cu")
    assert board.footprints[0].pad_center(board.footprints[0].pads[0]) == pytest.approx(
        (12.5, 21.5)
    )
    assert board.footprints[0].pad_bounds(board.footprints[0].pads[0]) == pytest.approx(
        (11.5, 21.0, 13.5, 22.0)
    )
    assert board.footprints[0].body_bounds() == pytest.approx((7.5, 18.0, 13.5, 22.0))
    assert board.tracks[0].uuid == "track-1"
    assert board.tracks[0].width == 0.5
    assert board.vias[0].uuid == "via-1"
    assert (board.vias[0].diameter, board.vias[0].drill) == (0.8, 0.4)
    assert board.edge_bounds() == (0.0, 0.0, 50.0, 40.0)
    assert board.pad_nets() == {
        ("SW1", "1"): "GND",
        ("SW1", "2"): "",
        ("R1", "1"): "+3V3",
    }


def test_inspection_accepts_a_shared_parsed_root() -> None:
    assert inspect_pcb(parse(PCB)) == inspect_pcb(PCB)


def test_inspection_rejects_a_non_board_root() -> None:
    with pytest.raises(SexpError, match="kicad_pcb"):
        inspect_pcb("(kicad_sch (version 20240108))")
