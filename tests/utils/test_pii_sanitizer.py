"""Tests for generated-artifact PII sanitization."""

from scripts.utils.pii_sanitizer import sanitize_discovery_payload, sanitize_emails


def test_sanitize_emails_preserves_reserved_domains() -> None:
    value = {
        "safe": "dana@example.org",
        "unsafe": "person@" + "customer.invalid",
        "nested": ["Contact person@" + "customer.invalid for access."],
        "short": "x@" + "customer.invalid",
    }

    assert sanitize_emails(value) == {
        "safe": "dana@example.org",
        "unsafe": "dana@example.com",
        "nested": ["Contact dana@example.com for access."],
        "short": "dana@example.com",
    }


def test_sanitize_discovery_payload_replaces_live_identity_values() -> None:
    captured_namespace = "captured-" + "namespace"
    captured_tenant = "captured-" + "tenant"
    captured_name = "Cap" + "tured"
    captured_zip = "123" + "45"
    value = {
        "metadata": {
            "namespace": captured_namespace,
            "tenant": captured_tenant,
            "first_name": captured_name,
            "zip_code": captured_zip,
        },
        "customer": False,
        "schema": {"namespace": {"type": "string"}},
    }

    assert sanitize_discovery_payload(value) == {
        "metadata": {
            "namespace": "default",
            "tenant": "example-corp",
            "first_name": "Example",
            "zip_code": "90210",
        },
        "customer": False,
        "schema": {"namespace": {"type": "string"}},
    }
