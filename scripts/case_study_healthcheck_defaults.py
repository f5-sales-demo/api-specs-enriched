# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Case study: Discover healthcheck server-applied defaults.

Tests the F5 XC API with progressively minimal healthcheck configurations
to determine which fields are truly required vs have server-applied defaults.

Usage:
    # Set credentials (staging environment)
    export F5XC_API_URL="https://nferreira.staging.volterra.us"
    export F5XC_API_TOKEN='your-token'

    # Run the case study
    python -m scripts.case_study_healthcheck_defaults

    # Or with verbose output
    python -m scripts.case_study_healthcheck_defaults --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent / "discovery"))
try:
    from scripts.discovery.rate_limiter import RateLimitConfig, RateLimiter
except ModuleNotFoundError:
    from rate_limiter import (  # type: ignore[import-not-found,no-redef,unused-ignore]
        RateLimitConfig,
        RateLimiter,
    )

logger = logging.getLogger(__name__)


@dataclass
class TestCase:
    """A single test configuration."""

    name: str
    description: str
    config: dict
    expected_success: bool


@dataclass
class TestResult:
    """Result of a test case execution."""

    test_name: str
    description: str
    sent_config: dict
    success: bool
    status_code: int
    response_body: dict | None = None
    error_message: str | None = None
    received_spec: dict | None = None
    discovered_defaults: dict = field(default_factory=dict)
    duration_ms: float = 0.0


@dataclass
class CaseStudyReport:
    """Complete case study report."""

    timestamp: str
    api_url: str
    namespace: str
    test_results: list[TestResult] = field(default_factory=list)
    minimum_required_fields: list[str] = field(default_factory=list)
    discovered_defaults: dict = field(default_factory=dict)
    summary: str = ""
    duration_seconds: float = 0.0


class HealthcheckCaseStudy:
    """Case study for healthcheck server defaults discovery."""

    def __init__(
        self,
        api_url: str,
        api_token: str,
        namespace: str = "default",
        verbose: bool = False,
    ) -> None:
        """Initialize the case study.

        Args:
            api_url: F5 XC API base URL
            api_token: API authentication token
            namespace: Namespace for test resources
            verbose: Enable verbose logging
        """
        self.api_url = api_url.rstrip("/")
        self.api_token = api_token
        self.namespace = namespace
        self.verbose = verbose

        # Rate limiter - conservative for testing
        rate_config = RateLimitConfig(
            requests_per_second=1.0,
            burst_limit=3,
            retry_attempts=2,
        )
        self.rate_limiter = RateLimiter(rate_config)

        # Test prefix for resource naming
        self.test_prefix = "hc-case-study"

        # Track created resources for cleanup
        self.created_resources: list[str] = []

    def _get_auth_headers(self) -> dict[str, str]:
        """Get authentication headers."""
        return {
            "Authorization": f"APIToken {self.api_token}",
            "Content-Type": "application/json",
        }

    def _generate_test_name(self, suffix: str = "") -> str:
        """Generate unique test resource name."""
        short_uuid = uuid.uuid4().hex[:6]
        if suffix:
            return f"{self.test_prefix}-{suffix}-{short_uuid}"
        return f"{self.test_prefix}-{short_uuid}"

    def _get_test_cases(self) -> list[TestCase]:
        """Define progressive test cases for healthcheck.

        Tests progress from ultra-minimal to fully specified,
        allowing discovery of which fields have server defaults.
        """
        return [
            # Test 1: Ultra minimal - just name and health check type
            TestCase(
                name="test-1-ultra-minimal",
                description="Only name + http_health_check with path",
                config={
                    "spec": {
                        "http_health_check": {"path": "/health"},
                    },
                },
                expected_success=False,  # Expect this to fail
            ),
            # Test 2: Add interval only
            TestCase(
                name="test-2-with-interval",
                description="Add interval (10 seconds)",
                config={
                    "spec": {
                        "http_health_check": {"path": "/health"},
                        "interval": 10,
                    },
                },
                expected_success=False,  # May still fail
            ),
            # Test 3: Add interval + timeout
            TestCase(
                name="test-3-with-timeout",
                description="Add interval (10s) + timeout (3s)",
                config={
                    "spec": {
                        "http_health_check": {"path": "/health"},
                        "interval": 10,
                        "timeout": 3,
                    },
                },
                expected_success=False,  # May still fail
            ),
            # Test 4: Add healthy_threshold
            TestCase(
                name="test-4-with-healthy-threshold",
                description="Add healthy_threshold (2)",
                config={
                    "spec": {
                        "http_health_check": {"path": "/health"},
                        "interval": 10,
                        "timeout": 3,
                        "healthy_threshold": 2,
                    },
                },
                expected_success=False,  # May still fail
            ),
            # Test 5: Add unhealthy_threshold - current documented minimum
            TestCase(
                name="test-5-full-minimum",
                description="Full documented minimum (all thresholds)",
                config={
                    "spec": {
                        "http_health_check": {"path": "/health"},
                        "interval": 10,
                        "timeout": 3,
                        "healthy_threshold": 2,
                        "unhealthy_threshold": 3,
                    },
                },
                expected_success=True,  # Should succeed
            ),
            # Test 6: Try without healthy_threshold (if Test 5 succeeds)
            TestCase(
                name="test-6-no-healthy-threshold",
                description="Without healthy_threshold (has unhealthy_threshold)",
                config={
                    "spec": {
                        "http_health_check": {"path": "/health"},
                        "interval": 10,
                        "timeout": 3,
                        "unhealthy_threshold": 3,
                    },
                },
                expected_success=False,  # Testing if healthy_threshold has default
            ),
            # Test 7: Try without unhealthy_threshold (if Test 5 succeeds)
            TestCase(
                name="test-7-no-unhealthy-threshold",
                description="Without unhealthy_threshold (has healthy_threshold)",
                config={
                    "spec": {
                        "http_health_check": {"path": "/health"},
                        "interval": 10,
                        "timeout": 3,
                        "healthy_threshold": 2,
                    },
                },
                expected_success=False,  # Testing if unhealthy_threshold has default
            ),
            # Test 8: Try without timeout
            TestCase(
                name="test-8-no-timeout",
                description="Without timeout (has both thresholds)",
                config={
                    "spec": {
                        "http_health_check": {"path": "/health"},
                        "interval": 10,
                        "healthy_threshold": 2,
                        "unhealthy_threshold": 3,
                    },
                },
                expected_success=False,  # Testing if timeout has default
            ),
        ]

    async def _execute_request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        json_data: dict | None = None,
    ) -> tuple[int, dict | None, str | None]:
        """Execute HTTP request with rate limiting."""
        async with self.rate_limiter:
            try:
                if method == "GET":
                    response = await client.get(url, timeout=30)
                elif method == "POST":
                    response = await client.post(url, json=json_data, timeout=30)
                elif method == "DELETE":
                    response = await client.delete(url, timeout=30)
                else:
                    return 0, None, f"Unsupported method: {method}"

                try:
                    body = response.json()
                except (json.JSONDecodeError, ValueError):
                    body = None

                return response.status_code, body, None

            except httpx.TimeoutException:
                return 0, None, "Request timed out"
            except httpx.RequestError as e:
                return 0, None, str(e)
            except Exception as e:
                return 0, None, f"Unexpected error: {e}"

    def _compare_configs(
        self,
        sent_spec: dict,
        received_spec: dict,
    ) -> dict[str, dict]:
        """Compare sent spec with received spec to find server-applied defaults.

        Returns:
            Dict mapping field paths to {sent: value, received: value}
        """
        differences = {}

        def flatten(d: dict, prefix: str = "") -> dict:
            """Flatten nested dict to dot-notation paths."""
            result = {}
            for key, value in d.items():
                path = f"{prefix}.{key}" if prefix else key
                if isinstance(value, dict) and value:  # Non-empty dict
                    result.update(flatten(value, path))
                else:
                    result[path] = value
            return result

        sent_flat = flatten(sent_spec)
        received_flat = flatten(received_spec)

        # Find fields in received but not in sent (server defaults)
        for key, value in received_flat.items():
            if key not in sent_flat:
                differences[key] = {"sent": None, "received": value}

        # Find fields with different values
        for key in sent_flat:
            if key in received_flat and sent_flat[key] != received_flat[key]:
                differences[key] = {"sent": sent_flat[key], "received": received_flat[key]}

        return differences

    async def run_test_case(
        self,
        client: httpx.AsyncClient,
        test_case: TestCase,
    ) -> TestResult:
        """Execute a single test case."""
        start = time.monotonic()
        test_name = self._generate_test_name(test_case.name)

        # Build full config with metadata
        full_config = copy.deepcopy(test_case.config)
        full_config["metadata"] = {
            "name": test_name,
            "namespace": self.namespace,
        }

        if self.verbose:
            logger.info("Running: %s - %s", test_case.name, test_case.description)
            logger.debug("Config: %s", json.dumps(full_config, indent=2))

        # Create healthcheck
        create_url = f"{self.api_url}/api/config/namespaces/{self.namespace}/healthchecks"
        status, body, error = await self._execute_request(client, "POST", create_url, full_config)

        result = TestResult(
            test_name=test_name,
            description=test_case.description,
            sent_config=full_config,
            success=status in [200, 201],
            status_code=status,
            response_body=body,
            error_message=error,
        )

        if result.success:
            self.created_resources.append(test_name)

            # Read back the created resource to capture server-populated values
            await asyncio.sleep(0.5)  # Small delay for consistency
            read_url = (
                f"{self.api_url}/api/config/namespaces/{self.namespace}/healthchecks/{test_name}"
            )
            read_status, read_body, _ = await self._execute_request(
                client,
                "GET",
                read_url,
            )

            if read_status == 200 and read_body:
                result.received_spec = read_body.get("spec", {})
                sent_spec = full_config.get("spec", {})
                if result.received_spec:
                    result.discovered_defaults = self._compare_configs(
                        sent_spec,
                        result.received_spec,
                    )

                if self.verbose and result.discovered_defaults:
                    logger.info("  Discovered defaults: %s", result.discovered_defaults)

        elif body and "message" in body:
            result.error_message = body.get("message", str(body))
            if self.verbose:
                logger.info("  Failed: %s", result.error_message)

        result.duration_ms = (time.monotonic() - start) * 1000
        return result

    async def cleanup(self, client: httpx.AsyncClient) -> int:
        """Clean up all created test resources."""
        cleaned = 0
        for name in self.created_resources:
            delete_url = (
                f"{self.api_url}/api/config/namespaces/{self.namespace}/healthchecks/{name}"
            )
            status, _, _ = await self._execute_request(client, "DELETE", delete_url)
            if status in [200, 202, 204]:
                cleaned += 1
                if self.verbose:
                    logger.info("Cleaned up: %s", name)
        return cleaned

    async def run(self) -> CaseStudyReport:
        """Execute the full case study."""
        start = time.monotonic()
        report = CaseStudyReport(
            timestamp=datetime.now(tz=UTC).isoformat(),
            api_url=self.api_url,
            namespace=self.namespace,
        )

        test_cases = self._get_test_cases()
        headers = self._get_auth_headers()

        logger.info("Starting healthcheck case study with %d test cases", len(test_cases))
        logger.info("API URL: %s", self.api_url)
        logger.info("Namespace: %s", self.namespace)
        logger.info("-" * 60)

        async with httpx.AsyncClient(
            headers=headers,
            verify=True,
            follow_redirects=True,
        ) as client:
            # Run all test cases
            for test_case in test_cases:
                result = await self.run_test_case(client, test_case)
                report.test_results.append(result)

                # Log result
                status_emoji = "✅" if result.success else "❌"
                logger.info(
                    "%s %s: status=%s, success=%s",
                    status_emoji,
                    test_case.name,
                    result.status_code,
                    result.success,
                )
                if result.error_message and not result.success:
                    logger.info("   Error: %s", result.error_message[:100])

            # Cleanup
            logger.info("-" * 60)
            cleaned = await self.cleanup(client)
            logger.info("Cleaned up %s test resources", cleaned)

        # Analyze results
        self._analyze_results(report)

        report.duration_seconds = time.monotonic() - start
        return report

    def _analyze_results(self, report: CaseStudyReport) -> None:
        """Analyze test results to determine minimum requirements and defaults."""
        # Find first successful test
        first_success = None
        for result in report.test_results:
            if result.success:
                first_success = result
                break

        if not first_success:
            report.summary = "All tests failed - unable to determine minimum requirements"
            return

        # Collect all discovered defaults from successful tests
        all_defaults: dict[str, set] = {}
        for result in report.test_results:
            if result.success and result.discovered_defaults:
                for field, values in result.discovered_defaults.items():
                    if values.get("sent") is None:  # Server-applied default
                        if field not in all_defaults:
                            all_defaults[field] = set()
                        received = values.get("received")
                        # Convert to string for set storage
                        all_defaults[field].add(json.dumps(received, default=str))

        # Convert back to single values
        for field, values in all_defaults.items():
            if len(values) == 1:
                report.discovered_defaults[field] = json.loads(values.pop())
            else:
                # Multiple values seen - take first
                report.discovered_defaults[field] = json.loads(values.pop())

        # Determine minimum required fields based on first successful test
        first_success_spec = first_success.sent_config.get("spec", {})
        required_fields = ["metadata.name", "metadata.namespace"]

        def extract_fields(d: dict, prefix: str = "spec") -> list[str]:
            """Extract all fields from config."""
            fields = []
            for key, value in d.items():
                path = f"{prefix}.{key}"
                if isinstance(value, dict) and value:
                    fields.extend(extract_fields(value, path))
                else:
                    fields.append(path)
            return fields

        required_fields.extend(extract_fields(first_success_spec))
        report.minimum_required_fields = required_fields

        # Build summary
        successful_tests = [r for r in report.test_results if r.success]

        summary_lines = [
            "Case Study Results:",
            f"  - {len(successful_tests)}/{len(report.test_results)} tests succeeded",
            f"  - First successful test: {first_success.test_name}",
            f"  - Minimum required fields: {len(report.minimum_required_fields)}",
            f"  - Discovered server defaults: {len(report.discovered_defaults)}",
            "",
            "Minimum Required Fields:",
        ]
        summary_lines.extend(f"  - {field}" for field in report.minimum_required_fields)

        if report.discovered_defaults:
            summary_lines.append("")
            summary_lines.append("Server-Applied Defaults:")
            for field, value in report.discovered_defaults.items():
                summary_lines.append(f"  - {field}: {value}")

        report.summary = "\n".join(summary_lines)


def generate_markdown_report(report: CaseStudyReport, output_path: Path) -> None:
    """Generate markdown report from case study results."""
    lines = [
        "# Healthcheck Minimum Configuration Case Study",
        "",
        f"**Generated**: {report.timestamp}",
        f"**API URL**: `{report.api_url}`",
        f"**Namespace**: `{report.namespace}`",
        f"**Duration**: {report.duration_seconds:.1f}s",
        "",
        "## Executive Summary",
        "",
        "This case study tests the F5 XC API with progressively minimal healthcheck",
        "configurations to discover which fields are truly required vs have server-applied defaults.",
        "",
        "## Test Results",
        "",
        "| Test | Description | Status | HTTP Code |",
        "|------|-------------|--------|-----------|",
    ]

    for result in report.test_results:
        status = "✅ Success" if result.success else "❌ Failed"
        lines.append(
            f"| {result.test_name} | {result.description} | {status} | {result.status_code} |",
        )

    lines.extend(
        [
            "",
            "## Minimum Required Fields",
            "",
            "Based on the first successful test, the following fields are required:",
            "",
        ],
    )

    lines.extend(f"- `{f}`" for f in report.minimum_required_fields)

    lines.extend(
        [
            "",
            "## Discovered Server-Applied Defaults",
            "",
            "The following fields have server-applied defaults (not sent, but received):",
            "",
            "| Field | Default Value |",
            "|-------|---------------|",
        ],
    )

    for field_name, value in report.discovered_defaults.items():
        value_str = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        lines.append(f"| `{field_name}` | `{value_str}` |")

    lines.extend(
        [
            "",
            "## Test Case Details",
            "",
        ],
    )

    for result in report.test_results:
        status_emoji = "✅" if result.success else "❌"
        lines.extend(
            [
                f"### {status_emoji} {result.test_name}",
                "",
                f"**Description**: {result.description}",
                f"**Status Code**: {result.status_code}",
                f"**Duration**: {result.duration_ms:.1f}ms",
                "",
                "**Sent Configuration**:",
                "```json",
                json.dumps(result.sent_config, indent=2),
                "```",
                "",
            ],
        )

        if result.success and result.received_spec:
            lines.extend(
                [
                    "**Received Spec** (from GET after creation):",
                    "```json",
                    json.dumps(result.received_spec, indent=2),
                    "```",
                    "",
                ],
            )

            if result.discovered_defaults:
                lines.append("**Server-Applied Defaults Found**:")
                for field_name, values in result.discovered_defaults.items():
                    if values.get("sent") is None:
                        lines.append(f"- `{field_name}`: `{values.get('received')}`")
                lines.append("")

        if result.error_message and not result.success:
            lines.extend(
                [
                    "**Error**:",
                    "```",
                    result.error_message,
                    "```",
                    "",
                ],
            )

    lines.extend(
        [
            "",
            "## Recommendations",
            "",
            "Based on this case study:",
            "",
        ],
    )

    if report.discovered_defaults:
        lines.append("### Update `config/discovered_defaults.yaml`")
        lines.append("")
        lines.append("Add the following discovered defaults:")
        lines.append("```yaml")
        lines.append("healthcheck:")
        lines.append("  defaults:")
        for field, value in report.discovered_defaults.items():
            if not field.startswith("http_health_check."):
                value_str = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
                # Convert field path to yaml key
                key = field.split(".")[-1]
                lines.append(f"    {key}: {value_str}")

        nested_defaults = {
            k: v
            for k, v in report.discovered_defaults.items()
            if k.startswith("http_health_check.")
        }
        if nested_defaults:
            lines.append("  nested:")
            lines.append("    http_health_check:")
            for field, value in nested_defaults.items():
                key = field.replace("http_health_check.", "")
                value_str = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
                lines.append(f"      {key}: {value_str}")
        lines.append("```")
        lines.append("")

    lines.extend(
        [
            "### Update `config/minimum_configs.yaml`",
            "",
            "Verify the `required_fields` list matches the true minimum:",
            "```yaml",
            "healthcheck:",
            "  required_fields:",
        ],
    )
    lines.extend(f'    - "{f}"' for f in report.minimum_required_fields)
    lines.extend(
        [
            "```",
            "",
            "---",
            "",
            "*Report generated by `scripts/case_study_healthcheck_defaults.py`*",
        ],
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines))


def generate_json_report(report: CaseStudyReport, output_path: Path) -> None:
    """Generate JSON report from case study results."""
    data = {
        "timestamp": report.timestamp,
        "api_url": report.api_url,
        "namespace": report.namespace,
        "duration_seconds": round(report.duration_seconds, 2),
        "summary": {
            "total_tests": len(report.test_results),
            "passed": sum(1 for r in report.test_results if r.success),
            "failed": sum(1 for r in report.test_results if not r.success),
        },
        "minimum_required_fields": report.minimum_required_fields,
        "discovered_defaults": report.discovered_defaults,
        "test_results": [
            {
                "test_name": r.test_name,
                "description": r.description,
                "success": r.success,
                "status_code": r.status_code,
                "error_message": r.error_message,
                "duration_ms": round(r.duration_ms, 2),
                "sent_config": r.sent_config,
                "received_spec": r.received_spec,
                "discovered_defaults": r.discovered_defaults,
            }
            for r in report.test_results
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


async def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Healthcheck minimum configuration case study",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output",
    )
    parser.add_argument(
        "--namespace",
        default="default",
        help="Namespace for test resources (default: default)",
    )
    parser.add_argument(
        "--output-dir",
        default="reports",
        help="Output directory for reports (default: reports)",
    )

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    # Get credentials from environment
    api_url = os.environ.get("F5XC_API_URL", "")
    api_token = os.environ.get("F5XC_API_TOKEN", "")

    if not api_url or not api_token:
        logger.error("Missing required environment variables:")
        logger.error("  F5XC_API_URL - F5 XC API base URL")
        logger.error("  F5XC_API_TOKEN - API authentication token")
        return 1

    # Run case study
    case_study = HealthcheckCaseStudy(
        api_url=api_url,
        api_token=api_token,
        namespace=args.namespace,
        verbose=args.verbose,
    )

    report = await case_study.run()

    # Generate reports
    output_dir = Path(args.output_dir)
    md_path = output_dir / "healthcheck-case-study.md"
    json_path = output_dir / "healthcheck-case-study.json"

    generate_markdown_report(report, md_path)
    generate_json_report(report, json_path)

    logger.info("-" * 60)
    logger.info("Reports generated:")
    logger.info("  Markdown: %s", md_path)
    logger.info("  JSON: %s", json_path)
    logger.info("-" * 60)
    logger.info(report.summary)

    # Return exit code based on results
    passed = sum(1 for r in report.test_results if r.success)
    return 0 if passed > 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
