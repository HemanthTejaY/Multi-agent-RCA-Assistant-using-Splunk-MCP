"""Tests for persistence abstractions."""

from app.db import InMemoryDatabaseClient
from app.memory.persistence import InMemoryMemoryStore, PersistenceManager
from app.models import ApprovalRecord, IncidentInput, InvestigationStep
from app.state import GraphState


def test_persistence_manager_saves_and_loads_state():
    db_client = InMemoryDatabaseClient()
    memory_store = InMemoryMemoryStore()
    persistence = PersistenceManager(db_client=db_client, memory_store=memory_store)

    state = GraphState(
        case_id="case-123",
        incident=IncidentInput(issue_text="Payment latency issue", service_name="payment-service"),
        status="investigating",
    )
    persistence.save_case_state(state)

    loaded = persistence.load_state("case-123")
    assert loaded is not None
    assert loaded.case_id == "case-123"
    assert db_client.cases["case-123"]["record"].status == "investigating"


def test_persistence_manager_stores_steps_and_approvals():
    db_client = InMemoryDatabaseClient()
    memory_store = InMemoryMemoryStore()
    persistence = PersistenceManager(db_client=db_client, memory_store=memory_store)

    step = InvestigationStep(
        step_number=1,
        thought="Inspect service errors",
        action="get_recent_errors",
        action_input={"service_name": "identity-service"},
        evidence_summary="Found repeated JWT validation failures.",
    )
    approval = ApprovalRecord(stage="final", action="approved", approver="tester")

    persistence.save_step("case-xyz", step)
    persistence.save_approval("case-xyz", approval)

    assert len(db_client.steps) == 1
    assert len(db_client.approvals) == 1

