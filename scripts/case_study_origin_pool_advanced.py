# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Case study: Discover origin_pool advanced options and server-applied defaults.

Tests all 15 UI configuration options for origin_pool to document:
1. Constrained values (enum options, OneOf choices)
2. Server-applied defaults (values applied when fields omitted)
3. UI defaults vs Server defaults (preselected UI values vs API behavior)

Usage:
    # Set credentials (staging environment)
    export F5XC_API_URL="https://tenant.staging.volterra.us"
    export F5XC_API_TOKEN='your-token'

    # Run the case study
    python -m scripts.case_study_origin_pool_advanced

    # Or with verbose output
    python -m scripts.case_study_origin_pool_advanced --verbose

    # Run specific test groups
    python -m scripts.case_study_origin_pool_advanced --group port
    python -m scripts.case_study_origin_pool_advanced --group tls
    python -m scripts.case_study_origin_pool_advanced --group lb_algorithm
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
from typing import Any, ClassVar

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
    config: dict[str, Any]
    expected_success: bool
    group: str = "default"
    ui_option: int = 0  # Maps to UI option number (1-15)


@dataclass
class TestResult:
    """Result of a test case execution."""

    test_name: str
    description: str
    sent_config: dict[str, Any]
    success: bool
    status_code: int
    group: str = "default"
    ui_option: int = 0
    response_body: dict[str, Any] | None = None
    error_message: str | None = None
    received_spec: dict[str, Any] | None = None
    discovered_defaults: dict[str, dict[str, Any]] = field(default_factory=dict)
    duration_ms: float = 0.0


@dataclass
class CaseStudyReport:
    """Complete case study report."""

    timestamp: str
    api_url: str
    namespace: str
    test_results: list[TestResult] = field(default_factory=list)
    minimum_required_fields: list[str] = field(default_factory=list)
    discovered_defaults: dict[str, Any] = field(default_factory=dict)
    oneof_defaults: dict[str, str] = field(default_factory=dict)
    enum_values_discovered: dict[str, list[str]] = field(default_factory=dict)
    ui_vs_server_defaults: dict[str, dict[str, Any]] = field(default_factory=dict)
    summary: str = ""
    duration_seconds: float = 0.0


class OriginPoolCaseStudy:
    """Case study for origin_pool advanced options discovery."""

    # Base origin server config used in all tests
    BASE_ORIGIN_SERVER: ClassVar[dict[str, Any]] = {
        "public_name": {"dns_name": "backend.example.com"},
    }

    def __init__(
        self,
        api_url: str,
        api_token: str,
        namespace: str = "default",
        verbose: bool = False,
        test_group: str | None = None,
    ) -> None:
        """Initialize the case study.

        Args:
            api_url: F5 XC API base URL
            api_token: API authentication token
            namespace: Namespace for test resources
            verbose: Enable verbose logging
            test_group: Run only tests in this group
        """
        self.api_url = api_url.rstrip("/")
        self.api_token = api_token
        self.namespace = namespace
        self.verbose = verbose
        self.test_group = test_group

        # Rate limiter - conservative for testing
        rate_config = RateLimitConfig(
            requests_per_second=1.0,
            burst_limit=3,
            retry_attempts=2,
        )
        self.rate_limiter = RateLimiter(rate_config)

        # Test prefix for resource naming
        self.test_prefix = "op-adv-case"

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
            return f"{self.test_prefix}-{suffix[:20]}-{short_uuid}"
        return f"{self.test_prefix}-{short_uuid}"

    def _get_base_config(self, port: int = 443) -> dict[str, Any]:
        """Get base valid origin_pool configuration."""
        return {
            "spec": {
                "origin_servers": [copy.deepcopy(self.BASE_ORIGIN_SERVER)],
                "port": port,
            },
        }

    def _get_test_cases(self) -> list[TestCase]:
        """Define test cases for all 15 UI options.

        Test groups:
        - port: Port configuration (UI options 1, 3)
        - connectivity: Connection pool, timeouts (UI options 2, 7, 8)
        - lb_algorithm: Load balancing algorithm (UI option 4)
        - endpoint_selection: Endpoint selection (UI option 5)
        - tls: TLS configuration (UI option 6)
        - circuit_breaker: Circuit breaker (UI option 9)
        - outlier_detection: Outlier detection (UI option 10)
        - panic_threshold: Panic threshold (UI option 11)
        - subset: Subset load balancing (UI option 12)
        - http_protocol: HTTP protocol config (UI option 13)
        - proxy_protocol: Proxy protocol (UI option 14)
        - lb_source_ip: LB source IP persistence (UI option 15)
        """
        test_cases = []

        # ========================================================================
        # Group 1: Port Configuration Tests (UI options 1, 3)
        # ========================================================================

        # Test 1.1: Explicit port (most common)
        test_cases.append(
            TestCase(
                name="port-explicit",
                description="Explicit port number (443)",
                config={
                    "spec": {
                        "origin_servers": [copy.deepcopy(self.BASE_ORIGIN_SERVER)],
                        "port": 443,
                    },
                },
                expected_success=True,
                group="port",
                ui_option=1,
            ),
        )

        # Test 1.2: Automatic port
        test_cases.append(
            TestCase(
                name="port-automatic",
                description="Automatic port assignment",
                config={
                    "spec": {
                        "origin_servers": [copy.deepcopy(self.BASE_ORIGIN_SERVER)],
                        "automatic_port": {},
                    },
                },
                expected_success=True,
                group="port",
                ui_option=1,
            ),
        )

        # Test 1.3: LB port (use load balancer's port)
        test_cases.append(
            TestCase(
                name="port-lb",
                description="Use load balancer port",
                config={
                    "spec": {
                        "origin_servers": [copy.deepcopy(self.BASE_ORIGIN_SERVER)],
                        "lb_port": {},
                    },
                },
                expected_success=True,
                group="port",
                ui_option=1,
            ),
        )

        # ========================================================================
        # Group 2: Connectivity Tests (UI options 2, 7, 8)
        # ========================================================================

        # Test 2.1: Connection pool reuse enabled (default)
        base = self._get_base_config()
        base["spec"]["enable_conn_pool_reuse"] = {}
        test_cases.append(
            TestCase(
                name="conn-pool-reuse-enable",
                description="Enable connection pool reuse",
                config=base,
                expected_success=True,
                group="connectivity",
                ui_option=2,
            ),
        )

        # Test 2.2: Connection pool reuse disabled
        base = self._get_base_config()
        base["spec"]["disable_conn_pool_reuse"] = {}
        test_cases.append(
            TestCase(
                name="conn-pool-reuse-disable",
                description="Disable connection pool reuse",
                config=base,
                expected_success=True,
                group="connectivity",
                ui_option=2,
            ),
        )

        # Test 2.3: Custom connection timeout
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {"connection_timeout": 5000}
        test_cases.append(
            TestCase(
                name="timeout-connection-custom",
                description="Custom connection timeout (5000ms)",
                config=base,
                expected_success=True,
                group="connectivity",
                ui_option=7,
            ),
        )

        # Test 2.4: Custom HTTP idle timeout
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {"http_idle_timeout": 60000}
        test_cases.append(
            TestCase(
                name="timeout-http-idle-custom",
                description="Custom HTTP idle timeout (60000ms)",
                config=base,
                expected_success=True,
                group="connectivity",
                ui_option=8,
            ),
        )

        # Test 2.5: Health check port - same as endpoint
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {"same_as_endpoint_port": {}}
        test_cases.append(
            TestCase(
                name="hc-port-same-endpoint",
                description="Health check port same as endpoint",
                config=base,
                expected_success=True,
                group="connectivity",
                ui_option=3,
            ),
        )

        # Test 2.6: Health check port - explicit
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {"health_check_port": 8080}
        test_cases.append(
            TestCase(
                name="hc-port-explicit",
                description="Explicit health check port (8080)",
                config=base,
                expected_success=True,
                group="connectivity",
                ui_option=3,
            ),
        )

        # ========================================================================
        # Group 3: Load Balancing Algorithm Tests (UI option 4)
        # ========================================================================

        lb_algorithms = [
            ("ROUND_ROBIN", "Round robin selection"),
            ("LEAST_REQUEST", "Least request selection"),
            ("RING_HASH", "Ring hash consistent hashing"),
            ("RANDOM", "Random selection"),
            ("LB_OVERRIDE", "Inherit from load balancer"),
        ]

        for algo, desc in lb_algorithms:
            base = self._get_base_config()
            base["spec"]["loadbalancer_algorithm"] = algo
            test_cases.append(
                TestCase(
                    name=f"lb-algo-{algo.lower().replace('_', '-')}",
                    description=f"LB algorithm: {desc}",
                    config=base,
                    expected_success=True,
                    group="lb_algorithm",
                    ui_option=4,
                ),
            )

        # Test without specifying algorithm (discover default)
        base = self._get_base_config()
        test_cases.append(
            TestCase(
                name="lb-algo-default",
                description="LB algorithm: default (omitted)",
                config=base,
                expected_success=True,
                group="lb_algorithm",
                ui_option=4,
            ),
        )

        # ========================================================================
        # Group 4: Endpoint Selection Tests (UI option 5)
        # ========================================================================

        endpoint_selections = [
            ("DISTRIBUTED", "All endpoints - local and remote"),
            ("LOCAL_ONLY", "Only local endpoints"),
            ("LOCAL_PREFERRED", "Prefer local, fallback to remote"),
        ]

        for selection, desc in endpoint_selections:
            base = self._get_base_config()
            base["spec"]["endpoint_selection"] = selection
            test_cases.append(
                TestCase(
                    name=f"endpoint-{selection.lower().replace('_', '-')}",
                    description=f"Endpoint selection: {desc}",
                    config=base,
                    expected_success=True,
                    group="endpoint_selection",
                    ui_option=5,
                ),
            )

        # ========================================================================
        # Group 5: TLS Configuration Tests (UI option 6)
        # ========================================================================

        # Test 5.1: No TLS (default)
        base = self._get_base_config()
        base["spec"]["no_tls"] = {}
        test_cases.append(
            TestCase(
                name="tls-disabled",
                description="TLS disabled (no_tls)",
                config=base,
                expected_success=True,
                group="tls",
                ui_option=6,
            ),
        )

        # Test 5.2: TLS enabled with basic config
        base = self._get_base_config()
        base["spec"]["use_tls"] = {
            "skip_server_verification": {},
        }
        test_cases.append(
            TestCase(
                name="tls-enabled-basic",
                description="TLS enabled with skip verification",
                config=base,
                expected_success=True,
                group="tls",
                ui_option=6,
            ),
        )

        # Test 5.3: TLS with SNI
        base = self._get_base_config()
        base["spec"]["use_tls"] = {
            "skip_server_verification": {},
            "sni": "backend.example.com",
        }
        test_cases.append(
            TestCase(
                name="tls-enabled-sni",
                description="TLS enabled with SNI",
                config=base,
                expected_success=True,
                group="tls",
                ui_option=6,
            ),
        )

        # ========================================================================
        # Group 6: Circuit Breaker Tests (UI option 9)
        # ========================================================================

        # Test 6.1: Default circuit breaker
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {"default_circuit_breaker": {}}
        test_cases.append(
            TestCase(
                name="circuit-breaker-default",
                description="Default circuit breaker settings",
                config=base,
                expected_success=True,
                group="circuit_breaker",
                ui_option=9,
            ),
        )

        # Test 6.2: Disable circuit breaker
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {"disable_circuit_breaker": {}}
        test_cases.append(
            TestCase(
                name="circuit-breaker-disable",
                description="Disable circuit breaker",
                config=base,
                expected_success=True,
                group="circuit_breaker",
                ui_option=9,
            ),
        )

        # Test 6.3: Custom circuit breaker
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {
            "circuit_breaker": {
                "max_connections": 1000,
                "max_pending_requests": 500,
                "max_requests": 1000,
                "max_retries": 3,
            },
        }
        test_cases.append(
            TestCase(
                name="circuit-breaker-custom",
                description="Custom circuit breaker settings",
                config=base,
                expected_success=True,
                group="circuit_breaker",
                ui_option=9,
            ),
        )

        # ========================================================================
        # Group 7: Outlier Detection Tests (UI option 10)
        # ========================================================================

        # Test 7.1: Disable outlier detection (default)
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {"disable_outlier_detection": {}}
        test_cases.append(
            TestCase(
                name="outlier-disable",
                description="Disable outlier detection",
                config=base,
                expected_success=True,
                group="outlier_detection",
                ui_option=10,
            ),
        )

        # Test 7.2: Enable outlier detection
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {
            "outlier_detection": {
                "consecutive_5xx": 5,
                "interval": 10000,
                "base_ejection_time": 30000,
                "max_ejection_percent": 50,
            },
        }
        test_cases.append(
            TestCase(
                name="outlier-enable",
                description="Enable outlier detection",
                config=base,
                expected_success=True,
                group="outlier_detection",
                ui_option=10,
            ),
        )

        # ========================================================================
        # Group 8: Panic Threshold Tests (UI option 11)
        # ========================================================================

        # Test 8.1: No panic threshold (default)
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {"no_panic_threshold": {}}
        test_cases.append(
            TestCase(
                name="panic-threshold-none",
                description="No panic threshold",
                config=base,
                expected_success=True,
                group="panic_threshold",
                ui_option=11,
            ),
        )

        # Test 8.2: Custom panic threshold
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {"panic_threshold": 25}
        test_cases.append(
            TestCase(
                name="panic-threshold-25",
                description="Panic threshold at 25%",
                config=base,
                expected_success=True,
                group="panic_threshold",
                ui_option=11,
            ),
        )

        # ========================================================================
        # Group 9: Subset Load Balancing Tests (UI option 12)
        # ========================================================================

        # Test 9.1: Disable subsets (default)
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {"disable_subsets": {}}
        test_cases.append(
            TestCase(
                name="subset-disable",
                description="Disable subset load balancing",
                config=base,
                expected_success=True,
                group="subset",
                ui_option=12,
            ),
        )

        # Test 9.2: Enable subsets
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {
            "enable_subsets": {
                "endpoint_subsets": [
                    {"keys": ["version"]},
                ],
            },
        }
        test_cases.append(
            TestCase(
                name="subset-enable",
                description="Enable subset load balancing",
                config=base,
                expected_success=True,
                group="subset",
                ui_option=12,
            ),
        )

        # ========================================================================
        # Group 10: HTTP Protocol Tests (UI option 13)
        # ========================================================================

        # Test 10.1: Auto HTTP config (default)
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {"auto_http_config": {}}
        test_cases.append(
            TestCase(
                name="http-proto-auto",
                description="Auto HTTP protocol negotiation",
                config=base,
                expected_success=True,
                group="http_protocol",
                ui_option=13,
            ),
        )

        # Test 10.2: HTTP/1 config
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {
            "http1_config": {
                "header_transformation": {},
            },
        }
        test_cases.append(
            TestCase(
                name="http-proto-http1",
                description="HTTP/1.x protocol",
                config=base,
                expected_success=True,
                group="http_protocol",
                ui_option=13,
            ),
        )

        # Test 10.3: HTTP/2 config
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {"http2_options": {}}
        test_cases.append(
            TestCase(
                name="http-proto-http2",
                description="HTTP/2 protocol",
                config=base,
                expected_success=True,
                group="http_protocol",
                ui_option=13,
            ),
        )

        # ========================================================================
        # Group 11: Proxy Protocol Tests (UI option 14)
        # ========================================================================

        # Test 11.1: Disable proxy protocol (default)
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {"disable_proxy_protocol": {}}
        test_cases.append(
            TestCase(
                name="proxy-proto-disable",
                description="Disable proxy protocol",
                config=base,
                expected_success=True,
                group="proxy_protocol",
                ui_option=14,
            ),
        )

        # Test 11.2: Proxy Protocol v1
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {"proxy_protocol_v1": {}}
        test_cases.append(
            TestCase(
                name="proxy-proto-v1",
                description="Proxy Protocol v1",
                config=base,
                expected_success=True,
                group="proxy_protocol",
                ui_option=14,
            ),
        )

        # Test 11.3: Proxy Protocol v2
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {"proxy_protocol_v2": {}}
        test_cases.append(
            TestCase(
                name="proxy-proto-v2",
                description="Proxy Protocol v2",
                config=base,
                expected_success=True,
                group="proxy_protocol",
                ui_option=14,
            ),
        )

        # ========================================================================
        # Group 12: LB Source IP Persistence Tests (UI option 15)
        # ========================================================================

        # Test 12.1: Disable LB source IP persistence (default)
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {"disable_lb_source_ip_persistance": {}}
        test_cases.append(
            TestCase(
                name="lb-src-ip-disable",
                description="Disable LB source IP persistence",
                config=base,
                expected_success=True,
                group="lb_source_ip",
                ui_option=15,
            ),
        )

        # Test 12.2: Enable LB source IP persistence
        base = self._get_base_config()
        base["spec"]["advanced_options"] = {"enable_lb_source_ip_persistance": {}}
        test_cases.append(
            TestCase(
                name="lb-src-ip-enable",
                description="Enable LB source IP persistence",
                config=base,
                expected_success=True,
                group="lb_source_ip",
                ui_option=15,
            ),
        )

        # ========================================================================
        # Minimal Config Test (baseline)
        # ========================================================================

        test_cases.insert(
            0,
            TestCase(
                name="minimal-baseline",
                description="Minimal config (origin_servers + port only)",
                config=self._get_base_config(),
                expected_success=True,
                group="baseline",
                ui_option=0,
            ),
        )

        return test_cases

    async def _execute_request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        json_data: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any] | None, str | None]:
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
        sent_spec: dict[str, Any],
        received_spec: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        """Compare sent spec with received spec to find server-applied defaults.

        Returns:
            Dict mapping field paths to {sent: value, received: value}
        """
        differences: dict[str, dict[str, Any]] = {}

        def flatten(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
            """Flatten nested dict to dot-notation paths."""
            result: dict[str, Any] = {}
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

        # Create origin_pool
        create_url = f"{self.api_url}/api/config/namespaces/{self.namespace}/origin_pools"
        status, body, error = await self._execute_request(client, "POST", create_url, full_config)

        result = TestResult(
            test_name=test_name,
            description=test_case.description,
            sent_config=full_config,
            success=status in [200, 201],
            status_code=status,
            response_body=body,
            error_message=error,
            group=test_case.group,
            ui_option=test_case.ui_option,
        )

        if result.success:
            self.created_resources.append(test_name)

            # Read back the created resource to capture server-populated values
            await asyncio.sleep(0.5)  # Small delay for consistency
            read_url = (
                f"{self.api_url}/api/config/namespaces/{self.namespace}/origin_pools/{test_name}"
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
                f"{self.api_url}/api/config/namespaces/{self.namespace}/origin_pools/{name}"
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

        all_test_cases = self._get_test_cases()

        # Filter by group if specified
        if self.test_group:
            test_cases = [tc for tc in all_test_cases if tc.group == self.test_group]
            logger.info("Filtered to group '%s': %d tests", self.test_group, len(test_cases))
        else:
            test_cases = all_test_cases

        if not test_cases:
            logger.warning("No test cases to run!")
            return report

        headers = self._get_auth_headers()

        logger.info("Starting origin_pool advanced case study with %d test cases", len(test_cases))
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
                    "%s [%s] %s: status=%s",
                    status_emoji,
                    test_case.group,
                    test_case.name,
                    result.status_code,
                )
                if result.error_message and not result.success:
                    logger.info("   Error: %s", result.error_message[:100])

            # Cleanup
            logger.info("-" * 60)
            cleaned = await self.cleanup(client)
            logger.info("Cleaned up %d test resources", cleaned)

        # Analyze results
        self._analyze_results(report)

        report.duration_seconds = time.monotonic() - start
        return report

    def _analyze_results(self, report: CaseStudyReport) -> None:
        """Analyze test results to determine defaults and enum values."""
        # Find baseline test result (minimal config)
        baseline = None
        for result in report.test_results:
            if result.test_name.startswith(f"{self.test_prefix}-minimal"):
                baseline = result
                break

        if not baseline or not baseline.success:
            report.summary = "Baseline test failed - unable to determine defaults"
            return

        # Collect all discovered defaults from baseline
        if baseline.received_spec:
            for field, values in baseline.discovered_defaults.items():
                if values.get("sent") is None:  # Server-applied default
                    report.discovered_defaults[field] = values.get("received")

        # Analyze enum values discovered from successful tests
        lb_algos_seen: set[str] = set()
        endpoint_selections_seen: set[str] = set()

        for result in report.test_results:
            if result.success and result.received_spec:
                spec = result.received_spec
                if "loadbalancer_algorithm" in spec:
                    lb_algos_seen.add(spec["loadbalancer_algorithm"])
                if "endpoint_selection" in spec:
                    endpoint_selections_seen.add(spec["endpoint_selection"])

        report.enum_values_discovered["loadbalancer_algorithm"] = sorted(lb_algos_seen)
        report.enum_values_discovered["endpoint_selection"] = sorted(endpoint_selections_seen)

        # Determine OneOf defaults by comparing with baseline
        # What value appears when nothing is specified?
        if baseline.received_spec:
            spec = baseline.received_spec
            advanced = spec.get("advanced_options") or {}

            # Port choice
            if "port" in spec:
                report.oneof_defaults["port_choice"] = "port"
            elif "automatic_port" in spec:
                report.oneof_defaults["port_choice"] = "automatic_port"
            elif "lb_port" in spec:
                report.oneof_defaults["port_choice"] = "lb_port"

            # TLS choice
            if "no_tls" in spec:
                report.oneof_defaults["tls_choice"] = "no_tls"
            elif "use_tls" in spec:
                report.oneof_defaults["tls_choice"] = "use_tls"

            # Connection pool reuse
            if "enable_conn_pool_reuse" in spec:
                report.oneof_defaults["upstream_conn_pool_reuse"] = "enable_conn_pool_reuse"
            elif "disable_conn_pool_reuse" in spec:
                report.oneof_defaults["upstream_conn_pool_reuse"] = "disable_conn_pool_reuse"

            # Check advanced_options for other OneOf defaults
            if "default_circuit_breaker" in advanced:
                report.oneof_defaults["circuit_breaker_choice"] = "default_circuit_breaker"
            elif "disable_circuit_breaker" in advanced:
                report.oneof_defaults["circuit_breaker_choice"] = "disable_circuit_breaker"

            if "disable_outlier_detection" in advanced:
                report.oneof_defaults["outlier_detection_choice"] = "disable_outlier_detection"

            if "no_panic_threshold" in advanced:
                report.oneof_defaults["panic_threshold_type"] = "no_panic_threshold"

            if "disable_subsets" in advanced:
                report.oneof_defaults["subset_choice"] = "disable_subsets"

            if "auto_http_config" in advanced:
                report.oneof_defaults["http_protocol_type"] = "auto_http_config"

            if "disable_proxy_protocol" in advanced:
                report.oneof_defaults["proxy_protocol_choice"] = "disable_proxy_protocol"

            if "disable_lb_source_ip_persistance" in advanced:
                report.oneof_defaults["lb_source_ip_persistence_choice"] = (
                    "disable_lb_source_ip_persistance"
                )

        # Document UI vs Server defaults
        # Based on plan: UI shows LB_OVERRIDE, server applies ROUND_ROBIN
        if baseline.received_spec:
            lb_algo = baseline.received_spec.get("loadbalancer_algorithm")
            report.ui_vs_server_defaults["loadbalancer_algorithm"] = {
                "ui_default": "LB_OVERRIDE",
                "server_default": lb_algo,
                "match": lb_algo == "LB_OVERRIDE",
            }

        # Build summary
        successful_tests = [r for r in report.test_results if r.success]
        failed_tests = [r for r in report.test_results if not r.success]

        # Group results by group
        groups: dict[str, list[TestResult]] = {}
        for result in report.test_results:
            if result.group not in groups:
                groups[result.group] = []
            groups[result.group].append(result)

        summary_lines = [
            "Origin Pool Advanced Case Study Results:",
            f"  - {len(successful_tests)}/{len(report.test_results)} tests succeeded",
            "",
            "Results by Group:",
        ]

        for group_name, results in sorted(groups.items()):
            passed = sum(1 for r in results if r.success)
            summary_lines.append(f"  - {group_name}: {passed}/{len(results)} passed")

        if report.discovered_defaults:
            summary_lines.append("")
            summary_lines.append("Server-Applied Defaults (from baseline):")
            summary_lines.extend(
                f"  - {field_name}: {value}"
                for field_name, value in sorted(report.discovered_defaults.items())
            )

        if report.oneof_defaults:
            summary_lines.append("")
            summary_lines.append("OneOf Default Selections:")
            summary_lines.extend(
                f"  - {field_name}: {value}"
                for field_name, value in sorted(report.oneof_defaults.items())
            )

        if report.ui_vs_server_defaults:
            summary_lines.append("")
            summary_lines.append("UI vs Server Defaults:")
            for field_name, info in report.ui_vs_server_defaults.items():
                match_str = "✅" if info["match"] else "⚠️ DIFFERS"
                summary_lines.append(
                    f"  - {field_name}: UI='{info['ui_default']}', Server='{info['server_default']}' {match_str}",
                )

        if failed_tests:
            summary_lines.append("")
            summary_lines.append("Failed Tests:")
            summary_lines.extend(
                f"  - {result.test_name}: {result.error_message}" for result in failed_tests
            )

        report.summary = "\n".join(summary_lines)


def generate_markdown_report(report: CaseStudyReport, output_path: Path) -> None:
    """Generate markdown report from case study results."""
    lines = [
        "# Origin Pool Advanced Configuration Case Study",
        "",
        f"**Generated**: {report.timestamp}",
        f"**API URL**: `{report.api_url}`",
        f"**Namespace**: `{report.namespace}`",
        f"**Duration**: {report.duration_seconds:.1f}s",
        "",
        "## Executive Summary",
        "",
        "This case study tests all 15 UI configuration options for origin_pool to discover:",
        "- Constrained values (enum options, OneOf choices)",
        "- Server-applied defaults (values applied when fields omitted)",
        "- UI defaults vs Server defaults (preselected UI values vs API behavior)",
        "",
        "## Test Results by Group",
        "",
    ]

    # Group results
    groups: dict[str, list[TestResult]] = {}
    for result in report.test_results:
        if result.group not in groups:
            groups[result.group] = []
        groups[result.group].append(result)

    for group_name, results in sorted(groups.items()):
        passed = sum(1 for r in results if r.success)
        lines.append(f"### {group_name.replace('_', ' ').title()} ({passed}/{len(results)} passed)")
        lines.append("")
        lines.append("| Test | Description | Status | HTTP Code |")
        lines.append("|------|-------------|--------|-----------|")

        for result in results:
            status = "✅" if result.success else "❌"
            lines.append(
                f"| {result.test_name} | {result.description} | {status} | {result.status_code} |",
            )
        lines.append("")

    # Server-Applied Defaults Section
    lines.extend(
        [
            "## Server-Applied Defaults",
            "",
            "These values are automatically applied by the API when fields are omitted:",
            "",
            "| Field | Default Value |",
            "|-------|---------------|",
        ],
    )

    for field_name, value in sorted(report.discovered_defaults.items()):
        value_str = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        lines.append(f"| `{field_name}` | `{value_str}` |")

    # OneOf Defaults Section
    lines.extend(
        [
            "",
            "## OneOf Default Selections",
            "",
            "For fields with mutually exclusive choices, these are the server defaults:",
            "",
            "| OneOf Group | Default Selection |",
            "|-------------|-------------------|",
        ],
    )

    for group, selection in sorted(report.oneof_defaults.items()):
        lines.append(f"| `{group}` | `{selection}` |")

    # UI vs Server Defaults Section
    if report.ui_vs_server_defaults:
        lines.extend(
            [
                "",
                "## UI vs Server Defaults",
                "",
                "⚠️ **Important**: UI preselected values may differ from server defaults!",
                "",
                "| Field | UI Default | Server Default | Match |",
                "|-------|------------|----------------|-------|",
            ],
        )

        for field, info in report.ui_vs_server_defaults.items():
            match_str = "✅" if info["match"] else "❌"
            lines.append(
                f"| `{field}` | `{info['ui_default']}` | `{info['server_default']}` | {match_str} |",
            )

    # Enum Values Section
    if report.enum_values_discovered:
        lines.extend(
            [
                "",
                "## Discovered Enum Values",
                "",
            ],
        )

        for enum_name, values in sorted(report.enum_values_discovered.items()):
            lines.append(f"### {enum_name}")
            lines.append("")
            lines.extend(f"- `{value}`" for value in values)
            lines.append("")

    # Recommendations Section
    lines.extend(
        [
            "## Recommendations",
            "",
            "### Update `config/validation_schema.yaml`",
            "",
            "Add the following enum_values and conditional_requirements:",
            "",
            "```yaml",
            "# See the plan for complete YAML additions",
            "```",
            "",
            "### Update `config/discovered_defaults.yaml`",
            "",
            "Add the comprehensive defaults and oneof_defaults sections.",
            "",
            "---",
            "",
            "*Report generated by `scripts/case_study_origin_pool_advanced.py`*",
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
        "discovered_defaults": report.discovered_defaults,
        "oneof_defaults": report.oneof_defaults,
        "enum_values_discovered": report.enum_values_discovered,
        "ui_vs_server_defaults": report.ui_vs_server_defaults,
        "test_results": [
            {
                "test_name": r.test_name,
                "description": r.description,
                "group": r.group,
                "ui_option": r.ui_option,
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
        description="Origin pool advanced configuration case study",
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
    parser.add_argument(
        "--group",
        choices=[
            "baseline",
            "port",
            "connectivity",
            "lb_algorithm",
            "endpoint_selection",
            "tls",
            "circuit_breaker",
            "outlier_detection",
            "panic_threshold",
            "subset",
            "http_protocol",
            "proxy_protocol",
            "lb_source_ip",
        ],
        help="Run only tests in this group",
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
    case_study = OriginPoolCaseStudy(
        api_url=api_url,
        api_token=api_token,
        namespace=args.namespace,
        verbose=args.verbose,
        test_group=args.group,
    )

    report = await case_study.run()

    # Generate reports
    output_dir = Path(args.output_dir)
    md_path = output_dir / "origin-pool-advanced-case-study.md"
    json_path = output_dir / "origin-pool-advanced-case-study.json"

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
