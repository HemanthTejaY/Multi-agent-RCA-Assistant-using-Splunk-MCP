"""Application configuration and shared factories."""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "splunk-mcp-multi-agent-rca-assistant"
    app_log_level: str = "INFO"

    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: Optional[str] = None

    splunk_adapter_mode: str = "mock"
    splunk_mcp_base_url: Optional[str] = None
    splunk_mcp_api_key: Optional[str] = None
    splunk_mcp_stdio_command: Optional[str] = None
    splunk_mcp_timeout_seconds: int = 20

    postgres_dsn: str = "postgresql://postgres:postgres@localhost:5432/rca_assistant"
    redis_url: str = "redis://localhost:6379/0"

    default_timerange: str = "-30m"
    max_investigation_steps: int = Field(default=4, ge=1, le=8)
    allow_interactive_approval: bool = True
    mcp_allow_roles: str = "tool_discovery,alert_context,recent_errors,correlation_lookup"
    mcp_approval_required_roles: str = "broad_search,index_listing"
    mcp_deny_roles: str = ""
    mcp_allow_tool_names: str = ""
    mcp_approval_required_tool_names: str = ""
    mcp_deny_tool_names: str = ""
    mcp_deny_tool_name_patterns: str = "create_,delete_,update_,write_,insert_,drop_,truncate_,grant_,revoke_,rotate_,restart_,stop_,start_"
    mcp_unknown_tool_policy: str = "require_approval"
    jira_enabled: bool = False
    jira_base_url: Optional[str] = None
    jira_email: Optional[str] = None
    jira_api_token: Optional[str] = None
    jira_project_key: str = "OPS"
    jira_issue_type: str = "Task"
    jira_default_assignee: Optional[str] = None
    jira_labels: str = "rca-assistant,observability"
    jira_components: str = "support-ai"
    jira_timeout_seconds: int = 20

    @field_validator("splunk_adapter_mode")
    @classmethod
    def normalize_adapter_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"mock", "http", "stdio"}:
            raise ValueError("SPLUNK_ADAPTER_MODE must be 'mock', 'http', or 'stdio'")
        return normalized

    @field_validator("app_log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @field_validator("mcp_unknown_tool_policy")
    @classmethod
    def normalize_unknown_tool_policy(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"require_approval", "deny"}:
            raise ValueError("MCP_UNKNOWN_TOOL_POLICY must be 'require_approval' or 'deny'")
        return normalized


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings for the current process."""

    return Settings()


def build_chat_model(settings: Optional[Settings] = None):
    """Return a ChatOpenAI model when the package and credentials are available."""

    runtime_settings = settings or get_settings()
    if not runtime_settings.openai_api_key:
        return None

    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        return None

    return ChatOpenAI(
        model=runtime_settings.openai_model,
        api_key=runtime_settings.openai_api_key,
        base_url=runtime_settings.openai_base_url,
        temperature=0,
    )
