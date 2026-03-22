"""Supervisor/router agent."""

from __future__ import annotations

from typing import List

from app.models import IncidentInput, SimilarCaseMemory


class RouterAgent:
    """Classify incidents and decide whether investigation is needed."""

    def route(self, incident: IncidentInput, similar_cases: List[SimilarCaseMemory]) -> dict:
        text = incident.issue_text.lower()
        classification = "general_incident"
        route = "investigate"
        investigation_required = True

        if any(token in text for token in ["latency", "slow", "timeout", "timed out"]):
            classification = "performance_or_dependency"
        elif any(token in text for token in ["login", "auth", "unauthorized", "forbidden"]):
            classification = "authentication_issue"
        elif any(token in text for token in ["error", "exception", "failure", "incident"]):
            classification = "application_error"
        else:
            route = "document_only"
            investigation_required = False

        if similar_cases and classification == "general_incident":
            classification = "pattern_match_recent_case"

        return {
            "issue_classification": classification,
            "route": route,
            "investigation_required": investigation_required,
        }

