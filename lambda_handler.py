# Entry point for AWS Lambda — wraps graph.py's invocation for API Gateway / Lambda Function URL
# proxy integration. Deployed as a container image (see Dockerfile).
import base64
import json
import uuid
from graph import graph


def handler(event, context):
    raw_body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw_body = base64.b64decode(raw_body).decode("utf-8")

    try:
        body = json.loads(raw_body)
    except json.JSONDecodeError:
        return {"statusCode": 400, "body": json.dumps({"error": f"Invalid JSON body: {raw_body!r}"})}

    query = body.get("query")

    if not query:
        return {"statusCode": 400, "body": json.dumps({"error": "Missing 'query' in request body"})}

    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    initial_state = {"query": query, "domain": "", "confidence": 0.0, "documents": [], "answer": "", "audit_log": []}
    result = graph.invoke(initial_state, config=config)

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(
            {
                "domain": result["domain"],
                "answer": result["answer"],
                "audit_log": result["audit_log"],
            }
        ),
    }
