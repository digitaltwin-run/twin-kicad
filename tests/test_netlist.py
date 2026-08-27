from __future__ import annotations

import pytest

from twin_kicad.netlist import NetlistError, parse_netlist_xml

XML = '''<?xml version="1.0" encoding="utf-8"?>
<export>
  <components>
    <comp ref="U1">
      <value>MCU</value>
      <footprint>Package:QFN</footprint>
      <libsource lib="local" part="MCU"/>
    </comp>
  </components>
  <libparts>
    <libpart lib="local" part="MCU">
      <pins>
        <pin num="1" name="GND" type="power_in"/>
        <pin num="2" name="GP0" type="bidirectional"/>
      </pins>
    </libpart>
  </libparts>
  <nets>
    <net code="1" name="GND">
      <node ref="U1" pin="1" pinfunction="GND" pintype="power_in"/>
    </net>
  </nets>
</export>'''


def test_xml_netlist_preserves_components_pins_nets_and_nodes() -> None:
    document = parse_netlist_xml(XML)

    assert document.as_dict("panel.kicad_sch") == {
        "schema_id": "twin-kicad.netlist/v1",
        "source": "panel.kicad_sch",
        "components": [{
            "reference": "U1",
            "part": "local:MCU",
            "value": "MCU",
            "footprint": "Package:QFN",
            "pins": [
                {"number": "1", "name": "GND", "type": "power_in"},
                {"number": "2", "name": "GP0", "type": "bidirectional"},
            ],
        }],
        "nets": [{
            "code": "1",
            "name": "GND",
            "nodes": [{
                "reference": "U1",
                "pin": "1",
                "function": "GND",
                "type": "power_in",
            }],
        }],
    }


def test_invalid_xml_fails_closed() -> None:
    with pytest.raises(NetlistError, match="invalid KiCad XML"):
        parse_netlist_xml("<export>")
