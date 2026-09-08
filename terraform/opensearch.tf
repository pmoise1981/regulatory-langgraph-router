resource "aws_opensearch_domain" "main" {
  domain_name    = var.opensearch_domain_name
  engine_version = "OpenSearch_2.11" # k-NN plugin included by default at this version

  cluster_config {
    instance_type  = var.opensearch_instance_type
    instance_count = 1
  }

  ebs_options {
    ebs_enabled = true
    volume_size = 20
  }

  encrypt_at_rest {
    enabled = true
  }

  node_to_node_encryption {
    enabled = true
  }

  domain_endpoint_options {
    enforce_https = true
  }
}

# Resource-based access policy: only the Lambda's IAM role can call this domain.
resource "aws_opensearch_domain_policy" "main" {
  domain_name = aws_opensearch_domain.main.domain_name

  access_policies = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = aws_iam_role.lambda_exec.arn }
      Action    = "es:ESHttp*"
      Resource  = "${aws_opensearch_domain.main.arn}/*"
    }]
  })
}
