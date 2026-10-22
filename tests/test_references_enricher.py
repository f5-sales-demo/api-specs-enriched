# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Unit tests for ReferencesEnricher.

The enricher stamps ``x-f5xc-references`` on ObjectRefType properties — the
resource-reference dimension of the dependency model. The referred resource kind
is NOT present in F5 specs (ObjectRefType is generic), so it is resolved from a
curated map; references nested inside oneOf choice variants are choice-gated.
"""

import pytest

from scripts.utils.extension_constants import X_F5XC_REFERENCES
from scripts.utils.references_enricher import ReferencesEnricher


@pytest.fixture
def kind_map():
    """Curated <schema>.<field> → referred resource kind map (deterministic source)."""
    return {
        "viewshttp_loadbalancerCreateSpecType.default_origin_pools": "origin_pool",
        "viewsOriginPoolListType.pool": "origin_pool",
        "fast_aclCreateSpecType.protocol_policer": "protocol_policer",
    }


@pytest.fixture
def enricher(kind_map):
    return ReferencesEnricher(kind_map=kind_map)


def _spec(schemas):
    return {"components": {"schemas": schemas}}


def test_stamps_reference_on_top_level_objectref(enricher):
    """A top-level ObjectRefType property gets x-f5xc-references with the mapped kind."""
    spec = _spec(
        {
            "fast_aclCreateSpecType": {
                "type": "object",
                "properties": {
                    "protocol_policer": {
                        "allOf": [{"$ref": "#/components/schemas/schemaviewsObjectRefType"}],
                        "x-f5xc-required-for": {"create": True},
                    },
                },
            },
        }
    )
    out = enricher.enrich_spec(spec)
    prop = out["components"]["schemas"]["fast_aclCreateSpecType"]["properties"]["protocol_policer"]
    refs = prop[X_F5XC_REFERENCES]
    assert refs[0]["resource_kind"] == "protocol_policer"
    assert refs[0]["required"] is True
    assert refs[0]["cardinality"] == "single"
    assert refs[0]["gated_by"] is None


def test_unmapped_objectref_gets_null_kind_not_a_guess(enricher):
    """When the curated map lacks the field, resource_kind is null (honest gap)."""
    spec = _spec(
        {
            "someResourceCreateSpecType": {
                "properties": {
                    "mystery_ref": {
                        "allOf": [{"$ref": "#/components/schemas/schemaviewsObjectRefType"}]
                    },
                },
            },
        }
    )
    out = enricher.enrich_spec(spec)
    refs = out["components"]["schemas"]["someResourceCreateSpecType"]["properties"]["mystery_ref"][
        X_F5XC_REFERENCES
    ]
    assert refs[0]["resource_kind"] is None


def test_non_objectref_property_is_untouched(enricher):
    """Plain scalar/object properties never get x-f5xc-references."""
    spec = _spec(
        {
            "fooCreateSpecType": {
                "properties": {"name": {"type": "string"}, "count": {"type": "integer"}},
            },
        }
    )
    out = enricher.enrich_spec(spec)
    for prop in out["components"]["schemas"]["fooCreateSpecType"]["properties"].values():
        assert X_F5XC_REFERENCES not in prop


def test_gated_by_records_oneof_choice(enricher):
    """A reference field that is a oneOf variant records the gating choice group."""
    spec = _spec(
        {
            "viewshttp_loadbalancerCreateSpecType": {
                "type": "object",
                "x-ves-oneof-field-waf_choice": ["app_firewall", "disable_waf"],
                "properties": {
                    "app_firewall": {
                        "allOf": [{"$ref": "#/components/schemas/schemaviewsObjectRefType"}]
                    },
                    "disable_waf": {"type": "object"},
                },
            },
        }
    )
    out = enricher.enrich_spec(spec)
    refs = out["components"]["schemas"]["viewshttp_loadbalancerCreateSpecType"]["properties"][
        "app_firewall"
    ][X_F5XC_REFERENCES]
    assert refs[0]["gated_by"] == {"choice": "waf_choice"}


def test_from_config_loads_curated_map(tmp_path):
    """from_config reads the curated referred-kind map from YAML."""
    cfg = tmp_path / "resource_references.yaml"
    cfg.write_text('references:\n  "fooCreateSpecType.bar": origin_pool\n')
    e = ReferencesEnricher.from_config(cfg)
    assert e.kind_map == {"fooCreateSpecType.bar": "origin_pool"}


def test_recursive_nested_objectref_through_sub_schema(enricher):
    """ObjectRefType nested inside allOf→sub-schema→array→items→sub-schema is detected."""
    spec = _spec(
        {
            "viewshttp_loadbalancerCreateSpecType": {
                "type": "object",
                "properties": {
                    "default_pool_list": {
                        "allOf": [{"$ref": "#/components/schemas/viewsOriginPoolListType"}],
                    },
                },
            },
            "viewsOriginPoolListType": {
                "properties": {
                    "pools": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/viewsOriginPoolWithWeight"},
                    },
                },
            },
            "viewsOriginPoolWithWeight": {
                "properties": {
                    "pool": {
                        "allOf": [{"$ref": "#/components/schemas/schemaviewsObjectRefType"}],
                    },
                    "weight": {"type": "integer"},
                },
            },
            "schemaviewsObjectRefType": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
        }
    )
    # The enricher should find the nested pool→ObjectRefType when recursing.
    kind_map = {"viewsOriginPoolWithWeight.pool": "origin_pool"}
    e = ReferencesEnricher(kind_map=kind_map)
    out = e.enrich_spec(spec)
    pool_prop = out["components"]["schemas"]["viewsOriginPoolWithWeight"]["properties"]["pool"]
    assert X_F5XC_REFERENCES in pool_prop
    assert pool_prop[X_F5XC_REFERENCES][0]["resource_kind"] == "origin_pool"


def test_idempotent(enricher):
    """Re-running does not duplicate descriptors."""
    spec = _spec(
        {
            "fast_aclCreateSpecType": {
                "properties": {
                    "protocol_policer": {
                        "allOf": [{"$ref": "#/components/schemas/schemaObjectRefType"}]
                    },
                },
            },
        }
    )
    once = enricher.enrich_spec(spec)
    enricher.reset_stats()
    twice = enricher.enrich_spec(once)
    refs = twice["components"]["schemas"]["fast_aclCreateSpecType"]["properties"][
        "protocol_policer"
    ][X_F5XC_REFERENCES]
    assert len(refs) == 1


def test_nullable_reference_retains_outer_and_original_annotations():
    import copy

    original = {"allOf": [{"$ref": "#/components/schemas/schemaObjectRefType"}]}
    nullable = {
        "anyOf": [copy.deepcopy(original), {"type": "object", "nullable": True, "enum": [None]}]
    }
    spec = _spec({"Example": {"properties": {"owner_view": nullable}}})
    enricher = ReferencesEnricher()
    enricher.enrich_spec(spec)
    assert nullable[X_F5XC_REFERENCES] == nullable["anyOf"][0][X_F5XC_REFERENCES]
    assert nullable[X_F5XC_REFERENCES][0]["resource_kind"] is None
    once = copy.deepcopy(spec)
    enricher.enrich_spec(spec)
    assert spec == once


def test_arbitrary_anyof_reference_alternatives_are_not_reclassified():
    prop = {
        "anyOf": [
            {"allOf": [{"$ref": "#/components/schemas/schemaObjectRefType"}]},
            {"type": "string"},
        ]
    }
    spec = _spec({"Example": {"properties": {"field": prop}}})
    ReferencesEnricher().enrich_spec(spec)
    assert X_F5XC_REFERENCES not in prop
