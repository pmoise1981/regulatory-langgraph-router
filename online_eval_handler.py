# Entry point for the scheduled online-evaluation Lambda (see
# terraform/online_eval.tf) — EventBridge invokes this on a fixed interval to
# score a sample of recent production traces with the LLM-as-judge defined in
# evals/online_eval.py. Deployed via the same container image as
# lambda_handler.py, just with a different CMD override.
import os

from langsmith import Client

from evals.online_eval import run_online_evaluation


def handler(event, context):
    project_name = os.environ.get("LANGCHAIN_PROJECT")
    if not project_name:
        raise RuntimeError("LANGCHAIN_PROJECT env var is required but not set.")

    since_minutes = int(os.environ.get("ONLINE_EVAL_SINCE_MINUTES", "60"))
    sample_rate = float(os.environ.get("ONLINE_EVAL_SAMPLE_RATE", "0.2"))
    limit = int(os.environ.get("ONLINE_EVAL_LIMIT", "100"))

    client = Client()
    stats = run_online_evaluation(
        client,
        project_name=project_name,
        since_minutes=since_minutes,
        sample_rate=sample_rate,
        limit=limit,
    )
    print(f"Online eval run complete: {stats}")
    return {"statusCode": 200, "body": stats}
