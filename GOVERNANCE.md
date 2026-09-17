# Model Risk Governance & Validation Summary

**System:** Regulatory LangGraph Router
**Version:** 1.0 (portfolio/demonstration system)
**Environment:** AWS (Lambda, OpenSearch, DynamoDB, S3), deployed via Terraform

## 1. Overview

This document summarizes the validation methodology applied to a domain-routed hybrid RAG system covering AML/BSA/OFAC/KYC regulatory Q&A. The intent is to demonstrate model risk validation practices — evidence-based failure diagnosis, honest reporting of negative results, and verified remediation — applied to a GenAI/RAG system rather than a traditional statistical model.

**This is a portfolio project, not a certified production system.** Section 6 states explicitly what is and isn't implemented, so the scope of these claims is unambiguous.

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

## 5. AI Reliability Layer (post-deployment monitoring and guardrails)

Sections 3 and 4 cover pre-deployment validation: a golden-set eval and a measured cost change, both point-in-time. This section covers what runs *after* deployment, on an ongoing basis:

| Concern | Mechanism | Risk it addresses |
|---|---|---|
| Golden-set regression | `evals/eval_langsmith.py`, scheduled or run on demand | Same four RAGAS metrics as Section 3, now tracked as versioned LangSmith Experiments instead of one-off local runs, so a regression across code changes is comparable run-to-run |
| Production quality drift | `evals/online_eval.py`, hourly Lambda | The golden set is 10 fixed questions; real traffic isn't. An LLM-as-judge samples live traces and scores faithfulness/relevance, so a quality drop on real queries doesn't have to wait for someone to notice |
| Malformed model output | `guardrails.py` | A structured-output field that validates in principle but not in practice (e.g. a confidence score outside its declared range) previously passed silently; the field constraints are now enforced, with retry/fallback so a failure degrades one node instead of the whole request |
| Latency/cost/quality regression | `evals/slo_monitor.py`, every 15 min | Ties the above together into named thresholds (config.py) with a webhook alert on breach, rather than requiring someone to go look at LangSmith |
| The monitor itself failing silently | `terraform/self_monitoring.tf`, `alarm_forwarder_handler.py`, `run_self_check` in `evals/slo_monitor.py` | An alerting layer nobody watches defeats its own purpose. CloudWatch alarms on the SLO monitor Lambda's own Errors and Invocations metrics (the latter a dead-man's-switch: missing data is treated as the failure, not ignored) route through SNS to a forwarder that posts into the same webhook channel as an SLO breach — this is the actual paging trigger, since LangSmith has no visibility into a Lambda that makes no LLM calls. Each self-check invocation is also logged as an explicit LangSmith run (pass/fail, error message on failure), so the same failure is visible from the LangSmith project too, not only from AWS/Slack |
| The judge itself being wrong | `evals/judge_calibration.py`, `evals/judge_calibration_set.json`, run on demand | An LLM-as-judge scoring production quality (row above) is itself a probabilistic component with known failure modes -- it can be fooled by a fluent but fabricated added claim, or inconsistent about scoring a true-but-off-topic answer. This checks the same judge against 6 hand-labeled, deliberately clear-cut cases (a correct paraphrase, two fabricated-claim cases, an off-topic-but-true case, and one genuinely ambiguous faithful-but-incomplete case) and reports agreement, logged as its own LangSmith run. Not a statistically rigorous calibration set yet -- a starting point to expand with real judge disagreements once this system has live traffic |

**Status, plainly**: deployed to real AWS and exercised against real Bedrock/OpenSearch/LangSmith traffic. A real query ("What is a Politically Exposed Person (PEP)?") produced a correct, cited answer in 6.39s for $0.0117, which `online_eval.py` then judged for real and scored 0.97 with reasoning that correctly traced every claim back to the retrieved FFIEC text. Getting there surfaced four real bugs no unit test caught: a Docker container-image manifest format Lambda rejects outright, an undocumented 100-item cap on LangSmith's `/runs/query` that was silently failing the already-deployed scheduled Lambdas on every invocation, production trace outputs being lost to AWS Lambda freezing before LangChain's async trace flush completed, and the SLO monitor's own health-check logging contaminating the very production metrics it and the online evaluator were supposed to measure. All four were root-caused against the real system (not guessed at) and fixed. The default thresholds in `config.py` remain a single real data point, not a calibrated baseline -- tune them against sustained traffic before trusting them operationally.

## 6. Scope and Limitations (explicitly, to avoid overclaiming)

This system does **not** currently implement:
- Prompt-injection detection or filtering
- Deterministic transaction-limit / authorization guardrails
- Cryptographic provenance (hashing, signed audit trails)
- Automated/scheduled re-ingestion (corpus updates are manual)
- Formal alignment or certification against NIST AI RMF or SR 11-7 — this system was built informed by model-risk-management principles from banking experience, but no formal gap analysis against either framework has been performed

These are legitimate directions for future work, not implemented features being described as present.

## 7. Key Engineering Takeaways

- **The chunk-count tradeoff**: naively increasing retriever top-k improved recall but degraded precision and relevancy — a real tradeoff requiring targeted tuning (e.g., reranking), not free scaling.
- **Corpus gaps vs. pipeline defects**: two of three initial evaluation failures were caused by missing source documents, not retrieval or generation bugs. Distinguishing these categories before attempting a fix avoided wasted engineering effort on the wrong problem.
- **Verify fixes, don't assume them**: each proposed fix (top-k change, corpus addition) was validated with a second evaluation run rather than treated as resolved based on the diagnosis alone.
- **Packaging is a distinct failure mode from application logic**: `guardrails.py` passed every syntax check and unit test when it was added, but was never added to the Lambda container image's file list — a deployed run would have crashed on import despite the code itself being correct. Caught only by simulating the exact container file layout locally and importing each entry point from it, not by testing the module in isolation.
