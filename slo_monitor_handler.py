# Entry point for the scheduled SLO-monitoring Lambda (see
# terraform/slo_monitor.tf) — EventBridge invokes this on a fixed interval to
# check recent production traces against the latency/cost/quality thresholds
# in config.py and fire a webhook alert on a breach (evals/slo_monitor.py).
# Deployed via the same container image as lambda_handler.py, just with a
# different CMD override. Makes no Bedrock calls itself (it only reads
# LangSmith run/feedback data already recorded by the main app and
# online_eval_handler.py), so its IAM role in Terraform grants no Bedrock
# permission at all.
#
# run_self_check() additionally logs this invocation as an explicit
# LangSmith run (pass/fail, not an LLM trace) so its own history is visible
# from the same LangSmith project as everything else -- see run_self_check's
# docstring for why that's additive to, not a replacement for, the
# CloudWatch alarms in terraform/self_monitoring.tf that actually trigger
# paging on a crash.
import os

from langsmith import Client

from config import ALERT_WEBHOOK_URL
from evals.slo_monitor import run_self_check, send_alert


def handler(event, context):
    project_name = os.environ.get("LANGCHAIN_PROJECT")
    if not project_name:
        raise RuntimeError("LANGCHAIN_PROJECT env var is required but not set.")

    since_minutes = int(os.environ.get("SLO_SINCE_MINUTES", "30"))
    limit = int(os.environ.get("SLO_LIMIT", "100"))

    client = Client()
    result = run_self_check(client, project_name, since_minutes, limit)
    metrics = result["metrics"]
    breaches = result["breaches"]
    print(f"Metrics over last {since_minutes} min ({metrics['num_runs']} runs): {metrics}")

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
