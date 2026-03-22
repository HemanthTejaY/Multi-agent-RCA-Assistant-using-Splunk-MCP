"""Graph node implementations."""

from __future__ import annotations

import logging
from uuid import uuid4

from app.agents.investigator import InvestigationAgent
from app.agents.rca_writer import RCAWriterAgent
from app.agents.router import RouterAgent
from app.hitl.approval import ApprovalManager
from app.memory.persistence import PersistenceManager
from app.models import ApprovalRecord
from app.state import GraphState
from app.tools.jira_adapter import JiraAdapter

LOGGER = logging.getLogger(__name__)


class WorkflowNodes:
    """State transition handlers for the RCA workflow."""

    def __init__(
        self,
        persistence: PersistenceManager,
        router_agent: RouterAgent,
        investigator_agent: InvestigationAgent,
        rca_writer_agent: RCAWriterAgent,
        approval_manager: ApprovalManager,
        jira_adapter: JiraAdapter,
    ):
        self.persistence = persistence
        self.router_agent = router_agent
        self.investigator_agent = investigator_agent
        self.rca_writer_agent = rca_writer_agent
        self.approval_manager = approval_manager
        self.jira_adapter = jira_adapter

    def supervisor_router(self, payload):
        state = GraphState.from_dict(payload)
        if not state.case_id:
            state.case_id = "case-{0}".format(uuid4().hex[:12])
        state.similar_cases = self.persistence.fetch_similar_cases(limit=5)
        route_result = self.router_agent.route(state.incident, state.similar_cases)
        state.issue_classification = route_result["issue_classification"]
        state.route = route_result["route"]
        state.investigation_required = route_result["investigation_required"]
        state.status = "triaged"
        self.persistence.save_case_state(state)
        return state.to_dict()

    def investigation(self, payload):
        state = GraphState.from_dict(payload)
        state.status = "investigating"
        state = self.investigator_agent.run(state)
        if state.case_id:
            for step in state.investigation_steps:
                self.persistence.save_step(state.case_id, step)
            for finding in state.findings:
                self.persistence.save_finding(state.case_id, finding)
        self.persistence.save_case_state(state)
        return state.to_dict()

    def search_approval_gate(self, payload):
        state = GraphState.from_dict(payload)
        query = state.pending_search_query
        if not query:
            return state.to_dict()

        approval = self.approval_manager.request_search_approval(
            issue_summary=state.incident.issue_text,
            tool_name=state.pending_tool_name or "unknown-tool",
            action_input=state.pending_tool_input,
            reason=state.pending_search_reason or "Additional evidence is required.",
        )
        state = self.apply_search_approval(state, approval)
        return state.to_dict()

    def rca_documentation(self, payload):
        state = GraphState.from_dict(payload)
        state.status = "drafting_rca"
        state = self.rca_writer_agent.write(state)
        self.persistence.save_case_state(state)
        return state.to_dict()

    def final_approval_gate(self, payload):
        state = GraphState.from_dict(payload)
        if state.escalation_required:
            self.persistence.save_case_state(state)
            return state.to_dict()
        report = state.rca_report
        if report is None:
            raise ValueError("RCA report must exist before final approval.")

        evidence_text = "\n".join("- {0}".format(item.summary) for item in state.evidence[:4]) or "- No evidence"
        next_steps = "\n".join("- {0}".format(item) for item in report.next_steps)
        approval = self.approval_manager.request_final_approval(
            issue_summary=state.incident.issue_text,
            evidence=evidence_text,
            likely_cause=report.likely_cause,
            next_steps=next_steps,
        )
        state = self.apply_final_approval(state, approval)
        return state.to_dict()

    def apply_search_approval(self, state: GraphState, approval: ApprovalRecord) -> GraphState:
        """Apply a search approval decision to the current state."""

        query = state.pending_search_query
        if not query and not state.pending_tool_name:
            return state

        state.approvals.append(approval)
        if state.case_id:
            self.persistence.save_approval(state.case_id, approval)

        if approval.action in {"approved", "auto_approved", "edited"}:
            state = self.investigator_agent.execute_approved_action(state)
            if state.case_id and state.investigation_steps:
                self.persistence.save_step(state.case_id, state.investigation_steps[-1])
                for finding in state.findings:
                    self.persistence.save_finding(state.case_id, finding)
        else:
            denied_tool = state.pending_tool_name or "unknown-tool"
            state.pending_tool_name = None
            state.pending_tool_role = None
            state.pending_tool_input = {}
            state.search_approval_required = False
            state.pending_search_query = None
            state.pending_search_reason = None
            state.recommended_next_actions.append("Search was not approved; continue with scoped evidence or human review.")
            state.escalation_required = True
            if approval.action == "pending":
                state.escalation_reason = "The investigation could not reach a human approver for the required MCP action '{0}'.".format(denied_tool)
            else:
                state.escalation_reason = "A required MCP action '{0}' was not approved by a human.".format(denied_tool)

        self.persistence.save_case_state(state)
        return state

    def apply_final_approval(self, state: GraphState, approval: ApprovalRecord) -> GraphState:
        """Apply a final RCA approval decision to the current state."""

        report = state.rca_report
        if report is None:
            raise ValueError("RCA report must exist before applying final approval.")

        state.approvals.append(approval)
        if approval.action == "edited" and approval.edited_conclusion:
            report.engineer_summary = approval.edited_conclusion
            report.customer_summary = approval.edited_conclusion
        if approval.action in {"approved", "auto_approved", "edited"}:
            state.status = "approved"
        else:
            state.status = "needs_review"
            state.escalation_required = True
            if approval.action == "pending":
                state.escalation_reason = "The investigation reached final review without a human approver."
            else:
                state.escalation_reason = "The final RCA or next actions were not approved by the human reviewer."
        state = self.rca_writer_agent.refresh_report_sections(state)
        if state.case_id:
            self.persistence.save_approval(state.case_id, approval)
        self.persistence.save_case_state(state)
        return state

    def jira_escalation(self, payload):
        state = GraphState.from_dict(payload)
        if not state.escalation_required or state.jira_ticket or state.rca_report is None:
            return state.to_dict()

        if not state.case_id:
            raise ValueError("Cannot create a Jira escalation without a case ID.")

        ticket = self.jira_adapter.create_blocked_investigation_ticket(
            case_id=state.case_id,
            summary_of_issue=state.rca_report.summary_of_issue,
            issue_breakdown=state.rca_report.issue_breakdown,
            actions_taken=state.rca_report.actions_taken,
            human_approvals=state.rca_report.human_approvals,
            next_steps=state.rca_report.next_steps,
            blocked_reason=state.escalation_reason or "The workflow could not safely proceed.",
            severity=state.incident.severity,
        )
        state.jira_ticket = ticket
        state.status = "escalated_to_jira"
        self.persistence.save_case_state(state)
        return state.to_dict()

    def finalize_case(self, payload):
        state = GraphState.from_dict(payload)
        if state.rca_report is None:
            raise ValueError("Cannot finalize without an RCA report.")
        if state.case_id:
            self.persistence.save_report(state.case_id, state.rca_report)
        state.status = "closed" if state.status == "approved" else state.status
        state = self.rca_writer_agent.finalize_summary(state)
        self.persistence.save_case_state(state)
        return state.to_dict()
