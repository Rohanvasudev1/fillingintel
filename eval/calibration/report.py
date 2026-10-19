"""``python -m eval.calibration.report``: the judge against the user's scores (Step 13
ticket 04).  Offline: it reads the draw, the run files, the user's entries and the
judge's cached citation-support replies, and writes ``benchmarks/calibration/report.json``.

Per score type (RESEARCH-PLAN amendment of 2026-10-16), against the judge's mean
over its 3 runs:

- faithfulness: the user's share of supported claims (supported over supported
  plus not supported; an answer with neither is left out), by Spearman's rho with a
  Fisher-z interval, MAE and mean signed difference (judge minus user);
- answer relevancy: the 1-5 rating mapped to (r - 1) / 4, the same statistics;
- citation support: each kept sentence's yes/no against the judge's majority
  verdict over its runs, by Cohen's kappa with an interval bootstrapped by answer;
- decline and not-found correctness: percent agreement and the disagreement count.

Rho and kappa pass at a point estimate of ``PASS_AT`` or more; decline and not-found
have no pass rule.  The report also gives the judge's agreement with itself across
runs (the same statistic, averaged over run pairs), the user's re-score agreement
when re-score entries exist, and the pace log.

    uv run python -m eval.calibration.report --reviewer "Rohan Vasudev"

Exit codes: 0 written; 2 bad arguments; 3 could not run.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Callable, Mapping, Sequence
from itertools import combinations
from pathlib import Path
from statistics import fmean, median

from eval.bootstrap import interval_rng
from eval.calibration.answers import AnswerFileError, RunAnswer, load_answers
from eval.calibration.draw import CALIBRATION_DIR, DRAW_PATH, DrawError, drawn_answers, read_draw
from eval.calibration.judge_verdicts import (
    Verdicts,
    cached_citation_verdicts,
    judge_sources,
    majority,
)
from eval.calibration.scores import FAST_SECONDS, ScoresFileError, read_entries, scores_path
from eval.calibration.stats import (
    cohen_kappa,
    fisher_interval,
    kappa_by_answer_interval,
    mean_absolute_error,
    mean_signed_difference,
    percent_agreement,
    pooled_kappa,
    spearman,
)
from eval.corpus_index import ChunkIndex
from eval.judging.scoring import JudgeConfig
from eval.step13_spend import CLAIMS_FILE
from ingest.atomic_json import write_json_atomic
from ingest.parsed_files import DEFAULT_PARSED_DIR
from ingest.provenance import git_state
from retrieve.response_cache import DEFAULT_CACHE_DIR

PASS_AT = 0.6
REPORT_NAME = "report.json"
LABEL = "calibrated, n = {n}, claims split by an agent"
BEHAVIOURS = {"decline": "decline_correct", "unanswerable": "not_found_correct"}
Entry = Mapping[str, object]


def _mean(values: Sequence[float | None]) -> float | None:
    found = [v for v in values if v is not None]
    return fmean(found) if found else None


def human_faithfulness(entry: Entry) -> float | None:
    labels = [c["label"] for c in entry["claims"]]  # type: ignore[union-attr]
    checked = [label for label in labels if label != "not_a_claim"]
    return checked.count("supported") / len(checked) if checked else None


def human_relevancy(entry: Entry) -> float:
    return (int(entry["relevancy"]) - 1) / 4  # type: ignore[arg-type]


def _correlation(pairs: Sequence[tuple[float, float]], excluded: Mapping[str, int]
                 ) -> dict[str, object]:
    human, judge = [h for h, _ in pairs], [j for _, j in pairs]
    rho = spearman(human, judge)
    interval = fisher_interval(rho, len(pairs)) if rho is not None else None
    return {
        "statistic": "spearman_rho", "n": len(pairs), "value": rho,
        "interval_95": list(interval) if interval else None,
        "mae": mean_absolute_error(human, judge) if pairs else None,
        "signed_bias": mean_signed_difference(human, judge) if pairs else None,
        "threshold": PASS_AT, "passed": rho is not None and rho >= PASS_AT,
        "label": LABEL.format(n=len(pairs)), "excluded": dict(excluded),
    }


def _score_metric(answers: Mapping[str, RunAnswer], entries: Sequence[Entry], metric: str,
                  human: Callable[[Entry], float | None]) -> dict[str, object]:
    pairs, excluded = [], {"no_claims": 0, "no_judge_score": 0}
    for entry in entries:
        mine = human(entry)
        theirs = _mean(answers[str(entry["key"])].judged.get(metric, ()))
        if mine is None:
            excluded["no_claims"] += 1
        elif theirs is None:
            excluded["no_judge_score"] += 1
        else:
            pairs.append((mine, theirs))
    row = _correlation(pairs, excluded)
    row["judge_self_agreement"] = _self_rho([answers[str(e["key"])] for e in entries], metric)
    return row


def _self_rho(answers: Sequence[RunAnswer], metric: str) -> float | None:
    """Spearman's rho between each pair of judge runs over the answers, averaged."""
    runs = [a.judged.get(metric, ()) for a in answers]
    values = []
    for i, j in combinations(range(3), 2):
        pairs = [(r[i], r[j]) for r in runs
                 if len(r) == 3 and r[i] is not None and r[j] is not None]
        rho = spearman([a for a, _ in pairs], [b for _, b in pairs]) if pairs else None
        if rho is not None:
            values.append(rho)
    return fmean(values) if values else None


def _citation_pairs(entries: Sequence[Entry], verdicts: Mapping[str, Sequence[Verdicts]]
                    ) -> tuple[list[tuple[list[bool], list[bool]]], int]:
    answers, dropped = [], 0
    for entry in entries:
        human = list(entry["citation_support"])  # type: ignore[arg-type]
        runs = verdicts.get(str(entry["key"]), ())
        kept_h, kept_j = [], []
        for index, mine in enumerate(human):
            theirs = majority(runs, index) if runs else None
            if theirs is None:
                dropped += 1
                continue
            kept_h.append(bool(mine))
            kept_j.append(theirs)
        if kept_h:
            answers.append((kept_h, kept_j))
    return answers, dropped


def _citation(entries: Sequence[Entry], verdicts: Mapping[str, Sequence[Verdicts]],
              seed: int) -> dict[str, object]:
    answers, dropped = _citation_pairs(entries, verdicts)
    flat_h = [v for h, _ in answers for v in h]
    flat_j = [v for _, j in answers for v in j]
    kappa = pooled_kappa(answers)
    interval = kappa_by_answer_interval(answers, interval_rng(seed, "calibration",
                                                              "citation_support"))
    agreement, disagreements = percent_agreement(flat_h, flat_j) if flat_h else (None, 0)
    return {
        "statistic": "cohen_kappa", "n_answers": len(answers), "n_sentences": len(flat_h),
        "value": kappa,
        "interval_95": [interval["low"], interval["high"]] if interval else None,
        "percent_agreement": agreement, "disagreements": disagreements,
        "threshold": PASS_AT, "passed": kappa is not None and kappa >= PASS_AT,
        "label": LABEL.format(n=len(answers)),
        "excluded": {"sentences_without_judge_majority": dropped},
        "judge_self_agreement": _self_kappa(verdicts, [str(e["key"]) for e in entries]),
    }


def _self_kappa(verdicts: Mapping[str, Sequence[Verdicts]], keys: Sequence[str]
                ) -> float | None:
    values = []
    for i, j in combinations(range(3), 2):
        a, b = [], []
        for key in keys:
            runs = verdicts.get(key, ())
            if len(runs) == 3 and runs[i] is not None and runs[j] is not None:
                a += list(runs[i])
                b += list(runs[j])
        kappa = cohen_kappa(a, b) if a else None
        if kappa is not None:
            values.append(kappa)
    return fmean(values) if values else None


def _behaviour(answers: Mapping[str, RunAnswer], entries: Sequence[Entry],
               metric: str) -> dict[str, object]:
    pairs = [(bool(e["behaviour_correct"]), m >= 0.5) for e in entries
             if (m := _mean(answers[str(e["key"])].judged.get(metric, ()))) is not None]
    value, disagreements = (percent_agreement([h for h, _ in pairs], [j for _, j in pairs])
                            if pairs else (None, 0))
    return {"statistic": "percent_agreement", "n": len(pairs), "value": value,
            "disagreements": disagreements, "passed": None,
            "note": "no pass rule: both outcomes are too rare for kappa"}


def _rescore(answers: Mapping[str, RunAnswer], first: Mapping[str, Entry],
             again: Sequence[Entry]) -> dict[str, object] | None:
    if not again:
        return None
    pairs = [(first[str(e["key"])], e) for e in again if str(e["key"]) in first]
    scored = [(a, b) for a, b in pairs if a["kind"] == "answer"]
    faith = [(x, y) for a, b in scored
             if (x := human_faithfulness(a)) is not None
             and (y := human_faithfulness(b)) is not None]
    citations = [(list(a["citation_support"]), list(b["citation_support"]))  # type: ignore[arg-type]
                 for a, b in scored]
    flat_a = [v for a, _ in citations for v in a]
    flat_b = [v for _, b in citations for v in b]
    behaviour = [(bool(a["behaviour_correct"]), bool(b["behaviour_correct"]))
                 for a, b in pairs if a["kind"] == "behaviour"]
    return {
        "answers": len(pairs),
        "faithfulness": {"n": len(faith), "rho": spearman(*zip(*faith, strict=True))
                         if len(faith) >= 3 else None,
                         "mae": mean_absolute_error(*zip(*faith, strict=True)) if faith else None},
        "answer_relevancy": {
            "n": len(scored),
            "exact": sum(a["relevancy"] == b["relevancy"] for a, b in scored),
            "mae": (mean_absolute_error([human_relevancy(a) for a, _ in scored],
                                        [human_relevancy(b) for _, b in scored])
                    if scored else None)},
        "citation_support": {"n_sentences": len(flat_a),
                             "kappa": cohen_kappa(flat_a, flat_b) if flat_a else None,
                             "disagreements": sum(x != y for x, y in zip(flat_a, flat_b,
                                                                         strict=True))},
        "behaviour": {"n": len(behaviour), "disagreements": sum(x != y for x, y in behaviour)},
    }


def _pace(entries: Sequence[Entry]) -> dict[str, object]:
    seconds = [float(e["seconds"]) for e in entries]  # type: ignore[arg-type]
    return {"answers": len(seconds), "median_seconds": median(seconds) if seconds else None,
            "total_minutes": sum(seconds) / 60,
            "under_15_seconds": sum(s < FAST_SECONDS for s in seconds),
            "looked_again": sum(bool(e["looked_again"]) for e in entries)}


def calibration_report(answers: Sequence[RunAnswer], entries: Sequence[Entry],
                       verdicts: Mapping[str, Sequence[Verdicts]],
                       seed: int) -> dict[str, object]:
    """The statistics for *answers* (the draw), from the user's *entries* and the judge's
    per-run citation *verdicts* keyed by answer."""
    found = {a.key: a for a in answers}
    first = {str(e["key"]): e for e in entries if not e["rescore"] and e["key"] in found}
    again = [e for e in entries if e["rescore"] and e["key"] in found]
    scored = [first[a.key] for a in answers if a.key in first]
    scored_answers = [e for e in scored if e["kind"] == "answer"]
    metrics: dict[str, object] = {
        "faithfulness": _score_metric(found, scored_answers, "faithfulness",
                                      human_faithfulness),
        "answer_relevancy": _score_metric(found, scored_answers, "answer_relevancy",
                                          human_relevancy),
        "citation_support": _citation(scored_answers, verdicts, seed),
    }
    for class_, metric in BEHAVIOURS.items():
        mine = [e for e in scored if found[str(e["key"])].class_ == class_]
        metrics[metric] = _behaviour(found, mine, metric)
    return {"drawn": len(answers), "scored": len(scored), "pass_at": PASS_AT, "metrics": metrics,
            "rescore": _rescore(found, first, again), "pace": _pace([*scored, *again])}


def _judge_verdicts(answers: Sequence[RunAnswer], parsed_dir: Path, response_cache: Path
                    ) -> dict[str, tuple[Verdicts, ...]]:
    index = ChunkIndex.from_parsed_dir(parsed_dir)
    found = {}
    for answer in answers:
        if answer.kind != "answer":
            continue
        texts = {c: t for c in answer.retrieved if (t := index.text(c)) is not None}
        found[answer.key] = cached_citation_verdicts(
            answer.question, answer.kept, judge_sources(answer.retrieved, texts), response_cache)
    return found


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: Sequence[str] | None = None, *, out_dir: Path = CALIBRATION_DIR,
         response_cache: Path = DEFAULT_CACHE_DIR) -> int:
    parser = argparse.ArgumentParser(description="Compare the judge with your scores.")
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--parsed-dir", type=Path, default=DEFAULT_PARSED_DIR)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 2
    try:
        draw_file = read_draw(out_dir / DRAW_PATH.name)
        answers = drawn_answers(draw_file, load_answers())
        scores = scores_path(args.reviewer, out_dir)
        entries = read_entries(scores)
        if not entries:
            raise ScoresFileError(f"{scores.name} holds no entries")
        verdicts = _judge_verdicts(answers, args.parsed_dir, response_cache)
    except (AnswerFileError, DrawError, ScoresFileError, OSError, ValueError) as exc:
        print(f"could not run: {exc}", file=sys.stderr)
        return 3
    report = calibration_report(answers, entries, verdicts, draw_file.seed)
    claims = out_dir / CLAIMS_FILE
    document = {
        **report,
        "questions": "agent-drafted questions, dev",
        "reviewer": args.reviewer.strip(),
        "commit": git_state(),
        "judging": JudgeConfig().as_header(),
        "inputs": {p.name: _sha256(p) for p in (out_dir / DRAW_PATH.name, claims, scores)
                   if p.exists()},
    }
    path = out_dir / REPORT_NAME
    write_json_atomic(path, document, indent=2)
    passed = [m for m, row in report["metrics"].items()  # type: ignore[union-attr]
              if row.get("passed")]
    print(f"wrote {path}: {report['scored']} of {report['drawn']} scored; "
          f"passed: {', '.join(passed) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
