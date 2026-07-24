#!/usr/bin/env python3
"""
Unit tests for ConstraintEnricher

Tests cover:
- Pattern matching (string, array, numeric)
- Constraint extraction for each type
- Reconciliation priority (EXISTING > DISCOVERY > INFERRED)
- Edge cases and error handling
- Statistics collection

Target coverage: 80%+
"""

# Import the constraint enricher
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.utils.constraint_enricher import (
    ArrayConstraintExtractor,
    ConstraintEnricher,
    ConstraintReconciler,
    NumericConstraintExtractor,
    ObjectConstraintExtractor,
    PatternMatcher,
    StringConstraintExtractor,
)

# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def test_config():
    """Load test constraint patterns configuration"""
    config_path = Path(__file__).parent.parent / "config" / "constraint_patterns.yaml"
    with config_path.open() as f:
        return yaml.safe_load(f)


@pytest.fixture
def pattern_matcher(test_config):
    """Create PatternMatcher instance"""
    return PatternMatcher(test_config)


@pytest.fixture
def enricher(test_config):
    """Create ConstraintEnricher instance"""
    config_path = Path(__file__).parent.parent / "config" / "constraint_patterns.yaml"
    return ConstraintEnricher(config_path=config_path)


# =============================================================================
# PatternMatcher Tests
# =============================================================================


class TestPatternMatcher:
    """Test pattern matching functionality"""

    def test_match_string_name_pattern(self, pattern_matcher):
        """Test matching 'name' field against string patterns"""
        result = pattern_matcher.match_string_pattern("name")
        assert result is not None
        assert result["description"] == "DNS-1035 label format resource name"
        assert result["metadata"]["confidence"] == 0.99

    def test_match_string_email_pattern(self, pattern_matcher):
        """Test matching 'email' field against string patterns"""
        result = pattern_matcher.match_string_pattern("email")
        assert result is not None
        assert result["constraints"]["format"] == "email"
        assert result["metadata"]["category"] == "communication"

    def test_match_string_uuid_pattern(self, pattern_matcher):
        """Test matching 'uuid' field against string patterns"""
        result = pattern_matcher.match_string_pattern("uuid")
        assert result is not None
        assert result["constraints"]["minLength"] == 36
        assert result["constraints"]["maxLength"] == 36
        assert result["metadata"]["confidence"] == 0.99

    def test_match_string_no_match(self, pattern_matcher):
        """Test field with no matching pattern"""
        result = pattern_matcher.match_string_pattern("unknown_field_xyz")
        assert result is None

    def test_match_array_origins_pattern(self, pattern_matcher):
        """Test matching 'origins' field against array patterns"""
        result = pattern_matcher.match_array_pattern("origins")
        assert result is not None
        assert result["constraints"]["minItems"] == 1
        assert result["constraints"]["maxItems"] == 50

    def test_match_array_tags_pattern(self, pattern_matcher):
        """Test matching 'tags' field against array patterns"""
        result = pattern_matcher.match_array_pattern("tags")
        assert result is not None
        assert result["constraints"]["maxItems"] == 100

    def test_match_number_port_pattern(self, pattern_matcher):
        """Test matching 'port' field against numeric patterns"""
        result = pattern_matcher.match_number_pattern("port")
        assert result is not None
        assert result["constraints"]["minimum"] == 1
        assert result["constraints"]["maximum"] == 65535
        assert result["metadata"]["confidence"] == 0.99

    def test_match_number_timeout_pattern(self, pattern_matcher):
        """Test matching 'timeout' field against numeric patterns"""
        result = pattern_matcher.match_number_pattern("timeout")
        assert result is not None
        assert result["constraints"]["minimum"] == 1
        assert result["constraints"]["maximum"] == 3600

    def test_match_number_vlan_id_pattern(self, pattern_matcher):
        """Test matching 'vlan_id' field against numeric patterns"""
        result = pattern_matcher.match_number_pattern("vlan_id")
        assert result is not None
        assert result["constraints"]["minimum"] == 1
        assert result["constraints"]["maximum"] == 4094
        assert result["metadata"]["confidence"] == 0.95

    def test_pattern_case_insensitive(self, pattern_matcher):
        """Test patterns are case-insensitive"""
        result1 = pattern_matcher.match_string_pattern("name")
        result2 = pattern_matcher.match_string_pattern("NAME")
        result3 = pattern_matcher.match_string_pattern("Name")
        assert result1 is not None
        assert result2 is not None
        assert result3 is not None


# =============================================================================
# StringConstraintExtractor Tests
# =============================================================================


class TestStringConstraintExtractor:
    """Test string constraint extraction"""

    def test_extract_from_existing_schema(self):
        """Test extraction from schema with existing constraints"""
        schema = {
            "type": "string",
            "minLength": 1,
            "maxLength": 100,
            "pattern": "^[a-z]+$",
        }
        result = StringConstraintExtractor.extract("test_field", schema, None)
        assert result is not None
        assert result["minLength"] == 1
        assert result["maxLength"] == 100
        assert result["pattern"] == "^[a-z]+$"

    def test_extract_from_pattern_match(self, pattern_matcher):
        """Test extraction from pattern match only"""
        schema = {"type": "string"}
        pattern_match = pattern_matcher.match_string_pattern("name")
        result = StringConstraintExtractor.extract("name", schema, pattern_match)
        assert result is not None
        assert result["minLength"] == 1
        assert result["maxLength"] == 63
        assert "pattern" in result

    def test_extract_schema_preserved_for_non_authoritative_pattern(self, pattern_matcher):
        """Non-authoritative patterns only fill gaps; existing schema values win."""
        schema = {
            "type": "string",
            "minLength": 10,  # Different from the email pattern's defaults
            "maxLength": 50,
        }
        pattern_match = pattern_matcher.match_string_pattern("email")
        assert not pattern_match.get("authoritative")
        result = StringConstraintExtractor.extract("email", schema, pattern_match)
        assert result is not None
        assert result["minLength"] == 10  # Schema value preserved
        assert result["maxLength"] == 50  # Schema value preserved

    def test_extract_authoritative_naming_pattern_overrides_schema(self, pattern_matcher):
        """Authoritative naming patterns override generic/incorrect schema values.

        F5 XC enforces DNS-1035 length 1-63 (live-verified); a stale generic bound
        (e.g. the 1024 string default) must not survive on a name field.
        """
        schema = {
            "type": "string",
            "minLength": 6,  # Incorrect values a prior enricher may have stamped
            "maxLength": 1024,
        }
        pattern_match = pattern_matcher.match_string_pattern("name")
        assert pattern_match.get("authoritative") is True
        result = StringConstraintExtractor.extract("name", schema, pattern_match)
        assert result is not None
        assert result["minLength"] == 1  # DNS-1035 authoritative bound
        assert result["maxLength"] == 63

    def test_extract_no_constraints(self):
        """Test extraction when no constraints available"""
        schema = {"type": "string"}
        result = StringConstraintExtractor.extract("test_field", schema, None)
        assert result is None


# =============================================================================
# ArrayConstraintExtractor Tests
# =============================================================================


class TestArrayConstraintExtractor:
    """Test array constraint extraction"""

    def test_extract_from_existing_schema(self):
        """Test extraction from schema with existing constraints"""
        schema = {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "uniqueItems": True,
        }
        result = ArrayConstraintExtractor.extract("test_field", schema, None)
        assert result is not None
        assert result["minItems"] == 1
        assert result["maxItems"] == 10
        assert result["uniqueItems"] is True

    def test_extract_from_pattern_match(self, pattern_matcher):
        """Test extraction from pattern match only"""
        schema = {"type": "array"}
        pattern_match = pattern_matcher.match_array_pattern("origins")
        result = ArrayConstraintExtractor.extract("origins", schema, pattern_match)
        assert result is not None
        assert result["minItems"] == 1
        assert result["maxItems"] == 50

    def test_extract_no_constraints(self):
        """Test extraction when no constraints available"""
        schema = {"type": "array"}
        result = ArrayConstraintExtractor.extract("test_field", schema, None)
        assert result is None


# =============================================================================
# NumericConstraintExtractor Tests
# =============================================================================


class TestNumericConstraintExtractor:
    """Test numeric constraint extraction"""

    def test_extract_from_existing_schema(self):
        """Test extraction from schema with existing constraints"""
        schema = {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "multipleOf": 5,
        }
        result = NumericConstraintExtractor.extract("test_field", schema, None)
        assert result is not None
        assert result["minimum"] == 1
        assert result["maximum"] == 100
        assert result["multipleOf"] == 5

    def test_extract_from_pattern_match(self, pattern_matcher):
        """Test extraction from pattern match only"""
        schema = {"type": "integer"}
        pattern_match = pattern_matcher.match_number_pattern("port")
        result = NumericConstraintExtractor.extract("port", schema, pattern_match)
        assert result is not None
        assert result["minimum"] == 1
        assert result["maximum"] == 65535

    def test_extract_no_constraints(self):
        """Test extraction when no constraints available"""
        schema = {"type": "integer"}
        result = NumericConstraintExtractor.extract("test_field", schema, None)
        assert result is None


# =============================================================================
# ObjectConstraintExtractor Tests
# =============================================================================


class TestObjectConstraintExtractor:
    """Test object constraint extraction"""

    def test_extract_from_existing_schema(self):
        """Test extraction from schema with existing constraints"""
        schema = {
            "type": "object",
            "minProperties": 1,
            "maxProperties": 10,
            "required": ["field1", "field2"],
        }
        result = ObjectConstraintExtractor.extract("test_field", schema)
        assert result is not None
        assert result["minProperties"] == 1
        assert result["maxProperties"] == 10
        assert result["required"] == ["field1", "field2"]

    def test_extract_no_constraints(self):
        """Test extraction when no constraints available"""
        schema = {"type": "object"}
        result = ObjectConstraintExtractor.extract("test_field", schema)
        assert result is None


# =============================================================================
# ConstraintReconciler Tests
# =============================================================================


class TestConstraintReconciler:
    """Test constraint reconciliation logic"""

    def test_existing_takes_priority(self):
        """Test that existing constraints are preserved"""
        existing = {
            "constraintType": "string",
            "minLength": 10,
            "metadata": {"source": "existing", "confidence": 1.0},
        }
        discovery = {
            "constraintType": "string",
            "minLength": 5,
        }
        inferred = {
            "constraintType": "string",
            "minLength": 1,
        }

        result = ConstraintReconciler.reconcile(existing, discovery, inferred)
        assert result == existing

    def test_discovery_overrides_inferred(self):
        """Test that discovery data overrides inferred"""
        discovery = {
            "constraintType": "string",
            "minLength": 5,
        }
        inferred = {
            "constraintType": "string",
            "minLength": 1,
            "metadata": {"source": "inferred", "confidence": 0.85},
        }

        result = ConstraintReconciler.reconcile(None, discovery, inferred)
        assert result is not None
        assert result["metadata"]["source"] == "discovery"
        assert result["metadata"]["confidence"] == 0.99

    def test_inferred_only(self):
        """Test reconciliation with only inferred data"""
        inferred = {
            "constraintType": "string",
            "minLength": 1,
            "metadata": {"source": "inferred", "confidence": 0.85},
        }

        result = ConstraintReconciler.reconcile(None, None, inferred)
        assert result is not None
        assert result["metadata"]["source"] == "inferred"
        assert result["metadata"]["confidence"] == 0.85

    def test_deterministic_flag_high_confidence(self):
        """Test deterministic flag for high confidence"""
        inferred = {
            "constraintType": "string",
            "minLength": 1,
            "metadata": {"source": "inferred", "confidence": 0.95},
        }

        result = ConstraintReconciler.reconcile(None, None, inferred, confidence_threshold=0.9)
        assert result is not None
        assert result.get("deterministic") is True

    def test_deterministic_flag_low_confidence(self):
        """Test no deterministic flag for low confidence"""
        inferred = {
            "constraintType": "string",
            "minLength": 1,
            "metadata": {"source": "inferred", "confidence": 0.75},
        }

        result = ConstraintReconciler.reconcile(None, None, inferred, confidence_threshold=0.9)
        assert result is not None
        assert "deterministic" not in result or result.get("deterministic") is False

    def test_no_constraints(self):
        """Test reconciliation with no constraints"""
        result = ConstraintReconciler.reconcile(None, None, None)
        assert result is None


# =============================================================================
# ConstraintEnricher Integration Tests
# =============================================================================


class TestConstraintEnricher:
    """Test the complete enrichment process"""

    def test_enrich_string_property(self, enricher):
        """Test enriching a string property"""
        spec = {
            "components": {
                "schemas": {
                    "TestSchema": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                            },
                        },
                    },
                },
            },
        }

        enriched = enricher.enrich_spec(spec)
        name_property = enriched["components"]["schemas"]["TestSchema"]["properties"]["name"]

        assert "x-f5xc-constraints" in name_property
        constraints = name_property["x-f5xc-constraints"]
        assert constraints["constraintType"] == "string"
        assert constraints["minLength"] == 1
        assert constraints["maxLength"] == 63

    def test_enrich_array_property(self, enricher):
        """Test enriching an array property"""
        spec = {
            "components": {
                "schemas": {
                    "TestSchema": {
                        "type": "object",
                        "properties": {
                            "origins": {
                                "type": "array",
                            },
                        },
                    },
                },
            },
        }

        enriched = enricher.enrich_spec(spec)
        origins_property = enriched["components"]["schemas"]["TestSchema"]["properties"]["origins"]

        assert "x-f5xc-constraints" in origins_property
        constraints = origins_property["x-f5xc-constraints"]
        assert constraints["constraintType"] == "array"
        assert constraints["minItems"] == 1
        assert constraints["maxItems"] == 50

    def test_enrich_number_property(self, enricher):
        """Test enriching a numeric property"""
        spec = {
            "components": {
                "schemas": {
                    "TestSchema": {
                        "type": "object",
                        "properties": {
                            "port": {
                                "type": "integer",
                            },
                        },
                    },
                },
            },
        }

        enriched = enricher.enrich_spec(spec)
        port_property = enriched["components"]["schemas"]["TestSchema"]["properties"]["port"]

        assert "x-f5xc-constraints" in port_property
        constraints = port_property["x-f5xc-constraints"]
        assert constraints["constraintType"] == "number"
        assert constraints["minimum"] == 1
        assert constraints["maximum"] == 65535

    def test_enrich_uint32_discovery_bounds(self, enricher):
        """uint32.gte/lte validation rules must yield numeric min/max bounds."""
        spec = {
            "components": {
                "schemas": {
                    "IfSpec": {
                        "type": "object",
                        "properties": {
                            "priority": {
                                "type": "integer",
                                "x-ves-validation-rules": {
                                    "ves.io.schema.rules.uint32.gte": "0",
                                    "ves.io.schema.rules.uint32.lte": "255",
                                },
                            },
                        },
                    },
                },
            },
        }
        enriched = enricher.enrich_spec(spec)
        c = enriched["components"]["schemas"]["IfSpec"]["properties"]["priority"][
            "x-f5xc-constraints"
        ]
        assert c["minimum"] == 0
        assert c["maximum"] == 255
        assert c["metadata"]["source"] == "api-probed"

    def test_enrich_uint32_ranges_discontinuous(self, enricher):
        """A discontinuous uint32.ranges yields maximum only (no single minimum)."""
        spec = {
            "components": {
                "schemas": {
                    "IfSpec": {
                        "type": "object",
                        "properties": {
                            "mtu": {
                                "type": "integer",
                                "x-ves-validation-rules": {
                                    "ves.io.schema.rules.uint32.ranges": "0,512-16384",
                                },
                            },
                        },
                    },
                },
            },
        }
        c = enricher.enrich_spec(spec)["components"]["schemas"]["IfSpec"]["properties"]["mtu"][
            "x-f5xc-constraints"
        ]
        assert c["maximum"] == 16384
        assert "minimum" not in c  # discontinuous → no single lower bound

    def test_enrich_uint32_ranges_contiguous(self, enricher):
        """A single contiguous uint32.ranges yields both minimum and maximum."""
        spec = {
            "components": {
                "schemas": {
                    "IfSpec": {
                        "type": "object",
                        "properties": {
                            "n": {
                                "type": "integer",
                                "x-ves-validation-rules": {
                                    "ves.io.schema.rules.uint32.ranges": "10-20",
                                },
                            },
                        },
                    },
                },
            },
        }
        c = enricher.enrich_spec(spec)["components"]["schemas"]["IfSpec"]["properties"]["n"][
            "x-f5xc-constraints"
        ]
        assert c["minimum"] == 10
        assert c["maximum"] == 20

    def test_name_inference_skips_non_numeric(self, enricher):
        """A string field named ``id`` must NOT receive numeric bounds."""
        spec = {
            "components": {
                "schemas": {
                    "S": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Subnet ID"},
                        },
                    },
                },
            },
        }
        p = enricher.enrich_spec(spec)["components"]["schemas"]["S"]["properties"]["id"]
        c = p.get("x-f5xc-constraints", {})
        assert "minimum" not in p
        assert "maximum" not in p
        assert c.get("constraintType") != "number"

    def test_skip_existing_constraints(self, enricher):
        """Test that existing x-f5xc-constraints are preserved"""
        spec = {
            "components": {
                "schemas": {
                    "TestSchema": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "x-f5xc-constraints": {
                                    "constraintType": "string",
                                    "minLength": 100,  # Different value
                                    "metadata": {"source": "existing", "confidence": 1.0},
                                },
                            },
                        },
                    },
                },
            },
        }

        enriched = enricher.enrich_spec(spec)
        name_property = enriched["components"]["schemas"]["TestSchema"]["properties"]["name"]

        # Original constraint preserved
        assert name_property["x-f5xc-constraints"]["minLength"] == 100
        assert name_property["x-f5xc-constraints"]["metadata"]["source"] == "existing"

    def test_statistics_collection(self, enricher):
        """Test that statistics are collected correctly"""
        spec = {
            "components": {
                "schemas": {
                    "TestSchema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "email": {"type": "string"},
                            "port": {"type": "integer"},
                            "tags": {"type": "array"},
                        },
                    },
                },
            },
        }

        enricher.enrich_spec(spec)
        stats = enricher.get_stats()

        assert stats["properties_analyzed"] == 4
        assert stats["constraints_added"] > 0
        assert stats["string_constraints"] >= 2
        assert stats["array_constraints"] >= 1
        assert stats["number_constraints"] >= 1
        assert stats["coverage_percentage"] > 0


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================


class TestEdgeCases:
    """Test edge cases and error handling"""

    def test_empty_spec(self, enricher):
        """Test enriching an empty spec"""
        spec = {}
        result = enricher.enrich_spec(spec)
        assert result == {}

    def test_spec_without_schemas(self, enricher):
        """Test spec without components.schemas"""
        spec = {
            "openapi": "3.0.0",
            "info": {"title": "Test", "version": "1.0.0"},
        }
        enricher.enrich_spec(spec)
        stats = enricher.get_stats()
        assert stats["properties_analyzed"] == 0

    def test_schema_without_properties(self, enricher):
        """Test schema without properties"""
        spec = {
            "components": {
                "schemas": {
                    "TestSchema": {
                        "type": "object",
                    },
                },
            },
        }
        enricher.enrich_spec(spec)
        stats = enricher.get_stats()
        assert stats["properties_analyzed"] == 0

    def test_unknown_field_type(self, enricher):
        """Test property with unknown type"""
        spec = {
            "components": {
                "schemas": {
                    "TestSchema": {
                        "type": "object",
                        "properties": {
                            "unknown": {
                                # No type field
                            },
                        },
                    },
                },
            },
        }
        enriched = enricher.enrich_spec(spec)
        unknown_property = enriched["components"]["schemas"]["TestSchema"]["properties"]["unknown"]
        assert "x-f5xc-constraints" not in unknown_property


# =============================================================================
# Pattern Coverage Tests
# =============================================================================


class TestPatternCoverage:
    """Test coverage of various patterns"""

    @pytest.mark.parametrize(
        ("field_name", "expected_category"),
        [
            ("username", "identity"),
            ("email", "communication"),
            ("phone", "communication"),
            ("uuid", "identifier"),
            ("timestamp", "temporal"),
            ("date", "temporal"),
            ("certificate", "crypto"),
            ("private_key", "crypto"),
            ("description", "content"),
            ("path", "content"),
        ],
    )
    def test_string_pattern_categories(self, pattern_matcher, field_name, expected_category):
        """Test string patterns match correct categories"""
        result = pattern_matcher.match_string_pattern(field_name)
        assert result is not None
        assert result["metadata"]["category"] == expected_category

    @pytest.mark.parametrize(
        "field_name",
        [
            "servers",
            "pools",
            "origins",
            "tags",
            "domains",
            "items",
            "rules",
            "ips",
            "hosts",
            "headers",
        ],
    )
    def test_array_pattern_coverage(self, pattern_matcher, field_name):
        """Test array patterns are matched"""
        result = pattern_matcher.match_array_pattern(field_name)
        assert result is not None
        assert "constraints" in result

    @pytest.mark.parametrize(
        "field_name",
        [
            "port",
            "vlan_id",
            "timeout",
            "interval",
            "retries",
            "delay",
            "weight",
            "priority",
            "threshold",
            "limit",
        ],
    )
    def test_number_pattern_coverage(self, pattern_matcher, field_name):
        """Test numeric patterns are matched"""
        result = pattern_matcher.match_number_pattern(field_name)
        assert result is not None
        assert "constraints" in result


class TestResourceConstraintOverrides:
    """Tests for resource_constraint_overrides in constraint_patterns.yaml."""

    @pytest.fixture
    def config_path(self):
        return Path(__file__).parent.parent / "config" / "constraint_patterns.yaml"

    @pytest.fixture
    def enricher(self, config_path):
        return ConstraintEnricher(config_path=config_path)

    @pytest.fixture
    def healthcheck_spec(self):
        return {
            "components": {
                "schemas": {
                    "healthcheckCreateSpecType": {
                        "type": "object",
                        "properties": {
                            "timeout": {"type": "integer"},
                            "interval": {"type": "integer"},
                            "healthy_threshold": {"type": "integer"},
                            "unhealthy_threshold": {"type": "integer"},
                            "jitter_percent": {"type": "integer"},
                        },
                    },
                },
            },
        }

    def test_timeout_uses_healthcheck_override(self, enricher, healthcheck_spec):
        result = enricher.enrich_spec(healthcheck_spec)
        timeout = result["components"]["schemas"]["healthcheckCreateSpecType"]["properties"][
            "timeout"
        ]
        constraints = timeout["x-f5xc-constraints"]
        assert constraints["maximum"] == 600, f"Expected 600 but got {constraints['maximum']}"

    def test_healthy_threshold_has_constraints(self, enricher, healthcheck_spec):
        result = enricher.enrich_spec(healthcheck_spec)
        field = result["components"]["schemas"]["healthcheckCreateSpecType"]["properties"][
            "healthy_threshold"
        ]
        constraints = field["x-f5xc-constraints"]
        assert constraints["minimum"] == 1
        assert constraints["maximum"] == 16

    def test_unhealthy_threshold_has_constraints(self, enricher, healthcheck_spec):
        result = enricher.enrich_spec(healthcheck_spec)
        field = result["components"]["schemas"]["healthcheckCreateSpecType"]["properties"][
            "unhealthy_threshold"
        ]
        constraints = field["x-f5xc-constraints"]
        assert constraints["minimum"] == 1
        assert constraints["maximum"] == 16

    def test_jitter_percent_has_constraints(self, enricher, healthcheck_spec):
        result = enricher.enrich_spec(healthcheck_spec)
        field = result["components"]["schemas"]["healthcheckCreateSpecType"]["properties"][
            "jitter_percent"
        ]
        constraints = field["x-f5xc-constraints"]
        assert "ranges" in constraints
        assert "minimum" not in constraints
        assert "maximum" not in constraints
        ranges = constraints["ranges"]
        assert len(ranges) == 2
        assert ranges[0] == {"minimum": 0, "maximum": 0}
        assert ranges[1] == {"minimum": 10, "maximum": 50}

    def test_interval_uses_global_pattern(self, enricher, healthcheck_spec):
        """interval has no resource override — should use global pattern (max 600)."""
        result = enricher.enrich_spec(healthcheck_spec)
        interval = result["components"]["schemas"]["healthcheckCreateSpecType"]["properties"][
            "interval"
        ]
        constraints = interval["x-f5xc-constraints"]
        assert constraints["maximum"] == 600

    def test_non_healthcheck_timeout_uses_global(self, enricher):
        """A non-healthcheck schema should still get the global timeout max (3600)."""
        spec = {
            "components": {
                "schemas": {
                    "someOtherSpecType": {
                        "type": "object",
                        "properties": {
                            "timeout": {"type": "integer"},
                        },
                    },
                },
            },
        }
        result = enricher.enrich_spec(spec)
        timeout = result["components"]["schemas"]["someOtherSpecType"]["properties"]["timeout"]
        constraints = timeout["x-f5xc-constraints"]
        assert constraints["maximum"] == 3600

    def test_override_includes_constraint_type(self, enricher, healthcheck_spec):
        result = enricher.enrich_spec(healthcheck_spec)
        timeout = result["components"]["schemas"]["healthcheckCreateSpecType"]["properties"][
            "timeout"
        ]
        constraints = timeout["x-f5xc-constraints"]
        assert constraints["constraintType"] == "number"

    def test_override_includes_deterministic(self, enricher, healthcheck_spec):
        result = enricher.enrich_spec(healthcheck_spec)
        timeout = result["components"]["schemas"]["healthcheckCreateSpecType"]["properties"][
            "timeout"
        ]
        constraints = timeout["x-f5xc-constraints"]
        assert constraints["deterministic"] is True

    def test_override_includes_validated_at(self, enricher, healthcheck_spec):
        result = enricher.enrich_spec(healthcheck_spec)
        timeout = result["components"]["schemas"]["healthcheckCreateSpecType"]["properties"][
            "timeout"
        ]
        constraints = timeout["x-f5xc-constraints"]
        assert "validatedAt" in constraints["metadata"]

    def test_override_includes_category(self, enricher, healthcheck_spec):
        result = enricher.enrich_spec(healthcheck_spec)
        timeout = result["components"]["schemas"]["healthcheckCreateSpecType"]["properties"][
            "timeout"
        ]
        constraints = timeout["x-f5xc-constraints"]
        assert constraints["category"] == "timing"

    def test_override_applies_to_ref_property(self, enricher):
        """A resource override should apply to a bare $ref property (no inline type)."""
        spec = {
            "components": {
                "schemas": {
                    "origin_poolUpstreamTlsParameters": {
                        "type": "object",
                        "properties": {
                            "tls_config": {
                                "$ref": "#/components/schemas/TlsConfig",
                            },
                        },
                    },
                },
            },
        }
        result = enricher.enrich_spec(spec)
        tls_config = result["components"]["schemas"]["origin_poolUpstreamTlsParameters"][
            "properties"
        ]["tls_config"]
        assert "x-f5xc-constraints" in tls_config
        constraints = tls_config["x-f5xc-constraints"]
        assert constraints["constraintType"] == "object"
        assert "note" in constraints["metadata"]
        assert "use_tls" in constraints["metadata"]["note"]

    def test_override_on_ref_respects_existing_constraints(self, enricher):
        """If a $ref property already has x-f5xc-constraints, override is skipped."""
        spec = {
            "components": {
                "schemas": {
                    "origin_poolUpstreamTlsParameters": {
                        "type": "object",
                        "properties": {
                            "tls_config": {
                                "$ref": "#/components/schemas/TlsConfig",
                                "x-f5xc-constraints": {"existing": True},
                            },
                        },
                    },
                },
            },
        }
        result = enricher.enrich_spec(spec)
        tls_config = result["components"]["schemas"]["origin_poolUpstreamTlsParameters"][
            "properties"
        ]["tls_config"]
        assert tls_config["x-f5xc-constraints"] == {"existing": True}

    def test_tls_config_note_on_upstream_tls_parameters(self, enricher):
        """Integration: tls_config in origin_poolUpstreamTlsParameters gets constraint note."""
        spec = {
            "components": {
                "schemas": {
                    "origin_poolUpstreamTlsParameters": {
                        "type": "object",
                        "properties": {
                            "tls_config": {
                                "$ref": "#/components/schemas/TlsConfig",
                            },
                            "max_session_keys": {
                                "type": "integer",
                                "x-ves-validation-rules": {
                                    "ves.io.schema.rules.uint32.gte": "2",
                                },
                            },
                        },
                    },
                },
            },
        }
        result = enricher.enrich_spec(spec)
        props = result["components"]["schemas"]["origin_poolUpstreamTlsParameters"]["properties"]

        # tls_config should have the constraint note
        assert "x-f5xc-constraints" in props["tls_config"]
        note = props["tls_config"]["x-f5xc-constraints"]["metadata"]["note"]
        assert "use_tls" in note
        assert "400" in note


class TestObjectRefNameExclusion:
    """The DNS-1035 self-name bound must NOT apply to object-REFERENCE wrappers.

    schemaObjectRefType / ioschemaObjectRefType / schemaviewsObjectRefType have a
    `name` field that POINTS AT another object. That target can be server-generated
    and exceed 63 chars (e.g. the 71-char SMSv2 auto interface name), so the
    authoritative DNS-1035 length/pattern bound wrongly rejects valid references.
    """

    @pytest.fixture
    def config_path(self):
        return Path(__file__).parent.parent / "config" / "constraint_patterns.yaml"

    @pytest.fixture
    def enricher(self, config_path):
        return ConstraintEnricher(config_path=config_path)

    @pytest.mark.parametrize(
        "schema_name",
        ["schemaObjectRefType", "ioschemaObjectRefType", "schemaviewsObjectRefType"],
    )
    def test_objectref_name_gets_no_dns1035_bound(self, enricher, schema_name):
        """A bare `name` in an ObjectRefType wrapper gets no maxLength:63 / DNS-1035 pattern."""
        spec = {
            "components": {
                "schemas": {
                    schema_name: {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                        },
                    },
                },
            },
        }
        result = enricher.enrich_spec(spec)
        name_prop = result["components"]["schemas"][schema_name]["properties"]["name"]
        constraints = name_prop.get("x-f5xc-constraints", {})
        assert constraints.get("maxLength") != 63
        assert constraints.get("pattern") != "^[a-z]([-a-z0-9]*[a-z0-9])?$"
        assert constraints.get("format") != "dns-label"

    def test_objectref_name_preserves_genuine_inline_bound(self, enricher):
        """schemaviewsObjectRefType carries a real inline maxLength (128) in the source.

        Skipping the authoritative naming PATTERN must still preserve the genuine
        spec-level inline bound; it must not be clobbered to 63 nor stamped DNS-1035.
        """
        spec = {
            "components": {
                "schemas": {
                    "schemaviewsObjectRefType": {
                        "type": "object",
                        "properties": {
                            "name": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 128,
                            },
                        },
                    },
                },
            },
        }
        result = enricher.enrich_spec(spec)
        name_prop = result["components"]["schemas"]["schemaviewsObjectRefType"]["properties"][
            "name"
        ]
        constraints = name_prop["x-f5xc-constraints"]
        assert constraints["maxLength"] == 128
        assert constraints.get("pattern") != "^[a-z]([-a-z0-9]*[a-z0-9])?$"

    def test_objectref_namespace_still_gets_naming_bound(self, enricher):
        """Scope guard: only `name` is excluded; `namespace` in a ref wrapper is unchanged."""
        spec = {
            "components": {
                "schemas": {
                    "schemaObjectRefType": {
                        "type": "object",
                        "properties": {
                            "namespace": {"type": "string"},
                        },
                    },
                },
            },
        }
        result = enricher.enrich_spec(spec)
        ns_prop = result["components"]["schemas"]["schemaObjectRefType"]["properties"]["namespace"]
        constraints = ns_prop["x-f5xc-constraints"]
        assert constraints["maxLength"] == 63
        assert constraints["pattern"] == "^[a-z]([-a-z0-9]*[a-z0-9])?$"

    def test_metadata_name_still_gets_dns1035_bound(self, enricher):
        """The object's OWN name (create/replace meta schemas) MUST keep the DNS-1035 bound."""
        spec = {
            "components": {
                "schemas": {
                    "schemaObjectCreateMetaType": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                        },
                    },
                },
            },
        }
        result = enricher.enrich_spec(spec)
        name_prop = result["components"]["schemas"]["schemaObjectCreateMetaType"]["properties"][
            "name"
        ]
        constraints = name_prop["x-f5xc-constraints"]
        assert constraints["minLength"] == 1
        assert constraints["maxLength"] == 63
        assert constraints["pattern"] == "^[a-z]([-a-z0-9]*[a-z0-9])?$"


if __name__ == "__main__":
    pytest.main(
        [__file__, "-v", "--cov=scripts.utils.constraint_enricher", "--cov-report=term-missing"],
    )
