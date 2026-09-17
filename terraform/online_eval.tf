# Scheduled online-evaluation Lambda (evals/online_eval.py). Reuses the same
# container image as the main app Lambda (lambda.tf) with a different entry
# point (image_config.command) instead of a second build/push pipeline, and
# runs on a fixed interval via EventBridge instead of per-request, since it
# scores a sample of recent production traces rather than answering queries.

resource "aws_iam_role" "online_eval_lambda_exec" {
  name = "${var.project_name}-online-eval-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "online_eval_lambda_permissions" {
  name = "${var.project_name}-online-eval-lambda-permissions"
  role = aws_iam_role.online_eval_lambda_exec.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:*"
      },
      {
        # This function only judges traces via Bedrock and talks to the
        # LangSmith API (LANGCHAIN_API_KEY, not IAM) — it never touches
        # OpenSearch, DynamoDB, or S3, so it gets none of those permissions,
        # unlike the main app's role in iam.tf.
        Sid    = "Bedrock"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:Converse",
          "bedrock:ConverseStream"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_lambda_function" "online_eval" {
  function_name = "${var.project_name}-online-eval"
  role          = aws_iam_role.online_eval_lambda_exec.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.app.repository_url}:latest"
  # Judging several sampled traces per run (each a Bedrock call) needs more
  # headroom than the main app's single-query timeout.
  timeout     = 300
  memory_size = 1024

  image_config {
    command = ["online_eval_handler.handler"]
  }

  environment {
    variables = {
      LANGCHAIN_TRACING_V2      = var.langsmith_api_key != "" ? "true" : "false"
      LANGCHAIN_API_KEY         = var.langsmith_api_key
      LANGCHAIN_PROJECT         = var.project_name
      ONLINE_EVAL_SINCE_MINUTES = tostring(var.online_eval_since_minutes)
      ONLINE_EVAL_SAMPLE_RATE   = tostring(var.online_eval_sample_rate)
      ONLINE_EVAL_LIMIT         = tostring(var.online_eval_limit)
    }
  }

  depends_on = [aws_ecr_repository.app]
}

resource "aws_cloudwatch_event_rule" "online_eval_schedule" {
  name                = "${var.project_name}-online-eval-schedule"
  schedule_expression = var.online_eval_schedule
}

resource "aws_cloudwatch_event_target" "online_eval" {
  rule = aws_cloudwatch_event_rule.online_eval_schedule.name
  arn  = aws_lambda_function.online_eval.arn
}

resource "aws_lambda_permission" "allow_eventbridge_online_eval" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.online_eval.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.online_eval_schedule.arn
}
