"""Guard current product API fields whose wire names explicitly contain ``legacy``."""

import json
from pathlib import Path

EXPECTED_SCHEMA_PROPERTIES = {
    ("network_interfaceGetSpecType", "legacy_interface"),
    ("network_interfaceReplaceSpecType", "legacy_interface"),
    ("schemaHeaderTransformationType", "legacy_header_transformation"),
    ("schemanetwork_policyGetSpecType", "legacy_rules"),
    ("schemanetwork_policyReplaceSpecType", "legacy_rules"),
    ("schemasecret_policyGetSpecType", "legacy_rule_list"),
    ("schemasecret_policyReplaceSpecType", "legacy_rule_list"),
    ("schemaservice_policyGetSpecType", "legacy_rule_list"),
    ("schemaservice_policyReplaceSpecType", "legacy_rule_list"),
    ("virtual_networkCreateSpecType", "legacy_type"),
    ("virtual_networkGetSpecType", "legacy_type"),
    ("virtual_networkReplaceSpecType", "legacy_type"),
}


def _oneof_variants(schema: dict) -> set[str]:
    variants: set[str] = set()
    for key, value in schema.items():
        if not key.startswith("x-ves-oneof-field-"):
            continue
        parsed = json.loads(value) if isinstance(value, str) else value
        if isinstance(parsed, list):
            variants.update(item for item in parsed if isinstance(item, str))
    return variants


def test_legacy_named_properties_are_current_api_contract_not_compatibility_aliases():
    occurrences: list[tuple[str, str, str]] = []
    index = json.loads(Path("docs/specifications/api/index.json").read_text(encoding="utf-8"))
    domain_files = sorted(entry["file"] for entry in index["specifications"])

    for filename in domain_files:
        spec_file = Path("docs/specifications/api") / filename
        spec = json.loads(spec_file.read_text(encoding="utf-8"))
        schemas = (spec.get("components") or {}).get("schemas") or {}
        for schema_name, schema in schemas.items():
            properties = schema.get("properties") or {}
            for property_name, prop in properties.items():
                if not property_name.startswith("legacy_"):
                    continue
                occurrences.append((spec_file.name, schema_name, property_name))

                assert property_name in _oneof_variants(schema)
                assert not schema.get("deprecated")
                assert not schema.get("x-ves-deprecated")
                assert not prop.get("deprecated")
                assert not prop.get("x-ves-deprecated")
                assert "x-f5xc-wire-name" not in prop

                refs = prop.get("allOf") or [prop]
                ref = next((entry.get("$ref") for entry in refs if isinstance(entry, dict)), None)
                assert ref
                assert ref.startswith("#/components/schemas/")
                assert ref.rsplit("/", 1)[-1] in schemas

    unique = {(schema, prop) for _, schema, prop in occurrences}
    assert len(occurrences) == 20
    assert unique == EXPECTED_SCHEMA_PROPERTIES
