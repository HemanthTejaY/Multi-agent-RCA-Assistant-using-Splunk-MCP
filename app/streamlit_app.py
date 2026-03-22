"""Simple Streamlit chat UI between the human and the RCA agent."""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from app.config import get_settings
from app.logging_config import configure_logging
from app.main import build_default_persistence, build_runtime_components
from app.memory.persistence import PersistenceManager
from app.models import ApprovalRecord, IncidentInput
from app.state import GraphState

try:
    import streamlit as st
except ImportError:  # pragma: no cover - optional dependency
    st = None


def ensure_session_state() -> None:
    """Initialize Streamlit session keys."""

    st.session_state.setdefault(
        "messages",
        [
            {
                "role": "assistant",
                "content": (
                    "Describe an incident to start a case. I will triage it, investigate through MCP-discovered "
                    "Splunk tools, and ask you here when I need approval."
                ),
            }
        ],
    )
    st.session_state.setdefault("graph_state_json", None)
    st.session_state.setdefault("pending_stage", None)


def load_graph_state() -> Optional[GraphState]:
    """Deserialize the current graph state from session storage."""

    payload = st.session_state.get("graph_state_json")
    if not payload:
        return None
    return GraphState.model_validate_json(payload)


def save_graph_state(state: Optional[GraphState]) -> None:
    """Persist graph state into session storage."""

    st.session_state["graph_state_json"] = state.model_dump_json() if state else None


def add_message(role: str, content: str) -> None:
    """Append a chat message."""

    st.session_state["messages"].append({"role": role, "content": content})


def render_messages() -> None:
    """Render chat history."""

    for message in st.session_state["messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])


def approval_help_text(stage: str) -> str:
    """Return chat instructions for approval input."""

    if stage == "search":
        return (
            "Reply with `approve`, `reject`, or `pending`. "
            "You can also send `edit: <note>` to attach a note while approving."
        )
    return (
        "Reply with `approve`, `reject`, `pending`, or `edit: <revised conclusion>`."
    )


def summarize_state_for_chat(state: GraphState) -> str:
    """Return a concise case progress summary."""

    evidence = "\n".join("- {0}".format(item.summary) for item in state.evidence[-3:]) or "- No evidence yet"
    actions = "\n".join(
        "- Step {0}: {1}".format(step.step_number, step.action) for step in state.investigation_steps[-4:]
    ) or "- No actions yet"
    return (
        "**Case ID:** `{case_id}`\n\n"
        "**Classification:** {classification}\n\n"
        "**Status:** {status}\n\n"
        "**Recent Actions:**\n{actions}\n\n"
        "**Evidence:**\n{evidence}"
    ).format(
        case_id=state.case_id or "pending",
        classification=state.issue_classification,
        status=state.status,
        actions=actions,
        evidence=evidence,
    )


def prompt_for_search_approval(state: GraphState) -> str:
    """Build the agent message for a policy-gated MCP action approval."""

    return (
        "I need your approval before running an MCP action blocked by policy.\n\n"
        "**Tool:** `{tool_name}`\n\n"
        "**Reason:** {reason}\n\n"
        "**Input:** `{query}`\n\n"
        "{instructions}"
    ).format(
        tool_name=state.pending_tool_name or "unknown-tool",
        reason=state.pending_search_reason or "Additional evidence is required.",
        query=state.pending_tool_input or {},
        instructions=approval_help_text("search"),
    )


def prompt_for_final_approval(state: GraphState) -> str:
    """Build the agent message for final approval."""

    report = state.rca_report
    if report is None:
        return "The RCA draft is unavailable."
    evidence = "\n".join("- {0}".format(item.summary) for item in state.evidence[:4]) or "- No evidence"
    next_steps = "\n".join("- {0}".format(item) for item in report.next_steps) or "- No next steps"
    return (
        "I have a draft RCA ready for your review.\n\n"
        "**Likely Cause:** {cause}\n\n"
        "**Evidence:**\n{evidence}\n\n"
        "**Next Steps:**\n{next_steps}\n\n"
        "{instructions}"
    ).format(
        cause=report.likely_cause,
        evidence=evidence,
        next_steps=next_steps,
        instructions=approval_help_text("final"),
    )


def format_final_summary(state: GraphState) -> str:
    """Build the final assistant response."""

    summary = state.final_summary
    if summary is None:
        return "The workflow completed without a final summary."
    parts = [
        "**Summary of issue:** {0}".format(summary.summary_of_issue),
        "**Issue Breakdown:**\n" + "\n".join("- {0}".format(item) for item in summary.issue_breakdown),
        "**Actions Taken:**\n" + "\n".join("- {0}".format(item) for item in summary.actions_taken),
        "**Human approvals:**\n" + "\n".join("- {0}".format(item) for item in summary.human_approvals),
        "**Next Steps:**\n" + "\n".join("- {0}".format(item) for item in summary.next_steps),
        "**Status:** {0}".format(summary.status),
    ]
    if summary.jira_ticket_key:
        parts.append(
            "**Jira:** `{0}` {1}".format(summary.jira_ticket_key, summary.jira_ticket_url or "")
        )
    return "\n\n".join(parts)


def parse_approval_response(stage: str, message: str) -> Tuple[ApprovalRecord, str]:
    """Parse a chat reply into an approval record."""

    raw = message.strip()
    lowered = raw.lower()
    if lowered.startswith("edit:"):
        edited = raw.split(":", 1)[1].strip()
        return (
            ApprovalRecord(
                stage=stage,
                action="edited",
                approver="streamlit-human",
                edited_conclusion=edited or None,
                note="Edited in Streamlit chat",
            ),
            "I recorded your edit and will continue using the revised conclusion.",
        )
    if lowered.startswith("reject"):
        note = raw.split(":", 1)[1].strip() if ":" in raw else None
        return (
            ApprovalRecord(stage=stage, action="rejected", approver="streamlit-human", note=note),
            "I recorded your rejection. I will stop autonomous progress and escalate if needed.",
        )
    if lowered.startswith("pending"):
        note = raw.split(":", 1)[1].strip() if ":" in raw else "No human approver available."
        return (
            ApprovalRecord(stage=stage, action="pending", approver="streamlit-human", note=note),
            "I recorded that no approver is currently available.",
        )
    if lowered.startswith("approve"):
        note = raw.split(":", 1)[1].strip() if ":" in raw else None
        return (
            ApprovalRecord(stage=stage, action="approved", approver="streamlit-human", note=note),
            "I recorded your approval and will continue.",
        )
    return (
        ApprovalRecord(stage=stage, action="pending", approver="streamlit-human", note="Unrecognized approval response."),
        "I could not interpret that as an approval decision, so I marked it as pending.",
    )


def run_until_checkpoint(nodes, state: GraphState) -> GraphState:
    """Advance the workflow until a human decision or completion is needed."""

    if not state.case_id:
        state = GraphState.from_dict(nodes.supervisor_router(state.to_dict()))
        add_message("assistant", "I created a new case and completed triage.\n\n" + summarize_state_for_chat(state))

    if state.investigation_required and not state.evidence and not state.findings:
        state = GraphState.from_dict(nodes.investigation(state.to_dict()))
        add_message("assistant", "I ran the initial investigation steps.\n\n" + summarize_state_for_chat(state))

    if state.search_approval_required:
        st.session_state["pending_stage"] = "search"
        add_message("assistant", prompt_for_search_approval(state))
        save_graph_state(state)
        return state

    if state.rca_report is None:
        state = GraphState.from_dict(nodes.rca_documentation(state.to_dict()))

    if state.escalation_required:
        state = GraphState.from_dict(nodes.jira_escalation(state.to_dict()))
        state = GraphState.from_dict(nodes.finalize_case(state.to_dict()))
        add_message("assistant", format_final_summary(state))
        st.session_state["pending_stage"] = None
        save_graph_state(state)
        return state

    st.session_state["pending_stage"] = "final"
    add_message("assistant", prompt_for_final_approval(state))
    save_graph_state(state)
    return state


def handle_new_incident(nodes, user_message: str, metadata: Dict[str, str]) -> None:
    """Start a new case from a chat message."""

    incident = IncidentInput(
        issue_text=user_message,
        service_name=metadata.get("service_name") or None,
        severity=metadata.get("severity") or "medium",
        environment=metadata.get("environment") or "production",
        correlation_id=metadata.get("correlation_id") or None,
        alert_id=metadata.get("alert_id") or None,
    )
    state = GraphState(incident=incident)
    run_until_checkpoint(nodes, state)


def handle_approval_reply(nodes, state: GraphState, stage: str, user_message: str) -> None:
    """Handle a human approval response in chat."""

    approval, response_text = parse_approval_response(stage, user_message)
    add_message("assistant", response_text)

    if stage == "search":
        state = nodes.apply_search_approval(state, approval)
        if state.rca_report is None:
            state = GraphState.from_dict(nodes.rca_documentation(state.to_dict()))
    else:
        state = nodes.apply_final_approval(state, approval)

    if state.escalation_required:
        state = GraphState.from_dict(nodes.jira_escalation(state.to_dict()))
        state = GraphState.from_dict(nodes.finalize_case(state.to_dict()))
        add_message("assistant", format_final_summary(state))
        st.session_state["pending_stage"] = None
        save_graph_state(state)
        return

    if stage == "search":
        st.session_state["pending_stage"] = "final"
        save_graph_state(state)
        add_message("assistant", prompt_for_final_approval(state))
        return

    state = GraphState.from_dict(nodes.finalize_case(state.to_dict()))
    add_message("assistant", format_final_summary(state))
    st.session_state["pending_stage"] = None
    save_graph_state(state)


def reset_conversation() -> None:
    """Reset the Streamlit conversation state."""

    st.session_state["messages"] = [
        {
            "role": "assistant",
            "content": (
                "Conversation reset. Describe a new incident when you are ready."
            ),
        }
    ]
    st.session_state["graph_state_json"] = None
    st.session_state["pending_stage"] = None


def main() -> None:
    """Render the chat UI."""

    if st is None:
        raise RuntimeError("streamlit is required to run the dashboard.")

    settings = get_settings()
    configure_logging(settings.app_log_level)

    st.set_page_config(page_title="RCA Agent Chat", layout="wide")
    st.title("Splunk MCP RCA Assistant")
    st.caption("Human and agent collaborate here. The agent investigates, then asks you for approvals in chat.")

    ensure_session_state()

    with st.sidebar:
        st.subheader("New Case Metadata")
        service_name = st.text_input("Service Name", key="service_name")
        severity = st.selectbox("Severity", ["low", "medium", "high", "critical"], index=1, key="severity")
        environment = st.text_input("Environment", value="production", key="environment")
        correlation_id = st.text_input("Correlation ID", key="correlation_id")
        alert_id = st.text_input("Alert ID", key="alert_id")
        if st.button("Reset Conversation", use_container_width=True):
            reset_conversation()
            st.rerun()

    persistence = build_default_persistence(settings)
    nodes, _ = build_runtime_components(
        settings=settings,
        persistence_manager=persistence,
    )

    render_messages()

    current_state = load_graph_state()
    if current_state is not None:
        with st.expander("Current Case Snapshot", expanded=False):
            st.json(current_state.model_dump(mode="json"))

    user_message = st.chat_input("Describe an incident or respond with approve / reject / edit / pending")
    if not user_message:
        return

    add_message("user", user_message)

    metadata = {
        "service_name": service_name,
        "severity": severity,
        "environment": environment,
        "correlation_id": correlation_id,
        "alert_id": alert_id,
    }

    current_state = load_graph_state()
    pending_stage = st.session_state.get("pending_stage")
    if current_state is None:
        handle_new_incident(nodes, user_message, metadata)
    elif pending_stage in {"search", "final"}:
        handle_approval_reply(nodes, current_state, pending_stage, user_message)
    else:
        add_message(
            "assistant",
            "The current case is already complete. Reset the conversation from the sidebar to start a new incident.",
        )

    st.rerun()


if __name__ == "__main__":
    main()
