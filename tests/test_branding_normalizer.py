# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=use-implicit-booleaness-not-comparison  # Explicit empty results document the expected shape.

"""Unit tests for BrandingNormalizer — current API name transformations.

Tests the BrandingNormalizer class for transforming legacy Volterra-era
terminology to current F5 Distributed Cloud API product names:
- AppStack/VoltStack → Managed Kubernetes
- vK8s → Virtual Kubernetes
"""

from scripts.utils.branding import BrandingNormalizer, BrandingStats


class TestBrandingStats:
    """Test BrandingStats dataclass."""

    def test_default_values(self) -> None:
        stats = BrandingStats()
        assert stats.legacy_terms_replaced == 0
        assert stats.managed_k8s_transformations == 0
        assert stats.virtual_k8s_transformations == 0
        assert stats.glossary_terms_added == 0
        assert stats.files_processed == 0
        assert stats.transformations_by_type == {}

    def test_to_dict(self) -> None:
        stats = BrandingStats(
            managed_k8s_transformations=5,
            virtual_k8s_transformations=3,
            files_processed=10,
        )
        result = stats.to_dict()
        assert isinstance(result, dict)
        assert result["managed_k8s_transformations"] == 5
        assert result["virtual_k8s_transformations"] == 3
        assert result["files_processed"] == 10


class TestBrandingNormalizerInitialization:
    def test_default_initialization(self) -> None:
        normalizer = BrandingNormalizer()
        assert normalizer.canonical is not None
        assert "managed_kubernetes" in normalizer.canonical

    def test_managed_kubernetes_canonical_config(self) -> None:
        normalizer = BrandingNormalizer()
        mk = normalizer.canonical.get("managed_kubernetes")
        assert mk is not None
        assert "Managed Kubernetes" in mk["long_form"]
        assert "AppStack" in mk.get("legacy_names", [])
        assert "AWS EKS" in mk.get("comparable_to", [])

    def test_virtual_kubernetes_canonical_config(self) -> None:
        normalizer = BrandingNormalizer()
        vk = normalizer.canonical.get("virtual_kubernetes")
        assert vk is not None
        assert "Virtual Kubernetes" in vk["long_form"]
        assert "vK8s" in vk.get("legacy_names", [])

    def test_no_xcks_xccs_references(self) -> None:
        normalizer = BrandingNormalizer()
        for value in normalizer.canonical.values():
            assert "XCKS" not in str(value)
            assert "XCCS" not in str(value)

    def test_glossary_loaded(self) -> None:
        normalizer = BrandingNormalizer()
        assert "CE" in normalizer.glossary
        assert "RE" in normalizer.glossary
        assert "XCKS" not in normalizer.glossary
        assert "XCCS" not in normalizer.glossary


class TestVirtualKubernetesTransformations:
    def test_vk8s_to_virtual_kubernetes(self) -> None:
        normalizer = BrandingNormalizer()
        result = normalizer.normalize_text(
            "Configure vK8s for multi-tenant deployments.", field_context="info.description"
        )
        assert "vK8s" not in result
        assert "Virtual Kubernetes" in result

    def test_virtual_k8s_stats_tracking(self) -> None:
        normalizer = BrandingNormalizer()
        normalizer.reset_stats()
        normalizer.normalize_text("Deploy vK8s workloads", field_context="info.description")
        stats = normalizer.get_stats()
        assert stats["virtual_k8s_transformations"] >= 1


class TestManagedKubernetesTransformations:
    def test_appstack_to_managed_kubernetes(self) -> None:
        normalizer = BrandingNormalizer()
        result = normalizer.normalize_text(
            "Deploy AppStack for enterprise Kubernetes.", field_context="info.description"
        )
        assert "AppStack" not in result
        assert "Managed Kubernetes" in result

    def test_voltstack_to_managed_kubernetes(self) -> None:
        normalizer = BrandingNormalizer()
        result = normalizer.normalize_text(
            "Configure VoltStack site for on-premises deployment.", field_context="info.description"
        )
        assert "VoltStack" not in result
        assert "Managed Kubernetes" in result

    def test_managed_k8s_stats_tracking(self) -> None:
        normalizer = BrandingNormalizer()
        normalizer.reset_stats()
        normalizer.normalize_text("Deploy AppStack clusters", field_context="info.description")
        stats = normalizer.get_stats()
        assert stats["managed_k8s_transformations"] >= 1


class TestSpecNormalization:
    def test_normalize_spec_description(self) -> None:
        normalizer = BrandingNormalizer()
        spec = {"info": {"title": "Test API", "description": "Manage vK8s namespaces"}}
        result = normalizer.normalize_spec(spec)
        assert "vK8s" not in result["info"]["description"]
        assert "Virtual Kubernetes" in result["info"]["description"]

    def test_normalize_spec_nested_schemas(self) -> None:
        normalizer = BrandingNormalizer()
        spec = {
            "info": {"title": "Test API"},
            "components": {
                "schemas": {"WorkloadSpec": {"description": "AppStack workload specification"}}
            },
        }
        result = normalizer.normalize_spec(spec)
        assert "AppStack" not in result["components"]["schemas"]["WorkloadSpec"]["description"]
        assert (
            "Managed Kubernetes" in result["components"]["schemas"]["WorkloadSpec"]["description"]
        )

    def test_files_processed_stat(self) -> None:
        normalizer = BrandingNormalizer()
        normalizer.reset_stats()
        spec = {"info": {"title": "Test"}}
        normalizer.normalize_spec(spec)
        normalizer.normalize_spec(spec)
        normalizer.normalize_spec(spec)
        stats = normalizer.get_stats()
        assert stats["files_processed"] == 3


class TestGlossaryIntegration:
    def test_glossary_added_to_info(self) -> None:
        normalizer = BrandingNormalizer()
        spec = {"info": {"title": "Test API", "description": "Test"}}
        result = normalizer.normalize_spec(spec)
        assert "x-f5xc-glossary" in result["info"]
        glossary = result["info"]["x-f5xc-glossary"]
        assert "CE" in glossary
        assert "RE" in glossary
        assert "XCKS" not in glossary
        assert "XCCS" not in glossary

    def test_existing_glossary_preserved(self) -> None:
        normalizer = BrandingNormalizer()
        spec = {
            "info": {
                "title": "Test API",
                "x-f5xc-glossary": {"CUSTOM_TERM": {"term": "Custom", "definition": "Custom def"}},
            }
        }  # gitleaks:allow
        result = normalizer.normalize_spec(spec)
        glossary = result["info"]["x-f5xc-glossary"]
        assert "CUSTOM_TERM" in glossary
        assert glossary["CUSTOM_TERM"]["definition"] == "Custom def"


class TestCanonicalNaming:
    def test_get_canonical_name_managed_kubernetes(self) -> None:
        normalizer = BrandingNormalizer()
        canonical = normalizer.get_canonical_name("managed_kubernetes")
        assert canonical is not None
        assert "Managed Kubernetes" in canonical["long_form"]

    def test_get_canonical_name_virtual_kubernetes(self) -> None:
        normalizer = BrandingNormalizer()
        canonical = normalizer.get_canonical_name("virtual_kubernetes")
        assert canonical is not None
        assert "Virtual Kubernetes" in canonical["long_form"]

    def test_get_canonical_name_unknown(self) -> None:
        normalizer = BrandingNormalizer()
        assert normalizer.get_canonical_name("unknown_domain") is None


class TestStatsReset:
    def test_reset_stats(self) -> None:
        normalizer = BrandingNormalizer()
        normalizer.normalize_text("Deploy AppStack", field_context="info.description")
        normalizer.normalize_text("Deploy vK8s", field_context="info.description")
        normalizer.reset_stats()
        stats = normalizer.get_stats()
        assert stats["managed_k8s_transformations"] == 0
        assert stats["virtual_k8s_transformations"] == 0
        assert stats["files_processed"] == 0


class TestEdgeCases:
    def test_empty_text(self) -> None:
        assert BrandingNormalizer().normalize_text("") == ""

    def test_none_text(self) -> None:
        assert BrandingNormalizer().normalize_text(None) is None  # type: ignore[arg-type]

    def test_text_without_legacy_terms(self) -> None:
        text = "This is a normal description without legacy terms."
        assert BrandingNormalizer().normalize_text(text) == text

    def test_empty_spec(self) -> None:
        assert BrandingNormalizer().normalize_spec({}) == {}

    def test_spec_without_info(self) -> None:
        spec = {"paths": {"/test": {"get": {"summary": "Test endpoint"}}}}
        result = BrandingNormalizer().normalize_spec(spec)
        assert "paths" in result


class TestContextFiltering:
    def test_context_matches(self) -> None:
        result = BrandingNormalizer().normalize_text(
            "Deploy vK8s", field_context="info.description"
        )
        assert "vK8s" not in result

    def test_no_context_provided(self) -> None:
        text = "Deploy vK8s"
        result = BrandingNormalizer().normalize_text(text, field_context="")
        assert text == result or "Virtual Kubernetes" in result


class TestNoXcksXccsInOutput:
    def test_appstack_does_not_produce_xcks(self) -> None:
        result = BrandingNormalizer().normalize_text(
            "Deploy AppStack", field_context="info.description"
        )
        assert "XCKS" not in result

    def test_vk8s_does_not_produce_xccs(self) -> None:
        result = BrandingNormalizer().normalize_text(
            "Configure vK8s", field_context="info.description"
        )
        assert "XCCS" not in result

    def test_voltstack_does_not_produce_xcks(self) -> None:
        result = BrandingNormalizer().normalize_text(
            "VoltStack site", field_context="info.description"
        )
        assert "XCKS" not in result
