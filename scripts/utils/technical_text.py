# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Structural protection for wire-significant text embedded in API prose."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

TextReplacement = str | Callable[[re.Match[str]], str]
TextRule = tuple[re.Pattern[str], TextReplacement]

API_EXAMPLE_FIELDS = frozenset({"example", "examples", "x-f5xc-example", "x-ves-example"})

_ABSOLUTE_HTTP_URI = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_API_PATH = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"/api/(?:config|data)/[^\s<>\"'`),;]+",
    re.IGNORECASE,
)
_OPENAPI_REFERENCE = re.compile(
    r"(?:\$ref|#/components/(?:schemas|responses|parameters|requestBodies)/[^\s<>\"'`),;]+)",
    re.IGNORECASE,
)
_DNS_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_DNS_HOSTNAME = re.compile(
    rf"(?<![A-Za-z0-9.-])(?:{_DNS_LABEL}\.)+"
    r"(?:[A-Za-z]{2,63}|[Ii]/[Oo])"
    r"(?![A-Za-z0-9-]|\.[A-Za-z0-9])",
)
_MULTILABEL_DNS_HOSTNAME = re.compile(
    rf"(?<![A-Za-z0-9.-])(?:{_DNS_LABEL}\.){{2,}}"
    r"(?:[A-Za-z]{2,63}|[Ii]/[Oo])"
    r"(?![A-Za-z0-9-]|\.[A-Za-z0-9])",
)
_PROTO_SCHEMA_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9_./-])"
    r"[A-Za-z_][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_/-]+)*"
    r"\.schema(?:\.[A-Za-z0-9_/-]+)+"
    r"(?![A-Za-z0-9_/-])",
    re.IGNORECASE,
)
_QUALIFIED_LABEL_KEY = re.compile(
    rf"(?<![A-Za-z0-9./-])(?:{_DNS_LABEL}\.)+"
    r"[A-Za-z]{2,63}/[A-Za-z0-9](?:[-_.A-Za-z0-9]*[A-Za-z0-9])?"
    r"(?![-_A-Za-z0-9]|\.[-_.A-Za-z0-9])",
    re.IGNORECASE,
)
_X_CODE_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_-])x-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+"
    r"(?![A-Za-z0-9_-])",
    re.IGNORECASE,
)
_SERVER_VARIABLE_DESCRIPTION = re.compile(
    r"(?:^|\.)servers(?:\[\d+\])?\.variables\.[^.]+\.description$",
)


def prose_target_fields(target_fields: Iterable[str]) -> list[str]:
    """Return configured text fields that contain prose rather than API values."""
    return [field for field in target_fields if field not in API_EXAMPLE_FIELDS]


def merge_spans(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Return ordered non-overlapping spans."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(set(spans)):
        if merged and start <= merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def _declared_parameter_value_spans(
    text: str,
    container: dict[str, Any] | None,
) -> list[tuple[int, int]]:
    """Locate documented values declared by the surrounding parameter contract."""
    if not isinstance(container, dict) or container.get("in") not in {
        "cookie",
        "header",
        "path",
        "query",
    }:
        return []

    declared: list[str] = []
    for key in ("example", "x-f5xc-example"):
        value = container.get(key)
        if isinstance(value, str) and value:
            declared.append(value)

    schema = container.get("schema")
    if isinstance(schema, dict):
        for key in ("default", "example"):
            value = schema.get(key)
            if isinstance(value, str) and value:
                declared.append(value)
        enum_values = schema.get("enum")
        if isinstance(enum_values, list):
            declared.extend(value for value in enum_values if isinstance(value, str) and value)

    spans: list[tuple[int, int]] = []
    for value in set(declared):
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_-]){re.escape(value)}(?![A-Za-z0-9_-])",
            re.IGNORECASE,
        )
        spans.extend((match.start(), match.end()) for match in pattern.finditer(text))
    return spans


def immutable_technical_spans(
    text: str,
    *,
    path: str = "",
    container: dict[str, Any] | None = None,
) -> list[tuple[int, int]]:
    """Identify immutable identifiers by syntax and OpenAPI structure.

    Three-or-more-label hostnames are unambiguously hostnames in ordinary prose.
    Two-label names such as ``ves.io`` are protected only where OpenAPI structure
    declares a hostname; elsewhere they can be genuine legacy branding prose.
    """
    spans = [
        (match.start(), match.end())
        for pattern in (
            _ABSOLUTE_HTTP_URI,
            _API_PATH,
            _OPENAPI_REFERENCE,
            _MULTILABEL_DNS_HOSTNAME,
            _PROTO_SCHEMA_IDENTIFIER,
            _QUALIFIED_LABEL_KEY,
            _X_CODE_TOKEN,
        )
        for match in pattern.finditer(text)
    ]
    if _SERVER_VARIABLE_DESCRIPTION.search(path) or (
        isinstance(container, dict) and container.get("format") == "hostname"
    ):
        spans.extend((match.start(), match.end()) for match in _DNS_HOSTNAME.finditer(text))
    spans.extend(_declared_parameter_value_spans(text, container))
    return merge_spans(spans)


def replace_outside_technical_spans(
    text: str,
    pattern: re.Pattern[str],
    replacement: TextReplacement,
    *,
    path: str = "",
    container: dict[str, Any] | None = None,
    additional_protected_patterns: Iterable[re.Pattern[str]] = (),
) -> str:
    """Apply one replacement only to prose outside immutable spans."""
    spans = immutable_technical_spans(text, path=path, container=container)
    spans.extend(
        (match.start(), match.end())
        for protected_pattern in additional_protected_patterns
        for match in protected_pattern.finditer(text)
    )
    merged = merge_spans(spans)
    if not merged:
        return pattern.sub(replacement, text)

    result: list[str] = []
    cursor = 0
    for start, end in merged:
        result.append(pattern.sub(replacement, text[cursor:start]))
        result.append(text[start:end])
        cursor = end
    result.append(pattern.sub(replacement, text[cursor:]))
    return "".join(result)


def replace_many_outside_technical_spans(
    text: str,
    rules: Iterable[TextRule],
    *,
    path: str = "",
    container: dict[str, Any] | None = None,
    additional_protected_patterns: Iterable[re.Pattern[str]] = (),
) -> str:
    """Apply ordered replacements after classifying immutable spans once.

    Protection is derived from the original input. Each unprotected prose
    segment then receives every rule in order, so replacements may change
    length without invalidating offsets for neighboring immutable segments.
    """
    ordered_rules = tuple(rules)

    def apply_rules(segment: str) -> str:
        result = segment
        for pattern, replacement in ordered_rules:
            result = pattern.sub(replacement, result)
        return result

    spans = immutable_technical_spans(text, path=path, container=container)
    spans.extend(
        (match.start(), match.end())
        for protected_pattern in additional_protected_patterns
        for match in protected_pattern.finditer(text)
    )
    merged = merge_spans(spans)
    if not merged:
        return apply_rules(text)

    result: list[str] = []
    cursor = 0
    for start, end in merged:
        result.append(apply_rules(text[cursor:start]))
        result.append(text[start:end])
        cursor = end
    result.append(apply_rules(text[cursor:]))
    return "".join(result)
