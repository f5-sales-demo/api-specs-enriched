"""Apply reviewed spelling corrections only to human-readable prose fields."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


class ProseSpellingError(ValueError):
    """Raised when a prose correction could overlap an API wire value."""


class ProseSpellingTransformer:
    """Correct reviewed typos without visiting identifiers, examples, refs, or enums."""

    def __init__(self, config_path: Path | None = None) -> None:
        """Load the reviewed correction vocabulary and allowed prose fields."""
        if config_path is None:
            config_path = Path(__file__).parents[2] / "config" / "enrichment.yaml"
        document = yaml.safe_load(config_path.read_text()) or {}
        config = document.get("spelling_corrections", {})
        self.fields = frozenset(config.get("fields", []))
        replacements = config.get("replacements", [])
        if not self.fields or not isinstance(replacements, list):
            raise ProseSpellingError("prose spelling configuration is incomplete")
        self.replacements: tuple[tuple[str, str, re.Pattern[str]], ...] = tuple(
            (
                entry["search"],
                entry["replacement"],
                re.compile(rf"\b{re.escape(entry['search'])}\b"),
            )
            for entry in replacements
        )

    def transform_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Validate correction safety, then mutate only configured prose values."""
        wire_values: list[tuple[str, str]] = []

        def collect(value: Any) -> None:
            if isinstance(value, dict):
                properties = value.get("properties")
                if isinstance(properties, dict):
                    wire_values.extend(("property", name) for name in properties)
                enum = value.get("enum")
                if isinstance(enum, list):
                    wire_values.extend(
                        ("enum", member) for member in enum if isinstance(member, str)
                    )
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        collect(spec)
        for search, _replacement, _pattern in self.replacements:
            collisions = [
                f"{kind}:{value}"
                for kind, value in wire_values
                if search.casefold() in value.casefold()
            ]
            if collisions:
                raise ProseSpellingError(
                    f"spelling correction {search!r} overlaps wire values: {collisions[:3]}"
                )

        def correct(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: self._correct_text(child)
                    if key in self.fields and isinstance(child, str)
                    else correct(child)
                    for key, child in value.items()
                }
            if isinstance(value, list):
                return [correct(child) for child in value]
            return value

        return correct(spec)

    def _correct_text(self, text: str) -> str:
        for _search, replacement, pattern in self.replacements:
            text = pattern.sub(replacement, text)
        return text
