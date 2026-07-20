#!/usr/bin/env python3
"""Live F5 XC API verification of resource naming constraints (plan item F).

These tests create and delete throwaway resources against the live F5 XC API to
empirically confirm the naming rules the enriched specs declare. They are
skipped automatically when credentials are absent (e.g. in CI), so they never
block the normal suite.

Run locally with credentials in the environment (or a gitignored .env):
    XCSH_API_URL=... XCSH_API_TOKEN=... .venv/bin/python -m pytest \
        tests/test_naming_constraints_live.py -m live -o addopts=""

Ground truth captured here (staging, SE tenant, 2026-07-20):
- digit-first / dotted / >63-char names -> HTTP 400 "DNS-1035 label"
- 1-char and <=63-char alpha-first names -> accepted
"""

from __future__ import annotations

import contextlib
import os
import sys
import uuid
from pathlib import Path

import pytest
import requests

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.discover_namespace_crud import get_api_client  # noqa: E402

_HAVE_CREDS = bool(
    (os.environ.get("F5XC_API_URL") or os.environ.get("XCSH_API_URL"))
    and (os.environ.get("F5XC_API_TOKEN") or os.environ.get("XCSH_API_TOKEN"))
)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not _HAVE_CREDS, reason="F5 XC API credentials not set"),
]

TEST_NAMESPACE = os.environ.get("XCSH_TEST_NAMESPACE", "default")


def _healthcheck_payload(name: str, namespace: str) -> dict:
    return {
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "http_health_check": {"path": "/health", "use_origin_server_name": {}},
            "interval": 15,
            "timeout": 3,
            "unhealthy_threshold": 1,
            "healthy_threshold": 3,
            "jitter_percent": 30,
        },
    }


def _create(name: str) -> requests.Response:
    base_url, headers = get_api_client()
    url = f"{base_url}/api/config/namespaces/{TEST_NAMESPACE}/healthchecks"
    return requests.post(
        url, json=_healthcheck_payload(name, TEST_NAMESPACE), headers=headers, timeout=20
    )


def _delete(name: str) -> None:
    base_url, headers = get_api_client()
    url = f"{base_url}/api/config/namespaces/{TEST_NAMESPACE}/healthchecks/{name}"
    with contextlib.suppress(Exception):
        requests.delete(url, headers=headers, timeout=15)


@pytest.fixture
def cleanup():
    """Track created resource names and delete them after the test."""
    created: list[str] = []
    yield created
    for name in created:
        _delete(name)


def test_digit_first_name_rejected_dns1035():
    resp = _create("1-live-probe-digit")
    assert resp.status_code == 400
    assert "DNS-1035 label" in resp.json().get("message", "")


def test_dotted_name_rejected():
    resp = _create("bad.dotted.name")
    assert resp.status_code == 400
    assert "DNS-1035 label" in resp.json().get("message", "")


def test_name_over_63_chars_rejected():
    long_name = "a" + "b" * 62 + "c"  # 64 chars
    resp = _create(long_name)
    assert resp.status_code == 400
    assert "63" in resp.json().get("message", "")


def test_valid_alpha_first_name_accepted(cleanup):
    name = "live-probe-" + uuid.uuid4().hex[:8]
    resp = _create(name)
    assert resp.status_code in (200, 201), resp.text[:300]
    cleanup.append(name)
