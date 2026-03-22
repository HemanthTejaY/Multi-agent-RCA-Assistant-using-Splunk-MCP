"""MCP-native Splunk integration, discovery, and tool registry."""

from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.tools.mock_splunk_data import ALERT_CONTEXT, MOCK_EVENTS

LOGGER = logging.getLogger(__name__)

try:
    import requests
except ImportError:  # pragma: no cover - optional dependency
    requests = None

try:
    from langchain_core.tools import StructuredTool
except ImportError:  # pragma: no cover - optional dependency
    StructuredTool = None


class SimpleTool:
    """Minimal fallback for environments without LangChain."""

    def __init__(self, name: str, description: str, func, metadata: Optional[Dict[str, Any]] = None):
        self.name = name
        self.description = description
        self._func = func
        self.metadata = metadata or {}

    def invoke(self, payload: Dict[str, Any]):
        return self._func(**payload)


@dataclass
class MCPToolSpec:
    """Discovered MCP tool metadata."""

    name: str
    description: str
    input_schema: Dict[str, Any] = field(default_factory=dict)
    annotations: Dict[str, Any] = field(default_factory=dict)


class MCPClient:
    """Base MCP client interface."""

    def list_tools(self) -> List[MCPToolSpec]:
        raise NotImplementedError

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        raise NotImplementedError


class MockMCPClient(MCPClient):
    """Local MCP-like client exposing realistic Splunk tools and results."""

    def __init__(self):
        self.events = list(MOCK_EVENTS)
        self.tool_specs = [
            MCPToolSpec(
                name="search_splunk",
                description="Execute a Splunk search query with earliest/latest time bounds.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "search_query": {"type": "string"},
                        "earliest_time": {"type": "string"},
                        "latest_time": {"type": "string"},
                        "max_results": {"type": "integer"},
                    },
                },
            ),
            MCPToolSpec(
                name="get_recent_errors",
                description="Get recent warning and error events for a service.",
                input_schema={"type": "object", "properties": {"service_name": {"type": "string"}}},
            ),
            MCPToolSpec(
                name="summarize_alert_context",
                description="Summarize an alert and its affected service.",
                input_schema={"type": "object", "properties": {"alert_id": {"type": "string"}}},
            ),
            MCPToolSpec(
                name="fetch_related_events",
                description="Fetch events related to a correlation identifier.",
                input_schema={"type": "object", "properties": {"correlation_id": {"type": "string"}}},
            ),
            MCPToolSpec(
                name="list_indexes",
                description="List indexes available to the Splunk user.",
                input_schema={"type": "object", "properties": {}},
            ),
            MCPToolSpec(
                name="list_tools",
                description="List all MCP tools and their schemas.",
                input_schema={"type": "object", "properties": {}},
            ),
        ]

    def list_tools(self) -> List[MCPToolSpec]:
        return self.tool_specs

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if tool_name == "list_tools":
            return {
                "tools": [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "inputSchema": tool.input_schema,
                    }
                    for tool in self.tool_specs
                ]
            }
        if tool_name == "search_splunk":
            query = arguments.get("search_query", "")
            max_results = int(arguments.get("max_results", 10))
            query_lower = query.lower()
            matched = []
            for event in self.events:
                haystack = " ".join(
                    [
                        event["service_name"],
                        event["severity"],
                        event["message"],
                        " ".join(event.get("tags", [])),
                        str(event.get("correlation_id", "")),
                        str(event.get("alert_id", "")),
                    ]
                ).lower()
                if all(token in haystack for token in query_lower.split()):
                    matched.append(event)
            return {
                "tool_name": tool_name,
                "search_query": query,
                "earliest_time": arguments.get("earliest_time"),
                "latest_time": arguments.get("latest_time"),
                "result_count": len(matched),
                "events": matched[:max_results],
            }
        if tool_name == "get_recent_errors":
            service_name = arguments["service_name"]
            events = [
                event for event in self.events
                if event["service_name"] == service_name and event["severity"] in {"error", "warn"}
            ]
            return {"service_name": service_name, "result_count": len(events), "events": events[:10]}
        if tool_name == "summarize_alert_context":
            alert_id = arguments["alert_id"]
            return ALERT_CONTEXT.get(
                alert_id,
                {
                    "alert_id": alert_id,
                    "title": "Unknown alert",
                    "severity": "unknown",
                    "summary": "No alert context was found in mock mode.",
                    "affected_service": None,
                },
            )
        if tool_name == "fetch_related_events":
            correlation_id = arguments["correlation_id"]
            events = [event for event in self.events if event.get("correlation_id") == correlation_id]
            return {"correlation_id": correlation_id, "result_count": len(events), "events": events[:10]}
        if tool_name == "list_indexes":
            return {"indexes": ["main", "security", "application", "audit"]}
        raise ValueError("Unsupported mock MCP tool: {0}".format(tool_name))


class HttpMCPClient(MCPClient):
    """HTTP client for MCP servers with API endpoints."""

    def __init__(self, base_url: str, api_key: Optional[str], timeout_seconds: int = 20):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    def list_tools(self) -> List[MCPToolSpec]:
        payload = self._post("/api/v1/list_tools", {})
        return parse_tool_specs(payload)

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._post(
            "/api/v1/{0}".format(tool_name),
            arguments,
        )
        return normalize_mcp_tool_response(tool_name, payload)

    def _post(self, endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if requests is None:
            raise RuntimeError("requests is required to use the HTTP MCP client.")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer {0}".format(self.api_key)
        response = requests.post(
            "{0}/{1}".format(self.base_url, endpoint.lstrip("/")),
            json=payload,
            headers=headers,
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()


class StdioMCPClient(MCPClient):
    """Best-effort STDIO MCP client wrapper for command-driven integrations."""

    def __init__(self, command: str, timeout_seconds: int = 20):
        self.command = command
        self.timeout_seconds = timeout_seconds

    def list_tools(self) -> List[MCPToolSpec]:
        payload = self._invoke({"method": "list_tools", "params": {}})
        return parse_tool_specs(payload)

    def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        payload = self._invoke({"method": tool_name, "params": arguments})
        return normalize_mcp_tool_response(tool_name, payload)

    def _invoke(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        process = subprocess.run(
            self.command,
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            shell=True,
            timeout=self.timeout_seconds,
            check=True,
        )
        if not process.stdout.strip():
            return {}
        return json.loads(process.stdout)


def parse_tool_specs(payload: Dict[str, Any]) -> List[MCPToolSpec]:
    """Parse MCP tool listings into internal models."""

    raw_tools = payload.get("tools") or payload.get("result") or []
    tool_specs: List[MCPToolSpec] = []
    for item in raw_tools:
        tool_specs.append(
            MCPToolSpec(
                name=item.get("name", ""),
                description=item.get("description", ""),
                input_schema=item.get("inputSchema") or item.get("input_schema") or {},
                annotations=item.get("annotations") or {},
            )
        )
    return tool_specs


def normalize_mcp_tool_response(tool_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize different MCP server response shapes."""

    if "result" in payload and isinstance(payload["result"], dict):
        result = payload["result"]
    else:
        result = payload
    if "content" in result and isinstance(result["content"], list):
        content = result["content"]
        if len(content) == 1 and isinstance(content[0], dict):
            text_payload = content[0].get("text")
            if text_payload:
                try:
                    parsed = json.loads(text_payload)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    return {"tool_name": tool_name, "text": text_payload}
    if "tool_name" not in result:
        result["tool_name"] = tool_name
    return result


def _tool_factory(name: str, description: str, func, metadata: Optional[Dict[str, Any]] = None):
    if StructuredTool is not None:
        return StructuredTool.from_function(name=name, description=description, func=func)
    return SimpleTool(name=name, description=description, func=func, metadata=metadata)


class MCPToolRegistry:
    """Registry for discovered MCP tools and canonical investigation roles."""

    def __init__(self, client: MCPClient):
        self.client = client
        self.tool_specs = client.list_tools()
        self.tools = {
            spec.name: _tool_factory(
                name=spec.name,
                description=spec.description or "MCP-discovered Splunk capability",
                func=self._build_callable(spec.name),
                metadata={"input_schema": spec.input_schema},
            )
            for spec in self.tool_specs
        }
        self.role_map = self._build_role_map(self.tool_specs)

    def _build_callable(self, tool_name: str):
        def caller(**kwargs):
            return self.client.call_tool(tool_name, kwargs)

        return caller

    def _build_role_map(self, tool_specs: List[MCPToolSpec]) -> Dict[str, str]:
        role_map: Dict[str, str] = {}
        for spec in tool_specs:
            name = spec.name.lower()
            if name in {"search_splunk", "search_logs", "run_search"}:
                role_map["broad_search"] = spec.name
            elif "alert" in name and "context" in name:
                role_map["alert_context"] = spec.name
            elif "recent" in name and "error" in name:
                role_map["recent_errors"] = spec.name
            elif "correlation" in name or "related_events" in name:
                role_map["correlation_lookup"] = spec.name
            elif name == "list_indexes":
                role_map["index_listing"] = spec.name
            elif name == "list_tools":
                role_map["tool_discovery"] = spec.name
        return role_map

    def get_tool(self, name: str):
        return self.tools[name]

    def get_spec(self, name: str) -> Optional[MCPToolSpec]:
        for spec in self.tool_specs:
            if spec.name == name:
                return spec
        return None

    def role_for_tool(self, name: str) -> Optional[str]:
        for role, tool_name in self.role_map.items():
            if tool_name == name:
                return role
        return None

    def tool_for_role(self, role: str):
        tool_name = self.role_map.get(role)
        return self.tools.get(tool_name) if tool_name else None

    def available_roles(self) -> Dict[str, str]:
        return dict(self.role_map)

    def all_tools(self) -> List[object]:
        return list(self.tools.values())


def build_mcp_client(settings) -> MCPClient:
    """Create the appropriate MCP client from runtime settings."""

    mode = settings.splunk_adapter_mode
    if mode == "http":
        if not settings.splunk_mcp_base_url:
            raise ValueError("SPLUNK_MCP_BASE_URL is required when SPLUNK_ADAPTER_MODE=http")
        return HttpMCPClient(
            base_url=settings.splunk_mcp_base_url,
            api_key=settings.splunk_mcp_api_key,
            timeout_seconds=settings.splunk_mcp_timeout_seconds,
        )
    if mode == "stdio":
        if not settings.splunk_mcp_stdio_command:
            raise ValueError("SPLUNK_MCP_STDIO_COMMAND is required when SPLUNK_ADAPTER_MODE=stdio")
        return StdioMCPClient(
            command=settings.splunk_mcp_stdio_command,
            timeout_seconds=settings.splunk_mcp_timeout_seconds,
        )
    return MockMCPClient()
