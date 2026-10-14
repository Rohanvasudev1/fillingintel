"""The guard's ontology check (Step 11, ADR-0007 check 2).

Reads a tokenized query against the code ontology:
- every label in a node pattern and edge type in a relationship pattern must
  exist; a `n:X` predicate outside a pattern may name a label or an edge type,
  since Cypher allows both there;
- every property in a pattern's property map, and every `x.prop` read on a
  variable a pattern binds, must be a property some label or edge type defines;
- a pattern with labels on both ends must be an allowed endpoint pair in the
  direction written. An undirected pattern passes if either direction is
  allowed. Ends without labels, negated or wildcard labels and variable-length
  patterns are not checked.

Labels are read only from the pattern itself, not from earlier bindings of the
same variable. Property reads on anything else (a map, a function's result, a
temporal value such as `f.filing_date.year`) are not checked here; EXPLAIN
reports an unknown property key (01N52) wherever it appears. Unbalanced
brackets are left to EXPLAIN, which reports them as syntax errors.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from itertools import product

from graph.cypher_text import Kind, Token, is_map_key, punct_at
from graph.ontology import EDGE_TYPES, EDGE_TYPES_BY_NAME, LABELS, LABELS_BY_NAME

PROPERTIES: frozenset[str] = frozenset(
    p.name for d in (*LABELS, *EDGE_TYPES) for p in d.properties)

_CLOSERS = {"(": ")", "[": "]", "{": "}"}
# A name before `(` makes a function call, unless it is one of these keywords.
_KEYWORDS_BEFORE_PATTERNS = frozenset({
    "MATCH", "WHERE", "AND", "OR", "XOR", "NOT", "CALL", "EXISTS", "IN", "RETURN", "WITH",
    "UNWIND", "WHEN", "THEN", "ELSE", "AS", "OPTIONAL", "UNION", "ALL", "DISTINCT", "BY",
})


class FrameKind(StrEnum):
    NODE = "node"    # ( ... ) of a node pattern
    REL = "rel"      # [ ... ] of a relationship pattern
    PROPS = "props"  # { ... } property map inside either
    OTHER = "other"  # any other bracket


class Direction(StrEnum):
    FORWARD = "forward"
    BACKWARD = "backward"
    EITHER = "either"


@dataclass
class _Frame:
    """One bracket pair. Filled in during a single scan and never shared outside it."""

    kind: FrameKind
    variable: str | None = None  # the variable a node or relationship pattern binds
    names: list[str] = field(default_factory=list)  # its labels or edge types
    checked: bool = True  # False when a negated or wildcard label makes the end unknown
    variable_length: bool = False


def _matching(tokens: list[Token]) -> dict[int, int] | None:
    """Opening bracket index -> closing index, or None when unbalanced."""
    stack: list[int] = []
    pairs: dict[int, int] = {}
    for i, token in enumerate(tokens):
        if token.kind is not Kind.PUNCT:
            continue
        if token.text in _CLOSERS:
            stack.append(i)
        elif token.text in _CLOSERS.values():
            if not stack or _CLOSERS[tokens[stack[-1]].text] != token.text:
                return None
            pairs[stack.pop()] = i
    return pairs if not stack else None


class _Reader:
    """One pass over the tokens; then property reads and endpoints over the patterns found."""

    def __init__(self, tokens: list[Token], pairs: dict[int, int]) -> None:
        self.tokens = tokens
        self.pairs = pairs
        self.frames: dict[int, _Frame] = {}  # opening index -> frame
        self.reads: list[tuple[str, str]] = []  # (variable, property) for each x.prop

    def punct(self, i: int) -> str | None:
        return punct_at(self.tokens, i)

    def name(self, i: int) -> str | None:
        if 0 <= i < len(self.tokens) and self.tokens[i].is_name:
            return self.tokens[i].text
        return None

    def _is_node(self, i: int) -> bool:
        before = self.tokens[i - 1] if i > 0 else None
        if before is not None and before.is_name and (
                before.keyword not in _KEYWORDS_BEFORE_PATTERNS):
            return False  # a function call
        j = i + 1 + (self.name(i + 1) is not None)
        token = self.tokens[j] if j < len(self.tokens) else None
        return token is not None and (
            self.punct(j) in (")", ":", "{") or token.keyword == "WHERE")

    def _open(self, i: int, enclosing: _Frame | None) -> _Frame:
        text = self.tokens[i].text
        if text == "(" and self._is_node(i):
            return _Frame(FrameKind.NODE, variable=self.name(i + 1))
        if text == "[" and self.punct(i - 1) == "-":
            return _Frame(FrameKind.REL, variable=self.name(i + 1))
        if text == "{" and enclosing is not None and enclosing.kind in (
                FrameKind.NODE, FrameKind.REL):
            return _Frame(FrameKind.PROPS)
        return _Frame(FrameKind.OTHER)

    def _label_expression(self, i: int, frame: _Frame | None) -> tuple[list[str], int]:
        """Names after the `:` at *i*, and the index after them.

        A negated or wildcard label marks *frame* unchecked. Alternatives (`A|B`, `A&B`)
        are read only inside a pattern, where `|` cannot end a list comprehension.
        """
        names: list[str] = []
        chained = frame is not None and frame.kind in (FrameKind.NODE, FrameKind.REL)
        j = i + 1
        while True:
            while self.punct(j) in ("!", "%"):
                if frame is not None:
                    frame.checked = False
                j += 1
            if (name := self.name(j)) is not None:
                names.append(name)
                j += 1
            if not (chained and self.punct(j) in ("|", "&")):
                return names, j
            j += 1 + (self.punct(j + 1) == ":")

    def scan(self) -> str | None:
        """The first unknown label, edge type or pattern property, as a message."""
        stack: list[_Frame] = []
        closing: dict[int, int] = {}  # closing index -> opening index
        i = 0
        while i < len(self.tokens):
            token, punct = self.tokens[i], self.punct(i)
            frame = stack[-1] if stack else None
            if punct in _CLOSERS:
                new = self._open(i, frame)
                self.frames[i] = new
                closing[self.pairs[i]] = i
                stack.append(new)
            elif i in closing:
                stack.pop()
            elif punct == "*" and frame is not None and frame.kind is FrameKind.REL:
                frame.variable_length = True
            elif punct == ":" and not (self.name(i - 1) is not None and is_map_key(
                    self.tokens, i - 1)):
                names, after = self._label_expression(i, frame)
                if (message := _check_names(names, frame)) is not None:
                    return message
                i = after
                continue
            elif punct == ".":
                self._record_read(i)
            elif (token.is_name and frame is not None and frame.kind is FrameKind.PROPS
                  and is_map_key(self.tokens, i) and token.text not in PROPERTIES):
                return _unknown_property(token.text)
            i += 1
        return None

    def _record_read(self, i: int) -> None:
        """Note `x.prop` at the `.` at *i*, unless it is a namespaced function call."""
        owner, prop = self.name(i - 1), self.name(i + 1)
        if owner is None or prop is None or self.punct(i - 2) == ".":
            return
        if self.punct(i + 2) in ("(", "."):
            return  # date.truncate(...), db.index.x(...): a function's namespace
        self.reads.append((owner, prop))

    def check_reads(self) -> str | None:
        """The first property read on a pattern variable that no type defines."""
        bound = {f.variable for f in self.frames.values()
                 if f.kind in (FrameKind.NODE, FrameKind.REL) and f.variable}
        for owner, prop in self.reads:
            if owner in bound and prop not in PROPERTIES:
                return _unknown_property(prop)
        return None

    def check_endpoints(self) -> str | None:
        """The first pattern whose labelled ends its edge type does not allow."""
        for start, frame in self.frames.items():
            if frame.kind is not FrameKind.NODE:
                continue
            found = self._connection(self.pairs[start] + 1)
            if found is None:
                continue
            rel, direction, end = found
            if (message := _endpoint_violation(frame, rel, direction, end)) is not None:
                return message
        return None

    def _connection(self, k: int) -> tuple[_Frame, Direction, _Frame] | None:
        """The relationship and node a pattern continues with at *k*, if any."""
        left = self.punct(k) == "<"
        k += left
        if self.punct(k) != "-":
            return None
        k += 1
        if self.punct(k) == "[":
            rel = self.frames[k]
            k = self.pairs[k] + 1
            if self.punct(k) != "-":
                return None
            k += 1
        elif self.punct(k) == "-":
            rel = _Frame(FrameKind.REL)
            k += 1
        else:
            return None
        right = self.punct(k) == ">"
        k += right
        end = self.frames.get(k)
        if end is None or end.kind is not FrameKind.NODE:
            return None
        if right and not left:
            return rel, Direction.FORWARD, end
        return rel, Direction.BACKWARD if left and not right else Direction.EITHER, end


def _check_names(names: list[str], frame: _Frame | None) -> str | None:
    kind = frame.kind if frame is not None else FrameKind.OTHER
    for name in names:
        if kind is FrameKind.NODE and name not in LABELS_BY_NAME:
            return f"unknown label {name!r}; labels: {', '.join(LABELS_BY_NAME)}"
        if kind is FrameKind.REL and name not in EDGE_TYPES_BY_NAME:
            return f"unknown edge type {name!r}; types: {', '.join(EDGE_TYPES_BY_NAME)}"
        if name not in LABELS_BY_NAME and name not in EDGE_TYPES_BY_NAME:
            return f"unknown label or edge type {name!r}"
    if frame is not None and kind in (FrameKind.NODE, FrameKind.REL):
        frame.names.extend(names)
    return None


def _endpoint_violation(start: _Frame, rel: _Frame, direction: Direction,
                        end: _Frame) -> str | None:
    if not (start.names and end.names and rel.names) or rel.variable_length:
        return None
    if not (start.checked and end.checked and rel.checked):
        return None
    pairs = {Direction.FORWARD: [(start, end)], Direction.BACKWARD: [(end, start)],
             Direction.EITHER: [(start, end), (end, start)]}[direction]
    for type_name, (a, b) in product(rel.names, pairs):
        edge = EDGE_TYPES_BY_NAME[type_name]
        if any(s in edge.starts and e in edge.ends for s, e in product(a.names, b.names)):
            return None
    edge = EDGE_TYPES_BY_NAME[rel.names[0]]
    left = "<-" if direction is Direction.BACKWARD else "-"
    right = "->" if direction is Direction.FORWARD else "-"
    written = (f"(:{'|'.join(start.names)}){left}[:{'|'.join(rel.names)}]{right}"
               f"(:{'|'.join(end.names)})")
    return (f"pattern {written} is not allowed: {edge.name} runs from "
            f"{'|'.join(edge.starts)} to {'|'.join(edge.ends)}")


def _unknown_property(name: str) -> str:
    return f"unknown property {name!r}; properties: {', '.join(sorted(PROPERTIES))}"


def ontology_violation(tokens: list[Token]) -> str | None:
    """The first ontology rule *tokens* break, as a message for a repair prompt; None if none."""
    pairs = _matching(tokens)
    if pairs is None:
        return None
    reader = _Reader(tokens, pairs)
    return reader.scan() or reader.check_reads() or reader.check_endpoints()
