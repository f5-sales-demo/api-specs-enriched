# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=protected-access  # Tests intentionally verify deterministic internal helpers.

"""Unit tests for the cross-field/cross-resource dependency enricher.

Focus: the structured cross-resource requirement (`requires_resource`) that the
enricher emits additively alongside the opaque `requires_field`, so downstream
tooling (the terraform-provider-xcsh preflight codegen) can mechanically derive
an apply-time prerequisite check. Issue #967.
"""

from scripts.utils.dependency_enricher import DependencyEnricher
from scripts.utils.extension_constants import X_F5XC_REQUIRES


def _stamp(dep, parent_prop="client_side_defense"):
    """Stamp a single dependency onto a minimal schema and return the entry."""
    enricher = DependencyEnricher()
    schema = {"properties": {parent_prop: {}}}
    enricher._stamp_dependency(schema, dep, "http_loadbalancerCreateSpecType")
    return schema["properties"][parent_prop].get(X_F5XC_REQUIRES, [])


def test_cross_resource_requires_emits_structured_form():
    dep = {
        "field": "spec.client_side_defense",
        "requires": "resource:protected_domain (same namespace)",
        "reason": "CSD needs a protected_domain in the same namespace.",
    }
    entries = _stamp(dep)
    assert len(entries) == 1
    entry = entries[0]
    # Additive: opaque string preserved for existing consumers.
    assert entry["requires_field"] == "resource:protected_domain (same namespace)"
    # New structured form for mechanical consumers.
    assert entry["requires_resource"] == {
        "resource": "protected_domain",
        "scope": "same_namespace",
    }
    assert entry["reason"].startswith("CSD needs")


def test_sibling_field_requires_has_no_structured_resource():
    dep = {"field": "spec.foo", "requires": "spec.enable_waf"}
    entry = _stamp(dep, parent_prop="foo")[0]
    assert entry["requires_field"] == "spec.enable_waf"
    assert "requires_resource" not in entry


def test_required_and_min_items_pass_through():
    dep = {"field": "spec.bar", "required": True, "min_items": 2}
    entry = _stamp(dep, parent_prop="bar")[0]
    assert entry["required"] is True
    assert entry["min_items"] == 2
    assert "requires_resource" not in entry
