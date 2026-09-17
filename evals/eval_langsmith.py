"""LangSmith-integrated evaluation-as-monitoring for the regulatory router graph.

Piece #1 of the AI reliability layer: this script is the *offline, golden-set*
half of evaluation-as-monitoring. It differs from the repo's existing
`eval.py` (which runs RAGAS's own batch `evaluate()` locally, entirely outside
LangSmith) in three ways:

  1. The golden Q&A set lives in `evals/golden_dataset.json`, not hardcoded in
     a .py file, so it can be edited/extended without touching code.
  2. That JSON is uploaded as a LangSmith Dataset, so every run is versioned
     and browsable in the LangSmith UI, not just a local CSV.
  3. Scoring runs through LangSmith's own `evaluate()`, which records the
     result as a LangSmith Experiment linked to the dataset — this is the
     hook piece #2 (online evaluators) will build on next, so both the
     offline golden-set runs and live production traces show up in the same
     project.

RAGAS is still the scoring engine (same four metrics, same judge/embedding
models as `eval.py`) — it's just invoked per-example as LangSmith evaluators
instead of as one local batch call.

Usage:
    python evals/eval_langsmith.py

Requires the standard LangSmith env vars to be set (LANGSMITH_API_KEY at
minimum; LANGSMITH_PROJECT optional) in addition to whatever AWS credentials
config.py's Bedrock clients need.
"""

import json
import sys
import uuid
from pathlib import Path

from langsmith import Client
from langsmith.evaluation import evaluate
from langsmith.schemas import Example, Run
from ragas import SingleTurnSample
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import (
    AnswerRelevancy,
    Faithfulness,
    LLMContextPrecisionWithReference,
    LLMContextRecall,
)

# graph.py / config.py / guardrails.py live at the repo root, one level up from evals/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import embeddings, llm  # noqa: E402
from graph import graph  # noqa: E402
from guardrails import bedrock_retry  # noqa: E402

DATASET_PATH = Path(__file__).resolve().parent / "golden_dataset.json"
LANGSMITH_DATASET_NAME = "regulatory-router-golden-qa"
# Namespaces the golden set's own "id" fields (q01, q02, ...) into stable
# LangSmith example ids, so re-running this script after editing
# golden_dataset.json updates the changed examples in place (create_examples
# upserts by id) instead of silently testing against a stale remote copy.
_EXAMPLE_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, f"langsmith-dataset/{LANGSMITH_DATASET_NAME}")


def _stable_example_id(item_id: str) -> str:
    return str(uuid.uuid5(_EXAMPLE_ID_NAMESPACE, item_id))


def load_golden_dataset() -> list[dict]:
    """Loads the golden Q&A set from JSON. Fails loudly rather than silently
    running against an empty/malformed set — a missing file here should stop
    the eval, not produce a falsely-clean zero-example "pass"."""
    if not DATASET_PATH.exists():
        raise FileNotFoundError(
            f"Golden dataset not found at {DATASET_PATH}. "
            "This file is maintained by hand — it is not generated."
        )
    with open(DATASET_PATH) as f:
        golden_set = json.load(f)
    if not golden_set:
        raise ValueError(f"{DATASET_PATH} is empty — nothing to evaluate.")
    for item in golden_set:
        if "question" not in item or "reference" not in item:
            raise ValueError(f"Golden dataset entry missing question/reference: {item}")
    return golden_set


@bedrock_retry
def run_graph(question: str) -> dict:
    """Runs one question through the actual compiled graph — same code path
    main.py and production traffic use — and returns the answer plus the
    real retrieved context."""
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


def target(inputs: dict) -> dict:
    """The function LangSmith's evaluate() calls for every dataset example."""
    return run_graph(inputs["question"])


# Same judge (Sonnet) and embeddings (Titan) config.py already configures for
# the rest of the app — no separate eval-only model config.
evaluator_llm = LangchainLLMWrapper(llm)
evaluator_embeddings = LangchainEmbeddingsWrapper(embeddings)

_faithfulness = Faithfulness(llm=evaluator_llm)
_answer_relevancy = AnswerRelevancy(llm=evaluator_llm, embeddings=evaluator_embeddings)
_context_precision = LLMContextPrecisionWithReference(llm=evaluator_llm)
_context_recall = LLMContextRecall(llm=evaluator_llm)


def _build_sample(run: Run, example: Example) -> SingleTurnSample:
    outputs = run.outputs or {}
    return SingleTurnSample(
        user_input=example.inputs["question"],
        response=outputs.get("answer", ""),
        retrieved_contexts=outputs.get("contexts", []),
        reference=example.outputs["reference"],
    )


def make_ragas_evaluator(metric, key: str):
    """Wraps one RAGAS metric as a LangSmith evaluator function (run, example)
    -> {"key": ..., "score": ...}, which is the shape evaluate() expects."""

    @bedrock_retry
    def _evaluator(run: Run, example: Example) -> dict:
        sample = _build_sample(run, example)
        return {"key": key, "score": metric.single_turn_score(sample)}

    _evaluator.__name__ = f"{key}_evaluator"
    return _evaluator


evaluators = [
    make_ragas_evaluator(_faithfulness, "faithfulness"),
    make_ragas_evaluator(_answer_relevancy, "answer_relevancy"),
    make_ragas_evaluator(_context_precision, "context_precision"),
    make_ragas_evaluator(_context_recall, "context_recall"),
]


def upload_dataset(client: Client, golden_set: list[dict]) -> str:
    """Creates the LangSmith dataset if it doesn't exist, then upserts every
    example by a stable id derived from the JSON's own "id" field.
    create_examples upserts by id rather than only inserting, so re-running
    this after editing golden_dataset.json updates the changed examples in
    place instead of silently testing against a stale remote copy.
    Removing a question from the JSON does not delete its remote example --
    that's a deliberate simplification for a 10-question set, not handled
    here."""
    if client.has_dataset(dataset_name=LANGSMITH_DATASET_NAME):
        print(f"Reusing existing LangSmith dataset '{LANGSMITH_DATASET_NAME}'.")
    else:
        print(f"Creating LangSmith dataset '{LANGSMITH_DATASET_NAME}'...")
        client.create_dataset(
            dataset_name=LANGSMITH_DATASET_NAME,
            description="Golden Q&A set for the regulatory router graph (AML/BSA/OFAC/KYC).",
        )

    client.create_examples(
        dataset_name=LANGSMITH_DATASET_NAME,
        examples=[
            {
                "id": _stable_example_id(item["id"]),
                "inputs": {"question": item["question"]},
                "outputs": {"reference": item["reference"]},
                "metadata": {"id": item["id"]},
            }
            for item in golden_set
        ],
    )
    return LANGSMITH_DATASET_NAME


def main():
    golden_set = load_golden_dataset()
    client = Client()  # reads LANGSMITH_API_KEY / LANGSMITH_ENDPOINT from env
    dataset_name = upload_dataset(client, golden_set)

    print(f"\nRunning LangSmith evaluate() against '{dataset_name}'...")
    results = evaluate(
        target,
        data=dataset_name,
        evaluators=evaluators,
        experiment_prefix="regulatory-router-ragas",
        # Kept low deliberately: RAGAS's own judge/embedding calls plus the
        # graph's classifier + generation calls can otherwise fire enough
        # concurrent Bedrock requests to throttle even with retry/backoff.
        max_concurrency=2,
    )

    print(f"\nExperiment: {results.experiment_name}")
    print(f"View in LangSmith: {results.url}")

    df = results.to_pandas()
    out_path = Path(__file__).resolve().parent / "eval_results.csv"
    df.to_csv(out_path, index=False)
    print(f"\nPer-example results saved to {out_path}")
    score_columns = [c for c in df.columns if c.startswith("feedback.")]
    if score_columns:
        print("\n=== Mean scores ===")
        print(df[score_columns].mean())


if __name__ == "__main__":
    main()
