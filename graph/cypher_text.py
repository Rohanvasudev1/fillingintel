"""Cypher tokens and the guard's text rules (Step 11, ADR-0007 check 1).

The tokenizer drops comments and keeps string literals as single tokens, so
the rules never read words inside either. A `//` comment ends at any line
break, Unicode ones included: a comment the tokenizer ends later than Neo4j
does would hide the rest of the line from the rules while the server runs it.
Control characters other than tab, newline and carriage return are refused.
Backtick-quoted names become name tokens holding the unquoted name; they are
never keywords.

The text rules allow one read statement: no `;` except one at the very end,
and none of the denied keywords. `CALL` is allowed only as a subquery, `CALL {`
or `CALL (`; any other `CALL` runs a procedure. A keyword used as a property
(`n.set`), label (`:Use`), parameter or map key (`{use: 1}`) is not a keyword.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Kind(StrEnum):
    NAME = "name"
    QUOTED = "quoted"  # a backtick-quoted name
    STRING = "string"
    NUMBER = "number"
    PARAM = "param"
    PUNCT = "punct"


@dataclass(frozen=True, slots=True)
class Token:
    kind: Kind
    text: str  # for QUOTED, the name without backticks
    start: int  # offset in the query text

    @property
    def is_name(self) -> bool:
        return self.kind in (Kind.NAME, Kind.QUOTED)

    @property
    def keyword(self) -> str | None:
        """The upper-cased word, for unquoted names only."""
        return self.text.upper() if self.kind is Kind.NAME else None


class CypherTextError(ValueError):
    """The text cannot be tokenized: an unterminated string, name or comment."""


_TOKEN = re.compile(
    r"""
      (?P<space>\s+)
    | (?P<comment>//[^\n\r\v\f\x1c-\x1e\x85\u2028\u2029]*|/\*.*?\*/)
    | (?P<string>'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*")
    | (?P<quoted>`(?:[^`]|``)*`)
    | (?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+)
    | (?P<param>\$(?:[^\W\d]\w*|\d+|`(?:[^`]|``)*`))
    | (?P<name>[^\W\d]\w*)
    | (?P<punct>::|\.\.|<>|<=|>=|=~|\+=|/\*|['"`]|.)
    """,
    re.VERBOSE | re.DOTALL,
)
_UNTERMINATED = {"'": "string", '"': "string", "`": "quoted name", "/*": "comment"}


_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1b\x7f]")


def tokenize(cypher: str) -> list[Token]:
    """The tokens of *cypher*, comments and whitespace left out."""
    if (control := _CONTROL.search(cypher)) is not None:
        raise CypherTextError(
            f"control character {control.group()!r} at offset {control.start()}")
    tokens: list[Token] = []
    for match in _TOKEN.finditer(cypher):
        group, text = match.lastgroup, match.group()
        if group in ("space", "comment"):
            continue
        if group == "punct" and text in _UNTERMINATED:
            raise CypherTextError(
                f"unterminated {_UNTERMINATED[text]} at offset {match.start()}")
        if group == "quoted":
            tokens.append(Token(Kind.QUOTED, text[1:-1].replace("``", "`"), match.start()))
        else:
            tokens.append(Token(Kind(group), text, match.start()))
    return tokens


DENIED_KEYWORDS: frozenset[str] = frozenset({
    "TERMINATE", "SHOW", "USE", "CREATE", "INSERT", "MERGE", "SET", "DELETE", "REMOVE",
    "DROP", "FOREACH",
})
_NOT_A_KEYWORD_AFTER = {".", ":", "$"}
_SUBQUERY_OPENERS = {"{", "("}
_READ_ONLY = "the query must only read the graph"


def punct_at(tokens: list[Token], i: int) -> str | None:
    """The punctuation at index *i*, or None for another token or an index out of range."""
    if 0 <= i < len(tokens) and tokens[i].kind is Kind.PUNCT:
        return tokens[i].text
    return None


def is_map_key(tokens: list[Token], i: int) -> bool:
    """Whether the name at *i* is a map key: `{key:` or `, key:`."""
    return punct_at(tokens, i + 1) == ":" and punct_at(tokens, i - 1) in ("{", ",")


def text_violation(tokens: list[Token]) -> str | None:
    """The first text rule *tokens* break, as a message for a repair prompt; None if none."""
    semicolons = [i for i, t in enumerate(tokens) if punct_at(tokens, i) == ";"]
    if semicolons and semicolons != [len(tokens) - 1]:
        return "send one statement: a ';' may only end the query"
    for i, token in enumerate(tokens):
        word = token.keyword
        if word is None or punct_at(tokens, i - 1) in _NOT_A_KEYWORD_AFTER:
            continue
        if is_map_key(tokens, i):
            continue
        if word in DENIED_KEYWORDS:
            return f"{word} is not allowed: {_READ_ONLY}"
        following = tokens[i + 1].keyword if i + 1 < len(tokens) else None
        if word == "LOAD" and following == "CSV":
            return f"LOAD CSV is not allowed: {_READ_ONLY}"
        if word == "CALL" and punct_at(tokens, i + 1) not in _SUBQUERY_OPENERS:
            return ("procedure CALL is not allowed; only CALL { ... } and CALL (...) { ... } "
                    "subqueries may be used")
    return None

