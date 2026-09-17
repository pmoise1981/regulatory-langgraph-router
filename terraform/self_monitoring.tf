# "Who watches the watcher": CloudWatch alarms on the SLO monitor Lambda's
# own AWS-provided metrics, forwarded through SNS to a small Lambda that
# posts into the same webhook channel evals/slo_monitor.py alerts to. If the
# SLO monitor stops running or starts erroring, this is what makes that
# visible instead of silent -- an alerting layer nobody watches defeats its
# own purpose.

resource "aws_sns_topic" "slo_monitor_health" {
  name = "${var.project_name}-slo-monitor-health"
}

# Fires when the Lambda invocation itself raises.
resource "aws_cloudwatch_metric_alarm" "slo_monitor_errors" {
  alarm_name          = "${var.project_name}-slo-monitor-errors"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods  = 1
  metric_name         = "Errors"
  namespace           = "AWS/Lambda"
  period              = var.slo_monitor_period_seconds
  statistic           = "Sum"
  threshold           = 1
  # No invocations yet just means nothing to alarm on for THIS metric --
  # the dead-man's-switch alarm below is what covers "didn't run at all".
  treat_missing_data = "notBreaching"
  alarm_description  = "The SLO monitor Lambda itself raised an error -- the reliability layer's own alerting path may be silently down."

  dimensions = {
    FunctionName = aws_lambda_function.slo_monitor.function_name
  }

  alarm_actions = [aws_sns_topic.slo_monitor_health.arn]
  ok_actions    = [aws_sns_topic.slo_monitor_health.arn]
}

# Dead man's switch: fires when the Lambda hasn't been invoked at all over a
# window wider than its own schedule -- e.g. EventBridge stopped triggering
# it, or it's stuck/throttled and never completing.
resource "aws_cloudwatch_metric_alarm" "slo_monitor_missed_invocation" {
  alarm_name          = "${var.project_name}-slo-monitor-missed-invocation"
  comparison_operator = "LessThanThreshold"
  evaluation_periods  = 1
  metric_name         = "Invocations"
  namespace           = "AWS/Lambda"
  # Wider than the schedule interval so one delayed EventBridge tick doesn't
  # false-alarm, while still catching "stopped running entirely" quickly.
  period    = var.slo_monitor_period_seconds * 2
  statistic = "Sum"
  threshold = 1
  # This is the actual point of a dead man's switch: no Invocations
  # datapoint must be treated as the failure itself, not ignored as "no
  # data" (CloudWatch's default for missing data would otherwise hide
  # exactly the failure mode this alarm exists to catch).
  treat_missing_data = "breaching"
  alarm_description  = "The SLO monitor Lambda hasn't run in twice its schedule interval -- EventBridge may have stopped triggering it, or it's stuck/throttled."

  dimensions = {
    FunctionName = aws_lambda_function.slo_monitor.function_name
  }

  alarm_actions = [aws_sns_topic.slo_monitor_health.arn]
  ok_actions    = [aws_sns_topic.slo_monitor_health.arn]
}

resource "aws_iam_role" "alarm_forwarder_lambda_exec" {
  name = "${var.project_name}-alarm-forwarder-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "alarm_forwarder_lambda_permissions" {
  name = "${var.project_name}-alarm-forwarder-lambda-permissions"
  role = aws_iam_role.alarm_forwarder_lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      }
    ]
  })
}

resource "aws_lambda_function" "alarm_forwarder" {
  function_name = "${var.project_name}-alarm-forwarder"
  role          = aws_iam_role.alarm_forwarder_lambda_exec.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.app.repository_url}:latest"
  timeout       = 30
  memory_size   = 256

  image_config {
    command = ["alarm_forwarder_handler.handler"]
  }

  environment {
    variables = {
      ALERT_WEBHOOK_URL = var.alert_webhook_url
    }
  }

  depends_on = [aws_ecr_repository.app]
}

resource "aws_sns_topic_subscription" "slo_monitor_health_to_forwarder" {
  topic_arn = aws_sns_topic.slo_monitor_health.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.alarm_forwarder.arn
}

resource "aws_lambda_permission" "allow_sns_alarm_forwarder" {
  statement_id  = "AllowSNSInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.alarm_forwarder.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.slo_monitor_health.arn
}
