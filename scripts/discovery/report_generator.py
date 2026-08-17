# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Report generator for API discovery results.

Generates:
- discovered/openapi.json - Full discovered OpenAPI spec
- discovered/diffs/summary.json - All differences found
- reports/discovery-report.md - Human-readable summary
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.utils.extension_constants import (
    X_F5XC_API_URL,
    X_F5XC_DISCOVERED_AT,
    X_F5XC_RESPONSE_TIME_MS,
)
from scripts.utils.path_config import PathConfig
from scripts.utils.pii_sanitizer import sanitize_api_url, sanitize_discovery_payload
from scripts.utils.report_base import BaseReporter
from scripts.utils.server_variables_markdown import ServerVariablesMarkdownHelper

from .diff_analyzer import DiffReport, DiffSeverity
from .schema_inferrer import InferredSchema


@dataclass
class EndpointDiscovery:
    """Discovery results for a single endpoint."""

    path: str
    method: str
    status_code: int | None = None
    response_time_ms: float | None = None
    inferred_schema: InferredSchema | None = None
    diff_report: DiffReport | None = None
    examples: list[dict] = field(default_factory=list)
    error: str | None = None


@dataclass
class DiscoverySession:
    """Complete discovery session results."""

    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    api_url: str = ""
    namespaces: list[str] = field(default_factory=list)
    endpoints: list[EndpointDiscovery] = field(default_factory=list)
    rate_limiter_stats: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        """Get session duration in seconds."""
        end = self.completed_at or datetime.now(timezone.utc)
        return (end - self.started_at).total_seconds()

    @property
    def success_rate(self) -> float:
        """Get percentage of successful endpoint discoveries."""
        if not self.endpoints:
            return 0.0
        successful = len([e for e in self.endpoints if e.error is None])
        return successful / len(self.endpoints) * 100


class ReportGenerator(BaseReporter):
    """Generate reports from discovery results.

    Provides:
    - OpenAPI spec generation from discovered schemas
    - Diff report generation
    - Markdown summary generation
    - Server variables documentation integration
    """

    def __init__(
        self,
        output_dir: Path | str = "specs/discovered",
        include_examples: bool = True,
        pretty_print: bool = True,
        path_config: PathConfig | None = None,
    ) -> None:
        """Initialize report generator.

        Args:
            output_dir: Directory for output files
            include_examples: Include example responses in schemas
            pretty_print: Pretty print JSON output
            path_config: Optional PathConfig instance
        """
        super().__init__(
            title="API Discovery Report",
            description="API specification discovered from live API exploration",
            path_config=path_config,
        )
        self.output_dir = Path(output_dir)
        self.include_examples = include_examples
        self.pretty_print = pretty_print
        self.sv_helper = ServerVariablesMarkdownHelper()

    def generate_all(self, session: DiscoverySession) -> dict[str, Path]:  # type: ignore[override]  # pylint: disable=arguments-differ
        """Generate all reports from discovery session.

        Args:
            session: Completed discovery session

        Returns:
            Dict mapping report type to file path
        """
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "schemas").mkdir(exist_ok=True)
        (self.output_dir / "endpoints").mkdir(exist_ok=True)
        (self.output_dir / "diffs").mkdir(exist_ok=True)

        generated = {}

        # Generate OpenAPI spec
        openapi_path = self.generate_openapi(session)
        if openapi_path:
            generated["openapi"] = openapi_path

        # Generate diff summary
        diff_path = self.generate_diff_summary(session)
        if diff_path:
            generated["diffs"] = diff_path

        # Generate markdown report
        md_path = self.generate_markdown_report(session)
        if md_path:
            generated["markdown"] = md_path

        # Generate session summary
        summary_path = self.generate_session_summary(session)
        if summary_path:
            generated["summary"] = summary_path

        return generated

    def generate_openapi(self, session: DiscoverySession) -> Path | None:
        """Generate OpenAPI spec from discovered schemas.

        Args:
            session: Discovery session with endpoint results

        Returns:
            Path to generated spec file
        """
        api_url = sanitize_api_url(session.api_url)
        spec: dict[str, Any] = {
            "openapi": "3.0.3",
            "info": {
                "title": "F5 Distributed Cloud API (Discovered)",
                "version": datetime.now(timezone.utc).strftime("%Y%m%d%H%M"),
                "description": "API specification discovered from live API exploration",
                X_F5XC_DISCOVERED_AT: session.started_at.isoformat(),
                X_F5XC_API_URL: api_url,
            },
            "servers": [{"url": api_url}] if session.api_url else [],
            "paths": {},
            "components": {"schemas": {}},
        }

        # Add paths from discovered endpoints
        for endpoint in session.endpoints:
            if endpoint.inferred_schema and endpoint.error is None:
                path = endpoint.path
                method = endpoint.method.lower()

                if path not in spec["paths"]:
                    spec["paths"][path] = {}

                operation: dict[str, Any] = {
                    "operationId": f"{method}_{path.replace('/', '_').strip('_')}",
                    "responses": {
                        str(endpoint.status_code or 200): {
                            "description": "Discovered response",
                            "content": {
                                "application/json": {
                                    "schema": endpoint.inferred_schema.to_json_schema(),
                                },
                            },
                        },
                    },
                }

                if self.include_examples and endpoint.examples:
                    operation["responses"][str(endpoint.status_code or 200)]["content"][
                        "application/json"
                    ]["example"] = sanitize_discovery_payload(endpoint.examples[0])

                if endpoint.response_time_ms:
                    operation[X_F5XC_RESPONSE_TIME_MS] = round(endpoint.response_time_ms, 2)

                spec["paths"][path][method] = operation

        # Write spec
        spec_path = self.output_dir / "openapi.json"
        self._write_json(spec_path, spec)

        return spec_path

    def generate_diff_summary(self, session: DiscoverySession) -> Path | None:
        """Generate diff summary from all endpoint comparisons.

        Args:
            session: Discovery session with diff reports

        Returns:
            Path to diff summary file
        """
        diff_reports = [e.diff_report for e in session.endpoints if e.diff_report]

        if not diff_reports:
            return None

        summary: dict[str, Any] = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_endpoints": len(session.endpoints),
            "endpoints_with_diffs": len([r for r in diff_reports if r.total_diffs > 0]),
            "total_diffs": sum(r.total_diffs for r in diff_reports),
            "by_severity": {
                "error": sum(len(r.errors) for r in diff_reports),
                "warning": sum(len(r.warnings) for r in diff_reports),
                "info": sum(
                    len([d for d in r.diffs if d.severity == DiffSeverity.INFO])
                    for r in diff_reports
                ),
            },
            "by_type": self._count_diff_types(diff_reports),
            "endpoints": [r.to_dict() for r in diff_reports if r.total_diffs > 0],
        }

        summary_path = self.output_dir / "diffs" / "summary.json"
        self._write_json(summary_path, summary)

        return summary_path

    def generate_markdown_report(self, session: DiscoverySession) -> Path | None:
        """Generate human-readable markdown report.

        Args:
            session: Completed discovery session

        Returns:
            Path to markdown report file
        """
        reports_dir = self.path_config.reports_dir
        reports_dir.mkdir(parents=True, exist_ok=True)

        safe_namespaces = [
            sanitize_discovery_payload({"namespace": namespace})["namespace"]
            for namespace in session.namespaces
        ]
        lines = [
            "# F5 XC API Discovery Report",
            "",
            f"**Generated**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}",
            f"**API URL**: {sanitize_api_url(session.api_url)}",
            f"**Duration**: {session.duration_seconds:.1f} seconds",
            "",
            "---",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Endpoints Explored | {len(session.endpoints)} |",
            f"| Success Rate | {session.success_rate:.1f}% |",
            f"| Namespaces | {', '.join(safe_namespaces)} |",
            "",
        ]

        # Server variables section
        sv_section = self.sv_helper.render_server_configuration_section()
        if sv_section:
            lines.extend(sv_section.split("\n"))
            lines.append("")

        # Rate limiter stats
        if session.rate_limiter_stats:
            lines.extend(
                [
                    "## Rate Limiting",
                    "",
                    "| Metric | Value |",
                    "|--------|-------|",
                ],
            )
            for key, value in session.rate_limiter_stats.items():
                lines.append(f"| {key.replace('_', ' ').title()} | {value} |")
            lines.append("")

        # Diff summary
        diff_reports = [e.diff_report for e in session.endpoints if e.diff_report]
        if diff_reports:
            total_diffs = sum(r.total_diffs for r in diff_reports)
            total_errors = sum(len(r.errors) for r in diff_reports)
            total_warnings = sum(len(r.warnings) for r in diff_reports)

            lines.extend(
                [
                    "## Schema Differences",
                    "",
                    "| Severity | Count |",
                    "|----------|-------|",
                    f"| Errors | {total_errors} |",
                    f"| Warnings | {total_warnings} |",
                    f"| Total | {total_diffs} |",
                    "",
                ],
            )

            # Notable discoveries
            lines.extend(
                [
                    "### Notable Discoveries",
                    "",
                ],
            )

            for report in diff_reports[:20]:  # Limit output
                if report.total_diffs > 0:
                    lines.append(f"**{report.method} {report.endpoint}**")
                    for diff in report.diffs[:5]:  # Limit per endpoint
                        icon = "!" if diff.severity == DiffSeverity.ERROR else "?"
                        lines.append(f"- [{icon}] {diff.message}")
                    lines.append("")

        # Errors
        if session.errors:
            lines.extend(
                [
                    "## Errors",
                    "",
                ],
            )
            for error in session.errors[:20]:
                lines.append(f"- {error}")
            lines.append("")

        # Endpoint details
        lines.extend(
            [
                "## Endpoints Explored",
                "",
                "| Endpoint | Method | Status | Response Time |",
                "|----------|--------|--------|---------------|",
            ],
        )

        for endpoint in session.endpoints[:100]:  # Limit output
            status = "OK" if endpoint.error is None else "Error"
            rt = f"{endpoint.response_time_ms:.0f}ms" if endpoint.response_time_ms else "-"
            lines.append(f"| {endpoint.path} | {endpoint.method} | {status} | {rt} |")

        lines.append("")

        # Write report
        report_path = self.path_config.discovery_report
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(lines))

        return report_path

    def generate_session_summary(self, session: DiscoverySession) -> Path | None:
        """Generate session summary JSON.

        Args:
            session: Completed discovery session

        Returns:
            Path to summary file
        """
        safe_namespaces = [
            sanitize_discovery_payload({"namespace": namespace})["namespace"]
            for namespace in session.namespaces
        ]
        summary = {
            "started_at": session.started_at.isoformat(),
            "completed_at": (session.completed_at.isoformat() if session.completed_at else None),
            "duration_seconds": session.duration_seconds,
            "api_url": sanitize_api_url(session.api_url),
            "namespaces": safe_namespaces,
            "statistics": {
                "endpoints_total": len(session.endpoints),
                "endpoints_successful": len(
                    [e for e in session.endpoints if e.error is None],
                ),
                "endpoints_failed": len(
                    [e for e in session.endpoints if e.error is not None],
                ),
                "success_rate": session.success_rate,
            },
            "rate_limiter": session.rate_limiter_stats,
            "errors": session.errors[:50],  # Limit
        }

        summary_path = self.output_dir / "session.json"
        self._write_json(summary_path, summary)

        return summary_path

    def _write_json(self, path: Path, data: dict) -> None:
        """Write JSON data to file.

        Args:
            path: Output file path
            data: Data to write
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            if self.pretty_print:
                json.dump(data, f, indent=2, default=str)
            else:
                json.dump(data, f, default=str)
            f.write("\n")

    def _count_diff_types(self, reports: list[DiffReport]) -> dict[str, int]:
        """Count differences by type across all reports.

        Args:
            reports: List of diff reports

        Returns:
            Dict mapping diff type to count
        """
        counts: dict[str, int] = {}
        for report in reports:
            for diff in report.diffs:
                type_name = diff.diff_type.value
                counts[type_name] = counts.get(type_name, 0) + 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        """Convert report to dictionary format.

        Returns:
            Dictionary representation of report data
        """
        return {
            "title": self.title,
            "description": self.description,
            "generated_at": self.generated_at,
        }

    def to_markdown(self) -> str:
        """Convert report to markdown format.

        Returns:
            Markdown-formatted string
        """
        return f"# {self.title}\n\n{self.description}\n\nGenerated at: {self.generated_at}\n"
