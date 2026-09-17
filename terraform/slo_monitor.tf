# Scheduled SLO-monitoring Lambda (evals/slo_monitor.py). Same pattern as
# online_eval.tf: reuses the main app's container image with a different
# entry point, runs on its own EventBridge schedule. This function makes no
# Bedrock calls at all (it only reads LangSmith run/feedback data that the
# main app and the online-eval Lambda already record, then posts a webhook
# on breach), so its IAM role grants Logs only — not even Bedrock.

resource "aws_iam_role" "slo_monitor_lambda_exec" {
  name = "${var.project_name}-slo-monitor-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "slo_monitor_lambda_permissions" {
  name = "${var.project_name}-slo-monitor-lambda-permissions"
  role = aws_iam_role.slo_monitor_lambda_exec.id

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

resource "aws_lambda_function" "slo_monitor" {
  function_name = "${var.project_name}-slo-monitor"
  role          = aws_iam_role.slo_monitor_lambda_exec.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.app.repository_url}:latest"
  timeout       = 60
  memory_size   = 512

  image_config {
    command = ["slo_monitor_handler.handler"]
  }

  environment {
    variables = {
      LANGCHAIN_API_KEY           = var.langsmith_api_key
      LANGCHAIN_PROJECT           = var.project_name
      SLO_SINCE_MINUTES           = tostring(var.slo_since_minutes)
      SLO_LIMIT                   = tostring(var.slo_limit)
      ALERT_WEBHOOK_URL           = var.alert_webhook_url
      SLO_MAX_P95_LATENCY_SECONDS = tostring(var.slo_max_p95_latency_seconds)
      SLO_MAX_MEAN_COST_USD       = tostring(var.slo_max_mean_cost_usd)
      SLO_MIN_MEAN_QUALITY_SCORE  = tostring(var.slo_min_mean_quality_score)
    }
  }

  depends_on = [aws_ecr_repository.app]
}

resource "aws_cloudwatch_event_rule" "slo_monitor_schedule" {
  name                = "${var.project_name}-slo-monitor-schedule"
  schedule_expression = var.slo_monitor_schedule
}

resource "aws_cloudwatch_event_target" "slo_monitor" {
  rule = aws_cloudwatch_event_rule.slo_monitor_schedule.name
  arn  = aws_lambda_function.slo_monitor.arn
}

resource "aws_lambda_permission" "allow_eventbridge_slo_monitor" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.slo_monitor.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.slo_monitor_schedule.arn
}
