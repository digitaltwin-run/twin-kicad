"""Normalize KiCad XML netlists into a typed, policy-free model."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any


class NetlistError(ValueError):
    """KiCad XML cannot be normalized into a schematic netlist."""


@dataclass(frozen=True, slots=True)
class NetlistPin:
    number: str
    name: str
    type: str

    def as_dict(self) -> dict[str, str]:
        return {"number": self.number, "name": self.name, "type": self.type}


@dataclass(frozen=True, slots=True)
class NetlistComponent:
    reference: str
    part: str
    value: str
    footprint: str
    pins: tuple[NetlistPin, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "part": self.part,
            "value": self.value,
            "footprint": self.footprint,
            "pins": [pin.as_dict() for pin in self.pins],
        }


@dataclass(frozen=True, slots=True)
class NetlistNode:
    reference: str
    pin: str
    function: str
    type: str

    def as_dict(self) -> dict[str, str]:
        return {
            "reference": self.reference,
            "pin": self.pin,
            "function": self.function,
            "type": self.type,
        }


@dataclass(frozen=True, slots=True)
class NetlistNet:
    code: str
    name: str
    nodes: tuple[NetlistNode, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "nodes": [node.as_dict() for node in self.nodes],
        }


@dataclass(frozen=True, slots=True)
class SchematicNetlist:
    components: tuple[NetlistComponent, ...]
    nets: tuple[NetlistNet, ...]

    def as_dict(self, source: str = "") -> dict[str, Any]:
        return {
            "schema_id": "twin-kicad.netlist/v1",
            "source": source,
            "components": [component.as_dict() for component in self.components],
            "nets": [net.as_dict() for net in self.nets],
        }


def _content(element: ET.Element | None) -> str:
    return (element.text or "") if element is not None else ""


def parse_netlist_xml(xml_text: str) -> SchematicNetlist:
    """Parse the `kicadxml` output produced by ``kicad-cli sch export netlist``."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise NetlistError(f"invalid KiCad XML netlist: {exc}") from exc

    pins_by_part: dict[str, tuple[NetlistPin, ...]] = {}
    libparts = root.find("libparts")
    for libpart in libparts if libparts is not None else []:
        key = f"{libpart.get('lib', '')}:{libpart.get('part', '')}"
        pins = libpart.find("pins")
        pins_by_part[key] = tuple(
            NetlistPin(
                number=pin.get("num", ""),
                name=pin.get("name", ""),
                type=pin.get("type", ""),
            )
            for pin in (pins if pins is not None else [])
        )

    components: list[NetlistComponent] = []
    component_elements = root.find("components")
    for component in component_elements if component_elements is not None else []:
        libsource = component.find("libsource")
        part = (
            f"{libsource.get('lib', '')}:{libsource.get('part', '')}"
            if libsource is not None
            else ""
        )
        components.append(
            NetlistComponent(
                reference=component.get("ref", ""),
                part=part,
                value=_content(component.find("value")),
                footprint=_content(component.find("footprint")),
                pins=pins_by_part.get(part, ()),
            )
        )

    nets: list[NetlistNet] = []
    net_elements = root.find("nets")
    for net in net_elements if net_elements is not None else []:
        nets.append(
            NetlistNet(
                code=net.get("code", ""),
                name=net.get("name", ""),
                nodes=tuple(
                    NetlistNode(
                        reference=node.get("ref", ""),
                        pin=node.get("pin", ""),
                        function=node.get("pinfunction", ""),
                        type=node.get("pintype", ""),
                    )
                    for node in net.findall("node")
                ),
            )
        )
    return SchematicNetlist(components=tuple(components), nets=tuple(nets))
