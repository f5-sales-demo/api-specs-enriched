# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for the 5-layer description validation system.

Tests cover:
- Layer 1: Banned patterns (regex word boundaries)
- Layer 2: Self-referential detection
- Layer 3: Quality metrics validation
- Layer 4: Circular definition detection
- Full structured validation integration
"""

import pytest

from scripts.generate_descriptions import (
    BANNED_PATTERNS,
    DOMAIN_SYNONYMS,
    SUCCESSFUL_PATTERNS,
    Violation,
    build_refinement_prompt,
    check_cross_tier_violations,
    check_domain_name_usage,
    is_circular_definition,
    is_self_referential,
    run_all_validations,
)


class TestBannedPatterns:
    """Layer 1: Test regex-based banned pattern detection."""

    @pytest.mark.parametrize(
        ("text", "should_fail"),
        [
            # Redundant terms
            ("This is the API for users.", True),
            ("REST API documentation.", True),
            ("Endpoint configuration.", True),
            ("Specifications for the service.", True),
            # Word boundary test - should NOT fail
            ("Rapid deployment options.", False),
            ("Therapy management system.", False),
            ("Rapier tools available.", False),
            # Brand names
            ("F5 load balancer.", True),
            ("XC configuration.", True),
            ("Distributed Cloud services.", True),
            ("Volterra platform.", True),
            # Filler words
            ("Utilize advanced features.", True),
            ("Leverage cloud services.", True),
            ("Facilitate communication.", True),
            ("In order to configure.", True),
            # Acceptable alternatives
            ("Advanced features available.", False),
            ("Communication protocols.", False),
            ("Settings for configuration.", False),
            # Vague descriptors
            ("Various configurations.", True),
            ("Multiple options available.", True),
            ("Several settings etc.", True),
            # Marketing hype
            ("Seamless integration.", True),
            ("Robust security.", True),
            ("Powerful automation.", True),
            ("Cutting-edge technology.", True),
            # Passive voice
            ("Data is returned by the server.", True),
            ("Connections are handled automatically.", True),
            # Active voice (should pass)
            ("Server returns data directly.", False),
            ("Connection handling automatic.", False),
            # Truncation indicators
            ("Configure settings...", True),
            ("Set up load balancing…", True),
        ],
    )
    def test_banned_patterns(self, text: str, should_fail: bool) -> None:
        """Test that banned patterns are correctly detected."""
        violations = [
            v
            for v in run_all_validations("unrelated", {"short": text})
            if v.layer == "banned_patterns"
        ]
        if should_fail:
            assert len(violations) > 0, f"Expected violation for: {text}"
        else:
            assert len(violations) == 0, f"Unexpected violation for: {text}"

    def test_patterns_are_case_insensitive(self) -> None:
        """Verify all patterns match case-insensitively."""
        # Test various cases
        test_cases = [
            "API configuration",
            "api configuration",
            "Api Configuration",
            "API CONFIGURATION",
        ]
        for text in test_cases:
            violations = [
                v
                for v in run_all_validations("unrelated", {"short": text})
                if v.layer == "banned_patterns"
            ]
            assert len(violations) > 0, f"Should detect API in: {text}"

    def test_word_boundaries_prevent_false_positives(self) -> None:
        """Verify word boundaries prevent matching within words."""
        # These should NOT trigger violations
        safe_texts = [
            "rapid deployment",  # Contains "api" but not as word
            "therapy management",  # Contains "api" substring
            "capitalize on opportunities",  # Contains "api" substring
        ]
        for text in safe_texts:
            violations = run_all_validations("unrelated", {"short": text})
            api_violations = [
                v for v in violations if v.layer == "banned_patterns" and "api" in v.message.lower()
            ]
            assert len(api_violations) == 0, f"False positive for: {text}"


class TestSelfReferential:
    """Layer 2: Test self-referential pattern detection."""

    @pytest.mark.parametrize(
        ("domain", "description", "should_fail"),
        [
            # Exact match patterns
            ("authentication", "Authentication API", True),
            ("data_intelligence", "Data Intelligence Service", True),
            ("virtual", "Virtual APIs", True),
            ("network_security", "Network Security System", True),
            # Acceptable descriptions (noun-first, DRY-compliant)
            ("authentication", "Secure identity verification.", False),
            ("data_intelligence", "Traffic pattern analysis.", False),
            ("loadbalancing", "Load balancers and routing.", False),
        ],
    )
    def test_self_referential_detection(
        self,
        domain: str,
        description: str,
        should_fail: bool,
    ) -> None:
        """Test detection of domain name + generic suffix patterns."""
        is_violation, msg = is_self_referential(domain, description)
        if should_fail:
            assert is_violation, f"Expected violation for: {domain} -> {description}"
            assert "LAZY" in msg
        else:
            assert not is_violation, f"Unexpected violation for: {domain} -> {description}"


class TestQualityMetrics:
    """Layer 3: Test quality metrics validation."""

    def test_short_description_character_limit(self) -> None:
        """Verify short descriptions are limited to 60 characters."""
        long_text = "A" * 65
        violations = run_all_validations("unrelated", {"short": long_text})
        assert any(v.code == "LENGTH" for v in violations)

    def test_medium_description_character_limit(self) -> None:
        """Verify medium descriptions are limited to 150 characters."""
        long_text = "A" * 155
        violations = run_all_validations("unrelated", {"medium": long_text})
        assert any(v.code == "LENGTH" for v in violations)

    def test_long_description_character_limit(self) -> None:
        """Verify long descriptions are limited to 500 characters."""
        long_text = "A" * 505
        violations = run_all_validations("unrelated", {"long": long_text})
        assert any(v.code == "LENGTH" for v in violations)

    def test_minimum_word_count(self) -> None:
        """Verify descriptions have at least 3 words."""
        violations = run_all_validations("unrelated", {"short": "Too short"})
        assert any(v.code == "SPARSE" for v in violations)

    def test_quality_metrics_no_style_enforcement(self) -> None:
        """Verify quality metrics no longer enforce style (DRY-compliant).

        Action verb requirements were removed because CRUD verbs are
        implied in a CRUD API context. Descriptions should start with
        nouns/concepts, not action verbs.
        """
        # Quality metrics only checks length and word count now
        text = "Load balancers and routing policies"
        violations = run_all_validations("unrelated", {"short": text})
        style_violations = [v for v in violations if v.code == "STYLE"]
        assert len(style_violations) == 0, "Valid noun-first text should pass style validation"


class TestCircularDefinition:
    """Layer 4: Test circular definition detection."""

    def test_short_description_no_repetition(self) -> None:
        """Short descriptions should not repeat exact same words."""
        # "routes" and "route" are different words, so no violation
        is_circular, _ = is_circular_definition("Manage routes and route tables", "short")
        assert not is_circular, "Different word forms should not trigger violation"

        # Same word repeated should trigger violation
        is_circular, _ = is_circular_definition("Manage security and security policies", "short")
        assert is_circular, "Should detect 'security' repetition in short"

    def test_medium_description_no_repetition(self) -> None:
        """Medium descriptions should not repeat words."""
        is_circular, _ = is_circular_definition(
            "Configure security settings and security policies",
            "medium",
        )
        assert is_circular, "Should detect 'security' repetition in medium"

    def test_long_description_allows_some_repetition(self) -> None:
        """Long descriptions allow up to 2 repetitions of a word."""
        # 2 repetitions should pass
        is_circular, _ = is_circular_definition(
            "Configure cache rules for caching behavior",
            "long",
        )
        assert not is_circular, "Should allow 2 repetitions in long"

        # 3+ repetitions should fail
        is_circular, _ = is_circular_definition(
            "Configure cache rules for cache invalidation and cache purging",
            "long",
        )
        assert is_circular, "Should detect 3+ repetitions in long"

    def test_short_words_ignored(self) -> None:
        """Words with 4 or fewer characters should be ignored."""
        is_circular, _ = is_circular_definition("Set the API and the DNS", "short")
        # "the" appears twice but should be ignored (≤4 chars)
        # "API" triggers banned pattern, not circular
        assert not is_circular, "Should ignore short words"


class TestRunAllValidationsIntegration:
    """Layer 5: Integration test for complete validation."""

    def test_valid_descriptions_pass(self) -> None:
        """Verify well-formed descriptions pass all checks."""
        descriptions = {
            "short": "Load balancers, routing, and traffic distribution.",
            "medium": "HTTP and TCP listeners with health checks. Failover policies and routing.",
            "long": (
                "Application delivery infrastructure with origin pools. "
                "Geo-routing, rate limiting, and SSL termination. "
                "Security policies for WAF and bot protection."
            ),
        }
        violations = run_all_validations("loadbalancing", descriptions)
        assert len(violations) == 0, f"Unexpected violations: {violations}"

    def test_api_in_description_fails(self) -> None:
        """Verify 'API' in description triggers violation."""
        descriptions = {
            "short": "Identity verification API.",
            "medium": "Token-based authentication methods.",
            "long": "Identity verification and access control.",
        }
        violations = run_all_validations("auth", descriptions)
        assert any(v.code == "REDUNDANT" and v.location == "API" for v in violations)

    def test_domain_name_in_description_fails(self) -> None:
        """Verify domain name in description triggers violation."""
        descriptions = {
            "short": "Virtual resources and workloads.",
            "medium": "Virtual machines and containers.",
            "long": "Virtual infrastructure and scaling.",
        }
        violations = run_all_validations("virtual", descriptions)
        assert any(v.code == "DOMAIN_NAME" for v in violations)

    def test_brand_names_fail(self) -> None:
        """Verify brand names trigger violations."""
        descriptions = {
            "short": "F5 services and deployments.",
            "medium": "XC deployments and infrastructure.",
            "long": "Distributed Cloud infrastructure.",
        }
        violations = run_all_validations("network", descriptions)
        assert any(v.code == "BRAND" for v in violations)

    def test_cross_tier_repetition_detection(self) -> None:
        """Verify excessive word overlap between tiers is detected.

        Cross-tier overlap requires 4+ significant words to trigger.
        Uses unusual domain words that are NOT in the stop words list.
        Common terms like 'security', 'policies', 'rules' are filtered out.
        """
        descriptions = {
            # Use words NOT in the stop_words list: kubernetes, container, replica, upstream, downstream
            # These are significant domain-specific words that should trigger overlap detection
            "short": "Orchestrate kubernetes replica upstream downstream",
            "medium": "Orchestrate kubernetes replica upstream downstream clusters. Synchronize stateful workloads.",
            "long": "Orchestrate kubernetes replica upstream downstream clusters. Synchronize stateful workloads across regions. Maintain replica consistency.",
        }
        violations = run_all_validations("orchestration", descriptions)
        # Should detect significant overlap (4+ words: kubernetes, replica, upstream, downstream)
        assert any(v.code == "CROSS_TIER_OVERLAP" for v in violations)


class TestBannedPatternsCompleteness:
    """Test that BANNED_PATTERNS covers all required categories."""

    def test_redundant_category_exists(self) -> None:
        """Verify redundant terms are covered."""
        patterns_str = str(BANNED_PATTERNS)
        assert "api" in patterns_str.lower()
        assert "endpoint" in patterns_str.lower()

    def test_brand_category_exists(self) -> None:
        """Verify brand names are covered."""
        patterns_str = str(BANNED_PATTERNS)
        assert "f5" in patterns_str.lower()
        assert "volterra" in patterns_str.lower()

    def test_filler_category_exists(self) -> None:
        """Verify filler words are covered."""
        patterns_str = str(BANNED_PATTERNS)
        assert "utilize" in patterns_str.lower()
        assert "leverage" in patterns_str.lower()

    def test_vague_category_exists(self) -> None:
        """Verify vague descriptors are covered."""
        patterns_str = str(BANNED_PATTERNS)
        assert "various" in patterns_str.lower()
        assert "etc" in patterns_str.lower()

    def test_marketing_category_exists(self) -> None:
        """Verify marketing hype is covered."""
        patterns_str = str(BANNED_PATTERNS)
        assert "seamless" in patterns_str.lower()
        assert "robust" in patterns_str.lower()

    def test_passive_voice_category_exists(self) -> None:
        """Verify passive voice patterns are covered."""
        patterns_str = str(BANNED_PATTERNS)
        assert "is returned" in patterns_str.lower()
        assert "are handled" in patterns_str.lower()


class TestViolationDataclass:
    """Test the Violation dataclass and its methods."""

    def test_violation_str_representation(self) -> None:
        """Verify the concise log representation."""
        v = Violation(
            layer="self_referential",
            tier="long",
            code="DOMAIN_NAME",
            message="Contains domain name",
            location="observability",
            suggestion="Replace with: monitoring infrastructure",
        )
        assert str(v) == "long/DOMAIN_NAME: Contains domain name"

    def test_violation_to_feedback_basic(self) -> None:
        """Verify to_feedback() generates actionable feedback."""
        v = Violation(
            layer="self_referential",
            tier="long",
            code="DOMAIN_NAME",
            message="Contains domain name 'observability'",
            location="observability",
            suggestion="Replace with: monitoring infrastructure, telemetry systems",
            examples=["Deploy monitoring infrastructure with metrics collection"],
        )
        feedback = v.to_feedback()
        assert "LONG" in feedback
        assert "DOMAIN_NAME" in feedback
        assert "observability" in feedback
        assert "monitoring infrastructure" in feedback
        assert "Deploy monitoring infrastructure" in feedback

    def test_violation_to_feedback_without_examples(self) -> None:
        """Verify to_feedback() works without examples."""
        v = Violation(
            layer="banned_patterns",
            tier="short",
            code="REDUNDANT",
            message="Banned term: API",
            location="API",
            suggestion="Remove or replace this term",
        )
        feedback = v.to_feedback()
        assert "SHORT" in feedback
        assert "REDUNDANT" in feedback
        assert "API" in feedback

    def test_violation_to_feedback_cross_tier(self) -> None:
        """Verify to_feedback() handles cross-tier violations."""
        v = Violation(
            layer="cross_tier",
            tier="cross_tier",
            code="CROSS_TIER_OVERLAP",
            message="Medium and long share 4+ words",
            location="connections, transit, azure, virtual",
            suggestion="Replace 'connections' with links/paths; 'transit' with routing",
        )
        feedback = v.to_feedback()
        assert "CROSS_TIER" in feedback
        assert "connections" in feedback
        assert "links" in feedback or "Replace" in feedback


class TestDomainNameUsage:
    """Test the check_domain_name_usage() function."""

    def test_detects_domain_name_in_text(self) -> None:
        """Verify domain name is detected in text."""
        v = check_domain_name_usage(
            "observability",
            "Observability tools and dashboards.",
            "long",
        )
        assert v is not None
        assert v.code == "DOMAIN_NAME"
        assert v.tier == "long"
        assert "observability" in v.location.lower()

    def test_no_violation_when_domain_absent(self) -> None:
        """Verify no violation when domain name is not present."""
        v = check_domain_name_usage(
            "observability",
            "Monitoring tools and dashboards.",
            "long",
        )
        assert v is None

    def test_provides_domain_synonyms(self) -> None:
        """Verify suggestion includes domain-specific synonyms."""
        v = check_domain_name_usage(
            "observability",
            "Observability pipelines and data flows.",
            "medium",
        )
        assert v is not None
        assert "monitoring" in v.suggestion.lower() or "telemetry" in v.suggestion.lower()

    def test_handles_underscore_domains(self) -> None:
        """Verify domains with underscores are normalized."""
        v = check_domain_name_usage(
            "network_security",
            "Network security policies.",
            "short",
        )
        assert v is not None
        assert v.code == "DOMAIN_NAME"


class TestCrossTierViolations:
    """Test the check_cross_tier_violations() function."""

    def test_detects_word_overlap(self) -> None:
        """Verify word overlap is detected between tiers (needs 4+ words not in stop list)."""
        # Use words NOT in stop_words: connections, transit, virtual, peering, upstream
        descriptions = {
            "short": "Connections transit virtual peering.",
            "medium": "Connections with transit links. Virtual peering upstream.",
            "long": "Connections across regions. Transit virtual peering upstream.",
        }
        violations = check_cross_tier_violations(descriptions)
        assert len(violations) > 0
        overlap_v = [v for v in violations if v.code == "CROSS_TIER_OVERLAP"]
        assert len(overlap_v) > 0

    def test_no_violation_with_unique_vocabulary(self) -> None:
        """Verify no violation when tiers use unique words."""
        descriptions = {
            "short": "Load balancers and routing.",
            "medium": "Routing policies with health checks. Failover rules.",
            "long": "Traffic distribution across regions. Latency and throughput metrics.",
        }
        violations = check_cross_tier_violations(descriptions)
        # Should not have cross-tier overlap since vocabulary is different
        overlap_v = [v for v in violations if v.code == "CROSS_TIER_OVERLAP"]
        assert len(overlap_v) == 0

    def test_provides_synonym_hints(self) -> None:
        """Verify synonym hints are provided for overlapping words."""
        descriptions = {
            "short": "Orchestrate kubernetes replica upstream downstream",
            "medium": "Orchestrate kubernetes replica upstream downstream clusters.",
            "long": "Orchestrate kubernetes replica upstream downstream in regions.",
        }
        violations = check_cross_tier_violations(descriptions)
        overlap_v = [v for v in violations if v.code == "CROSS_TIER_OVERLAP"]
        if overlap_v:
            # Should have synonym suggestions
            assert "→" in overlap_v[0].suggestion or "Replace" in overlap_v[0].suggestion


class TestRunAllValidations:
    """Test the canonical run_all_validations() function."""

    def test_returns_empty_for_valid_descriptions(self) -> None:
        """Verify empty list for compliant descriptions."""
        descriptions = {
            "short": "Load balancers and routing policies.",
            "medium": "HTTP listeners with health checks. Failover policies.",
            "long": (
                "Application delivery infrastructure with origin pools. "
                "Geo-routing, rate limiting, and SSL termination."
            ),
        }
        violations = run_all_validations("loadbalancing", descriptions)
        # Filter out any overlap violations (depends on exact wording)
        critical_violations = [v for v in violations if v.code in ("DOMAIN_NAME", "BANNED_PATTERN")]
        assert len(critical_violations) == 0

    def test_returns_violations_for_banned_terms(self) -> None:
        """Verify violations are returned for banned terms."""
        descriptions = {
            "short": "API endpoints and interfaces.",
            "medium": "HTTP listeners and routing.",
            "long": "Infrastructure and deployments.",
        }
        violations = run_all_validations("routing", descriptions)
        assert len(violations) > 0
        assert any(v.code == "REDUNDANT" for v in violations)

    def test_returns_violations_for_domain_name(self) -> None:
        """Verify violations are returned for domain name in text."""
        descriptions = {
            "short": "Observability tools and dashboards.",
            "medium": "Dashboards for observability data.",
            "long": "Observability infrastructure.",
        }
        violations = run_all_validations("observability", descriptions)
        assert len(violations) > 0
        domain_violations = [v for v in violations if v.code == "DOMAIN_NAME"]
        assert len(domain_violations) >= 1

    def test_all_violations_have_required_fields(self) -> None:
        """Verify all violations have required fields populated."""
        descriptions = {
            "short": "The API provides observability",
            "medium": "Various settings are handled.",
            "long": "Robust and seamless integration is provided.",
        }
        violations = run_all_validations("observability", descriptions)
        for v in violations:
            assert v.layer, "layer should be set"
            assert v.tier, "tier should be set"
            assert v.code, "code should be set"
            assert v.message, "message should be set"

    def test_ignores_non_tier_metadata(self) -> None:
        """Verify metadata stored beside the tiers is not treated as prose."""
        descriptions = {
            "short": "Load balancers and routing policies.",
            "medium": "HTTP listeners with health checks.",
            "long": "Application delivery infrastructure with origin pools.",
            "source_patterns_hash": "api",
        }

        violations = run_all_validations("loadbalancing", descriptions)

        assert all(v.tier != "source_patterns_hash" for v in violations)

    def test_refinement_prompt_rejects_string_violations(self) -> None:
        """Verify the refinement boundary accepts only structured violations."""
        with pytest.raises(TypeError, match="Violation objects"):
            build_refinement_prompt(
                "routing",
                {"domain_title": "Routing"},
                {"short": "Routing resources."},
                ["legacy violation"],  # type: ignore[list-item]
            )


class TestDomainSynonymsCompleteness:
    """Test that DOMAIN_SYNONYMS covers all domains."""

    def test_observability_has_synonyms(self) -> None:
        """Verify observability domain has synonyms."""
        assert "observability" in DOMAIN_SYNONYMS
        synonyms = DOMAIN_SYNONYMS["observability"]
        assert len(synonyms) >= 2
        assert "monitoring" in " ".join(synonyms).lower()

    def test_sites_has_synonyms(self) -> None:
        """Verify sites domain has synonyms."""
        assert "sites" in DOMAIN_SYNONYMS
        synonyms = DOMAIN_SYNONYMS["sites"]
        assert len(synonyms) >= 2

    def test_all_major_domains_covered(self) -> None:
        """Verify all major domains have synonym mappings."""
        major_domains = [
            "observability",
            "sites",
            "network",
            "authentication",
            "waf",
            "cdn",
            "dns",
            "virtual",
            "certificates",
            "users",
        ]
        for domain in major_domains:
            assert domain in DOMAIN_SYNONYMS, f"Missing synonyms for {domain}"


class TestSuccessfulPatterns:
    """Test that SUCCESSFUL_PATTERNS are valid."""

    def test_all_categories_have_patterns(self) -> None:
        """Verify all categories have pattern examples."""
        expected_categories = ["infrastructure", "security", "delivery", "management", "operations"]
        for cat in expected_categories:
            assert cat in SUCCESSFUL_PATTERNS, f"Missing pattern for {cat}"

    def test_patterns_have_all_tiers(self) -> None:
        """Verify each pattern has short/medium/long."""
        for cat, pattern in SUCCESSFUL_PATTERNS.items():
            assert "short" in pattern, f"{cat} missing short"
            assert "medium" in pattern, f"{cat} missing medium"
            assert "long" in pattern, f"{cat} missing long"

    def test_patterns_fit_character_limits(self) -> None:
        """Verify patterns fit within character limits."""
        for cat, pattern in SUCCESSFUL_PATTERNS.items():
            assert len(pattern["short"]) <= 60, f"{cat} short too long: {len(pattern['short'])}"
            assert len(pattern["medium"]) <= 150, f"{cat} medium too long: {len(pattern['medium'])}"
            assert len(pattern["long"]) <= 500, f"{cat} long too long: {len(pattern['long'])}"

    def test_patterns_are_noun_first(self) -> None:
        """Verify patterns start with nouns, not action verbs (DRY-compliant)."""
        banned_starters = {
            "configure",
            "create",
            "manage",
            "define",
            "deploy",
            "set",
            "monitor",
            "access",
            "enable",
            "handle",
        }
        for cat, pattern in SUCCESSFUL_PATTERNS.items():
            first_word = pattern["short"].split()[0].lower().rstrip(",.:;")
            assert first_word not in banned_starters, (
                f"{cat} short starts with banned verb: {first_word}"
            )
