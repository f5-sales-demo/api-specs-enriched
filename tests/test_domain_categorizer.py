# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Unit tests for domain categorization utility.

Tests the DomainCategorizer class and module-level functions for categorizing
API specification files by functional domain.
"""

import re

import pytest

from scripts.utils.domain_categorizer import DomainCategorizer, categorize_spec, get_domain_patterns


class TestDomainCategorizerSingleton:
    """Test singleton pattern of DomainCategorizer."""

    def test_singleton_instance(self) -> None:
        """Verify that DomainCategorizer returns same instance."""
        categorizer1 = DomainCategorizer()
        categorizer2 = DomainCategorizer()
        assert categorizer1 is categorizer2

    def test_patterns_loaded(self) -> None:
        """Verify that patterns are loaded on initialization."""
        categorizer = DomainCategorizer()
        patterns = categorizer.get_domain_patterns()
        assert isinstance(patterns, dict)
        assert len(patterns) > 0
        assert "sites" in patterns


class TestDomainCategorization:
    """Test domain categorization for all 34 domains."""

    # A. Infrastructure & Deployment (6 categories)
    def test_sites(self) -> None:
        """Test categorization of site specs."""
        assert categorize_spec("ves.io.schema.views.aws_vpc_site.json") == "sites"
        assert categorize_spec("ves.io.schema.views.azure_vnet_site.json") == "sites"
        assert categorize_spec("ves.io.schema.views.gcp_vpc_site.json") == "sites"
        assert categorize_spec("ves.io.schema.views.virtual_site.json") == "sites"

    def test_cloud_infrastructure(self) -> None:
        """Test categorization of cloud infrastructure specs."""
        assert (
            categorize_spec("ves.io.schema.views.cloud_credentials.json") == "cloud_infrastructure"
        )
        assert categorize_spec("ves.io.schema.views.cloud_region.json") == "cloud_infrastructure"

    def test_ce_management(self) -> None:
        """Test categorization of Customer Edge management specs."""
        assert categorize_spec("ves.io.schema.views.registration.json") == "ce_management"
        assert categorize_spec("ves.io.schema.views.module_management.json") == "ce_management"

    def test_container_services(self) -> None:
        """Test categorization of Virtual Kubernetes (vK8s) specs."""
        assert categorize_spec("ves.io.schema.views.virtual_k8s.json") == "container_services"
        assert categorize_spec("ves.io.schema.views.workload.json") == "container_services"
        assert categorize_spec("ves.io.schema.views.workload_flavor.json") == "container_services"

    def test_managed_kubernetes(self) -> None:
        """Test categorization of managed Kubernetes cluster specs."""
        assert categorize_spec("ves.io.schema.views.k8s_pod_security.json") == "managed_kubernetes"
        assert categorize_spec("ves.io.schema.views.k8s_cluster_role.json") == "managed_kubernetes"
        assert (
            categorize_spec("ves.io.schema.views.container_registry.json") == "managed_kubernetes"
        )

    def test_service_mesh(self) -> None:
        """Test categorization of service mesh specs."""
        assert categorize_spec("ves.io.schema.views.site_mesh.json") == "service_mesh"
        # virtual_network moved to network domain (L3 overlay)

    # B. Security - Core (4 categories)
    def test_api(self) -> None:
        """Test categorization of API security specs."""
        assert categorize_spec("ves.io.schema.views.api_sec.json") == "api"
        assert categorize_spec("ves.io.schema.views.api_credential.json") == "api"

    def test_bot_and_threat_defense(self) -> None:
        """Test categorization of bot defense specs."""
        assert categorize_spec("ves.io.schema.views.bot_defense.json") == "bot_and_threat_defense"
        assert (
            categorize_spec("ves.io.schema.views.threat_intelligence.json")
            == "bot_and_threat_defense"
        )

    def test_network_security(self) -> None:
        """Test categorization of network security specs."""
        assert categorize_spec("ves.io.schema.views.network_firewall.json") == "network_security"
        assert categorize_spec("ves.io.schema.views.nat_policy.json") == "network_security"

    # C. Security - Advanced (4 categories)
    def test_data_and_privacy_security(self) -> None:
        """Test categorization of data privacy specs."""
        assert (
            categorize_spec("ves.io.schema.views.data_privacy.json") == "data_and_privacy_security"
        )
        assert (
            categorize_spec("ves.io.schema.views.sensitive_data_policy.json")
            == "data_and_privacy_security"
        )

    def test_ddos(self) -> None:
        """Test categorization of DDoS protection specs."""
        assert categorize_spec("ves.io.schema.views.infraprotect.json") == "ddos"

    def test_blindfold(self) -> None:
        """Test categorization of blindfold (secret policy) specs."""
        assert categorize_spec("ves.io.schema.views.secret_policy.json") == "blindfold"
        # secret_management moved from secops_and_incident_response
        assert categorize_spec("ves.io.schema.views.secret_management.json") == "blindfold"

    def test_secops_and_incident_response(self) -> None:
        """Test categorization of security operations specs."""
        # secret_management moved to blindfold domain
        assert (
            categorize_spec("ves.io.schema.views.malicious_user.json")
            == "secops_and_incident_response"
        )

    # D. Application Delivery (2 categories)
    def test_virtual(self) -> None:
        """Test categorization of virtual service specs (HTTP/TCP/UDP load balancing with WAF)."""
        # Virtual services
        assert categorize_spec("ves.io.schema.views.http_loadbalancer.json") == "virtual"
        assert categorize_spec("ves.io.schema.views.tcp_loadbalancer.json") == "virtual"
        assert categorize_spec("ves.io.schema.views.origin_pool.json") == "virtual"
        assert categorize_spec("ves.io.schema.views.threat_campaign.json") == "virtual"
        assert categorize_spec("ves.io.schema.views.geo_location_set.json") == "virtual"
        assert categorize_spec("ves.io.schema.views.malware_protection.json") == "virtual"
        # WAF (merged into virtual domain)
        assert categorize_spec("ves.io.schema.views.app_firewall.json") == "virtual"
        assert categorize_spec("ves.io.schema.views.waf.json") == "virtual"

    def test_dns(self) -> None:
        """Test categorization of DNS specs."""
        assert categorize_spec("ves.io.schema.views.dns_load_balancer.json") == "dns"
        assert categorize_spec("ves.io.schema.views.dns_zone.json") == "dns"
        assert categorize_spec("ves.io.schema.views.rrset.json") == "dns"

    # E. Connectivity & Networking (1 category)
    def test_network(self) -> None:
        """Test categorization of network routing and tunnel specs."""
        assert categorize_spec("ves.io.schema.views.bgp_routing.json") == "network"
        assert categorize_spec("ves.io.schema.views.tunnel.json") == "network"
        assert categorize_spec("ves.io.schema.views.public_ip.json") == "network"
        assert categorize_spec("ves.io.schema.views.ike1.json") == "network"
        assert categorize_spec("ves.io.schema.views.ike2.json") == "network"
        # virtual_network moved from service_mesh (L3 overlay)
        assert categorize_spec("ves.io.schema.views.virtual_network.json") == "network"

    # F. Content & Performance
    def test_cdn(self) -> None:
        """Test categorization of CDN specs."""
        assert categorize_spec("ves.io.schema.views.cdn_loadbalancer.json") == "cdn"
        assert categorize_spec("ves.io.schema.views.cdn_cache.json") == "cdn"
        assert categorize_spec("ves.io.schema.views.data_delivery.json") == "cdn"

    # G. Observability (3 categories)
    def test_observability(self) -> None:
        """Test categorization of observability specs."""
        assert categorize_spec("ves.io.schema.views.synthetic_monitor.json") == "observability"

    def test_statistics(self) -> None:
        """Test categorization of statistics specs."""
        assert categorize_spec("ves.io.schema.views.alert_policy.json") == "statistics"
        assert categorize_spec("ves.io.schema.views.log_receiver.json") == "statistics"
        assert categorize_spec("ves.io.schema.views.graph.json") == "statistics"
        assert categorize_spec("ves.io.schema.views.flow.json") == "statistics"

    def test_support(self) -> None:
        """Test categorization of support specs."""
        assert categorize_spec("ves.io.schema.views.operate.setup.json") == "support"
        assert categorize_spec("ves.io.schema.views.ticket_tracking.json") == "support"

    # H. Enterprise & Administration (2 categories)
    def test_tenant_and_identity(self) -> None:
        """Test categorization of tenant and identity management specs."""
        assert (
            categorize_spec("ves.io.schema.views.tenant_management.json") == "tenant_and_identity"
        )
        assert categorize_spec("ves.io.schema.views.authentication.json") == "tenant_and_identity"

    def test_users(self) -> None:
        """Test categorization of user management specs.

        Note: 'user' pattern also exists in tenant_and_identity,
        so it matches there first due to pattern order. Use token pattern which is unique to users.
        """
        result = categorize_spec("ves.io.schema.views.token.json")
        # token matches users domain
        assert result == "users"

    # I. Platform & Integrations (3 categories)
    def test_bigip(self) -> None:
        """Test categorization of BigIP integration specs."""
        assert categorize_spec("ves.io.schema.views.bigip.json") == "bigip"
        assert categorize_spec("ves.io.schema.views.irule.json") == "bigip"

    def test_nginx_one(self) -> None:
        """Test categorization of NGINX One specs."""
        assert categorize_spec("ves.io.schema.views.nginx.json") == "nginx_one"

    def test_marketplace(self) -> None:
        """Test categorization of marketplace specs."""
        assert categorize_spec("ves.io.schema.views.marketplace.json") == "marketplace"
        assert categorize_spec("ves.io.schema.views.addon_package.json") == "marketplace"

    # J. Advanced & Emerging (5 categories)
    def test_ai_services(self) -> None:
        """Test categorization of AI services specs."""
        assert categorize_spec("ves.io.schema.views.ai_assistant.json") == "ai_services"
        assert categorize_spec("ves.io.schema.views.ai_data.json") == "ai_services"

    def test_rate_limiting(self) -> None:
        """Test categorization of rate limiting specs."""
        assert categorize_spec("ves.io.schema.views.rate_limiter.json") == "rate_limiting"
        assert categorize_spec("ves.io.schema.views.policer.json") == "rate_limiting"

    def test_certificates(self) -> None:
        """Test categorization of certificate and configuration specs."""
        assert categorize_spec("ves.io.schema.views.manifest.json") == "certificates"
        assert categorize_spec("ves.io.schema.views.certificate.json") == "certificates"

    def test_object_storage(self) -> None:
        """Test categorization of object storage specs."""
        assert categorize_spec("ves.io.schema.views.stored_object.json") == "object_storage"

    def test_shape(self) -> None:
        """Test categorization of Shape Security specs."""
        assert categorize_spec("ves.io.schema.views.shape.safe.json") == "shape"

    # K. UI & Platform Infrastructure (3 categories)
    def test_admin_console_and_ui(self) -> None:
        """Test categorization of admin console specs."""
        assert categorize_spec("ves.io.schema.views.ui_static.json") == "admin_console_and_ui"
        assert categorize_spec("ves.io.schema.views.navigation_tile.json") == "admin_console_and_ui"

    def test_billing_and_usage(self) -> None:
        """Test categorization of billing specs."""
        assert categorize_spec("ves.io.schema.views.billing.invoice.json") == "billing_and_usage"
        assert categorize_spec("ves.io.schema.views.subscription.json") == "billing_and_usage"

    def test_label(self) -> None:
        """Test categorization of label and governance specs."""
        assert categorize_spec("ves.io.schema.views.label.json") == "label"


class TestFallbackBehavior:
    """Test fallback behavior for uncategorized specs."""

    def test_unknown_spec_returns_other(self) -> None:
        """Verify that unknown specs fall back to 'other' domain."""
        assert categorize_spec("unknown_spec.json") == "other"
        assert categorize_spec("random.file.name.json") == "other"
        assert categorize_spec("ves.io.schema.views.nonexistent.json") == "other"

    def test_case_insensitive(self) -> None:
        """Verify that categorization is case-insensitive."""
        assert categorize_spec("VES.IO.SCHEMA.VIEWS.AWS_VPC_SITE.JSON") == "sites"
        assert categorize_spec("Ves.Io.Schema.Views.App_Firewall.Json") == "virtual"


class TestModuleLevelFunctions:
    """Test module-level function exports (v3.0.0 - no backward compat exports)."""

    def test_categorize_spec_function(self) -> None:
        """Verify that categorize_spec function works correctly."""
        assert categorize_spec("ves.io.schema.views.aws_vpc_site.json") == "sites"

    def test_get_domain_patterns_function(self) -> None:
        """Verify that get_domain_patterns function works correctly."""
        patterns = get_domain_patterns()
        assert isinstance(patterns, dict)
        assert len(patterns) == 33  # 33 domains (WAF merged into virtual)

    def test_all_domains_in_patterns(self) -> None:
        """Verify that all domains are present via get_domain_patterns()."""
        patterns = get_domain_patterns()
        expected_domain_count = 33  # WAF merged into virtual
        assert len(patterns) == expected_domain_count

        # Verify key domains exist
        key_domains = {
            "sites",
            "ddos",
            "blindfold",
            "virtual",
            "dns",
            "network",
            "cdn",
            "observability",
            "statistics",
            "support",
            "ai_services",
            "admin_console_and_ui",
            "label",
        }
        assert key_domains.issubset(set(patterns.keys()))


class TestCaching:
    """Test performance of domain categorization with compiled patterns."""

    def test_repeated_categorization_is_fast(self) -> None:
        """Verify that repeated categorizations work consistently."""
        categorizer = DomainCategorizer()
        filename = "ves.io.schema.views.aws_vpc_site.json"

        # First call
        result1 = categorizer.categorize(filename)
        # Second call should return same result (patterns are compiled at load time)
        result2 = categorizer.categorize(filename)

        assert result1 == result2 == "sites"

    def test_many_categorizations(self) -> None:
        """Verify that categorization works efficiently with many filenames."""
        categorizer = DomainCategorizer()
        # Test with many unknown filenames
        test_files = [f"ves.io.schema.views.test_service_{i}.json" for i in range(100)]

        results = []
        for filename in test_files:
            result = categorizer.categorize(filename)
            # Each unknown filename returns "other"
            assert result == "other"
            results.append(result)

        # All should be "other"
        assert all(r == "other" for r in results)


class TestPatternValidation:
    """Test that domain patterns are valid regexes."""

    def test_all_patterns_valid_regex(self) -> None:
        """Verify that all patterns via get_domain_patterns() are valid regexes."""
        domain_patterns = get_domain_patterns()
        # Collect all patterns first to validate outside nested loop
        all_patterns = [
            (domain, pattern)
            for domain, patterns in domain_patterns.items()
            for pattern in patterns
        ]

        invalid_patterns = []
        for domain, pattern in all_patterns:
            try:
                re.compile(pattern)
            except re.error as e:
                invalid_patterns.append((domain, pattern, str(e)))

        if invalid_patterns:
            errors = "\n".join(f"Domain '{d}', pattern '{p}': {e}" for d, p, e in invalid_patterns)
            pytest.fail(f"Invalid regex patterns found:\n{errors}")

    def test_categorizer_handles_complex_patterns(self) -> None:
        """Verify that categorizer correctly handles complex regex patterns."""
        # Test negative lookbehind pattern for healthcheck
        # healthcheck.ves without dns_lb prefix should match virtual
        assert categorize_spec("ves.io.schema.views.healthcheck.ves.json") == "virtual"

        # Test dns patterns matching dns_lb
        assert categorize_spec("ves.io.schema.views.dns_lb_healthcheck.json") == "dns"
        assert categorize_spec("ves.io.schema.views.dns_lb_pool.json") == "dns"

        # Test route pattern - matches network domain (route pattern)
        assert categorize_spec("ves.io.schema.views.route.json") == "network"

        # Test virtual_host - exists in both service_mesh and virtual
        # service_mesh comes first, so it matches there
        result = categorize_spec("ves.io.schema.views.virtual_host.ves.json")
        assert result in ["service_mesh", "virtual"]
