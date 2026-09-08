# LangGraph orchestration: state, classifier node, per-domain retrieval nodes,
# generation node, conditional routing, and the compiled graph.
from typing import TypedDict, List, Literal
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field
from config import llm, classifier_llm_base, DOMAINS, CONFIDENCE_THRESHOLD
from retriever import hybrid_retrievers
from checkpointer import checkpointer


# --- Graph state: the shared object every node reads/writes ---
class GraphState(TypedDict):
    query: str
    domain: str
    confidence: float
    documents: List[Document]
    answer: str
    audit_log: List[dict]  # append-only decision trail, for compliance review


# --- Structured output schema for the classifier ---
class DomainClassification(BaseModel):
    domain: Literal["AML", "BSA", "OFAC", "KYC", "GENERAL"] = Field(
        description="The single regulatory domain this query is primarily about"
    )
    confidence: float = Field(description="Confidence score between 0.0 and 1.0")
    reasoning: str = Field(description="One sentence explaining the classification, for audit purposes")


classifier_llm = classifier_llm_base.with_structured_output(DomainClassification)

classification_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You classify compliance/regulatory questions into exactly one domain: "
            "AML (anti-money laundering programs, SARs, suspicious activity), "
            "BSA (Bank Secrecy Act recordkeeping, CTRs, reporting thresholds), "
            "OFAC (sanctions screening, blocked transactions, SDN list), "
            "KYC (customer identification, due diligence, PEPs). "
            "If the query doesn't clearly fit one domain, classify as GENERAL.",
        ),
        ("human", "{query}"),
    ]
)


# --- Node: classify_domain — entry point of the graph ---
def classify_domain(state: GraphState) -> GraphState:
    chain = classification_prompt | classifier_llm
    result: DomainClassification = chain.invoke({"query": state["query"]})

    # Force GENERAL if confidence is too low — safety valve against misrouting to the wrong corpus.
    final_domain = result.domain if result.confidence >= CONFIDENCE_THRESHOLD else "GENERAL"

    audit_entry = {
        "step": "classify_domain",
        "raw_classification": result.domain,
        "confidence": result.confidence,
        "final_domain": final_domain,
        "reasoning": result.reasoning,
    }
    return {
        **state,
        "domain": final_domain,
        "confidence": result.confidence,
        "audit_log": state.get("audit_log", []) + [audit_entry],
    }


# --- Retrieval node factory: one node per domain, sharing the same implementation ---
def make_retrieve_node(domain: str):
    def retrieve(state: GraphState) -> GraphState:
        retriever = hybrid_retrievers[domain]
        docs = retriever.invoke(state["query"])

        audit_entry = {
            "step": f"retrieve_{domain}",
            "num_docs_retrieved": len(docs),
            "sources": [d.metadata.get("source") for d in docs],
        }
        return {
            **state,
            "documents": docs,
            "audit_log": state["audit_log"] + [audit_entry],
        }

    return retrieve


retrieve_nodes = {domain: make_retrieve_node(domain) for domain in DOMAINS + ["GENERAL"]}

# --- Node: generate_answer — shared across all domain paths ---
generation_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Answer the question using ONLY the provided context. Cite the source id for every claim. "
            "If the context doesn't contain the answer, say so explicitly — do not guess.",
        ),
        ("human", "Context:\n{context}\n\nQuestion: {query}"),
    ]
)


def generate_answer(state: GraphState) -> GraphState:
    context = "\n\n".join(f"[{doc.metadata.get('source')}] {doc.page_content}" for doc in state["documents"])
    chain = generation_prompt | llm
    response = chain.invoke({"context": context, "query": state["query"]})

    audit_entry = {"step": "generate_answer", "domain_used": state["domain"]}
    return {
        **state,
        "answer": response.content,
        "audit_log": state["audit_log"] + [audit_entry],
    }


# --- Conditional edge function: reads the classifier's decision, picks the next node ---
def route_domain(state: GraphState) -> str:
    return state["domain"]


# --- Build and compile the graph ---
builder = StateGraph(GraphState)

builder.add_node("classify", classify_domain)
for domain in DOMAINS + ["GENERAL"]:
    builder.add_node(f"retrieve_{domain}", retrieve_nodes[domain])
builder.add_node("generate", generate_answer)

builder.set_entry_point("classify")

# The actual "if AML -> this path, if BSA -> that path" routing.
builder.add_conditional_edges(
    "classify",
    route_domain,
    {domain: f"retrieve_{domain}" for domain in DOMAINS + ["GENERAL"]},
)

# All retrieval paths converge on the same generation node.
for domain in DOMAINS + ["GENERAL"]:
    builder.add_edge(f"retrieve_{domain}", "generate")

builder.add_edge("generate", END)

graph = builder.compile(checkpointer=checkpointer)
