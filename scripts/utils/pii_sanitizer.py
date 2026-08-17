"""Sanitize PII-shaped values before generated artifacts are persisted."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

EMAIL_RE = re.compile(
    r"(?<![-A-Za-z0-9._%+/])"
    r"[A-Za-z0-9](?:[-A-Za-z0-9.!#$%&'*+=?^_`{|}~]*[A-Za-z0-9])?@"
    r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}"
)
SAFE_EMAIL_DOMAINS = {"example.com", "example.net", "example.org"}
STRUCTURAL_NAMESPACES = {"shared", "system"}
IDENTITY_KEYS = {
    "tenant",
    "tenant_name",
    "tenant_id",
    "customer",
    "customer_name",
    "customer_id",
    "account",
    "account_name",
    "account_id",
    "subscription",
    "subscription_name",
    "subscription_id",
    "project",
    "project_name",
    "project_id",
    "namespace",
}
PERSON_KEYS = {
    "address",
    "creator",
    "display_name",
    "family_name",
    "full_name",
    "first_name",
    "last_name",
    "given_name",
    "owner",
    "phone",
    "username",
}
PERSONAL_RECORD_KEYS = {
    "street_address",
    "postal_address",
    "postal_code",
    "zip_code",
    "date_of_birth",
    "dob",
    "social_security_number",
    "ssn",
}


def _safe_email(value: str) -> bool:
    return value.rsplit("@", 1)[-1].lower() in SAFE_EMAIL_DOMAINS


def sanitize_email_text(value: str, replacements: dict[str, str] | None = None) -> str:
    """Replace unsafe emails with distinct fictional reserved addresses."""
    replacements = replacements if replacements is not None else {}

    def replace_email(match: re.Match[str]) -> str:
        email = match.group(0)
        if _safe_email(email):
            return email
        normalized_email = email.casefold()
        if normalized_email not in replacements:
            replacements[normalized_email] = f"redacted{len(replacements) + 1}@example.com"
        return replacements[normalized_email]

    return EMAIL_RE.sub(replace_email, value)


def _sanitize_emails(value: Any, replacements: dict[str, str]) -> Any:
    """Recursively sanitize email addresses without changing non-string values."""
    if isinstance(value, dict):
        return {key: _sanitize_emails(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_emails(item, replacements) for item in value]
    if isinstance(value, str):
        return sanitize_email_text(value, replacements)
    return value


def sanitize_emails(value: Any) -> Any:
    """Recursively sanitize emails with stable placeholders per payload."""
    return _sanitize_emails(value, {})


def _sanitize_identity_value(value: Any, replacement: str) -> Any:
    """Preserve a sensitive value's shape without retaining its live identity."""
    if isinstance(value, dict):
        return {
            key: (
                item
                if key.lower() == "namespace"
                and isinstance(item, str)
                and item.lower() in STRUCTURAL_NAMESPACES
                else _sanitize_identity_value(item, replacement)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_identity_value(item, replacement) for item in value]
    if isinstance(value, str):
        return replacement
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return 0
    return replacement


def sanitize_discovery_payload(
    value: Any,
    replacements: dict[str, str] | None = None,
) -> Any:
    """Replace identities and personal records captured from live discovery."""
    replacements = replacements if replacements is not None else {}
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            normalized_key = key.lower() if isinstance(key, str) else ""
            if (
                normalized_key == "namespace"
                and isinstance(item, str)
                and item.lower() in STRUCTURAL_NAMESPACES
            ):
                result[key] = item
            elif (
                normalized_key == "namespace"
                and isinstance(item, dict)
                and any(
                    schema_key in item for schema_key in ("type", "properties", "items", "$ref")
                )
            ):
                result[key] = sanitize_discovery_payload(item, replacements)
            elif normalized_key in IDENTITY_KEYS:
                result[key] = _sanitize_identity_value(
                    item,
                    "default" if normalized_key == "namespace" else "example-corp",
                )
            elif normalized_key in PERSON_KEYS:
                result[key] = _sanitize_identity_value(item, "Example")
            elif normalized_key in PERSONAL_RECORD_KEYS:
                result[key] = _sanitize_identity_value(
                    item,
                    "90210" if normalized_key in {"postal_code", "zip_code"} else "example",
                )
            else:
                result[key] = sanitize_discovery_payload(item, replacements)
        return result
    if isinstance(value, list):
        return [sanitize_discovery_payload(item, replacements) for item in value]
    if isinstance(value, str):
        return sanitize_email_text(value, replacements)
    return value


def sanitize_api_url(value: str) -> str:
    """Replace a live discovery endpoint with a documentation-safe URL."""
    if not value:
        return value
    parsed = urlsplit(value)
    if parsed.hostname and parsed.hostname.lower().endswith(tuple(SAFE_EMAIL_DOMAINS)):
        return value
    return urlunsplit((parsed.scheme or "https", "api.example.com", parsed.path or "", "", ""))
