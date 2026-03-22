"""Workflow state definitions."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.models import (
    ApprovalRecord,
    EvidenceItem,
    FinalCaseSummary,
    Finding,
    IncidentInput,
    InvestigationStep,
    JiraTicket,
    RCAReport,
    SimilarCaseMemory,
)


class GraphState(BaseModel):
    """Pydantic-backed workflow state carried across graph nodes."""

    incident: IncidentInput
    case_id: Optional[str] = None
    issue_classification: str = "general_incident"
    investigation_required: bool = True
    route: str = "investigate"
    status: str = "received"
    pending_tool_name: Optional[str] = None
    pending_tool_role: Optional[str] = None
    pending_tool_input: Dict[str, Any] = Field(default_factory=dict)
    pending_search_query: Optional[str] = None
    pending_search_reason: Optional[str] = None
    search_approval_required: bool = False
    evidence: List[EvidenceItem] = Field(default_factory=list)
    investigation_steps: List[InvestigationStep] = Field(default_factory=list)
    findings: List[Finding] = Field(default_factory=list)
    likely_cause: Optional[str] = None
    confidence_level: float = 0.0
    recommended_next_actions: List[str] = Field(default_factory=list)
    similar_cases: List[SimilarCaseMemory] = Field(default_factory=list)
    approvals: List[ApprovalRecord] = Field(default_factory=list)
    rca_report: Optional[RCAReport] = None
    final_summary: Optional[FinalCaseSummary] = None
    escalation_required: bool = False
    escalation_reason: Optional[str] = None
    jira_ticket: Optional[JiraTicket] = None
    last_error: Optional[str] = None
    metadata: Dict[str, str] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        """Return a LangGraph-friendly dictionary."""

        return self.model_dump()

    @classmethod
    def from_dict(cls, payload: Dict[str, object]) -> "GraphState":
        """Construct GraphState from plain dictionaries."""

        return cls.model_validate(payload)
