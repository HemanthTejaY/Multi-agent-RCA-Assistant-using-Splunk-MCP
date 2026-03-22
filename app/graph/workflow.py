"""LangGraph workflow wiring with a simple local fallback."""

from __future__ import annotations

from app.graph.nodes import WorkflowNodes

try:
    from langgraph.graph import END, StateGraph
except ImportError:  # pragma: no cover - optional dependency
    END = "__end__"
    StateGraph = None


class SequentialWorkflow:
    """Fallback workflow used when LangGraph is unavailable."""

    def __init__(self, nodes: WorkflowNodes):
        self.nodes = nodes

    def invoke(self, payload):
        state = self.nodes.supervisor_router(payload)
        if state.get("investigation_required", True):
            state = self.nodes.investigation(state)
            if state.get("search_approval_required"):
                state = self.nodes.search_approval_gate(state)
        state = self.nodes.rca_documentation(state)
        state = self.nodes.final_approval_gate(state)
        if state.get("escalation_required"):
            state = self.nodes.jira_escalation(state)
        state = self.nodes.finalize_case(state)
        return state


def build_workflow(nodes: WorkflowNodes):
    """Create the LangGraph workflow when available, else a sequential fallback."""

    if StateGraph is None:
        return SequentialWorkflow(nodes)

    graph = StateGraph(dict)
    graph.add_node("supervisor_router", nodes.supervisor_router)
    graph.add_node("investigation", nodes.investigation)
    graph.add_node("search_approval_gate", nodes.search_approval_gate)
    graph.add_node("rca_documentation", nodes.rca_documentation)
    graph.add_node("final_approval_gate", nodes.final_approval_gate)
    graph.add_node("jira_escalation", nodes.jira_escalation)
    graph.add_node("finalize_case", nodes.finalize_case)

    graph.set_entry_point("supervisor_router")

    graph.add_conditional_edges(
        "supervisor_router",
        lambda state: "investigation" if state.get("investigation_required", True) else "rca_documentation",
        {
            "investigation": "investigation",
            "rca_documentation": "rca_documentation",
        },
    )
    graph.add_conditional_edges(
        "investigation",
        lambda state: "search_approval_gate" if state.get("search_approval_required") else "rca_documentation",
        {
            "search_approval_gate": "search_approval_gate",
            "rca_documentation": "rca_documentation",
        },
    )
    graph.add_edge("search_approval_gate", "rca_documentation")
    graph.add_edge("rca_documentation", "final_approval_gate")
    graph.add_conditional_edges(
        "final_approval_gate",
        lambda state: "jira_escalation" if state.get("escalation_required") else "finalize_case",
        {
            "jira_escalation": "jira_escalation",
            "finalize_case": "finalize_case",
        },
    )
    graph.add_edge("jira_escalation", "finalize_case")
    graph.add_edge("finalize_case", END)

    return graph.compile()
