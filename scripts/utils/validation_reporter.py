# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Validation report generator for live API testing.

Extracts report generation logic from validate.py into a reusable reporter class
supporting both JSON and markdown output formats.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from path_config import PathConfig
from report_base import BaseReporter

logger = logging.getLogger(__name__)


@dataclass
class EndpointResult:
    """Result of validating a single endpoint."""

    path: str
    method: str
    status: str  # available, unavailable, error, skipped, unresolved
    status_code: int | None = None
    schema_match: bool = True
    response_time_ms: float | None = None
    error: str | None = None
    discrepancies: list[str] = field(default_factory=list)


@dataclass
class SpecValidationResult:
    """Result of validating a single specification."""

    filename: str
    endpoints_total: int = 0
    endpoints_eligible: int = 0
    endpoints_safely_resolved: int = 0
    endpoints_unresolved: int = 0
    endpoints_executed: int = 0
    unresolved_endpoints: list[dict[str, str]] = field(default_factory=list)
    endpoints_validated: int = 0
    endpoints_available: int = 0
    endpoints_unavailable: int = 0
    endpoints_skipped: int = 0
    schema_matches: int = 0
    schema_mismatches: int = 0
    endpoint_results: list[EndpointResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class ValidationStats:
    """Aggregate validation statistics."""

    specs_processed: int = 0
    total_endpoints: int = 0
    endpoints_eligible: int = 0
    endpoints_safely_resolved: int = 0
    endpoints_unresolved: int = 0
    endpoints_executed: int = 0
    unresolved_endpoints: list[dict[str, str]] = field(default_factory=list)
    endpoints_validated: int = 0
    endpoints_available: int = 0
    endpoints_unavailable: int = 0
    schema_matches: int = 0
    spec_results: list[SpecValidationResult] = field(default_factory=list)
    discrepancies: list[dict[str, Any]] = field(default_factory=list)


class ValidationReporter(BaseReporter):
    """Reporter for API validation results.

    Generates both JSON and markdown reports with validation statistics
    and endpoint coverage analysis.
    """

    def __init__(
        self,
        stats: ValidationStats,
        path_config: PathConfig | None = None,
    ) -> None:
        """Initialize validation reporter.

        Args:
            stats: ValidationStats object with validation results
            path_config: Optional PathConfig instance
        """
        super().__init__(
            title="API Validation Report",
            description="Live API endpoint validation and schema compliance testing",
            path_config=path_config,
        )
        self.stats = stats

    def to_dict(self) -> dict[str, Any]:
        """Convert validation report to dictionary."""
        availability_percentage = 0.0
        if self.stats.endpoints_validated > 0:
            availability_percentage = round(
                (self.stats.endpoints_available / self.stats.endpoints_validated * 100),
                2,
            )

        schema_match_percentage = 0.0
        if self.stats.endpoints_available > 0:
            schema_match_percentage = round(
                (self.stats.schema_matches / self.stats.endpoints_available * 100),
                2,
            )

        return {
            "summary": {
                "specs_processed": self.stats.specs_processed,
                "total_endpoints": self.stats.total_endpoints,
                "endpoints_eligible": self.stats.endpoints_eligible,
                "endpoints_safely_resolved": self.stats.endpoints_safely_resolved,
                "endpoints_unresolved": self.stats.endpoints_unresolved,
                "endpoints_executed": self.stats.endpoints_executed,
                "endpoints_validated": self.stats.endpoints_validated,
                "endpoints_available": self.stats.endpoints_available,
                "endpoints_unavailable": self.stats.endpoints_unavailable,
                "schema_matches": self.stats.schema_matches,
                "availability_percentage": availability_percentage,
                "schema_match_percentage": schema_match_percentage,
            },
            "unresolved_endpoints": self.stats.unresolved_endpoints,
            "discrepancies": self.stats.discrepancies,
            "specs": [
                {
                    "filename": r.filename,
                    "endpoints_total": r.endpoints_total,
                    "endpoints_eligible": r.endpoints_eligible,
                    "endpoints_safely_resolved": r.endpoints_safely_resolved,
                    "endpoints_unresolved": r.endpoints_unresolved,
                    "endpoints_executed": r.endpoints_executed,
                    "unresolved_endpoints": r.unresolved_endpoints,
                    "endpoints_validated": r.endpoints_validated,
                    "endpoints_available": r.endpoints_available,
                    "endpoints_skipped": r.endpoints_skipped,
                    "schema_matches": r.schema_matches,
                    "errors": r.errors,
                }
                for r in self.stats.spec_results
            ],
        }

    def to_markdown(self) -> str:
        """Convert validation report to markdown."""
        # Validation evidence must be byte-reproducible for identical measured
        # results, so it intentionally has no wall-clock generation timestamp.
        md = f"# {self.title}\n\n{self.description}\n\n"

        # Summary section
        md += self.markdown_section(
            "Executive Summary",
            self._markdown_summary_table(),
            level=2,
        )

        # Thresholds and metrics
        md += self._markdown_metrics_section()

        # Specification results
        if self.stats.spec_results:
            md += self._markdown_spec_results_section()

        # Exact evidence for every operation that could not be safely resolved
        if self.stats.unresolved_endpoints:
            md += self._markdown_unresolved_endpoints_section()

        # Discrepancies
        if self.stats.discrepancies:
            md += self._markdown_discrepancies_section()

        return md

    def _markdown_summary_table(self) -> str:
        """Create markdown summary table."""
        headers = ["Metric", "Value"]
        rows = [
            ["Specifications Processed", str(self.stats.specs_processed)],
            ["Endpoints Extracted", str(self.stats.total_endpoints)],
            ["Endpoints Eligible", str(self.stats.endpoints_eligible)],
            ["Endpoints Safely Resolved", str(self.stats.endpoints_safely_resolved)],
            ["Endpoints Unresolved", str(self.stats.endpoints_unresolved)],
            ["Endpoints Executed", str(self.stats.endpoints_executed)],
            ["Response Results", str(self.stats.endpoints_validated)],
            ["Endpoints Available", str(self.stats.endpoints_available)],
            ["Endpoints Unavailable", str(self.stats.endpoints_unavailable)],
            ["Schema Matches", str(self.stats.schema_matches)],
        ]

        if self.stats.endpoints_validated > 0:
            availability = round(
                (self.stats.endpoints_available / self.stats.endpoints_validated * 100),
                1,
            )
            rows.append(["Availability %", f"{availability}%"])

        if self.stats.endpoints_available > 0:
            schema_match = round(
                (self.stats.schema_matches / self.stats.endpoints_available * 100),
                1,
            )
            rows.append(["Schema Match %", f"{schema_match}%"])

        return BaseReporter.markdown_table(headers, rows)

    def _markdown_metrics_section(self) -> str:
        """Create section with validation metrics."""
        content = ""

        if self.stats.endpoints_validated > 0:
            availability = round(
                (self.stats.endpoints_available / self.stats.endpoints_validated * 100),
                1,
            )
            content += f"- **Availability**: {availability}% of endpoints available\n"

        if self.stats.endpoints_available > 0:
            schema_match = round(
                (self.stats.schema_matches / self.stats.endpoints_available * 100),
                1,
            )
            content += (
                f"- **Schema Compliance**: {schema_match}% of available endpoints match schema\n"
            )

        content += f"- **Extracted**: {self.stats.total_endpoints}\n"
        content += f"- **Eligible**: {self.stats.endpoints_eligible}\n"
        content += f"- **Safely Resolved**: {self.stats.endpoints_safely_resolved}\n"
        content += f"- **Unresolved**: {self.stats.endpoints_unresolved}\n"
        content += f"- **Executed**: {self.stats.endpoints_executed}\n"
        content += f"- **Response Results**: {self.stats.endpoints_validated}\n"

        return self.markdown_section("Metrics", content, level=3)

    def _markdown_spec_results_section(self) -> str:
        """Create section with per-spec results."""
        headers = [
            "Specification",
            "Extracted",
            "Eligible",
            "Safely Resolved",
            "Unresolved",
            "Executed",
            "Response Results",
            "Available",
            "Schema Match",
        ]
        rows = [
            [
                result.filename,
                str(result.endpoints_total),
                str(result.endpoints_eligible),
                str(result.endpoints_safely_resolved),
                str(result.endpoints_unresolved),
                str(result.endpoints_executed),
                str(result.endpoints_validated),
                str(result.endpoints_available),
                str(result.schema_matches),
            ]
            for result in self.stats.spec_results
        ]

        content = BaseReporter.markdown_table(headers, rows)

        errors = [
            (result.filename, error)
            for result in self.stats.spec_results
            for error in result.errors
        ]
        if errors:
            content += "Validation errors:\n\n"
            for filename, error in errors:
                content += f"- `{filename}`: {error}\n"
            content += "\n"

        return self.markdown_section("Specification Results", content, level=3)

    def _markdown_unresolved_endpoints_section(self) -> str:
        """Document every endpoint that was not safe to execute."""
        headers = ["Specification", "Method", "Path", "Reason"]
        rows = [
            [item["spec"], item["method"], item["path"], item["reason"]]
            for item in self.stats.unresolved_endpoints
        ]
        content = BaseReporter.markdown_table(headers, rows)
        return self.markdown_section("Unresolved Endpoints", content, level=3)

    def _markdown_discrepancies_section(self) -> str:
        """Create section documenting discovered discrepancies."""
        if not self.stats.discrepancies:
            return ""

        content = f"Found {len(self.stats.discrepancies)} discrepancies:\n\n"

        for i, disc in enumerate(self.stats.discrepancies, 1):
            if isinstance(disc, dict):
                location = " ".join(
                    str(value) for value in (disc.get("method"), disc.get("path")) if value
                )
                heading = location or str(disc.get("description", "Discrepancy"))
                content += f"{i}. {heading}\n"
                if "spec" in disc:
                    content += f"   - Specification: {disc['spec']}\n"
                if "endpoint" in disc:
                    content += f"   - Endpoint: {disc['endpoint']}\n"
                if "issue" in disc:
                    content += f"   - Issue: {disc['issue']}\n"
                issues = disc.get("issues", [])
                if not isinstance(issues, list):
                    issues = [issues]
                for issue in issues:
                    content += f"   - Issue: {issue}\n"
            else:
                content += f"{i}. {disc!s}\n"

        return self.markdown_section("Discrepancies", content, level=3)
