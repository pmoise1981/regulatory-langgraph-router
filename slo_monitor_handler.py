# Entry point for the scheduled SLO-monitoring Lambda (see
# terraform/slo_monitor.tf) — EventBridge invokes this on a fixed interval to
# check recent production traces against the latency/cost/quality thresholds
# in config.py and fire a webhook alert on a breach (evals/slo_monitor.py).
# Deployed via the same container image as lambda_handler.py, just with a
# different CMD override. Makes no Bedrock calls itself (it only reads
# LangSmith run/feedback data already recorded by the main app and
# online_eval_handler.py), so its IAM role in Terraform grants no Bedrock
# permission at all.
import os

from langsmith import Client

from config import ALERT_WEBHOOK_URL
from evals.slo_monitor import check_slos, compute_metrics, fetch_window_runs, send_alert


def handler(event, context):
    project_name = os.environ.get("LANGCHAIN_PROJECT")
    if not project_name:
        raise RuntimeError("LANGCHAIN_PROJECT env var is required but not set.")

    since_minutes = int(os.environ.get("SLO_SINCE_MINUTES", "30"))
    limit = int(os.environ.get("SLO_LIMIT", "500"))

    client = Client()
    runs = fetch_window_runs(client, project_name, since_minutes, limit)
    metrics = compute_metrics(client, runs)
    print(f"Metrics over last {since_minutes} min ({metrics['num_runs']} runs): {metrics}")

    breaches = check_slos(metrics)
    if not breaches:
        print("All SLOs within threshold.")
        return {"statusCode": 200, "body": {"breaches": [], "metrics": metrics}}

    for b in breaches:
        print(f"[SLO BREACH] {b.detail}")
    send_alert(ALERT_WEBHOOK_URL, project_name, breaches, metrics)
    return {
        "statusCode": 200,
        "body": {"breaches": [b.detail for b in breaches], "metrics": metrics},
    }
