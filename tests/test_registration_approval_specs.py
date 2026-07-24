#!/usr/bin/env python3
"""Cross-spec invariant tests for the registrationApprovalReq enrichment (S6-A).

Guards the injection of a top-level ``state`` ($ref to the existing
``registrationObjectState`` enum) and the schema-level ``x-f5xc-action: approve``
marker onto ``registrationApprovalReq`` in the generated ``ce_management.json``.
Skips if the specs have not been generated yet.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

API_DIR = Path(__file__).parent.parent / "docs" / "specifications" / "api"
pytestmark = pytest.mark.skipif(not API_DIR.exists(), reason="specs not generated")

OBJECT_STATE_MEMBERS = [
    "NOTSET",
    "NEW",
    "APPROVED",
    "ADMITTED",
    "RETIRED",
    "FAILED",
    "DONE",
    "PENDING",
    "ONLINE",
    "UPGRADING",
    "MAINTENANCE",
    "FAILED_INACTIVE",
]


def _ce_mgmt() -> dict:
    return json.loads((API_DIR / "ce_management.json").read_text())


def test_approval_req_has_state_ref():
    schemas = _ce_mgmt()["components"]["schemas"]
    props = schemas["registrationApprovalReq"]["properties"]
    assert props["state"] == {"$ref": "#/components/schemas/registrationObjectState"}


def test_object_state_enum_members():
    schemas = _ce_mgmt()["components"]["schemas"]
    assert schemas["registrationObjectState"]["enum"] == OBJECT_STATE_MEMBERS


def test_approval_req_has_action_extension():
    schemas = _ce_mgmt()["components"]["schemas"]
    assert schemas["registrationApprovalReq"]["x-f5xc-action"] == "approve"
