# Hybrid retrieval via OpenSearch's native BM25 + kNN search pipeline.
# Assumes ingestion.py has already been run (documents + pipeline both exist).
# This module only reads from OpenSearch — it never writes/ingests.
from typing import Optional
from langchain_core.documents import Document
from config import embeddings, OPENSEARCH_INDEX, OPENSEARCH_PIPELINE, DOMAINS
from ingestion import get_client


def hybrid_search(query: str, domain: Optional[str], k: int = 4) -> list[Document]:
    """Hybrid BM25 + kNN retrieval using OpenSearch's native hybrid query.
    'hybrid' must always be the top-level query (OpenSearch rejects it wrapped
    in a bool). This domain runs OpenSearch 2.11, which predates the 3.0+
    top-level hybrid.filter shortcut, so domain filtering is applied inside
    each subquery individually instead: a bool/filter around the match clause,
    and a filter key inside the knn clause."""
    client = get_client()
    query_vector = embeddings.embed_query(query)

    if domain:
        domain_filter = {"term": {"metadata.domain.keyword": domain}}
        match_subquery = {"bool": {"must": {"match": {"text": query}}, "filter": domain_filter}}
        knn_subquery = {
            "knn": {"vector_field": {"vector": query_vector, "k": k, "filter": domain_filter}}
        }
    else:
        match_subquery = {"match": {"text": query}}
        knn_subquery = {"knn": {"vector_field": {"vector": query_vector, "k": k}}}

    search_body = {
        "size": k,
        "query": {"hybrid": {"queries": [match_subquery, knn_subquery]}},
    }

    response = client.transport.perform_request(
        "GET",
        f"/{OPENSEARCH_INDEX}/_search?search_pipeline={OPENSEARCH_PIPELINE}",
        body=search_body,
    )

    return [
        Document(
            page_content=hit["_source"]["text"],
            metadata=hit["_source"].get("metadata", {}),
        )
        for hit in response["hits"]["hits"]
    ]


def make_hybrid_retriever(domain: Optional[str]):
    """Returns an object matching the .invoke(query) interface the graph nodes expect."""

    class _Retriever:
        def invoke(self, query: str) -> list[Document]:
            return hybrid_search(query, domain)

    return _Retriever()


hybrid_retrievers = {domain: make_hybrid_retriever(domain) for domain in DOMAINS}
hybrid_retrievers["GENERAL"] = make_hybrid_retriever(None)
