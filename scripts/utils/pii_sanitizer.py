"""Sanitize PII-shaped values before generated artifacts are persisted."""

from __future__ import annotations

import re
from typing import Any

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
    "full_name",
    "first_name",
    "last_name",
    "given_name",
    "family_name",
    "display_name",
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


def sanitize_email_text(value: str) -> str:
    """Replace unsafe emails with distinct fictional reserved addresses."""
    replacement_index = 0

    def replace_email(match: re.Match[str]) -> str:
        nonlocal replacement_index
        email = match.group(0)
        if _safe_email(email):
            return email
        replacement_index += 1
        return f"redacted{replacement_index}@example.com"

    return EMAIL_RE.sub(replace_email, value)


def sanitize_emails(value: Any) -> Any:
    """Recursively sanitize email addresses without changing non-string values."""
    if isinstance(value, dict):
        return {key: sanitize_emails(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_emails(item) for item in value]
    if isinstance(value, str):
        return sanitize_email_text(value)
    return value


def sanitize_discovery_payload(value: Any) -> Any:
    """Replace identities and personal records captured from live discovery."""
    if isinstance(value, dict):
        result: dict[Any, Any] = {}
        for key, item in value.items():
            normalized_key = key.lower() if isinstance(key, str) else ""
            scalar = isinstance(item, (str, int, float)) and not isinstance(item, bool)
            if (
                normalized_key == "namespace"
                and isinstance(item, str)
                and item.lower() in STRUCTURAL_NAMESPACES
            ):
                result[key] = item
            elif normalized_key in IDENTITY_KEYS and scalar:
                result[key] = "default" if normalized_key == "namespace" else "example-corp"
            elif normalized_key in PERSON_KEYS and isinstance(item, str):
                result[key] = "Example"
            elif normalized_key in PERSONAL_RECORD_KEYS and scalar:
                result[key] = (
                    "90210" if normalized_key in {"postal_code", "zip_code"} else "example"
                )
            else:
                result[key] = sanitize_discovery_payload(item)
        return result
    if isinstance(value, list):
        return [sanitize_discovery_payload(item) for item in value]
    if isinstance(value, str):
        return sanitize_email_text(value)
    return value
