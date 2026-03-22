"""Persistence and memory abstractions over Postgres and Redis."""

from __future__ import annotations

import json
import logging
from typing import List, Optional

from app.db import DatabaseClient
from app.models import ApprovalRecord, CaseRecord, Finding, InvestigationStep, RCAReport, SimilarCaseMemory
from app.state import GraphState

LOGGER = logging.getLogger(__name__)

try:
    import redis
except ImportError:  # pragma: no cover - optional dependency
    redis = None


class MemoryStore:
    """Redis-backed short-term memory store with graceful test fallbacks."""

    def save_state(self, state: GraphState) -> None:
        raise NotImplementedError

    def load_state(self, case_id: str) -> Optional[GraphState]:
        raise NotImplementedError


class RedisMemoryStore(MemoryStore):
    """Store graph state snapshots in Redis."""

    def __init__(self, redis_url: str, prefix: str = "rca-assistant:case:"):
        if redis is None:
            raise RuntimeError("redis package is required to use RedisMemoryStore.")
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)
        self.prefix = prefix

    def save_state(self, state: GraphState) -> None:
        if not state.case_id:
            return
        self.client.set(self.prefix + state.case_id, state.model_dump_json())

    def load_state(self, case_id: str) -> Optional[GraphState]:
        payload = self.client.get(self.prefix + case_id)
        if not payload:
            return None
        return GraphState.model_validate_json(payload)


class InMemoryMemoryStore(MemoryStore):
    """Simple test double for Redis-backed state."""

    def __init__(self):
        self.items = {}

    def save_state(self, state: GraphState) -> None:
        if state.case_id:
            self.items[state.case_id] = state.model_dump_json()

    def load_state(self, case_id: str) -> Optional[GraphState]:
        payload = self.items.get(case_id)
        if not payload:
            return None
        return GraphState.model_validate_json(payload)


class PersistenceManager:
    """Coordinate durable and short-term memory access."""

    def __init__(self, db_client: DatabaseClient, memory_store: MemoryStore):
        self.db_client = db_client
        self.memory_store = memory_store

    def initialize(self) -> None:
        self.db_client.initialize()

    def save_case_state(self, state: GraphState) -> None:
        """Persist the current case snapshot."""

        if not state.case_id:
            return
        record = CaseRecord(
            case_id=state.case_id,
            issue_summary=state.incident.issue_text,
            service_name=state.incident.service_name,
            severity=state.incident.severity,
            status=state.status,
        )
        self.db_client.upsert_case(record, state.model_dump())
        self.memory_store.save_state(state)

    def save_step(self, case_id: str, step: InvestigationStep) -> None:
        self.db_client.insert_step(case_id, step)

    def save_finding(self, case_id: str, finding: Finding) -> None:
        self.db_client.insert_finding(case_id, finding)

    def save_approval(self, case_id: str, approval: ApprovalRecord) -> None:
        self.db_client.insert_approval(case_id, approval)

    def save_report(self, case_id: str, report: RCAReport) -> None:
        self.db_client.insert_rca_report(case_id, report)

    def load_state(self, case_id: str) -> Optional[GraphState]:
        return self.memory_store.load_state(case_id)

    def fetch_similar_cases(self, limit: int = 5) -> List[SimilarCaseMemory]:
        return self.db_client.fetch_recent_case_memories(limit=limit)

