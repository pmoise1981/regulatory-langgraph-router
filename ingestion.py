# Loads real documents from S3 into OpenSearch, plus hybrid search pipeline setup.
# Run standalone (python ingestion.py) — the runtime graph never imports ingest() itself,
# only get_client(), since loading data and answering questions are different lifecycles.
#
# Expects documents in S3 under one prefix per domain, e.g.:
#   s3://<bucket>/aml/some-report.pdf
#   s3://<bucket>/bsa/another-filing.pdf
#   s3://<bucket>/ofac/...
#   s3://<bucket>/kyc/...
# The prefix (lowercased) becomes the "domain" metadata tag used for per-domain filtering.
import tempfile

import boto3
from opensearchpy import OpenSearch, RequestsHttpConnection
from langchain_core.documents import Document
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import OpenSearchVectorSearch
from config import (
    embeddings,
    OPENSEARCH_URL,
    OPENSEARCH_INDEX,
    OPENSEARCH_PIPELINE,
    AWS_REGION,
    get_opensearch_auth,
    DOCUMENTS_BUCKET,
    DOMAINS,
)

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150


def get_client() -> OpenSearch:
    return OpenSearch(
        hosts=[OPENSEARCH_URL],
        http_auth=get_opensearch_auth(AWS_REGION),
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
    )


def load_documents_from_s3() -> list[Document]:
    """Downloads every PDF under each domain prefix in the bucket, splits into chunks,
    and tags each chunk with its domain (from the S3 prefix) and source (the S3 key)."""
    if not DOCUMENTS_BUCKET:
        raise RuntimeError(
            "DOCUMENTS_BUCKET is not set. Run: "
            "export DOCUMENTS_BUCKET=$(terraform -chdir=terraform output -raw documents_bucket)"
        )

    s3 = boto3.client("s3", region_name=AWS_REGION)
    splitter = RecursiveCharacterTextSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP)
    all_chunks: list[Document] = []

    for domain in DOMAINS + ["GENERAL"]:
        prefix = f"{domain.lower()}/"
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=DOCUMENTS_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.lower().endswith(".pdf"):
                    continue  # skip folder markers / non-PDF files

                with tempfile.NamedTemporaryFile(suffix=".pdf") as tmp:
                    s3.download_file(DOCUMENTS_BUCKET, key, tmp.name)
                    pages = PyPDFLoader(tmp.name).load()

                chunks = splitter.split_documents(pages)
                for i, chunk in enumerate(chunks):
                    chunk.metadata = {"domain": domain, "source": key, "chunk": i}
                    all_chunks.append(chunk)

                print(f"Loaded {len(chunks)} chunks from s3://{DOCUMENTS_BUCKET}/{key} (domain={domain})")

    return all_chunks


def ensure_hybrid_pipeline():
    """Creates the search pipeline that combines BM25 + kNN scores in one OpenSearch query.
    normalization-processor rescales both score types before combining, since BM25 and
    cosine-similarity scores aren't on the same scale."""
    client = get_client()
    pipeline_body = {
        "description": "Hybrid BM25 + kNN search",
        "phase_results_processors": [
            {
                "normalization-processor": {
                    "normalization": {"technique": "min_max"},
                    "combination": {"technique": "arithmetic_mean", "parameters": {"weights": [0.5, 0.5]}},
                }
            }
        ],
    }
    client.transport.perform_request("PUT", f"/_search/pipeline/{OPENSEARCH_PIPELINE}", body=pipeline_body)


def ingest():
    """Pulls PDFs from S3 (per-domain prefixes), chunks them, pushes them into OpenSearch,
    and ensures the hybrid search pipeline exists. Run from CLI or a scheduled job —
    never from the request path of the running app."""
    documents = load_documents_from_s3()

    if not documents:
        prefixes = [d.lower() + "/" for d in DOMAINS]
        print(
            f"No PDFs found under any of {prefixes} in s3://{DOCUMENTS_BUCKET}. "
            "Upload some PDFs to those prefixes and rerun."
        )
        return

    OpenSearchVectorSearch.from_documents(
        documents=documents,
        embedding=embeddings,
        opensearch_url=OPENSEARCH_URL,
        index_name=OPENSEARCH_INDEX,
        http_auth=get_opensearch_auth(AWS_REGION),
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        bulk_size=len(documents) + 100,  # must exceed doc count, or OpenSearch rejects the bulk upload
        engine="faiss",  # NMSLIB (the default) does not support filtered k-NN queries; Faiss does
        space_type="l2",
    )
    ensure_hybrid_pipeline()
    print(f"Ingested {len(documents)} chunks into '{OPENSEARCH_INDEX}' and ensured hybrid pipeline.")


if __name__ == "__main__":
    ingest()
