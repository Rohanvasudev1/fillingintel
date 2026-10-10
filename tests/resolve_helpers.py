"""Hand-built resolution nodes around names and chunk IDs from NVDA run 1 (Step 9)."""
from resolve.nodes import EvidenceRef, MetricFacts, ResolveNode

NVDA_10K = "0001045810-26-000021"
AMD_10K = "0000002488-26-000018"


def chunk(ordinal: int, accession_no: str = NVDA_10K) -> str:
    return f"{accession_no}:{ordinal:04d}"


def node(label: str, name: str, *ordinals: int, key: str | None = None,
         accession_no: str = NVDA_10K) -> ResolveNode:
    """A named node keyed as extraction keys it, with one evidence entry per ordinal."""
    return ResolveNode(
        key=key if key is not None else f"{label}:{' '.join(name.casefold().split())}",
        label=label, name=name,
        evidence=tuple(EvidenceRef(chunk(o, accession_no), name) for o in (ordinals or (1,))),
    )


def metric(concept: str, *, value: float = 216.0, unit: str = "USD billions",
           period: str = "FY2026", subject: str = "1045810", accession_no: str = NVDA_10K,
           ordinal: int = 61) -> ResolveNode:
    key = f"{accession_no}:{subject}:{concept.casefold()}:{period}"
    return ResolveNode(
        key=key, label="MetricValue", name=concept,
        evidence=(EvidenceRef(chunk(ordinal, accession_no), concept),),
        metric=MetricFacts(accession_no=accession_no, subject_key=subject, period=period,
                           value=value, unit=unit),
    )
