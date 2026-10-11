"""Four NVDA run 1 pairs whose real labeller replies are recorded (Step 9, ticket 04).

Names, keys, spans and chunk IDs are copied from
data/extract/0001045810-26-000021-run1.jsonl. scripts/capture_label_responses.py
sends them once; test_resolve_label_recorded.py replays the replies offline.
"""
from resolve.nodes import EvidenceRef, MetricFacts, ResolveNode
from resolve.sample import DrawnPair, Stratum

NVDA_10K = "0001045810-26-000021"
RESPONSES = "tests/fixtures/label/responses"


def _node(key: str, label: str, name: str, *evidence: tuple[int, str],
          metric: MetricFacts | None = None) -> ResolveNode:
    return ResolveNode(key, label, name,
                       tuple(EvidenceRef(f"{NVDA_10K}:{o:04d}", span) for o, span in evidence),
                       metric)


def _revenue(concept: str, *evidence: tuple[int, str]) -> ResolveNode:
    facts = MetricFacts(accession_no=NVDA_10K, subject_key="1045810", period="FY2026",
                        value=215938.0, unit="USD millions")
    return _node(f"{NVDA_10K}:1045810:{concept.casefold()}:FY2026", "MetricValue", concept,
                 *evidence, metric=facts)


CASES = {
    "groq": (
        _node("Organization:groq", "Organization", "Groq", (70, "license agreement with Groq")),
        _node("Organization:groq, inc.", "Organization", "Groq, Inc.",
              (102, "Groq, Inc\\., or Groq")),
    ),
    "gdpr": (
        _node("Regulation:gdpr", "Regulation", "GDPR",
              (50, "the GDPR governs the transfer of personal data to countries outside of "
                   "the EEA")),
        _node("Regulation:u.k. gdpr", "Regulation", "U.K. GDPR",
              (49, "the United Kingdom similarly adopted the U\\.K\\. GDPR")),
    ),
    "blackwell_gb200": (
        _node("Product:blackwell", "Product", "Blackwell",
              (4, "Introduced with the Blackwell architecture, our data\\-center\\-scale "
                  "offerings feature extreme co\\-design"),
              (24, "Blackwell material"),
              (61, "Our Blackwell architectures represented the majority of our Data Center "
                   "revenue\\."),
              (67, "demand for our Blackwell computing platform"),
              (68, "as our business model transitioned from offering Hopper HGX systems to "
                   "Blackwell full\\-scale datacenter solutions")),
        _node("Product:gb200", "Product", "GB200",
              (14, "such as our H200, GB200 and GB300"),
              (15, "The IFR would have imposed a worldwide licensing requirement on our data "
                   "center products, such as our H200, GB200 and GB300\\."),
              (43, "The IFR would have imposed a worldwide licensing requirement on our data "
                   "center products, such as our H200, GB200 and GB300")),
    ),
    "revenue": (
        _revenue("Revenue", (63, "| Revenue | $ | 215,938 |  | $ | 130,497 |  | Up 65% |"),
                 (88, "| Revenue | $ | 215,938 | $ | 130,497 | $ | 60,922 |"),
                 (123, "| Revenue | $ | 193,479 | $ | 22,459 | $ | 215,938 |")),
        _revenue("Total revenue",
                 (124, "| Total revenue | $ | 215,938 | $ | 130,497 | $ | 60,922 |")),
    ),
}


def drawn(name: str) -> DrawnPair:
    a, b = CASES[name]
    return DrawnPair(a.key, b.key, a.label, Stratum.CANDIDATE)
