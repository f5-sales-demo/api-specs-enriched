# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Comprehensive test coverage for ALL API endpoints across all specifications.

This test module programmatically discovers and validates enrichment for every
API endpoint in the F5 XC specification suite (270+ specs, 1700+ operations).

Issue #408: Ensures DRY-compliant descriptions are applied to ALL endpoints,
not just a configured subset.
"""

import json
from pathlib import Path

import pytest

from scripts.utils.operation_description_enricher import OperationDescriptionEnricher
from scripts.utils.operation_metadata_enricher import OperationMetadataEnricher

# Path to original specifications
SPECS_DIR = Path(__file__).parent.parent / "specs" / "original"


def discover_all_endpoints() -> list[tuple[str, str, str, dict]]:
    """Discover all API endpoints from all specifications.

    Returns:
        List of tuples: (spec_name, path, method, operation_dict)
    """
    endpoints = []

    if not SPECS_DIR.exists():
        # discover_all_endpoints() is invoked at module import time on
        # line 94 (ALL_ENDPOINTS = ...), so the skip needs
        # allow_module_level=True — modern pytest rejects module-scope
        # skips without it and aborts collection.
        pytest.skip(
            f"Specs directory not found: {SPECS_DIR}",
            allow_module_level=True,
        )

    metadata_files = {
        "index.json",
        "minimal-export-defaults.json",
        "namespace_profiles.json",
        "resource_coverage.json",
        "validation.json",
    }
    for spec_file in sorted(
        path for path in SPECS_DIR.glob("*.json") if path.name not in metadata_files
    ):
        try:
            with spec_file.open() as f:
                spec = json.load(f)

            spec_name = spec_file.stem
            paths = spec.get("paths", {})

            for path, methods in paths.items():
                if not isinstance(methods, dict):
                    continue
                for method, operation in methods.items():
                    if method.lower() in ["get", "post", "put", "patch", "delete"] and isinstance(
                        operation,
                        dict,
                    ):
                        endpoints.append((spec_name, path, method.lower(), operation))
        except (json.JSONDecodeError, OSError):
            # Skip malformed specs
            continue

    return endpoints


def extract_resource_type(path: str) -> str | None:
    """Extract resource type from API path.

    Args:
        path: API path like /api/config/namespaces/{namespace}/http_loadbalancers

    Returns:
        Resource type like 'http_loadbalancer' or None if cannot be extracted
    """
    segments = [s for s in path.split("/") if s and not s.startswith("{")]
    skip_segments = {
        "api",
        "config",
        "web",
        "system",
        "public",
        "namespaces",
        "v1",
        "v2",
        "ves",
        "io",
        "schema",
    }
    resource_segments = [s for s in segments if s.lower() not in skip_segments]

    if resource_segments:
        resource = resource_segments[-1]
        # Remove trailing 's' for plural forms
        if resource.endswith("s") and not resource.endswith("ss"):
            resource = resource[:-1]
        return resource
    return None


# Discover all endpoints at module load time
ALL_ENDPOINTS = discover_all_endpoints()


@pytest.fixture(scope="module")
def description_enricher():
    """Create OperationDescriptionEnricher (module-scoped for efficiency)."""
    return OperationDescriptionEnricher()


@pytest.fixture(scope="module")
def metadata_enricher():
    """Create OperationMetadataEnricher (module-scoped for efficiency)."""
    return OperationMetadataEnricher()


class TestAllEndpointsDiscovery:
    """Verify endpoint discovery completeness."""

    def test_specs_directory_exists(self):
        """Verify specs directory exists and contains files."""
        if not SPECS_DIR.exists():
            pytest.skip(f"Specs directory not found: {SPECS_DIR}")

        spec_count = len(list(SPECS_DIR.glob("*.json")))
        assert spec_count > 0, "No spec files found"
        assert spec_count >= 200, f"Expected 200+ specs, found {spec_count}"

    def test_endpoint_discovery_count(self):
        """Verify we discovered a reasonable number of endpoints."""
        if not ALL_ENDPOINTS:
            pytest.skip("No endpoints discovered - specs may not be present")

        assert len(ALL_ENDPOINTS) >= 1000, f"Expected 1000+ endpoints, found {len(ALL_ENDPOINTS)}"

    def test_endpoint_methods_distribution(self):
        """Verify distribution of HTTP methods across endpoints."""
        if not ALL_ENDPOINTS:
            pytest.skip("No endpoints discovered")

        method_counts = {}
        for _, _, method, _ in ALL_ENDPOINTS:
            method_counts[method] = method_counts.get(method, 0) + 1

        # Should have all 5 HTTP methods represented
        for method in ["get", "post", "put", "delete"]:
            assert method in method_counts, f"Missing {method.upper()} endpoints"
            assert method_counts[method] > 0, f"No {method.upper()} endpoints found"


class TestAllEndpointsEnrichment:
    """Test enrichment coverage for ALL API endpoints."""

    def test_all_endpoints_get_metadata(self, description_enricher, metadata_enricher):
        """Verify every endpoint gets x-f5xc-operation-metadata after enrichment."""
        if not ALL_ENDPOINTS:
            pytest.skip("No endpoints discovered")

        if not description_enricher.enabled:
            pytest.skip("OperationDescriptionEnricher is disabled")

        # Sample endpoints for reasonable test time (test 10% or 200, whichever is smaller)
        sample_size = min(200, len(ALL_ENDPOINTS) // 10)
        import random

        random.seed(42)  # Reproducible sampling
        sampled_endpoints = random.sample(ALL_ENDPOINTS, sample_size)

        missing_metadata = []
        empty_purpose = []

        for spec_name, path, method, original_op in sampled_endpoints:
            # Create a minimal spec with this single operation
            spec = {
                "paths": {
                    path: {
                        method: original_op.copy(),
                    },
                },
            }

            # Run enrichment pipeline
            spec = description_enricher.enrich_spec(spec)
            spec = metadata_enricher.enrich_spec(spec)

            # Verify metadata was added
            operation = spec["paths"][path][method]
            if "x-f5xc-operation-metadata" not in operation:
                missing_metadata.append(f"{spec_name}:{path}:{method}")
            elif not operation["x-f5xc-operation-metadata"].get("purpose"):
                empty_purpose.append(f"{spec_name}:{path}:{method}")

        # Report failures
        assert not missing_metadata, (
            f"{len(missing_metadata)} endpoints missing x-f5xc-operation-metadata:\n"
            + "\n".join(missing_metadata[:20])
            + (f"\n... and {len(missing_metadata) - 20} more" if len(missing_metadata) > 20 else "")
        )

        assert not empty_purpose, (
            f"{len(empty_purpose)} endpoints have empty purpose:\n"
            + "\n".join(empty_purpose[:20])
            + (f"\n... and {len(empty_purpose) - 20} more" if len(empty_purpose) > 20 else "")
        )

    def test_enrichment_preserves_existing_operation_fields(
        self,
        description_enricher,
        metadata_enricher,
    ):
        """Verify enrichment doesn't remove existing operation fields."""
        if not ALL_ENDPOINTS:
            pytest.skip("No endpoints discovered")

        # Sample a few endpoints
        sample_endpoints = ALL_ENDPOINTS[:50]

        for spec_name, path, method, original_op in sample_endpoints:
            original_keys = set(original_op.keys())

            spec = {
                "paths": {
                    path: {
                        method: original_op.copy(),
                    },
                },
            }

            spec = description_enricher.enrich_spec(spec)
            spec = metadata_enricher.enrich_spec(spec)

            enriched_op = spec["paths"][path][method]
            enriched_keys = set(enriched_op.keys())

            # All original keys should still be present
            missing_keys = original_keys - enriched_keys
            assert not missing_keys, (
                f"{spec_name}:{path}:{method} lost keys after enrichment: {missing_keys}"
            )


class TestResourceTypeCoverage:
    """Test that all unique resource types get meaningful enrichment."""

    @pytest.fixture(scope="class")
    def unique_resources(self) -> dict[str, list[tuple[str, str, str]]]:
        """Get unique resource types with their endpoints."""
        resources: dict[str, list[tuple[str, str, str]]] = {}

        for spec_name, path, method, _ in ALL_ENDPOINTS:
            resource = extract_resource_type(path)
            if resource:
                if resource not in resources:
                    resources[resource] = []
                resources[resource].append((spec_name, path, method))

        return resources

    def test_resource_type_extraction_coverage(self, unique_resources):
        """Verify we can extract resource types from most endpoints."""
        if not ALL_ENDPOINTS:
            pytest.skip("No endpoints discovered")

        total_endpoints = len(ALL_ENDPOINTS)
        endpoints_with_resource = sum(len(eps) for eps in unique_resources.values())

        coverage = endpoints_with_resource / total_endpoints * 100
        assert coverage >= 80, f"Resource type extraction coverage too low: {coverage:.1f}%"

    def test_unique_resource_count(self, unique_resources):
        """Verify we discovered a reasonable number of unique resources."""
        if not unique_resources:
            pytest.skip("No resources discovered")

        assert len(unique_resources) >= 100, (
            f"Expected 100+ unique resources, found {len(unique_resources)}"
        )

    def test_all_resource_types_get_enriched(
        self,
        unique_resources,
        description_enricher,
        metadata_enricher,
    ):
        """Verify all resource types get purpose descriptions."""
        if not unique_resources:
            pytest.skip("No resources discovered")

        if not description_enricher.enabled:
            pytest.skip("OperationDescriptionEnricher is disabled")

        # Test one endpoint per resource type
        resources_without_purpose = []

        for resource_type, endpoints in list(unique_resources.items())[:100]:  # Sample 100
            spec_name, path, method = endpoints[0]

            spec = {
                "paths": {
                    path: {
                        method: {
                            "operationId": f"{method}{resource_type.title()}",
                        },
                    },
                },
            }

            spec = description_enricher.enrich_spec(spec)
            spec = metadata_enricher.enrich_spec(spec)

            operation = spec["paths"][path][method]
            metadata = operation.get("x-f5xc-operation-metadata", {})
            purpose = metadata.get("purpose", "")

            if not purpose:
                resources_without_purpose.append(resource_type)

        assert not resources_without_purpose, (
            f"{len(resources_without_purpose)} resource types have no purpose:\n"
            + "\n".join(resources_without_purpose[:30])
        )


class TestEnrichmentQuality:
    """Test the quality of enrichment across all endpoints."""

    def test_purpose_is_not_empty(self, description_enricher, metadata_enricher):
        """Verify purpose field is never empty after enrichment."""
        if not ALL_ENDPOINTS:
            pytest.skip("No endpoints discovered")

        # Test sample of endpoints
        sample_size = min(100, len(ALL_ENDPOINTS))
        import random

        random.seed(123)
        sampled = random.sample(ALL_ENDPOINTS, sample_size)

        for spec_name, path, method, original_op in sampled:
            spec = {
                "paths": {
                    path: {
                        method: original_op.copy(),
                    },
                },
            }

            spec = description_enricher.enrich_spec(spec)
            spec = metadata_enricher.enrich_spec(spec)

            operation = spec["paths"][path][method]
            metadata = operation.get("x-f5xc-operation-metadata", {})
            purpose = metadata.get("purpose", "")

            assert purpose, f"Empty purpose for {spec_name}:{path}:{method}"
            assert len(purpose) > 5, (
                f"Purpose too short for {spec_name}:{path}:{method}: '{purpose}'"
            )

    def test_purpose_length_constraints(self, description_enricher, metadata_enricher):
        """Verify purpose descriptions meet length constraints (max 60 chars for short tier)."""
        if not ALL_ENDPOINTS:
            pytest.skip("No endpoints discovered")

        if not description_enricher.enabled:
            pytest.skip("OperationDescriptionEnricher is disabled")

        # Sample endpoints
        sample_size = min(100, len(ALL_ENDPOINTS))
        import random

        random.seed(456)
        sampled = random.sample(ALL_ENDPOINTS, sample_size)

        too_long = []

        for _spec_name, path, method, original_op in sampled:
            spec = {
                "paths": {
                    path: {
                        method: original_op.copy(),
                    },
                },
            }

            spec = description_enricher.enrich_spec(spec)
            spec = metadata_enricher.enrich_spec(spec)

            operation = spec["paths"][path][method]
            metadata = operation.get("x-f5xc-operation-metadata", {})
            purpose = metadata.get("purpose", "")

            # Short tier max is 60 chars
            if len(purpose) > 60:
                too_long.append(f"{path}:{method} ({len(purpose)} chars): {purpose[:50]}...")

        # Allow some exceptions but majority should comply
        assert len(too_long) < sample_size * 0.2, (
            f"Too many purposes exceed 60 char limit ({len(too_long)}/{sample_size}):\n"
            + "\n".join(too_long[:10])
        )

    def test_danger_level_assigned(self, description_enricher, metadata_enricher):
        """Verify all endpoints get danger_level assigned."""
        if not ALL_ENDPOINTS:
            pytest.skip("No endpoints discovered")

        sample_size = min(50, len(ALL_ENDPOINTS))
        import random

        random.seed(789)
        sampled = random.sample(ALL_ENDPOINTS, sample_size)

        for spec_name, path, method, original_op in sampled:
            spec = {
                "paths": {
                    path: {
                        method: original_op.copy(),
                    },
                },
            }

            spec = description_enricher.enrich_spec(spec)
            spec = metadata_enricher.enrich_spec(spec)

            operation = spec["paths"][path][method]
            metadata = operation.get("x-f5xc-operation-metadata", {})

            assert "danger_level" in metadata, (
                f"Missing danger_level for {spec_name}:{path}:{method}"
            )
            assert metadata["danger_level"] in ["low", "medium", "high"], (
                f"Invalid danger_level for {spec_name}:{path}:{method}: {metadata['danger_level']}"
            )


class TestHTTPMethodEnrichment:
    """Test enrichment by HTTP method type."""

    @pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete"])
    def test_method_specific_enrichment(
        self,
        method,
        description_enricher,
        metadata_enricher,
    ):
        """Test that each HTTP method type gets appropriate enrichment."""
        if not ALL_ENDPOINTS:
            pytest.skip("No endpoints discovered")

        # Get endpoints for this method
        method_endpoints = [
            (spec, path, m, op) for spec, path, m, op in ALL_ENDPOINTS if m == method
        ]

        if not method_endpoints:
            pytest.skip(f"No {method.upper()} endpoints found")

        # Sample 20 endpoints of this method
        sample_size = min(20, len(method_endpoints))
        import random

        random.seed(hash(method))
        sampled = random.sample(method_endpoints, sample_size)

        for _spec_name, path, m, original_op in sampled:
            spec = {
                "paths": {
                    path: {
                        m: original_op.copy(),
                    },
                },
            }

            spec = description_enricher.enrich_spec(spec)
            spec = metadata_enricher.enrich_spec(spec)

            operation = spec["paths"][path][m]
            metadata = operation.get("x-f5xc-operation-metadata", {})

            # Verify enrichment was applied
            assert "purpose" in metadata, f"Missing purpose for {method.upper()} {path}"
            assert "danger_level" in metadata, f"Missing danger_level for {method.upper()} {path}"

            # Verify method-appropriate danger levels
            if method == "delete":
                assert metadata["danger_level"] == "high", (
                    f"DELETE should have high danger level: {path}"
                )
            elif method == "get":
                expected_danger = (
                    "medium" if operation.get("x-f5xc-operation-role") == "issuance" else "low"
                )
                assert metadata["danger_level"] == expected_danger, (
                    f"GET should have {expected_danger} danger level: {path}"
                )


class TestEnrichmentStatistics:
    """Test and report enrichment statistics across all endpoints."""

    def test_generate_coverage_report(self, description_enricher, metadata_enricher):
        """Generate a coverage report for all endpoints."""
        if not ALL_ENDPOINTS:
            pytest.skip("No endpoints discovered")

        if not description_enricher.enabled:
            pytest.skip("OperationDescriptionEnricher is disabled")

        stats = {
            "total_endpoints": len(ALL_ENDPOINTS),
            "endpoints_with_metadata": 0,
            "endpoints_with_purpose": 0,
            "explicit_matches": 0,
            "pattern_matches": 0,
            "fallback_matches": 0,
            "methods": {"get": 0, "post": 0, "put": 0, "patch": 0, "delete": 0},
        }

        # Sample for reasonable test time
        sample_size = min(200, len(ALL_ENDPOINTS))
        import random

        random.seed(999)
        sampled = random.sample(ALL_ENDPOINTS, sample_size)

        for _spec_name, path, method, original_op in sampled:
            stats["methods"][method] = stats["methods"].get(method, 0) + 1

            spec = {
                "paths": {
                    path: {
                        method: original_op.copy(),
                    },
                },
            }

            spec = description_enricher.enrich_spec(spec)
            spec = metadata_enricher.enrich_spec(spec)

            operation = spec["paths"][path][method]

            if "x-f5xc-operation-metadata" in operation:
                stats["endpoints_with_metadata"] += 1

                metadata = operation["x-f5xc-operation-metadata"]
                if metadata.get("purpose"):
                    stats["endpoints_with_purpose"] += 1

        # Calculate coverage
        metadata_coverage = stats["endpoints_with_metadata"] / sample_size * 100
        purpose_coverage = stats["endpoints_with_purpose"] / sample_size * 100

        # Assert minimum coverage thresholds
        assert metadata_coverage >= 95, f"Metadata coverage too low: {metadata_coverage:.1f}%"
        assert purpose_coverage >= 95, f"Purpose coverage too low: {purpose_coverage:.1f}%"

        # Report statistics
        print("\n=== Enrichment Coverage Report ===")
        print(f"Total endpoints in specs: {stats['total_endpoints']}")
        print(f"Sample size tested: {sample_size}")
        print(f"Metadata coverage: {metadata_coverage:.1f}%")
        print(f"Purpose coverage: {purpose_coverage:.1f}%")
        print(f"Methods distribution: {stats['methods']}")


class TestPreExistingPurposePreservation:
    """Test that pre-existing purpose fields are preserved across ALL endpoints."""

    def test_pre_existing_purpose_never_overwritten(
        self,
        description_enricher,
        metadata_enricher,
    ):
        """Verify existing purpose is preserved for any endpoint."""
        if not ALL_ENDPOINTS:
            pytest.skip("No endpoints discovered")

        # Sample endpoints and test preservation
        sample_size = min(100, len(ALL_ENDPOINTS))
        import random

        random.seed(111)
        sampled = random.sample(ALL_ENDPOINTS, sample_size)

        overwritten = []

        for _spec_name, path, method, original_op in sampled:
            pre_existing_purpose = "Custom pre-existing description for testing"

            # Create operation with pre-existing purpose
            operation_with_purpose = original_op.copy()
            operation_with_purpose["x-f5xc-operation-metadata"] = {
                "purpose": pre_existing_purpose,
            }

            spec = {
                "paths": {
                    path: {
                        method: operation_with_purpose,
                    },
                },
            }

            # Run enrichment
            spec = description_enricher.enrich_spec(spec)
            spec = metadata_enricher.enrich_spec(spec)

            # Check if purpose was preserved
            enriched_op = spec["paths"][path][method]
            enriched_purpose = enriched_op.get("x-f5xc-operation-metadata", {}).get("purpose")

            if enriched_purpose != pre_existing_purpose:
                overwritten.append(f"{path}:{method} -> '{enriched_purpose}'")

        assert not overwritten, (
            f"{len(overwritten)} endpoints had their purpose overwritten:\n"
            + "\n".join(overwritten[:20])
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
