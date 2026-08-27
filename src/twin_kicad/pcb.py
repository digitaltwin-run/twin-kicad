"""Typed, policy-free inspection of KiCad PCB documents."""
from __future__ import annotations

from dataclasses import dataclass

from .sexp import Node, SexpError, child, children, head, number, parse, text


@dataclass(frozen=True, slots=True)
class PcbNet:
    code: int
    name: str


@dataclass(frozen=True, slots=True)
class PcbPad:
    number: str
    uuid: str
    net_code: int
    net_name: str
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class PcbFootprint:
    library_id: str
    uuid: str
    reference: str
    value: str
    layer: str
    x: float
    y: float
    rotation: float
    pads: tuple[PcbPad, ...]


@dataclass(frozen=True, slots=True)
class PcbBoard:
    version: int | None
    nets: tuple[PcbNet, ...]
    footprints: tuple[PcbFootprint, ...]

    def pad_nets(self) -> dict[tuple[str, str], str]:
        """Return ``(reference, pad number) -> net name`` for every placed pad."""
        return {
            (footprint.reference, pad.number): pad.net_name
            for footprint in self.footprints
            for pad in footprint.pads
            if footprint.reference
        }


def _integer(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except ValueError:
        return default


def _stamp(node: Node) -> str:
    return text(child(node, "uuid") or child(node, "tstamp"), 1)


def _field(node: Node, name: str) -> str:
    """Read legacy ``fp_text`` and current footprint ``property`` fields."""
    expected = name.casefold()
    for nested in children(node):
        if head(nested) == "fp_text" and text(nested, 1).casefold() == expected:
            return text(nested, 2)
        if head(nested) == "property" and text(nested, 1).casefold() == expected:
            return text(nested, 2)
    return ""


def _pad(node: Node) -> PcbPad:
    net = child(node, "net")
    at = child(node, "at")
    return PcbPad(
        number=text(node, 1),
        uuid=_stamp(node),
        net_code=_integer(text(net, 1)) if net is not None else 0,
        net_name=text(net, 2) if net is not None else "",
        x=number(at, 1) if at is not None else 0.0,
        y=number(at, 2) if at is not None else 0.0,
    )


def _footprint(node: Node) -> PcbFootprint:
    at = child(node, "at")
    layer = child(node, "layer")
    return PcbFootprint(
        library_id=text(node, 1),
        uuid=_stamp(node),
        reference=_field(node, "reference"),
        value=_field(node, "value"),
        layer=text(layer, 1),
        x=number(at, 1) if at is not None else 0.0,
        y=number(at, 2) if at is not None else 0.0,
        rotation=number(at, 3) if at is not None else 0.0,
        pads=tuple(_pad(pad) for pad in children(node, "pad")),
    )


def inspect_pcb(source: str | Node) -> PcbBoard:
    """Inspect one KiCad PCB source or an already parsed root node."""
    root = parse(source) if isinstance(source, str) else source
    if head(root) != "kicad_pcb":
        raise SexpError("expected a kicad_pcb root expression")
    version_text = text(child(root, "version"), 1)
    version = _integer(version_text) if version_text else None
    nets = tuple(
        PcbNet(code=int(text(node, 1)), name=text(node, 2))
        for node in children(root, "net")
        if text(node, 1).isdigit()
    )
    return PcbBoard(
        version=version,
        nets=nets,
        footprints=tuple(_footprint(node) for node in children(root, "footprint")),
    )
