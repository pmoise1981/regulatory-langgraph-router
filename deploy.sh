#!/usr/bin/env bash
# Full deploy sequence. Requires: AWS CLI configured, Docker running, Terraform installed.
set -euo pipefail

cd "$(dirname "$0")"

echo "== 1/6: Provisioning ECR repo + S3 documents bucket (needed before image push / doc upload) =="
cd terraform
terraform init
terraform apply -target=aws_ecr_repository.app -target=aws_s3_bucket.documents -auto-approve
ECR_URL=$(terraform output -raw ecr_repository_url)
DOCUMENTS_BUCKET=$(terraform output -raw documents_bucket)
cd ..

echo ""
echo "== 2/6: Upload your source PDFs now =="
echo "The bucket is ready: s3://${DOCUMENTS_BUCKET}"
echo "Upload PDFs under one prefix per domain, e.g.:"
echo "  aws s3 cp your-aml-doc.pdf s3://${DOCUMENTS_BUCKET}/aml/"
echo "  aws s3 cp your-bsa-doc.pdf s3://${DOCUMENTS_BUCKET}/bsa/"
echo "  aws s3 cp your-ofac-doc.pdf s3://${DOCUMENTS_BUCKET}/ofac/"
echo "  aws s3 cp your-kyc-doc.pdf s3://${DOCUMENTS_BUCKET}/kyc/"
read -p "Press Enter once your PDFs are uploaded to continue... "

echo "== 3/6: Building and pushing container image =="
aws ecr get-login-password --region "${AWS_REGION:-us-east-1}" | docker login --username AWS --password-stdin "${ECR_URL%%/*}"
docker build -t regulatory-langgraph-router .
docker tag regulatory-langgraph-router:latest "${ECR_URL}:latest"
docker push "${ECR_URL}:latest"

echo "== 4/6: Applying remaining infra (OpenSearch, IAM, Lambda, function URL) =="
cd terraform
terraform apply -auto-approve
OPENSEARCH_ENDPOINT=$(terraform output -raw opensearch_endpoint)
LAMBDA_URL=$(terraform output -raw lambda_url)
cd ..

echo "== 5/6: Running ingestion — loads PDFs from S3 into the live OpenSearch domain =="
export OPENSEARCH_URL="https://${OPENSEARCH_ENDPOINT}"
export DOCUMENTS_BUCKET
python ingestion.py

echo "== 6/6: Done =="
echo "Lambda URL: ${LAMBDA_URL}"
echo "Test with:"
echo "curl -X POST ${LAMBDA_URL} --aws-sigv4 \"aws:amz:${AWS_REGION:-us-east-1}:lambda\" --user \"\$(aws configure get aws_access_key_id):\$(aws configure get aws_secret_access_key)\" -d '{\"query\": \"What are the SAR filing deadlines under AML requirements?\"}'"
