output "lambda_url" {
  value = aws_lambda_function_url.app.function_url
}

output "opensearch_endpoint" {
  value = aws_opensearch_domain.main.endpoint
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "documents_bucket" {
  value = aws_s3_bucket.documents.bucket
}

output "online_eval_lambda_name" {
  value = aws_lambda_function.online_eval.function_name
}

output "slo_monitor_lambda_name" {
  value = aws_lambda_function.slo_monitor.function_name
}
