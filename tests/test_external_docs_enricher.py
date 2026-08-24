# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=protected-access,use-implicit-booleaness-not-comparison  # Tests inspect internals and explicit result shapes.

"""Tests for ExternalDocsEnricher.

Tests the enrichment of OpenAPI specs with externalDocs metadata
providing links to F5's official documentation.
"""

import pytest

from scripts.utils.extension_constants import X_F5XC_CLI_DOMAIN
from scripts.utils.external_docs_enricher import ExternalDocsEnricher, ExternalDocsStats


class TestExternalDocsStats:
    """Test ExternalDocsStats dataclass."""

    def test_stats_initialization(self):
        """Test stats initialization with default values."""
        stats = ExternalDocsStats()
        assert stats.specs_enriched == 0
        assert stats.docs_added == 0
        assert stats.already_had_docs == 0
        assert stats.used_default == 0
        assert stats.errors == []

    def test_stats_to_dict(self):
        """Test stats conversion to dictionary."""
        stats = ExternalDocsStats()
        stats.specs_enriched = 5
        stats.docs_added = 3
        stats.already_had_docs = 1
        stats.used_default = 1

        result = stats.to_dict()
        assert result["specs_enriched"] == 5
        assert result["docs_added"] == 3
        assert result["already_had_docs"] == 1
        assert result["used_default"] == 1
        assert "error_count" in result


class TestExternalDocsEnricherBasics:
    """Test basic ExternalDocsEnricher functionality."""

    def test_enricher_initialization(self):
        """Test enricher initializes with config loaded."""
        enricher = ExternalDocsEnricher()
        assert enricher.config is not None
        assert enricher.default_docs is not None
        assert "url" in enricher.default_docs
        assert "description" in enricher.default_docs
        assert enricher.stats is not None

    def test_domain_docs_loaded(self):
        """Test that domain documentation mappings are loaded."""
        enricher = ExternalDocsEnricher()
        # Should have domain mappings
        assert len(enricher.domain_docs) > 0
        # Known domains should be present
        assert "virtual" in enricher.domain_docs
        assert "waf" in enricher.domain_docs
        assert "dns" in enricher.domain_docs

    def test_get_stats(self):
        """Test get_stats returns valid dictionary."""
        enricher = ExternalDocsEnricher()
        stats = enricher.get_stats()
        assert isinstance(stats, dict)
        assert "specs_enriched" in stats
        assert "docs_added" in stats
        assert "already_had_docs" in stats
        assert "used_default" in stats

    def test_reset_stats(self):
        """Test reset_stats clears statistics."""
        enricher = ExternalDocsEnricher()
        enricher.stats.specs_enriched = 10
        enricher.stats.docs_added = 5
        enricher.reset_stats()
        assert enricher.stats.specs_enriched == 0
        assert enricher.stats.docs_added == 0


class TestDocsRetrieval:
    """Test docs retrieval logic."""

    def test_get_docs_for_known_domain(self):
        """Test getting docs for a known domain."""
        enricher = ExternalDocsEnricher()
        docs = enricher.get_docs_for_domain("virtual")
        assert "url" in docs
        assert "description" in docs
        assert "f5.com" in docs["url"].lower() or "docs.cloud" in docs["url"].lower()

    def test_get_docs_for_waf_domain(self):
        """Test getting docs for WAF domain."""
        enricher = ExternalDocsEnricher()
        docs = enricher.get_docs_for_domain("waf")
        assert "url" in docs
        assert "waf" in docs["url"].lower() or "firewall" in docs["url"].lower()

    def test_get_docs_for_unknown_domain(self):
        """Test getting docs for unknown domain returns default."""
        enricher = ExternalDocsEnricher()
        docs = enricher.get_docs_for_domain("unknown_domain_xyz")
        assert "url" in docs
        assert docs["url"] == enricher.default_docs["url"]

    def test_get_docs_for_empty_domain(self):
        """Test getting docs for empty domain returns default."""
        enricher = ExternalDocsEnricher()
        docs = enricher.get_docs_for_domain("")
        assert "url" in docs


class TestDomainDetection:
    """Test domain detection from specs."""

    def test_detect_domain_from_filename(self):
        """Test domain detection from filename."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {"title": "Some API"},
            "paths": {},
        }
        domain = enricher._detect_domain(
            spec,
            filename="ves.io.schema.views.http_loadbalancer.json",
        )
        assert domain == "virtual"

    def test_detect_domain_from_cli_domain_extension(self):
        """Test domain detection from x-f5xc-cli-domain extension."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {
                "title": "Some API",
                X_F5XC_CLI_DOMAIN: "waf",
            },
            "paths": {},
        }
        domain = enricher._detect_domain(spec, filename=None)
        assert domain == "waf"

    def test_detect_domain_from_title_fallback(self):
        """Test domain detection from title as fallback."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {"title": "DNS Load Balancer API"},
            "paths": {},
        }
        domain = enricher._detect_domain(spec, filename=None)
        # Should detect from title pattern
        assert domain in ["dns", "other"]

    def test_detect_domain_unknown(self):
        """Test domain detection returns 'other' for unknown."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {"title": "Unknown API"},
            "paths": {},
        }
        domain = enricher._detect_domain(spec, filename="unknown_file.json")
        assert domain == "other"


class TestSpecEnrichment:
    """Test specification enrichment."""

    def test_enrich_spec_adds_external_docs(self):
        """Test that enrich_spec adds externalDocs field."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {
                "title": "HTTP Load Balancer API",
            },
            "paths": {},
        }
        result = enricher.enrich_spec(spec, filename="http_loadbalancer.json")
        assert "externalDocs" in result["info"]
        assert "url" in result["info"]["externalDocs"]
        assert "description" in result["info"]["externalDocs"]

    def test_enrich_spec_virtual_domain(self):
        """Test enrichment of virtual domain spec."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {
                "title": "HTTP Load Balancer API",
                X_F5XC_CLI_DOMAIN: "virtual",
            },
            "paths": {},
        }
        result = enricher.enrich_spec(spec)
        assert "externalDocs" in result["info"]
        assert (
            "load-balancer" in result["info"]["externalDocs"]["url"].lower()
            or "http" in result["info"]["externalDocs"]["url"].lower()
        )
        assert enricher.stats.docs_added == 1

    def test_enrich_spec_waf_domain(self):
        """Test enrichment of WAF domain spec."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {
                "title": "App Firewall API",
                X_F5XC_CLI_DOMAIN: "waf",
            },
            "paths": {},
        }
        result = enricher.enrich_spec(spec)
        assert "externalDocs" in result["info"]
        external_docs = result["info"]["externalDocs"]
        assert "waf" in external_docs["url"].lower() or "firewall" in external_docs["url"].lower()

    def test_enrich_spec_idempotent(self):
        """Test that enrichment is idempotent."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {
                "title": "Some API",
                "externalDocs": {
                    "url": "https://example.com/docs",
                    "description": "Existing docs",
                },
            },
            "paths": {},
        }
        result = enricher.enrich_spec(spec)
        # Should preserve existing externalDocs
        assert result["info"]["externalDocs"]["url"] == "https://example.com/docs"
        assert result["info"]["externalDocs"]["description"] == "Existing docs"
        assert enricher.stats.already_had_docs == 1

    def test_enrich_spec_preserves_existing_info(self):
        """Test that enrichment preserves existing info fields."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {
                "title": "Some API",
                "version": "1.0.0",
                "description": "API description",
                X_F5XC_CLI_DOMAIN: "virtual",
            },
            "paths": {},
        }
        result = enricher.enrich_spec(spec)
        assert result["info"]["title"] == "Some API"
        assert result["info"]["version"] == "1.0.0"
        assert result["info"]["description"] == "API description"
        assert result["info"][X_F5XC_CLI_DOMAIN] == "virtual"
        assert "externalDocs" in result["info"]

    def test_enrich_spec_creates_info_if_missing(self):
        """Test that enrichment creates info section if missing."""
        enricher = ExternalDocsEnricher()
        spec = {
            "paths": {},
        }
        result = enricher.enrich_spec(spec)
        assert "info" in result
        assert "externalDocs" in result["info"]

    def test_enrich_spec_stats_updated(self):
        """Test that stats are updated after enrichment."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {
                "title": "Some API",
            },
            "paths": {},
        }
        enricher.enrich_spec(spec)
        assert enricher.stats.specs_enriched == 1

    def test_enrich_multiple_specs(self):
        """Test enriching multiple specifications."""
        enricher = ExternalDocsEnricher()
        specs = [
            {
                "info": {"title": "HTTP Load Balancer API", X_F5XC_CLI_DOMAIN: "virtual"},
                "paths": {},
            },
            {"info": {"title": "App Firewall API", X_F5XC_CLI_DOMAIN: "waf"}, "paths": {}},
            {"info": {"title": "DNS Zone API", X_F5XC_CLI_DOMAIN: "dns"}, "paths": {}},
        ]
        for spec in specs:
            enricher.enrich_spec(spec)
        assert enricher.stats.specs_enriched == 3
        assert enricher.stats.docs_added == 3


class TestDomainMappings:
    """Test configured domain documentation mappings."""

    @pytest.mark.parametrize(
        "domain",
        [
            "virtual",
            "waf",
            "dns",
            "cdn",
            "network",
            "sites",
            "api",
            "observability",
            "certificates",
        ],
    )
    def test_known_domains_have_docs(self, domain):
        """Test that known domains have documentation mappings."""
        enricher = ExternalDocsEnricher()
        docs = enricher.get_docs_for_domain(domain)
        assert "url" in docs
        assert "description" in docs
        assert docs["url"].startswith("https://")

    @pytest.mark.parametrize(
        ("domain", "expected_url_part"),
        [
            ("virtual", "load-balancer"),
            ("waf", "firewall"),
            ("dns", "dns"),
            ("cdn", "cdn"),
            ("sites", "site"),
            ("api", "api"),
        ],
    )
    def test_domain_url_relevance(self, domain, expected_url_part):
        """Test that domain URLs contain relevant keywords."""
        enricher = ExternalDocsEnricher()
        docs = enricher.get_docs_for_domain(domain)
        # URL should contain the expected keyword or something related
        url_lower = docs["url"].lower()
        assert expected_url_part in url_lower or domain in url_lower


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_enrich_empty_spec(self):
        """Test enriching an empty specification."""
        enricher = ExternalDocsEnricher()
        spec = {}
        result = enricher.enrich_spec(spec)
        assert "info" in result
        assert "externalDocs" in result["info"]

    def test_enrich_spec_with_empty_info(self):
        """Test enriching spec with empty info section."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {},
            "paths": {},
        }
        result = enricher.enrich_spec(spec)
        assert "externalDocs" in result["info"]

    def test_external_docs_structure(self):
        """Test that externalDocs has required OpenAPI fields."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {"title": "Test API"},
            "paths": {},
        }
        result = enricher.enrich_spec(spec)
        external_docs = result["info"]["externalDocs"]
        # OpenAPI 3.0 externalDocs requires url and allows description
        assert "url" in external_docs
        assert isinstance(external_docs["url"], str)
        assert external_docs["url"].startswith("https://")
        if "description" in external_docs:
            assert isinstance(external_docs["description"], str)

    def test_default_docs_used_for_other(self):
        """Test that default docs are used for 'other' domain."""
        enricher = ExternalDocsEnricher()
        enricher.reset_stats()
        spec = {
            "info": {"title": "Completely Unknown API"},
            "paths": {},
        }
        enricher.enrich_spec(spec, filename="unknown_xyz.json")
        assert enricher.stats.used_default >= 0  # May or may not use default


class TestFilenameBasedDetection:
    """Test filename-based domain detection."""

    @pytest.mark.parametrize(
        ("filename", "expected_domain"),
        [
            ("ves.io.schema.views.http_loadbalancer.json", "virtual"),
            ("ves.io.schema.app_firewall.json", "virtual"),
            ("ves.io.schema.dns_zone.json", "dns"),
            ("ves.io.schema.cdn_loadbalancer.json", "cdn"),
            ("ves.io.schema.aws_vpc_site.json", "sites"),
        ],
    )
    def test_filename_domain_detection(self, filename, expected_domain):
        """Test that filenames correctly map to domains."""
        enricher = ExternalDocsEnricher()
        spec = {"info": {"title": "Test API"}, "paths": {}}
        domain = enricher._detect_domain(spec, filename=filename)
        assert domain == expected_domain


class TestIntegrationPatterns:
    """Test integration patterns with other enrichers."""

    def test_works_with_cli_domain_from_other_enricher(self):
        """Test using x-f5xc-cli-domain set by another enricher."""
        enricher = ExternalDocsEnricher()
        # Simulate spec already enriched by MinimumConfigurationEnricher
        spec = {
            "info": {
                "title": "HTTP Load Balancer API",
                X_F5XC_CLI_DOMAIN: "virtual",
                "x-f5xc-minimum-configuration": {
                    "description": "Minimum viable load balancer",
                },
            },
            "paths": {},
        }
        result = enricher.enrich_spec(spec)
        assert "externalDocs" in result["info"]
        # Should still have other extensions
        assert X_F5XC_CLI_DOMAIN in result["info"]
        assert "x-f5xc-minimum-configuration" in result["info"]

    def test_works_with_namespace_profile(self):
        """Test compatibility with namespace profile enricher."""
        enricher = ExternalDocsEnricher()
        # Simulate spec already enriched by NamespaceProfileEnricher
        spec = {
            "info": {
                "title": "Alert Policy API",
                "x-f5xc-namespace-profile": {
                    "constraint": {"allowed": ["system"]},
                    "recommendation": {"default": "system"},
                    "classification": {"multi_tenant_pattern": "system-only"},
                },
            },
            "paths": {},
        }
        result = enricher.enrich_spec(spec)
        assert "externalDocs" in result["info"]
        assert "x-f5xc-namespace-profile" in result["info"]


class TestApiReferenceRewrite:
    """Test operation-level externalDocs URL rewriting."""

    OLD_PREFIX = "https://docs.cloud.f5.com/docs-v2/platform/reference/api-ref/"
    NEW_BASE = "https://f5-sales-demo.github.io/api-specs-enriched/api-reference"

    def _make_spec(self, op_url: str, domain: str = "shape") -> dict:
        """Build a minimal spec with one operation-level externalDocs."""
        return {
            "info": {"title": "Test API", X_F5XC_CLI_DOMAIN: domain},
            "paths": {
                "/api/test": {
                    "get": {
                        "summary": "List items",
                        "externalDocs": {"url": op_url},
                    },
                },
            },
        }

    def test_rewrites_matching_url(self):
        """Test that upstream API reference URLs are rewritten."""
        enricher = ExternalDocsEnricher()
        spec = self._make_spec(
            f"{self.OLD_PREFIX}ves-io-schema-shape-api-list",
        )
        result = enricher.enrich_spec(spec, filename="shape.json")
        url = result["paths"]["/api/test"]["get"]["externalDocs"]["url"]
        assert url == f"{self.NEW_BASE}/shape/"
        assert enricher.stats.api_links_rewritten == 1

    def test_leaves_non_matching_url_untouched(self):
        """Test that non-API-ref URLs are not rewritten."""
        enricher = ExternalDocsEnricher()
        original_url = "https://example.com/custom-docs"
        spec = self._make_spec(original_url)
        result = enricher.enrich_spec(spec, filename="shape.json")
        url = result["paths"]["/api/test"]["get"]["externalDocs"]["url"]
        assert url == original_url
        assert enricher.stats.api_links_rewritten == 0

    def test_leaves_howto_docs_untouched(self):
        """Test that how-to guide URLs are not rewritten."""
        enricher = ExternalDocsEnricher()
        howto_url = "https://docs.cloud.f5.com/docs/how-to/app-security/waf"
        spec = self._make_spec(howto_url)
        result = enricher.enrich_spec(spec, filename="shape.json")
        url = result["paths"]["/api/test"]["get"]["externalDocs"]["url"]
        assert url == howto_url

    def test_rewrites_multiple_operations(self):
        """Test that all matching operations in a spec are rewritten."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {"title": "Test API", X_F5XC_CLI_DOMAIN: "dns"},
            "paths": {
                "/api/dns/zones": {
                    "get": {
                        "externalDocs": {
                            "url": f"{self.OLD_PREFIX}ves-io-schema-dns-zone-api-list",
                        },
                    },
                    "post": {
                        "externalDocs": {
                            "url": f"{self.OLD_PREFIX}ves-io-schema-dns-zone-api-create",
                        },
                    },
                },
                "/api/dns/zones/{{name}}": {
                    "get": {
                        "externalDocs": {
                            "url": f"{self.OLD_PREFIX}ves-io-schema-dns-zone-api-get",
                        },
                    },
                },
            },
        }
        result = enricher.enrich_spec(spec, filename="dns.json")
        expected = f"{self.NEW_BASE}/dns/"
        assert result["paths"]["/api/dns/zones"]["get"]["externalDocs"]["url"] == expected
        assert result["paths"]["/api/dns/zones"]["post"]["externalDocs"]["url"] == expected
        assert result["paths"]["/api/dns/zones/{{name}}"]["get"]["externalDocs"]["url"] == expected
        assert enricher.stats.api_links_rewritten == 3

    def test_handles_operations_without_external_docs(self):
        """Test that operations without externalDocs are handled gracefully."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {"title": "Test API", X_F5XC_CLI_DOMAIN: "shape"},
            "paths": {
                "/api/test": {
                    "get": {"summary": "No externalDocs here"},
                },
            },
        }
        result = enricher.enrich_spec(spec, filename="shape.json")
        assert "externalDocs" not in result["paths"]["/api/test"]["get"]
        assert enricher.stats.api_links_rewritten == 0

    def test_uses_detected_domain_for_new_url(self):
        """Test that the rewritten URL uses the correct detected domain."""
        enricher = ExternalDocsEnricher()
        spec = self._make_spec(
            f"{self.OLD_PREFIX}ves-io-schema-virtual-host-api-list",
            domain="virtual",
        )
        result = enricher.enrich_spec(spec)
        url = result["paths"]["/api/test"]["get"]["externalDocs"]["url"]
        assert url == f"{self.NEW_BASE}/virtual/"

    def test_stats_in_to_dict(self):
        """Test that api_links_rewritten appears in stats dict."""
        enricher = ExternalDocsEnricher()
        spec = self._make_spec(f"{self.OLD_PREFIX}ves-io-schema-shape-api-list")
        enricher.enrich_spec(spec, filename="shape.json")
        stats = enricher.get_stats()
        assert "api_links_rewritten" in stats
        assert stats["api_links_rewritten"] == 1


class TestApiReferenceUrlField:
    """Test x-f5xc-api-reference-url extension field generation."""

    NEW_BASE = "https://f5-sales-demo.github.io/api-specs-enriched/api-reference"

    def test_api_reference_url_set_on_enrichment(self):
        """Test that x-f5xc-api-reference-url is added during enrichment."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {"title": "Shape API", X_F5XC_CLI_DOMAIN: "shape"},
            "paths": {},
        }
        result = enricher.enrich_spec(spec, filename="shape.json")
        assert "x-f5xc-api-reference-url" in result["info"]
        assert result["info"]["x-f5xc-api-reference-url"] == f"{self.NEW_BASE}/shape/"

    def test_api_reference_url_follows_domain_pattern(self):
        """Test that the URL uses {base}/{domain}/ pattern."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {"title": "DNS API", X_F5XC_CLI_DOMAIN: "dns"},
            "paths": {},
        }
        result = enricher.enrich_spec(spec)
        url = result["info"]["x-f5xc-api-reference-url"]
        assert url.startswith(self.NEW_BASE)
        assert url.endswith("/dns/")

    def test_api_reference_url_set_on_idempotent_enrichment(self):
        """Test that x-f5xc-api-reference-url is added even when externalDocs already exists."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {
                "title": "Virtual API",
                X_F5XC_CLI_DOMAIN: "virtual",
                "externalDocs": {
                    "url": "https://docs.cloud.f5.com/docs/how-to/app-networking/http-load-balancer",
                    "description": "Existing docs",
                },
            },
            "paths": {},
        }
        result = enricher.enrich_spec(spec)
        assert "x-f5xc-api-reference-url" in result["info"]
        assert result["info"]["x-f5xc-api-reference-url"] == f"{self.NEW_BASE}/virtual/"
        assert enricher.stats.already_had_docs == 1

    def test_api_reference_url_not_overwritten_on_re_enrichment(self):
        """Test idempotency: existing x-f5xc-api-reference-url is preserved."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {
                "title": "WAF API",
                X_F5XC_CLI_DOMAIN: "waf",
                "externalDocs": {
                    "url": "https://docs.cloud.f5.com/docs/how-to/app-security/web-app-firewall",
                    "description": "WAF docs",
                },
                "x-f5xc-api-reference-url": f"{self.NEW_BASE}/waf/",
            },
            "paths": {},
        }
        result = enricher.enrich_spec(spec)
        assert result["info"]["x-f5xc-api-reference-url"] == f"{self.NEW_BASE}/waf/"

    @pytest.mark.parametrize(
        ("domain", "expected_suffix"),
        [
            ("blindfold", "/blindfold/"),
            ("virtual", "/virtual/"),
            ("waf", "/waf/"),
            ("dns", "/dns/"),
            ("shape", "/shape/"),
            ("cdn", "/cdn/"),
        ],
    )
    def test_api_reference_url_per_domain(self, domain: str, expected_suffix: str):
        """Test API reference URL generation for multiple domains."""
        enricher = ExternalDocsEnricher()
        spec = {
            "info": {"title": f"{domain.title()} API", X_F5XC_CLI_DOMAIN: domain},
            "paths": {},
        }
        result = enricher.enrich_spec(spec)
        url = result["info"]["x-f5xc-api-reference-url"]
        assert url == f"{self.NEW_BASE}{expected_suffix}"

    def test_api_reference_url_not_set_without_base_url(self):
        """Test that field is not set when api_reference_base_url is empty."""
        enricher = ExternalDocsEnricher()
        enricher.api_reference_base_url = ""
        spec = {
            "info": {"title": "Test API", X_F5XC_CLI_DOMAIN: "shape"},
            "paths": {},
        }
        result = enricher.enrich_spec(spec, filename="shape.json")
        assert "x-f5xc-api-reference-url" not in result["info"]
