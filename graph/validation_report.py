"""What validate_graph() found, check by check (Step 7).

Each check yields one Finding per bad node or edge. build_report() counts them
per check and keeps the first EXAMPLE_LIMIT of each, so one run shows the full
damage without flooding the output.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

EXAMPLE_LIMIT = 20


class Check(StrEnum):
    """The checks, in the order they run and are reported."""

    GRAPH_META = "graph_meta"
    NODE_LABELS = "node_labels"
    EDGE_TYPES = "edge_types"
    ENDPOINTS = "endpoints"
    NODE_PROPERTIES = "node_properties"
    EDGE_PROPERTIES = "edge_properties"
    NODE_EVIDENCE = "node_evidence"
    EDGE_EVIDENCE = "edge_evidence"
    STRUCTURE = "structure"
    POSTGRES = "postgres_chunks"


DESCRIPTIONS = {
    Check.GRAPH_META: "one :GraphMeta node records this ontology's version and schema hash",
    Check.NODE_LABELS: "every node has exactly one ontology label and no other",
    Check.EDGE_TYPES: "every edge type is in the ontology",
    Check.ENDPOINTS: "every edge joins labels its type allows",
    Check.NODE_PROPERTIES: "node properties are known, required ones present, types right",
    Check.EDGE_PROPERTIES: "edge properties are known, required ones present, types right",
    Check.NODE_EVIDENCE: "every extracted node has an EVIDENCED_BY edge to a Chunk",
    Check.EDGE_EVIDENCE: "every extracted edge cites existing chunks, one span each",
    Check.STRUCTURE: "one FILED and one COVERS_PERIOD per Filing, one PART_OF per Chunk",
    Check.POSTGRES: "every Chunk node's chunk_id is in Postgres under the same filing",
}


@dataclass(frozen=True, slots=True)
class Finding:
    """One bad node, edge or graph-level fact, and what is wrong with it."""

    check: Check
    message: str


@dataclass(frozen=True, slots=True)
class CheckResult:
    """How many items failed one check, with the first few as examples."""

    check: Check
    count: int
    examples: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Every check that ran, in Check order, and whether Postgres was consulted."""

    results: tuple[CheckResult, ...]
    postgres_checked: bool

    @property
    def violation_count(self) -> int:
        return sum(r.count for r in self.results)

    @property
    def ok(self) -> bool:
        return self.violation_count == 0

    def __str__(self) -> str:
        width = max(len(c.value) for c in Check)
        lines = ["Graph validation checks:"]
        lines += [
            f"  {r.check.value:<{width}}  {'ok' if not r.count else f'{r.count} failed':<10}  "
            f"{DESCRIPTIONS[r.check]}"
            for r in self.results
        ]
        lines.append(
            "Postgres check: ran" if self.postgres_checked
            else "Postgres check: skipped (no Postgres connection; set DATABASE_URL)"
        )
        failed = [r for r in self.results if r.count]
        if not failed:
            return "\n".join([*lines, "The graph is valid."])
        lines.append(f"{self.violation_count} violations in {len(failed)} checks.")
        for r in failed:
            shown = f", first {len(r.examples)}" if len(r.examples) < r.count else ""
            lines.append(f"{r.check.value}: {r.count} failed{shown}:")
            lines += [f"  {example}" for example in r.examples]
        return "\n".join(lines)


class GraphValidationError(RuntimeError):
    """The graph breaks the ontology or evidence rules; `report` says how."""

    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        super().__init__(str(report))


def build_report(
    checks: Iterable[Check], findings: Iterable[Finding], postgres_checked: bool,
) -> ValidationReport:
    """Count *findings* per check, keeping EXAMPLE_LIMIT examples each."""
    order = [c for c in Check if c in set(checks)]
    counts = dict.fromkeys(order, 0)
    examples: dict[Check, list[str]] = {c: [] for c in order}
    for finding in findings:
        counts[finding.check] += 1
        if len(examples[finding.check]) < EXAMPLE_LIMIT:
            examples[finding.check].append(finding.message)
    return ValidationReport(
        tuple(CheckResult(c, counts[c], tuple(examples[c])) for c in order), postgres_checked,
    )
