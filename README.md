# twin-kicad

`twin-kicad` provides the shared, dependency-free foundation for reading and
patching KiCad S-expression files without reformatting unrelated source text.

The package owns mechanics that can be reused without project policy:

- tokenization with source offsets;
- a single-root S-expression tree;
- stable node lookup helpers;
- compatibility range scanning for existing Viewer checks;
- validated, non-overlapping text replacements.
- deterministic rectilinear copper routing over typed geometry primitives.

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

## Architectural boundary

`wellmanifest/pcb` is the HOME of PCB/SCH rules and project context. This
package implements KiCad syntax mechanics and must not define a competing
style, authority or dependency vocabulary.

Typed KiCad inspection and netlist/parity will be extracted only with
consumer contract tests. Natural language interpretation and effect
authorization stay outside this package. The copper router finds geometric
paths only: the consumer still chooses the net, rules, candidate and approval.
