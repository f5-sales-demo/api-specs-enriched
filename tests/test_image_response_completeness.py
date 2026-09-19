"""Successful image/template responses must contain usable payloads."""

from pathlib import Path

import pytest

from scripts.utils.schema_override_enricher import SchemaOverrideEnricher


@pytest.mark.parametrize(
    ("name", "fields", "response_format"),
    [
        (
            "registrationGetImageDownloadUrlResp",
            ["image_download_url", "image_md5_download_url"],
            "uri",
        ),
        ("tokenGetCloudInitConfigResp", ["cloud_init_config"], None),
    ],
)
def test_response_completeness_is_schema_owned(name, fields, response_format):
    spec = {
        "components": {
            "schemas": {
                name: {
                    "type": "object",
                    "properties": {field: {"type": "string"} for field in fields},
                }
            }
        }
    }
    enricher = SchemaOverrideEnricher(
        config_path=Path(__file__).parents[1] / "config/schema_overrides.yaml"
    )
    result = enricher.enrich_spec(spec)
    schema = result["components"]["schemas"][name]
    assert set(schema["required"]) == set(fields)
    for field in fields:
        assert schema["properties"][field]["minLength"] == 1
        assert schema["properties"][field].get("format") == response_format
    assert enricher.enrich_spec(result) == result
