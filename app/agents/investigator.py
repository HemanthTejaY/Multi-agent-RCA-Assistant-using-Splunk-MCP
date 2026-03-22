"""ReAct-style investigation agent over discovered MCP tools."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app.policy import MCPToolPolicy
from app.models import EvidenceItem, Finding, InvestigationStep
from app.state import GraphState


class InvestigationAgent:
    """Bounded investigation loop over Splunk tools."""

    def __init__(self, tool_registry, policy: MCPToolPolicy, default_timerange: str, max_steps: int = 4):
        self.tool_registry = tool_registry
        self.policy = policy
        self.default_timerange = default_timerange
        self.max_steps = max_steps

    def run(self, state: GraphState) -> GraphState:
        step_number = len(state.investigation_steps) + 1
        tool_plan = self._build_tool_plan(state)

        for tool_name, action_input, thought, role in tool_plan:
            if len(state.investigation_steps) >= self.max_steps:
                break

            decision = self.policy.evaluate(tool_name, role=role)
            if decision.decision == "deny":
                state.investigation_steps.append(
                    InvestigationStep(
                        step_number=step_number,
                        thought=thought,
                        action=tool_name,
                        action_input=action_input,
                        evidence_summary=decision.reason,
                        status="rejected",
                    )
                )
                state.escalation_required = True
                state.escalation_reason = "Policy denied the required MCP action '{0}'.".format(tool_name)
                break
            if decision.decision == "require_approval":
                state.pending_tool_name = tool_name
                state.pending_tool_role = role
                state.pending_tool_input = {str(key): str(value) for key, value in action_input.items()}
                state.pending_search_query = action_input.get("search_query") or action_input.get("query")
                state.pending_search_reason = "{0} Policy reason: {1}".format(thought, decision.reason)
                state.search_approval_required = True
                break

            tool = self.tool_registry.get_tool(tool_name)
            result = tool.invoke(action_input)
            evidence_summary = self._summarize_tool_result(tool_name, result)
            state.evidence.append(
                EvidenceItem(
                    source="splunk",
                    tool_name=tool_name,
                    summary=evidence_summary,
                    raw_reference=result,
                )
            )
            state.investigation_steps.append(
                InvestigationStep(
                    step_number=step_number,
                    thought=thought,
                    action=tool_name,
                    action_input=action_input,
                    evidence_summary=evidence_summary,
                )
            )
            step_number += 1

        state.findings = self._derive_findings(state)
        if state.findings:
            state.likely_cause = state.findings[0].likely_cause
            state.confidence_level = state.findings[0].confidence
            state.recommended_next_actions = self._recommend_actions(state.findings[0])
        else:
            state.likely_cause = "Insufficient evidence to determine a likely cause."
            state.confidence_level = 0.2
            state.recommended_next_actions = [
                "Request more scoped service context or a correlation ID.",
                "Review the case with a human operator before drawing conclusions.",
            ]

        if state.confidence_level < 0.5 and not state.search_approval_required:
            state.escalation_required = True
            state.escalation_reason = "The investigation exhausted safe actions without enough confidence to proceed."

        return state

    def execute_approved_action(self, state: GraphState) -> GraphState:
        """Execute a human-approved MCP action."""

        tool_name = state.pending_tool_name
        if not tool_name:
            return state
        action_input = dict(state.pending_tool_input)
        if not action_input:
            state.escalation_required = True
            state.escalation_reason = "No pending MCP action input was available after approval."
            return state
        result = self.tool_registry.get_tool(tool_name).invoke(action_input)
        evidence_summary = self._summarize_tool_result(tool_name, result)
        step_number = len(state.investigation_steps) + 1
        state.evidence.append(
            EvidenceItem(
                source="splunk",
                tool_name=tool_name,
                summary=evidence_summary,
                raw_reference=result,
            )
        )
        state.investigation_steps.append(
            InvestigationStep(
                step_number=step_number,
                thought=state.pending_search_reason or "Human-approved search to gather more evidence.",
                action=tool_name,
                action_input=action_input,
                evidence_summary=evidence_summary,
            )
        )
        state.pending_tool_name = None
        state.pending_tool_role = None
        state.pending_tool_input = {}
        state.pending_search_query = None
        state.pending_search_reason = None
        state.search_approval_required = False
        state.findings = self._derive_findings(state)
        if state.findings:
            state.likely_cause = state.findings[0].likely_cause
            state.confidence_level = state.findings[0].confidence
            state.recommended_next_actions = self._recommend_actions(state.findings[0])
        if state.confidence_level < 0.5:
            state.escalation_required = True
            state.escalation_reason = "The approved search still did not produce enough evidence to continue safely."
        return state

    def _build_tool_plan(self, state: GraphState) -> List[Tuple[str, Dict[str, Any], str, Optional[str]]]:
        incident = state.incident
        plan: List[Tuple[str, Dict[str, Any], str, Optional[str]]] = []
        role_map = self.tool_registry.available_roles()

        if incident.alert_id and role_map.get("alert_context"):
            plan.append(
                (
                    role_map["alert_context"],
                    {"alert_id": incident.alert_id},
                    "Start with alert context to anchor the investigation in a known signal.",
                    None,
                )
            )
        if incident.service_name and role_map.get("recent_errors"):
            plan.append(
                (
                    role_map["recent_errors"],
                    {"service_name": incident.service_name},
                    "Pull recent scoped service errors before considering broader searches.",
                    None,
                )
            )
        if incident.correlation_id and role_map.get("correlation_lookup"):
            plan.append(
                (
                    role_map["correlation_lookup"],
                    {"correlation_id": incident.correlation_id},
                    "Use the correlation ID to connect events in the same incident flow.",
                    None,
                )
            )

        broad_query = self._build_search_query(state)
        if broad_query and role_map.get("broad_search"):
            plan.append(
                (
                    role_map["broad_search"],
                    {
                        "search_query": broad_query,
                        "earliest_time": self.default_timerange,
                        "latest_time": "now",
                        "max_results": 10,
                    },
                    "Use a broader search only if scoped evidence still leaves the likely cause uncertain.",
                    "broad_search",
                )
            )
        return plan[: self.max_steps]

    def _build_search_query(self, state: GraphState) -> Optional[str]:
        incident = state.incident
        service_name = incident.service_name
        text = incident.issue_text.lower()

        if not service_name:
            if "auth" in text or "login" in text:
                return "authentication failures across services"
            if "latency" in text or "timeout" in text:
                return "timeout latency errors across services"
            return None

        if "auth" in text or "login" in text:
            return "service:{0} authentication jwt key rotation".format(service_name)
        if "latency" in text or "timeout" in text:
            return "service:{0} timeout latency database".format(service_name)
        return "service:{0} error incident".format(service_name)

    def _summarize_tool_result(self, tool_name: str, result: Dict[str, object]) -> str:
        normalized_name = tool_name.lower()
        if "alert" in normalized_name and "context" in normalized_name:
            return "{0}: {1}".format(result.get("title", "Alert"), result.get("summary", "No summary"))
        if any(token in normalized_name for token in ["search", "error", "event"]):
            events = result.get("events", [])
            if not events:
                return "No relevant events were returned from {0}.".format(tool_name)
            first_event = events[0]
            return "{0} returned {1} event(s); representative evidence: {2}".format(
                tool_name,
                result.get("result_count", len(events)),
                first_event.get("message", "No message"),
            )
        return "Tool result captured from {0}.".format(tool_name)

    def _derive_findings(self, state: GraphState) -> List[Finding]:
        evidence_text = " ".join(item.summary.lower() for item in state.evidence)
        summaries = [item.summary for item in state.evidence]
        limitations: List[str] = []

        if not state.evidence:
            limitations.append("No tool evidence was collected.")
            return [
                Finding(
                    title="Insufficient evidence",
                    category="needs_human_review",
                    likely_cause="No likely cause identified with current evidence.",
                    confidence=0.2,
                    supporting_evidence=[],
                    limitations=limitations,
                )
            ]

        if any(token in evidence_text for token in ["expired signing key", "jwt validation failed", "key mismatch"]):
            return [
                Finding(
                    title="Authentication key rotation issue",
                    category="authentication_configuration",
                    likely_cause="Authentication failures are likely caused by an expired or mismatched signing key after rotation.",
                    confidence=0.91,
                    supporting_evidence=summaries[:3],
                    limitations=limitations,
                )
            ]

        if any(token in evidence_text for token in ["connection pool saturation", "database timeout", "dependency latency spike"]):
            return [
                Finding(
                    title="Downstream database contention",
                    category="dependency_timeout",
                    likely_cause="The incident is most consistent with downstream database contention or timeouts impacting the service.",
                    confidence=0.88,
                    supporting_evidence=summaries[:3],
                    limitations=limitations,
                )
            ]

        if any(token in evidence_text for token in ["latency", "timeout"]):
            return [
                Finding(
                    title="Performance degradation",
                    category="performance_issue",
                    likely_cause="The service is showing performance degradation, but the precise source is still uncertain.",
                    confidence=0.62,
                    supporting_evidence=summaries[:3],
                    limitations=["Evidence suggests latency, but the root dependency is not fully isolated."],
                )
            ]

        return [
            Finding(
                title="Inconclusive finding",
                category="needs_human_review",
                likely_cause="Evidence is inconclusive and requires additional human review.",
                confidence=0.35,
                supporting_evidence=summaries[:3],
                limitations=["The available evidence does not isolate a specific fault domain."],
            )
        ]

    def _recommend_actions(self, finding: Finding) -> List[str]:
        if finding.category == "authentication_configuration":
            return [
                "Validate the active signing key and rotation status across identity components.",
                "Roll out a synchronized key refresh to affected auth services.",
                "Monitor authentication failure rate during recovery.",
            ]
        if finding.category == "dependency_timeout":
            return [
                "Inspect database health and connection pool saturation on the affected dependency.",
                "Throttle or queue non-critical traffic while dependency latency recovers.",
                "Review recent deploys or workload spikes affecting the downstream system.",
            ]
        return [
            "Collect additional service-scoped telemetry before final remediation.",
            "Keep the incident under human review until confidence improves.",
        ]
