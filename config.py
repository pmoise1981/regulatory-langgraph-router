# Central settings, shared model clients, and auth helpers — everything else imports from here.
import os
import boto3
from botocore.config import Config
from requests_aws4auth import AWS4Auth
from langchain_aws import ChatBedrockConverse, BedrockEmbeddings

# --- Domain routing config ---
DOMAINS = ["AML", "BSA", "OFAC", "KYC"]
CONFIDENCE_THRESHOLD = 0.6  # below this, force GENERAL fallback rather than trust a shaky classification

# --- SLO thresholds for evals/slo_monitor.py ---
# Illustrative demo defaults, not derived from a real SLA — same caveat as
# CONFIDENCE_THRESHOLD above. Override via env var (Terraform sets these on
# the slo_monitor Lambda; see terraform/slo_monitor.tf) rather than editing
# here, so the threshold is visible in one place without a code change.
SLO_MAX_P95_LATENCY_SECONDS = float(os.environ.get("SLO_MAX_P95_LATENCY_SECONDS", "15"))
SLO_MAX_MEAN_COST_USD = float(os.environ.get("SLO_MAX_MEAN_COST_USD", "0.02"))
SLO_MIN_MEAN_QUALITY_SCORE = float(os.environ.get("SLO_MIN_MEAN_QUALITY_SCORE", "0.7"))
# Slack-compatible incoming webhook URL (a generic JSON POST works for any
# webhook receiver that logs the body) — never commit a real one; empty
# disables alerting and just logs breaches instead.
ALERT_WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL", "")

# Shared between evals/online_eval.py (which writes this feedback) and
# evals/slo_monitor.py (which only reads it) — lives here, not in
# online_eval.py, so slo_monitor.py doesn't need to import a module that
# constructs a Bedrock client just to get one constant.
JUDGE_FEEDBACK_KEY = "online_llm_judge_quality"


def is_application_trace(run) -> bool:
    """True only for a root run that's a real graph.invoke() production
    trace (has a non-empty "query" in its inputs, matching GraphState's
    shape). evals/online_eval.py and evals/slo_monitor.py both scan
    is_root=True runs in the LangSmith project via list_runs(), which
    surfaces *any* root-level run logged there -- including this project's
    own instrumentation, like slo_monitor.py's run_self_check "tool" run.
    Without this filter, a self-check run gets judged as an empty/garbage
    production answer (online_eval.py) or counted into latency/cost stats
    as if it were a real query (slo_monitor.py) -- a real bug caught by
    actually running this against live LangSmith data, not something a
    unit test with fake Run objects would have surfaced."""
    inputs = run.inputs or {}
    return bool(inputs.get("query"))

# --- AWS / infra config ---
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
OPENSEARCH_URL = os.environ.get("OPENSEARCH_URL", "https://your-opensearch-domain.us-east-1.es.amazonaws.com")
OPENSEARCH_INDEX = "regulatory_multi_domain"
OPENSEARCH_PIPELINE = "hybrid-search-pipeline"
DYNAMODB_TABLE_NAME = "regulatory-langgraph-checkpoints"
# Set by Terraform output after `terraform apply`; export it before running ingestion.py:
#   export DOCUMENTS_BUCKET=$(terraform -chdir=terraform output -raw documents_bucket)
DOCUMENTS_BUCKET = os.environ.get("DOCUMENTS_BUCKET", "")

# LangSmith tracing — LangChain/LangGraph read these env vars automatically, no code needed
# beyond making sure they're set. Locally: export them in your shell before running main.py.
# On Lambda: set via terraform/lambda.tf's environment block (sourced from a Terraform
# variable, never hardcoded here or committed to the repo).
os.environ.setdefault("LANGCHAIN_TRACING_V2", os.environ.get("LANGCHAIN_TRACING_V2", "false"))

# --- Retry config: absorbs Bedrock's ~15-min auto-subscribe window on first model invocation ---
BEDROCK_RETRY_CONFIG = Config(retries={"max_attempts": 8, "mode": "adaptive"})

# --- Shared model clients ---
llm = ChatBedrockConverse(
    model="us.anthropic.claude-sonnet-4-6",
    region_name=AWS_REGION,
    temperature=0,
    config=BEDROCK_RETRY_CONFIG,
)

# Cheaper/faster model for simple classification — the classifier only picks one
# of five labels, so a full Sonnet call is unnecessary cost. Haiku 4.5 handles this
# task just as reliably at a fraction of the token cost and latency.
classifier_llm_base = ChatBedrockConverse(
    model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
    region_name=AWS_REGION,
    temperature=0,
    config=BEDROCK_RETRY_CONFIG,
)

embeddings = BedrockEmbeddings(model_id="amazon.titan-embed-text-v2:0", region_name=AWS_REGION)


# --- IAM-based OpenSearch auth (no manual username/password) ---
def get_opensearch_auth(region: str = AWS_REGION) -> AWS4Auth:
    """SigV4-signs OpenSearch requests using whatever AWS credentials are already
    active (CLI profile locally, the Lambda execution role in production)."""
    credentials = boto3.Session().get_credentials()
    return AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        region,
        "es",
        session_token=credentials.token,
    )
