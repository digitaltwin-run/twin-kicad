from __future__ import annotations

import pytest

from twin_kicad.sexp import (
    SexpError,
    apply_replacements,
    block_end,
    blocks,
    child,
    children,
    head,
    number,
    parse,
    text,
    token,
    walk,
)

PCB = '''(kicad_pcb
  (version 20240108)
  ; a comment with misleading parentheses: (ignored)
  (net 1 "GND")
  (footprint "Button:SW" (at 10.5 20 90)
    (fp_text reference "SW1")
    (pad "1" thru_hole circle (net 1 "GND"))
  )
)'''


def test_tree_retains_offsets_and_decoded_values() -> None:
    root = parse(PCB)
    assert head(root) == "kicad_pcb"
    assert text(child(root, "version"), 1) == "20240108"
    footprint = children(root, "footprint")[0]
    assert text(footprint, 1) == "Button:SW"
    assert number(child(footprint, "at"), 1) == 10.5
    assert PCB[footprint.start:footprint.end].startswith('(footprint "Button:SW"')
    assert token(footprint, 1).kind == "string"
    assert [head(node) for node in walk(root)].count("pad") == 1


def test_compatibility_blocks_ignore_prefixes_strings_and_comments() -> None:
    source = '(root (net_class "x") (item "(net 8 fake)") ; (net 9 fake)\n (net 1 "GND"))'
    spans = blocks(source, "net")
    assert [source[start:end] for start, end in spans] == ['(net 1 "GND")']
    assert block_end(source, 0) == len(source)


def test_replacements_preserve_every_unselected_byte() -> None:
    root = parse(PCB)
    net = children(root, "net")[0]
    name = token(net, 2)
    updated = apply_replacements(PCB, [(name.start, name.end, '"+3V3"')])
    assert updated == PCB[:name.start] + '"+3V3"' + PCB[name.end:]


def test_replacements_reject_overlap_and_invalid_ranges() -> None:
    with pytest.raises(SexpError, match="overlapping"):
        apply_replacements("abcdef", [(1, 4, "x"), (3, 5, "y")])
    with pytest.raises(SexpError, match="invalid"):
        apply_replacements("abcdef", [(-1, 2, "x")])


@pytest.mark.parametrize(
    "source,message",
    [
        ("", "unbalanced"),
        ("(root", "unbalanced"),
        ("(root))", "unexpected closing"),
        ('(root "open)', "unterminated string"),
        ("(a)(b)", "multiple root"),
    ],
)
def test_invalid_sources_fail_closed(source: str, message: str) -> None:
    with pytest.raises(SexpError, match=message):
        parse(source)

