"""Lightweight scenario-based evaluation runner."""

from __future__ import annotations

import json
from pathlib import Path

from app.config import get_settings
from app.hitl.approval import ApprovalManager
from app.main import run_case
from app.db import InMemoryDatabaseClient
from app.memory.persistence import InMemoryMemoryStore, PersistenceManager
from app.models import IncidentInput
from app.tools.splunk_adapter import MockMCPClient


def load_scenarios():
    """Load eval scenarios from disk."""

    scenarios_dir = Path(__file__).resolve().parent / "scenarios"
    for path in sorted(scenarios_dir.glob("*.json")):
        with path.open("r", encoding="utf-8") as handle:
            yield json.load(handle)


def evaluate_scenario(scenario):
    """Run one scenario and return validation metadata."""

    settings = get_settings()
    persistence = PersistenceManager(
        db_client=InMemoryDatabaseClient(),
        memory_store=InMemoryMemoryStore(),
    )
    approval_manager = ApprovalManager(interactive=False)
    incident = IncidentInput(**scenario["incident"])
    final_state = run_case(
        incident=incident,
        settings=settings,
        approval_manager=approval_manager,
        persistence_manager=persistence,
        adapter=MockMCPClient(),
    )
    summary = final_state.final_summary.model_dump()
    missing_fields = [field for field in scenario["expected_output_fields"] if field not in summary]
    matched_category = any(
        category.replace("_", " ") in summary["likely_cause"].lower() or category in final_state.findings[0].category
        for category in scenario["expected_likely_cause_categories"]
    )
    return {
        "scenario": scenario["name"],
        "passed": not missing_fields and matched_category,
        "missing_fields": missing_fields,
        "likely_cause": summary["likely_cause"],
        "human_approval_status": summary["human_approval_status"],
    }


def main() -> None:
    """Run all evaluation scenarios."""

    results = [evaluate_scenario(scenario) for scenario in load_scenarios()]
    passed = sum(1 for item in results if item["passed"])
    print(json.dumps({"passed": passed, "total": len(results), "results": results}, indent=2))


if __name__ == "__main__":
    main()
