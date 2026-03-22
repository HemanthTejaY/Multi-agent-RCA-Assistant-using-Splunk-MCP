"""Postgres persistence layer."""

from __future__ import annotations

import json
import logging
from contextlib import contextmanager
from typing import Dict, Iterable, List, Optional

from app.models import ApprovalRecord, CaseRecord, Finding, InvestigationStep, RCAReport, SimilarCaseMemory

LOGGER = logging.getLogger(__name__)

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:  # pragma: no cover - optional dependency for local planning
    psycopg = None
    dict_row = None


class DatabaseClient:
    """Small Postgres helper for case persistence."""

    def __init__(self, dsn: str):
        self.dsn = dsn

    @contextmanager
    def connect(self):
        """Open a database connection."""

        if psycopg is None:
            raise RuntimeError("psycopg is required to use the Postgres persistence layer.")
        connection = psycopg.connect(self.dsn, row_factory=dict_row, autocommit=True)
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create required tables if they do not exist."""

        statements = [
            """
            CREATE TABLE IF NOT EXISTS cases (
                case_id TEXT PRIMARY KEY,
                issue_summary TEXT NOT NULL,
                service_name TEXT,
                severity TEXT NOT NULL,
                status TEXT NOT NULL,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS investigation_steps (
                id BIGSERIAL PRIMARY KEY,
                case_id TEXT NOT NULL,
                step_number INTEGER NOT NULL,
                action TEXT NOT NULL,
                action_input JSONB NOT NULL,
                evidence_summary TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS findings (
                id BIGSERIAL PRIMARY KEY,
                case_id TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                likely_cause TEXT NOT NULL,
                confidence DOUBLE PRECISION NOT NULL,
                supporting_evidence JSONB NOT NULL,
                limitations JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS approvals (
                id BIGSERIAL PRIMARY KEY,
                case_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                action TEXT NOT NULL,
                approver TEXT NOT NULL,
                note TEXT,
                edited_conclusion TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS rca_reports (
                id BIGSERIAL PRIMARY KEY,
                case_id TEXT NOT NULL,
                summary_of_issue TEXT NOT NULL,
                issue_breakdown JSONB NOT NULL,
                actions_taken JSONB NOT NULL,
                human_approvals JSONB NOT NULL,
                engineer_summary TEXT NOT NULL,
                customer_summary TEXT NOT NULL,
                likely_cause TEXT NOT NULL,
                confidence DOUBLE PRECISION NOT NULL,
                next_steps JSONB NOT NULL,
                remediation_suggestions JSONB NOT NULL,
                open_questions JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """,
        ]
        with self.connect() as connection:
            with connection.cursor() as cursor:
                for statement in statements:
                    cursor.execute(statement)

    def upsert_case(self, case_record: CaseRecord, payload: Dict[str, object]) -> None:
        """Insert or update a case row."""

        query = """
        INSERT INTO cases (case_id, issue_summary, service_name, severity, status, payload, created_at, updated_at)
        VALUES (%(case_id)s, %(issue_summary)s, %(service_name)s, %(severity)s, %(status)s, %(payload)s::jsonb,
                %(created_at)s, %(updated_at)s)
        ON CONFLICT (case_id) DO UPDATE SET
            issue_summary = EXCLUDED.issue_summary,
            service_name = EXCLUDED.service_name,
            severity = EXCLUDED.severity,
            status = EXCLUDED.status,
            payload = EXCLUDED.payload,
            updated_at = EXCLUDED.updated_at
        """
        params = case_record.model_dump()
        params["payload"] = json.dumps(payload, default=str)
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, params)

    def insert_step(self, case_id: str, step: InvestigationStep) -> None:
        """Persist an investigation step."""

        query = """
        INSERT INTO investigation_steps (case_id, step_number, action, action_input, evidence_summary, status, created_at)
        VALUES (%s, %s, %s, %s::jsonb, %s, %s, %s)
        """
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    query,
                    (
                        case_id,
                        step.step_number,
                        step.action,
                        json.dumps(step.action_input, default=str),
                        step.evidence_summary,
                        step.status,
                        step.created_at,
                    ),
                )

    def insert_finding(self, case_id: str, finding: Finding) -> None:
        """Persist a structured finding."""

        query = """
        INSERT INTO findings (case_id, title, category, likely_cause, confidence, supporting_evidence, limitations)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
        """
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    query,
                    (
                        case_id,
                        finding.title,
                        finding.category,
                        finding.likely_cause,
                        finding.confidence,
                        json.dumps(finding.supporting_evidence),
                        json.dumps(finding.limitations),
                    ),
                )

    def insert_approval(self, case_id: str, approval: ApprovalRecord) -> None:
        """Persist a human approval checkpoint."""

        query = """
        INSERT INTO approvals (case_id, stage, action, approver, note, edited_conclusion, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    query,
                    (
                        case_id,
                        approval.stage,
                        approval.action,
                        approval.approver,
                        approval.note,
                        approval.edited_conclusion,
                        approval.created_at,
                    ),
                )

    def insert_rca_report(self, case_id: str, report: RCAReport) -> None:
        """Persist the RCA report."""

        query = """
        INSERT INTO rca_reports (
            case_id, summary_of_issue, issue_breakdown, actions_taken, human_approvals,
            engineer_summary, customer_summary, likely_cause, confidence,
            next_steps, remediation_suggestions, open_questions
        ) VALUES (%s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
        """
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    query,
                    (
                        case_id,
                        report.summary_of_issue,
                        json.dumps(report.issue_breakdown),
                        json.dumps(report.actions_taken),
                        json.dumps(report.human_approvals),
                        report.engineer_summary,
                        report.customer_summary,
                        report.likely_cause,
                        report.confidence,
                        json.dumps(report.next_steps),
                        json.dumps(report.remediation_suggestions),
                        json.dumps(report.open_questions),
                    ),
                )

    def fetch_recent_case_memories(self, limit: int = 5) -> List[SimilarCaseMemory]:
        """Return recent cases as simple long-term memory candidates."""

        query = """
        SELECT case_id, issue_summary, payload, created_at
        FROM cases
        ORDER BY updated_at DESC
        LIMIT %s
        """
        memories: List[SimilarCaseMemory] = []
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, (limit,))
                for row in cursor.fetchall():
                    payload = row.get("payload") or {}
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    report = payload.get("rca_report") or {}
                    memories.append(
                        SimilarCaseMemory(
                            case_id=row["case_id"],
                            issue_summary=row["issue_summary"],
                            likely_cause=report.get("likely_cause", "Unknown"),
                            resolution_summary="; ".join(report.get("next_steps", [])) or "Review stored report",
                            created_at=row["created_at"],
                        )
                    )
        return memories

    def list_pending_approvals(self) -> List[Dict[str, object]]:
        """Return pending approval rows for the Streamlit dashboard."""

        query = """
        SELECT a.id, a.case_id, a.stage, a.action, a.note, c.issue_summary, c.status, c.updated_at
        FROM approvals a
        JOIN cases c ON c.case_id = a.case_id
        WHERE a.action = 'pending'
        ORDER BY a.created_at DESC
        """
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query)
                return list(cursor.fetchall())


class InMemoryDatabaseClient(DatabaseClient):
    """Simple in-memory implementation used for tests."""

    def __init__(self):
        self.cases: Dict[str, Dict[str, object]] = {}
        self.steps: List[Dict[str, object]] = []
        self.findings: List[Dict[str, object]] = []
        self.approvals: List[Dict[str, object]] = []
        self.reports: List[Dict[str, object]] = []

    def initialize(self) -> None:
        return None

    def upsert_case(self, case_record: CaseRecord, payload: Dict[str, object]) -> None:
        self.cases[case_record.case_id] = {
            "record": case_record,
            "payload": payload,
        }

    def insert_step(self, case_id: str, step: InvestigationStep) -> None:
        self.steps.append({"case_id": case_id, "step": step})

    def insert_finding(self, case_id: str, finding: Finding) -> None:
        self.findings.append({"case_id": case_id, "finding": finding})

    def insert_approval(self, case_id: str, approval: ApprovalRecord) -> None:
        self.approvals.append({"case_id": case_id, "approval": approval})

    def insert_rca_report(self, case_id: str, report: RCAReport) -> None:
        self.reports.append({"case_id": case_id, "report": report})

    def fetch_recent_case_memories(self, limit: int = 5) -> List[SimilarCaseMemory]:
        memories: List[SimilarCaseMemory] = []
        for case_id, entry in list(self.cases.items())[-limit:]:
            payload = entry["payload"]
            report = payload.get("rca_report") or {}
            record = entry["record"]
            memories.append(
                SimilarCaseMemory(
                    case_id=case_id,
                    issue_summary=record.issue_summary,
                    likely_cause=report.get("likely_cause", "Unknown"),
                    resolution_summary="; ".join(report.get("next_steps", [])) or "Review stored report",
                    created_at=record.created_at,
                )
            )
        return memories

    def list_pending_approvals(self) -> List[Dict[str, object]]:
        rows: List[Dict[str, object]] = []
        for item in self.approvals:
            approval = item["approval"]
            if approval.action == "pending":
                case_record = self.cases[item["case_id"]]["record"]
                rows.append(
                    {
                        "case_id": item["case_id"],
                        "stage": approval.stage,
                        "action": approval.action,
                        "note": approval.note,
                        "issue_summary": case_record.issue_summary,
                        "status": case_record.status,
                    }
                )
        return rows
