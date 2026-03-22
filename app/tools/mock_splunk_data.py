"""Mock Splunk data used for local execution and evals."""

from __future__ import annotations

from typing import Any, Dict, List


MOCK_EVENTS: List[Dict[str, Any]] = [
    {
        "timestamp": "2026-03-21T13:40:00Z",
        "service_name": "payment-service",
        "severity": "error",
        "message": "Upstream database timeout while processing charge request",
        "correlation_id": "corr-payment-001",
        "alert_id": "alert-payment-latency",
        "tags": ["latency", "database", "timeout"],
        "context": {"p95_ms": 3400, "dependency": "orders-db"},
    },
    {
        "timestamp": "2026-03-21T13:41:00Z",
        "service_name": "payment-service",
        "severity": "warn",
        "message": "Connection pool saturation detected for orders-db",
        "correlation_id": "corr-payment-001",
        "alert_id": "alert-payment-latency",
        "tags": ["latency", "connection-pool", "database"],
        "context": {"active_connections": 120, "pool_limit": 120},
    },
    {
        "timestamp": "2026-03-21T09:10:00Z",
        "service_name": "identity-service",
        "severity": "error",
        "message": "Repeated authentication failures caused by expired signing key",
        "correlation_id": "corr-auth-042",
        "alert_id": "alert-auth-failures",
        "tags": ["authentication", "key-rotation", "login-failure"],
        "context": {"failure_rate": 0.63, "principal_type": "user"},
    },
    {
        "timestamp": "2026-03-21T09:12:00Z",
        "service_name": "identity-service",
        "severity": "warn",
        "message": "JWT validation failed due to key mismatch after rotation",
        "correlation_id": "corr-auth-042",
        "alert_id": "alert-auth-failures",
        "tags": ["authentication", "jwt", "config"],
        "context": {"key_version": "2026-03-20"},
    },
    {
        "timestamp": "2026-03-21T15:55:00Z",
        "service_name": "inventory-api",
        "severity": "error",
        "message": "Database query timeout against catalog-db during inventory refresh",
        "correlation_id": "corr-db-900",
        "alert_id": "alert-db-timeout",
        "tags": ["database", "timeout", "dependency"],
        "context": {"timeout_ms": 5000, "query_name": "refresh_catalog"},
    },
    {
        "timestamp": "2026-03-21T15:55:20Z",
        "service_name": "inventory-api",
        "severity": "warn",
        "message": "Downstream dependency latency spike from catalog-db",
        "correlation_id": "corr-db-900",
        "alert_id": "alert-db-timeout",
        "tags": ["database", "latency", "dependency"],
        "context": {"p99_ms": 5200, "dependency": "catalog-db"},
    },
]


ALERT_CONTEXT: Dict[str, Dict[str, Any]] = {
    "alert-payment-latency": {
        "alert_id": "alert-payment-latency",
        "title": "Payment API latency spike",
        "severity": "high",
        "summary": "Payment transactions exceeded the p95 latency SLO for 12 minutes.",
        "affected_service": "payment-service",
    },
    "alert-auth-failures": {
        "alert_id": "alert-auth-failures",
        "title": "Authentication failure burst",
        "severity": "high",
        "summary": "Login failures rose sharply after a key rotation window.",
        "affected_service": "identity-service",
    },
    "alert-db-timeout": {
        "alert_id": "alert-db-timeout",
        "title": "Database timeout impacting inventory refresh",
        "severity": "critical",
        "summary": "Inventory refresh jobs are timing out against catalog-db.",
        "affected_service": "inventory-api",
    },
}

