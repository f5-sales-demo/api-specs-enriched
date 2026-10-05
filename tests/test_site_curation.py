"""Contract tests for the reviewed v12 site API curation boundary."""

from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from scripts.contract_diff import run_contract_diff
from scripts.site_curation import (
    curate_catalog_metadata,
    curate_metadata,
    curate_sidecar_resources,
    curate_spec,
    load_policy,
)


def _policy_entry():
    return next(item for item in load_policy()["operations"] if item["topic"] == "cloud-connect")


def _document(entry):
    path = entry["path"]
    method = entry["method"]
    return {
        "paths": {
            path: {
                method: {
                    "operationId": entry["operationId"],
                    "responses": {"200": {"$ref": "#/components/responses/Legacy"}},
                },
                "patch": {
                    "operationId": "retained.patch",
                    "description": ("Kind of view e.g. Aws_vpc_site, azure_vnet_site."),
                    "x-f5xc-operation-aliases": [entry["operationId"], "retained.alias"],
                    "responses": {"200": {"$ref": "#/components/schemas/Shared"}},
                },
            },
        },
        "components": {
            "responses": {
                "Legacy": {
                    "description": "legacy response",
                    "content": {
                        "application/json": {
                            "schema": {
                                "allOf": [
                                    {"$ref": "#/components/schemas/Exclusive"},
                                    {"$ref": "#/components/schemas/Shared"},
                                ]
                            },
                        }
                    },
                }
            },
            "schemas": {
                "Exclusive": {"properties": {"cycle": {"$ref": "#/components/schemas/Exclusive"}}},
                "Shared": {"properties": {"cycle": {"$ref": "#/components/schemas/Shared"}}},
                "Unrelated": {"type": "string"},
            },
        },
    }


def _policy_with_retained_patch(entry):
    return {
        "policyVersion": "v12.0.0",
        "operations": [
            entry,
            {
                "method": "patch",
                "path": entry["path"],
                "operationId": "retained.patch",
                "topic": entry["topic"],
                "reason": "Explicitly retained mixed-method operation",
                "disposition": "retain",
            },
        ],
    }


def test_policy_adds_only_the_five_reviewed_appstack_identities():
    policy = load_policy()
    prior = json.loads(Path("config/curation/v11.0.0.json").read_text())
    assert len(prior["operations"]) == 42
    assert len(policy["operations"]) == 47
    assert len({item["path"] for item in policy["operations"]}) == 41
    assert all(item in policy["operations"] for item in prior["operations"])
    assert Counter(item["topic"] for item in policy["operations"]) == {
        "secure-mesh-v1": 5,
        "provider-cloud-site": 24,
        "cloud-connect": 13,
        "appstack-site": 5,
    }
    assert {
        item["operationId"] for item in policy["operations"] if item["topic"] == "appstack-site"
    } == {
        f"ves.io.schema.views.voltstack_site.API.{action}"
        for action in ("Create", "Replace", "List", "Get", "Delete")
    }
    assert "SITE_MANAGEMENT_APP_STACK_SITES" in policy["navigationValues"]
    assert all(item["reason"] and item["disposition"] == "remove" for item in policy["operations"])


def test_exact_removal_preserves_mixed_path_shared_cycle_and_is_idempotent(tmp_path):
    entry = _policy_entry()
    spec = _document(entry)
    policy = _policy_with_retained_patch(entry)
    audit_path = tmp_path / "curation.json"
    audit = curate_spec(spec, policy, audit_path=audit_path)
    assert len(audit["removed"]) == 1
    assert audit["retained_shared"] == ["Shared"]
    assert audit["removed_schemas"] == ["Exclusive"]
    assert audit["removed_other_components"] == [{"kind": "responses", "name": "Legacy"}]
    assert list(spec["paths"][entry["path"]]) == ["patch"]
    assert set(spec["components"]["schemas"]) == {"Shared", "Unrelated"}
    assert not spec["components"]["responses"]
    retained = spec["paths"][entry["path"]]["patch"]
    assert "aws_vpc_site" not in retained["description"].lower()
    assert retained["x-f5xc-operation-aliases"] == ["retained.alias"]
    assert json.loads(audit_path.read_text()) == audit
    snapshot = json.dumps(spec)
    second = curate_spec(spec, policy)
    assert len(second["already_absent"]) == 1
    assert json.dumps(spec) == snapshot


def test_absent_upstream_identity_is_accepted():
    entry = _policy_entry()
    spec = {"paths": {}, "components": {"schemas": {}}}
    audit = curate_spec(spec, {"policyVersion": "v12.0.0", "operations": [entry]})
    assert len(audit["already_absent"]) == 1
    assert not audit["newly_discovered_candidates"]


@pytest.mark.parametrize("change", ["operationId", "path", "method"])
def test_moved_or_changed_identity_fails_and_records_candidate(change, tmp_path):
    entry = _policy_entry()
    spec = _document(entry)
    original = spec["paths"][entry["path"]].pop(entry["method"])
    if change == "operationId":
        original["operationId"] += "Moved"
        spec["paths"][entry["path"]][entry["method"]] = original
    elif change == "path":
        spec["paths"][entry["path"] + "/new"] = {entry["method"]: original}
    else:
        spec["paths"][entry["path"]]["get"] = original
    policy = _policy_with_retained_patch(entry)
    before = copy.deepcopy(spec)
    path = tmp_path / "audit.json"
    with pytest.raises(ValueError, match="Unreviewed legacy site API drift"):
        curate_spec(spec, policy, audit_path=path)
    assert spec == before
    assert json.loads(path.read_text())["newly_discovered_candidates"]


def test_new_family_operation_fails_even_when_approved_identity_is_present():
    entry = _policy_entry()
    spec = _document(entry)
    spec["paths"]["/api/config/cloud_connects/new"] = {
        "get": {"operationId": "new.cloud_connect.API.Get"}
    }
    with pytest.raises(ValueError, match="new candidates"):
        curate_spec(spec, _policy_with_retained_patch(entry))


def test_new_appstack_operation_and_moved_identity_fail_closed():
    entry = next(item for item in load_policy()["operations"] if item["topic"] == "appstack-site")
    spec = _document(entry)
    spec["paths"]["/api/config/namespaces/{namespace}/voltstack_sites/new"] = {
        "get": {"operationId": "ves.io.schema.views.voltstack_site.API.New"}
    }
    with pytest.raises(ValueError, match="new candidates"):
        curate_spec(spec, _policy_with_retained_patch(entry))
    spec["paths"]["/api/config/namespaces/{namespace}/voltstack_sites/new"]["get"][
        "operationId"
    ] = "ves.io.schema.views.securemesh_site_v2.API.New"
    with pytest.raises(ValueError, match="new candidates"):
        curate_spec(spec, _policy_with_retained_patch(entry))
    del spec["paths"]["/api/config/namespaces/{namespace}/voltstack_sites/new"]
    spec["paths"][entry["path"]][entry["method"]]["operationId"] += "Moved"
    with pytest.raises(ValueError, match="changed identities"):
        curate_spec(spec, _policy_with_retained_patch(entry))


def test_appstack_navigation_is_removed_but_historical_wire_value_is_retained():
    entry = next(item for item in load_policy()["operations"] if item["topic"] == "appstack-site")
    spec = _document(entry)
    spec["components"]["schemas"]["commonDashboardLinkType"] = {
        "enum": ["SITE_MANAGEMENT_APP_STACK_SITES", "SITE_MANAGEMENT_SECURE_MESH_V2_SITES"],
        "description": (
            "- SITE_MANAGEMENT_APP_STACK_SITES: App Stack sites\n"
            "- SITE_MANAGEMENT_SECURE_MESH_V2_SITES: Mesh sites"
        ),
    }
    spec["components"]["schemas"]["HistoricalSiteAppType"] = {
        "enum": ["SITE_APPTYPE_APPSTACK", "SITE_APPTYPE_MESH"]
    }
    policy = _policy_with_retained_patch(entry)
    policy["navigationValues"] = ["SITE_MANAGEMENT_APP_STACK_SITES"]
    curate_spec(spec, policy)
    assert spec["components"]["schemas"]["commonDashboardLinkType"]["enum"] == [
        "SITE_MANAGEMENT_SECURE_MESH_V2_SITES"
    ]
    assert (
        "SITE_MANAGEMENT_APP_STACK_SITES"
        not in spec["components"]["schemas"]["commonDashboardLinkType"]["description"]
    )
    assert spec["components"]["schemas"]["HistoricalSiteAppType"]["enum"] == [
        "SITE_APPTYPE_APPSTACK",
        "SITE_APPTYPE_MESH",
    ]


def test_smsv2_six_operations_and_schema_closure_are_preserved():
    entry = _policy_entry()
    spec = _document(entry)
    for i in range(5):
        spec["paths"][f"/api/config/securemesh_site_v2s/{i}"] = {
            "get": {
                "operationId": f"securemesh_site_v2.API.Get{i}",
                "responses": {"200": {"$ref": "#/components/schemas/Shared"}},
            }
        }
    spec["paths"]["/api/sync-cloud-data/securemesh_site_v2/cloud_resources"] = {
        "get": {
            "operationId": "securemesh_site_v2.API.CloudResources",
            "responses": {"200": {"$ref": "#/components/schemas/Shared"}},
        }
    }
    before = copy.deepcopy(spec["paths"]["/api/sync-cloud-data/securemesh_site_v2/cloud_resources"])
    curate_spec(spec, _policy_with_retained_patch(entry), protect_smsv2=True)
    assert spec["paths"]["/api/sync-cloud-data/securemesh_site_v2/cloud_resources"] == before


def test_retained_family_guard_rejects_shared_schema_changes(monkeypatch):
    entry = _policy_entry()
    spec = _document(entry)
    spec["paths"]["/api/config/namespaces/{namespace}/workloads"] = {
        "get": {
            "operationId": "ves.io.schema.views.workload.API.List",
            "responses": {"200": {"$ref": "#/components/schemas/Shared"}},
        }
    }
    spec["components"]["schemas"]["Shared"]["description"] = (
        "Kind of view e.g. Aws_vpc_site, azure_vnet_site."
    )
    monkeypatch.setattr("scripts.site_curation.RETAINED_FAMILY_COUNTS", {"workload": 1})
    with pytest.raises(ValueError, match="changed retained workload"):
        curate_spec(
            spec,
            _policy_with_retained_patch(entry),
            protect_retained_families=True,
        )


def test_contract_diff_normalizes_only_reviewed_removals():
    entry = _policy_entry()
    before = _document(entry)
    after = copy.deepcopy(before)
    policy = _policy_with_retained_patch(entry)
    curate_spec(after, policy)
    assert not run_contract_diff(before, after, curation_policy=policy)


def test_published_metadata_and_sidecars_drop_curated_resource_keys():
    metadata = {
        "x-f5xc-primary-resources": [
            {"name": "aws_vpc_site"},
            {"name": "voltstack_site"},
            {"name": "securemesh_site_v2"},
        ],
        "x-f5xc-critical-resources": ["cloud_connect", "origin_pool"],
    }
    curate_metadata(metadata)
    assert metadata["x-f5xc-primary-resources"] == [{"name": "securemesh_site_v2"}]
    assert metadata["x-f5xc-critical-resources"] == ["origin_pool"]

    sidecar = {
        "resources": {
            "azure_vnet_site": {"_meta": {"verification": "assumed"}},
            "views_aws_vpc_site": {"_meta": {"verification": "unverified"}},
            "views_voltstack_site": {"_meta": {"verification": "unverified"}},
            "securemesh_site_v2": {"_meta": {"verification": "verified"}},
        },
        "_coverage": {
            "total_explicit": 4,
            "verified": 1,
            "assumed": 1,
            "unverified": 2,
            "default_deny_worklist": [],
            "default_deny_worklist_count": 0,
        },
    }
    curate_sidecar_resources(sidecar)
    assert list(sidecar["resources"]) == ["securemesh_site_v2"]
    assert sidecar["_coverage"]["total_explicit"] == 1
    assert sidecar["_coverage"]["assumed"] == 0
    assert sidecar["_coverage"]["unverified"] == 0

    coverage = {
        "resources": {
            "views_gcp_vpc_site": {"disposition": "excluded"},
            "securemesh_site_v2": {"disposition": "generated"},
        },
        "coverage": {"generated": 1, "manual": 0, "excluded": 1, "total": 2},
    }
    curate_sidecar_resources(coverage)
    assert list(coverage["resources"]) == ["securemesh_site_v2"]
    assert coverage["coverage"] == {
        "generated": 1,
        "manual": 0,
        "excluded": 0,
        "total": 1,
    }

    catalog = {
        "categories": [
            {
                "fieldMetadata": {
                    "view_kind": {
                        "description": "e.g. Aws_vpc_site, azure_vnet_site",
                        "console": {
                            "resources": {
                                "cloud_connect": {"hidden": False},
                                "securemesh_site_v2": {"hidden": False},
                            }
                        },
                    }
                },
                "oneOfVariants": {"scope_choice": ["cloud_connect", "namespace"]},
            }
        ]
    }
    curate_catalog_metadata(catalog)
    category = catalog["categories"][0]
    assert category["fieldMetadata"]["view_kind"]["console"]["resources"] == {
        "securemesh_site_v2": {"hidden": False}
    }
    assert category["oneOfVariants"]["scope_choice"] == ["namespace"]
    assert "aws_vpc_site" not in category["fieldMetadata"]["view_kind"]["description"].lower()


def test_reviewed_navigation_values_and_short_prose_are_removed_idempotently():
    entry = _policy_entry()
    spec = _document(entry)
    spec["components"]["schemas"]["commonDashboardLinkType"] = {
        "type": "string",
        "enum": ["SITE_MANAGEMENT_AWS_VPC_SITES", "SITE_MANAGEMENT_AWS_TGW_SITES"],
        "description": (
            "Link Type\n\n"
            "- SITE_MANAGEMENT_AWS_VPC_SITES: SITE_MANAGEMENT_AWS_VPC_SITES\n\n"
            "Legacy site route\n"
            "- SITE_MANAGEMENT_AWS_TGW_SITES: SITE_MANAGEMENT_AWS_TGW_SITES\n\n"
            "Transit gateway route"
        ),
    }
    spec["components"]["schemas"]["Shared"]["x-f5xc-description-short"] = (
        "e.g. Aws_vpc_site, azure_vnet_site"
    )
    policy = {
        **_policy_with_retained_patch(entry),
        "navigationValues": ["SITE_MANAGEMENT_AWS_VPC_SITES"],
    }
    audit = curate_spec(spec, policy)
    assert audit["navigation_values_removed"] == ["SITE_MANAGEMENT_AWS_VPC_SITES"]
    link_type = spec["components"]["schemas"]["commonDashboardLinkType"]
    assert link_type["enum"] == ["SITE_MANAGEMENT_AWS_TGW_SITES"]
    assert "AWS_VPC" not in link_type["description"]
    assert "AWS_TGW" in link_type["description"]
    assert (
        "aws_vpc_site"
        not in spec["components"]["schemas"]["Shared"]["x-f5xc-description-short"].lower()
    )
    snapshot = json.dumps(spec)
    curate_spec(spec, policy)
    assert json.dumps(spec) == snapshot
