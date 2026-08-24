# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=use-implicit-booleaness-not-comparison  # Explicit empty results document the expected shape.

"""Unit tests for resource_resolver module (Issue #252)."""

import pytest

from scripts.utils.resource_resolver import (
    OPERATIONID_REGEX,
    apply_overrides,
    resolve_resource,
    validate_resource_mappings,
)


class TestOperationIdParsing:
    """Verify OPERATIONID_REGEX extracts components from both API and CustomAPI."""

    def test_standard_api_operationid(self):
        match = OPERATIONID_REGEX.match("ves.io.schema.views.http_loadbalancer.API.Create")
        assert match is not None
        assert match.group(1) == "views.http_loadbalancer"

    def test_custom_api_operationid(self):
        match = OPERATIONID_REGEX.match("ves.io.schema.dns_zone.CustomAPI.Verify")
        assert match is not None
        assert match.group(1) == "dns_zone"

    def test_crudapi_prefix_extracted(self):
        match = OPERATIONID_REGEX.match("ves.io.schema.crudapi.aws_vpc_site.API.List")
        assert match is not None
        assert match.group(1) == "crudapi.aws_vpc_site"

    def test_non_matching_operationid_rejected(self):
        assert OPERATIONID_REGEX.match("some.other.format.Create") is None

    def test_empty_string_rejected(self):
        assert OPERATIONID_REGEX.match("") is None

    def test_public_config_custom_api_extracted(self):
        match = OPERATIONID_REGEX.match(
            "ves.io.schema.views.api_definition.PublicConfigCustomAPI.ListLoadbalancers"
        )
        assert match is not None
        assert match.group(1) == "views.api_definition"

    def test_custom_data_api_extracted(self):
        match = OPERATIONID_REGEX.match("ves.io.schema.dns_zone.CustomDataAPI.GetMetrics")
        assert match is not None
        assert match.group(1) == "dns_zone"


class TestSchemaComponentResolution:
    """Verify component extraction: all prefix variants, no early stop, deduplication."""

    @pytest.fixture
    def http_lb_paths(self):
        return {
            "/api/config/namespaces/{namespace}/http_loadbalancers": {
                "post": {"operationId": "ves.io.schema.views.http_loadbalancer.API.Create"},
                "get": {"operationId": "ves.io.schema.views.http_loadbalancer.API.List"},
            },
            "/api/config/namespaces/{namespace}/http_loadbalancers/{name}": {
                "get": {"operationId": "ves.io.schema.views.http_loadbalancer.API.Get"},
                "delete": {"operationId": "ves.io.schema.views.http_loadbalancer.API.Delete"},
            },
        }

    def test_views_prefix_matched(self, http_lb_paths):
        schema_comps, _ = resolve_resource("http_loadbalancer", http_lb_paths)
        assert "views.http_loadbalancer" in schema_comps

    def test_multiple_prefix_variants_collected(self):
        paths = {
            "/api/config/namespaces/{namespace}/things": {
                "post": {"operationId": "ves.io.schema.views.thing.API.Create"},
                "get": {"operationId": "ves.io.schema.crudapi.thing.API.List"},
            },
        }
        schema_comps, _ = resolve_resource("thing", paths)
        assert "views.thing" in schema_comps
        assert "crudapi.thing" in schema_comps

    def test_deduplication(self, http_lb_paths):
        schema_comps, _ = resolve_resource("http_loadbalancer", http_lb_paths)
        assert schema_comps.count("views.http_loadbalancer") == 1

    def test_no_match_returns_empty_lists(self):
        paths = {
            "/api/other/stuff": {
                "get": {"operationId": "ves.io.schema.other.thing.API.List"},
            }
        }
        schema_comps, api_paths = resolve_resource("http_loadbalancer", paths)
        assert schema_comps == []
        assert api_paths == []

    def test_exact_component_name_matched(self):
        paths = {
            "/api/config/namespaces/{namespace}/dns_zones": {
                "get": {"operationId": "ves.io.schema.dns_zone.API.List"},
            }
        }
        schema_comps, _ = resolve_resource("dns_zone", paths)
        assert "dns_zone" in schema_comps

    def test_partial_name_not_matched(self):
        paths = {
            "/api/config/namespaces/{namespace}/http_loadbalancers": {
                "get": {"operationId": "ves.io.schema.views.http_loadbalancer.API.List"},
            }
        }
        schema_comps, _ = resolve_resource("loadbalancer", paths)
        assert schema_comps == []


class TestApiPathResolution:
    """Verify path selection: schema-tied, pluralized segment, CustomAPI included."""

    def test_pluralized_path_segment_matched(self):
        paths = {
            "/api/config/namespaces/{namespace}/http_loadbalancers": {
                "post": {"operationId": "ves.io.schema.views.http_loadbalancer.API.Create"},
            }
        }
        _, api_paths = resolve_resource("http_loadbalancer", paths)
        assert "/api/config/namespaces/{namespace}/http_loadbalancers" in api_paths

    def test_custom_api_path_included(self):
        paths = {
            "/api/config/namespaces/{namespace}/dns_zones": {
                "get": {"operationId": "ves.io.schema.dns_zone.API.List"},
            },
            "/api/config/namespaces/{namespace}/dns_zones/{name}/verify": {
                "post": {"operationId": "ves.io.schema.dns_zone.CustomAPI.Verify"},
            },
        }
        _, api_paths = resolve_resource("dns_zone", paths)
        assert "/api/config/namespaces/{namespace}/dns_zones/{name}/verify" in api_paths

    def test_path_without_matching_component_excluded(self):
        paths = {
            "/api/config/namespaces/{namespace}/http_loadbalancers": {
                "post": {"operationId": "ves.io.schema.some_other_thing.API.Create"},
            },
        }
        _, api_paths = resolve_resource("http_loadbalancer", paths)
        assert api_paths == []

    def test_unrelated_path_sharing_segment_excluded(self):
        paths = {
            "/api/config/namespaces/{namespace}/http_loadbalancers": {
                "post": {"operationId": "ves.io.schema.views.http_loadbalancer.API.Create"},
            },
            "/api/config/namespaces/{namespace}/other_http_loadbalancers": {
                "get": {"operationId": "ves.io.schema.views.other.API.List"},
            },
        }
        _, api_paths = resolve_resource("http_loadbalancer", paths)
        assert "/api/config/namespaces/{namespace}/other_http_loadbalancers" not in api_paths


class TestOverrideMerge:
    """Verify apply_overrides behavior: config takes precedence, absent key falls through."""

    @pytest.fixture
    def heuristic_result(self):
        return (
            ["views.http_loadbalancer"],
            ["/api/config/namespaces/{namespace}/http_loadbalancers"],
        )

    def test_absent_config_uses_heuristic(self, heuristic_result):
        schema_comps, api_paths = apply_overrides(heuristic_result, {})
        assert schema_comps == ["views.http_loadbalancer"]
        assert api_paths == ["/api/config/namespaces/{namespace}/http_loadbalancers"]

    def test_config_schema_components_replaces_heuristic(self, heuristic_result):
        override = {"schema_components": ["views.custom_lb"]}
        schema_comps, api_paths = apply_overrides(heuristic_result, override)
        assert schema_comps == ["views.custom_lb"]
        assert api_paths == ["/api/config/namespaces/{namespace}/http_loadbalancers"]

    def test_empty_array_override_replaces_non_empty_heuristic(self, heuristic_result):
        override = {"schema_components": [], "api_paths": []}
        schema_comps, api_paths = apply_overrides(heuristic_result, override)
        assert schema_comps == []
        assert api_paths == []

    def test_both_fields_overridden_independently(self, heuristic_result):
        override = {
            "schema_components": ["views.fleet"],
            "api_paths": ["/api/config/namespaces/{namespace}/fleets"],
        }
        schema_comps, api_paths = apply_overrides(heuristic_result, override)
        assert schema_comps == ["views.fleet"]
        assert api_paths == ["/api/config/namespaces/{namespace}/fleets"]

    def test_string_override_raises_type_error(self, heuristic_result):
        with pytest.raises(TypeError, match="schema_components must be a list"):
            apply_overrides(heuristic_result, {"schema_components": "views.http_loadbalancer"})

    def test_none_override_raises_type_error(self, heuristic_result):
        with pytest.raises(TypeError, match="api_paths must be a list"):
            apply_overrides(heuristic_result, {"api_paths": None})


class TestValidation:
    """Verify validate_resource_mappings error reporting."""

    @pytest.fixture
    def minimal_domain_paths(self):
        return {
            "/api/config/namespaces/{namespace}/http_loadbalancers": {
                "post": {"operationId": "ves.io.schema.views.http_loadbalancer.API.Create"},
            }
        }

    def test_valid_override_passes(self, minimal_domain_paths):
        heuristic = {"http_loadbalancer": (["views.http_loadbalancer"], [])}
        overrides = {"http_loadbalancer": {"schema_components": ["views.http_loadbalancer"]}}
        errors = validate_resource_mappings(heuristic, overrides, minimal_domain_paths, "virtual")
        assert errors == []

    def test_invalid_schema_component_fails(self, minimal_domain_paths):
        heuristic = {"http_loadbalancer": ([], [])}
        overrides = {"http_loadbalancer": {"schema_components": ["views.nonexistent_resource"]}}
        errors = validate_resource_mappings(heuristic, overrides, minimal_domain_paths, "virtual")
        assert len(errors) == 1
        assert "views.nonexistent_resource" in errors[0]
        assert "not found in domain" in errors[0]

    def test_invalid_api_path_fails(self, minimal_domain_paths):
        heuristic = {"http_loadbalancer": ([], [])}
        overrides = {
            "http_loadbalancer": {"api_paths": ["/api/config/namespaces/{namespace}/nonexistent"]}
        }
        errors = validate_resource_mappings(heuristic, overrides, minimal_domain_paths, "virtual")
        assert len(errors) == 1
        assert "/api/config/namespaces/{namespace}/nonexistent" in errors[0]

    def test_empty_array_override_skips_validation(self, minimal_domain_paths):
        heuristic = {"some_resource": ([], [])}
        overrides = {"some_resource": {"schema_components": [], "api_paths": []}}
        errors = validate_resource_mappings(heuristic, overrides, minimal_domain_paths, "virtual")
        assert errors == []

    def test_redundant_override_emits_warning(self):
        paths = {
            "/api/config/namespaces/{namespace}/http_loadbalancers": {
                "post": {"operationId": "ves.io.schema.views.http_loadbalancer.API.Create"},
            }
        }
        heuristic = {"http_loadbalancer": (["views.old_name"], [])}
        overrides = {"http_loadbalancer": {"schema_components": ["views.http_loadbalancer"]}}
        errors = validate_resource_mappings(heuristic, overrides, paths, "virtual")
        assert errors == []

    def test_multiple_errors_reported(self, minimal_domain_paths):
        heuristic = {"r1": ([], []), "r2": ([], [])}
        overrides = {
            "r1": {"schema_components": ["views.bad1"]},
            "r2": {"schema_components": ["views.bad2"]},
        }
        errors = validate_resource_mappings(heuristic, overrides, minimal_domain_paths, "virtual")
        assert len(errors) == 2

    def test_string_schema_components_in_validation_reports_error(self, minimal_domain_paths):
        heuristic = {"r": ([], [])}
        overrides = {"r": {"schema_components": "views.http_loadbalancer"}}
        errors = validate_resource_mappings(heuristic, overrides, minimal_domain_paths, "virtual")
        assert len(errors) == 1
        assert "must be a list" in errors[0]

    def test_none_api_paths_in_validation_reports_error(self, minimal_domain_paths):
        heuristic = {"r": ([], [])}
        overrides = {"r": {"api_paths": None}}
        errors = validate_resource_mappings(heuristic, overrides, minimal_domain_paths, "virtual")
        assert len(errors) == 1
        assert "must be a list" in errors[0]


class TestConceptualResources:
    """Verify resources with no API entity return empty lists from heuristic."""

    def test_no_operationid_match_returns_empty(self):
        paths = {
            "/api/config/namespaces/{namespace}/real_things": {
                "get": {"operationId": "ves.io.schema.views.real_thing.API.List"},
            }
        }
        schema_comps, api_paths = resolve_resource("conceptual_resource", paths)
        assert schema_comps == []
        assert api_paths == []

    def test_empty_paths_returns_empty(self):
        schema_comps, api_paths = resolve_resource("anything", {})
        assert schema_comps == []
        assert api_paths == []

    def test_apply_overrides_empty_config_preserves_empty_heuristic(self):
        schema_comps, api_paths = apply_overrides(
            ([], []), {"schema_components": [], "api_paths": []}
        )
        assert schema_comps == []
        assert api_paths == []
