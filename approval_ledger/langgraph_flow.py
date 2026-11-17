"""Optional LangGraph adapter; the ledger remains the authority for approvals."""
from typing import TypedDict


class State(TypedDict, total=False):
    request_key: str
    incident: str
    run_id: str
    plan_hash: str
    status: str


def build_graph(store, planner, proposer, checkpointer):
    from langgraph.graph import StateGraph, START, END
    from langgraph.types import interrupt

    def propose(state):
        previous = store.lookup(proposer, state["request_key"], state["incident"])
        if previous:
            run = previous
        else:
            evidence = store.retrieve(proposer, state["incident"])
            plan = planner.plan(state["incident"], evidence)
            run = store.propose(proposer, state["request_key"], state["incident"], plan, evidence)
        return {"run_id": run["id"], "plan_hash": run["plan_hash"], "status": run["state"]}

    def review(state):
        run = store.get(proposer, state["run_id"])
        if state["status"] == "awaiting_review":
            interrupt({"run_id": run["id"], "plan_hash": run["plan_hash"], "plan": run["plan"]})
        # Resume data never grants approval. An authenticated reviewer must already
        # have committed a review through Store.review before graph resumption.
        run = store.get(proposer, state["run_id"])
        if run["state"] == "awaiting_review":
            raise ValueError("resume attempted without a committed review")
        return {"status": run["state"]}

    graph = StateGraph(State)
    graph.add_node("propose", propose)
    graph.add_node("review", review)
    graph.add_edge(START, "propose")
    graph.add_edge("propose", "review")
    graph.add_edge("review", END)
    return graph.compile(checkpointer=checkpointer)
