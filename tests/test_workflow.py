"""Workflow tests using in-memory infrastructure."""

from app.config import Settings, get_settings
from app.hitl.approval import ApprovalManager
from app.main import run_case
from app.db import InMemoryDatabaseClient
from app.memory.persistence import InMemoryMemoryStore, PersistenceManager
from app.models import IncidentInput
from app.policy import MCPToolPolicy
from app.tools.splunk_adapter import MCPToolRegistry, MockMCPClient


def build_test_dependencies():
    persistence = PersistenceManager(
        db_client=InMemoryDatabaseClient(),
        memory_store=InMemoryMemoryStore(),
    )
    return persistence


def test_workflow_happy_path_generates_structured_summary():
    persistence = build_test_dependencies()
    state = run_case(
        incident=IncidentInput(
            issue_text="Payment latency spike causing timeouts in checkout.",
            service_name="payment-service",
            correlation_id="corr-payment-001",
            alert_id="alert-payment-latency",
        ),
        settings=get_settings(),
        approval_manager=ApprovalManager(interactive=False),
        persistence_manager=persistence,
        adapter=MockMCPClient(),
    )

    assert state.final_summary is not None
    assert state.final_summary.likely_cause
    assert state.final_summary.evidence_used
    assert state.final_summary.summary_of_issue
    assert state.final_summary.issue_breakdown
    assert state.final_summary.actions_taken
    assert state.final_summary.human_approvals
    assert state.final_summary.next_steps
    assert state.final_summary.human_approval_status in {"approved", "auto_approved", "edited"}


def test_broad_search_requires_approval_and_records_rejection():
    persistence = build_test_dependencies()
    state = run_case(
        incident=IncidentInput(
            issue_text="Authentication failures across the platform with no obvious service owner.",
            severity="high",
        ),
        settings=get_settings(),
        approval_manager=ApprovalManager(interactive=False, preset_search_action="rejected", preset_final_action="approved"),
        persistence_manager=persistence,
        adapter=MockMCPClient(),
    )

    assert any(approval.stage == "search" for approval in state.approvals)
    assert state.approvals[0].action == "rejected"
    assert "Search was not approved" in " ".join(state.recommended_next_actions)
    assert state.jira_ticket is not None
    assert state.jira_ticket.key.startswith("DRYRUN-")


def test_pending_final_approval_escalates_to_jira():
    persistence = build_test_dependencies()
    state = run_case(
        incident=IncidentInput(
            issue_text="Inventory refresh jobs are failing due to repeated database timeouts.",
            service_name="inventory-api",
            correlation_id="corr-db-900",
            alert_id="alert-db-timeout",
        ),
        settings=get_settings(),
        approval_manager=ApprovalManager(interactive=False, preset_final_action="pending"),
        persistence_manager=persistence,
        adapter=MockMCPClient(),
    )

    assert state.final_summary is not None
    assert state.final_summary.jira_ticket_key is not None
    assert state.status == "escalated_to_jira"


def test_strict_policy_requires_approval_for_broad_search_role():
    registry = MCPToolRegistry(MockMCPClient())
    policy = MCPToolPolicy(settings=Settings(), tool_registry=registry)

    decision = policy.evaluate("search_splunk", role="broad_search")

    assert decision.decision == "require_approval"


def test_strict_policy_denies_mutating_tool_names_by_pattern():
    registry = MCPToolRegistry(MockMCPClient())
    policy = MCPToolPolicy(settings=Settings(), tool_registry=registry)

    decision = policy.evaluate("delete_kvstore_collection")

    assert decision.decision == "deny"
