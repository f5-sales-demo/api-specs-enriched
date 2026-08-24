"""#1142: the shipped specs must carry the corrected requiredness markers.

The enricher tests prove the mechanism and the config-guard tests prove the
patterns match. Neither proves the built artifact under
``docs/specifications/api/`` actually ships the correction — and the artifact is
what downstream codegen reads.

It is also where an ordering bug hides. ``x-ves-required`` is corrected by
``config/schema_overrides.yaml``, but ``x-f5xc-required-for.create`` is *derived*
from that marker earlier in the pipeline. Correct the marker after the derivation
and you ship a property that says "not required" in one field and "required" in
the other, with the consumer reading whichever it happens to check —
terraform-provider-xcsh reads both, ORed together, so the stale one wins.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

SPEC_DIR = Path(__file__).parent.parent / "docs" / "specifications" / "api"


def load_schema(spec_file: str, schema_name: str) -> dict:
    path = SPEC_DIR / spec_file
    if not path.exists():  # pragma: no cover - the specs are committed
        pytest.skip(f"{path} not built")
    with path.open() as f:
        spec = json.load(f)
    schemas = spec.get("components", {}).get("schemas", {})
    assert schema_name in schemas, f"{schema_name} missing from {spec_file}"
    return schemas[schema_name]


def required_for_create(prop: dict) -> bool:
    return bool((prop.get("x-f5xc-required-for") or {}).get("create"))


class TestForceIsNotRequired:
    """Marked required upstream, not enforced by the API.

    Verified live 2026-07-30 against site cem1-l1:
      POST /api/config/namespaces/system/sites/cem1-l1/upgrade_sw
        omitting force and version -> 400 "version empty in the request"
        omitting force only        -> 200
    The API names version and never mentions force.
    """

    @pytest.mark.parametrize("schema_name", ["siteUpgradeSWRequest", "siteUpgradeOSRequest"])
    def test_force_is_marked_not_required(self, schema_name):
        props = load_schema("sites.json", schema_name)["properties"]
        assert props["force"].get("x-ves-required") == "false", (
            f"{schema_name}.force must be marked NOT required. Forcing a caller to state "
            "a value for a flag that overrides the platform's own safety checks is the "
            "wrong thing to make unavoidable."
        )

    @pytest.mark.parametrize("schema_name", ["siteUpgradeSWRequest", "siteUpgradeOSRequest"])
    def test_the_marker_is_negated_not_deleted(self, schema_name):
        # The contract-diff gate rejects removing a key upstream provides, and it is
        # right to: erasing the marker also erases the evidence that F5 ever asserted
        # it. Negating keeps the disagreement auditable and classifies as additive.
        props = load_schema("sites.json", schema_name)["properties"]
        assert "x-ves-required" in props["force"]
        rules = props["force"].get("x-ves-validation-rules") or {}
        assert rules.get("ves.io.schema.rules.message.required") == "false", (
            "the nested rule must be negated too — the pipeline derives "
            "x-f5xc-required-for from it as well as from the marker"
        )
        assert "ves.io.schema.rules.message.required" in rules

    @pytest.mark.parametrize("schema_name", ["siteUpgradeSWRequest", "siteUpgradeOSRequest"])
    def test_the_derived_field_agrees(self, schema_name):
        props = load_schema("sites.json", schema_name)["properties"]
        assert not required_for_create(props["force"]), (
            f"{schema_name}.force has no x-ves-required but x-f5xc-required-for.create "
            "is still true. The correction landed after the derivation — consumers OR "
            "the two signals together, so the stale one decides."
        )

    @pytest.mark.parametrize("schema_name", ["siteUpgradeSWRequest", "siteUpgradeOSRequest"])
    def test_no_description_claims_it_is_required(self, schema_name):
        # F5 asserts requiredness in prose too — the upstream description reads
        # "... Required: YES ...". A corrected marker beside a sentence saying the
        # opposite is worse than either alone, and the description is what the API
        # viewer publishes. Asserted as the ABSENCE of a requiredness claim rather
        # than as exact wording, so reworded upstream text cannot reintroduce one.
        props = load_schema("sites.json", schema_name)["properties"]
        for field in ("description", "x-f5xc-description-short", "x-f5xc-description-medium"):
            text = props["force"].get(field) or ""
            assert "Required: YES" not in text, (
                f"{schema_name}.force {field} still claims the field is required"
            )

    @pytest.mark.parametrize("schema_name", ["siteUpgradeSWRequest", "siteUpgradeOSRequest"])
    def test_the_description_still_explains_the_flag(self, schema_name):
        # Removing the false claim must not remove the useful part.
        props = load_schema("sites.json", schema_name)["properties"]
        assert "Force upgrade" in (props["force"].get("description") or "")

    @pytest.mark.parametrize("schema_name", ["siteUpgradeSWRequest", "siteUpgradeOSRequest"])
    def test_the_example_survives_the_description_override(self, schema_name):
        # x-f5xc-example is extracted FROM the description prose, so replacing the
        # description dropped it the first time. Overriding a description must not
        # cost the field its example.
        props = load_schema("sites.json", schema_name)["properties"]
        assert props["force"].get("x-f5xc-example"), (
            f"{schema_name}.force lost x-f5xc-example — the description override "
            "removed the 'Example:' line the extractor reads"
        )

    @pytest.mark.parametrize("schema_name", ["siteUpgradeSWRequest", "siteUpgradeOSRequest"])
    def test_version_keeps_both_signals(self, schema_name):
        # The control: version IS enforced, and correcting force must not touch it.
        props = load_schema("sites.json", schema_name)["properties"]
        assert props["version"].get("x-ves-required") == "true"
        assert required_for_create(props["version"])
        rules = props["version"].get("x-ves-validation-rules") or {}
        assert rules.get("ves.io.schema.rules.message.required") == "true"


class TestPassportIsRequired:
    """Unmarked upstream, enforced by the API.

    Verified live 2026-07-30 against registration r-d2e92964-...-615e9b2daf93:
      POST /api/register/namespaces/system/registration/{name}/approve
        without passport -> 500 "Validation approval: Passport is required"
        with passport    -> past that check, fails on the state transition instead
    Presence is what is checked, and before the state gate: a deliberately wrong
    passport reached the same state error.
    """

    def test_passport_carries_the_marker(self):
        props = load_schema("ce_management.json", "registrationApprovalReq")["properties"]
        assert props["passport"].get("x-ves-required") == "true", (
            "passport must be marked required. Its absence is why "
            "terraform-provider-xcsh#636 failed with a 500 and why that repository "
            "carries a hand-written workaround to supply it."
        )

    def test_the_derived_field_agrees(self):
        props = load_schema("ce_management.json", "registrationApprovalReq")["properties"]
        assert required_for_create(props["passport"]), (
            "passport is marked required but x-f5xc-required-for.create is false. The "
            "correction landed after the derivation."
        )

    def test_decorative_fields_are_not_swept_in(self):
        # required_fields already lists labels and annotations (see #1150). Marking
        # passport must not conflate "the server rejects this if absent" with "this
        # is a field a user fills in".
        props = load_schema("ce_management.json", "registrationApprovalReq")["properties"]
        for decorative in ("labels", "annotations"):
            assert "x-ves-required" not in props[decorative]
            assert not required_for_create(props[decorative])


def test_no_property_description_reasserts_requiredness() -> None:
    master_path = SPEC_DIR / "openapi.json"
    if not master_path.exists():  # pragma: no cover - artifacts are committed
        pytest.skip(f"{master_path} not built")
    master = json.loads(master_path.read_text())
    marker = re.compile(r"(?im)^\s*Required:\s*(?:YES|NO)\s*[.!]?\s*$")
    offenders: list[str] = []
    for schema_name, schema in master.get("components", {}).get("schemas", {}).items():
        for property_name, prop in schema.get("properties", {}).items():
            if not isinstance(prop, dict):
                continue
            fields = (
                "description",
                "x-f5xc-description-short",
                "x-f5xc-description-medium",
            )
            offenders.extend(
                f"{schema_name}.{property_name}.{field}"
                for field in fields
                if marker.search(prop.get(field, ""))
            )
    assert not offenders, f"requiredness prose reappeared: {offenders[:10]}"
