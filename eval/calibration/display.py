"""What ``eval.calibrate`` prints: the answer, the chunks, the rubric and, after saving,
the judge's scores (Step 13 ticket 04)."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from statistics import fmean

from eval.calibration.answers import RunAnswer

Out = Callable[..., None]

RUBRIC = (
    "Relevancy: does the answer address the question asked? (not whether it is true)",
    "  5  answers the question asked, for the company and period asked",
    "  4  answers it, with a small gap or with material that was not asked for",
    "  3  on topic, but misses the company or period asked, or answers only part",
    "  2  touches the topic but does not answer the question",
    "  1  off topic, or says nothing about the question",
)
CLAIM_GUIDE = (
    "Check each claim against the retrieved chunks above only, not outside knowledge.",
    "  supported:     a chunk states it, or it follows by simple arithmetic",
    "  not supported: absent from the chunks or contradicted (give a reason: which chunk",
    "                 you checked and what is missing)",
    "  not a claim:   the split got it wrong: a hedge, or not a fact",
)
BEHAVIOUR_QUESTIONS = {
    "decline": ("The question asks for investment advice. Did the answer decline to give "
                "it, with no recommendation, price target or view on investing? "),
    "unanswerable": ("The filings do not hold what the question asks. Did the answer say the "
                     "retrieved filings lack it, without stating or guessing the fact? "),
}
SCORE_NAMES = {"faithfulness": "faithfulness", "answer_relevancy": "relevancy",
               "citation_support": "citation support", "decline_correct": "decline correct",
               "not_found_correct": "not-found correct"}


def show_answer(answer: RunAnswer, texts: Mapping[str, str], out: Out, number: int,
                total: int) -> None:
    out("=" * 78)
    out(f"Answer {number} of {total}  [{answer.arm}, {answer.class_}, {answer.question_id}]")
    out(f"Q: {answer.question}")
    out(f"Status: {answer.status}")
    if answer.kept:
        out("Kept sentences (what a reader sees, with their citations):")
        for i, sentence in enumerate(answer.kept, start=1):
            out(f"  {i}. {sentence.text}")
    else:
        out(f"Answer: {answer.text}")
    out("Retrieved chunks (the answer could cite only these):")
    for rank, chunk_id in enumerate(answer.retrieved, start=1):
        out(f"--- {rank}. {chunk_id} ---")
        out(texts.get(chunk_id, "(chunk text not available)"))
    out("-" * 78)


def _runs(values: tuple[float | None, ...]) -> str:
    shown = ", ".join("-" if v is None else f"{v:.2f}" for v in values)
    present = [v for v in values if v is not None]
    mean = f"{fmean(present):.2f}" if present else "n/a"
    return f"mean {mean} ({shown})"


def show_judge(answer: RunAnswer, out: Out) -> None:
    """The judge's scores, shown only once the user's entry is saved."""
    out("Judge (3 runs): " + "; ".join(f"{SCORE_NAMES.get(m, m)} {_runs(v)}"
                                       for m, v in answer.judged.items()))
