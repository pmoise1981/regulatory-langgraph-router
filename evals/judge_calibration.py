"""LLM-as-judge calibration: checks the online evaluator's judge (the same
judge_answer() in evals/online_eval.py that scores live production traces)
against a small hand-labeled set of clear-cut cases (evals/judge_calibration_
set.json), and reports how often the judge agrees with the human labels.

WHY THIS EXISTS (the industry context, so the "why" isn't just a comment):
LLM-as-judge is now the standard way teams score RAG/agent quality at scale
-- RAGAS, DeepEval, and this repo's own eval_langsmith.py all do it. But an
LLM judge has known, well-documented biases (it can be fooled by confident,
fluent-sounding but unsupported claims; it can conflate "off-topic" with
"low quality" inconsistently). The mature pattern isn't "trust the judge's
score blindly" -- it's "periodically check the judge itself against a small
set of cases a human has already decided the right answer for." That's all
this script does. It does NOT replace evals/online_eval.py's judge (same
prompt, same model, same QualityJudgment schema) -- it periodically checks
whether that judge is still behaving the way a human would expect.

WHEN TO RUN THIS:
  - Whenever you change judge_prompt or QualityJudgment in online_eval.py,
    to see whether the change moved the judge's behavior in the direction
    you intended.
  - Occasionally in general, to catch silent drift (e.g. a Bedrock model
    version update changing behavior underneath you).

This is deliberately NOT wired into a scheduled Lambda like online_eval.py
or slo_monitor.py are: it's a small, cheap, on-demand check (6 Bedrock
calls per run in the starter set below), not a recurring cost. Run it by
hand: `python evals/judge_calibration.py`.

THE CALIBRATION SET ITSELF (evals/judge_calibration_set.json) is a small
starter set of 6 examples, deliberately constructed (not fabricated
production data) to cover the failure modes a judge needs to catch: a
correct paraphrase, a plausible-but-fabricated added claim, an off-topic
but factually-true answer, and one genuinely ambiguous case (faithful but
incomplete). It reuses the same reference facts already in
golden_dataset.json -- see that file's docstring-equivalent comments for
why. Expand it over time with real judge disagreements you find once this
system has live traffic; 6 examples is a starting point, not a statistically
rigorous calibration set.
"""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from langsmith import Client

# config.py / online_eval.py live one level up (config.py) and in this same
# evals/ directory (online_eval.py) respectively.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from evals.online_eval import judge_answer  # noqa: E402

CALIBRATION_SET_PATH = Path(__file__).resolve().parent / "judge_calibration_set.json"
CALIBRATION_RUN_NAME = "judge_calibration_check"


def load_calibration_set() -> list[dict]:
    """Same fail-loudly pattern as eval_langsmith.py's load_golden_dataset():
    a missing/empty file should stop the script, not silently report a
    meaningless "0/0 agreement = 100%!" result."""
    if not CALIBRATION_SET_PATH.exists():
        raise FileNotFoundError(f"Calibration set not found at {CALIBRATION_SET_PATH}")
    with open(CALIBRATION_SET_PATH) as f:
        calibration_set = json.load(f)
    if not calibration_set:
        raise ValueError(f"{CALIBRATION_SET_PATH} is empty -- nothing to calibrate against.")
    return calibration_set


def check_one_example(example: dict) -> dict:
    """Runs the real judge (judge_answer, imported from online_eval.py --
    literally the same function that scores production traces) against one
    hand-labeled fixture, and compares its verdict to the expected labels.

    Returns a plain dict rather than a dataclass so it serializes directly
    into the LangSmith run's outputs in run_calibration() below without a
    conversion step.
    """
    judgment = judge_answer(
        question=example["question"],
        context=example["context"],
        answer=example["answer"],
    )

    faithful_agrees = judgment.faithful == example["expected_faithful"]
    relevant_agrees = judgment.relevant == example["expected_relevant"]
    score_in_band = example["expected_score_min"] <= judgment.score <= example["expected_score_max"]

    return {
        "id": example["id"],
        "note": example["note"],
        "judge_faithful": judgment.faithful,
        "expected_faithful": example["expected_faithful"],
        "faithful_agrees": faithful_agrees,
        "judge_relevant": judgment.relevant,
        "expected_relevant": example["expected_relevant"],
        "relevant_agrees": relevant_agrees,
        "judge_score": judgment.score,
        "expected_score_range": [example["expected_score_min"], example["expected_score_max"]],
        "score_in_band": score_in_band,
        "judge_reasoning": judgment.reasoning,
    }


def run_calibration(client: Client, calibration_set: list[dict]) -> dict:
    """Runs check_one_example over the whole set, aggregates agreement
    rates, and logs the whole check as one explicit LangSmith run --
    exactly the same Client.create_run/update_run pattern
    evals/slo_monitor.py's run_self_check() uses, for the same reason: this
    makes the judge's calibration history visible in the LangSmith project
    itself (browsable, comparable run-to-run) rather than only in whoever's
    terminal happened to run this script.
    """
    run_id = uuid.uuid4()
    client.create_run(
        id=run_id,
        name=CALIBRATION_RUN_NAME,
        run_type="tool",
        inputs={"num_examples": len(calibration_set)},
        start_time=datetime.now(timezone.utc),
    )

    try:
        results = [check_one_example(example) for example in calibration_set]
    except Exception as e:
        client.update_run(run_id, error=str(e), end_time=datetime.now(timezone.utc))
        raise

    n = len(results)
    faithful_agreement_rate = sum(r["faithful_agrees"] for r in results) / n
    relevant_agreement_rate = sum(r["relevant_agrees"] for r in results) / n
    score_in_band_rate = sum(r["score_in_band"] for r in results) / n

    summary = {
        "num_examples": n,
        "faithful_agreement_rate": faithful_agreement_rate,
        "relevant_agreement_rate": relevant_agreement_rate,
        "score_in_band_rate": score_in_band_rate,
        "disagreements": [r["id"] for r in results if not (r["faithful_agrees"] and r["relevant_agrees"])],
    }

    client.update_run(
        run_id,
        outputs={"summary": summary, "results": results},
        end_time=datetime.now(timezone.utc),
    )

    return {"summary": summary, "results": results}


def print_report(outcome: dict) -> None:
    summary = outcome["summary"]
    print(f"\n=== Judge calibration: {summary['num_examples']} examples ===")
    print(f"Faithfulness agreement: {summary['faithful_agreement_rate']:.0%}")
    print(f"Relevance agreement:    {summary['relevant_agreement_rate']:.0%}")
    print(f"Scores within expected band: {summary['score_in_band_rate']:.0%}")

    if summary["disagreements"]:
        print(f"\nDisagreements ({len(summary['disagreements'])}):")
        for result in outcome["results"]:
            if result["id"] not in summary["disagreements"]:
                continue
            print(f"\n  [{result['id']}] {result['note']}")
            print(
                f"    faithful: judge={result['judge_faithful']} "
                f"expected={result['expected_faithful']} "
                f"({'OK' if result['faithful_agrees'] else 'DISAGREE'})"
            )
            print(
                f"    relevant: judge={result['judge_relevant']} "
                f"expected={result['expected_relevant']} "
                f"({'OK' if result['relevant_agrees'] else 'DISAGREE'})"
            )
            print(f"    judge's reasoning: {result['judge_reasoning']}")
    else:
        print("\nNo disagreements on faithful/relevant labels.")


def main():
    calibration_set = load_calibration_set()
    client = Client()
    print(f"Running judge calibration against {len(calibration_set)} hand-labeled examples...")
    outcome = run_calibration(client, calibration_set)
    print_report(outcome)


if __name__ == "__main__":
    main()
