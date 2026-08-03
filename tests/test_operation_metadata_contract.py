# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Unit tests for the canonical OperationMetadataEnricher wrapper contract."""

import pytest

from scripts.utils.operation_metadata_enricher import OperationMetadataEnricher


@pytest.fixture
def enricher():
    """Create enricher with default config."""
    return OperationMetadataEnricher()


@pytest.fixture
def simple_spec():
    """Create a simple OpenAPI spec for testing."""
    return {
        "paths": {
            "/api/resources": {
                "get": {
                    "operationId": "listResources",
                    "requestBody": {},
                    "responses": {
                        "200": {"description": "Success"},
                        "400": {"description": "Bad request"},
                        "404": {"description": "Not found"},
                    },
                },
                "post": {
                    "operationId": "createResource",
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string", "description": "Resource name"},
                                        "description": {
                                            "type": "string",
                                            "description": "Resource description",
                                        },
                                    },
                                    "required": ["name"],
                                },
                            },
                        },
                    },
                    "responses": {
                        "201": {"description": "Created"},
                        "400": {"description": "Bad request"},
                        "409": {"description": "Conflict"},
                    },
                },
            },
            "/api/resources/{id}": {
                "get": {
                    "operationId": "getResource",
                    "parameters": [{"name": "id", "in": "path", "required": True}],
                    "responses": {
                        "200": {"description": "Success"},
                        "404": {"description": "Not found"},
                    },
                },
                "delete": {
                    "operationId": "deleteResource",
                    "parameters": [{"name": "id", "in": "path", "required": True}],
                    "responses": {
                        "204": {"description": "No Content"},
                        "404": {"description": "Not found"},
                    },
                },
            },
        },
    }


class TestOperationMetadataContract:
    """Test the single operation-metadata wrapper contract."""

    def test_wrapper_is_complete_and_flat_extensions_are_absent(self, enricher, simple_spec):
        """Every operation has one complete wrapper and no duplicate flat metadata."""
        result = enricher.enrich_spec(simple_spec)
        flat_extensions = {
            "x-f5xc-required-fields",
            "x-f5xc-danger-level",
            "x-f5xc-confirmation-required",
            "x-f5xc-side-effects",
        }
        wrapper_fields = {
            "purpose",
            "required_fields",
            "optional_fields",
            "field_docs",
            "conditions",
            "side_effects",
            "danger_level",
            "confirmation_required",
            "common_errors",
            "performance_impact",
        }

        for path_item in result["paths"].values():
            for operation in path_item.values():
                assert flat_extensions.isdisjoint(operation)
                assert set(operation["x-f5xc-operation-metadata"]) == wrapper_fields

    def test_metadata_values_exist_only_inside_wrapper(self, enricher, simple_spec):
        """Requiredness, risk, confirmation, and effects remain in the wrapper."""
        result = enricher.enrich_spec(simple_spec)
        create = result["paths"]["/api/resources"]["post"]["x-f5xc-operation-metadata"]
        delete = result["paths"]["/api/resources/{id}"]["delete"]["x-f5xc-operation-metadata"]

        assert "name" in create["required_fields"]
        assert create["danger_level"] == "medium"
        assert create["confirmation_required"] is False
        assert create["side_effects"] == {"creates": ["resource"]}
        assert delete["danger_level"] == "high"
        assert delete["confirmation_required"] is True
        assert delete["side_effects"] == {"deletes": ["resource"]}

    def test_purpose_generation_get_list(self, enricher, simple_spec):
        """Test purpose generation for GET list operation."""
        result = enricher.enrich_spec(simple_spec)
        get_op = result["paths"]["/api/resources"]["get"]
        metadata = get_op["x-f5xc-operation-metadata"]

        assert "List" in metadata["purpose"]
        assert "resources" in metadata["purpose"]

    def test_purpose_generation_get_single(self, enricher, simple_spec):
        """Test purpose generation for GET single resource operation."""
        result = enricher.enrich_spec(simple_spec)
        get_op = result["paths"]["/api/resources/{id}"]["get"]
        metadata = get_op["x-f5xc-operation-metadata"]

        assert "Retrieve" in metadata["purpose"]
        assert "resource" in metadata["purpose"]

    def test_purpose_generation_post_create(self, enricher, simple_spec):
        """Test purpose generation for POST create operation."""
        result = enricher.enrich_spec(simple_spec)
        post_op = result["paths"]["/api/resources"]["post"]
        metadata = post_op["x-f5xc-operation-metadata"]

        assert "Create" in metadata["purpose"]
        assert "resource" in metadata["purpose"]

    def test_purpose_generation_delete(self, enricher, simple_spec):
        """Test purpose generation for DELETE operation."""
        result = enricher.enrich_spec(simple_spec)
        delete_op = result["paths"]["/api/resources/{id}"]["delete"]
        metadata = delete_op["x-f5xc-operation-metadata"]

        assert "Delete" in metadata["purpose"]
        assert "resource" in metadata["purpose"]

    def test_required_fields_identification(self, enricher, simple_spec):
        """Test identification of required fields."""
        result = enricher.enrich_spec(simple_spec)
        post_op = result["paths"]["/api/resources"]["post"]
        metadata = post_op["x-f5xc-operation-metadata"]

        # Name is required, description is optional
        assert "name" in metadata["required_fields"]
        assert "description" in metadata["optional_fields"]

    def test_field_docs_extraction(self, enricher, simple_spec):
        """Test extraction of field documentation."""
        result = enricher.enrich_spec(simple_spec)
        post_op = result["paths"]["/api/resources"]["post"]
        metadata = post_op["x-f5xc-operation-metadata"]

        field_docs = metadata["field_docs"]
        assert "name" in field_docs
        assert "description" in field_docs
        assert "Resource name" in field_docs["name"]

    def test_confirmation_required_high_danger(self, enricher, simple_spec):
        """Test confirmation requirement for high-danger operations."""
        result = enricher.enrich_spec(simple_spec)
        delete_op = result["paths"]["/api/resources/{id}"]["delete"]
        metadata = delete_op["x-f5xc-operation-metadata"]

        # DELETE is high danger, so confirmation should be required
        if metadata["danger_level"] == "high":
            assert metadata["confirmation_required"] is True

    def test_common_errors_mapping(self, enricher, simple_spec):
        """Test mapping of HTTP status codes to user-friendly errors."""
        result = enricher.enrich_spec(simple_spec)
        post_op = result["paths"]["/api/resources"]["post"]
        metadata = post_op["x-f5xc-operation-metadata"]

        errors = metadata["common_errors"]

        # Should have multiple error mappings
        assert len(errors) > 0

        # Each error should have code, message, and solution
        for error in errors:
            assert "code" in error
            assert "message" in error
            assert "solution" in error

    def test_performance_impact_assessment(self, enricher, simple_spec):
        """Test performance impact assessment."""
        result = enricher.enrich_spec(simple_spec)
        get_op = result["paths"]["/api/resources"]["get"]
        metadata = get_op["x-f5xc-operation-metadata"]

        impact = metadata["performance_impact"]
        assert "latency" in impact
        assert "resource_usage" in impact
        assert impact["latency"] in ["low", "moderate", "high"]
        assert impact["resource_usage"] in ["low", "moderate", "high"]

    def test_side_effects_preservation(self, enricher, simple_spec):
        """Test that side effects are preserved in comprehensive metadata."""
        result = enricher.enrich_spec(simple_spec)
        post_op = result["paths"]["/api/resources"]["post"]
        metadata = post_op["x-f5xc-operation-metadata"]

        # POST should have side effects
        assert "side_effects" in metadata
        side_effects = metadata["side_effects"]
        assert isinstance(side_effects, dict)

    def test_prerequisites_identification(self, enricher):
        """Test identification of operation prerequisites."""
        spec = {
            "paths": {
                "/api/namespaces/{namespace}/resources": {
                    "post": {
                        "operationId": "createResource",
                        "parameters": [
                            {"name": "namespace", "in": "path", "required": True},
                        ],
                        "responses": {"201": {"description": "Created"}},
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        post_op = result["paths"]["/api/namespaces/{namespace}/resources"]["post"]
        metadata = post_op["x-f5xc-operation-metadata"]

        conditions = metadata["conditions"]
        assert "prerequisites" in conditions
        # Should identify namespace requirement
        assert any("namespace" in str(p).lower() for p in conditions["prerequisites"])

    def test_postconditions_for_create(self, enricher, simple_spec):
        """Test postcondition generation for create operation."""
        result = enricher.enrich_spec(simple_spec)
        post_op = result["paths"]["/api/resources"]["post"]
        metadata = post_op["x-f5xc-operation-metadata"]

        conditions = metadata["conditions"]
        postconditions = conditions["postconditions"]

        # Should mention resource creation
        assert any("created" in str(p).lower() for p in postconditions)

    def test_postconditions_for_delete(self, enricher, simple_spec):
        """Test postcondition generation for delete operation."""
        result = enricher.enrich_spec(simple_spec)
        delete_op = result["paths"]["/api/resources/{id}"]["delete"]
        metadata = delete_op["x-f5xc-operation-metadata"]

        conditions = metadata["conditions"]
        postconditions = conditions["postconditions"]

        # Should mention resource removal
        assert any("removed" in str(p).lower() for p in postconditions)

    def test_multiple_operations_independent(self, enricher, simple_spec):
        """Test that metadata for different operations is independent."""
        result = enricher.enrich_spec(simple_spec)

        get_op_metadata = result["paths"]["/api/resources"]["get"]["x-f5xc-operation-metadata"]
        post_op_metadata = result["paths"]["/api/resources"]["post"]["x-f5xc-operation-metadata"]
        delete_op_metadata = result["paths"]["/api/resources/{id}"]["delete"][
            "x-f5xc-operation-metadata"
        ]

        # Each should have different purpose
        assert get_op_metadata["purpose"] != post_op_metadata["purpose"]
        assert post_op_metadata["purpose"] != delete_op_metadata["purpose"]


class TestPurposePreservation:
    """Test purpose field preservation behavior (Issue #408)."""

    def test_existing_purpose_preserved(self, enricher):
        """Test that existing purpose field is preserved, not overwritten."""
        spec = {
            "paths": {
                "/api/config/namespaces/{namespace}/http_loadbalancers": {
                    "post": {
                        "operationId": "createHttpLoadBalancer",
                        "x-f5xc-operation-metadata": {
                            "purpose": "HTTP/HTTPS load balancer with origin pools",
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        post_op = result["paths"]["/api/config/namespaces/{namespace}/http_loadbalancers"]["post"]
        metadata = post_op["x-f5xc-operation-metadata"]

        # Purpose should be preserved, not overwritten with "Create new http-loadbalancer"
        assert metadata["purpose"] == "HTTP/HTTPS load balancer with origin pools"
        assert not metadata["purpose"].startswith("Create")

    def test_fallback_purpose_when_none_exists(self, enricher):
        """Test that fallback purpose is generated when none exists."""
        spec = {
            "paths": {
                "/api/config/namespaces/{namespace}/test_items": {
                    "post": {
                        "operationId": "createTestItem",
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        post_op = result["paths"]["/api/config/namespaces/{namespace}/test_items"]["post"]
        metadata = post_op["x-f5xc-operation-metadata"]

        # Should generate verb-first fallback
        assert metadata["purpose"] == "Create new test-item"

    def test_empty_purpose_gets_fallback(self, enricher):
        """Test that empty string purpose gets fallback."""
        spec = {
            "paths": {
                "/api/config/namespaces/{namespace}/widgets": {
                    "delete": {
                        "operationId": "deleteWidget",
                        "x-f5xc-operation-metadata": {
                            "purpose": "",
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        del_op = result["paths"]["/api/config/namespaces/{namespace}/widgets"]["delete"]
        metadata = del_op["x-f5xc-operation-metadata"]

        # Empty string is falsy, should get fallback
        assert metadata["purpose"] == "Delete widget"

    def test_noun_first_preserved_over_verb_first_fallback(self, enricher):
        """Test that noun-first descriptions take priority over verb-first fallbacks."""
        spec = {
            "paths": {
                "/api/config/namespaces/{namespace}/origin_pools": {
                    "post": {
                        "operationId": "createOriginPool",
                        "x-f5xc-operation-metadata": {
                            "purpose": "Backend server pool for load balancing",
                        },
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        post_op = result["paths"]["/api/config/namespaces/{namespace}/origin_pools"]["post"]
        metadata = post_op["x-f5xc-operation-metadata"]

        # Should preserve noun-first description
        assert metadata["purpose"] == "Backend server pool for load balancing"
        # Should NOT be verb-first
        assert not metadata["purpose"].startswith("Create")
        assert "new" not in metadata["purpose"].lower()


class TestEdgeCases:
    """Test edge cases in wrapper generation."""

    def test_operation_without_responses(self, enricher):
        """Test handling operation with no responses."""
        spec = {
            "paths": {
                "/api/resource": {
                    "get": {
                        "operationId": "getResource",
                        "responses": {},
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        operation = result["paths"]["/api/resource"]["get"]

        # Should still generate metadata
        assert "x-f5xc-operation-metadata" in operation

    def test_operation_without_request_body(self, enricher):
        """Test handling operation with no request body."""
        spec = {
            "paths": {
                "/api/resource": {
                    "get": {
                        "operationId": "listResources",
                        "responses": {"200": {"description": "Success"}},
                    },
                },
            },
        }

        result = enricher.enrich_spec(spec)
        operation = result["paths"]["/api/resource"]["get"]
        metadata = operation["x-f5xc-operation-metadata"]

        # Optional fields should be empty
        assert metadata["optional_fields"] == []

    def test_empty_paths(self, enricher):
        """Test handling spec with no paths."""
        spec = {"paths": {}}

        result = enricher.enrich_spec(spec)
        assert result == spec


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
