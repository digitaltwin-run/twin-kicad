"""Typed, policy-free inspection of KiCad PCB documents."""
from __future__ import annotations

import math
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
    kind: str = ""
    shape: str = ""
    rotation: float = 0.0
    width: float = 0.0
    height: float = 0.0
    layers: tuple[str, ...] = ()

    @property
    def copper_layers(self) -> tuple[str, ...]:
        if "*.Cu" in self.layers:
            return ("F.Cu", "B.Cu")
        return tuple(layer for layer in self.layers if layer.endswith(".Cu"))


@dataclass(frozen=True, slots=True)
class PcbGraphicLine:
    x0: float
    y0: float
    x1: float
    y1: float
    layer: str
    uuid: str = ""


@dataclass(frozen=True, slots=True)
class PcbTrack:
    net_code: int
    x0: float
    y0: float
    x1: float
    y1: float
    width: float
    layer: str
    uuid: str = ""


@dataclass(frozen=True, slots=True)
class PcbVia:
    net_code: int
    x: float
    y: float
    diameter: float
    drill: float
    layers: tuple[str, ...]
    uuid: str = ""


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
    graphics: tuple[PcbGraphicLine, ...] = ()

    def point(self, x: float, y: float) -> tuple[float, float]:
        """Transform a footprint-local point into absolute board coordinates."""
        radians = math.radians(self.rotation)
        cosine, sine = math.cos(radians), math.sin(radians)
        return (
            self.x + x * cosine - y * sine,
            self.y + x * sine + y * cosine,
        )

    def pad_center(self, pad: PcbPad) -> tuple[float, float]:
        return self.point(pad.x, pad.y)

    def pad_bounds(self, pad: PcbPad) -> tuple[float, float, float, float]:
        """Axis-aligned board bounds enclosing a potentially rotated pad."""
        x, y = self.pad_center(pad)
        # KiCad stores the pad orientation in board coordinates. Rotating a
        # footprint updates both its own angle and every pad's `(at ... angle)`;
        # adding the two would rotate rectangular pad bounds twice.
        radians = math.radians(pad.rotation)
        cosine, sine = abs(math.cos(radians)), abs(math.sin(radians))
        half_x = cosine * pad.width / 2 + sine * pad.height / 2
        half_y = sine * pad.width / 2 + cosine * pad.height / 2
        return (x - half_x, y - half_y, x + half_x, y + half_y)

    def body_bounds(self) -> tuple[float, float, float, float] | None:
        """Axis-aligned bounds of footprint graphics, falling back to pads."""
        points = [
            self.point(x, y)
            for line in self.graphics
            for x, y in ((line.x0, line.y0), (line.x1, line.y1))
        ]
        if points:
            return (
                min(point[0] for point in points),
                min(point[1] for point in points),
                max(point[0] for point in points),
                max(point[1] for point in points),
            )
        pad_bounds = [self.pad_bounds(pad) for pad in self.pads if pad.width > 0 and pad.height > 0]
        if not pad_bounds:
            return None
        return (
            min(bounds[0] for bounds in pad_bounds),
            min(bounds[1] for bounds in pad_bounds),
            max(bounds[2] for bounds in pad_bounds),
            max(bounds[3] for bounds in pad_bounds),
        )


@dataclass(frozen=True, slots=True)
class PcbBoard:
    version: int | None
    nets: tuple[PcbNet, ...]
    footprints: tuple[PcbFootprint, ...]
    tracks: tuple[PcbTrack, ...] = ()
    vias: tuple[PcbVia, ...] = ()
    graphics: tuple[PcbGraphicLine, ...] = ()

    def pad_nets(self) -> dict[tuple[str, str], str]:
        """Return ``(reference, pad number) -> net name`` for every placed pad."""
        return {
            (footprint.reference, pad.number): pad.net_name
            for footprint in self.footprints
            for pad in footprint.pads
            if footprint.reference
        }

    def edge_bounds(self) -> tuple[float, float, float, float] | None:
        edges = [line for line in self.graphics if line.layer == "Edge.Cuts"]
        if not edges:
            return None
        xs = [coordinate for line in edges for coordinate in (line.x0, line.x1)]
        ys = [coordinate for line in edges for coordinate in (line.y0, line.y1)]
        return (min(xs), min(ys), max(xs), max(ys))


def _integer(value: str, default: int = 0) -> int:
    try:
        return int(value)
    except ValueError:
        return default


def _stamp(node: Node) -> str:
    return text(child(node, "uuid") or child(node, "tstamp"), 1)


def _texts(node: Node | None, start: int = 1) -> tuple[str, ...]:
    values: list[str] = []
    index = start
    while node is not None:
        value = text(node, index)
        if not value:
            break
        values.append(value)
        index += 1
    return tuple(values)


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
    size = child(node, "size")
    return PcbPad(
        number=text(node, 1),
        uuid=_stamp(node),
        net_code=_integer(text(net, 1)) if net is not None else 0,
        net_name=text(net, 2) if net is not None else "",
        x=number(at, 1) if at is not None else 0.0,
        y=number(at, 2) if at is not None else 0.0,
        kind=text(node, 2),
        shape=text(node, 3),
        rotation=number(at, 3) if at is not None else 0.0,
        width=number(size, 1) if size is not None else 0.0,
        height=number(size, 2) if size is not None else 0.0,
        layers=_texts(child(node, "layers")),
    )


def _line(node: Node) -> PcbGraphicLine:
    start = child(node, "start")
    end = child(node, "end")
    return PcbGraphicLine(
        x0=number(start, 1),
        y0=number(start, 2),
        x1=number(end, 1),
        y1=number(end, 2),
        layer=text(child(node, "layer"), 1),
        uuid=_stamp(node),
    )


def _track(node: Node) -> PcbTrack:
    start = child(node, "start")
    end = child(node, "end")
    return PcbTrack(
        net_code=_integer(text(child(node, "net"), 1)),
        x0=number(start, 1),
        y0=number(start, 2),
        x1=number(end, 1),
        y1=number(end, 2),
        width=number(child(node, "width"), 1),
        layer=text(child(node, "layer"), 1),
        uuid=_stamp(node),
    )


def _via(node: Node) -> PcbVia:
    at = child(node, "at")
    return PcbVia(
        net_code=_integer(text(child(node, "net"), 1)),
        x=number(at, 1),
        y=number(at, 2),
        diameter=number(child(node, "size"), 1),
        drill=number(child(node, "drill"), 1),
        layers=_texts(child(node, "layers")),
        uuid=_stamp(node),
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
        graphics=tuple(_line(line) for line in children(node, "fp_line")),
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
        tracks=tuple(_track(node) for node in children(root, "segment")),
        vias=tuple(_via(node) for node in children(root, "via")),
        graphics=tuple(_line(node) for node in children(root, "gr_line")),
    )


def inspect_footprint(source: str | Node) -> PcbFootprint:
    """Inspect a standalone ``.kicad_mod`` without inventing board context."""
    root = parse(source) if isinstance(source, str) else source
    if head(root) not in {"footprint", "module"}:
        raise SexpError("expected a footprint root expression")
    return _footprint(root)
