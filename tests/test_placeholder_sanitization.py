"""Regression coverage for reserved documentation example values."""
# pylint: disable=use-implicit-booleaness-not-comparison  # Explicit empty results document the expected shape.

from __future__ import annotations

import re
from pathlib import Path

import yaml

from scripts.pipeline import (
    _normalize_domain_names,
    _sanitize_documentation_examples,
    enrich_spec,
    load_config,
)
from scripts.utils.branding import BrandingTransformer


def _sanitize(spec: dict) -> dict:
    config = load_config(Path("config/enrichment.yaml"))
    target_fields = config["target_fields"]
    transformer = BrandingTransformer()
    result = transformer.transform_spec(spec, target_fields)
    result = _sanitize_documentation_examples(result, transformer)
    result, _ = _normalize_domain_names(result, target_fields)
    return result


def test_general_enrichment_does_not_rewrite_example_metadata() -> None:
    config = load_config(Path("config/enrichment.yaml"))

    assert "x-f5xc-example" not in config["target_fields"]


def test_example_metadata_uses_reserved_values() -> None:
    spec = {
        "components": {
            "schemas": {
                "Example": {
                    "description": "Tenant keys include acme-key-foo and acme-key-bar.",
                    "properties": {
                        "api_server": {"x-f5xc-example": "api.acme.com:4430"},
                        "cluster_name": {"x-ves-example": "Acme-ce01"},
                        "company": {"x-f5xc-example": "ACME Ltd."},
                        "email": {"x-f5xc-example": "joe.doe@" + "acme.com"},
                    },
                }
            }
        }
    }

    result = _sanitize(spec)["components"]["schemas"]["Example"]

    assert result["description"] == ("Tenant keys include example-key-foo and example-key-bar.")
    assert result["properties"]["api_server"]["x-f5xc-example"] == ("api.example.com:4430")
    assert result["properties"]["cluster_name"]["x-ves-example"] == "example-ce01"
    assert result["properties"]["company"]["x-f5xc-example"] == "Example Corp"
    assert result["properties"]["email"]["x-f5xc-example"] == "dana@example.com"


def test_opaque_token_examples_do_not_publish_embedded_identity() -> None:
    encoded_identity = ("61" * 60) + ":" + ("62" * 32)
    spec = {
        "components": {
            "schemas": {
                "Example": {
                    "properties": {
                        "token": {
                            "x-ves-example": encoded_identity + ".",
                            "x-f5xc-example": encoded_identity,
                        }
                    }
                }
            }
        }
    }

    token = _sanitize(spec)["components"]["schemas"]["Example"]["properties"]["token"]
    assert token["x-ves-example"] == "example-token-value"
    assert token["x-f5xc-example"] == "example-token-value"


def test_embedded_example_is_sanitized_without_rewriting_title_prose() -> None:
    title = (
        'Company name\nx-displayName: "Company"\nx-example: "Acme Ltd."\n'
        "Volterra metadata remains contract text"
    )
    spec = {"components": {"schemas": {"Example": {"title": title}}}}

    result = _sanitize(spec)["components"]["schemas"]["Example"]["title"]

    assert 'x-example: "Example Corp"' in result
    assert "Volterra metadata remains contract text" in result


def test_rfc_8555_challenge_reference_is_preserved() -> None:
    description = "Create the _acme-challenge TXT record for domain verification."
    spec = {"components": {"schemas": {"Certificate": {"description": description}}}}

    result = _sanitize(spec)["components"]["schemas"]["Certificate"]["description"]

    assert result == description


def test_examples_extracted_late_in_enrichment_are_sanitized() -> None:
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Example", "version": "1.0.0"},
        "paths": {},
        "components": {
            "securitySchemes": {"token": {"type": "apiKey", "in": "header", "name": "X"}},
            "schemas": {
                "Example": {
                    "type": "object",
                    "properties": {
                        "api_server": {
                            "type": "string",
                            "description": "API server.\n\nExample: `api.acme.com:4430`",
                        }
                    },
                }
            },
        },
        "security": [{"token": []}],
        "servers": [{"url": "https://example.com"}],
    }

    result, _ = enrich_spec(spec, load_config(Path("config/enrichment.yaml")))
    example = result["components"]["schemas"]["Example"]["properties"]["api_server"]

    assert example["x-f5xc-example"] == "api.example.com:4430"


def test_server_variable_examples_use_reserved_tenants() -> None:
    config = yaml.safe_load(Path("config/server_variables.yaml").read_text())
    tenant = config["variables"]["tenant"]
    rendered = yaml.safe_dump(tenant)

    assert "acme" not in rendered.casefold()
    assert "example-corp" in tenant["examples"]
    assert "example-partners" in tenant["examples"]


def test_published_specs_have_no_acme_placeholders() -> None:
    placeholder = re.compile(r"\bacme\b", re.IGNORECASE)
    findings: list[str] = []

    for path in sorted(Path("docs/specifications/api").glob("*.json")):
        text = path.read_text()
        without_rfc_8555 = text.replace("_acme-challenge", "")
        if placeholder.search(without_rfc_8555):
            findings.append(str(path))

    assert findings == []
