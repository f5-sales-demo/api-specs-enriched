"""Constraint boundary prober orchestrator."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from rate_limiter import RateLimitConfig, RateLimiter

from scripts.discovery.error_parser import parse_constraint_from_error
from scripts.discovery.probes import ProbeRequest, ProbeResponse, ResourceAuditResult
from scripts.discovery.probes.enum import EnumProbe
from scripts.discovery.probes.numeric import NumericBoundaryProbe
from scripts.discovery.probes.oneof import OneOfProbe
from scripts.discovery.probes.required import RequiredFieldProbe
from scripts.discovery.probes.roundtrip import RoundtripProbe
from scripts.discovery.probes.string import StringProbe

logger = logging.getLogger(__name__)

RESOURCE_ENDPOINTS: dict[str, str] = {
    "healthcheck": "healthchecks",
    "origin_pool": "origin_pools",
    "http_loadbalancer": "http_loadbalancers",
    "tcp_loadbalancer": "tcp_loadbalancers",
    "app_firewall": "app_firewalls",
    "service_policy": "service_policys",
    "securemesh_site_v2": "securemesh_site_v2s",
}

DOMAIN_MAP: dict[str, str] = {
    "healthcheck": "virtual",
    "origin_pool": "virtual",
    "http_loadbalancer": "virtual",
    "tcp_loadbalancer": "virtual",
    "app_firewall": "bot_and_threat_defense",
    "service_policy": "network_security",
    "securemesh_site_v2": "cloud_infrastructure",
}


class ConstraintProber:
    """Orchestrates boundary probing for a single resource type."""

    def __init__(
        self,
        api_url: str,
        api_token: str,
        namespace: str = "r-mordasiewicz",
        requests_per_second: float = 5.0,
        dry_run: bool = False,
    ) -> None:
        """Initialize prober with API credentials and rate limit settings."""
        self.api_url = api_url.rstrip("/")
        self.api_token = api_token
        self.namespace = namespace
        self.dry_run = dry_run

        rate_cfg = RateLimitConfig(requests_per_second=requests_per_second, burst_limit=5)
        self.rate_limiter = RateLimiter(rate_cfg)

        self._numeric = NumericBoundaryProbe()
        self._string = StringProbe()
        self._oneof = OneOfProbe()
        self._enum = EnumProbe()
        self._required = RequiredFieldProbe()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"APIToken {self.api_token}",
            "Content-Type": "application/json",
        }

    def _post_url(self, resource_type: str) -> str:
        endpoint = RESOURCE_ENDPOINTS.get(resource_type, f"{resource_type}s")
        return f"{self.api_url}/api/config/namespaces/{self.namespace}/{endpoint}"

    async def _execute_probe(
        self,
        client: httpx.AsyncClient,
        probe: ProbeRequest,
        resource_type: str,
    ) -> ProbeResponse:
        """Send one probe request and return a ProbeResponse."""
        if self.dry_run:
            return ProbeResponse(
                field_path=probe.field_path,
                status_code=0,
                accepted=False,
                error_message="[dry-run: not sent]",
                parsed_constraint=None,
                response_body=None,
            )

        url = self._post_url(resource_type)
        async with self.rate_limiter:
            try:
                resp = await client.post(url, json=probe.payload, timeout=30)
            except httpx.RequestError as e:
                return ProbeResponse(
                    field_path=probe.field_path,
                    status_code=0,
                    accepted=False,
                    error_message=str(e),
                    parsed_constraint=None,
                    response_body=None,
                )

        accepted = resp.is_success
        error_text: str | None = None

        if not accepted:
            try:
                body = resp.json()
                error_text = body.get("message", body.get("error", resp.text[:500]))
            except (json.JSONDecodeError, ValueError):
                error_text = resp.text[:500]

        parsed = parse_constraint_from_error(error_text) if error_text else None

        return ProbeResponse(
            field_path=probe.field_path,
            status_code=resp.status_code,
            accepted=accepted,
            error_message=error_text,
            parsed_constraint=parsed,
            response_body=resp.json() if accepted else None,
        )

    async def probe_resource(
        self,
        resource_type: str,
        spec: dict,
        base_payload: dict,
        current_constraints: dict | None = None,
    ) -> ResourceAuditResult:
        """Run all probes for a resource type."""
        result = ResourceAuditResult(
            resource_type=resource_type,
            timestamp=datetime.now(UTC).isoformat(),
            namespace=self.namespace,
        )

        schemas = spec.get("components", {}).get("schemas", {})
        create_schema = schemas.get(f"{resource_type}CreateSpecType", {})
        properties = create_schema.get("properties", {})

        oneof_groups = {
            k.replace("x-ves-oneof-field-", ""): k
            for k in create_schema
            if k.startswith("x-ves-oneof-field-")
        }

        async with httpx.AsyncClient(
            headers=self._headers(), verify=True, follow_redirects=True
        ) as client:
            # Phase A — validation error probes
            for prop_name, prop_schema in properties.items():
                field_path = f"spec.{prop_name}"
                prop_type = prop_schema.get("type")
                field_constraints = (current_constraints or {}).get(prop_name)

                probes: list[ProbeRequest] = []
                if prop_type in ("integer", "number"):
                    probes = self._numeric.generate_probes(
                        field_path, prop_schema, base_payload, field_constraints
                    )
                elif prop_schema.get("enum") or prop_schema.get("x-ves-enum"):
                    probes = self._enum.generate_probes(
                        field_path, prop_schema, base_payload, field_constraints
                    )
                elif prop_type == "string":
                    probes = self._string.generate_probes(
                        field_path, prop_schema, base_payload, field_constraints
                    )

                responses: list[ProbeResponse] = []
                for probe in probes:
                    logger.debug("Probing: %s", probe.description)
                    r = await self._execute_probe(client, probe, resource_type)
                    responses.append(r)
                    result.probes_executed += 1
                    if r.accepted:
                        result.probes_accepted += 1
                    else:
                        result.probes_rejected += 1
                    if r.error_message and not r.parsed_constraint:
                        result.errors_unparseable += 1

                if responses:
                    if prop_type in ("integer", "number"):
                        field_result = self._numeric.interpret_results(field_path, responses)
                    elif prop_schema.get("enum") or prop_schema.get("x-ves-enum"):
                        field_result = self._enum.interpret_results(field_path, responses)
                    else:
                        field_result = self._string.interpret_results(field_path, responses)
                    result.fields.append(field_result)

            # Required field probes
            for prop_name in properties:
                field_path = f"spec.{prop_name}"
                probes = self._required.generate_probes(field_path, {}, base_payload, None)
                for probe in probes:
                    r = await self._execute_probe(client, probe, resource_type)
                    result.probes_executed += 1
                    if r.accepted:
                        result.probes_accepted += 1
                    else:
                        result.probes_rejected += 1
                    field_result = self._required.interpret_results(field_path, [r])
                    result.fields.append(field_result)

            # OneOf group probes
            for group_name in oneof_groups:
                field_path = f"spec.{group_name}"
                probes = self._oneof.generate_probes(field_path, create_schema, base_payload, None)
                responses = []
                for probe in probes:
                    r = await self._execute_probe(client, probe, resource_type)
                    responses.append(r)
                    result.probes_executed += 1
                    if r.accepted:
                        result.probes_accepted += 1
                    else:
                        result.probes_rejected += 1
                if responses:
                    field_result = self._oneof.interpret_results(field_path, responses)
                    result.fields.append(field_result)

        # Phase B — roundtrip probe (live only)
        if not self.dry_run:
            endpoint = RESOURCE_ENDPOINTS.get(resource_type, f"{resource_type}s")
            roundtrip = RoundtripProbe(
                api_url=self.api_url,
                api_token=self.api_token,
                namespace=self.namespace,
                resource_type=resource_type,
                resource_endpoint=endpoint,
            )
            rt = await roundtrip.run(base_payload, self.rate_limiter)
            result.server_default_fields = rt.server_default_fields
            result.response_only_fields = rt.response_only_fields
            result.cleanup_status = "clean" if rt.cleanup_ok else "failed"
            result.probes_executed += 3  # POST + GET + DELETE
        else:
            result.cleanup_status = "skipped"

        return result


def _load_base_payload(resource_type: str) -> dict:
    """Load minimum base payload from config/minimum_configs.yaml."""
    config_path = Path(__file__).parent.parent.parent / "config" / "minimum_configs.yaml"
    with config_path.open() as f:
        config = yaml.safe_load(f)
    example_json = config["resources"][resource_type].get("example_json", "{}")
    return json.loads(example_json)


def _load_published_spec(resource_type: str) -> dict:
    """Load the published OpenAPI spec for the resource's domain."""
    domain = DOMAIN_MAP.get(resource_type, "virtual")
    spec_path = (
        Path(__file__).parent.parent.parent / "docs" / "specifications" / "api" / f"{domain}.json"
    )
    with spec_path.open() as f:
        return json.load(f)


def _result_to_dict(obj: Any) -> Any:
    """Recursively convert dataclass instances to plain dicts."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _result_to_dict(v) for k, v in vars(obj).items()}
    if isinstance(obj, list):
        return [_result_to_dict(i) for i in obj]
    return obj


async def _run(resource_type: str, dry_run: bool, output: str | None, rate: float) -> None:
    api_url = os.environ.get("F5XC_API_URL", "")
    api_token = os.environ.get("F5XC_API_TOKEN", "")
    namespace = os.environ.get("F5XC_NAMESPACE", "r-mordasiewicz")

    if not dry_run and not api_token:
        print(
            "ERROR: F5XC_API_TOKEN not set. Use --dry-run or set credentials.",
            file=sys.stderr,
        )
        sys.exit(1)

    prober = ConstraintProber(
        api_url=api_url or "https://placeholder.example.com",
        api_token=api_token,
        namespace=namespace,
        requests_per_second=rate,
        dry_run=dry_run,
    )

    base_payload = _load_base_payload(resource_type)
    spec = _load_published_spec(resource_type)

    print(f"Probing {resource_type} ({'dry-run' if dry_run else 'live'})...")
    audit_result = await prober.probe_resource(resource_type, spec, base_payload)

    result_dict = _result_to_dict(audit_result)

    if output:
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        with Path(output).open("w") as f:
            json.dump(result_dict, f, indent=2)
        print(f"Results written to {output}")
    else:
        print(json.dumps(result_dict, indent=2))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Constraint boundary prober")
    parser.add_argument("--resource", default="healthcheck", help="Resource type to probe")
    parser.add_argument("--dry-run", action="store_true", help="Generate probes without API calls")
    parser.add_argument("--output", help="Write results to JSON file")
    parser.add_argument("--rate", type=float, default=5.0, help="Requests per second")
    args = parser.parse_args()

    asyncio.run(_run(args.resource, args.dry_run, args.output, args.rate))
