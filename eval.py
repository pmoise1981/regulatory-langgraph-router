# RAGAS evaluation harness — runs the golden question set through the actual
# LangGraph app (same code path as main.py), collects real retrieved context
# and generated answers, and scores them with industry-standard RAG metrics:
#
#   Faithfulness       - is the answer actually supported by retrieved context?
#   Answer Relevancy   - does the answer address the question that was asked?
#   Context Precision  - are the retrieved chunks actually relevant?
#   Context Recall     - did retrieval surface everything needed to answer well?
#
# Uses the same Bedrock models already configured in config.py as the judge
# (Sonnet 4.6) and embeddings (Titan) for scoring — no separate eval-only
# credentials or providers needed.

import uuid
import pandas as pd

from ragas import SingleTurnSample, EvaluationDataset, evaluate
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.metrics import Faithfulness, AnswerRelevancy, LLMContextPrecisionWithReference, LLMContextRecall
from ragas.run_config import RunConfig

from graph import graph
from config import llm, embeddings
from eval_dataset import EVAL_SET


def run_question(question: str) -> dict:
    """Runs one question through the actual graph and returns the answer
    plus the real retrieved context — the same code path production traffic uses."""
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    initial_state = {
        "query": question,
        "domain": "",
        "confidence": 0.0,
        "documents": [],
        "answer": "",
        "audit_log": [],
    }
    result = graph.invoke(initial_state, config=config)
    return {
        "answer": result["answer"],
        "contexts": [doc.page_content for doc in result["documents"]],
        "domain": result["domain"],
    }


def build_eval_dataset() -> EvaluationDataset:
    """Runs every golden question through the graph and packages the results
    into RAGAS's expected format for scoring."""
    samples = []
    for item in EVAL_SET:
        print(f"Running: {item['question']}")
        run_result = run_question(item["question"])
        samples.append(
            SingleTurnSample(
                user_input=item["question"],
                retrieved_contexts=run_result["contexts"],
                response=run_result["answer"],
                reference=item["reference"],
            )
        )
        print(f"  -> routed to {run_result['domain']}, {len(run_result['contexts'])} chunks retrieved")
    return EvaluationDataset(samples=samples)


def main():
    eval_dataset = build_eval_dataset()

    # Wrap your existing Bedrock LLM/embeddings so RAGAS can use them as the judge.
    evaluator_llm = LangchainLLMWrapper(llm)
    evaluator_embeddings = LangchainEmbeddingsWrapper(embeddings)

    print("\nRunning RAGAS evaluation...")
    results = evaluate(
        dataset=eval_dataset,
        metrics=[
            Faithfulness(),
            AnswerRelevancy(),
            LLMContextPrecisionWithReference(),
            LLMContextRecall(),
        ],
        llm=evaluator_llm,
        embeddings=evaluator_embeddings,
        # Lower concurrency (default fires many jobs at once, overwhelming Bedrock
        # and causing timeouts) and a longer per-job timeout to absorb Bedrock latency.
        run_config=RunConfig(timeout=600, max_workers=2),
    )

    print("\n=== Aggregate scores ===")
    print(results)

    # Per-question breakdown, saved for review — averages alone hide which
    # specific questions are underperforming.
    df = results.to_pandas()
    df.to_csv("eval_results.csv", index=False)
    print("\nPer-question results saved to eval_results.csv")
    print(df[["user_input", "faithfulness", "answer_relevancy", "llm_context_precision_with_reference", "context_recall"]])


if __name__ == "__main__":
    main()
