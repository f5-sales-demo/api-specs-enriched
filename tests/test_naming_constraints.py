#!/usr/bin/env python3
"""Naming-constraint completeness & consistency tests.

Verifies that F5 XC resource naming rules are identified completely and
consistently so downstream consumers can pull them deterministically.

Ground truth (live-verified against staging, SE tenant, 2026-07-20):
- Native object ``metadata.name`` = DNS-1035 label: ``^[a-z]([-a-z0-9]*[a-z0-9])?$``,
  1-63 chars, alpha-first, no dots. (digit-first / dotted / 64-char all HTTP 400;
  1-char accepted.)
- Workload volume names use the upstream rule ``dns_1123_label`` (alnum-first,
  leading digit allowed) and must NOT be normalized to DNS-1035.

Covers plan items A (single source of truth), B (map the authoritative
``ves_object_name`` rule), C (honor ``dns_1123_label``), E (correct length bounds).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.utils.constraint_enricher import ConstraintEnricher  # noqa: E402

CONFIG_DIR = REPO_ROOT / "config"

# Canonical, live-verified naming regexes.
DNS_1035 = "^[a-z]([-a-z0-9]*[a-z0-9])?$"  # alpha-first (native object names)
DNS_1123 = "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"  # alnum-first (workload volume names)


@pytest.fixture
def enricher() -> ConstraintEnricher:
    return ConstraintEnricher(config_path=CONFIG_DIR / "constraint_patterns.yaml")


def _enrich_one_property(enricher: ConstraintEnricher, field_name: str, schema: dict) -> dict:
    """Run the enricher over a single-property schema and return that property."""
    spec = {"components": {"schemas": {"T": {"properties": {field_name: schema}}}}}
    enricher.enrich_spec(spec)
    return spec["components"]["schemas"]["T"]["properties"][field_name]


# ---------------------------------------------------------------------------
# Item A: single source of truth — the name/namespace regex is identical
# across every config that defines it.
# ---------------------------------------------------------------------------


class TestConfigConsistency:
    """The canonical native-name regex (DNS-1035) must not drift across configs.

    The alnum-first DNS-1123 form is allowed ONLY for the ``dns_1123_label``
    discovery rule (workload volume names), never for native object names.
    """

    def _pattern_entries(self, cfg: str, key: str) -> list[dict]:
        data = yaml.safe_load((CONFIG_DIR / cfg).read_text())
        return data.get(key, []) or []

    def test_constraint_patterns_native_name_is_dns1035(self):
        entries = self._pattern_entries("constraint_patterns.yaml", "string_patterns")
        naming = [e for e in entries if e.get("pattern") in (r"\bname$", r"\bnamespace$")]
        assert naming, "expected \\bname$/\\bnamespace$ patterns in constraint_patterns.yaml"
        for e in naming:
            assert e["constraints"]["pattern"] == DNS_1035, (
                f"native name pattern must be DNS-1035: {e.get('pattern')}"
            )

    def test_validation_schema_native_name_is_dns1035(self):
        data = yaml.safe_load((CONFIG_DIR / "validation_schema.yaml").read_text())
        entries = data.get("constraints", {}).get("patterns", []) or []
        naming = [e for e in entries if e.get("pattern") in (r"\bname$", r"\bnamespace$")]
        assert naming, "expected name/namespace entries in validation_schema.yaml"
        for e in naming:
            assert e["constraints"]["pattern"] == DNS_1035

    def test_extension_registry_naming_example_is_dns1035(self):
        text = (CONFIG_DIR / "extension_registry.yaml").read_text()
        assert DNS_1035 in text, "extension_registry naming example must use DNS-1035"
        # The alnum-first form must not appear as a native-name example there.
        assert DNS_1123 not in text, "extension_registry must not use DNS-1123 for native names"

    def test_dns1123_only_for_workload_volume_rule(self):
        """The alnum-first regex may appear only alongside the dns_1123_label rule."""
        data = yaml.safe_load((CONFIG_DIR / "constraint_patterns.yaml").read_text())
        string_rules = data["discovery_mapping"]["string_rules"]
        alnum_rules = {
            r["ves_rule"]
            for r in string_rules
            if r.get("constraint_field") == "pattern" and r.get("constraint_value") == DNS_1123
        }
        assert alnum_rules == {"ves.io.schema.rules.string.dns_1123_label"}, (
            f"alnum-first pattern must map only to dns_1123_label, got: {alnum_rules}"
        )
        # And the native object-name rule must map to DNS-1035.
        dns1035_rules = {
            r["ves_rule"]
            for r in string_rules
            if r.get("constraint_field") == "pattern" and r.get("constraint_value") == DNS_1035
        }
        assert "ves.io.schema.rules.string.ves_object_name" in dns1035_rules


# ---------------------------------------------------------------------------
# Item B: the authoritative upstream rule ves_object_name is mapped.
# ---------------------------------------------------------------------------


class TestVesObjectNameMapping:
    def test_ves_object_name_in_discovery_mapping(self):
        cfg = yaml.safe_load((CONFIG_DIR / "constraint_patterns.yaml").read_text())
        rules = {r["ves_rule"] for r in cfg["discovery_mapping"]["string_rules"] if "ves_rule" in r}
        assert "ves.io.schema.rules.string.ves_object_name" in rules

    def test_ves_object_name_produces_dns1035(self, enricher):
        prop = _enrich_one_property(
            enricher,
            "name",
            {
                "type": "string",
                "x-ves-validation-rules": {"ves.io.schema.rules.string.ves_object_name": "true"},
            },
        )
        c = prop["x-f5xc-constraints"]
        assert c["format"] == "dns-label"
        assert c["pattern"] == DNS_1035
        assert c["metadata"]["source"] == "discovery"

    def test_ves_object_name_does_not_clobber_explicit_max_len(self, enricher):
        """ves_object_name is a charset rule; an explicit max_len must be preserved.

        Regression guard: fields like `domain` (max_len 17) and `role` (max_len
        256) carry ves_object_name AND their own length rule. The object-name
        mapping must set only pattern/format, never override the tighter length.
        """
        prop = _enrich_one_property(
            enricher,
            "domain",
            {
                "type": "string",
                "x-ves-validation-rules": {
                    "ves.io.schema.rules.string.ves_object_name": "true",
                    "ves.io.schema.rules.string.max_len": "17",
                },
            },
        )
        c = prop["x-f5xc-constraints"]
        assert c["pattern"] == DNS_1035
        assert c["maxLength"] == 17, "explicit max_len must win over the object-name default"


# ---------------------------------------------------------------------------
# Item C: dns_1123_label fields must be alnum-first, not normalized to DNS-1035.
# ---------------------------------------------------------------------------


class TestDns1123Label:
    def test_dns1123_field_is_alnum_first(self, enricher):
        prop = _enrich_one_property(
            enricher,
            "name",
            {
                "type": "string",
                "x-ves-validation-rules": {"ves.io.schema.rules.string.dns_1123_label": "true"},
            },
        )
        c = prop["x-f5xc-constraints"]
        assert c["pattern"] == DNS_1123, "dns_1123_label must keep the alnum-first pattern"
        assert c["pattern"] != DNS_1035, "dns_1123_label must NOT be normalized to DNS-1035"


# ---------------------------------------------------------------------------
# Item E: name length bounds must match the live API (min 1, max 63) — never
# the generic 1024 string default.
# ---------------------------------------------------------------------------


class TestNameLengthBounds:
    def test_name_maxlength_is_63_not_generic_default(self, enricher):
        # Simulate a name field that a prior enricher stamped with the generic 1024.
        prop = _enrich_one_property(
            enricher,
            "name",
            {"type": "string", "maxLength": 1024},
        )
        c = prop["x-f5xc-constraints"]
        assert c["maxLength"] == 63, "name maxLength must be the DNS-1035 limit (63), not 1024"
        assert c["minLength"] == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
