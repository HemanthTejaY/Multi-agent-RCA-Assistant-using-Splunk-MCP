"""CLI entrypoint for the RCA assistant prototype."""

from __future__ import annotations

import argparse
import json
from typing import Optional

from app.agents.investigator import InvestigationAgent
from app.agents.rca_writer import RCAWriterAgent
from app.agents.router import RouterAgent
from app.config import Settings, get_settings
from app.db import DatabaseClient
from app.graph.nodes import WorkflowNodes
from app.graph.workflow import build_workflow
from app.hitl.approval import ApprovalManager
from app.logging_config import configure_logging
from app.memory.persistence import InMemoryMemoryStore, PersistenceManager, RedisMemoryStore
from app.models import IncidentInput
from app.policy import MCPToolPolicy
from app.state import GraphState
from app.tools.jira_adapter import JiraAdapter
from app.tools.splunk_adapter import MCPToolRegistry, build_mcp_client


def build_default_persistence(settings: Settings) -> PersistenceManager:
    """Create production-shaped persistence providers."""

    return PersistenceManager(
        db_client=DatabaseClient(settings.postgres_dsn),
        memory_store=RedisMemoryStore(settings.redis_url),
    )


def build_runtime(
    settings: Settings,
    approval_manager: Optional[ApprovalManager] = None,
    persistence_manager: Optional[PersistenceManager] = None,
    adapter=None,
):
    """Create the workflow runtime."""

    nodes, persistence = build_runtime_components(
        settings,
        approval_manager=approval_manager,
        persistence_manager=persistence_manager,
        adapter=adapter,
    )
    return build_workflow(nodes), persistence


def build_runtime_components(
    settings: Settings,
    approval_manager: Optional[ApprovalManager] = None,
    persistence_manager: Optional[PersistenceManager] = None,
    adapter=None,
):
    """Create reusable workflow components for CLI and UI entrypoints."""

    persistence = persistence_manager or build_default_persistence(settings)
    persistence.initialize()
    mcp_client = adapter or build_mcp_client(settings)
    jira_adapter = JiraAdapter(
        enabled=settings.jira_enabled,
        base_url=settings.jira_base_url,
        email=settings.jira_email,
        api_token=settings.jira_api_token,
        project_key=settings.jira_project_key,
        issue_type=settings.jira_issue_type,
        default_assignee=settings.jira_default_assignee,
        labels=[item.strip() for item in settings.jira_labels.split(",") if item.strip()],
        components=[item.strip() for item in settings.jira_components.split(",") if item.strip()],
        timeout_seconds=settings.jira_timeout_seconds,
    )
    tool_registry = MCPToolRegistry(mcp_client)
    tool_policy = MCPToolPolicy(settings=settings, tool_registry=tool_registry)
    nodes = WorkflowNodes(
        persistence=persistence,
        router_agent=RouterAgent(),
        investigator_agent=InvestigationAgent(
            tool_registry=tool_registry,
            policy=tool_policy,
            default_timerange=settings.default_timerange,
            max_steps=settings.max_investigation_steps,
        ),
        rca_writer_agent=RCAWriterAgent(),
        approval_manager=approval_manager or ApprovalManager(interactive=settings.allow_interactive_approval),
        jira_adapter=jira_adapter,
    )
    return nodes, persistence


def run_case(
    incident: IncidentInput,
    settings: Optional[Settings] = None,
    approval_manager: Optional[ApprovalManager] = None,
    persistence_manager: Optional[PersistenceManager] = None,
    adapter=None,
) -> GraphState:
    """Run the end-to-end incident workflow."""

    runtime_settings = settings or get_settings()
    workflow, _ = build_runtime(
        runtime_settings,
        approval_manager=approval_manager,
        persistence_manager=persistence_manager,
        adapter=adapter,
    )
    initial_state = GraphState(incident=incident)
    final_state = workflow.invoke(initial_state.to_dict())
    return GraphState.from_dict(final_state)


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(description="Run the Splunk MCP RCA assistant prototype.")
    parser.add_argument("issue_text", help="Incident or support issue description.")
    parser.add_argument("--service-name", default=None)
    parser.add_argument("--severity", default="medium")
    parser.add_argument("--environment", default="production")
    parser.add_argument("--correlation-id", default=None)
    parser.add_argument("--alert-id", default=None)
    parser.add_argument("--metadata-json", default="{}")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--search-approval", choices=["approved", "rejected", "edited", "pending"], default="approved")
    parser.add_argument("--final-approval", choices=["approved", "rejected", "edited", "pending"], default="approved")
    parser.add_argument("--approval-note", default=None)
    parser.add_argument("--approval-edit", default=None)
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint."""

    settings = get_settings()
    configure_logging(settings.app_log_level)
    args = parse_args()

    approval_manager = ApprovalManager(
        interactive=(settings.allow_interactive_approval and not args.non_interactive),
        preset_search_action=args.search_approval,
        preset_final_action=args.final_approval,
        preset_note=args.approval_note,
        preset_edit=args.approval_edit,
    )
    incident = IncidentInput(
        issue_text=args.issue_text,
        service_name=args.service_name,
        severity=args.severity,
        environment=args.environment,
        correlation_id=args.correlation_id,
        alert_id=args.alert_id,
        metadata=json.loads(args.metadata_json),
    )
    final_state = run_case(incident=incident, settings=settings, approval_manager=approval_manager)
    print(json.dumps(final_state.final_summary.model_dump(), indent=2))


if __name__ == "__main__":
    main()
