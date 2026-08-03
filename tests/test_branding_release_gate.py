"""Semantic branding boundaries enforced by the release gate."""

from __future__ import annotations

from typing import TYPE_CHECKING

from scripts.pipeline import _normalize_release_text
from scripts.utils import technical_text
from scripts.utils.acronyms import AcronymNormalizer
from scripts.utils.branding import BrandingTransformer, BrandingValidator

if TYPE_CHECKING:
    import pytest


def test_transformer_classifies_technical_spans_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original = technical_text.immutable_technical_spans

    def count_calls(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(technical_text, "immutable_technical_spans", count_calls)

    transformed = BrandingTransformer().transform_text(
        "Volterra and ves.io prose; keep ves.io/team-name and x-ves-io-managed.",
        field_name="description",
    )

    assert transformed == (
        "F5 Distributed Cloud and F5 XC prose; keep ves.io/team-name and x-ves-io-managed."
    )
    assert calls == 1


def test_validator_deduplicates_overlapping_legacy_patterns() -> None:
    findings = BrandingValidator().validate_text("Volterra prose and ves.io prose")

    assert [(finding["term"], finding["position"]) for finding in findings] == [
        ("Volterra", 0),
        ("ves.io", 19),
    ]


def test_immutable_identifiers_are_preserved_by_structure() -> None:
    spec = {
        "servers": [
            {
                "variables": {
                    "console_url": {
                        "description": (
                            "Endpoint host: tenant.console.ves.volterra.io or staging.volterra.us"
                        ),
                    },
                },
            },
        ],
        "paths": {
            "/namespaces/{vesnamespace}": {
                "get": {
                    "description": (
                        "Calls VES.I/o.schema.views.api_definition.publicconfigcustomapi."
                    ),
                    "parameters": [
                        {
                            "name": "vesnamespace",
                            "in": "path",
                            "required": True,
                            "description": "Defaults to VES-system.",
                            "x-f5xc-example": "ves-system",
                            "schema": {"type": "string"},
                        },
                    ],
                },
            },
        },
        "components": {
            "schemas": {
                "Selector": {
                    "description": (
                        "Use ves.io/siteName, VES.IO/region, or (ves.io/team-name). "
                        "Keep x-ves-io-managed and tenant.console.ves.volterra.io exact."
                    ),
                },
                "TenantDomain": {
                    "type": "string",
                    "format": "hostname",
                    "description": "Example domain.console.ves.volterra.I/O.",
                },
            },
        },
    }

    transformed = BrandingTransformer().transform_spec(spec)

    assert transformed == spec
    assert BrandingValidator().validate_spec(transformed) == []

    normalized = AcronymNormalizer().normalize_spec(spec)
    assert normalized == spec


def test_genuine_legacy_prose_and_synthetic_examples_are_transformed() -> None:
    spec = {
        "components": {
            "schemas": {
                "Site": {
                    "description": "Configuration parameter for Volterra software override.",
                },
                "MonitorEvent": {
                    "description": (
                        'Synthetic sources "VES-I/O-paris" and ves-io-london are in '
                        "the ves.io shared region."
                    ),
                },
            },
        },
    }

    transformed = BrandingTransformer().transform_spec(spec)

    assert transformed["components"]["schemas"]["Site"]["description"] == (
        "Configuration parameter for F5 Distributed Cloud software override."
    )
    assert transformed["components"]["schemas"]["MonitorEvent"]["description"] == (
        'Synthetic sources "F5XC-paris" and F5XC-london are in the F5 XC shared region.'
    )
    assert BrandingValidator().validate_spec(transformed) == []


def test_full_text_mutator_chain_preserves_identifiers_and_transforms_prose() -> None:
    spec = {
        "components": {
            "schemas": {
                "Selector": {
                    "description": (
                        "Use VES.IO/team-name, x-ves-io-managed, and "
                        "tenant.console.ves.volterra.io; ves-io prose is legacy."
                    ),
                },
            },
        },
    }

    branded = BrandingTransformer().transform_spec(spec)
    normalized = AcronymNormalizer().normalize_spec(branded)
    finalized = BrandingTransformer().transform_spec(normalized)

    assert finalized["components"]["schemas"]["Selector"]["description"] == (
        "Use VES.IO/team-name, x-ves-io-managed, and "
        "tenant.console.ves.volterra.io; F5XC prose is legacy."
    )
    assert BrandingValidator().validate_spec(finalized) == []


def test_sentence_final_qualified_key_survives_the_full_mutator_chain() -> None:
    spec = {"info": {"description": "Use ves.io/team-name."}}

    branded = BrandingTransformer().transform_spec(spec)
    normalized = AcronymNormalizer().normalize_spec(branded)
    finalized = BrandingTransformer().transform_spec(normalized)

    assert finalized == spec
    assert BrandingValidator().validate_spec(finalized) == []


def test_terminal_release_normalization_handles_late_prose_and_is_idempotent() -> None:
    spec = {
        "info": {
            "description": (
                "Late Volterra description for ves-io users; "
                "keep ves.io/team-name and tenant.console.ves.volterra.io."
            ),
        },
        "components": {
            "schemas": {
                "Examples": {
                    "x-ves-example": "f5-nginx-cdn-crl-html",
                    "x-f5xc-example": "f5-nginx-cdn-crl-html",
                },
            },
        },
    }

    targets = ["description", "x-ves-example", "x-f5xc-example"]
    once = _normalize_release_text(spec, targets)
    twice = _normalize_release_text(once, targets)

    assert once == twice
    assert once["info"]["description"] == (
        "Late F5 Distributed Cloud description for F5XC users; "
        "keep ves.io/team-name and tenant.console.ves.volterra.io."
    )
    examples = once["components"]["schemas"]["Examples"]
    assert examples["x-ves-example"] == "f5-nginx-cdn-crl-html"
    assert examples["x-f5xc-example"] == "f5-nginx-cdn-crl-html"
    assert BrandingValidator().validate_spec(once) == []


def test_two_label_brand_is_prose_unless_structure_declares_hostname() -> None:
    prose = {"info": {"description": "The ves.io platform is legacy wording."}}
    hostname = {
        "components": {
            "schemas": {
                "Host": {
                    "type": "string",
                    "format": "hostname",
                    "description": "Use ves.io.",
                },
            },
        },
    }

    assert BrandingTransformer().transform_spec(prose)["info"]["description"] == (
        "The F5 XC platform is legacy wording."
    )
    assert BrandingTransformer().transform_spec(hostname) == hostname
