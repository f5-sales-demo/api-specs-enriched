# Lower-privilege wire qualification does not grant operational admission.
import hashlib
import json
from copy import deepcopy

import pytest

from scripts.utils.gcp_contract import (
    GCP_OPERATIONAL_COMMANDS,
    GCP_OPERATIONAL_CORE_SEED,
    GCP_OPERATIONAL_RUNTIME_DEPENDENCIES,
    validate_gcp_operational_candidate_openapi,
    validate_gcp_operational_profile,
    validate_gcp_operational_release,
    validate_gcp_terraform_ha_lifecycle,
)


def fixture():
    return {
        "paths": {
            "/api/operate/namespaces/{namespace}/sites/{site}/vpm/debug/{node}/exec-user": {
                "post": {
                    "operationId": "ves.io.schema.operate.debug.CustomPublicAPI.ExecUser",
                    "parameters": [
                        {"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
                        for name in ("namespace", "site", "node")
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/debugExecRequest"}
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/debugExecResponse"}
                                }
                            }
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "debugExecRequest": {
                    "properties": {"command": {"type": "array", "items": {"type": "string"}}}
                },
                "debugExecResponse": {
                    "properties": {"output": {"type": "string"}, "return_code": {"type": "integer"}}
                },
            }
        },
    }


def test_operational_transport_and_minimum_coverage():
    validate_gcp_operational_candidate_openapi(fixture())
    assert len(GCP_OPERATIONAL_CORE_SEED) == 17
    assert set(GCP_OPERATIONAL_RUNTIME_DEPENDENCIES) == {"coredns", "kube-proxy", "kube-multus-ds"}
    assert GCP_OPERATIONAL_COMMANDS["list"] == ["crictl-ps-a"]


@pytest.mark.parametrize(
    "change", ["operation", "parameters", "request", "response", "command", "output", "code"]
)
def test_rejects_transport_drift(change):
    value = deepcopy(fixture())
    operation = next(iter(value["paths"].values()))["post"]
    schemas = value["components"]["schemas"]
    if change == "operation":
        operation["operationId"] = "privileged-exec"
    elif change == "parameters":
        operation["parameters"].append({"name": "command", "in": "query"})
    elif change == "request":
        operation["requestBody"]["required"] = False
    elif change == "response":
        operation["responses"] = {}
    elif change == "command":
        schemas["debugExecRequest"]["properties"]["command"]["type"] = "string"
    else:
        schemas["debugExecResponse"]["properties"][
            "output" if change == "output" else "return_code"
        ]["type"] = "object"
    with pytest.raises(ValueError, match="Operational Site CLI"):
        validate_gcp_operational_candidate_openapi(value)


def profile_fixture():
    core = list(GCP_OPERATIONAL_CORE_SEED)
    profile = {
        "schemaVersion": 2,
        "scope": "original-three-node-operational-health",
        "maximumAgeMs": 300000,
        "minimumRoundIntervalMs": 45000,
        "sourceHashes": {"schema": "sha256:" + "a" * 64},
        "parserVersions": {"runtime": "crictl-table-and-inspect/v2"},
        "qualification": dict.fromkeys(
            ("evidenceSha256", "discoveryCleanupSha256"),
            "sha256:" + "b" * 64,
        ),
        "inventoryServices": core,
        "verRunningStates": ["OPERATIONAL_READY"],
        "exclusions": [
            {
                "service": service,
                "reason": "auxiliary-outside-operational-scope",
                "evidenceSha256": "sha256:" + "d" * 64,
            }
            for service in ("vector-agent", "voucher", "kube-state-metrics")
        ],
        "roles": {
            "control": {
                "components": core,
                "containers": [
                    {
                        "service": service,
                        "name": service,
                        "namespace": "default",
                        "podPrefix": service + "-",
                        "podShape": "daemon",
                    }
                    for service in core + list(GCP_OPERATIONAL_RUNTIME_DEPENDENCIES)
                ],
                "diagnostics": ["peer-connectivity", "required-domains"],
            }
        },
    }

    def pin(value):
        return (
            "sha256:"
            + hashlib.sha256(
                json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )

    return profile, pin, validate_gcp_operational_profile


def test_profile_requires_frozen_complete_coverage():
    profile, pin, validate = profile_fixture()
    validate(profile, pin(profile))
    for change in ("pin", "core", "dependency", "diagnostics", "bounds", "qualification", "shape"):
        candidate = deepcopy(profile)
        if change == "pin":
            expected = "sha256:" + "c" * 64
        else:
            if change == "core":
                candidate["inventoryServices"] = candidate["inventoryServices"][:-1]
            elif change == "dependency":
                candidate["roles"]["control"]["containers"] = [
                    c
                    for c in candidate["roles"]["control"]["containers"]
                    if c["service"] != "coredns"
                ]
            elif change == "diagnostics":
                candidate["roles"]["control"]["diagnostics"] = []
            elif change == "bounds":
                candidate["maximumAgeMs"] = 600000
            elif change == "qualification":
                candidate["qualification"].pop("discoveryCleanupSha256")
            else:
                candidate["roles"]["control"]["containers"][0]["podShape"] = "arbitrary"
            expected = pin(candidate)
        with pytest.raises(ValueError, match="Operational"):
            validate(candidate, expected)


@pytest.mark.parametrize("field", ["inventoryServices", "verRunningStates", "exclusions"])
@pytest.mark.parametrize("value", [None, {}, [None], [{}], ["duplicate", "duplicate"]])
def test_rejects_malformed_profile_lists(field, value):
    profile, pin, validate = profile_fixture()
    profile[field] = value
    with pytest.raises(ValueError, match="Operational"):
        validate(profile, pin(profile))


def test_auxiliary_placement_can_be_role_dependent():
    profile, pin, validate = profile_fixture()
    profile["exclusions"] = [
        e for e in profile["exclusions"] if e["service"] != "kube-state-metrics"
    ]
    profile["roles"]["secondary"] = deepcopy(profile["roles"]["control"])
    profile["roles"]["control"]["containers"].append(
        {
            "service": "kube-state-metrics",
            "name": "kube-state-metrics",
            "namespace": "default",
            "podPrefix": "kube-state-metrics-",
            "podShape": "deployment",
        }
    )
    validate(profile, pin(profile))
    profile["roles"]["control"]["containers"].pop()
    with pytest.raises(ValueError, match="Operational auxiliary"):
        validate(profile, pin(profile))


def test_release_receipt_is_separate_from_frozen_contract():
    profile, pin, validate = profile_fixture()
    contract_digest = pin(profile)
    validate(profile, contract_digest)
    receipt = {
        "schemaVersion": 2,
        "contractFingerprint": contract_digest,
        "bindingFingerprint": "sha256:" + "a" * 64,
        "healthyRoundFingerprints": ["sha256:" + "b" * 64, "sha256:" + "c" * 64],
        "evidenceSha256": "sha256:" + "d" * 64,
        "campaignCleanupSha256": "sha256:" + "e" * 64,
        "objectsAbsent": 18,
        "emptyDestroy": True,
        "stableState": True,
        "repeatedResumeNoSubmission": True,
    }
    validate_gcp_operational_release(receipt, pin(receipt), contract_digest)
    for field in ("objectsAbsent", "emptyDestroy", "stableState", "repeatedResumeNoSubmission"):
        candidate = {**receipt, field: False}
        with pytest.raises(ValueError, match="Operational release"):
            validate_gcp_operational_release(candidate, pin(candidate), contract_digest)
    with pytest.raises(ValueError, match="Operational release"):
        validate_gcp_operational_release(
            profile["qualification"], pin(profile["qualification"]), contract_digest
        )
    assert pin(profile) == contract_digest


def test_placement_contract_requires_exact_full_counts_and_runtime_roles():
    profile, pin, validate = profile_fixture()
    profile["roles"]["control"]["runtimeRole"] = "k8s-master-primary"
    profile["placementCounts"] = dict.fromkeys(
        profile["inventoryServices"] + list(GCP_OPERATIONAL_RUNTIME_DEPENDENCIES), 3
    )
    validate(profile, pin(profile))
    for field, change in [
        ("etcd", 2),
        ("coredns", 1),
        ("asaf", True),
        ("aspen", 0),
        ("bdbewaf", 4),
    ]:
        changed = deepcopy(profile)
        changed["placementCounts"][field] = change
        with pytest.raises(ValueError, match="placement"):
            validate(changed, pin(changed))
    changed = deepcopy(profile)
    del changed["roles"]["control"]["runtimeRole"]
    with pytest.raises(ValueError, match="placement"):
        validate(changed, pin(changed))


def lifecycle_fixture():
    return {
        "schemaVersion": 2,
        "scope": "original-three-node-terraform-ha",
        "region": "northamerica-northeast1",
        "zones": [
            "northamerica-northeast1-a",
            "northamerica-northeast1-b",
            "northamerica-northeast1-c",
        ],
        "machineType": "n2-standard-8",
        "diskGiB": 80,
        "imageIdentitySha256": "sha256:" + "a" * 64,
        "versions": {"terraform": "1.16.6", "google": "8.7.0", "xcsh": "15.13.0"},
        "providerLockSha256": "sha256:" + "b" * 64,
        "operationalContractFingerprint": "sha256:" + "c" * 64,
        "lifecycleEvidenceSha256": "sha256:" + "d" * 64,
    }


@pytest.mark.parametrize("field", list(lifecycle_fixture()))
def test_lifecycle_scope_rejects_drift(field):
    profile = lifecycle_fixture()
    contract = profile["operationalContractFingerprint"]
    validate_gcp_terraform_ha_lifecycle(profile, contract)
    profile[field] = None
    with pytest.raises(ValueError, match="Operational Terraform HA"):
        validate_gcp_terraform_ha_lifecycle(profile, contract)


def test_lifecycle_scope_rejects_extensions_and_integer_booleans():
    profile = lifecycle_fixture()
    contract = profile["operationalContractFingerprint"]
    for changed in (
        {**profile, "qualificationFingerprint": contract},
        {**profile, "diskGiB": True},
    ):
        with pytest.raises(ValueError, match="Operational Terraform HA"):
            validate_gcp_terraform_ha_lifecycle(changed, contract)
