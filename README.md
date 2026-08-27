# twin-kicad

`twin-kicad` provides the shared, dependency-free foundation for reading and
patching KiCad S-expression files without reformatting unrelated source text.

The package owns mechanics that can be reused without project policy:

- tokenization with source offsets;
- a single-root S-expression tree;
- stable node lookup helpers;
- compatibility range scanning for existing Viewer checks;
- validated, non-overlapping text replacements;
- typed inspection of PCB nets, footprints, local/absolute pad geometry,
  tracks, vias and line-based board outlines;
- typed normalization of Eeschema's authoritative XML netlist;
- deterministic rectilinear copper routing over typed geometry primitives;
- bounded two-layer maze routing with explicit track and via dimensions.

It does not own LLM routing, candidates, approval, event history, project
authority, DRC/ERC decisions or the `wellmanifest/pcb` policy vocabulary.
TwinStudio and Viewer remain responsible for those workflows while adopting
this package as their one KiCad syntax implementation.

## Usage

```python
from twin_kicad.sexp import children, head, parse, text

root = parse('(kicad_pcb (version 20240108) (net 1 "GND"))')
nets = [node for node in children(root) if head(node) == "net"]
assert text(nets[0], 2) == "GND"
```

For source-preserving edits, retain node offsets and apply replacements from
the end of the document:

```python
from twin_kicad.sexp import apply_replacements

updated = apply_replacements(source, [(start, end, replacement)])
```

PCB inspection exposes the same connectivity and placement model to every
consumer, including both legacy `fp_text` and current `property` fields:

```python
from twin_kicad.pcb import inspect_pcb

board = inspect_pcb(source)
pad_nets = board.pad_nets()
board_bounds = board.edge_bounds()
absolute_pad_bounds = board.footprints[0].pad_bounds(board.footprints[0].pads[0])
```

Footprint-local pad coordinates are transformed by the footprint placement;
pad angles are already board-absolute, matching KiCad's saved PCB semantics.

Logical schematic connectivity comes from Eeschema rather than visual wire
guessing:

```python
from twin_kicad.netlist import parse_netlist_xml

netlist = parse_netlist_xml(kicad_xml).as_dict("panel.kicad_sch")
```

The copper router accepts geometry selected by a consumer and returns track
legs. It does not modify a board or imply approval:

```python
from twin_kicad.copper import Bounds, Box, route_edge

tracks = route_edge(
    (0.0, 0.0),
    (10.0, 0.0),
    net=1,
    obstacles=[Box(net=2, x0=4.0, y0=-1.0, x1=6.0, y1=1.0)],
    bounds=Bounds(-2.0, -3.0, 12.0, 3.0),
    width=0.2,
    clearance=0.2,
)
```

For several competing nets, `autoroute` adds bounded rip-up-and-retry while
preserving every requested width. It prefers each task's declared layer and
may use the other copper layer, but never narrows a track to gain reachability:

```python
from twin_kicad import Bounds, NetTask, autoroute

result = autoroute(
    [NetTask(code=1, name="GND", terminals=[(0, 0), (10, 10)], width=0.6)],
    static=[],
    bounds=Bounds(-1, -1, 11, 11),
)
assert not result.unrouted
```

Dense layouts can use the grid-based maze primitive when a short Hanan-style
route is insufficient. Endpoints may be restricted to the actual copper area
and layers of a pad. The result retains the exact width and via geometry used
for collision checks:

```python
from twin_kicad import Bounds, MazeRouter

router = MazeRouter(
    Bounds(0, 0, 50, 50),
    route_width=0.6,
    clearance=0.2,
    via_diameter=0.8,
    via_drill=0.4,
)
start = router.region(4, 4, 6, 6, ["B.Cu"])
goal = router.region(44, 44, 46, 46, ["B.Cu"])
router.add_keepout(20, 20, 30, 30, allow_nets=[1])
path = router.route(net=1, start=start, goal=goal)
if path is not None:
    tracks, vias = router.to_segments(net=1, path=path)
```

Overlapping foreign nets are fail-closed blocked cells. Coordinates outside
the routing bounds, net zero, invalid dimensions and output widths different
from the raster width are rejected instead of being silently corrected.
Keep-outs block both copper layers, intersect exceptions when they overlap and
may allow only the explicit nets needed to reach pads belonging to the part.

## Architectural boundary

`wellmanifest/pcb` is the HOME of PCB/SCH rules and project context. This
package implements KiCad syntax mechanics and must not define a competing
style, authority or dependency vocabulary.

Parity policy, natural language interpretation and effect authorization stay
outside this package. The copper router finds geometric paths only: the
consumer still chooses the net, rules, candidate and approval.
