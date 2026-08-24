"""Unit tests for all probe strategies."""
# pylint: disable=use-implicit-booleaness-not-comparison  # Explicit empty results document the expected shape.

# pylint: disable=attribute-defined-outside-init

from __future__ import annotations

import json

from scripts.discovery.probes import ProbeResponse
from scripts.discovery.probes.enum import EnumProbe
from scripts.discovery.probes.numeric import NumericBoundaryProbe, _set_nested
from scripts.discovery.probes.oneof import OneOfProbe
from scripts.discovery.probes.required import RequiredFieldProbe
from scripts.discovery.probes.roundtrip import _compare_payloads, _flatten
from scripts.discovery.probes.string import StringProbe

BASE_PAYLOAD = {
    "metadata": {"name": "test-hc", "namespace": "default"},
    "spec": {
        "http_health_check": {"path": "/health"},
        "interval": 15,
        "timeout": 3,
        "healthy_threshold": 3,
        "unhealthy_threshold": 1,
    },
}


class TestSetNested:
    def test_sets_top_level_key(self):
        d = {"a": 1}
        result = _set_nested(d, "a", 99)
        assert result["a"] == 99

    def test_sets_nested_key(self):
        d = {"spec": {"timeout": 3}}
        result = _set_nested(d, "spec.timeout", 999)
        assert result["spec"]["timeout"] == 999

    def test_creates_missing_intermediate(self):
        d = {}
        result = _set_nested(d, "spec.new_field", 42)
        assert result["spec"]["new_field"] == 42


class TestNumericBoundaryProbe:
    def setup_method(self):
        self.probe = NumericBoundaryProbe()

    def test_generates_base_probes(self):
        probes = self.probe.generate_probes("spec.timeout", {"type": "integer"}, BASE_PAYLOAD, None)
        values = [p.payload["spec"]["timeout"] for p in probes]
        assert -1 in values
        assert 0 in values
        assert 999_999 in values

    def test_generates_boundary_probes_when_constraints_known(self):
        probes = self.probe.generate_probes(
            "spec.timeout",
            {"type": "integer"},
            BASE_PAYLOAD,
            {"minimum": 1, "maximum": 600},
        )
        values = [p.payload["spec"]["timeout"] for p in probes]
        assert 0 in values
        assert 601 in values

    def test_interprets_parsed_constraint(self):
        results = [
            ProbeResponse(
                "spec.timeout",
                400,
                False,
                "must be >= 1 and <= 600",
                {"minimum": 1, "maximum": 600},
                None,
            ),
        ]
        result = self.probe.interpret_results("spec.timeout", results)
        assert result.actual == {"minimum": 1, "maximum": 600}
        assert result.confidence == 0.95

    def test_low_confidence_when_no_parsed_constraints(self):
        results = [
            ProbeResponse("spec.timeout", 400, False, "some unknown error", None, None),
        ]
        result = self.probe.interpret_results("spec.timeout", results)
        assert result.confidence == 0.7
        assert result.actual == {}


class TestStringProbe:
    def setup_method(self):
        self.probe = StringProbe()

    def test_generates_empty_and_long_probes(self):
        probes = self.probe.generate_probes("spec.host", {"type": "string"}, BASE_PAYLOAD, None)
        assert len(probes) >= 2

    def test_generates_digit_first_probe_for_name_field(self):
        payload = {"metadata": {"name": "test"}, "spec": {}}
        probes = self.probe.generate_probes("metadata.name", {"type": "string"}, payload, None)
        descriptions = [p.description for p in probes]
        assert any("digit-first" in d for d in descriptions)

    def test_no_digit_first_probe_for_non_name_field(self):
        probes = self.probe.generate_probes("spec.host", {"type": "string"}, BASE_PAYLOAD, None)
        descriptions = [p.description for p in probes]
        assert not any("digit-first" in d for d in descriptions)


class TestOneOfProbe:
    def setup_method(self):
        self.probe = OneOfProbe()

    def test_generates_conflict_probe(self):
        schema = {
            "x-ves-oneof-field-health_check": [
                "http_health_check",
                "tcp_health_check",
                "udp_icmp_health_check",
            ]
        }
        probes = self.probe.generate_probes("spec.health_check", schema, BASE_PAYLOAD, None)
        assert len(probes) == 1
        spec = probes[0].payload["spec"]
        assert "http_health_check" in spec
        assert "tcp_health_check" in spec

    def test_handles_json_string_encoded_variants(self):
        schema = {
            "x-ves-oneof-field-health_check": json.dumps(["http_health_check", "tcp_health_check"])
        }
        probes = self.probe.generate_probes("spec.health_check", schema, BASE_PAYLOAD, None)
        assert len(probes) == 1

    def test_returns_empty_if_fewer_than_two_variants(self):
        schema = {"x-ves-oneof-field-health_check": ["only_one"]}
        probes = self.probe.generate_probes("spec.health_check", schema, BASE_PAYLOAD, None)
        assert probes == []

    def test_interprets_variant_list_from_error(self):
        results = [
            ProbeResponse(
                "spec.health_check",
                400,
                False,
                "one of [http_health_check tcp_health_check udp_icmp_health_check] must be specified",
                {
                    "variants": [
                        "http_health_check",
                        "tcp_health_check",
                        "udp_icmp_health_check",
                    ]
                },
                None,
            )
        ]
        result = self.probe.interpret_results("spec.health_check", results)
        assert result.actual["variants"] == [
            "http_health_check",
            "tcp_health_check",
            "udp_icmp_health_check",
        ]
        assert result.confidence == 0.95


class TestEnumProbe:
    def setup_method(self):
        self.probe = EnumProbe()

    def test_generates_invalid_sentinel_probe(self):
        probes = self.probe.generate_probes(
            "spec.algorithm", {"type": "string"}, BASE_PAYLOAD, None
        )
        assert len(probes) == 1
        assert "INVALID_ENUM_VALUE" in str(probes[0].payload)

    def test_interprets_enum_values(self):
        results = [
            ProbeResponse(
                "spec.algorithm",
                400,
                False,
                "valid values are [ROUND_ROBIN, LEAST_ACTIVE, RANDOM]",
                {"enum_values": ["ROUND_ROBIN", "LEAST_ACTIVE", "RANDOM"]},
                None,
            )
        ]
        result = self.probe.interpret_results("spec.algorithm", results)
        assert result.actual["enum_values"] == ["ROUND_ROBIN", "LEAST_ACTIVE", "RANDOM"]


class TestRequiredFieldProbe:
    def setup_method(self):
        self.probe = RequiredFieldProbe()

    def test_generates_omission_probe(self):
        probes = self.probe.generate_probes("spec.timeout", {}, BASE_PAYLOAD, None)
        assert len(probes) == 1
        assert "timeout" not in probes[0].payload.get("spec", {})

    def test_interprets_rejection_as_required(self):
        results = [ProbeResponse("spec.timeout", 400, False, "field is required", None, None)]
        result = self.probe.interpret_results("spec.timeout", results)
        assert result.actual["required"] is True

    def test_interprets_acceptance_as_optional(self):
        results = [ProbeResponse("spec.timeout", 200, True, None, None, {})]
        result = self.probe.interpret_results("spec.timeout", results)
        assert result.actual["required"] is False


class TestFlattenAndCompare:
    def test_flatten_nested(self):
        d = {"a": {"b": {"c": 1}}}
        assert _flatten(d) == {"a.b.c": 1}

    def test_flatten_mixed(self):
        d = {"a": 1, "b": {"c": 2}}
        assert _flatten(d) == {"a": 1, "b.c": 2}

    def test_compare_finds_server_defaults(self):
        sent = {"timeout": 3}
        received = {"timeout": 3, "jitter": 0}
        defaults, _response_only = _compare_payloads(sent, received)
        assert "jitter" in defaults

    def test_compare_finds_response_only_metadata(self):
        sent = {"timeout": 3}
        received_spec = {"timeout": 3}
        received_metadata = {"uid": "abc-123"}
        defaults, response_only = _compare_payloads(sent, received_spec, received_metadata)
        assert "metadata.uid" in response_only
