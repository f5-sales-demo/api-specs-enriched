"""Schema Override Enricher — injects missing properties from schema_overrides.yaml."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "schema_overrides.yaml"


class SchemaOverrideEnricher:
    """Applies the local corrections declared in schema_overrides.yaml.

    Runs during the merge phase, before ConflictsWithEnricher, so that
    x-ves-oneof-field-* arrays are complete when conflicts-with derivation runs.

    Three kinds of correction:

    * ``inject_properties`` / ``inject_extensions`` add what upstream omits. Both
      are additive only — an existing property or extension is left alone.
    * ``set_property_extensions`` / ``remove_property_extensions`` correct an
      extension on a property that already exists. Needed because
      ``x-ves-required`` is F5's own upstream marker and is wrong in both
      directions (#1142): it appears on fields the API does not enforce, and is
      absent from fields it does.

    A property named by an override but absent from the schema is counted in
    ``property_overrides_missed`` rather than ignored, so a typo shows up as a
    number instead of as a correction everyone believes has been applied.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize with optional config path override."""
        self.config_path = Path(config_path) if config_path else CONFIG_PATH
        config = self._load_config()
        self.overrides: dict[str, Any] = config.get("overrides", {})
        self._compiled: list[dict] = self._compile_overrides()
        self._stats = self._empty_stats()

    def _load_config(self) -> dict:
        if not self.config_path.exists():
            logger.warning("schema_overrides.yaml not found at %s", self.config_path)
            return {}
        with self.config_path.open() as f:
            return yaml.safe_load(f) or {}

    def _compile_overrides(self) -> list[dict]:
        compiled: list[dict | None] = []
        for entry in self.overrides.values():
            compiled.extend(
                self._compile_single(schema_entry, canonical=entry.get("canonical") is True)
                for schema_entry in entry.get("schemas", [])
            )
        return [c for c in compiled if c is not None]

    @staticmethod
    def _compile_single(schema_entry: dict, *, canonical: bool = False) -> dict | None:
        try:
            return {
                "regex": re.compile(schema_entry["pattern"]),
                "canonical": canonical,
                "oneof_group": schema_entry.get("oneof_group"),
                "complete_variants": schema_entry.get("complete_variants", []),
                "inject_properties": schema_entry.get("inject_properties", {}),
                "inject_extensions": schema_entry.get("inject_extensions", {}),
                "set_property_extensions": schema_entry.get("set_property_extensions", {}),
                "remove_property_extensions": schema_entry.get("remove_property_extensions", {}),
                "remove_property_extension_keys": schema_entry.get(
                    "remove_property_extension_keys", {}
                ),
                "set_property_extension_keys": schema_entry.get("set_property_extension_keys", {}),
            }
        except re.error as e:
            logger.warning("Invalid override pattern '%s': %s", schema_entry.get("pattern"), e)
            return None

    @staticmethod
    def _empty_stats() -> dict[str, int]:
        return {
            "schemas_processed": 0,
            "schemas_matched": 0,
            "properties_injected": 0,
            "oneof_arrays_updated": 0,
            "property_extensions_set": 0,
            "property_extensions_removed": 0,
            "property_extension_keys_removed": 0,
            "property_extension_keys_set": 0,
            "property_overrides_missed": 0,
            "error_count": 0,
        }

    def enrich_spec(
        self,
        spec: dict,
        corrections_only: bool = False,
        canonical_only: bool = False,
    ) -> dict:
        """Apply the overrides declared in schema_overrides.yaml.

        corrections_only restricts the pass to the property-key corrections
        (set/remove_property_extensions, remove_property_extension_keys) and skips
        the additive halves. That is what the enrich phase needs: requiredness has
        to be corrected before anything derives from it, but injecting a property
        that early would then run it through the whole enrichment chain and change
        its shape — an injected bare $ref came back wrapped in allOf, which
        downstream codegen special-cases. So injection stays in the merge phase,
        where it has always been. canonical_only restricts the pass to overrides
        explicitly reviewed as safe for the provider-facing canonical contract.
        """
        if corrections_only and canonical_only:
            raise ValueError("corrections_only and canonical_only are mutually exclusive")

        schemas = spec.get("components", {}).get("schemas")
        if not schemas:
            return spec

        for schema_name, schema in schemas.items():
            self._stats["schemas_processed"] += 1
            self._apply_overrides(
                schema_name,
                schema,
                corrections_only=corrections_only,
                canonical_only=canonical_only,
            )

        return spec

    def _apply_overrides(
        self,
        schema_name: str,
        schema: dict,
        corrections_only: bool = False,
        canonical_only: bool = False,
    ) -> None:
        for override in self._compiled:
            if canonical_only and not override["canonical"]:
                continue
            if not override["regex"].search(schema_name):
                continue
            self._stats["schemas_matched"] += 1

            props = schema.get("properties", {})

            if corrections_only:
                self._apply_property_extensions(schema_name, props, override)
                continue

            for prop_name, prop_def in override["inject_properties"].items():
                if prop_name not in props:
                    props[prop_name] = dict(prop_def)
                    self._stats["properties_injected"] += 1
            if "properties" not in schema and override["inject_properties"]:
                schema["properties"] = props

            for ext_key, ext_val in override["inject_extensions"].items():
                if ext_key not in schema:
                    schema[ext_key] = ext_val

            self._apply_property_extensions(schema_name, props, override)

            if not override["oneof_group"]:
                continue

            group = override["oneof_group"]
            ext_key = f"x-ves-oneof-field-{group}"
            raw_existing = schema.get(ext_key, [])
            was_string = isinstance(raw_existing, str)
            existing_variants = yaml.safe_load(raw_existing) if was_string else raw_existing
            existing_set = set(existing_variants)
            new_variants = []
            for v in override["complete_variants"]:
                if v not in existing_set:
                    new_variants.append(v)
                    existing_set.add(v)
            if new_variants:
                updated = sorted(existing_set)
                schema[ext_key] = json.dumps(updated) if was_string else updated
                self._stats["oneof_arrays_updated"] += 1

    def _apply_property_extensions(self, schema_name: str, props: dict, override: dict) -> None:
        """Set or remove keys on properties that already exist.

        Unlike inject_*, these overwrite: correcting a wrong marker is the point.

        ``set_property_extensions`` sets any property-level key, not only ``x-``
        ones. ``description`` is a legitimate target: F5 states requiredness in the
        prose as well as in the marker ("Required: YES", present in 3628 property
        descriptions), so a field whose marker is corrected here would otherwise
        keep a sentence asserting the opposite.
        """
        for prop_name, extensions in override["set_property_extensions"].items():
            prop = props.get(prop_name)
            if not isinstance(prop, dict):
                self._miss(schema_name, prop_name, "set")
                continue
            for ext_key, ext_val in extensions.items():
                if prop.get(ext_key) != ext_val:
                    prop[ext_key] = ext_val
                    self._stats["property_extensions_set"] += 1

        for prop_name, ext_keys in override["remove_property_extensions"].items():
            prop = props.get(prop_name)
            if not isinstance(prop, dict):
                self._miss(schema_name, prop_name, "remove")
                continue
            for ext_key in ext_keys:
                if ext_key in prop:
                    del prop[ext_key]
                    self._stats["property_extensions_removed"] += 1

        for prop_name, extensions in override["set_property_extension_keys"].items():
            prop = props.get(prop_name)
            if not isinstance(prop, dict):
                self._miss(schema_name, prop_name, "set keys on")
                continue
            for ext_key, inner in extensions.items():
                container = prop.get(ext_key)
                if not isinstance(container, dict):
                    container = {}
                    prop[ext_key] = container
                for inner_key, inner_val in inner.items():
                    if container.get(inner_key) != inner_val:
                        container[inner_key] = inner_val
                        self._stats["property_extension_keys_set"] += 1

        for prop_name, extensions in override["remove_property_extension_keys"].items():
            prop = props.get(prop_name)
            if not isinstance(prop, dict):
                self._miss(schema_name, prop_name, "remove keys from")
                continue
            for ext_key, inner_keys in extensions.items():
                container = prop.get(ext_key)
                if not isinstance(container, dict):
                    continue
                for inner in inner_keys:
                    if inner in container:
                        del container[inner]
                        self._stats["property_extension_keys_removed"] += 1
                # An extension left as {} still reads as "there are rules here".
                if not container:
                    del prop[ext_key]

    def _miss(self, schema_name: str, prop_name: str, action: str) -> None:
        """Record an override that named a property the schema does not have."""
        self._stats["property_overrides_missed"] += 1
        logger.warning(
            "schema_overrides: cannot %s extensions on '%s.%s' — no such property",
            action,
            schema_name,
            prop_name,
        )

    def get_stats(self) -> dict[str, int]:
        """Return current enrichment statistics."""
        return dict(self._stats)

    def reset_stats(self) -> None:
        """Reset statistics for next domain iteration."""
        self._stats = self._empty_stats()
