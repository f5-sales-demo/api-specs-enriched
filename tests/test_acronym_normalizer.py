"""Deterministic and bounded-cost acronym normalization contracts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from scripts.utils import technical_text
from scripts.utils.acronyms import AcronymNormalizer

if TYPE_CHECKING:
    import re
    from pathlib import Path

    import pytest


def _ordered_segmented_reference(
    normalizer: AcronymNormalizer,
    text: str,
    *,
    path: str = "",
    container: dict | None = None,
) -> str:
    """Independent oracle: classify once, then apply every rule per prose segment."""
    spans = technical_text.immutable_technical_spans(
        text,
        path=path,
        container=container,
    )

    def normalize_segment(segment: str) -> str:
        result = segment
        for pattern, replacement in normalizer._compiled_patterns:

            def replace(match: re.Match[str], repl: str = replacement) -> str:
                if match.group(0).lower() in normalizer.exceptions:
                    return match.group(0)
                return repl

            result = pattern.sub(replace, result)
        return result

    result: list[str] = []
    cursor = 0
    for start, end in spans:
        result.append(normalize_segment(text[cursor:start]))
        result.append(text[start:end])
        cursor = end
    result.append(normalize_segment(text[cursor:]))
    return "".join(result)


def test_normalize_text_classifies_technical_spans_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original = technical_text.immutable_technical_spans

    def count_calls(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(technical_text, "immutable_technical_spans", count_calls)

    AcronymNormalizer().normalize_text(
        "Use ves.io/team-name, x-ves-io-managed, and api io prose.",
    )

    assert calls == 1


def test_one_scan_matches_ordered_segmented_reference(
    tmp_path: Path,
) -> None:
    config = tmp_path / "acronyms.yaml"
    config.write_text(
        """acronyms:
  api: APPLICATION
  io: I/O
  is: IS
  ves: VES
exceptions:
  - is
""",
        encoding="utf-8",
    )
    normalizer = AcronymNormalizer(config)
    text = (
        "api io is mutable; keep ves.io/team-name., x-ves-io-managed, "
        "and https://api.example.com/io; ves-io prose remains mutable."
    )

    expected = _ordered_segmented_reference(normalizer, text)
    actual = normalizer.normalize_text(text)

    assert actual == expected
    assert "APPLICATION I/O is mutable" in actual
    assert "ves.io/team-name." in actual
    assert "x-ves-io-managed" in actual
    assert "https://api.example.com/io" in actual
    assert "VES-I/O prose" in actual


def test_one_scan_normalization_is_idempotent() -> None:
    normalizer = AcronymNormalizer()
    text = (
        "Use ves.io/team-name. Keep tenant.console.ves.volterra.io and "
        "x-ves-io-managed while api io prose is normalized."
    )

    once = normalizer.normalize_text(text)

    assert normalizer.normalize_text(once) == once
