"""Hand-built resolution nodes around names and chunk IDs from NVDA run 1 (Step 9),
and a scripted stand-in for the adjudicator."""
from resolve.decisions import Answer, PairDecision, RelatedKind
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


ADJUDICATOR_PROMPT = "resolve/v1@0123abcd"


class ScriptedAdjudicator:
    """Answers each pair from *answers* (keyed by the two names), *default* otherwise."""

    prompt_version = ADJUDICATOR_PROMPT

    def __init__(self, answers, verified=True, default=Answer.DISTINCT):
        self._answers = {frozenset(k): v for k, v in answers.items()}
        self._verified = verified
        self._default = default
        self.judged = []

    def judge(self, pair, a, b):
        self.judged.append((a.name, b.name))
        answer = self._answers.get(frozenset((a.name, b.name)), self._default)
        kind = RelatedKind.VARIANT if answer is Answer.RELATED else None
        failure = None if answer is Answer.SAME else (
            f"related:{kind}" if kind else answer.value)
        if answer is Answer.SAME and not self._verified:
            failure = "quote_not_found"
        return PairDecision(
            decision_id=f"d-{a.name}-{b.name}", key_a=pair.key_a, key_b=pair.key_b,
            label=pair.label, answer=answer, related_kind=kind,
            quote_a=a.evidence[0].span, chunk_id_a=a.evidence[0].chunk_id,
            quote_b=b.evidence[0].span, chunk_id_b=b.evidence[0].chunk_id,
            reason="scripted", quotes_verified=self._verified, failure=failure,
            prompt_version=ADJUDICATOR_PROMPT)
