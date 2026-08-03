"""POST+GET+DELETE roundtrip for server-default and response-only field discovery."""

from __future__ import annotations

import copy
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class RoundtripResult:
    """Result of the POST+GET+DELETE roundtrip."""

    resource_name: str
    sent_spec: dict
    received_spec: dict
    server_default_fields: dict[str, Any]
    response_only_fields: list[str]
    post_status: int
    get_status: int
    delete_status: int
    cleanup_ok: bool
    error: str | None = None


def _flatten(d: dict, prefix: str = "") -> dict:
    """Flatten nested dict to dot-notation paths."""
    result = {}
    for key, value in d.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict) and value:
            result.update(_flatten(value, path))
        else:
            result[path] = value
    return result


def _compare_payloads(
    sent: dict,
    received_spec: dict,
    received_metadata: dict | None = None,
) -> tuple[dict, list[str]]:
    """Compare sent spec vs received spec and metadata."""
    sent_flat = _flatten(sent)
    received_flat = _flatten(received_spec)

    server_defaults = {key: value for key, value in received_flat.items() if key not in sent_flat}
    response_only: list[str] = []

    if received_metadata:
        meta_flat = _flatten(received_metadata)
        for key in meta_flat:
            if any(
                key.startswith(prefix)
                for prefix in (
                    "uid",
                    "tenant",
                    "creation_timestamp",
                    "modification_timestamp",
                    "object_index",
                    "owner_view",
                    "creator_id",
                    "creator_class",
                )
            ):
                response_only.append(f"metadata.{key}")

    return server_defaults, response_only


class RoundtripProbe:
    """Executes POST+GET+DELETE to discover server defaults and response-only fields."""

    def __init__(
        self,
        api_url: str,
        api_token: str,
        namespace: str,
        resource_type: str,
        resource_endpoint: str,
    ) -> None:
        """Initialize with API connection details."""
        self.api_url = api_url.rstrip("/")
        self.api_token = api_token
        self.namespace = namespace
        self.resource_type = resource_type
        self.resource_endpoint = resource_endpoint

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"APIToken {self.api_token}",
            "Content-Type": "application/json",
        }

    def _base_url(self) -> str:
        return f"{self.api_url}/api/config/namespaces/{self.namespace}/{self.resource_endpoint}"

    async def run(self, base_payload: dict, rate_limiter: Any) -> RoundtripResult:
        """Execute the full POST+GET+DELETE roundtrip."""
        resource_name = f"audit-{self.resource_type}-{uuid.uuid4().hex[:8]}"
        payload = copy.deepcopy(base_payload)
        payload.setdefault("metadata", {})["name"] = resource_name
        payload["metadata"]["namespace"] = self.namespace

        post_status = 0
        get_status = 0
        delete_status = 0
        sent_spec = payload.get("spec", {})
        received_spec: dict = {}
        received_metadata: dict = {}

        async with httpx.AsyncClient(
            headers=self._headers(), verify=True, follow_redirects=True
        ) as client:
            try:
                async with rate_limiter:
                    resp = await client.post(self._base_url(), json=payload, timeout=30)
                    post_status = resp.status_code

                if post_status not in (200, 201):
                    error_text = resp.text[:500]
                    return RoundtripResult(
                        resource_name=resource_name,
                        sent_spec=sent_spec,
                        received_spec={},
                        server_default_fields={},
                        response_only_fields=[],
                        post_status=post_status,
                        get_status=0,
                        delete_status=0,
                        cleanup_ok=False,
                        error=f"POST failed ({post_status}): {error_text}",
                    )

                get_url = f"{self._base_url()}/{resource_name}"
                async with rate_limiter:
                    get_resp = await client.get(get_url, timeout=30)
                    get_status = get_resp.status_code

                if get_status != 200:
                    logger.warning("GET failed for %s: status %d", resource_name, get_status)

                try:
                    get_body = get_resp.json()
                    received_spec = get_body.get("spec", {})
                    received_metadata = get_body.get("metadata", {})
                except (json.JSONDecodeError, ValueError):
                    received_spec = {}
                    received_metadata = {}

            finally:
                delete_url = f"{self._base_url()}/{resource_name}"
                try:
                    async with rate_limiter:
                        del_resp = await client.delete(delete_url, timeout=30)
                        delete_status = del_resp.status_code
                except Exception as e:
                    logger.warning("Cleanup DELETE failed for %s: %s", resource_name, e)
                    delete_status = 0

        server_defaults, response_only = _compare_payloads(
            sent_spec, received_spec, received_metadata
        )
        cleanup_ok = delete_status in (200, 202, 204)

        return RoundtripResult(
            resource_name=resource_name,
            sent_spec=sent_spec,
            received_spec=received_spec,
            server_default_fields=server_defaults,
            response_only_fields=response_only,
            post_status=post_status,
            get_status=get_status,
            delete_status=delete_status,
            cleanup_ok=cleanup_ok,
        )
