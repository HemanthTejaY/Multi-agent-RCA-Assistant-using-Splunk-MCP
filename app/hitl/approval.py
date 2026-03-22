"""Approval and gating utilities."""

from __future__ import annotations

import json
import logging
from typing import Optional

from app.models import ApprovalRecord
from app.prompts import FINAL_APPROVAL_TEMPLATE, SEARCH_APPROVAL_TEMPLATE

LOGGER = logging.getLogger(__name__)


class ApprovalManager:
    """Support interactive and non-interactive approval flows."""

    def __init__(
        self,
        interactive: bool = True,
        approver: str = "human-operator",
        preset_search_action: str = "approved",
        preset_final_action: str = "approved",
        preset_note: Optional[str] = None,
        preset_edit: Optional[str] = None,
    ):
        self.interactive = interactive
        self.approver = approver
        self.preset_search_action = preset_search_action
        self.preset_final_action = preset_final_action
        self.preset_note = preset_note
        self.preset_edit = preset_edit

    def request_search_approval(
        self,
        issue_summary: str,
        tool_name: str,
        action_input: dict,
        reason: str,
    ) -> ApprovalRecord:
        """Approve or reject an approval-gated MCP action."""

        prompt = SEARCH_APPROVAL_TEMPLATE.format(
            issue_summary=issue_summary,
            tool_name=tool_name,
            action_input=json.dumps(action_input, sort_keys=True),
            reason=reason,
        ).strip()
        return self._resolve(
            stage="search",
            prompt=prompt,
            default_action=self.preset_search_action,
        )

    def request_final_approval(
        self,
        issue_summary: str,
        evidence: str,
        likely_cause: str,
        next_steps: str,
    ) -> ApprovalRecord:
        """Approve, reject, or edit the final RCA."""

        prompt = FINAL_APPROVAL_TEMPLATE.format(
            issue_summary=issue_summary,
            evidence=evidence,
            likely_cause=likely_cause,
            next_steps=next_steps,
        ).strip()
        return self._resolve(
            stage="final",
            prompt=prompt,
            default_action=self.preset_final_action,
        )

    def _resolve(self, stage: str, prompt: str, default_action: str) -> ApprovalRecord:
        if not self.interactive:
            action = default_action
            if action == "pending":
                return ApprovalRecord(
                    stage=stage,
                    action="pending",
                    approver=self.approver,
                    note=self.preset_note or "No human approver was available.",
                )
            if action == "edited":
                return ApprovalRecord(
                    stage=stage,
                    action="edited",
                    approver=self.approver,
                    note=self.preset_note,
                    edited_conclusion=self.preset_edit,
                )
            return ApprovalRecord(
                stage=stage,
                action="auto_approved" if action == "approved" else action,
                approver=self.approver,
                note=self.preset_note,
            )

        print("\n=== Human Approval Required: {0} ===".format(stage.upper()))
        print(prompt)
        response = input("Enter approve / reject / edit / pending: ").strip().lower()
        if response.startswith("edit"):
            edited = input("Provide the revised conclusion text: ").strip()
            note = input("Optional reviewer note: ").strip() or None
            return ApprovalRecord(
                stage=stage,
                action="edited",
                approver=self.approver,
                note=note,
                edited_conclusion=edited,
            )
        if response.startswith("reject"):
            note = input("Optional rejection note: ").strip() or None
            return ApprovalRecord(
                stage=stage,
                action="rejected",
                approver=self.approver,
                note=note,
            )
        if response.startswith("pending"):
            note = input("Optional note about missing approver: ").strip() or None
            return ApprovalRecord(
                stage=stage,
                action="pending",
                approver=self.approver,
                note=note,
            )
        note = input("Optional approval note: ").strip() or None
        return ApprovalRecord(
            stage=stage,
            action="approved",
            approver=self.approver,
            note=note,
        )
