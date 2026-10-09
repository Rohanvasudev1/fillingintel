"""The filing being extracted and the fixed references the model uses for filers (Step 8).

The model never names the three filers or this filing as nodes. It writes
THIS_FILING for the filing, FILER for the company that filed it, and NVDA, AMD
or INTC for any of the three; code maps each to the Filing or Company node,
keyed by accession number or CIK. References are compared without regard to
case, as enum values are.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

from graph.batch import NodeRef

# The corpus filers (ingest.corpus.CIKS, which imports edgartools; a test keeps the two equal).
FILER_CIKS = MappingProxyType({"NVDA": "1045810", "AMD": "2488", "INTC": "50863"})
TICKER_BY_CIK = MappingProxyType({cik: ticker for ticker, cik in FILER_CIKS.items()})
THIS_FILING = "THIS_FILING"
FILER = "FILER"
FILER_REFERENCES: tuple[str, ...] = (THIS_FILING, FILER, *FILER_CIKS)


@dataclass(frozen=True, slots=True)
class FilingInfo:
    """The metadata of the filing whose chunks are extracted."""

    cik: str
    accession_no: str
    company_name: str
    form_type: str
    fiscal_period: str

    def __post_init__(self) -> None:
        if self.cik not in TICKER_BY_CIK:
            raise ValueError(f"CIK {self.cik!r} is not one of the three filers")
        for name in ("accession_no", "company_name", "form_type", "fiscal_period"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} is blank")

    @property
    def ticker(self) -> str:
        return TICKER_BY_CIK[self.cik]

    @property
    def filing_ref(self) -> NodeRef:
        return NodeRef.of("Filing", accession_no=self.accession_no)


def company_ref(cik: str) -> NodeRef:
    return NodeRef.of("Company", cik=cik)


def resolve_filer(reference: str, filing: FilingInfo) -> NodeRef | None:
    """The node a filer reference names, or None when *reference* is not one."""
    upper = reference.strip().upper()
    if upper == THIS_FILING:
        return filing.filing_ref
    if upper == FILER:
        return company_ref(filing.cik)
    cik = FILER_CIKS.get(upper)
    return company_ref(cik) if cik is not None else None


def known_nodes(filing: FilingInfo) -> frozenset[NodeRef]:
    """The structural nodes filer references can name, for the dry-run batch check."""
    return frozenset({filing.filing_ref, *(company_ref(cik) for cik in FILER_CIKS.values())})
