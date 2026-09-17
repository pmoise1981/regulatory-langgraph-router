"""Online evaluation (piece #2 of the AI reliability layer): scores live
production traces in the LangSmith project with an LLM-as-judge, so quality
signal exists between golden-set runs (eval_langsmith.py), not just at them.

This is deliberately a script you run on a schedule (cron, or an EventBridge-
triggered Lambda alongside the ones this repo already deploys via Terraform)
rather than a LangSmith UI-configured "Rule": the LangSmith Python SDK
(0.12.x, what this repo pins) doesn't expose rule creation, so the
UI/REST-only "Rules" feature can't be checked into this repo as code. This
script gets the same outcome — an LLM judge scoring sampled production
traces — as versioned, testable code instead.

What it does, each run:
  1. Lists root runs (i.e. top-level graph.invoke calls, not internal graph
     nodes) from the target LangSmith project in a trailing time window.
  2. Samples them (scoring every single trace with an LLM judge doubles
     inference cost/latency on top of production traffic — sampling is the
     standard tradeoff for online evals).
  3. Skips any run that already has feedback under our judge's key, so
     overlapping time windows across runs of this script don't double-score.
  4. Judges faithfulness + relevance with a structured-output LLM call
     (Pydantic schema, same with_structured_output pattern graph.py's
     classifier already uses) and writes the result back as LangSmith
     feedback (feedback_source_type="model") attached to that run.

Usage:
    python evals/online_eval.py --project regulatory-langgraph-router \
        --since-minutes 60 --sample-rate 0.2

Requires LANGSMITH_API_KEY (and the AWS credentials config.py's Bedrock
clients need) in the environment.
"""

import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from langchain_core.prompts import ChatPromptTemplate
from langsmith import Client
from langsmith.schemas import FeedbackSourceType, Run
from pydantic import BaseModel, Field

# config.py / guardrails.py live at the repo root, one level up from evals/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import JUDGE_FEEDBACK_KEY, llm  # noqa: E402
from guardrails import structured_output_retry  # noqa: E402


class QualityJudgment(BaseModel):
    faithful: bool = Field(
        description="True if every claim in the answer is supported by the retrieved context"
    )
    relevant: bool = Field(
        description="True if the answer actually addresses the question that was asked"
    )
    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Overall quality score from 0.0 (bad) to 1.0 (excellent), combining "
        "faithfulness and relevance",
    )
    reasoning: str = Field(description="One or two sentence justification, for audit purposes")


judge_llm = llm.with_structured_output(QualityJudgment)

judge_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You are grading a regulatory Q&A system's answer for a compliance audit. "
            "Judge two things: (1) faithfulness - is every claim in the answer actually "
            "supported by the retrieved context, with no unsupported additions? "
            "(2) relevance - does the answer address the question that was actually asked? "
            "If the context doesn't contain the answer and the system said so explicitly, "
            "that counts as faithful. Score harshly: an answer with any unsupported claim "
            "is not faithful.",
        ),
        (
            "human",
            "Question: {question}\n\nRetrieved context:\n{context}\n\nAnswer given: {answer}",
        ),
    ]
)


def _document_text(doc) -> str:
    """Production traces come back from the LangSmith API as plain JSON, not
    live Document objects, so this handles however a Document round-tripped
    through tracing serialization (dict with page_content, or a fallback for
    anything else) rather than assuming one fixed shape."""
    if isinstance(doc, dict):
        return doc.get("page_content") or doc.get("kwargs", {}).get("page_content", "") or ""
    return getattr(doc, "page_content", str(doc))


@structured_output_retry
def judge_run(run: Run) -> QualityJudgment:
    """Runs the LLM-as-judge against one production trace's real
    question/context/answer — pulled from the graph's actual state shape
    (query/documents/answer), the same GraphState defined in graph.py."""
    inputs = run.inputs or {}
    outputs = run.outputs or {}
    question = inputs.get("query", "")
    answer = outputs.get("answer", "")
    documents = outputs.get("documents") or []
    context = "\n\n".join(_document_text(d) for d in documents)

    chain = judge_prompt | judge_llm
    return chain.invoke({"question": question, "context": context, "answer": answer})


def already_scored(client: Client, run_id) -> bool:
    return any(client.list_feedback(run_ids=[run_id], feedback_key=[JUDGE_FEEDBACK_KEY]))


def run_online_evaluation(
    client: Client,
    project_name: str,
    since_minutes: int,
    sample_rate: float,
    limit: int,
) -> dict:
    since = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    runs = client.list_runs(
        project_name=project_name,
        is_root=True,
        start_time=since,
        limit=limit,
    )

    stats = {"scored": 0, "skipped_sample": 0, "skipped_already_scored": 0, "errored": 0}
    for run in runs:
        if random.random() > sample_rate:
            stats["skipped_sample"] += 1
            continue
        if already_scored(client, run.id):
            stats["skipped_already_scored"] += 1
            continue

        try:
            judgment = judge_run(run)
        except Exception as e:
            print(f"  [error] run {run.id}: {e}")
            stats["errored"] += 1
            continue

        client.create_feedback(
            run_id=run.id,
            key=JUDGE_FEEDBACK_KEY,
            score=judgment.score,
            comment=judgment.reasoning,
            feedback_source_type=FeedbackSourceType.MODEL,
            extra={"faithful": judgment.faithful, "relevant": judgment.relevant},
        )
        stats["scored"] += 1
        print(f"  scored run {run.id}: score={judgment.score:.2f} - {judgment.reasoning}")

    return stats


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        default=os.environ.get("LANGSMITH_PROJECT") or os.environ.get("LANGCHAIN_PROJECT"),
        help="LangSmith project name to pull production traces from.",
    )
    parser.add_argument(
        "--since-minutes",
        type=int,
        default=60,
        help="Only consider root runs started within this trailing window.",
    )
    parser.add_argument(
        "--sample-rate",
        type=float,
        default=0.2,
        help="Fraction of eligible runs to actually judge (0.0-1.0).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max root runs to fetch per invocation.",
    )
    args = parser.parse_args()

    if not args.project:
        parser.error(
            "--project is required (or set LANGSMITH_PROJECT / LANGCHAIN_PROJECT)."
        )

    client = Client()
    print(
        f"Scoring production traces in '{args.project}' from the last "
        f"{args.since_minutes} min, sample_rate={args.sample_rate}..."
    )
    stats = run_online_evaluation(
        client,
        project_name=args.project,
        since_minutes=args.since_minutes,
        sample_rate=args.sample_rate,
        limit=args.limit,
    )
    print(f"\nDone: {stats}")


if __name__ == "__main__":
    main()
