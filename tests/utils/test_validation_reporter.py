"""Regression tests for complete, reproducible validation evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from scripts.utils.validation_reporter import (
    SpecValidationResult,
    ValidationReporter,
    ValidationStats,
)


def _stats() -> ValidationStats:
    discrepancies = [
        {
            "spec": f"spec-{index}.json",
            "path": f"/objects/{index}",
            "method": "GET",
            "issues": [f"issue {index}-a", f"issue {index}-b"],
        }
        for index in range(105)
    ]
    unresolved = [
        {
            "spec": "fixture.json",
            "method": "GET",
            "path": f"/unresolved/{index}",
            "reason": f"reason {index}",
        }
        for index in range(30)
    ]
    return ValidationStats(
        specs_processed=1,
        total_endpoints=140,
        endpoints_eligible=135,
        endpoints_safely_resolved=105,
        endpoints_unresolved=30,
        endpoints_executed=105,
        unresolved_endpoints=unresolved,
        endpoints_validated=105,
        endpoints_available=105,
        schema_matches=0,
        spec_results=[
            SpecValidationResult(
                filename="fixture.json",
                endpoints_total=140,
                endpoints_eligible=135,
                endpoints_safely_resolved=105,
                endpoints_unresolved=30,
                endpoints_executed=105,
                unresolved_endpoints=[
                    {key: value for key, value in item.items() if key != "spec"}
                    for item in unresolved
                ],
                errors=[f"error {index}" for index in range(8)],
            )
        ],
        discrepancies=discrepancies,
    )


def test_validation_evidence_is_complete_and_uses_producer_issue_schema() -> None:
    reporter = ValidationReporter(_stats())

    report = reporter.to_dict()
    markdown = reporter.to_markdown()

    assert len(report["discrepancies"]) == 105
    assert report["summary"]["total_endpoints"] == 140
    assert report["summary"]["endpoints_eligible"] == 135
    assert report["summary"]["endpoints_safely_resolved"] == 105
    assert report["summary"]["endpoints_unresolved"] == 30
    assert report["summary"]["endpoints_executed"] == 105
    assert len(report["unresolved_endpoints"]) == 30
    assert report["specs"][0]["endpoints_eligible"] == 135
    assert report["specs"][0]["endpoints_safely_resolved"] == 105
    assert report["specs"][0]["endpoints_unresolved"] == 30
    assert report["specs"][0]["endpoints_executed"] == 105
    assert len(report["specs"][0]["unresolved_endpoints"]) == 30
    assert report["specs"][0]["errors"] == [f"error {index}" for index in range(8)]
    assert "issue 104-a" in markdown
    assert "issue 104-b" in markdown
    assert "error 7" in markdown
    assert "/unresolved/29" in markdown
    assert "reason 29" in markdown
    assert "Unknown discrepancy" not in markdown
    assert "more discrepancies" not in markdown


def test_validation_evidence_is_byte_identical_for_identical_stats(tmp_path: Path) -> None:
    first = ValidationReporter(_stats())
    second = ValidationReporter(_stats())
    first_markdown = tmp_path / "first.md"
    first_json = tmp_path / "first.json"
    second_markdown = tmp_path / "second.md"
    second_json = tmp_path / "second.json"

    first.generate_all(first_markdown, first_json)
    second.generate_all(second_markdown, second_json)

    assert first_json.read_bytes() == second_json.read_bytes()
    assert first_markdown.read_bytes() == second_markdown.read_bytes()
    assert "timestamp" not in first.to_dict()
    assert "**Generated**" not in first.to_markdown()


def test_validation_report_makes_no_unmeasured_server_variable_claims() -> None:
    markdown = ValidationReporter(_stats()).to_markdown().lower()

    assert "server variable" not in markdown
    assert "test environment" not in markdown
