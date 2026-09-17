"""Structured-output validation and retry/fallback for LLM calls in the graph
(piece #3 of the AI reliability layer).

A malformed LLM response can break downstream state two ways: it can crash
the whole request (an unhandled ValidationError propagating out of
graph.invoke), or worse, it can pass validation but be semantically wrong in
a way nothing catches (e.g. a confidence score outside 0.0-1.0 silently
forcing every query down the wrong routing branch). This module addresses
both: `invoke_structured_with_fallback` retries a structured-output call a
bounded number of times and falls back to a safe, explicitly-logged default
rather than raising; the Pydantic schemas it validates against (graph.py's
DomainClassification) should carry real constraints (ge/le, Literal), not
just descriptions, so out-of-range values fail loudly instead of drifting
through silently.
"""

import logging
from typing import TypeVar

from botocore.exceptions import ClientError
from pydantic import ValidationError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _is_bedrock_throttling(exc: BaseException) -> bool:
    """Matches Bedrock's rate-limit error, whether it surfaces as a raw
    botocore ClientError or gets wrapped by langchain-aws in its own
    exception type (in which case only the message text survives)."""
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        return code in {"ThrottlingException", "TooManyRequestsException"}
    return "ThrottlingException" in str(exc) or "Too Many Requests" in str(exc)


# Shared Bedrock throttling retry/backoff, used anywhere in this repo that
# calls Bedrock directly (graph nodes, the eval scripts) on top of
# config.py's own adaptive retry config: that absorbs single-call throttling,
# this absorbs a batch/loop still exceeding throughput despite it.
bedrock_retry = retry(
    retry=retry_if_exception(_is_bedrock_throttling),
    wait=wait_exponential(multiplier=2, min=2, max=60),
    stop=stop_after_attempt(6),
    reraise=True,
)


def _is_retryable_structured_output_error(exc: BaseException) -> bool:
    """Retries on a malformed structured response (the model's tool-call
    arguments didn't validate against the Pydantic schema — surfaces as
    ValidationError or ValueError from PydanticToolsParser) or Bedrock
    throttling. Does not retry on anything else (e.g. auth errors), since
    those won't be fixed by trying the same call again."""
    if isinstance(exc, (ValidationError, ValueError)):
        return True
    return _is_bedrock_throttling(exc)


structured_output_retry = retry(
    retry=retry_if_exception(_is_retryable_structured_output_error),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    stop=stop_after_attempt(3),
    reraise=True,
)


def invoke_structured_with_fallback(chain, inputs: dict, fallback: T, step_name: str) -> tuple[T, dict]:
    """Invokes a with_structured_output chain, retrying on a malformed
    response or Bedrock throttling. If it still fails after retries, returns
    `fallback` instead of raising, so one bad model response degrades a
    single node's output rather than crashing the whole graph invocation.
    The second return value is an audit_log-ready dict that always states
    whether a fallback was used — never a silent substitution.
    """

    @structured_output_retry
    def _call():
        return chain.invoke(inputs)

    try:
        result = _call()
        return result, {"step": step_name, "status": "ok"}
    except Exception as e:
        logger.warning("'%s' failed after retries, using fallback: %s", step_name, e)
        return fallback, {"step": step_name, "status": "fallback", "error": str(e)}


def extract_answer_text(content) -> str:
    """Coerces a chat model's .content into a plain, non-empty string.
    Newer LangChain/Bedrock responses can return content as a list of
    content blocks (e.g. reasoning + text blocks) instead of a raw string;
    without this, that shape would flow straight into state["answer"] and
    out through the Lambda response body unchanged."""
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        text_parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        joined = "\n".join(part for part in text_parts if part).strip()
        if joined:
            return joined
    logger.warning("Model returned an empty or non-text answer shape: %r", content)
    return "Unable to generate a response for this question — please retry."
