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
  description = "Max root runs the online evaluator fetches per invocation. LangSmith's /runs/query API hard-caps a single request at 100 -- do not raise this above 100, the Lambda will fail on every invocation if you do."
  type        = number
  default     = 100
}

variable "slo_monitor_schedule" {
  description = "EventBridge schedule expression for the SLO-monitoring Lambda."
  default     = "rate(15 minutes)"
}

variable "slo_since_minutes" {
  description = "Trailing window (minutes) of production traces the SLO monitor checks per run. Should be >= the schedule interval so no traces are skipped between runs."
  type        = number
  default     = 30
}

variable "slo_limit" {
  description = "Max root runs the SLO monitor fetches per invocation. LangSmith's /runs/query API hard-caps a single request at 100 -- do not raise this above 100, the Lambda will fail on every invocation if you do."
  type        = number
  default     = 100
}

variable "alert_webhook_url" {
  description = "Slack-compatible incoming webhook URL for SLO breach alerts. Pass via TF_VAR_alert_webhook_url env var — never commit it. Empty disables alerting (breaches are still logged)."
  type        = string
  sensitive   = true
  default     = ""
}

variable "slo_max_p95_latency_seconds" {
  description = "SLO threshold: p95 root-run latency, in seconds."
  type        = number
  default     = 15
}

variable "slo_max_mean_cost_usd" {
  description = "SLO threshold: mean Bedrock cost per query, in USD."
  type        = number
  default     = 0.02
}

variable "slo_min_mean_quality_score" {
  description = "SLO threshold: mean LLM-judge quality score (from online_eval feedback)."
  type        = number
  default     = 0.7
}

variable "slo_monitor_period_seconds" {
  description = "Must be kept in sync with slo_monitor_schedule (Terraform can't parse an arbitrary rate()/cron() expression into seconds automatically). Used as the evaluation window for the self-monitoring alarms in terraform/self_monitoring.tf: the Errors alarm uses this period, and the missed-invocation dead-man's-switch alarm uses 2x it."
  type        = number
  default     = 900 # 15 minutes, matching the default slo_monitor_schedule
}
