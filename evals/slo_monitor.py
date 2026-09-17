"""SLO monitoring and alerting (piece #4 of the AI reliability layer).

Checks recent production traces in the LangSmith project against defined
latency/cost/quality thresholds (config.py) and posts a webhook alert when
any is breached. Meant to run on a schedule (same EventBridge/Lambda pattern
as evals/online_eval.py — see terraform/slo_monitor.tf) so a breach surfaces
on its own instead of requiring someone to go look at the LangSmith UI.

Metrics, computed over a trailing window of root runs:
  - p95 latency (end_time - start_time per run)
  - mean cost per query (Run.total_cost, which LangSmith computes from
    token usage — no separate cost tracking needed)
  - mean LLM-judge quality score, read from the feedback online_eval.py
    already writes (JUDGE_FEEDBACK_KEY) — this only reads that feedback,
    it doesn't run its own judge calls, so it makes no Bedrock calls at all.

Usage:
    python evals/slo_monitor.py --project regulatory-langgraph-router \
        --since-minutes 30

Requires LANGSMITH_API_KEY. ALERT_WEBHOOK_URL is optional — if unset,
breaches are printed but no webhook is sent.
"""

import argparse
import os
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from langsmith import Client
from langsmith.schemas import Run

# config.py lives at the repo root, one level up from evals/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import (  # noqa: E402
    ALERT_WEBHOOK_URL,
    JUDGE_FEEDBACK_KEY,
    SLO_MAX_MEAN_COST_USD,
    SLO_MAX_P95_LATENCY_SECONDS,
    SLO_MIN_MEAN_QUALITY_SCORE,
)


@dataclass
class SLOBreach:
    metric: str
    detail: str


def _p95(values: list[float]) -> float:
    ordered = sorted(values)
    index = min(int(len(ordered) * 0.95), len(ordered) - 1)
    return ordered[index]


def fetch_window_runs(client: Client, project_name: str, since_minutes: int, limit: int) -> list[Run]:
    since = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    return list(
        client.list_runs(project_name=project_name, is_root=True, start_time=since, limit=limit)
    )


def compute_metrics(client: Client, runs: list[Run]) -> dict:
    latencies = [
        (r.end_time - r.start_time).total_seconds() for r in runs if r.start_time and r.end_time
    ]
    costs = [r.total_cost for r in runs if r.total_cost is not None]

    quality_scores = []
    if runs:
        # One batched call for the whole window rather than one per run.
        feedback = client.list_feedback(run_ids=[r.id for r in runs], feedback_key=[JUDGE_FEEDBACK_KEY])
        quality_scores = [f.score for f in feedback if f.score is not None]

    return {
        "num_runs": len(runs),
        "p95_latency_seconds": _p95(latencies) if latencies else None,
        "mean_cost_usd": statistics.mean(costs) if costs else None,
        "mean_quality_score": statistics.mean(quality_scores) if quality_scores else None,
        "num_quality_scored": len(quality_scores),
    }


def check_slos(metrics: dict) -> list[SLOBreach]:
    breaches = []

    p95 = metrics["p95_latency_seconds"]
    if p95 is not None and p95 > SLO_MAX_P95_LATENCY_SECONDS:
        breaches.append(
            SLOBreach(
                "p95_latency_seconds",
                f"p95 latency {p95:.2f}s over {metrics['num_runs']} runs exceeds "
                f"{SLO_MAX_P95_LATENCY_SECONDS}s",
            )
        )

    mean_cost = metrics["mean_cost_usd"]
    if mean_cost is not None and mean_cost > SLO_MAX_MEAN_COST_USD:
        breaches.append(
            SLOBreach(
                "mean_cost_usd",
                f"mean cost ${mean_cost:.4f}/query over {metrics['num_runs']} runs exceeds "
                f"${SLO_MAX_MEAN_COST_USD}",
            )
        )

    mean_quality = metrics["mean_quality_score"]
    if mean_quality is not None and mean_quality < SLO_MIN_MEAN_QUALITY_SCORE:
        breaches.append(
            SLOBreach(
                "mean_quality_score",
                f"mean LLM-judge quality {mean_quality:.2f} (n={metrics['num_quality_scored']}) "
                f"below {SLO_MIN_MEAN_QUALITY_SCORE}",
            )
        )

    return breaches


def send_alert(webhook_url: str, project_name: str, breaches: list[SLOBreach], metrics: dict) -> None:
    if not webhook_url:
        print("ALERT_WEBHOOK_URL not set — breach(es) logged above only, no webhook sent.")
        return

    text = f":rotating_light: SLO breach in `{project_name}`\n" + "\n".join(
        f"- {b.detail}" for b in breaches
    )
    response = requests.post(webhook_url, json={"text": text}, timeout=10)
    response.raise_for_status()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        default=os.environ.get("LANGSMITH_PROJECT") or os.environ.get("LANGCHAIN_PROJECT"),
        help="LangSmith project name to check.",
    )
    parser.add_argument(
        "--since-minutes",
        type=int,
        default=30,
        help="Trailing window (minutes) of root runs to compute metrics over.",
    )
    parser.add_argument("--limit", type=int, default=500, help="Max root runs to fetch per invocation.")
    args = parser.parse_args()

    if not args.project:
        parser.error("--project is required (or set LANGSMITH_PROJECT / LANGCHAIN_PROJECT).")

    client = Client()
    runs = fetch_window_runs(client, args.project, args.since_minutes, args.limit)
    metrics = compute_metrics(client, runs)
    print(f"Metrics over last {args.since_minutes} min ({metrics['num_runs']} runs): {metrics}")

    breaches = check_slos(metrics)
    if not breaches:
        print("All SLOs within threshold.")
        return

    for b in breaches:
        print(f"[SLO BREACH] {b.detail}")
    send_alert(ALERT_WEBHOOK_URL, args.project, breaches, metrics)


if __name__ == "__main__":
    main()
