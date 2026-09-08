# Regulatory LangGraph Router

Domain-routed RAG on AWS: a LangGraph app that classifies a regulatory question
(AML / BSA / OFAC / KYC / GENERAL), retrieves from the matching domain's corpus
using OpenSearch's native hybrid BM25+kNN search, and generates a cited answer
via Bedrock (Claude, Converse API). State is checkpointed to DynamoDB.

## Structure

```
config.py          # settings, model clients, IAM-based OpenSearch auth
ingestion.py        # standalone: loads docs into OpenSearch, sets up hybrid search pipeline
retriever.py         # runtime: hybrid retrieval per domain, reads from OpenSearch only
checkpointer.py       # DynamoDB checkpoint persistence, self-provisions the table
graph.py                # LangGraph: state, classifier node, retrieval nodes, generation node, routing
main.py                    # local CLI entry point
lambda_handler.py             # AWS Lambda entry point (same graph, API Gateway-style event)
Dockerfile                       # Lambda container image build
requirements.txt                    # Python dependencies
deploy.sh                              # full deploy sequence
terraform/                                # infra: OpenSearch domain, IAM, ECR, Lambda, function URL
```

## Local run (no AWS deploy)

Requires an already-provisioned OpenSearch domain and AWS credentials in your
environment (`aws configure`, or an assumed role).

```bash
pip install -r requirements.txt
export OPENSEARCH_URL="https://your-domain.us-east-1.es.amazonaws.com"
python ingestion.py   # one-time: load sample docs + set up hybrid pipeline
python main.py         # ask the example query
```

## Full AWS deploy

Requires: AWS CLI configured with credentials that can create IAM roles,
OpenSearch domains, ECR repos, DynamoDB tables, and Lambda functions;
Docker running; Terraform installed.

```bash
./deploy.sh
```

This runs, in order:
1. Provisions the ECR repo (needed before an image can be pushed to it)
2. Builds and pushes the Lambda container image
3. Applies the rest of the infra: OpenSearch domain (k-NN enabled), IAM role/policy,
   Lambda function, and a SigV4-authenticated function URL
4. Runs `ingestion.py` against the now-live OpenSearch domain
5. Prints the Lambda URL and a ready-to-run `curl` test command

## What's not automated

- The OpenSearch domain takes several minutes to become active after `terraform apply` —
  if ingestion fails immediately after infra creation, wait and retry.
- Bedrock model access for third-party models (e.g. Claude) auto-subscribes on first
  invocation, which can take up to ~15 minutes; `config.py`'s retry config absorbs this,
  but the very first request may still be slow.
- Swap the `sample_docs` in `ingestion.py` for your real document ingestion pipeline
  (PDF loaders, chunking) before using this for anything beyond the demo corpus.
