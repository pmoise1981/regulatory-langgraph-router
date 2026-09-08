# Local entry point — run this to ask the graph a question from the command line.
# For AWS deployment, lambda_handler.py is the entry point instead; both call the same graph.
import uuid
from graph import graph


def ask(query: str) -> dict:
    thread_id = str(uuid.uuid4())  # unique session id — DynamoDB partitions checkpoints by this
    config = {"configurable": {"thread_id": thread_id}}
    initial_state = {"query": query, "domain": "", "confidence": 0.0, "documents": [], "answer": "", "audit_log": []}
    return graph.invoke(initial_state, config=config)


if __name__ == "__main__":
    result = ask("What are the SAR filing deadlines under AML requirements?")
    print("Domain routed:", result["domain"])
    print("Answer:", result["answer"])
    print("\nAudit trail:")
    for entry in result["audit_log"]:
        print(entry)
