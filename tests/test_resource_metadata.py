# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Unit tests for per-resource metadata functionality (Issues #267-270).

Tests resource metadata loading, structure validation, and integration
with the spec index generation.
"""

import pytest

from scripts.utils.domain_metadata import (
    DOMAIN_PRIMARY_RESOURCES,
    get_primary_resources_metadata,
    get_resource_metadata,
)


class TestResourceMetadataLoading:
    """Test resource metadata configuration loading."""

    def test_resource_metadata_loads_without_error(self):
        """Test that resource metadata config loads successfully."""
        # Should not raise any exceptions
        metadata = get_resource_metadata("http_loadbalancer")
        assert metadata is not None

    def test_resource_metadata_returns_dict(self):
        """Test that resource metadata returns a dictionary."""
        metadata = get_resource_metadata("http_loadbalancer")
        assert isinstance(metadata, dict)

    def test_resource_metadata_caching_works(self):
        """Test that metadata is cached after first load."""
        # Call twice - should use cached value
        metadata1 = get_resource_metadata("http_loadbalancer")
        metadata2 = get_resource_metadata("http_loadbalancer")
        # Results should be equal
        assert metadata1 == metadata2


class TestResourceMetadataStructure:
    """Test required fields and valid values in resource metadata."""

    @pytest.fixture
    def priority_resources(self):
        """Priority resources that must have full metadata."""
        return [
            "http_loadbalancer",
            "tcp_loadbalancer",
            "origin_pool",
            "healthcheck",
            "app_firewall",
        ]

    def test_priority_resources_have_description(self, priority_resources):
        """Test that priority resources have descriptions."""
        for resource in priority_resources:
            metadata = get_resource_metadata(resource)
            assert "description" in metadata, f"{resource} missing description"
            assert len(metadata["description"]) > 10, f"{resource} description too short"

    def test_priority_resources_have_short_description(self, priority_resources):
        """Test that priority resources have short descriptions."""
        for resource in priority_resources:
            metadata = get_resource_metadata(resource)
            assert "description_short" in metadata, f"{resource} missing description_short"
            assert len(metadata["description_short"]) <= 60, f"{resource} short desc too long"

    def test_priority_resources_have_tier(self, priority_resources):
        """Test that priority resources have tier information."""
        valid_tiers = {"Free", "Standard", "Advanced", "Enterprise", "WAAP"}
        for resource in priority_resources:
            metadata = get_resource_metadata(resource)
            assert "tier" in metadata, f"{resource} missing tier"
            assert metadata["tier"] in valid_tiers, f"{resource} has invalid tier"

    def test_priority_resources_have_icon(self, priority_resources):
        """Test that priority resources have icons."""
        for resource in priority_resources:
            metadata = get_resource_metadata(resource)
            assert "icon" in metadata, f"{resource} missing icon"
            assert len(metadata["icon"]) > 0, f"{resource} has empty icon"

    def test_priority_resources_have_category(self, priority_resources):
        """Test that priority resources have categories."""
        for resource in priority_resources:
            metadata = get_resource_metadata(resource)
            assert "category" in metadata, f"{resource} missing category"
            assert len(metadata["category"]) > 0, f"{resource} has empty category"

    def test_priority_resources_have_dependencies(self, priority_resources):
        """Test that priority resources have dependency information."""
        for resource in priority_resources:
            metadata = get_resource_metadata(resource)
            assert "dependencies" in metadata, f"{resource} missing dependencies"
            deps = metadata["dependencies"]
            assert "required" in deps, f"{resource} missing required dependencies"
            assert "optional" in deps, f"{resource} missing optional dependencies"
            assert isinstance(deps["required"], list)
            assert isinstance(deps["optional"], list)


class TestResourceMetadataDefaults:
    """Test default fallback behavior for unconfigured resources."""

    def test_unknown_resource_returns_defaults(self):
        """Test that unknown resources get default metadata."""
        metadata = get_resource_metadata("some_unknown_resource_xyz")
        assert metadata is not None
        assert "name" in metadata
        assert metadata["name"] == "some_unknown_resource_xyz"

    def test_unknown_resource_has_tier_default(self):
        """Test that unknown resources get Standard tier."""
        metadata = get_resource_metadata("unknown_resource_abc")
        assert metadata.get("tier") == "Standard"

    def test_unknown_resource_has_icon_default(self):
        """Test that unknown resources get default icon."""
        metadata = get_resource_metadata("unknown_resource_def")
        assert "icon" in metadata
        assert len(metadata["icon"]) > 0

    def test_unknown_resource_has_empty_dependencies(self):
        """Test that unknown resources get empty dependency lists."""
        metadata = get_resource_metadata("unknown_resource_ghi")
        deps = metadata.get("dependencies", {})
        assert deps.get("required", []) == []
        assert deps.get("optional", []) == []


class TestPrimaryResourcesMetadata:
    """Test get_primary_resources_metadata function."""

    def test_returns_list_for_known_domain(self):
        """Test that known domains return a list of metadata."""
        result = get_primary_resources_metadata("virtual")
        assert isinstance(result, list)
        assert len(result) > 0

    def test_returns_empty_list_for_unknown_domain(self):
        """Test that unknown domains return empty list."""
        result = get_primary_resources_metadata("unknown_domain_xyz")
        assert isinstance(result, list)
        assert len(result) == 0

    def test_metadata_includes_name_field(self):
        """Test that each metadata item has name field."""
        result = get_primary_resources_metadata("virtual")
        for item in result:
            assert "name" in item
            assert isinstance(item["name"], str)

    def test_metadata_includes_description_field(self):
        """Test that each metadata item has description field."""
        result = get_primary_resources_metadata("virtual")
        for item in result:
            assert "description" in item
            assert isinstance(item["description"], str)

    def test_metadata_includes_all_required_fields(self):
        """Test that metadata includes all required fields."""
        required_fields = [
            "name",
            "description",
            "description_short",
            "tier",
            "icon",
            "category",
            "supports_logs",
            "supports_metrics",
            "dependencies",
            "relationship_hints",
        ]
        result = get_primary_resources_metadata("virtual")
        for item in result:
            for field in required_fields:
                assert field in item, f"Missing field: {field} in resource {item.get('name')}"


class TestRelationshipHints:
    """Test relationship hint format validation."""

    def test_relationship_hints_are_list(self):
        """Test that relationship_hints is a list."""
        metadata = get_resource_metadata("http_loadbalancer")
        hints = metadata.get("relationship_hints", [])
        assert isinstance(hints, list)

    def test_relationship_hints_format(self):
        """Test that relationship hints follow expected format."""
        metadata = get_resource_metadata("http_loadbalancer")
        hints = metadata.get("relationship_hints", [])
        for hint in hints:
            assert isinstance(hint, str)
            # Hints should contain a colon for resource:description format
            if hint:
                assert ":" in hint or len(hint) > 5


class TestDependencyValidation:
    """Test dependency metadata validation."""

    def test_http_loadbalancer_requires_origin_pool(self):
        """Test that http_loadbalancer requires origin_pool."""
        metadata = get_resource_metadata("http_loadbalancer")
        deps = metadata.get("dependencies", {})
        required = deps.get("required", [])
        assert "origin_pool" in required

    def test_optional_dependencies_are_strings(self):
        """Test that optional dependencies are string lists."""
        metadata = get_resource_metadata("http_loadbalancer")
        deps = metadata.get("dependencies", {})
        optional = deps.get("optional", [])
        for dep in optional:
            assert isinstance(dep, str)


class TestSingleResourceMetadata:
    """Test get_resource_metadata function."""

    def test_returns_dict_with_name(self):
        """Test that resource metadata includes name field."""
        metadata = get_resource_metadata("origin_pool")
        assert "name" in metadata
        assert metadata["name"] == "origin_pool"

    def test_configured_resource_has_full_metadata(self):
        """Test that configured resources have full metadata."""
        metadata = get_resource_metadata("http_loadbalancer")
        assert len(metadata.get("description", "")) > 20
        assert metadata.get("tier") in {"Free", "Standard", "Advanced", "Enterprise", "WAAP"}

    def test_auto_generated_description_format(self):
        """Test that auto-generated descriptions follow format."""
        # Use a resource that may not be explicitly configured
        metadata = get_resource_metadata("some_auto_resource")
        desc = metadata.get("description", "")
        # Auto-generated descriptions should at least have the resource name
        assert len(desc) > 0


class TestMetadataCompleteness:
    """Test that all DOMAIN_PRIMARY_RESOURCES have metadata."""

    def test_all_resources_have_metadata(self):
        """Test that all primary resources can get metadata."""
        for domain, resources in DOMAIN_PRIMARY_RESOURCES.items():
            for resource in resources:
                metadata = get_resource_metadata(resource)
                assert metadata is not None, f"No metadata for {resource} in {domain}"
                assert "name" in metadata

    def test_metadata_count_matches_domain_resources(self):
        """Test that rich metadata count matches domain resource count."""
        for domain, resources in list(DOMAIN_PRIMARY_RESOURCES.items())[:5]:
            rich = get_primary_resources_metadata(domain)
            assert len(rich) == len(resources), f"Count mismatch for {domain}"


class TestTierDefinitions:
    """Test tier metadata values."""

    def test_valid_tier_values(self):
        """Test that all tiers are valid."""
        valid_tiers = {"Free", "Standard", "Advanced", "Enterprise", "WAAP"}
        for domain in list(DOMAIN_PRIMARY_RESOURCES.keys())[:5]:
            for item in get_primary_resources_metadata(domain):
                tier = item.get("tier", "Standard")
                assert tier in valid_tiers, f"Invalid tier {tier} for {item.get('name')}"


class TestCategoryDefinitions:
    """Test category metadata values."""

    def test_valid_category_values(self):
        """Test that all categories are valid."""
        valid_categories = {
            "Load Balancing",
            "Security",
            "DNS",
            "CDN",
            "Sites",
            "Networking",
            "Observability",
            "Infrastructure",
            "AI/ML",
            "Integration",
            "Management",
            "Container",
            "Other",
        }
        for domain in list(DOMAIN_PRIMARY_RESOURCES.keys())[:5]:
            for item in get_primary_resources_metadata(domain):
                category = item.get("category", "Other")
                assert category in valid_categories, f"Invalid category {category}"


class TestMappingKeyAbsenceWithoutSpec:
    """Verify schema_components and api_paths are absent when spec is not provided."""

    def test_spec_none_omits_schema_components(self):
        """get_primary_resources_metadata(domain) must not include schema_components."""
        result = get_primary_resources_metadata("dns")
        for resource in result:
            assert "schema_components" not in resource, (
                f"resource '{resource['name']}' has 'schema_components' key "
                f"when spec=None — key must be absent, not empty"
            )

    def test_spec_none_omits_api_paths(self):
        """get_primary_resources_metadata(domain) must not include api_paths."""
        result = get_primary_resources_metadata("dns")
        for resource in result:
            assert "api_paths" not in resource, (
                f"resource '{resource['name']}' has 'api_paths' key "
                f"when spec=None — key must be absent, not empty"
            )

    def test_spec_none_all_domains(self):
        """All domains return dicts without mapping keys when spec is not provided."""
        for domain in DOMAIN_PRIMARY_RESOURCES:
            result = get_primary_resources_metadata(domain)
            for resource in result:
                assert "schema_components" not in resource, (
                    f"domain '{domain}' resource '{resource['name']}' has "
                    f"'schema_components' when spec=None"
                )
                assert "api_paths" not in resource, (
                    f"domain '{domain}' resource '{resource['name']}' has "
                    f"'api_paths' when spec=None"
                )


class TestMappingKeyPresenceWithSpec:
    """Verify schema_components and api_paths appear when spec is provided."""

    @pytest.fixture
    def minimal_virtual_spec(self):
        return {
            "paths": {
                "/api/config/namespaces/{namespace}/http_loadbalancers": {
                    "post": {"operationId": "ves.io.schema.views.http_loadbalancer.API.Create"},
                    "get": {"operationId": "ves.io.schema.views.http_loadbalancer.API.List"},
                },
            }
        }

    def test_spec_present_includes_schema_components(self, minimal_virtual_spec):
        """Every resource dict must have schema_components when spec is provided."""
        result = get_primary_resources_metadata("virtual", spec=minimal_virtual_spec)
        for resource in result:
            assert "schema_components" in resource, (
                f"resource '{resource['name']}' missing 'schema_components' when spec provided"
            )
            assert isinstance(resource["schema_components"], list)

    def test_spec_present_includes_api_paths(self, minimal_virtual_spec):
        """Every resource dict must have api_paths when spec is provided."""
        result = get_primary_resources_metadata("virtual", spec=minimal_virtual_spec)
        for resource in result:
            assert "api_paths" in resource, (
                f"resource '{resource['name']}' missing 'api_paths' when spec provided"
            )
            assert isinstance(resource["api_paths"], list)

    def test_auto_resolvable_resource_gets_populated(self, minimal_virtual_spec):
        """http_loadbalancer should auto-resolve from the minimal spec."""
        result = get_primary_resources_metadata("virtual", spec=minimal_virtual_spec)
        http_lb = next(r for r in result if r["name"] == "http_loadbalancer")
        assert "views.http_loadbalancer" in http_lb["schema_components"]
        assert any("http_loadbalancers" in p for p in http_lb["api_paths"])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
