"""RCA documentation agent."""

from __future__ import annotations

from typing import List

from app.models import FinalCaseSummary, RCAReport
from app.state import GraphState


class RCAWriterAgent:
    """Convert findings into structured case documentation."""

    def write(self, state: GraphState) -> GraphState:
        evidence_summaries = [item.summary for item in state.evidence]
        likely_cause = state.likely_cause or "Insufficient evidence to determine likely cause."
        confidence = state.confidence_level
        next_actions = state.recommended_next_actions or [
            "Request additional scoped logs and human review.",
        ]
        actions_taken = self._actions_taken(state)
        human_approvals = self._human_approvals(state)
        issue_breakdown = self._issue_breakdown(state)
        summary_of_issue = self._summary_of_issue(state, likely_cause, confidence)

        engineer_summary = self._engineer_summary(state, evidence_summaries, likely_cause, confidence)
        customer_summary = self._customer_summary(state, likely_cause)
        open_questions = self._open_questions(state)

        state.rca_report = RCAReport(
            summary_of_issue=summary_of_issue,
            issue_breakdown=issue_breakdown,
            actions_taken=actions_taken,
            human_approvals=human_approvals,
            next_steps=next_actions,
            engineer_summary=engineer_summary,
            customer_summary=customer_summary,
            likely_cause=likely_cause,
            confidence=confidence,
            remediation_suggestions=next_actions,
            open_questions=open_questions,
        )
        return state

    def refresh_report_sections(self, state: GraphState) -> GraphState:
        """Refresh dynamic RCA sections after approvals or escalation changes."""

        if state.rca_report is None:
            return self.write(state)
        state.rca_report.summary_of_issue = self._summary_of_issue(
            state,
            state.rca_report.likely_cause,
            state.rca_report.confidence,
        )
        state.rca_report.issue_breakdown = self._issue_breakdown(state)
        state.rca_report.actions_taken = self._actions_taken(state)
        state.rca_report.human_approvals = self._human_approvals(state)
        state.rca_report.next_steps = state.recommended_next_actions or state.rca_report.next_steps
        return state

    def finalize_summary(self, state: GraphState) -> GraphState:
        report = state.rca_report
        if report is None:
            raise ValueError("RCA report must be generated before finalization.")

        summary_of_issue = self._summary_of_issue(state, report.likely_cause, report.confidence)
        issue_breakdown = self._issue_breakdown(state)
        actions_taken = self._actions_taken(state)
        human_approvals = self._human_approvals(state)
        next_steps = state.recommended_next_actions or report.next_steps
        final_approval = state.approvals[-1].action if state.approvals else "pending"
        state.final_summary = FinalCaseSummary(
            case_id=state.case_id or "unassigned",
            summary_of_issue=summary_of_issue,
            issue_breakdown=issue_breakdown,
            actions_taken=actions_taken,
            human_approvals=human_approvals,
            next_steps=next_steps,
            issue_summary=state.incident.issue_text,
            evidence_used=[item.summary for item in state.evidence],
            likely_cause=report.likely_cause,
            confidence_level=report.confidence,
            recommended_next_actions=next_steps,
            human_approval_status=final_approval,
            engineer_summary=report.engineer_summary,
            customer_summary=report.customer_summary,
            status=state.status,
            jira_ticket_key=state.jira_ticket.key if state.jira_ticket else None,
            jira_ticket_url=state.jira_ticket.url if state.jira_ticket else None,
        )
        return state

    def _summary_of_issue(self, state: GraphState, likely_cause: str, confidence: float) -> str:
        return (
            "Incident for {service} classified as {classification}. "
            "Current likely cause: {cause} with confidence {confidence:.2f}."
        ).format(
            service=state.incident.service_name or "unspecified service",
            classification=state.issue_classification,
            cause=likely_cause,
            confidence=confidence,
        )

    def _issue_breakdown(self, state: GraphState) -> List[str]:
        details = [
            "User issue: {0}".format(state.incident.issue_text),
            "Severity: {0}".format(state.incident.severity),
            "Environment: {0}".format(state.incident.environment),
        ]
        if state.incident.service_name:
            details.append("Primary service: {0}".format(state.incident.service_name))
        if state.findings:
            details.append("Finding category: {0}".format(state.findings[0].category))
        if state.escalation_reason:
            details.append("Escalation trigger: {0}".format(state.escalation_reason))
        return details

    def _actions_taken(self, state: GraphState) -> List[str]:
        actions = [
            "Step {0}: {1} -> {2}".format(step.step_number, step.action, step.evidence_summary)
            for step in state.investigation_steps
        ]
        if not actions:
            actions.append("No automated investigation actions were completed.")
        return actions

    def _human_approvals(self, state: GraphState) -> List[str]:
        approvals = [
            "{0} approval: {1}{2}".format(
                approval.stage,
                approval.action,
                " ({0})".format(approval.note) if approval.note else "",
            )
            for approval in state.approvals
        ]
        if not approvals:
            approvals.append("No human approval events have been recorded.")
        return approvals

    def _engineer_summary(
        self,
        state: GraphState,
        evidence_summaries: List[str],
        likely_cause: str,
        confidence: float,
    ) -> str:
        evidence_text = "; ".join(evidence_summaries[:3]) or "No evidence was collected."
        return (
            "Classification: {classification}. Likely cause: {likely_cause} "
            "(confidence {confidence:.2f}). Key evidence: {evidence}. "
            "Current recommendation: {action}."
        ).format(
            classification=state.issue_classification,
            likely_cause=likely_cause,
            confidence=confidence,
            evidence=evidence_text,
            action=state.recommended_next_actions[0] if state.recommended_next_actions else "Continue investigation",
        )

    def _customer_summary(self, state: GraphState, likely_cause: str) -> str:
        if state.confidence_level < 0.5:
            certainty = "We have a working hypothesis but need additional validation."
        else:
            certainty = "Current evidence points to a likely underlying issue."
        return (
            "{certainty} The incident appears related to: {likely_cause} "
            "We are continuing human review before closure."
        ).format(certainty=certainty, likely_cause=likely_cause)

    def _open_questions(self, state: GraphState) -> List[str]:
        if state.confidence_level >= 0.8:
            return ["Confirm remediation effectiveness after the next monitoring interval."]
        return [
            "Do we need additional service-scoped logs or metrics to isolate the dependency?",
            "Should a broader cross-service Splunk search be approved?",
        ]
