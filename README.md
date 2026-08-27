# twin-kicad

`twin-kicad` provides the shared, dependency-free foundation for reading and
patching KiCad S-expression files without reformatting unrelated source text.

The first release deliberately owns only lossless syntax primitives:

- tokenization with source offsets;
- a single-root S-expression tree;
- stable node lookup helpers;
- compatibility range scanning for existing Viewer checks;
- validated, non-overlapping text replacements.

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

## Architectural boundary

`wellmanifest/pcb` is the HOME of PCB/SCH rules and project context. This
package implements KiCad syntax mechanics and must not define a competing
style, authority or dependency vocabulary.

Planned modules such as typed KiCad inspection, netlist/parity and optional
copper repair will be extracted only with consumer contract tests. Natural
language interpretation and effect authorization stay outside this package.

