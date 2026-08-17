"""Tests for generated-artifact PII sanitization."""

from scripts.utils.pii_sanitizer import (
    sanitize_api_url,
    sanitize_discovery_payload,
    sanitize_emails,
)


def test_sanitize_emails_preserves_reserved_domains() -> None:
    value = {
        "safe": "dana@example.org",
        "unsafe": "person@" + "customer.invalid",
        "nested": ["Contact person@" + "customer.invalid for access."],
        "short": "x@" + "customer.invalid",
    }

    assert sanitize_emails(value) == {
        "safe": "dana@example.org",
        "unsafe": "redacted1@example.com",
        "nested": ["Contact redacted1@example.com for access."],
        "short": "redacted2@example.com",
    }


def test_sanitize_emails_preserves_distinct_addresses_in_one_text() -> None:
    value = "Allow first@" + "sample.invalid and second@" + "sample.invalid access."

    assert sanitize_emails(value) == (
        "Allow redacted1@example.com and redacted2@example.com access."
    )


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


def test_sanitize_discovery_payload_preserves_structural_namespaces() -> None:
    captured_namespace = "captured-" + "namespace"
    value = {
        "system_resource": {"namespace": "system"},
        "shared_resource": {"namespace": "shared"},
        "tenant_resource": {"namespace": captured_namespace},
    }

    assert sanitize_discovery_payload(value) == {
        "system_resource": {"namespace": "system"},
        "shared_resource": {"namespace": "shared"},
        "tenant_resource": {"namespace": "default"},
    }


def test_sanitize_emails_preserves_distinct_addresses_across_payload_fields() -> None:
    first_email = "first@" + "sample.invalid"
    second_email = "second@" + "sample.invalid"

    assert sanitize_emails(
        {"owner_email": first_email, "admin_email": second_email, "repeat": first_email},
    ) == {
        "owner_email": "redacted1@example.com",
        "admin_email": "redacted2@example.com",
        "repeat": "redacted1@example.com",
    }


def test_sanitize_discovery_payload_redacts_nested_identity_and_contact_values() -> None:
    captured_tenant = "captured-" + "tenant"
    captured_user = "captured-" + "user"
    value = {
        "tenant": {"name": captured_tenant, "id": 42, "enabled": True},
        "owner": {"username": captured_user, "phone": "12" + "345"},
    }

    assert sanitize_discovery_payload(value) == {
        "tenant": {"name": "example-corp", "id": 0, "enabled": True},
        "owner": {"username": "Example", "phone": "Example"},
    }


def test_sanitize_api_url_replaces_live_hosts_and_preserves_safe_examples() -> None:
    captured_url = "https://" + "captured-tenant" + ".console.example.invalid/api"

    assert sanitize_api_url(captured_url) == "https://api.example.com/api"
    assert sanitize_api_url("https://api.example.com/api") == "https://api.example.com/api"
