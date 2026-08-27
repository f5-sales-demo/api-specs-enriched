"""Schema Override Enricher — injects missing properties from schema_overrides.yaml."""

from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "schema_overrides.yaml"
ISSUE_REFERENCE = re.compile(r"^(?:[\w.-]+/[\w.-]+)?#\d+$")


@dataclass(frozen=True)
class PropertyRemovalResult:
    """Counts produced while removing one property and its schema-local references."""

    properties_removed: int = 0
    metadata_references_removed: int = 0
    oneof_groups_removed: int = 0


def _property_reference(value: object, property_name: str) -> bool:
    """Return whether a scalar is a schema-local reference to ``property_name``."""
    return isinstance(value, str) and value in {property_name, f"spec.{property_name}"}


def _decode_sequence(value: object) -> tuple[list[Any] | None, bool]:
    """Decode list-valued metadata while retaining whether it was JSON text."""
    if isinstance(value, list):
        return value, False
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None, True
        if isinstance(parsed, list):
            return parsed, True
    return None, False


def _encode_sequence(values: list[Any], was_string: bool) -> list[Any] | str:
    """Re-encode a metadata sequence without changing list versus string shape."""
    return json.dumps(values, separators=(",", ":")) if was_string else values


def _clean_structured_value(value: Any, property_name: str) -> tuple[Any, int]:
    """Remove property paths and keys from structured schema metadata."""
    removed = 0
    if isinstance(value, dict):
        cleaned: dict[Any, Any] = {}
        for key, item in value.items():
            if _property_reference(key, property_name) or key == property_name:
                removed += 1
                continue
            cleaned_item, item_removed = _clean_structured_value(item, property_name)
            cleaned[key] = cleaned_item
            removed += item_removed
        return cleaned, removed
    if isinstance(value, list):
        cleaned_list = []
        for item in value:
            if _property_reference(item, property_name):
                removed += 1
                continue
            cleaned_item, item_removed = _clean_structured_value(item, property_name)
            cleaned_list.append(cleaned_item)
            removed += item_removed
        return cleaned_list, removed
    return value, 0


def _clean_serialized_example(
    value: object, property_name: str, *, yaml_text: bool
) -> tuple[object, int]:
    """Remove a property from JSON/YAML example text and deterministically re-encode it."""
    if not isinstance(value, str):
        return value, 0
    try:
        parsed = yaml.safe_load(value) if yaml_text else json.loads(value)
    except (json.JSONDecodeError, yaml.YAMLError):
        return value, 0
    cleaned, removed = _clean_structured_value(parsed, property_name)
    if not removed:
        return value, 0
    if yaml_text:
        return yaml.safe_dump(cleaned, sort_keys=False).rstrip(), removed
    return json.dumps(cleaned, indent=2, ensure_ascii=False), removed


def remove_schema_property(
    schema: dict[str, Any],
    property_name: str,
    *,
    require_present: bool = True,
) -> PropertyRemovalResult:
    """Remove one property plus requiredness, choices, conflicts, and examples.

    The function is deliberately shared with the contract-diff gate. Applying the
    identical transformation to its input graph makes the declared breaking change
    exact: an unrelated deletion remains visible, while every deterministic cleanup
    caused by the declared property removal compares equal.
    """
    properties = schema.get("properties")
    present = isinstance(properties, dict) and property_name in properties
    if require_present and not present:
        raise KeyError(property_name)

    properties_removed = 0
    references_removed = 0
    oneof_groups_removed = 0
    if present and isinstance(properties, dict):
        del properties[property_name]
        properties_removed = 1

    required = schema.get("required")
    if isinstance(required, list):
        cleaned_required = [item for item in required if item != property_name]
        references_removed += len(required) - len(cleaned_required)
        if cleaned_required:
            schema["required"] = cleaned_required
        elif cleaned_required != required:
            del schema["required"]

    for key in list(schema):
        if not key.startswith("x-ves-oneof-field-"):
            continue
        variants, was_string = _decode_sequence(schema[key])
        if variants is None or property_name not in variants:
            continue
        cleaned_variants = [variant for variant in variants if variant != property_name]
        references_removed += len(variants) - len(cleaned_variants)
        if len(cleaned_variants) < 2:
            del schema[key]
            oneof_groups_removed += 1
        else:
            schema[key] = _encode_sequence(cleaned_variants, was_string)

    if isinstance(properties, dict):
        for property_schema in properties.values():
            if not isinstance(property_schema, dict):
                continue
            key = "x-f5xc-conflicts-with"
            conflicts, was_string = _decode_sequence(property_schema.get(key))
            if conflicts is None:
                continue
            cleaned_conflicts = [
                conflict
                for conflict in conflicts
                if not _property_reference(conflict, property_name)
            ]
            removed = len(conflicts) - len(cleaned_conflicts)
            if not removed:
                continue
            references_removed += removed
            if cleaned_conflicts:
                property_schema[key] = _encode_sequence(cleaned_conflicts, was_string)
            else:
                del property_schema[key]

    metadata_keys = {
        "x-f5xc-minimum-configuration",
        "x-f5xc-field-examples",
        "example",
        "examples",
        "x-example",
        "x-examples",
        "x-f5xc-example",
        "x-f5xc-examples",
    }
    for key in metadata_keys & schema.keys():
        value = schema[key]
        if key == "x-f5xc-minimum-configuration" and isinstance(value, dict):
            cleaned_metadata: dict[str, Any] = {}
            for metadata_key, metadata_value in value.items():
                if metadata_key in {"example_yaml", "example_json"}:
                    cleaned_value, removed = _clean_serialized_example(
                        metadata_value,
                        property_name,
                        yaml_text=metadata_key.endswith("yaml"),
                    )
                else:
                    cleaned_value, removed = _clean_structured_value(metadata_value, property_name)
                cleaned_metadata[metadata_key] = cleaned_value
                references_removed += removed

            groups = cleaned_metadata.get("mutually_exclusive_groups")
            if isinstance(groups, list):
                valid_groups = []
                for group in groups:
                    fields = group.get("fields") if isinstance(group, dict) else None
                    if isinstance(fields, list) and len(fields) < 2:
                        references_removed += 1
                        continue
                    valid_groups.append(group)
                cleaned_metadata["mutually_exclusive_groups"] = valid_groups
            schema[key] = cleaned_metadata
            continue

        cleaned, removed = _clean_structured_value(value, property_name)
        if removed:
            schema[key] = cleaned
            references_removed += removed

    return PropertyRemovalResult(
        properties_removed=properties_removed,
        metadata_references_removed=references_removed,
        oneof_groups_removed=oneof_groups_removed,
    )


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
        for entry_name, entry in self.overrides.items():
            canonical = entry.get("canonical") is True
            issue = entry.get("upstream_issue")
            compiled.extend(
                self._compile_single(
                    schema_entry,
                    canonical=canonical,
                    issue=issue,
                    entry_name=entry_name,
                )
                for schema_entry in entry.get("schemas", [])
            )
        return [c for c in compiled if c is not None]

    @staticmethod
    def _compile_single(
        schema_entry: dict,
        *,
        canonical: bool = False,
        issue: object = None,
        entry_name: str = "<unknown>",
    ) -> dict | None:
        remove_properties = schema_entry.get("remove_properties", [])
        if remove_properties:
            valid_properties = isinstance(remove_properties, list) and all(
                isinstance(item, str) and item for item in remove_properties
            )
            if (
                not valid_properties
                or not canonical
                or not isinstance(issue, str)
                or ISSUE_REFERENCE.fullmatch(issue) is None
            ):
                message = (
                    f"schema override {entry_name!r}: remove_properties must be "
                    "canonical and issue-linked"
                )
                raise ValueError(message)
        try:
            return {
                "regex": re.compile(schema_entry["pattern"]),
                "pattern": schema_entry["pattern"],
                "canonical": canonical,
                "issue": issue,
                "oneof_group": schema_entry.get("oneof_group"),
                "complete_variants": schema_entry.get("complete_variants", []),
                "remove_properties": remove_properties,
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
            "properties_removed": 0,
            "property_removals_missed": 0,
            "property_metadata_references_removed": 0,
            "oneof_groups_removed": 0,
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
        removals_only: bool = False,
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

        for schema_name, schema_value in list(schemas.items()):
            # Domain fan-out deliberately reuses source component objects. A
            # destructive override must not leak through that alias into a later
            # projection, where the fail-closed target check would then see an
            # already-mutated schema. Isolate only removal targets; additive and
            # correction behavior remains byte-for-byte unchanged.
            if not corrections_only and self._matches_property_removal(
                schema_name,
                canonical_only=canonical_only,
            ):
                schema = copy.deepcopy(schema_value)
                schemas[schema_name] = schema
            else:
                schema = schema_value
            self._stats["schemas_processed"] += 1
            self._apply_overrides(
                schema_name,
                schema,
                corrections_only=corrections_only,
                canonical_only=canonical_only,
                removals_only=removals_only,
            )

        return spec

    def _matches_property_removal(self, schema_name: str, *, canonical_only: bool) -> bool:
        """Return whether this pass will remove a property from ``schema_name``."""
        return any(
            override["remove_properties"]
            and (not canonical_only or override["canonical"])
            and override["regex"].search(schema_name)
            for override in self._compiled
        )

    def _apply_overrides(
        self,
        schema_name: str,
        schema: dict,
        corrections_only: bool = False,
        canonical_only: bool = False,
        removals_only: bool = False,
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

            for prop_name in override["remove_properties"]:
                try:
                    removal = remove_schema_property(schema, prop_name)
                except KeyError as error:
                    self._stats["property_removals_missed"] += 1
                    self._miss(schema_name, prop_name, "remove property")
                    message = (
                        f"schema_overrides: declared removal target "
                        f"{schema_name}.{prop_name} is absent"
                    )
                    raise ValueError(message) from error
                self._stats["properties_removed"] += removal.properties_removed
                self._stats["property_metadata_references_removed"] += (
                    removal.metadata_references_removed
                )
                self._stats["oneof_groups_removed"] += removal.oneof_groups_removed

            if removals_only:
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

    def get_removal_declarations(self) -> list[dict[str, str]]:
        """Return validated canonical property-removal declarations."""
        return [
            {
                "schema_pattern": override["pattern"],
                "property_name": property_name,
                "issue": override["issue"],
            }
            for override in self._compiled
            for property_name in override["remove_properties"]
        ]

    def reset_stats(self) -> None:
        """Reset statistics for next domain iteration."""
        self._stats = self._empty_stats()
