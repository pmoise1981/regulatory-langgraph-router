# Entry point for forwarding CloudWatch Alarm state changes into the same
# alert channel evals/slo_monitor.py uses (see terraform/self_monitoring.tf).
#
# This exists to answer one question: if the SLO monitor itself stops
# running or starts erroring, does anything notice? Without this, the SLO
# monitor could fail silently forever -- an alerting layer that nobody
# watches defeats its own purpose. Two CloudWatch alarms feed this, both on
# the slo_monitor Lambda's own AWS-provided metrics (no application code
# needed for the alarms themselves):
#   - Errors >= 1: the Lambda invocation itself raised.
#   - Invocations < 1 over a window wider than its schedule (a "dead man's
#     switch"): the Lambda didn't even run -- EventBridge stopped
#     triggering it, or it's stuck/throttled. Silence itself is the signal.
#
# Deployed via the same container image as the other Lambdas here,
# subscribed to an SNS topic both alarms publish state changes to.
import json
import os

import requests

ALERT_WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL", "")


def format_alarm_message(alarm: dict) -> str:
    """Pure function: builds the Slack-compatible alert text from a parsed
    CloudWatch alarm state-change payload. Kept separate from the SNS/env
    plumbing below so it's unit-testable without mocking AWS at all."""
    name = alarm.get("AlarmName", "unknown alarm")
    state = alarm.get("NewStateValue", "UNKNOWN")
    reason = alarm.get("NewStateReason", "")

    if state == "ALARM":
        icon = ":rotating_light:"
    elif state == "OK":
        icon = ":white_check_mark:"
    else:
        icon = ":grey_question:"

    return f"{icon} SLO monitor health: `{name}` is now *{state}*\n{reason}"


def handler(event, context):
    results = []
    for record in event.get("Records", []):
        raw_message = record.get("Sns", {}).get("Message", "{}")
        try:
            alarm = json.loads(raw_message)
        except json.JSONDecodeError:
            print(f"Could not parse SNS message as JSON: {raw_message!r}")
            continue

        text = format_alarm_message(alarm)
        print(text)

        if not ALERT_WEBHOOK_URL:
            print("ALERT_WEBHOOK_URL not set -- logged above only, no webhook sent.")
            results.append({"alarm": alarm.get("AlarmName"), "sent": False})
            continue

        response = requests.post(ALERT_WEBHOOK_URL, json={"text": text}, timeout=10)
        response.raise_for_status()
        results.append({"alarm": alarm.get("AlarmName"), "sent": True})

    return {"statusCode": 200, "body": {"processed": results}}
