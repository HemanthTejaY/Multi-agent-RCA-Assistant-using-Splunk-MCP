"""Pydantic models used across the application."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""

    return datetime.now(timezone.utc)


class IncidentInput(BaseModel):
    """User-provided incident details."""

    issue_text: str
    service_name: Optional[str] = None
    severity: str = "medium"
    environment: str = "production"
    correlation_id: Optional[str] = None
    alert_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SimilarCaseMemory(BaseModel):
    """Condensed long-term memory about a prior case."""

    case_id: str
    issue_summary: str
    likely_cause: str
    resolution_summary: str
    created_at: datetime


class EvidenceItem(BaseModel):
    """Concise evidence captured from tool usage."""

    source: str
    tool_name: str
    summary: str
    raw_reference: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class InvestigationStep(BaseModel):
    """One investigation action and result."""

    step_number: int
    thought: str
    action: str
    action_input: Dict[str, Any] = Field(default_factory=dict)
    evidence_summary: str
    status: Literal["completed", "skipped", "rejected", "error"] = "completed"
    created_at: datetime = Field(default_factory=utc_now)


class Finding(BaseModel):
    """Structured investigative finding."""

    title: str
    category: str
    likely_cause: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    supporting_evidence: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)


class ApprovalRecord(BaseModel):
    """Human approval checkpoint result."""

    stage: Literal["search", "final"]
    action: Literal["approved", "rejected", "edited", "auto_approved", "pending"]
    approver: str = "system"
    note: Optional[str] = None
    edited_conclusion: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)


class RCAReport(BaseModel):
    """Final RCA output."""

    summary_of_issue: str
    issue_breakdown: List[str] = Field(default_factory=list)
    actions_taken: List[str] = Field(default_factory=list)
    human_approvals: List[str] = Field(default_factory=list)
    next_steps: List[str] = Field(default_factory=list)
    engineer_summary: str
    customer_summary: str
    likely_cause: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    remediation_suggestions: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)


class JiraTicket(BaseModel):
    """Escalated Jira ticket metadata."""

    key: str
    url: str
    status: str = "created"
    summary: str
    issue_type: str


class CaseRecord(BaseModel):
    """Persistent case record."""

    case_id: str
    issue_summary: str
    service_name: Optional[str] = None
    severity: str = "medium"
    status: str = "open"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class FinalCaseSummary(BaseModel):
    """CLI and eval-facing result object."""

    case_id: str
    summary_of_issue: str
    issue_breakdown: List[str]
    actions_taken: List[str]
    human_approvals: List[str]
    next_steps: List[str]
    issue_summary: str
    evidence_used: List[str]
    likely_cause: str
    confidence_level: float
    recommended_next_actions: List[str]
    human_approval_status: str
    engineer_summary: str
    customer_summary: str
    status: str
    jira_ticket_key: Optional[str] = None
    jira_ticket_url: Optional[str] = None
