# Model Risk Governance & Validation Summary

**System:** Regulatory LangGraph Router
**Version:** 1.0 (portfolio/demonstration system)
**Environment:** AWS (Lambda, OpenSearch, DynamoDB, S3), deployed via Terraform

## 1. Overview

This document summarizes the validation methodology applied to a domain-routed hybrid RAG system covering AML/BSA/OFAC/KYC regulatory Q&A. The intent is to demonstrate model risk validation practices — evidence-based failure diagnosis, honest reporting of negative results, and verified remediation — applied to a GenAI/RAG system rather than a traditional statistical model.

**This is a portfolio project, not a certified production system.** Section 5 states explicitly what is and isn't implemented, so the scope of these claims is unambiguous.

## 2. Data Lineage

| Source Authority | Domain | Ingestion Mechanism | Extraction | Storage / Retrieval |
|---|---|---|---|---|
| FFIEC (Federal Financial Institutions Examination Council) | AML, KYC, BSA | Manual sourcing, uploaded to S3 under domain-prefixed folders (`aml/`, `bsa/`, `kyc/`) | `PyPDFLoader` + `RecursiveCharacterTextSplitter` (1000-char chunks, 150-char overlap) | OpenSearch, hybrid BM25 + k-NN (Faiss engine) |
| OCC (Office of the Comptroller of the Currency) | AML | Same as above | Same as above | Same as above |
| FinCEN | BSA | Same as above | Same as above | Same as above |

Each ingested chunk carries `{domain, source, chunk_index}` metadata, allowing every retrieved passage to be traced back to its originating document. There is no cryptographic hashing or automated web-scraping in the current pipeline — ingestion is a manual, S3-triggered batch process, rerun when source documents are added or changed.

## 3. Evaluation Methodology & Results

Evaluated with RAGAS (Faithfulness, Answer Relevancy, Context Precision, Context Recall) against 10 golden questions, run through the live deployed graph — not a mocked test path.

### Baseline

| Metric | Score |
|---|---|
| Faithfulness | 0.889 |
| Answer Relevancy | 0.654 |
| Context Precision | 0.533 |
| Context Recall | 0.457 |

3 of 10 questions scored 0.0 across relevancy/precision/recall. Each failure was traced individually rather than accepted at face value.

### Root-cause diagnosis

| Question | Root cause |
|---|---|
| CIP requirements | No CIP document existed anywhere in the ingested corpus |
| BSA recordkeeping ($10K CTR) | No document in the corpus covers CTR thresholds |
| AML program elements | Relevant content existed but ranked below the retrieved top-k |

### Tested fix: retriever top-k (4 → 6) — reported as a tradeoff, not a clean win

| Metric | k=4 | k=6 | Delta |
|---|---|---|---|
| Faithfulness | 0.889 | 0.909 | +0.019 |
| Answer Relevancy | 0.654 | 0.612 | −0.042 |
| Context Precision | 0.533 | 0.505 | −0.029 |
| Context Recall | 0.457 | 0.580 | +0.123 |

Increasing k improved recall but reduced precision and relevancy — a real recall/precision tradeoff, not a universal improvement. This result was reported as-is rather than selectively citing the improving metric.

### Verified fix: sourced and ingested the missing FFIEC CIP document

| Metric | Before (no CIP doc) | After (+CIP doc) | Delta |
|---|---|---|---|
| Faithfulness | 0.909 | 0.942 | +0.033 |
| Answer Relevancy | 0.612 | 0.787 | +0.175 |
| Context Precision | 0.505 | 0.601 | +0.096 |
| Context Recall | 0.580 | 0.630 | +0.050 |

The CIP question specifically moved from a complete 0.0/0.0/0.0/0.0 failure to 1.0 / 0.955 / 0.633 / 0.5, confirming the corpus-gap diagnosis. The BSA recordkeeping question remained unresolved (0/0/0) across both evaluations, as expected — ingesting a CIP document does not address an unrelated missing CTR document.

## 4. Cost / Quality Tradeoff

The classification node (a 5-way domain label) originally ran on Claude Sonnet 4.6, the same model used for generation. Measured via LangSmith trace data:

| | Model | Cost/query |
|---|---|---|
| Before | Sonnet 4.6 classifier | $0.0127 |
| After | Haiku 4.5 classifier | $0.0015 |

~8x cost reduction on the classification step. No formal answer-quality regression test was run on this specific change; "no measurable quality loss" refers to the absence of any observed change in downstream generation quality across manual testing, not a statistically controlled comparison.

## 5. Scope and Limitations (explicitly, to avoid overclaiming)

This system does **not** currently implement:
- Prompt-injection detection or filtering
- Deterministic transaction-limit / authorization guardrails
- Cryptographic provenance (hashing, signed audit trails)
- Automated/scheduled re-ingestion (corpus updates are manual)
- Formal alignment or certification against NIST AI RMF or SR 11-7 — this system was built informed by model-risk-management principles from banking experience, but no formal gap analysis against either framework has been performed

These are legitimate directions for future work, not implemented features being described as present.

## 6. Key Engineering Takeaways

- **The chunk-count tradeoff**: naively increasing retriever top-k improved recall but degraded precision and relevancy — a real tradeoff requiring targeted tuning (e.g., reranking), not free scaling.
- **Corpus gaps vs. pipeline defects**: two of three initial evaluation failures were caused by missing source documents, not retrieval or generation bugs. Distinguishing these categories before attempting a fix avoided wasted engineering effort on the wrong problem.
- **Verify fixes, don't assume them**: each proposed fix (top-k change, corpus addition) was validated with a second evaluation run rather than treated as resolved based on the diagnosis alone.
