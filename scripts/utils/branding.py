#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Automated branding transformations for API specification text fields.

Applies consistent F5 branding by replacing legacy Volterra references
and normalizing product names to current F5 Distributed Cloud API names.
Fully automated - no manual intervention required.

Branding Strategy:
    - AppStack/VoltStack → Managed Kubernetes (current API name)
    - vK8s → Virtual Kubernetes (current API name)
    - No invented acronyms — use product names as they appear in the current API.

Version: v4.0.0 - Removes XCKS/XCCS, uses current API product names
"""

import re
from collections.abc import Set
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import yaml

from scripts.utils.extension_constants import X_F5XC_GLOSSARY
from scripts.utils.technical_text import (
    TextRule,
    immutable_technical_spans,
    replace_many_outside_technical_spans,
)


class BrandingConfigError(ValueError):
    """Raised when branding configuration is missing or structurally invalid."""


class _StrictSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _StrictSafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    """Construct a mapping without last-key-wins behavior."""
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _load_yaml_mapping(config_path: Path, config_name: str) -> dict[str, Any]:
    """Load a required YAML mapping with contextual errors."""
    try:
        text = config_path.read_text()
    except OSError as exc:
        raise BrandingConfigError(
            f"{config_name} configuration {config_path} cannot be read: {exc}",
        ) from exc
    loader = _StrictSafeLoader(text)
    try:
        document = loader.get_single_data()
    except yaml.YAMLError as exc:
        raise BrandingConfigError(
            f"{config_name} configuration {config_path} contains malformed YAML: {exc}",
        ) from exc
    finally:
        loader.dispose()
    if not isinstance(document, dict):
        raise BrandingConfigError(
            f"{config_name} configuration {config_path} must contain a YAML mapping",
        )
    return document


def _require_known_keys(
    value: dict[str, Any],
    *,
    required: Set[str],
    optional: Set[str] = frozenset(),
    context: str,
) -> None:
    """Reject missing and unknown mapping keys."""
    missing = sorted(required - value.keys())
    unknown = sorted(value.keys() - required - optional)
    if missing or unknown:
        raise BrandingConfigError(f"{context} has missing={missing}, unknown={unknown}")


@dataclass
class BrandingStats:
    """Statistics from branding transformations."""

    legacy_terms_replaced: int = 0
    managed_k8s_transformations: int = 0
    virtual_k8s_transformations: int = 0
    glossary_terms_added: int = 0
    files_processed: int = 0
    transformations_by_type: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert stats to dictionary."""
        return {
            "legacy_terms_replaced": self.legacy_terms_replaced,
            "managed_k8s_transformations": self.managed_k8s_transformations,
            "virtual_k8s_transformations": self.virtual_k8s_transformations,
            "glossary_terms_added": self.glossary_terms_added,
            "files_processed": self.files_processed,
            "transformations_by_type": self.transformations_by_type,
        }


class BrandingTransformer:
    """Transforms legacy branding to current F5 branding in API specifications.

    Fully automated branding updates with configurable rules.
    Loads rules from config/enrichment.yaml.
    Respects protected patterns (URLs, schema refs) that should not be transformed.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize with branding rules from config file.

        Args:
            config_path: Path to enrichment.yaml config. Defaults to config/enrichment.yaml.
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / "config" / "enrichment.yaml"

        (
            replacements,
            compiled_patterns,
            protected_patterns,
            preserve_fields,
        ) = self._load_config(config_path)
        self.replacements = replacements
        self._compiled_patterns = compiled_patterns
        self._protected_patterns = protected_patterns
        self._preserve_fields = preserve_fields

    @staticmethod
    def _load_config(
        config_path: Path,
    ) -> tuple[
        list[dict[str, Any]],
        list[tuple[re.Pattern[str], str, str | None]],
        list[re.Pattern[str]],
        set[str],
    ]:
        """Load and atomically compile strict enrichment branding rules."""
        config = _load_yaml_mapping(config_path, "branding transformer")
        _require_known_keys(
            config,
            required={"branding", "preserve_fields"},
            optional={
                "changelog",
                "consistency_validation",
                "deprecated_tiers",
                "description_structure",
                "description_validation",
                "discovery_enrichment",
                "grammar",
                "output",
                "paths",
                "processing",
                "schema_fixes",
                "source",
                "tags",
                "target_fields",
            },
            context=f"branding transformer configuration {config_path}",
        )

        branding = config["branding"]
        if not isinstance(branding, dict):
            raise BrandingConfigError("branding section must be a mapping")
        _require_known_keys(
            branding,
            required={"protected_patterns", "replacements"},
            context="branding section",
        )

        protected = branding["protected_patterns"]
        if not isinstance(protected, list) or not protected:
            raise BrandingConfigError("branding.protected_patterns must be a non-empty list")
        compiled_protected: list[re.Pattern[str]] = []
        for index, pattern_str in enumerate(protected):
            if not isinstance(pattern_str, str) or not pattern_str:
                raise BrandingConfigError(
                    f"branding.protected_patterns[{index}] must be a non-empty string",
                )
            try:
                compiled_protected.append(re.compile(pattern_str))
            except re.error as exc:
                raise BrandingConfigError(
                    f"branding.protected_patterns[{index}] has invalid regex "
                    f"{pattern_str!r}: {exc}",
                ) from exc

        replacements = branding["replacements"]
        if not isinstance(replacements, list) or not replacements:
            raise BrandingConfigError("branding.replacements must be a non-empty list")
        compiled_replacements: list[tuple[re.Pattern[str], str, str | None]] = []
        for index, rule in enumerate(replacements):
            context = f"branding.replacements[{index}]"
            if not isinstance(rule, dict):
                raise BrandingConfigError(f"{context} must be a mapping")
            _require_known_keys(
                rule,
                required={"case_sensitive", "pattern", "replacement"},
                optional={"context"},
                context=context,
            )
            pattern_str = rule["pattern"]
            replacement = rule["replacement"]
            case_sensitive = rule["case_sensitive"]
            field_context = rule.get("context")
            if not isinstance(pattern_str, str) or not pattern_str:
                raise BrandingConfigError(f"{context}.pattern must be a non-empty string")
            if not isinstance(replacement, str):
                raise BrandingConfigError(f"{context}.replacement must be a string")
            if not isinstance(case_sensitive, bool):
                raise BrandingConfigError(f"{context}.case_sensitive must be a boolean")
            if field_context is not None and (
                not isinstance(field_context, str) or not field_context
            ):
                raise BrandingConfigError(f"{context}.context must be a non-empty string")
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                pattern = re.compile(pattern_str, flags)
                pattern.sub(replacement, "")
            except re.error as exc:
                raise BrandingConfigError(
                    f"{context} has invalid regex or replacement: {exc}",
                ) from exc
            compiled_replacements.append((pattern, replacement, field_context))

        preserve_fields = config["preserve_fields"]
        if not isinstance(preserve_fields, list) or not preserve_fields:
            raise BrandingConfigError("preserve_fields must be a non-empty list")
        if any(not isinstance(field, str) or not field for field in preserve_fields):
            raise BrandingConfigError("preserve_fields entries must be non-empty strings")
        if len(set(preserve_fields)) != len(preserve_fields):
            raise BrandingConfigError("preserve_fields must not contain duplicates")

        return (
            replacements,
            compiled_replacements,
            compiled_protected,
            set(preserve_fields),
        )

    def transform_text(
        self,
        text: str,
        field_name: str | None = None,
        *,
        path: str = "",
        container: dict[str, Any] | None = None,
    ) -> str:
        """Apply branding transformations to a text string.

        Respects protected patterns (URLs, schema refs) that should not be modified.

        Args:
            text: Input text with legacy branding.
            field_name: Name of the field being transformed (for context filtering).
            path: Structural path of the text field in the OpenAPI document.
            container: Mapping that owns the text field.

        Returns:
            Text with updated branding.
        """
        if not text or not isinstance(text, str):
            return text

        rules = [
            (pattern, replacement)
            for pattern, replacement, context in self._compiled_patterns
            if context is None or field_name is None or field_name == context
        ]
        if not any(pattern.search(text) for pattern, _ in rules):
            return text

        return replace_many_outside_technical_spans(
            text,
            rules,
            path=path,
            container=container,
            additional_protected_patterns=self._protected_patterns,
        )

    def transform_spec(
        self,
        spec: dict[str, Any],
        target_fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Recursively apply branding transformations to an OpenAPI specification.

        Args:
            spec: OpenAPI specification dictionary.
            target_fields: List of field names to process.

        Returns:
            Specification with updated branding in target fields.
        """
        if target_fields is None:
            target_fields = ["description", "summary", "x-displayname"]

        return self._transform_recursive(spec, target_fields)

    def _transform_recursive(
        self,
        obj: Any,
        target_fields: list[str],
        current_path: str = "",
    ) -> Any:
        """Recursively process object and transform text fields."""
        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                # Skip preserved fields
                if key in self._preserve_fields:
                    result[key] = value
                    continue

                new_path = f"{current_path}.{key}" if current_path else key

                if key in target_fields and isinstance(value, str):
                    result[key] = self.transform_text(
                        value,
                        field_name=key,
                        path=new_path,
                        container=obj,
                    )
                else:
                    result[key] = self._transform_recursive(value, target_fields, new_path)
            return result
        if isinstance(obj, list):
            return [
                self._transform_recursive(item, target_fields, f"{current_path}[{index}]")
                for index, item in enumerate(obj)
            ]
        return obj

    def get_stats(self) -> dict[str, int]:
        """Return statistics about loaded branding rules."""
        return {
            "replacement_count": len(self.replacements),
            "pattern_count": len(self._compiled_patterns),
            "protected_pattern_count": len(self._protected_patterns),
            "preserve_field_count": len(self._preserve_fields),
        }


class BrandingValidator:
    """Validates that branding transformations were applied correctly.

    Checks for remaining legacy branding terms that should have been replaced.
    """

    # Terms that should not appear after branding transformation
    LEGACY_TERMS: ClassVar[list[str]] = [
        r"\bvolterra\b",
        r"\bves\.io\b",
        r"\bVES\b",
    ]

    def __init__(self) -> None:
        """Initialize validator with legacy term patterns."""
        self._legacy_patterns = [
            re.compile(pattern, re.IGNORECASE) for pattern in self.LEGACY_TERMS
        ]

    def validate_text(
        self,
        text: str,
        *,
        path: str = "",
        container: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Check text for remaining legacy branding terms.

        Args:
            text: Text to validate.
            path: Structural path of the text field in the OpenAPI document.
            container: Mapping that owns the text field.

        Returns:
            List of found legacy terms with positions.
        """
        if not text or not isinstance(text, str):
            return []

        protected = immutable_technical_spans(text, path=path, container=container)
        candidates = sorted(
            [
                (
                    match.start(),
                    match.end(),
                    match.group(0),
                )
                for pattern in self._legacy_patterns
                for match in pattern.finditer(text)
            ],
            key=lambda item: (item[0], -(item[1] - item[0]), item[2].casefold()),
        )

        findings: list[dict[str, Any]] = []
        accepted_spans: list[tuple[int, int]] = []
        for start, end, term in candidates:
            if any(
                start < protected_end and end > protected_start
                for protected_start, protected_end in protected
            ):
                continue
            if any(
                start < accepted_end and end > accepted_start
                for accepted_start, accepted_end in accepted_spans
            ):
                continue
            accepted_spans.append((start, end))
            findings.append(
                {
                    "term": term,
                    "position": start,
                    "context": text[max(0, start - 20) : end + 20],
                },
            )

        return findings

    def validate_spec(
        self,
        spec: dict[str, Any],
        target_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Validate an OpenAPI specification for legacy branding.

        Args:
            spec: OpenAPI specification dictionary.
            target_fields: List of field names to check.

        Returns:
            List of found legacy terms with field paths.
        """
        if target_fields is None:
            target_fields = ["description", "summary", "x-displayname"]

        findings: list[dict[str, Any]] = []
        self._validate_recursive(spec, target_fields, "", findings)
        return findings

    def _validate_recursive(
        self,
        obj: Any,
        target_fields: list[str],
        path: str,
        findings: list[dict[str, Any]],
    ) -> None:
        """Recursively validate object for legacy branding."""
        if isinstance(obj, dict):
            for key, value in obj.items():
                new_path = f"{path}.{key}" if path else key
                if key in target_fields and isinstance(value, str):
                    field_findings = self.validate_text(
                        value,
                        path=new_path,
                        container=obj,
                    )
                    for finding in field_findings:
                        finding["path"] = new_path
                        findings.append(finding)
                else:
                    self._validate_recursive(value, target_fields, new_path, findings)
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                new_path = f"{path}[{i}]"
                self._validate_recursive(item, target_fields, new_path, findings)


class BrandingNormalizer:
    """Normalizes F5 XC product terminology to current API names.

    Transforms legacy Volterra-era terms to current F5 Distributed Cloud names:
    - AppStack/VoltStack → Managed Kubernetes
    - vK8s → Virtual Kubernetes

    Configuration-driven from config/branding.yaml.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize with branding configuration.

        Args:
            config_path: Path to branding.yaml config. Defaults to config/branding.yaml.
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / "config" / "branding.yaml"

        self.config_path = config_path
        self.stats = BrandingStats()
        (
            self.canonical,
            self.transformations,
            self.glossary,
            self.domain_branding,
            self._compiled_patterns,
        ) = self._load_config()

    def _load_config(
        self,
    ) -> tuple[
        dict[str, Any],
        list[dict[str, Any]],
        dict[str, Any],
        dict[str, Any],
        list[tuple[re.Pattern[str], str, list[str], str]],
    ]:
        """Load, validate, and atomically compile branding normalization rules."""
        config = _load_yaml_mapping(self.config_path, "branding normalizer")
        required_sections = {
            "canonical",
            "deprecations",
            "description",
            "domain_branding",
            "glossary",
            "transformations",
            "version",
        }
        _require_known_keys(
            config,
            required=required_sections,
            context=f"branding normalizer configuration {self.config_path}",
        )
        version = config["version"]
        if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise BrandingConfigError("branding normalizer version must be a semantic version")
        if not isinstance(config["description"], str) or not config["description"].strip():
            raise BrandingConfigError("branding normalizer description must be a non-empty string")
        deprecations = config["deprecations"]
        if not isinstance(deprecations, dict) or not deprecations:
            raise BrandingConfigError(
                "branding normalizer deprecations must be a non-empty mapping"
            )
        deprecation_entry_keys = {
            "api_documentation": {"canonical", "deprecated"},
            "api_endpoint": {"canonical", "deprecated"},
            "cli": {"canonical", "deprecated"},
            "documentation": {"canonical", "deprecated"},
            "product_brand": {"canonical", "deprecated"},
            "terraform_provider": {"canonical", "deprecated", "required_providers_block"},
        }
        deprecation_side_keys = {
            ("api_documentation", "canonical"): {"note", "url"},
            ("api_documentation", "deprecated"): {"note", "url"},
            ("api_endpoint", "canonical"): {"note"},
            ("api_endpoint", "deprecated"): {"url"},
            ("cli", "canonical"): {"command", "note"},
            ("cli", "deprecated"): {"command", "note", "status"},
            ("documentation", "canonical"): {"url"},
            ("documentation", "deprecated"): {"note"},
            ("product_brand", "canonical"): {"name", "note"},
            ("product_brand", "deprecated"): {"name", "note"},
            ("terraform_provider", "canonical"): {
                "docs",
                "github",
                "llms_txt",
                "registry",
                "source",
            },
            ("terraform_provider", "deprecated"): {
                "downloads",
                "github",
                "last_version",
                "note",
                "registry",
                "source",
                "status",
            },
        }
        _require_known_keys(
            deprecations,
            required=set(deprecation_entry_keys),
            context="deprecations",
        )
        for name, required_keys in deprecation_entry_keys.items():
            entry = deprecations[name]
            if not isinstance(entry, dict):
                raise BrandingConfigError(f"deprecations.{name} must be a mapping")
            _require_known_keys(
                entry,
                required=required_keys,
                context=f"deprecations.{name}",
            )
            for side in ("canonical", "deprecated"):
                side_value = entry[side]
                if not isinstance(side_value, dict):
                    raise BrandingConfigError(f"deprecations.{name}.{side} must be a mapping")
                _require_known_keys(
                    side_value,
                    required=deprecation_side_keys[(name, side)],
                    context=f"deprecations.{name}.{side}",
                )
                if any(
                    not isinstance(value, str) or not value.strip() for value in side_value.values()
                ):
                    raise BrandingConfigError(
                        f"deprecations.{name}.{side} fields must be non-empty strings",
                    )
            for scalar_key in required_keys - {"canonical", "deprecated"}:
                scalar = entry[scalar_key]
                if not isinstance(scalar, str) or not scalar.strip():
                    raise BrandingConfigError(
                        f"deprecations.{name}.{scalar_key} must be a non-empty string",
                    )

        canonical = config["canonical"]
        if not isinstance(canonical, dict) or not canonical:
            raise BrandingConfigError("canonical must be a non-empty mapping")
        for name, entry in canonical.items():
            context = f"canonical.{name}"
            if not isinstance(name, str) or not name or not isinstance(entry, dict):
                raise BrandingConfigError(f"{context} must be a named mapping")
            _require_known_keys(
                entry,
                required={"comparable_to", "description", "legacy_names", "long_form"},
                context=context,
            )
            if not isinstance(entry["long_form"], str) or not entry["long_form"].strip():
                raise BrandingConfigError(f"{context}.long_form must be a non-empty string")
            if not isinstance(entry["description"], str) or not entry["description"].strip():
                raise BrandingConfigError(f"{context}.description must be a non-empty string")
            for field_name in ("legacy_names", "comparable_to"):
                values = entry[field_name]
                if (
                    not isinstance(values, list)
                    or not values
                    or any(not isinstance(value, str) or not value for value in values)
                ):
                    raise BrandingConfigError(
                        f"{context}.{field_name} must be a non-empty list of strings",
                    )

        transformations = config["transformations"]
        if not isinstance(transformations, list) or not transformations:
            raise BrandingConfigError("transformations must be a non-empty list")
        compiled_patterns: list[tuple[re.Pattern[str], str, list[str], str]] = []
        for index, rule in enumerate(transformations):
            context = f"transformations[{index}]"
            if not isinstance(rule, dict):
                raise BrandingConfigError(f"{context} must be a mapping")
            _require_known_keys(
                rule,
                required={"case_sensitive", "context", "pattern", "replacement"},
                optional={"type"},
                context=context,
            )
            pattern_str = rule["pattern"]
            replacement = rule["replacement"]
            contexts = rule["context"]
            case_sensitive = rule["case_sensitive"]
            transformation_type = rule.get("type")
            if not isinstance(pattern_str, str) or not pattern_str:
                raise BrandingConfigError(f"{context}.pattern must be a non-empty string")
            if not isinstance(replacement, str):
                raise BrandingConfigError(f"{context}.replacement must be a string")
            if not isinstance(case_sensitive, bool):
                raise BrandingConfigError(f"{context}.case_sensitive must be a boolean")
            if (
                not isinstance(contexts, list)
                or not contexts
                or any(not isinstance(item, str) or not item for item in contexts)
            ):
                raise BrandingConfigError(f"{context}.context must be a non-empty list of strings")
            if transformation_type is not None and (
                not isinstance(transformation_type, str) or not transformation_type
            ):
                raise BrandingConfigError(f"{context}.type must be a non-empty string")

            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                pattern = re.compile(pattern_str, flags)
                pattern.sub(replacement, "")
            except re.error as exc:
                raise BrandingConfigError(
                    f"{context} has invalid regex or replacement: {exc}",
                ) from exc
            trans_type = transformation_type or (
                "virtual_k8s"
                if "Virtual Kubernetes" in replacement
                else "managed_k8s"
                if "Managed Kubernetes" in replacement
                else "other"
            )
            compiled_patterns.append((pattern, replacement, contexts, trans_type))

        glossary = config["glossary"]
        if not isinstance(glossary, dict) or not glossary:
            raise BrandingConfigError("glossary must be a non-empty mapping")
        for name, entry in glossary.items():
            context = f"glossary.{name}"
            if not isinstance(name, str) or not name or not isinstance(entry, dict):
                raise BrandingConfigError(f"{context} must be a named mapping")
            _require_known_keys(entry, required={"definition", "term"}, context=context)
            if any(
                not isinstance(entry[field], str) or not entry[field].strip()
                for field in ("definition", "term")
            ):
                raise BrandingConfigError(f"{context} fields must be non-empty strings")

        domain_branding = config["domain_branding"]
        if not isinstance(domain_branding, dict) or not domain_branding:
            raise BrandingConfigError("domain_branding must be a non-empty mapping")
        for name, entry in domain_branding.items():
            context = f"domain_branding.{name}"
            if not isinstance(name, str) or not name or not isinstance(entry, dict):
                raise BrandingConfigError(f"{context} must be a named mapping")
            _require_known_keys(entry, required={"description", "title"}, context=context)
            if any(
                not isinstance(entry[field], str) or not entry[field].strip()
                for field in ("description", "title")
            ):
                raise BrandingConfigError(f"{context} fields must be non-empty strings")

        return canonical, transformations, glossary, domain_branding, compiled_patterns

    def normalize_text(
        self,
        text: str,
        field_context: str = "",
        *,
        container: dict[str, Any] | None = None,
    ) -> str:
        """Apply product name normalization to text.

        Args:
            text: Input text with legacy terminology.
            field_context: Field path context for selective application.
            container: Mapping that owns the text field.

        Returns:
            Text with normalized terminology.
        """
        if not text or not isinstance(text, str):
            return text

        def _matches(ctx: str, path: str) -> bool:
            """Return whether a configured semantic context applies to a path."""
            if ctx in path:
                return True
            ctx_parts = ctx.split(".")
            path_parts = path.split(".")
            if ctx_parts[-1] != path_parts[-1]:
                return False
            ancestor_key = ctx_parts[0] if len(ctx_parts) > 1 else ""
            if ancestor_key == "operation":
                http_methods = {
                    "get",
                    "post",
                    "put",
                    "delete",
                    "patch",
                    "options",
                    "head",
                }
                return any(part in http_methods for part in path_parts[:-1])
            return not ancestor_key or any(ancestor_key in part for part in path_parts[:-1])

        applicable = [
            (pattern, replacement, trans_type)
            for pattern, replacement, contexts, trans_type in self._compiled_patterns
            if not contexts
            or not field_context
            or any(_matches(ctx, field_context) for ctx in contexts)
        ]
        if not any(pattern.search(text) for pattern, _, _ in applicable):
            return text

        changed = [False] * len(applicable)
        rules: list[TextRule] = []
        for index, (pattern, replacement, _) in enumerate(applicable):

            def replace(
                match: re.Match[str],
                replacement: str = replacement,
                index: int = index,
            ) -> str:
                expanded = match.expand(replacement)
                if expanded != match.group(0):
                    changed[index] = True
                return expanded

            rules.append((pattern, replace))

        result = replace_many_outside_technical_spans(
            text,
            rules,
            path=field_context,
            container=container,
        )
        for was_changed, (_, _, trans_type) in zip(changed, applicable, strict=True):
            if not was_changed:
                continue
            if trans_type == "virtual_k8s":
                self.stats.virtual_k8s_transformations += 1
            elif trans_type == "managed_k8s":
                self.stats.managed_k8s_transformations += 1
            self.stats.transformations_by_type[trans_type] = (
                self.stats.transformations_by_type.get(trans_type, 0) + 1
            )

        return result

    def normalize_spec(
        self,
        spec: dict[str, Any],
        target_fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Apply product name normalization to an OpenAPI specification.

        Args:
            spec: OpenAPI specification dictionary.
            target_fields: List of field names to process.

        Returns:
            Specification with normalized terminology.
        """
        if target_fields is None:
            target_fields = ["description", "summary", "x-displayname"]

        self.stats.files_processed += 1
        result = self._normalize_recursive(spec, target_fields, "")

        # Optionally add glossary to spec info
        if self.glossary and "info" in result:
            result = self._add_glossary_to_info(result)

        return result

    def _normalize_recursive(
        self,
        obj: Any,
        target_fields: list[str],
        current_path: str,
    ) -> Any:
        """Recursively process object and normalize text fields."""
        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                new_path = f"{current_path}.{key}" if current_path else key

                if key in target_fields and isinstance(value, str):
                    result[key] = self.normalize_text(
                        value,
                        field_context=new_path,
                        container=obj,
                    )
                else:
                    result[key] = self._normalize_recursive(value, target_fields, new_path)
            return result

        if isinstance(obj, list):
            return [self._normalize_recursive(item, target_fields, current_path) for item in obj]

        return obj

    def _add_glossary_to_info(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Add glossary terms to spec info section.

        Args:
            spec: OpenAPI specification dictionary.

        Returns:
            Specification with glossary added to info.
        """
        if "info" not in spec:
            return spec

        # Check if glossary already exists
        existing_glossary = spec["info"].get(X_F5XC_GLOSSARY, {})

        # Merge our glossary terms
        for term, definition in self.glossary.items():
            if term not in existing_glossary:
                existing_glossary[term] = definition
                self.stats.glossary_terms_added += 1

        if existing_glossary:
            spec["info"][X_F5XC_GLOSSARY] = existing_glossary

        return spec

    def get_canonical_name(self, domain: str) -> dict[str, Any] | None:
        """Get canonical naming information for a domain.

        Args:
            domain: Domain identifier (e.g., "managed_kubernetes", "container_services").

        Returns:
            Dictionary with long_form, short_form, comparable_to, etc. or None.
        """
        return self.canonical.get(domain)

    def get_domain_branding(self, domain: str) -> dict[str, Any] | None:
        """Get domain-specific branding information.

        Args:
            domain: Domain identifier.

        Returns:
            Dictionary with title and description for the domain or None.
        """
        return self.domain_branding.get(domain)

    def get_stats(self) -> dict[str, Any]:
        """Return statistics about branding normalizations applied."""
        return self.stats.to_dict()

    def reset_stats(self) -> None:
        """Reset statistics counters."""
        self.stats = BrandingStats()
