"""Canonical source-to-master integration and order-independence tests."""

from __future__ import annotations

import copy
import json

import pytest

from scripts import pipeline
from scripts.utils.component_canonicalization import (
    SUPPORTED_COMPONENT_CATEGORIES,
    canonicalize_source_components,
)


def _output_bytes(value: dict) -> bytes:
    """Match the JSON writer before optional publishing-path formatting."""
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def _source(owner: str, path: str, schema_type: str) -> dict:
    return {
        "openapi": "3.0.0",
        "info": {
            "title": f"F5 Distributed Cloud Services API for {owner}",
            "version": "1.0.0",
        },
        "paths": {
            path: {
                "get": {
                    "operationId": f"{owner}.CustomAPI.GetFixture",
                    "tags": ["fixture"],
                    "responses": {
                        "200": {
                            "description": "OK",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Shared"}
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {"Shared": {"type": schema_type}},
            "responses": {
                "SharedResponse": {
                    "description": "fixture response",
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Shared"}}
                    },
                }
            },
            "parameters": {
                "SharedParameter": {
                    "name": "fixture",
                    "in": "query",
                    "schema": {"$ref": "#/components/schemas/Shared"},
                }
            },
            "requestBodies": {
                "SharedBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Shared"}}
                    }
                }
            },
        },
        "tags": [{"name": "fixture"}],
    }


def _sources() -> dict[str, dict]:
    alpha = "ves.io.schema.alpha"
    beta = "ves.io.schema.beta"
    return {
        f"docs-cloud-f5-com.1.public.{alpha}.ves-swagger.json": _source(alpha, "/alpha", "string"),
        f"docs-cloud-f5-com.2.public.{beta}.ves-swagger.json": _source(beta, "/beta", "integer"),
    }


def test_master_is_order_independent_and_owns_an_exact_canonical_component_copy(
    monkeypatch: pytest.MonkeyPatch,
):
    def preserve_spec(
        spec: dict,
        *,
        allow_partition_residuals: bool,
    ) -> tuple[dict, dict[str, int]]:
        assert allow_partition_residuals is True
        return spec, {"server_default_partition_residuals": 0}

    monkeypatch.setattr(pipeline, "load_operation_aliases", dict)
    monkeypatch.setattr(
        pipeline,
        "_apply_merged_schema_enrichments",
        preserve_spec,
    )
    sources = _sources()
    forward = canonicalize_source_components(sources)
    reverse = canonicalize_source_components(dict(reversed(list(sources.items()))))

    forward_master = pipeline._create_master_graph(forward, "1.2.3")
    reverse_master = pipeline._create_master_graph(reverse, "1.2.3")
    forward_domains, forward_stats = pipeline.merge_specs_by_domain(forward.documents, "1.2.3")
    reverse_domains, reverse_stats = pipeline.merge_specs_by_domain(reverse.documents, "1.2.3")

    assert pipeline._canonical_json(forward_master) == pipeline._canonical_json(reverse_master)
    assert pipeline._canonical_json(forward_domains) == pipeline._canonical_json(reverse_domains)
    assert _output_bytes(forward_master) == _output_bytes(reverse_master)
    assert _output_bytes(forward_domains) == _output_bytes(reverse_domains)
    assert forward_stats == reverse_stats
    for category in SUPPORTED_COMPONENT_CATEGORIES:
        assert forward_master["components"][category] == forward.components[category]
        assert forward_domains["other"]["components"][category] == forward.components[category]
        assert (
            len(forward.components[category])
            == forward.accounting.categories[category].canonical_keys
        )
    assert forward_stats["schemas"] == len(forward.components["schemas"])
    assert forward_stats["requestBodies"] == len(forward.components["requestBodies"])
    assert forward_stats["server_default_partition_residuals"] == 0
    before = copy.deepcopy(forward.components)
    first_schema = next(iter(forward_master["components"]["schemas"].values()))
    first_schema["x-test-mutation"] = True
    assert forward.components == before


def test_domain_projection_state_cannot_influence_master_bytes(
    monkeypatch: pytest.MonkeyPatch,
):
    def preserve_external_docs(
        _enricher: pipeline.ExternalDocsEnricher,
        spec: dict,
        *,
        filename: str,
    ) -> dict:
        assert filename in {"alpha.json", "beta.json"}
        return spec

    monkeypatch.setattr(pipeline, "load_operation_aliases", dict)
    monkeypatch.setattr(
        pipeline,
        "categorize_spec",
        lambda filename: "alpha" if ".alpha." in filename else "beta",
    )
    monkeypatch.setattr(
        pipeline.ExternalDocsEnricher,
        "enrich_spec",
        preserve_external_docs,
    )
    canonical = canonicalize_source_components(_sources())
    canonical_before = canonical.canonical_bytes()
    master = pipeline._create_master_graph(canonical, "1.2.3")
    master_before = pipeline._canonical_json(master)
    domain_projections, _ = pipeline.merge_specs_by_domain(canonical.documents, "1.2.3")
    assert list(domain_projections) == ["alpha", "beta"]

    for domain_name, domain in reversed(domain_projections.items()):
        first_schema = next(iter(domain["components"]["schemas"].values()))
        first_schema["x-domain-mutation"] = domain_name
        first_path = next(iter(domain["paths"].values()))
        first_path["get"]["summary"] = f"{domain_name}-only mutation"
        domain["tags"][0]["name"] = f"{domain_name}-only-tag"

    assert pipeline._canonical_json(master) == master_before
    assert canonical.canonical_bytes() == canonical_before
    assert (
        pipeline._canonical_json(pipeline._create_master_graph(canonical, "1.2.3")) == master_before
    )

    master["tags"][0]["name"] = "master-only-tag"

    assert canonical.canonical_bytes() == canonical_before


def test_exact_component_insertion_rejects_value_and_scalar_type_divergence():
    target: dict = {}
    assert pipeline._insert_exact_component(target, "schemas", "Shared", {"enum": [1]}, "test")
    assert not pipeline._insert_exact_component(target, "schemas", "Shared", {"enum": [1]}, "test")
    with pytest.raises(ValueError, match=r"conflicting canonical components\.schemas\.Shared"):
        pipeline._insert_exact_component(
            target,
            "schemas",
            "Shared",
            {"enum": [True]},
            "test",
        )
    assert target == {"Shared": {"enum": [1]}}
