"""Lossless S-expression primitives for KiCad source files.

The tree retains byte-compatible Python string offsets into the original
source. Consumers can inspect a structured document and replace only selected
spans, leaving whitespace, ordering and unrelated formatting untouched.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, Iterator, TypeAlias


class SexpError(ValueError):
    """The source cannot be represented as one balanced S-expression."""


@dataclass(frozen=True, slots=True)
class Token:
    kind: str
    value: str
    start: int
    end: int


@dataclass(slots=True)
class Node:
    start: int
    end: int
    values: list[Token | "Node"]


Replacement: TypeAlias = tuple[int, int, str]


def tokenize(source: str) -> list[Token]:
    """Tokenize a KiCad S-expression while retaining source offsets."""
    result: list[Token] = []
    index = 0
    while index < len(source):
        char = source[index]
        if char.isspace():
            index += 1
            continue
        if char == ";":
            newline = source.find("\n", index)
            index = len(source) if newline < 0 else newline + 1
            continue
        if char in "()":
            result.append(Token(char, char, index, index + 1))
            index += 1
            continue
        if char == '"':
            start = index
            index += 1
            escaped = False
            while index < len(source):
                current = source[index]
                index += 1
                if current == '"' and not escaped:
                    break
                escaped = current == "\\" and not escaped
                if current != "\\":
                    escaped = False
            else:
                raise SexpError("unterminated string in KiCad file")
            raw = source[start:index]
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise SexpError("invalid quoted string in KiCad file") from exc
            result.append(Token("string", value, start, index))
            continue
        start = index
        while index < len(source) and not source[index].isspace() and source[index] not in "()":
            index += 1
        result.append(Token("atom", source[start:index], start, index))
    return result


def parse(source: str) -> Node:
    """Parse exactly one root expression without normalizing its source."""
    stack: list[Node] = []
    root: Node | None = None
    for item in tokenize(source):
        if item.kind == "(":
            stack.append(Node(item.start, -1, []))
        elif item.kind == ")":
            if not stack:
                raise SexpError("unexpected closing parenthesis")
            node = stack.pop()
            node.end = item.end
            if stack:
                stack[-1].values.append(node)
            elif root is None:
                root = node
            else:
                raise SexpError("multiple root expressions")
        elif not stack:
            raise SexpError("atom outside root expression")
        else:
            stack[-1].values.append(item)
    if stack or root is None:
        raise SexpError("unbalanced KiCad S-expression")
    return root


def head(node: Node) -> str | None:
    first = node.values[0] if node.values else None
    return first.value if isinstance(first, Token) else None


def children(node: Node, name: str | None = None) -> list[Node]:
    result = [value for value in node.values if isinstance(value, Node)]
    return result if name is None else [value for value in result if head(value) == name]


def child(node: Node, name: str) -> Node | None:
    return next(iter(children(node, name)), None)


def token(node: Node, index: int) -> Token | None:
    values = [value for value in node.values if isinstance(value, Token)]
    return values[index] if 0 <= index < len(values) else None


def text(node: Node | None, index: int, default: str = "") -> str:
    item = token(node, index) if node is not None else None
    return item.value if item else default


def number(node: Node | None, index: int, default: float = 0.0) -> float:
    try:
        return float(text(node, index))
    except ValueError:
        return default


def walk(node: Node) -> Iterator[Node]:
    """Yield a node and all descendant nodes in source order."""
    yield node
    for nested in children(node):
        yield from walk(nested)


def block_end(source: str, start: int) -> int:
    """Return the end offset of the expression beginning at ``start``."""
    if start < 0 or start >= len(source) or source[start] != "(":
        raise SexpError("block start must point at an opening parenthesis")
    depth = 0
    index = start
    while index < len(source):
        char = source[index]
        if char == ";":
            newline = source.find("\n", index)
            index = len(source) if newline < 0 else newline + 1
            continue
        if char == '"':
            index += 1
            while index < len(source):
                if source[index] == "\\":
                    index += 2
                    continue
                if source[index] == '"':
                    break
                index += 1
            if index >= len(source):
                raise SexpError("unterminated string in KiCad file")
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
            if depth < 0:
                raise SexpError("unexpected closing parenthesis")
        index += 1
    raise SexpError("unbalanced KiCad S-expression")


def blocks(source: str, name: str) -> list[tuple[int, int]]:
    """Return non-overlapping ``(name ...)`` spans in source order.

    This preserves the established Viewer behavior: after a matching block is
    found, scanning resumes after that block. A caller that needs every nested
    node should use :func:`parse` and :func:`walk`.
    """
    if not name or any(char.isspace() or char in "()" for char in name):
        raise ValueError("block name must be one S-expression atom")
    spans: list[tuple[int, int]] = []
    needle = f"({name}"
    index = 0
    while index < len(source):
        if source[index] == ";":
            newline = source.find("\n", index)
            index = len(source) if newline < 0 else newline + 1
            continue
        if source[index] == '"':
            index += 1
            while index < len(source):
                if source[index] == "\\":
                    index += 2
                    continue
                if source[index] == '"':
                    index += 1
                    break
                index += 1
            continue
        if source.startswith(needle, index):
            tail = source[index + len(needle):index + len(needle) + 1]
            if tail not in {"", " ", "\n", "\r", "\t", "("}:
                index += 1
                continue
            end = block_end(source, index)
            spans.append((index, end))
            index = end
            continue
        index += 1
    return spans


def apply_replacements(source: str, replacements: Iterable[Replacement]) -> str:
    """Apply validated, non-overlapping source replacements losslessly."""
    ordered = sorted(replacements, key=lambda item: (item[0], item[1]))
    previous_end = 0
    for start, end, _replacement in ordered:
        if start < 0 or end < start or end > len(source):
            raise SexpError(f"invalid replacement span {start}:{end}")
        if start < previous_end:
            raise SexpError("overlapping replacement spans")
        previous_end = end
    result = source
    for start, end, replacement in reversed(ordered):
        result = result[:start] + replacement + result[end:]
    return result
