"""Current console image resolution consumes Site UIDs, not software-version IDs."""

import copy
from pathlib import Path

from scripts.contract_diff import run_contract_diff
from scripts.utils.schema_override_enricher import SchemaOverrideEnricher


def test_early_image_corrections_preserve_upstream_mapping_contract():
    spec = {
        "components": {
            "schemas": {
                "virtual_applianceGetImageResponse": {
                    "type": "object",
                    "properties": {"images": {"type": "object"}},
                },
            },
        },
    }
    original = copy.deepcopy(spec)
    enricher = SchemaOverrideEnricher()
    early = enricher.enrich_spec(spec, corrections_only=True)
    images = early["components"]["schemas"]["virtual_applianceGetImageResponse"]["properties"][
        "images"
    ]
    assert "Mapping keyed by each requested Site UID" in images["description"]
    assert "additionalProperties" not in images
    assert not run_contract_diff(original, early)

    documented = enricher.enrich_spec(copy.deepcopy(early))
    mapping = documented["components"]["schemas"]["virtual_applianceGetImageResponse"][
        "properties"
    ]["images"]["additionalProperties"]
    assert mapping["type"] == "object"
    assert "download_image_link" in mapping["properties"]
    assert any(
        "additionalProperties" in violation.pointer
        for violation in run_contract_diff(original, documented)
    )


def test_site_uid_image_response_has_a_typed_mapping():
    enricher = SchemaOverrideEnricher(
        config_path=Path(__file__).parents[1] / "config/schema_overrides.yaml"
    )
    spec = {
        "components": {
            "schemas": {
                "virtual_applianceGetImageRequest": {
                    "type": "object",
                    "properties": {"uids": {"type": "array", "items": {"type": "string"}}},
                },
                "virtual_applianceGetImageResponse": {
                    "type": "object",
                    "properties": {"images": {"type": "object"}},
                },
            }
        }
    }
    schemas = enricher.enrich_spec(spec)["components"]["schemas"]
    request = schemas["virtual_applianceGetImageRequest"]
    assert "Site object UIDs" in request["properties"]["uids"]["description"]
    assert "owner_view.uid" in request["properties"]["uids"]["description"]
    response = schemas["virtual_applianceGetImageResponse"]
    assert response["required"] == ["images"]
    image = response["properties"]["images"]["additionalProperties"]
    assert image["type"] == "object"
    assert set(image["properties"]) == {
        "download_image_link",
        "copy_image_name",
        "image_md5_sum",
        "error_description",
    }
    assert image["properties"]["download_image_link"]["format"] == "uri"
    assert image["properties"]["image_md5_sum"]["pattern"] == "^[0-9a-fA-F]{32}$"
    assert image["properties"]["error_description"]["x-f5xc-sensitive"] is True
