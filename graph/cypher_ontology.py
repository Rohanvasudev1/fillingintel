"""The guard's ontology check (Step 11, ADR-0007 check 2).

Reads a tokenized query against the code ontology:
- every label in a node pattern, edge type in a relationship pattern and
  property name must exist; a `n:X` predicate outside a pattern may name a
  label or an edge type, since Cypher allows both there;
- a pattern with labels on both ends must be an allowed endpoint pair in the
  direction written. An undirected pattern passes if either direction is
  allowed. Ends without labels, negated or wildcard labels and variable-length
  patterns are not checked.

Labels are read only from the pattern itself, not from earlier bindings of the
same variable. Property names are checked against every property any label or
edge type defines. A temporal accessor (`f.filing_date.year`) and a namespaced
function (`date.truncate(...)`) are not properties. Unbalanced brackets are
left to EXPLAIN, which reports them as syntax errors.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product

from graph.cypher_text import Kind, Token, is_map_key
from graph.ontology import EDGE_TYPES, EDGE_TYPES_BY_NAME, LABELS, LABELS_BY_NAME

PROPERTIES: frozenset[str] = frozenset(
    p.name for d in (*LABELS, *EDGE_TYPES) for p in d.properties)

_CLOSERS = {"(": ")", "[": "]", "{": "}"}
# A name before `(` makes a function call, unless it is one of these keywords.
_KEYWORDS_BEFORE_PATTERNS = frozenset({
    "MATCH", "WHERE", "AND", "OR", "XOR", "NOT", "CALL", "EXISTS", "IN", "RETURN", "WITH",
    "UNWIND", "WHEN", "THEN", "ELSE", "AS", "OPTIONAL", "UNION", "ALL", "DISTINCT", "BY",
})


@dataclass
class _Frame:
    kind: str  # node, rel, props, or other
    names: list[str] = field(default_factory=list)  # labels or types; empty if unchecked
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
    """One pass over the tokens, then the endpoint check over the patterns found."""

    def __init__(self, tokens: list[Token], pairs: dict[int, int]) -> None:
        self.tokens = tokens
        self.pairs = pairs
        self.frames: dict[int, _Frame] = {}  # opening index -> frame

    def punct(self, i: int) -> str | None:
        if 0 <= i < len(self.tokens) and self.tokens[i].kind is Kind.PUNCT:
            return self.tokens[i].text
        return None

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
            return _Frame("node")
        if text == "[" and self.punct(i - 1) == "-":
            return _Frame("rel")
        if text == "{" and enclosing is not None and enclosing.kind in ("node", "rel"):
            return _Frame("props")
        return _Frame("other")

    def _label_expression(self, i: int, frame: _Frame | None) -> tuple[list[str], int]:
        """Names after the `:` at *i*, and the index after them.

        A negated or wildcard label marks *frame* unchecked. Alternatives (`A|B`, `A&B`)
        are read only inside a pattern, where `|` cannot end a list comprehension.
        """
        names: list[str] = []
        chained = frame is not None and frame.kind in ("node", "rel")
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
        """The first unknown name, as a message; records patterns for check_endpoints."""
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
            elif punct == "*" and frame is not None and frame.kind == "rel":
                frame.variable_length = True
            elif punct == ":" and not (self.name(i - 1) is not None and is_map_key(
                    self.tokens, i - 1)):
                names, after = self._label_expression(i, frame)
                if (message := self._check_names(names, frame)) is not None:
                    return message
                i = after
                continue
            elif punct == "." and (message := self._check_property(i)) is not None:
                return message
            elif (token.is_name and frame is not None and frame.kind == "props"
                  and is_map_key(self.tokens, i) and token.text not in PROPERTIES):
                return f"unknown property {token.text!r}; {_properties_hint()}"
            i += 1
        return None

    def _check_names(self, names: list[str], frame: _Frame | None) -> str | None:
        kind = frame.kind if frame is not None else "other"
        for name in names:
            if kind == "node" and name not in LABELS_BY_NAME:
                return f"unknown label {name!r}; labels: {', '.join(LABELS_BY_NAME)}"
            if kind == "rel" and name not in EDGE_TYPES_BY_NAME:
                return f"unknown edge type {name!r}; types: {', '.join(EDGE_TYPES_BY_NAME)}"
            if name not in LABELS_BY_NAME and name not in EDGE_TYPES_BY_NAME:
                return f"unknown label or edge type {name!r}"
        if kind in ("node", "rel") and frame is not None:
            frame.names.extend(names)
        return None

    def _check_property(self, i: int) -> str | None:
        """The property after the `.` at *i*, unless it is an accessor or a namespace."""
        name = self.name(i + 1)
        if name is None:
            return None
        if self.name(i - 1) is not None and self.punct(i - 2) == ".":
            return None  # f.filing_date.year: an accessor of a property's value
        j = i + 1
        while self.punct(j + 1) == "." and self.name(j + 2) is not None:
            j += 2
        if self.punct(j + 1) == "(":
            return None  # date.truncate(...): a namespaced function
        if name not in PROPERTIES:
            return f"unknown property {name!r}; {_properties_hint()}"
        return None

    def check_endpoints(self) -> str | None:
        """The first pattern whose labelled ends its edge type does not allow."""
        for start, frame in self.frames.items():
            if frame.kind != "node":
                continue
            found = self._connection(self.pairs[start] + 1)
            if found is None:
                continue
            rel, direction, end = found
            message = _endpoint_violation(frame, rel, direction, end)
            if message is not None:
                return message
        return None

    def _connection(self, k: int) -> tuple[_Frame, str, _Frame] | None:
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
            rel = _Frame("rel")
            k += 1
        else:
            return None
        right = self.punct(k) == ">"
        k += right
        end = self.frames.get(k)
        if end is None or end.kind != "node":
            return None
        direction = "forward" if right and not left else "backward" if left and not right \
            else "either"
        return rel, direction, end


def _endpoint_violation(start: _Frame, rel: _Frame, direction: str, end: _Frame) -> str | None:
    if not (start.names and end.names and rel.names) or rel.variable_length:
        return None
    if not (start.checked and end.checked and rel.checked):
        return None
    pairs = {"forward": [(start, end)], "backward": [(end, start)],
             "either": [(start, end), (end, start)]}[direction]
    for type_name, (a, b) in product(rel.names, pairs):
        edge = EDGE_TYPES_BY_NAME[type_name]
        if any(s in edge.starts and e in edge.ends for s, e in product(a.names, b.names)):
            return None
    edge = EDGE_TYPES_BY_NAME[rel.names[0]]
    left = "<-" if direction == "backward" else "-"
    right = "->" if direction == "forward" else "-"
    written = (f"(:{'|'.join(start.names)}){left}[:{'|'.join(rel.names)}]{right}"
               f"(:{'|'.join(end.names)})")
    return (f"pattern {written} is not allowed: {edge.name} runs from "
            f"{'|'.join(edge.starts)} to {'|'.join(edge.ends)}")


def _properties_hint() -> str:
    return f"properties: {', '.join(sorted(PROPERTIES))}"


def ontology_violation(tokens: list[Token]) -> str | None:
    """The first ontology rule *tokens* break, as a message for a repair prompt; None if none."""
    pairs = _matching(tokens)
    if pairs is None:
        return None
    reader = _Reader(tokens, pairs)
    return reader.scan() or reader.check_endpoints()
