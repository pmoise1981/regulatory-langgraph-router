variable "aws_region" {
  default = "us-east-1"
}

variable "project_name" {
  default = "regulatory-langgraph-router"
}

variable "opensearch_instance_type" {
  default = "t3.small.search" # smallest instance that supports k-NN; bump for real workloads
}

variable "langsmith_api_key" {
  description = "LangSmith API key for tracing. Pass via TF_VAR_langsmith_api_key env var — never commit it."
  type        = string
  sensitive   = true
  default     = ""
}

variable "opensearch_domain_name" {
  description = "OpenSearch domain name — AWS caps this at 28 chars, so it's separate from project_name."
  default     = "reg-lg-router-search"
}
