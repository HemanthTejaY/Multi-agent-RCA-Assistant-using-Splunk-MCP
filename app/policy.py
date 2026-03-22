"""Central MCP tool policy engine for HITL decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from app.tools.splunk_adapter import MCPToolRegistry, MCPToolSpec


@dataclass(frozen=True)
class ToolPolicyDecision:
    """Outcome of evaluating a tool against policy."""

    decision: str
    reason: str


class MCPToolPolicy:
    """Config-driven policy for discovered MCP tools."""

    def __init__(self, settings, tool_registry: MCPToolRegistry):
        self.settings = settings
        self.tool_registry = tool_registry

        self.allow_roles = _split_csv(settings.mcp_allow_roles)
        self.approval_roles = _split_csv(settings.mcp_approval_required_roles)
        self.deny_roles = _split_csv(settings.mcp_deny_roles)

        self.allow_tool_names = _split_csv(settings.mcp_allow_tool_names)
        self.approval_tool_names = _split_csv(settings.mcp_approval_required_tool_names)
        self.deny_tool_names = _split_csv(settings.mcp_deny_tool_names)
        self.deny_name_patterns = _split_csv(settings.mcp_deny_tool_name_patterns)
        self.unknown_policy = settings.mcp_unknown_tool_policy

    def evaluate(self, tool_name: str, role: Optional[str] = None) -> ToolPolicyDecision:
        """Return allow, require_approval, or deny for a discovered tool."""

        normalized_name = tool_name.lower()
        normalized_role = (role or "").lower()
        spec = self.tool_registry.get_spec(tool_name)

        if normalized_name in self.deny_tool_names:
            return ToolPolicyDecision("deny", "Tool is explicitly denied by name policy.")
        if normalized_role and normalized_role in self.deny_roles:
            return ToolPolicyDecision("deny", "Tool role is explicitly denied by policy.")
        if self._matches_deny_pattern(normalized_name):
            return ToolPolicyDecision("deny", "Tool name matches a denied mutation/admin pattern.")
        if self._spec_looks_mutating(spec):
            return ToolPolicyDecision("deny", "Tool schema or description suggests a mutating/admin action.")

        if normalized_name in self.allow_tool_names:
            return ToolPolicyDecision("allow", "Tool is explicitly allowlisted by name policy.")
        if normalized_role and normalized_role in self.allow_roles:
            return ToolPolicyDecision("allow", "Tool role is allowlisted by policy.")

        if normalized_name in self.approval_tool_names:
            return ToolPolicyDecision("require_approval", "Tool is explicitly marked as approval-required.")
        if normalized_role and normalized_role in self.approval_roles:
            return ToolPolicyDecision("require_approval", "Tool role requires human approval by policy.")

        if self.unknown_policy == "deny":
            return ToolPolicyDecision("deny", "Unknown tools are denied by default policy.")
        return ToolPolicyDecision("require_approval", "Unknown tools require human approval by default policy.")

    def _matches_deny_pattern(self, normalized_name: str) -> bool:
        return any(pattern and pattern in normalized_name for pattern in self.deny_name_patterns)

    def _spec_looks_mutating(self, spec: Optional[MCPToolSpec]) -> bool:
        if spec is None:
            return False
        combined = "{0} {1}".format(spec.name, spec.description).lower()
        forbidden_terms = {
            "create",
            "delete",
            "update",
            "write",
            "insert",
            "drop",
            "truncate",
            "set",
            "grant",
            "revoke",
            "patch",
            "modify",
            "rotate",
            "start",
            "stop",
            "restart",
        }
        return any(term in combined for term in forbidden_terms)


def _split_csv(value: Optional[str]) -> set[str]:
    if not value:
        return set()
    return {item.strip().lower() for item in value.split(",") if item.strip()}
