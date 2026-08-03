# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Property Description Enricher for OpenAPI specifications.

Generates concise descriptions for schema properties with long descriptions (>300 chars):
- Short tier: 80-150 characters for tooltips and Terraform descriptions
- Medium tier: 150-300 characters for extended tooltips, CLI help, and summaries

Follows Azure/AWS Terraform provider conventions:
- Imperative tone: "Specifies...", "Enables...", "Configures..."
- Single sentence (short) or 2-3 sentences (medium), no code examples
- Includes defaults and constraints inline

Issue #330: https://github.com/f5-sales-demo/api-specs-enriched/issues/330
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import yaml

from scripts.utils.extension_constants import X_F5XC_DESCRIPTION_MEDIUM, X_F5XC_DESCRIPTION_SHORT

# Precompiled regex pattern for performance (used in hot paths)
_WHITESPACE_NORMALIZE_PATTERN = re.compile(r"\s+")


@dataclass
class PropertyDescriptionShortStats:
    """Statistics from property description enrichment (short and medium tiers)."""

    fields_processed: int = 0
    short_descriptions_added: int = 0
    medium_descriptions_added: int = 0
    descriptions_from_extraction: int = 0
    descriptions_from_config: int = 0
    skipped_already_short: int = 0
    skipped_has_extension: int = 0
    schemas_processed: int = 0

    def to_dict(self) -> dict[str, int]:
        """Convert stats to dictionary."""
        return {
            "fields_processed": self.fields_processed,
            "short_descriptions_added": self.short_descriptions_added,
            "medium_descriptions_added": self.medium_descriptions_added,
            "descriptions_from_extraction": self.descriptions_from_extraction,
            "descriptions_from_config": self.descriptions_from_config,
            "skipped_already_short": self.skipped_already_short,
            "skipped_has_extension": self.skipped_has_extension,
            "schemas_processed": self.schemas_processed,
        }


@dataclass
class EnricherSettings:
    """Configuration settings for the enricher."""

    min_source_length: int = 300
    # Short tier (80-150 chars)
    target_min_length: int = 80
    target_max_length: int = 150
    # Medium tier (150-300 chars)
    medium_min_length: int = 150
    medium_max_length: int = 300
    preserve_existing: bool = True


@dataclass
class PatternTemplate:
    """A pattern-based template for generating short descriptions."""

    pattern: re.Pattern[str]
    template: str


class PropertyDescriptionShortEnricher:
    """Generate 80-150 char descriptions for properties with long descriptions.

    Uses multi-tier approach:
    1. Configuration override (highest priority)
    2. Pattern-based templates
    3. First sentence extraction with style transformation

    Follows Azure/AWS Terraform provider conventions for documentation.
    """

    # Style transformation rules (pattern -> replacement)
    # Replacement can be string or callable for regex substitution
    STYLE_TRANSFORMS: ClassVar[
        list[tuple[str, str | Callable[[re.Match[str]], str] | Callable[[re.Match[str]], str]]]
    ] = [
        # Convert passive/descriptive to imperative
        (r"^The\s+(\w+)\s+(?:is|are)\s+(?:used\s+)?(?:to\s+)?", r"Specifies \1 "),
        (r"^This\s+(?:field|property|setting)\s+(?:is|specifies|defines|contains)\s+", ""),
        (r"^This\s+(?:is\s+)?(?:the\s+)?", ""),
        (r"^A\s+(\w+)\s+that\s+", r"\1 that "),
        (r"^An?\s+", ""),
        # Capitalize first letter after transforms
        (r"^([a-z])", lambda m: m.group(1).upper()),
    ]

    # Patterns to remove from descriptions
    # Order matters: code blocks must be removed before inline code
    REMOVAL_PATTERNS: ClassVar[list[str]] = [
        r"```[\s\S]*?```",  # Remove code blocks FIRST
        r"`[^`]+`",  # Remove inline code SECOND
        r"\s*Example:.*",
        r"\s*Examples?:.*",
        r"\s*See\s+http[s]?://\S+",
        r"\s*For\s+more\s+information.*",
        r"\s*Note:.*",
        r"\s*\([^)]*http[s]?://[^)]*\)",
    ]

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize enricher with configuration.

        Args:
            config_path: Path to property_description_short.yaml config.
                        Defaults to config/property_description_short.yaml.
        """
        if config_path is None:
            config_path = (
                Path(__file__).parent.parent.parent / "config" / "property_description_short.yaml"
            )

        self.config_path = config_path
        self.settings = EnricherSettings()
        # Short tier overrides and patterns
        self.overrides: dict[str, str] = {}
        self.patterns: list[PatternTemplate] = []
        # Medium tier overrides and patterns
        self.medium_overrides: dict[str, str] = {}
        self.medium_patterns: list[PatternTemplate] = []
        self.exclusions: list[re.Pattern[str]] = []
        self.stats = PropertyDescriptionShortStats()
        self._config_version: str = "1.0.0"

        self._load_config()
        self._compile_style_transforms()

    def _load_config(self) -> None:
        """Load configuration from YAML file."""
        if not self.config_path.exists():
            return

        try:
            with self.config_path.open() as f:
                config = yaml.safe_load(f) or {}

            self._config_version = config.get("version", "1.0.0")

            # Load settings (including medium tier)
            settings = config.get("settings", {})
            self.settings = EnricherSettings(
                min_source_length=settings.get("min_source_length", 300),
                target_min_length=settings.get("target_min_length", 80),
                target_max_length=settings.get("target_max_length", 150),
                medium_min_length=settings.get("medium_min_length", 150),
                medium_max_length=settings.get("medium_max_length", 300),
                preserve_existing=settings.get("preserve_existing", True),
            )

            # Load short tier overrides
            self.overrides = config.get("overrides", {})

            # Load medium tier overrides
            self.medium_overrides = config.get("medium_overrides", {})

            # Compile short tier patterns
            for pattern_config in config.get("patterns", []):
                try:
                    compiled = re.compile(pattern_config["pattern"])
                    self.patterns.append(
                        PatternTemplate(
                            pattern=compiled,
                            template=pattern_config["template"],
                        ),
                    )
                except re.error:
                    pass  # Skip invalid patterns

            # Compile medium tier patterns
            for pattern_config in config.get("medium_patterns", []):
                try:
                    compiled = re.compile(pattern_config["pattern"])
                    self.medium_patterns.append(
                        PatternTemplate(
                            pattern=compiled,
                            template=pattern_config["template"],
                        ),
                    )
                except re.error:
                    pass  # Skip invalid patterns

            # Compile exclusions
            for exclusion in config.get("exclusions", []):
                try:  # noqa: SIM105
                    self.exclusions.append(re.compile(exclusion))
                except re.error:
                    pass

        except Exception:  # noqa: S110
            pass  # Use defaults on any error

    def _compile_style_transforms(self) -> None:
        """Pre-compile style transformation patterns."""
        self._compiled_transforms: list[tuple[re.Pattern[str], Any]] = []
        for pattern, replacement in self.STYLE_TRANSFORMS:
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
                self._compiled_transforms.append((compiled, replacement))
            except re.error:
                pass

        self._compiled_removals: list[re.Pattern[str]] = []
        for pattern in self.REMOVAL_PATTERNS:
            try:  # noqa: SIM105
                self._compiled_removals.append(re.compile(pattern, re.IGNORECASE | re.DOTALL))
            except re.error:
                pass

    def get_config_version(self) -> str:
        """Get the configuration file version."""
        return self._config_version

    def get_stats(self) -> dict[str, int]:
        """Get enrichment statistics."""
        return self.stats.to_dict()

    def enrich_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Enrich all schema properties with short descriptions.

        Args:
            spec: OpenAPI specification dictionary

        Returns:
            Enriched specification with x-f5xc-description-short on properties
        """
        components = spec.get("components", {})
        schemas = components.get("schemas", {})

        for schema_name, schema in schemas.items():
            self._process_schema(schema_name, schema)

        return spec

    def _process_schema(self, schema_name: str, schema: dict[str, Any]) -> None:
        """Process a single schema and its properties.

        Args:
            schema_name: Name of the schema
            schema: Schema definition dictionary
        """
        self.stats.schemas_processed += 1

        # Process schema description itself (Issue #330)
        self._process_schema_description(schema_name, schema)

        # Process direct properties
        properties = schema.get("properties", {})
        for prop_name, prop in properties.items():
            self._process_property(schema_name, prop_name, prop)

        # Process nested schemas in allOf, oneOf, anyOf
        for composition_key in ["allOf", "oneOf", "anyOf"]:
            for item in schema.get(composition_key, []):
                if isinstance(item, dict):
                    nested_props = item.get("properties", {})
                    for prop_name, prop in nested_props.items():
                        self._process_property(schema_name, prop_name, prop)

        # Process items for array schemas
        items = schema.get("items", {})
        if isinstance(items, dict):
            items_props = items.get("properties", {})
            for prop_name, prop in items_props.items():
                self._process_property(schema_name, prop_name, prop)

    def _process_schema_description(self, schema_name: str, schema: dict[str, Any]) -> None:
        """Process the schema's own description for short/medium tiers.

        Args:
            schema_name: Name of the schema
            schema: Schema definition dictionary
        """
        description = schema.get("description", "")

        # Skip if no description or not longer than minimum source length
        if not description or len(description) <= self.settings.min_source_length:
            return

        # Check exclusions
        if any(exclusion.match(schema_name) for exclusion in self.exclusions):
            return

        # Track if extensions already exist
        has_short = X_F5XC_DESCRIPTION_SHORT in schema
        has_medium = X_F5XC_DESCRIPTION_MEDIUM in schema

        # Skip if both already exist and preserve_existing is True
        if self.settings.preserve_existing and has_short and has_medium:
            return

        # Generate short description if not already present
        if not (self.settings.preserve_existing and has_short):
            short_desc = self._generate_short_description_for_schema(description)
            if short_desc:
                schema[X_F5XC_DESCRIPTION_SHORT] = short_desc
                self.stats.short_descriptions_added += 1

        # Generate medium description if not already present
        if not (self.settings.preserve_existing and has_medium):
            medium_desc = self._generate_medium_description_for_schema(description)
            if medium_desc:
                schema[X_F5XC_DESCRIPTION_MEDIUM] = medium_desc
                self.stats.medium_descriptions_added += 1

    def _generate_short_description_for_schema(
        self,
        description: str,
    ) -> str | None:
        """Generate short description for a schema.

        Uses priority order:
        1. Configuration override (schema-level)
        2. First sentence extraction with style transformation

        Args:
            description: Original long description

        Returns:
            Short description (80-150 chars) or None if generation failed
        """
        # 1. Check for configuration override (schema-level overrides could be added to config)
        # For now, skip config overrides for schemas

        # 2. Extract and transform from description
        short_desc = self._extract_and_transform(description)
        if short_desc:
            self.stats.descriptions_from_extraction += 1
            return short_desc

        return None

    def _generate_medium_description_for_schema(
        self,
        description: str,
    ) -> str | None:
        """Generate medium description for a schema.

        Uses multi-sentence extraction with style transformation.

        Args:
            description: Original long description

        Returns:
            Medium description (150-300 chars) or None if generation failed
        """
        # Extract and transform from description (multiple sentences)
        return self._extract_and_transform_medium(description)

    def _process_property(
        self,
        schema_name: str,
        prop_name: str,
        prop: dict[str, Any],
    ) -> None:
        """Process a single property for description enrichment (short and medium tiers).

        Args:
            schema_name: Name of the parent schema
            prop_name: Name of the property
            prop: Property definition dictionary
        """
        self.stats.fields_processed += 1

        # Skip if property is not a dict
        if not isinstance(prop, dict):
            return

        description = prop.get("description", "")

        # Skip if no description or not longer than minimum source length
        if not description or len(description) <= self.settings.min_source_length:
            self.stats.skipped_already_short += 1
            return

        # Check exclusions
        full_path = f"{schema_name}.{prop_name}"
        for exclusion in self.exclusions:
            if exclusion.match(full_path):
                return

        # Track if both extensions already exist
        has_short = X_F5XC_DESCRIPTION_SHORT in prop
        has_medium = X_F5XC_DESCRIPTION_MEDIUM in prop

        # Skip if both already exist and preserve_existing is True
        if self.settings.preserve_existing and has_short and has_medium:
            self.stats.skipped_has_extension += 1
            return

        # Generate short description if not already present (or not preserving)
        if not (self.settings.preserve_existing and has_short):
            short_desc = self._generate_short_description(schema_name, prop_name, description)
            if short_desc:
                prop[X_F5XC_DESCRIPTION_SHORT] = short_desc
                self.stats.short_descriptions_added += 1

        # Generate medium description if not already present (or not preserving)
        if not (self.settings.preserve_existing and has_medium):
            medium_desc = self._generate_medium_description(schema_name, prop_name, description)
            if medium_desc:
                prop[X_F5XC_DESCRIPTION_MEDIUM] = medium_desc
                self.stats.medium_descriptions_added += 1

    def _generate_short_description(
        self,
        schema_name: str,
        prop_name: str,
        description: str,
    ) -> str | None:
        """Generate a short description for a property.

        Uses priority order:
        1. Configuration override
        2. Pattern-based template
        3. First sentence extraction

        Args:
            schema_name: Name of the parent schema
            prop_name: Name of the property
            description: Original long description

        Returns:
            Short description (80-150 chars) or None if generation failed
        """
        full_path = f"{schema_name}.{prop_name}"

        # 1. Check for configuration override
        if full_path in self.overrides:
            self.stats.descriptions_from_config += 1
            return self.overrides[full_path]

        # 2. Check pattern-based templates
        for pattern_template in self.patterns:
            if pattern_template.pattern.search(prop_name):
                self.stats.descriptions_from_config += 1
                return pattern_template.template

        # 3. Extract and transform from description
        short_desc = self._extract_and_transform(description)
        if short_desc:
            self.stats.descriptions_from_extraction += 1
            return short_desc

        return None

    def _generate_medium_description(
        self,
        schema_name: str,
        prop_name: str,
        description: str,
    ) -> str | None:
        """Generate a medium description for a property.

        Uses priority order:
        1. Configuration override (medium_overrides)
        2. Pattern-based template (medium_patterns)
        3. Multi-sentence extraction with style transformation

        Args:
            schema_name: Name of the parent schema
            prop_name: Name of the property
            description: Original long description

        Returns:
            Medium description (150-300 chars) or None if generation failed
        """
        full_path = f"{schema_name}.{prop_name}"

        # 1. Check for medium configuration override
        if full_path in self.medium_overrides:
            return self.medium_overrides[full_path]

        # 2. Check medium pattern-based templates
        for pattern_template in self.medium_patterns:
            if pattern_template.pattern.search(prop_name):
                return pattern_template.template

        # 3. Extract and transform from description (multiple sentences)
        return self._extract_and_transform_medium(description)

    def _extract_and_transform_medium(self, description: str) -> str | None:
        """Extract multiple sentences and apply style transformations for medium tier.

        Args:
            description: Original long description

        Returns:
            Transformed medium description or None
        """
        # Clean the description first
        cleaned = self._clean_description(description)

        # Extract up to 3 sentences for medium tier
        sentences = self._extract_multiple_sentences(cleaned, max_count=3)
        if not sentences:
            return None

        # Combine sentences
        combined = " ".join(sentences)

        # Apply style transformations
        transformed = self._apply_style_rules(combined)

        # Validate and adjust length
        if len(transformed) < self.settings.medium_min_length:
            # Try to get more sentences
            additional = self._extract_multiple_sentences(cleaned, max_count=5)
            if additional and len(additional) > len(sentences):
                combined = " ".join(additional)
                transformed = self._apply_style_rules(combined)

        # Truncate if too long
        if len(transformed) > self.settings.medium_max_length:
            transformed = self._smart_truncate(transformed, self.settings.medium_max_length)

        # Final validation
        if self.settings.medium_min_length <= len(transformed) <= self.settings.medium_max_length:
            return transformed

        # If still too short but has reasonable content, return it
        if transformed and len(transformed) >= 100:
            return transformed

        return None

    def _extract_multiple_sentences(self, text: str, max_count: int = 3) -> list[str]:
        """Extract multiple sentences from text.

        Args:
            text: Input text
            max_count: Maximum number of sentences to extract

        Returns:
            List of sentences (may be empty)
        """
        if not text:
            return []

        # Split on sentence boundaries
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)

        result = []
        for i, sentence in enumerate(sentences):
            if i >= max_count:
                break
            stripped = sentence.strip()
            if stripped:
                # Ensure it ends with punctuation
                if stripped[-1] not in ".!?":
                    stripped += "."
                result.append(stripped)

        return result

    def _extract_and_transform(self, description: str) -> str | None:
        """Extract first sentence and apply style transformations.

        Args:
            description: Original long description

        Returns:
            Transformed short description or None
        """
        # Clean the description first
        cleaned = self._clean_description(description)

        # Extract first sentence
        first_sentence = self._extract_first_sentence(cleaned)
        if not first_sentence:
            return None

        # Apply style transformations
        transformed = self._apply_style_rules(first_sentence)

        # Validate length
        if len(transformed) < self.settings.target_min_length:
            # Try to expand with second sentence if too short
            second = self._extract_second_sentence(cleaned)
            if second:
                combined = f"{transformed} {second}"
                if len(combined) <= self.settings.target_max_length:
                    transformed = combined

        # Truncate if too long
        if len(transformed) > self.settings.target_max_length:
            transformed = self._smart_truncate(transformed, self.settings.target_max_length)

        # Final validation
        if self.settings.target_min_length <= len(transformed) <= self.settings.target_max_length:
            return transformed

        # If still too short, return what we have (some description is better than none)
        if transformed and len(transformed) >= 40:
            return transformed

        return None

    def _clean_description(self, description: str) -> str:
        """Remove code examples, HTTP details, and normalize whitespace.

        Args:
            description: Original description

        Returns:
            Cleaned description
        """
        cleaned = description

        # Apply removal patterns
        for pattern in self._compiled_removals:
            cleaned = pattern.sub("", cleaned)

        # Normalize whitespace
        cleaned = _WHITESPACE_NORMALIZE_PATTERN.sub(" ", cleaned).strip()

        return cleaned  # noqa: RET504

    def _extract_first_sentence(self, text: str) -> str | None:
        """Extract the first sentence from text.

        Args:
            text: Input text

        Returns:
            First sentence or None
        """
        if not text:
            return None

        # Split on sentence boundaries
        # Match period, exclamation, or question mark followed by space and capital
        # or end of string
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])|(?<=[.!?])$", text)

        if sentences:
            first = sentences[0].strip()
            # Ensure it ends with punctuation
            if first and first[-1] not in ".!?":
                first += "."
            return first

        return text.strip()

    def _extract_second_sentence(self, text: str) -> str | None:
        """Extract the second sentence from text.

        Args:
            text: Input text

        Returns:
            Second sentence or None
        """
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
        if len(sentences) >= 2:
            return sentences[1].strip()
        return None

    def _apply_style_rules(self, text: str) -> str:
        """Apply Azure/AWS style transformations.

        Converts to imperative tone and cleans up phrasing.

        Args:
            text: Input text

        Returns:
            Transformed text
        """
        result = text

        for pattern, replacement in self._compiled_transforms:
            if callable(replacement):
                result = pattern.sub(replacement, result)
            else:
                result = pattern.sub(replacement, result)

        # Ensure first character is capitalized
        if result and result[0].islower():
            result = result[0].upper() + result[1:]

        # Clean up any double spaces
        result = _WHITESPACE_NORMALIZE_PATTERN.sub(" ", result).strip()

        return result  # noqa: RET504

    def _smart_truncate(self, text: str, max_length: int) -> str:
        """Truncate text at word boundary with ellipsis if needed.

        Args:
            text: Text to truncate
            max_length: Maximum length including any ellipsis

        Returns:
            Truncated text
        """
        if len(text) <= max_length:
            return text

        # Try to truncate at word boundary
        truncated = text[: max_length - 3]
        last_space = truncated.rfind(" ")

        if last_space > max_length // 2:
            truncated = truncated[:last_space]

        # Remove trailing punctuation before adding ellipsis
        truncated = truncated.rstrip(".,;:-")

        return truncated + "..."

    def get_override(self, schema_name: str, prop_name: str) -> str | None:
        """Get configured override for a property.

        Args:
            schema_name: Schema name
            prop_name: Property name

        Returns:
            Override value or None
        """
        full_path = f"{schema_name}.{prop_name}"
        return self.overrides.get(full_path)

    def has_override(self, schema_name: str, prop_name: str) -> bool:
        """Check if property has a configured override.

        Args:
            schema_name: Schema name
            prop_name: Property name

        Returns:
            True if override exists
        """
        return self.get_override(schema_name, prop_name) is not None


# Module-level singleton cache for convenient access
_ENRICHER_CACHE: dict[str, PropertyDescriptionShortEnricher | None] = {"instance": None}


def get_property_description_short_enricher() -> PropertyDescriptionShortEnricher:
    """Get or create the singleton enricher instance."""
    instance = _ENRICHER_CACHE["instance"]
    if instance is None:
        instance = PropertyDescriptionShortEnricher()
        _ENRICHER_CACHE["instance"] = instance
    return instance
