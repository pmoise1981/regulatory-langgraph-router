resource "aws_lambda_function" "app" {
  function_name = var.project_name
  role          = aws_iam_role.lambda_exec.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.app.repository_url}:latest"
  timeout       = 60
  memory_size   = 2048

  environment {
    variables = {
      OPENSEARCH_URL       = "https://${aws_opensearch_domain.main.endpoint}"
      LANGCHAIN_TRACING_V2 = var.langsmith_api_key != "" ? "true" : "false"
      LANGCHAIN_API_KEY    = var.langsmith_api_key
      LANGCHAIN_PROJECT    = var.project_name
    }
  }

  depends_on = [aws_ecr_repository.app]
}

resource "aws_lambda_function_url" "app" {
  function_name      = aws_lambda_function.app.function_name
  authorization_type = "AWS_IAM" # requires SigV4-signed requests; switch to NONE only if you want it public
}
