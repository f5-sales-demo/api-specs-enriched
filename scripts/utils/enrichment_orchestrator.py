"""Explicit production source-stage ordering shared by both CLI entrypoints."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import yaml

from scripts.utils import (
    ConstraintEnricher,
    FieldDescriptionEnricher,
    MinimumConfigurationEnricher,
    OperationDescriptionEnricher,
    OperationMetadataEnricher,
    PropertyDescriptionShortEnricher,
    ReadOnlyEnricher,
    ValidationEnricher,
)

SOURCE_STAGES = (
    FieldDescriptionEnricher,
    PropertyDescriptionShortEnricher,
    ValidationEnricher,
    ConstraintEnricher,
    OperationDescriptionEnricher,
    OperationMetadataEnricher,
    MinimumConfigurationEnricher,
    ReadOnlyEnricher,
)


def run_source_stages(spec: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute every active source stage in declared order, collecting statistics."""
    statistics = {}
    for stage in SOURCE_STAGES:
        factory: Any = stage
        enricher = (
            factory(config_path=Path("config/constraint_patterns.yaml"))
            if stage is ConstraintEnricher
            else factory()
        )
        spec = enricher.enrich_spec(spec)
        statistics[stage.__name__] = enricher.get_stats()
    return spec, statistics


def validate_stage_inventory() -> None:
    """Reject implemented enrichers with no explicit lifecycle disposition."""
    registry = yaml.safe_load(Path("config/enrichment_stages.yaml").read_text())["stages"]
    implemented = set()
    for path in sorted(Path("scripts/utils").glob("*.py")):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.ClassDef) and (
                node.name.endswith("Enricher") or node.name == "BrandingNormalizer"
            ):
                implemented.add(node.name)
    if implemented != set(registry):
        raise ValueError(
            f"Enricher lifecycle inventory drift: missing={sorted(implemented - set(registry))}, stale={sorted(set(registry) - implemented)}"
        )
    for name, entry in registry.items():
        if entry.get("status") not in {"active", "superseded", "retired", "standalone"}:
            raise ValueError(f"Invalid enricher lifecycle: {name}")
        for key in (
            "phase",
            "producer",
            "outputs",
            "configuration",
            "statistics",
            "artifacts",
            "disposition",
            "owner",
        ):
            if key not in entry:
                raise ValueError(f"Enricher {name} lacks {key}")
    declared = {
        name
        for name, entry in registry.items()
        if entry["status"] == "active" and entry["phase"] == "source"
    }
    executed = {stage.__name__ for stage in SOURCE_STAGES}
    if declared != executed:
        raise ValueError(
            f"Active source stage drift: declared={sorted(declared)}, executed={sorted(executed)}"
        )
