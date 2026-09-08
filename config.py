# Central settings, shared model clients, and auth helpers — everything else imports from here.
import os
import boto3
from botocore.config import Config
from requests_aws4auth import AWS4Auth
from langchain_aws import ChatBedrockConverse, BedrockEmbeddings

# --- Domain routing config ---
DOMAINS = ["AML", "BSA", "OFAC", "KYC"]
CONFIDENCE_THRESHOLD = 0.6  # below this, force GENERAL fallback rather than trust a shaky classification

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
