"""One bounded image query; emit only an allowlisted, digest-bound receipt.

This is instrumentation, not a repair or proof of a tenant-owned prerequisite.
Bootstrap issuance and resource changes deliberately remain outside this tool.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

IMAGE_PATH = "/api/register/namespaces/system/get-image-download-url"
PHASES = ("baseline", "site_created", "bootstrap_issued", "site_removed")
MAX_RESPONSE_BYTES = 1048576


def _https_url(value: object) -> bool:
    if not isinstance(value, str) or any(ord(char) <= 32 for char in value):
        return False
    try:
        url = urlsplit(value)
        return bool(
            url.scheme == "https" and url.hostname and not url.username and not url.password
        )
    except ValueError:
        return False


def project_response(status: int, body: object) -> dict:
    """Discard all response text and identifiers, including unknown fields."""
    fields = ("image_download_url", "image_md5_download_url")
    valid = {
        field: _https_url(body.get(field)) if isinstance(body, dict) else False for field in fields
    }
    outcome = "http_error"
    if 200 <= status < 300:
        outcome = "complete_response" if all(valid.values()) else "incomplete_response"
    elif isinstance(body, dict):
        message = body.get("message")
        if isinstance(message, str) and "number of maurice_config object is not one" in message:
            outcome = "maurice_lookup_cardinality_error"
    return {
        "http_status": status,
        "outcome": outcome,
        "valid_url_fields": valid,
        "lookup_scope": "unknown",
        "lookup_count": "unknown",
    }


def collect_receipt(
    api_url: str,
    expected_url: str,
    token: str,
    phase: str,
    source_commit: str,
    spec_sha256: str,
    *,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    """Validate identity and provenance before sending one non-replayed request."""
    if (
        api_url != expected_url
        or not _https_url(api_url)
        or urlsplit(api_url).path
        or urlsplit(api_url).query
        or urlsplit(api_url).fragment
    ):
        msg = "tenant URL must equal the expected HTTPS origin"
        raise ValueError(msg)
    if (
        phase not in PHASES
        or not re.fullmatch(r"[a-f0-9]{40}", source_commit)
        or not re.fullmatch(r"[a-f0-9]{64}", spec_sha256)
    ):
        msg = "invalid phase or source provenance"
        raise ValueError(msg)
    if not token or any(ord(char) <= 32 for char in token):
        msg = "credential is missing or malformed"
        raise ValueError(msg)
    response_projection: dict[str, object] = {"http_status": None, "outcome": "transport_error"}
    # Never use ambient proxies, redirects, retries, or logging for this authenticated query.
    with httpx.Client(
        transport=transport, follow_redirects=False, trust_env=False, timeout=30
    ) as client:
        try:
            with client.stream(
                "POST",
                api_url + IMAGE_PATH,
                headers={"Authorization": "APIToken " + token},
                json={"provider": "kvm"},
            ) as response:
                data = bytearray()
                for chunk in response.iter_bytes(chunk_size=65536):
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        response_projection = {
                            "http_status": response.status_code,
                            "outcome": "response_too_large",
                        }
                        break
                else:
                    try:
                        body = json.loads(data)
                    except (ValueError, UnicodeError):
                        body = None
                    response_projection = project_response(response.status_code, body)
        except httpx.HTTPError:
            # Exception strings may contain signed URLs, headers, or server diagnostics.
            pass
    receipt = {
        "schema": "f5xc-image-resolution-receipt/v1",
        "issue": "api-specs-enriched#1805",
        "phase": phase,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "tenant_alias": "expected_tenant",
        "tenant_origin_matched": True,
        "source_commit": source_commit,
        "spec_sha256": spec_sha256,
        "request": {"method": "POST", "path": IMAGE_PATH, "provider": "kvm"},
        "response": response_projection,
    }
    receipt["receipt_sha256"] = hashlib.sha256(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return receipt


def main() -> int:
    """Read credentials only from the environment; print no raw diagnostics."""
    # The discovery package configures root logging during import. CLI output
    # must remain receipt-only even when HTTP libraries log request locations.
    logging.disable(logging.CRITICAL)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-url", required=True)
    parser.add_argument("--phase", choices=PHASES, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--spec-sha256", required=True)
    args = parser.parse_args()
    try:
        receipt = collect_receipt(
            os.environ.get("XCSH_API_URL", ""),
            args.expected_url,
            os.environ.get("XCSH_API_TOKEN", ""),
            args.phase,
            args.source_commit,
            args.spec_sha256,
        )
    except ValueError:
        print('{"outcome":"invalid_input"}')
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0 if receipt["response"]["outcome"] == "complete_response" else 1


if __name__ == "__main__":
    raise SystemExit(main())
