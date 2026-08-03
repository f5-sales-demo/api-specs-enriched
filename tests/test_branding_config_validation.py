"""Fail-closed configuration tests for branding transformations."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from scripts.utils.branding import (
    BrandingConfigError,
    BrandingNormalizer,
    BrandingTransformer,
)

ROOT = Path(__file__).parents[1]
ENRICHMENT_CONFIG = ROOT / "config" / "enrichment.yaml"
NORMALIZER_CONFIG = ROOT / "config" / "branding.yaml"


def _load(path: Path) -> dict:
    document = yaml.safe_load(path.read_text())
    assert isinstance(document, dict)
    return document


def _write(tmp_path: Path, name: str, document: object) -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    return path


def test_current_configs_compile_every_rule() -> None:
    transformer = BrandingTransformer(ENRICHMENT_CONFIG)
    normalizer = BrandingNormalizer(NORMALIZER_CONFIG)

    assert transformer.get_stats() == {
        "replacement_count": 36,
        "pattern_count": 36,
        "protected_pattern_count": 3,
        "preserve_field_count": 8,
    }
    assert len(normalizer.transformations) == len(normalizer._compiled_patterns) == 5


def test_protected_capturing_group_does_not_duplicate_url_text() -> None:
    transformer = BrandingTransformer(ENRICHMENT_CONFIG)
    protected_url = "https://tenant.console.ves.volterra.io"

    assert transformer.transform_text(protected_url, "description") == protected_url
    assert (
        transformer.transform_text(
            f"Use {protected_url} for Volterra.",
            "description",
        )
        == f"Use {protected_url} for F5 Distributed Cloud."
    )


@pytest.mark.parametrize("factory", [BrandingTransformer, BrandingNormalizer])
def test_missing_config_is_fatal(tmp_path: Path, factory) -> None:
    missing = tmp_path / "missing.yaml"
    with pytest.raises(BrandingConfigError, match=r"cannot be read.*missing\.yaml"):
        factory(missing)


@pytest.mark.parametrize("factory", [BrandingTransformer, BrandingNormalizer])
def test_malformed_yaml_is_fatal(tmp_path: Path, factory) -> None:
    path = tmp_path / "malformed.yaml"
    path.write_text("branding: [")
    with pytest.raises(BrandingConfigError, match="malformed YAML"):
        factory(path)


@pytest.mark.parametrize(
    ("factory", "text"),
    [
        (
            BrandingTransformer,
            "branding: {}\nbranding: {}\npreserve_fields: [operationId]\n",
        ),
        (
            BrandingNormalizer,
            "version: 1.0.0\nversion: 2.0.0\n",
        ),
    ],
)
def test_duplicate_yaml_key_is_fatal(tmp_path: Path, factory, text: str) -> None:
    path = tmp_path / "duplicate.yaml"
    path.write_text(text)
    with pytest.raises(BrandingConfigError, match="duplicate key"):
        factory(path)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda doc: doc.pop("branding"), "missing=.*branding"),
        (lambda doc: doc.pop("preserve_fields"), "missing=.*preserve_fields"),
        (lambda doc: doc.__setitem__("unknown", {}), "unknown=.*unknown"),
        (lambda doc: doc["branding"].__setitem__("unknown", []), "unknown=.*unknown"),
        (
            lambda doc: doc["branding"]["protected_patterns"].__setitem__(0, "["),
            "protected_patterns\\[0\\].*invalid regex",
        ),
        (
            lambda doc: doc["branding"]["replacements"][1].__setitem__("pattern", "["),
            "replacements\\[1\\].*invalid regex",
        ),
        (
            lambda doc: doc["branding"]["replacements"][1].__setitem__("replacement", r"\99"),
            "replacements\\[1\\].*invalid regex or replacement",
        ),
        (
            lambda doc: doc["branding"]["replacements"][1].__setitem__("case_sensitive", "false"),
            "case_sensitive must be a boolean",
        ),
        (
            lambda doc: doc["branding"]["replacements"][1].__setitem__("unknown", True),
            "unknown=.*unknown",
        ),
        (lambda doc: doc.__setitem__("preserve_fields", "operationId"), "non-empty list"),
    ],
)
def test_transformer_config_mutations_fail(tmp_path: Path, mutate, match: str) -> None:
    document = _load(ENRICHMENT_CONFIG)
    mutate(document)
    path = _write(tmp_path, "enrichment.yaml", document)

    with pytest.raises(BrandingConfigError, match=match):
        BrandingTransformer(path)


@pytest.mark.parametrize(
    ("mutate", "match"),
    [
        (lambda doc: doc.pop("canonical"), "missing=.*canonical"),
        (lambda doc: doc.__setitem__("unknown", {}), "unknown=.*unknown"),
        (
            lambda doc: doc["canonical"]["managed_kubernetes"].pop("long_form"),
            "canonical.managed_kubernetes.*missing=.*long_form",
        ),
        (
            lambda doc: doc["canonical"]["managed_kubernetes"].__setitem__("unknown", True),
            "canonical.managed_kubernetes.*unknown=.*unknown",
        ),
        (
            lambda doc: doc["transformations"][1].__setitem__("pattern", "["),
            "transformations\\[1\\].*invalid regex",
        ),
        (
            lambda doc: doc["transformations"][1].__setitem__("replacement", r"\99"),
            "transformations\\[1\\].*invalid regex or replacement",
        ),
        (
            lambda doc: doc["transformations"][1].__setitem__("context", "info.description"),
            "context must be a non-empty list",
        ),
        (
            lambda doc: doc["transformations"][1].__setitem__("unknown", True),
            "transformations\\[1\\].*unknown=.*unknown",
        ),
        (
            lambda doc: doc["glossary"]["CE"].pop("definition"),
            "glossary.CE.*missing=.*definition",
        ),
        (
            lambda doc: doc["domain_branding"]["sites"].__setitem__("title", 7),
            "domain_branding.sites.*non-empty strings",
        ),
        (
            lambda doc: doc["deprecations"].__setitem__("unknown", {}),
            "deprecations.*unknown=.*unknown",
        ),
        (
            lambda doc: doc["deprecations"]["cli"]["canonical"].pop("command"),
            "deprecations.cli.canonical.*missing=.*command",
        ),
    ],
)
def test_normalizer_config_mutations_fail(tmp_path: Path, mutate, match: str) -> None:
    document = copy.deepcopy(_load(NORMALIZER_CONFIG))
    mutate(document)
    path = _write(tmp_path, "branding.yaml", document)

    with pytest.raises(BrandingConfigError, match=match):
        BrandingNormalizer(path)
