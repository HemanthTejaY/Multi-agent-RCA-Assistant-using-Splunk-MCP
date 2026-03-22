"""Jira escalation adapter."""

from __future__ import annotations

import base64
import logging
from typing import Dict, List, Optional

from app.models import JiraTicket

LOGGER = logging.getLogger(__name__)

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


class JiraAdapter:
    """Create Jira issues directly through the Jira REST API."""

    def __init__(
        self,
        enabled: bool,
        base_url: Optional[str],
        email: Optional[str],
        api_token: Optional[str],
        project_key: str,
        issue_type: str = "Task",
        default_assignee: Optional[str] = None,
        labels: Optional[List[str]] = None,
        components: Optional[List[str]] = None,
        timeout_seconds: int = 20,
    ):
        self.enabled = enabled
        self.base_url = (base_url or "").rstrip("/")
        self.email = email
        self.api_token = api_token
        self.project_key = project_key
        self.issue_type = issue_type
        self.default_assignee = default_assignee
        self.labels = labels or []
        self.components = components or []
        self.timeout_seconds = timeout_seconds

    def create_blocked_investigation_ticket(
        self,
        case_id: str,
        summary_of_issue: str,
        issue_breakdown: List[str],
        actions_taken: List[str],
        human_approvals: List[str],
        next_steps: List[str],
        blocked_reason: str,
        severity: str,
    ) -> JiraTicket:
        """Create or simulate a Jira ticket for a blocked investigation."""

        summary = "[{severity}] RCA follow-up for {case_id}".format(
            severity=severity.upper(),
            case_id=case_id,
        )
        description_lines = [
            "Summary of issue:",
            summary_of_issue,
            "",
            "Issue Breakdown:",
            *["- {0}".format(item) for item in issue_breakdown],
            "",
            "Actions Taken:",
            *["- {0}".format(item) for item in actions_taken],
            "",
            "Human approvals:",
            *["- {0}".format(item) for item in human_approvals],
            "",
            "Next Steps:",
            *["- {0}".format(item) for item in next_steps],
            "",
            "Blocked reason:",
            blocked_reason,
        ]
        payload = self._build_payload(summary=summary, description="\n".join(description_lines), severity=severity)

        if not self.enabled:
            return JiraTicket(
                key="DRYRUN-{0}".format(case_id[-6:].upper()),
                url="{0}/browse/{1}".format(self.base_url or "https://jira.example.com", "DRYRUN-{0}".format(case_id[-6:].upper())),
                status="dry_run",
                summary=summary,
                issue_type=self.issue_type,
            )

        if requests is None:
            raise RuntimeError("requests is required for live Jira integration.")
        if not (self.base_url and self.email and self.api_token):
            raise ValueError("Jira is enabled but required credentials are missing.")

        auth_token = base64.b64encode("{0}:{1}".format(self.email, self.api_token).encode("utf-8")).decode("utf-8")
        response = requests.post(
            "{0}/rest/api/3/issue".format(self.base_url),
            json=payload,
            headers={
                "Authorization": "Basic {0}".format(auth_token),
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        key = body["key"]
        return JiraTicket(
            key=key,
            url="{0}/browse/{1}".format(self.base_url, key),
            status="created",
            summary=summary,
            issue_type=self.issue_type,
        )

    def _build_payload(self, summary: str, description: str, severity: str) -> Dict[str, object]:
        fields: Dict[str, object] = {
            "project": {"key": self.project_key},
            "summary": summary,
            "issuetype": {"name": self.issue_type},
            "description": description,
            "labels": list(self.labels) + [severity.lower()],
        }
        if self.default_assignee:
            fields["assignee"] = {"name": self.default_assignee}
        if self.components:
            fields["components"] = [{"name": name} for name in self.components]
        return {"fields": fields}
