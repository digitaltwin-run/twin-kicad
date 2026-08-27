from __future__ import annotations

import pytest

from twin_kicad.pcb import inspect_pcb
from twin_kicad.sexp import SexpError, parse

PCB = '''(kicad_pcb
  (version 20240108)
  (net 1 "GND")
  (net 2 "+3V3")
  (footprint "Button:SW" (layer "F.Cu") (at 10.5 20 90)
    (uuid fp-1)
    (property "Reference" "SW1")
    (property "Value" "Button")
    (pad "1" thru_hole circle
      (at 1.5 -2)
      (net 1 "GND")
      (uuid pad-1))
    (pad "2" thru_hole circle (at -1.5 -2))
  )
  (footprint "Legacy:R" (layer "B.Cu") (at 30 40)
    (tstamp fp-2)
    (fp_text reference "R1")
    (fp_text value "10k")
    (pad "1" smd rect (net 2 "+3V3")))
)'''


def test_inspection_returns_typed_board_connectivity_and_placement() -> None:
    board = inspect_pcb(PCB)

    assert board.version == 20240108
    assert [(net.code, net.name) for net in board.nets] == [(1, "GND"), (2, "+3V3")]
    assert board.footprints[0].reference == "SW1"
    assert board.footprints[0].uuid == "fp-1"
    assert board.footprints[0].rotation == 90
    assert board.footprints[0].pads[0].uuid == "pad-1"
    assert (board.footprints[0].pads[0].x, board.footprints[0].pads[0].y) == (1.5, -2)
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
