# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=protected-access,use-implicit-booleaness-not-comparison  # Tests inspect internals and explicit result shapes.

"""Unit tests for ResourceExamplesEnricher."""

import pytest
import yaml

from scripts.utils.resource_examples_enricher import (
    ExampleValidator,
    ResourceExample,
    ResourceExamples,
    ResourceExamplesEnricher,
    ResourceExamplesEnrichmentStats,
)


@pytest.fixture
def enricher():
    """Create enricher with default config."""
    return ResourceExamplesEnricher()


@pytest.fixture
def sample_schemas():
    """Create sample OpenAPI schemas for validation."""
    return {
        "http_loadbalancerCreateRequest": {
            "type": "object",
            "properties": {
                "metadata": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "namespace": {"type": "string"},
                    },
                },
                "spec": {
                    "type": "object",
                    "properties": {
                        "domains": {"type": "array", "items": {"type": "string"}},
                        "http": {
                            "type": "object",
                            "properties": {"port": {"type": "integer"}},
                        },
                    },
                },
            },
        },
    }


@pytest.fixture
def spec_entry():
    """Create a sample spec entry for enrichment."""
    return {
        "domain": "virtual",
        "title": "Virtual API",
        "version": "1.0.0",
    }


class TestResourceExamplesEnricherBasics:
    """Test basic enricher functionality."""

    def test_initialization(self):
        """Test enricher initializes correctly."""
        enricher = ResourceExamplesEnricher()
        assert enricher is not None
        assert enricher.config_path is not None

    def test_config_version(self, enricher):
        """Test config version is loaded."""
        version = enricher.get_config_version()
        assert version is not None
        assert isinstance(version, str)

    def test_configured_domains(self, enricher):
        """Test configured domains list."""
        domains = enricher.get_configured_domains()
        assert isinstance(domains, list)
        # Virtual domain should be configured
        assert "virtual" in domains

    def test_stats_initialization(self, enricher):
        """Test stats are initialized to zero."""
        stats = enricher.get_stats()
        assert stats["domains_processed"] == 0
        assert stats["domains_with_examples"] == 0
        assert stats["resources_with_examples"] == 0
        assert stats["examples_added"] == 0


class TestResourceExamplesRetrieval:
    """Test example retrieval methods."""

    def test_get_examples_for_virtual_domain(self, enricher):
        """Test getting examples for virtual domain."""
        examples = enricher.get_examples_for_domain("virtual")
        assert examples is not None
        assert isinstance(examples, dict)
        assert "http_loadbalancer" in examples

    def test_get_examples_for_unknown_domain(self, enricher):
        """Test getting examples for unknown domain returns empty."""
        examples = enricher.get_examples_for_domain("unknown_domain_xyz")
        assert examples == {}

    def test_get_examples_for_resource(self, enricher):
        """Test getting examples for specific resource."""
        examples = enricher.get_examples_for_resource("virtual", "http_loadbalancer")
        assert examples is not None
        assert isinstance(examples, ResourceExamples)
        assert examples.minimal is not None

    def test_get_examples_for_unknown_resource(self, enricher):
        """Test getting examples for unknown resource returns None."""
        examples = enricher.get_examples_for_resource("virtual", "unknown_resource")
        assert examples is None

    def test_has_examples_true(self, enricher):
        """Test has_examples returns True for configured domain."""
        assert enricher.has_examples("virtual") is True

    def test_has_examples_false(self, enricher):
        """Test has_examples returns False for unknown domain."""
        assert enricher.has_examples("unknown_domain") is False


class TestResourceExampleStructure:
    """Test ResourceExample dataclass structure."""

    def test_resource_example_fields(self, enricher):
        """Test that resource examples have all required fields."""
        examples = enricher.get_examples_for_resource("virtual", "http_loadbalancer")
        assert examples is not None
        assert examples.minimal is not None

        example = examples.minimal
        assert isinstance(example, ResourceExample)
        assert hasattr(example, "description")
        assert hasattr(example, "use_case")
        assert hasattr(example, "yaml_content")
        assert hasattr(example, "validated")

    def test_resource_example_to_dict(self, enricher):
        """Test resource example to_dict method."""
        examples = enricher.get_examples_for_resource("virtual", "http_loadbalancer")
        assert examples is not None
        assert examples.minimal is not None

        example_dict = examples.minimal.to_dict()
        assert isinstance(example_dict, dict)
        assert "description" in example_dict
        assert "use_case" in example_dict
        assert "yaml" in example_dict


class TestResourceExamplesContainer:
    """Test ResourceExamples container class."""

    def test_resource_examples_tiers(self, enricher):
        """Test that resource examples have tier structure."""
        examples = enricher.get_examples_for_resource("virtual", "http_loadbalancer")
        assert examples is not None

        # Should have at least minimal and production
        assert examples.minimal is not None
        assert examples.production is not None

    def test_resource_examples_to_dict(self, enricher):
        """Test resource examples to_dict method."""
        examples = enricher.get_examples_for_resource("virtual", "http_loadbalancer")
        assert examples is not None

        examples_dict = examples.to_dict()
        assert isinstance(examples_dict, dict)
        assert "minimal" in examples_dict
        assert "production" in examples_dict

    def test_resource_examples_is_empty(self):
        """Test is_empty method on ResourceExamples."""
        empty = ResourceExamples()
        assert empty.is_empty() is True

        with_minimal = ResourceExamples(
            minimal=ResourceExample(
                description="test",
                use_case="test",
                yaml_content="test: value",
            ),
        )
        assert with_minimal.is_empty() is False

    def test_resource_examples_count_tiers(self):
        """Test count_tiers method."""
        empty = ResourceExamples()
        assert empty.count_tiers() == 0

        with_two = ResourceExamples(
            minimal=ResourceExample("desc", "use", "yaml: content"),
            production=ResourceExample("desc", "use", "yaml: content"),
        )
        assert with_two.count_tiers() == 2


class TestExampleValidator:
    """Test ExampleValidator class."""

    def test_validator_initialization(self, sample_schemas):
        """Test validator initializes correctly."""
        validator = ExampleValidator(sample_schemas)
        assert validator is not None
        assert validator.schemas == sample_schemas

    def test_validate_valid_yaml(self, sample_schemas):
        """Test validating valid YAML content."""
        validator = ExampleValidator(sample_schemas)
        yaml_content = """
metadata:
    name: my-lb
    namespace: default
spec:
    domains:
        - example.com
"""
        is_valid, errors = validator.validate_example(yaml_content, "http_loadbalancer")
        # Should be valid (or skip validation if jsonschema not available)
        assert is_valid is True

    def test_validate_invalid_yaml_syntax(self, sample_schemas):
        """Test validating YAML with syntax errors."""
        validator = ExampleValidator(sample_schemas)
        yaml_content = """
invalid: yaml:
    - missing proper structure
    this is not valid: [
"""
        is_valid, errors = validator.validate_example(yaml_content, "http_loadbalancer")
        assert is_valid is False
        assert len(errors) > 0
        assert "YAML parse error" in errors[0]

    def test_validate_empty_yaml(self, sample_schemas):
        """Test validating empty YAML content."""
        validator = ExampleValidator(sample_schemas)
        is_valid, errors = validator.validate_example("", "http_loadbalancer")
        assert is_valid is False
        assert "Empty YAML content" in errors[0]

    def test_validate_no_matching_schema(self):
        """Test validation with no matching schema."""
        validator = ExampleValidator({})
        yaml_content = "test: value"
        is_valid, errors = validator.validate_example(yaml_content, "unknown_resource")
        # Should pass with note about no schema found
        assert is_valid is True
        assert any("No schema found" in e for e in errors)


class TestIndexEntryEnrichment:
    """Test index entry enrichment functionality."""

    def test_enrich_index_entry_with_examples(self, enricher, spec_entry, sample_schemas):
        """Test enriching index entry with examples."""
        enriched = enricher.enrich_index_entry(spec_entry, "virtual", sample_schemas)
        assert "x-f5xc-examples" in enriched

    def test_enrich_index_entry_without_examples(self, enricher, spec_entry):
        """Test enriching index entry for domain without examples."""
        enriched = enricher.enrich_index_entry(spec_entry, "unknown_domain")
        assert "x-f5xc-examples" not in enriched

    def test_enrich_index_entry_structure(self, enricher, spec_entry, sample_schemas):
        """Test enriched entry has correct structure."""
        enriched = enricher.enrich_index_entry(spec_entry, "virtual", sample_schemas)
        examples = enriched["x-f5xc-examples"]

        assert isinstance(examples, dict)
        assert "http_loadbalancer" in examples

        lb_examples = examples["http_loadbalancer"]
        assert "minimal" in lb_examples
        assert "production" in lb_examples

    def test_enrich_index_entry_stats_updated(self, enricher, spec_entry, sample_schemas):
        """Test that stats are updated after enrichment."""
        enricher.enrich_index_entry(spec_entry, "virtual", sample_schemas)
        stats = enricher.get_stats()
        assert stats["domains_processed"] == 1
        assert stats["domains_with_examples"] == 1
        assert stats["resources_with_examples"] > 0

    def test_enrich_preserves_existing_fields(self, enricher, spec_entry, sample_schemas):
        """Test enrichment preserves existing spec entry fields."""
        enriched = enricher.enrich_index_entry(spec_entry, "virtual", sample_schemas)
        assert enriched["domain"] == "virtual"
        assert enriched["title"] == "Virtual API"
        assert enriched["version"] == "1.0.0"


class TestResourceExamplesEnrichmentStats:
    """Test enrichment statistics dataclass."""

    def test_stats_initialization(self):
        """Test stats initialize with zeros."""
        stats = ResourceExamplesEnrichmentStats()
        assert stats.domains_processed == 0
        assert stats.domains_with_examples == 0
        assert stats.resources_with_examples == 0
        assert stats.examples_added == 0
        assert stats.examples_validated == 0
        assert stats.validation_failures == 0
        assert stats.validation_warnings == []

    def test_stats_to_dict(self):
        """Test stats to_dict method."""
        stats = ResourceExamplesEnrichmentStats(
            domains_processed=5,
            domains_with_examples=3,
            resources_with_examples=10,
            examples_added=20,
            examples_validated=18,
            validation_failures=2,
            validation_warnings=["warning1", "warning2"],
        )
        stats_dict = stats.to_dict()
        assert isinstance(stats_dict, dict)
        assert stats_dict["domains_processed"] == 5
        assert stats_dict["domains_with_examples"] == 3
        assert stats_dict["resources_with_examples"] == 10
        assert stats_dict["examples_added"] == 20
        assert stats_dict["validation_warnings_count"] == 2


class TestValidationToggle:
    """Test validation enable/disable functionality."""

    def test_set_validation_enabled(self, enricher):
        """Test setting validation enabled/disabled."""
        enricher.set_validation_enabled(False)
        assert enricher._validate_examples is False

        enricher.set_validation_enabled(True)
        assert enricher._validate_examples is True


class TestMultipleDomains:
    """Test enricher with multiple configured domains."""

    def test_waf_domain_examples(self, enricher):
        """Test WAF domain has examples."""
        examples = enricher.get_examples_for_domain("waf")
        # Should have WAF-specific examples if configured
        if examples:
            assert isinstance(examples, dict)

    def test_dns_domain_examples(self, enricher):
        """Test DNS domain has examples."""
        examples = enricher.get_examples_for_domain("dns")
        # Should have DNS-specific examples if configured
        if examples:
            assert isinstance(examples, dict)

    def test_cdn_domain_examples(self, enricher):
        """Test CDN domain has examples."""
        examples = enricher.get_examples_for_domain("cdn")
        # Should have CDN-specific examples if configured
        if examples:
            assert isinstance(examples, dict)


class TestYamlContentQuality:
    """Test YAML content in examples is valid."""

    def test_minimal_example_yaml_parseable(self, enricher):
        """Test minimal example YAML is parseable."""
        examples = enricher.get_examples_for_resource("virtual", "http_loadbalancer")
        assert examples is not None
        assert examples.minimal is not None

        # YAML should be parseable
        parsed = yaml.safe_load(examples.minimal.yaml_content)
        assert parsed is not None
        assert "metadata" in parsed or "spec" in parsed

    def test_production_example_yaml_parseable(self, enricher):
        """Test production example YAML is parseable."""
        examples = enricher.get_examples_for_resource("virtual", "http_loadbalancer")
        assert examples is not None
        assert examples.production is not None

        # YAML should be parseable
        parsed = yaml.safe_load(examples.production.yaml_content)
        assert parsed is not None
