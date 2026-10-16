"""`python -m eval.global_estimate`: the full graph_global run's estimate against Step 12's
cap (Step 12 ticket 07).  The map and reduce estimate is injected; the judge estimate
and the cap check are the real ones."""
import json

import pytest

from eval.global_estimate import EXIT_OK, EXIT_OVER_CAP, EXIT_RUN_ERROR, JUDGE_MARGIN, main

DEV_GLOBAL = {"q0023", "q0030"}  # two of the dev global questions


@pytest.fixture
def dirs(tmp_path):
    communities, runs = tmp_path / "communities", tmp_path / "runs"
    communities.mkdir()
    runs.mkdir()
    (communities / "reports-run1.jsonl").write_text(json.dumps({"cost_this_run_usd": 1.0}) + "\n")
    return communities, runs


def _judged_run(runs, costs: dict[str, float]) -> None:
    (runs / "2026-10-14-graph_local-dev-375efac.json").write_text(json.dumps({
        "header": {"arm": "graph_local"},
        "questions": [{"id": q, "judged": {"cost_usd": c}} for q, c in costs.items()]}))


def _main(dirs, models_usd: float, *argv: str):
    communities, runs = dirs
    seen = []

    def models(records):
        seen.extend(r.id for r in records)
        return models_usd

    code = main(["--classes", "global", *argv], model_estimate=models,
                communities_dir=communities, runs_dir=runs, judged_run=runs /
                "2026-10-14-graph_local-dev-375efac.json")
    return code, seen


def test_within_the_remainder_it_prints_the_estimate_and_exits_0(dirs, capsys):
    _judged_run(dirs[1], {q: 0.01 for q in DEV_GLOBAL} | {"q9999": 5.0})
    code, seen = _main(dirs, 2.0)
    assert code == EXIT_OK
    assert DEV_GLOBAL <= set(seen) and len(seen) == 10  # the 10 dev global questions
    out = capsys.readouterr().out
    assert "Step 12 spend so far $1.00 of $6.00" in out and "remaining $5.00" in out


def test_judges_are_estimated_from_a_past_runs_judged_cost_times_the_margin(dirs, capsys):
    _judged_run(dirs[1], {q: 0.1 for q in DEV_GLOBAL})
    code, _ = _main(dirs, 0.0)
    # Questions the past run did not judge are priced at its highest per-question cost.
    assert f"judges ${10 * 0.1 * JUDGE_MARGIN:.2f}" in capsys.readouterr().out
    assert code == EXIT_OK


def test_above_the_remainder_it_exits_4(dirs, capsys):
    _judged_run(dirs[1], {q: 0.01 for q in DEV_GLOBAL})
    code, _ = _main(dirs, 4.9)
    assert code == EXIT_OVER_CAP
    assert "would pass the $6.00 cap" in capsys.readouterr().err


def test_a_past_run_with_no_judged_question_cannot_price_the_judges(dirs, capsys):
    _judged_run(dirs[1], {})
    code, _ = _main(dirs, 0.0)
    assert code == EXIT_RUN_ERROR
    assert "judged" in capsys.readouterr().err
