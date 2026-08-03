#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Automated acronym normalization for API specification text fields."""

import re
from pathlib import Path
from typing import Any

import yaml

from scripts.utils.technical_text import replace_many_outside_technical_spans


class AcronymNormalizer:
    """Normalizes acronyms to consistent casing in API specification text.

    Fully automated - no manual intervention required.
    Loads rules from config/acronyms.yaml.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize with acronym mappings from config file.

        Args:
            config_path: Path to acronyms.yaml config. Defaults to config/acronyms.yaml.
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / "config" / "acronyms.yaml"

        self.acronyms: dict[str, str] = {}
        self.exceptions: set[str] = set()
        self._compiled_patterns: list[tuple[re.Pattern, str]] = []

        self._load_config(config_path)
        self._compile_patterns()

    def _load_config(self, config_path: Path) -> None:
        """Load acronym mappings and exceptions from YAML config."""
        if not config_path.exists():
            return

        with config_path.open() as f:
            config = yaml.safe_load(f) or {}

        self.acronyms = config.get("acronyms", {})
        self.exceptions = set(config.get("exceptions", []))

    def _compile_patterns(self) -> None:
        """Pre-compile regex patterns for efficient matching."""
        for lowercase_form, normalized_form in self.acronyms.items():
            # Match word boundaries, case-insensitive
            pattern = re.compile(rf"\b{re.escape(lowercase_form)}\b", re.IGNORECASE)
            self._compiled_patterns.append((pattern, normalized_form))

        # Sort by length (longest first) to handle overlapping patterns
        self._compiled_patterns.sort(key=lambda x: len(x[1]), reverse=True)

    def normalize_text(
        self,
        text: str,
        *,
        path: str = "",
        container: dict[str, Any] | None = None,
    ) -> str:
        """Normalize acronyms in a text string.

        Args:
            text: Input text with potentially inconsistent acronym casing.
            path: Structural path of the text field in the OpenAPI document.
            container: Mapping that owns the text field.

        Returns:
            Text with normalized acronym casing.
        """
        if not text or not isinstance(text, str):
            return text

        rules = []
        for pattern, replacement in self._compiled_patterns:

            def replace_match(match: re.Match, repl: str = replacement) -> str:
                matched_word = match.group(0)
                # Skip if this word is in exceptions list
                if matched_word.lower() in self.exceptions:
                    return matched_word
                return repl

            rules.append((pattern, replace_match))

        return replace_many_outside_technical_spans(
            text,
            rules,
            path=path,
            container=container,
        )

    def normalize_spec(
        self,
        spec: dict[str, Any],
        target_fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Recursively normalize acronyms in an OpenAPI specification.

        Args:
            spec: OpenAPI specification dictionary.
            target_fields: List of field names to process. Defaults to common text fields.

        Returns:
            Specification with normalized acronyms in target fields.
        """
        if target_fields is None:
            target_fields = ["description", "summary", "x-displayname"]

        return self._normalize_recursive(spec, target_fields)

    def _normalize_recursive(
        self,
        obj: Any,
        target_fields: list[str],
        current_path: str = "",
    ) -> Any:
        """Recursively process object and normalize text fields."""
        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                new_path = f"{current_path}.{key}" if current_path else key
                if key in target_fields and isinstance(value, str):
                    result[key] = self.normalize_text(
                        value,
                        path=new_path,
                        container=obj,
                    )
                else:
                    result[key] = self._normalize_recursive(value, target_fields, new_path)
            return result
        if isinstance(obj, list):
            return [
                self._normalize_recursive(item, target_fields, f"{current_path}[{index}]")
                for index, item in enumerate(obj)
            ]
        return obj

    def get_stats(self) -> dict[str, int]:
        """Return statistics about loaded acronym rules."""
        return {
            "acronym_count": len(self.acronyms),
            "exception_count": len(self.exceptions),
            "pattern_count": len(self._compiled_patterns),
        }
