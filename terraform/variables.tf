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

variable "online_eval_schedule" {
  description = "EventBridge schedule expression for the online-evaluation Lambda."
  default     = "rate(1 hour)"
}

variable "online_eval_since_minutes" {
  description = "Trailing window (minutes) of production traces the online evaluator considers per run. Should be >= the schedule interval so no traces are skipped between runs."
  type        = number
  default     = 60
}

variable "online_eval_sample_rate" {
  description = "Fraction (0.0-1.0) of eligible production traces the online evaluator actually judges."
  type        = number
  default     = 0.2
}

variable "online_eval_limit" {
  description = "Max root runs the online evaluator fetches per invocation."
  type        = number
  default     = 200
}
