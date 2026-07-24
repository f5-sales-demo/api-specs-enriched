#!/usr/bin/env python3
"""F5 XC API Constraint Enricher.

Purpose: Enrich OpenAPI specifications with x-f5xc-constraints extension
Based on: Pattern matching, discovery data, and API validation rules

Architecture:
- PatternMatcher: Regex-based field name matching
- Type-specific extractors: String, Array, Numeric, Object
- ConstraintReconciler: 3-tier priority resolution (EXISTING > DISCOVERY > INFERRED)
- Main ConstraintEnricher: Orchestrates the enrichment process

Usage:
    enricher = ConstraintEnricher(config_path="config/constraint_patterns.yaml")
    enriched_spec = enricher.enrich_spec(spec)
    stats = enricher.get_stats()
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

import yaml

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PatternMatcher:
    """Regex-based pattern matcher for field names."""

    def __init__(self, patterns: dict[str, list[dict]]) -> None:
        """Initialize pattern matcher with compiled regex patterns.

        Args:
            patterns: Dict with 'string_patterns', 'array_patterns', 'number_patterns'
        """
        self.string_patterns = self._compile_patterns(patterns.get("string_patterns", []))
        self.array_patterns = self._compile_patterns(patterns.get("array_patterns", []))
        self.number_patterns = self._compile_patterns(patterns.get("number_patterns", []))

    def _compile_patterns(self, pattern_list: list[dict]) -> list[tuple]:
        """Compile regex patterns for efficiency.

        Args:
            pattern_list: List of pattern dictionaries

        Returns:
            List of (compiled_regex, pattern_dict) tuples
        """
        compiled = []
        for pattern_dict in pattern_list:
            pattern = pattern_dict.get("pattern", "")
            try:
                compiled_regex = re.compile(pattern, re.IGNORECASE)
                compiled.append((compiled_regex, pattern_dict))
            except re.error as e:
                logger.warning("Invalid regex pattern '%s': %s", pattern, e)
        return compiled

    def match_string_pattern(self, field_name: str) -> dict | None:
        """Match field name against string patterns."""
        return self._match_against_patterns(field_name, self.string_patterns)

    def match_array_pattern(self, field_name: str) -> dict | None:
        """Match field name against array patterns."""
        return self._match_against_patterns(field_name, self.array_patterns)

    def match_number_pattern(self, field_name: str) -> dict | None:
        """Match field name against number patterns."""
        return self._match_against_patterns(field_name, self.number_patterns)

    def _match_against_patterns(self, field_name: str, patterns: list[tuple]) -> dict | None:
        """Match field name against compiled patterns.

        Args:
            field_name: Field name to match
            patterns: List of (compiled_regex, pattern_dict) tuples

        Returns:
            First matching pattern dict or None
        """
        for regex, pattern_dict in patterns:
            if regex.search(field_name):
                return pattern_dict
        return None


class StringConstraintExtractor:
    """Extract string-specific constraints."""

    @staticmethod
    def extract(_field_name: str, schema: dict, pattern_match: dict | None) -> dict | None:
        """Extract string constraints from schema and pattern.

        Args:
            field_name: Name of the field
            schema: OpenAPI schema for the field
            pattern_match: Matched pattern dictionary or None

        Returns:
            String constraint dictionary or None
        """
        constraints: dict[str, Any] = {}

        # Extract from existing schema
        if "minLength" in schema:
            constraints["minLength"] = schema["minLength"]
        if "maxLength" in schema:
            constraints["maxLength"] = schema["maxLength"]
        if "pattern" in schema:
            constraints["pattern"] = schema["pattern"]
        if "format" in schema:
            constraints["format"] = schema["format"]

        # Apply pattern constraints. An "authoritative" pattern (e.g. the DNS-1035
        # naming rule) OVERRIDES any pre-existing scalar value, because a prior
        # enricher may have stamped a generic default (e.g. the 1024 string
        # maxLength) that contradicts the API-enforced bound. Non-authoritative
        # patterns only fill gaps, preserving genuine spec-level constraints.
        if pattern_match:
            authoritative = pattern_match.get("authoritative", False)
            pattern_constraints = pattern_match.get("constraints", {})
            for key, value in pattern_constraints.items():
                if authoritative or key not in constraints:
                    constraints[key] = value

        return constraints or None


class ArrayConstraintExtractor:
    """Extract array-specific constraints."""

    @staticmethod
    def extract(_field_name: str, schema: dict, pattern_match: dict | None) -> dict | None:
        """Extract array constraints from schema and pattern.

        Args:
            field_name: Name of the field
            schema: OpenAPI schema for the field
            pattern_match: Matched pattern dictionary or None

        Returns:
            Array constraint dictionary or None
        """
        constraints: dict[str, Any] = {}

        # Extract from existing schema
        if "minItems" in schema:
            constraints["minItems"] = schema["minItems"]
        if "maxItems" in schema:
            constraints["maxItems"] = schema["maxItems"]
        if "uniqueItems" in schema:
            constraints["uniqueItems"] = schema["uniqueItems"]

        # Apply pattern constraints if no existing value
        if pattern_match:
            pattern_constraints = pattern_match.get("constraints", {})
            for key, value in pattern_constraints.items():
                if key not in constraints:
                    constraints[key] = value

        return constraints or None


class NumericConstraintExtractor:
    """Extract numeric-specific constraints."""

    @staticmethod
    def extract(_field_name: str, schema: dict, pattern_match: dict | None) -> dict | None:
        """Extract numeric constraints from schema and pattern.

        Args:
            field_name: Name of the field
            schema: OpenAPI schema for the field
            pattern_match: Matched pattern dictionary or None

        Returns:
            Numeric constraint dictionary or None
        """
        constraints: dict[str, Any] = {}

        # Extract from existing schema
        if "minimum" in schema:
            constraints["minimum"] = schema["minimum"]
        if "maximum" in schema:
            constraints["maximum"] = schema["maximum"]
        if "multipleOf" in schema:
            constraints["multipleOf"] = schema["multipleOf"]
        if "exclusiveMinimum" in schema:
            constraints["exclusiveMinimum"] = schema["exclusiveMinimum"]
        if "exclusiveMaximum" in schema:
            constraints["exclusiveMaximum"] = schema["exclusiveMaximum"]

        # Apply pattern constraints if no existing value
        if pattern_match:
            pattern_constraints = pattern_match.get("constraints", {})
            for key, value in pattern_constraints.items():
                if key not in constraints:
                    constraints[key] = value

        return constraints or None


class ObjectConstraintExtractor:
    """Extract object-specific constraints."""

    @staticmethod
    def extract(_field_name: str, schema: dict) -> dict | None:
        """Extract object constraints from schema.

        Args:
            _field_name: Name of the field
            schema: OpenAPI schema for the field

        Returns:
            Object constraint dictionary or None
        """
        constraints: dict[str, Any] = {}

        # Extract from existing schema
        if "minProperties" in schema:
            constraints["minProperties"] = schema["minProperties"]
        if "maxProperties" in schema:
            constraints["maxProperties"] = schema["maxProperties"]
        if "required" in schema:
            constraints["required"] = schema["required"]

        return constraints or None


class ConstraintReconciler:
    """Reconcile constraints from multiple sources with priority."""

    PRIORITY_ORDER: ClassVar[list[str]] = ["existing", "discovery", "inferred"]

    @staticmethod
    def reconcile(
        existing: dict | None,
        discovery: dict | None,
        inferred: dict | None,
        confidence_threshold: float = 0.9,
    ) -> dict | None:
        """Reconcile constraints from multiple sources.

        Priority: EXISTING > DISCOVERY > INFERRED

        Args:
            existing: Existing x-f5xc-constraints from spec
            discovery: Constraints from discovery data
            inferred: Constraints from pattern matching
            confidence_threshold: Threshold for deterministic flag

        Returns:
            Reconciled constraints dictionary or None
        """
        # If existing constraints present, preserve them completely
        if existing:
            return existing

        # Start with inferred as base
        result = inferred.copy() if inferred else {}

        # Merge discovery constraints (override inferred)
        if discovery:
            for key, value in discovery.items():
                if key != "metadata":
                    result[key] = value

        # If no constraints collected, return None
        if not result:
            return None

        # Determine source and confidence
        if discovery:
            # Honour a discovery constraint's own authoritative source label
            # (e.g. uint32/uint64 bounds are "api-probed"); default to "discovery".
            discovery_meta = discovery.get("metadata") or {}
            source = discovery_meta.get("source", "discovery")
            confidence = discovery_meta.get("confidence", 0.99)
        elif inferred:
            source = "inferred"
            metadata = inferred.get("metadata", {})
            confidence = metadata.get("confidence", 0.85)
        else:
            return None

        # Add metadata
        result["metadata"] = {
            "source": source,
            "confidence": confidence,
            "validatedAt": datetime.now(timezone.utc).isoformat(),
        }

        # Set deterministic flag if confidence meets threshold
        if confidence >= confidence_threshold:
            result["deterministic"] = True

        return result


class ConstraintEnricher:
    """Main constraint enrichment orchestrator."""

    def __init__(self, config_path: Path) -> None:
        """Initialize constraint enricher.

        Args:
            config_path: Path to constraint_patterns.yaml
        """
        self.config_path = Path(config_path)
        self.config = self._load_config()
        self.pattern_matcher = PatternMatcher(self.config)
        self.resource_overrides = self._compile_resource_overrides()

        # Statistics
        self.stats: dict[str, Any] = {
            "properties_analyzed": 0,
            "constraints_added": 0,
            "constraints_skipped_existing": 0,
            "string_constraints": 0,
            "array_constraints": 0,
            "number_constraints": 0,
            "object_constraints": 0,
            "pattern_matches": 0,
            "discovery_merges": 0,
            "confidence_scores": [],
        }

    def _load_config(self) -> dict:
        """Load constraint patterns configuration."""
        try:
            with Path(self.config_path).open() as f:
                return yaml.safe_load(f)
        except Exception:
            logger.exception("Failed to load config from %s", self.config_path)
            raise

    def _compile_resource_overrides(self) -> list[dict]:
        """Compile resource_constraint_overrides patterns."""
        overrides = self.config.get("resource_constraint_overrides", {})
        compiled = []
        for entry in overrides.values():
            pattern = entry.get("schema_pattern", "")
            try:
                compiled.append(
                    {
                        "regex": re.compile(pattern),
                        "fields": entry.get("fields", {}),
                    }
                )
            except re.error as e:
                logger.warning("Invalid resource override pattern '%s': %s", pattern, e)
        return compiled

    @staticmethod
    def _is_object_ref_schema(schema_name: str) -> bool:
        """Return True for object-REFERENCE wrapper schemas.

        e.g. schemaObjectRefType, ioschemaObjectRefType, schemaviewsObjectRefType —
        the ``{namespace,name,tenant,uid}`` refs. Their ``name`` field points at
        ANOTHER object, which may be server-generated and exceed the DNS-1035 label
        length (e.g. the 71-char SMSv2 auto interface name). The authoritative
        DNS-1035 self-name bound describes an object's OWN name and must NOT be
        applied to these reference pointers.
        """
        return "ObjectRefType" in schema_name

    def _get_resource_override(self, schema_name: str, field_name: str) -> dict | None:
        """Check if a field has a resource-specific constraint override."""
        for override in self.resource_overrides:
            if override["regex"].search(schema_name):
                field_override = override["fields"].get(field_name)
                if field_override:
                    return field_override
        return None

    def enrich_spec(self, spec: dict) -> dict:
        """Enrich OpenAPI specification with x-f5xc-constraints.

        Args:
            spec: OpenAPI specification dictionary

        Returns:
            Enriched specification
        """
        logger.info("Starting constraint enrichment")

        # Process all schemas
        if "components" in spec and "schemas" in spec["components"]:
            for schema_name, schema in spec["components"]["schemas"].items():
                self._enrich_schema(schema_name, schema)

        logger.info(
            "Constraint enrichment complete. Added %d constraints",
            self.stats["constraints_added"],
        )
        return spec

    def _enrich_schema(self, schema_name: str, schema: dict) -> None:
        """Enrich a single schema definition."""
        if "properties" not in schema:
            return

        for prop_name, prop_schema in schema["properties"].items():
            self._enrich_property(prop_name, prop_schema, schema_name=schema_name)

    def _parse_ranges(self, raw: str) -> dict:
        """Parse a uint32/uint64 ``ranges`` string into numeric bounds.

        The ``ranges`` form is a comma-separated list of tokens, each either a
        ``lo-hi`` span or a single integer (e.g. ``"0,512-16384"``). The maximum
        is the highest upper bound across all tokens. A minimum is only emitted
        when the value space is a single contiguous span (one token) — a
        discontinuous set (e.g. ``{0}`` together with ``[512, 16384]``) has no
        single lower bound expressible as ``minimum``.

        Args:
            raw: The raw ranges string from the validation rule.

        Returns:
            Dict with ``maximum`` (always, if parseable) and optionally
            ``minimum``; empty when nothing parses.
        """
        tuples: list[tuple[int, int]] = []
        for raw_tok in raw.split(","):
            tok = raw_tok.strip()
            if "-" in tok:
                lo, hi = tok.split("-", 1)
                try:
                    tuples.append((int(lo), int(hi)))
                except ValueError:
                    continue
            elif tok:
                try:
                    v = int(tok)
                    tuples.append((v, v))
                except ValueError:
                    continue
        if not tuples:
            return {}
        out: dict[str, int] = {"maximum": max(h for _, h in tuples)}
        if len(tuples) == 1:
            out["minimum"] = tuples[0][0]
        return out

    def _extract_discovery_constraints(self, schema: dict) -> dict | None:
        """Extract constraints from x-ves-validation-rules.

        Args:
            schema: Property schema with potential x-ves-validation-rules

        Returns:
            Constraints dictionary in x-f5xc-constraints format or None
        """
        ves_rules = schema.get("x-ves-validation-rules")
        if not ves_rules:
            return None

        field_type = schema.get("type")
        if not field_type:
            return None

        # Get discovery mapping from config
        mapping = self.config.get("discovery_mapping", {})

        constraints = {}
        # A discovery rule may declare its own authoritative source
        # (e.g. uint32/uint64 bounds are "api-probed"). Defaults to "discovery".
        source_override: str | None = None

        # Map string rules
        if field_type == "string":
            # The `string.in` rule enumerates the allowed literal values. Map it
            # to the property's *native* JSON-Schema `enum` (not x-f5xc-constraints)
            # so the provider's convert.go enum path emits a `OneOf` validator.
            self._apply_string_in_enum(schema, ves_rules)

            string_rules = mapping.get("string_rules", [])
            for rule_def in string_rules:
                ves_rule = rule_def.get("ves_rule")
                if ves_rule in ves_rules:
                    constraint_field = rule_def.get("constraint_field")
                    constraint_subfield = rule_def.get("constraint_subfield")
                    constraint_value = rule_def.get("constraint_value")

                    if constraint_value is not None:
                        # Fixed value mapping (e.g., format: "email")
                        constraints[constraint_field] = constraint_value
                    elif constraint_subfield:
                        # Nested field mapping (e.g., byteLength.max)
                        rule_value = ves_rules[ves_rule]
                        try:
                            value = int(rule_value)
                            if constraint_field not in constraints:
                                constraints[constraint_field] = {}
                            constraints[constraint_field][constraint_subfield] = value
                        except ValueError:
                            pass
                    else:
                        # Direct value mapping
                        rule_value = ves_rules[ves_rule]
                        if rule_value not in ("true", "false"):
                            # Convert string numbers to int
                            try:
                                constraints[constraint_field] = int(rule_value)
                            except ValueError:
                                constraints[constraint_field] = rule_value

        # Map array rules
        elif field_type == "array":
            array_rules = mapping.get("array_rules", [])
            for rule_def in array_rules:
                ves_rule = rule_def.get("ves_rule")
                if ves_rule in ves_rules:
                    constraint_field = rule_def.get("constraint_field")
                    constraint_value = rule_def.get("constraint_value")

                    if constraint_value is not None:
                        constraints[constraint_field] = constraint_value
                    else:
                        rule_value = ves_rules[ves_rule]
                        try:
                            constraints[constraint_field] = int(rule_value)
                        except ValueError:
                            constraints[constraint_field] = rule_value

        # Map numeric rules
        elif field_type in ("integer", "number"):
            number_rules = mapping.get("number_rules", [])
            for rule_def in number_rules:
                ves_rule = rule_def.get("ves_rule")
                if ves_rule in ves_rules:
                    constraint_field = rule_def.get("constraint_field")
                    rule_value = ves_rules[ves_rule]

                    try:
                        value = int(rule_value)
                        constraints[constraint_field] = value

                        # Handle exclusive bounds
                        if rule_def.get("exclusive"):
                            exclusive_field = f"exclusive{constraint_field.capitalize()}"
                            constraints[exclusive_field] = True

                        if rule_def.get("source"):
                            source_override = rule_def["source"]
                    except ValueError:
                        pass

            # Handle the ranges form (e.g. uint32.ranges "0,512-16384").
            # These are authoritative API bounds (api-probed).
            for ranges_rule in (
                "ves.io.schema.rules.uint32.ranges",
                "ves.io.schema.rules.uint64.ranges",
            ):
                if ranges_rule in ves_rules:
                    parsed = self._parse_ranges(ves_rules[ranges_rule])
                    if parsed:
                        constraints.update(parsed)
                        source_override = "api-probed"

        if not constraints:
            return None

        # Build x-f5xc-constraints structure (flat object)
        constraint_type = "number" if field_type in ("integer", "number") else field_type
        result: dict[str, Any] = {
            "constraintType": constraint_type,
            "category": "discovery",
            **constraints,
        }

        # Add metadata
        result["metadata"] = {
            "source": source_override or "discovery",
            "confidence": 0.99,
            "validatedAt": datetime.now(timezone.utc).isoformat(),
        }
        result["deterministic"] = True

        return result

    @staticmethod
    def _apply_string_in_enum(schema: dict, ves_rules: dict) -> None:
        """Map a `ves.io.schema.rules.string.in` rule to a native `enum`.

        The rule value enumerates the allowed literal values. The provider reads
        the property's native JSON-Schema `enum` (not `x-f5xc-constraints`) to
        generate a `OneOf` validator, so the values are written straight onto
        `schema["enum"]`. Malformed values are skipped rather than raised.

        Args:
            schema: Property schema to mutate in place.
            ves_rules: The property's `x-ves-validation-rules` mapping.
        """
        for rule_key, rule_value in ves_rules.items():
            if not rule_key.endswith(".string.in"):
                continue

            values = rule_value
            if isinstance(values, str):
                # Upstream serializes the array either as clean JSON
                # (`["Control","Worker"]`) or with the inner quotes escaped
                # (`[\"Control\",\"Worker\"]`); try clean first, then unescape.
                try:
                    values = json.loads(values)
                except (json.JSONDecodeError, ValueError):
                    try:
                        values = json.loads(values.replace('\\"', '"'))
                    except (json.JSONDecodeError, ValueError):
                        continue

            if isinstance(values, list) and values:
                schema["enum"] = list(values)
            return

    def _enrich_property(self, field_name: str, schema: dict, *, schema_name: str = "") -> None:
        """Enrich a single property with constraints.

        Args:
            field_name: Name of the property
            schema: Property schema
            schema_name: Name of the parent schema (for resource overrides)
        """
        self.stats["properties_analyzed"] += 1

        # Get existing constraints
        existing = schema.get("x-f5xc-constraints")
        if existing:
            self.stats["constraints_skipped_existing"] += 1
            return

        # Extract discovery constraints
        discovery = self._extract_discovery_constraints(schema)
        if discovery:
            self.stats["discovery_merges"] += 1

        # Check for resource-specific constraint override (before type check —
        # overrides supply their own type and must apply to $ref properties)
        resource_override = self._get_resource_override(schema_name, field_name)
        if resource_override:
            override_constraints = resource_override.get("constraints", {})
            override_metadata = resource_override.get("metadata", {})
            confidence = override_metadata.get("confidence", 0)
            threshold = (
                self.config.get("metadata", {}).get("confidence_thresholds", {}).get("high", 0.9)
            )
            override_result: dict[str, Any] = {
                "constraintType": resource_override.get("type", "number"),
                "category": override_metadata.get("category", "api-probed"),
                **override_constraints,
                "deterministic": confidence >= threshold,
                "metadata": {
                    **override_metadata,
                    "validatedAt": datetime.now(timezone.utc).isoformat(),
                },
            }
            schema["x-f5xc-constraints"] = override_result
            self.stats["constraints_added"] += 1
            if confidence:
                self.stats["confidence_scores"].append(confidence)
            return

        # Determine field type
        field_type = schema.get("type")
        if not field_type:
            return

        # Extract inferred constraints based on type
        inferred = None
        pattern_match = None

        if field_type == "string":
            pattern_match = self.pattern_matcher.match_string_pattern(field_name)
            # The authoritative DNS-1035 `\bname$` naming bound describes an
            # object's OWN name. In object-reference wrappers the `name` field is a
            # POINTER to another (possibly server-generated, >63-char) object, so
            # the naming constraint must not be applied. Drop only the pattern —
            # any genuine inline bound (e.g. schemaviewsObjectRefType's maxLength
            # 128) is still read straight from the schema and preserved.
            if pattern_match and field_name == "name" and self._is_object_ref_schema(schema_name):
                pattern_match = None
            if pattern_match:
                self.stats["pattern_matches"] += 1
            inferred = self._extract_string_constraints(field_name, schema, pattern_match)
            if inferred:
                self.stats["string_constraints"] += 1

        elif field_type == "array":
            pattern_match = self.pattern_matcher.match_array_pattern(field_name)
            if pattern_match:
                self.stats["pattern_matches"] += 1
            inferred = self._extract_array_constraints(field_name, schema, pattern_match)
            if inferred:
                self.stats["array_constraints"] += 1

        elif field_type in ("integer", "number"):
            pattern_match = self.pattern_matcher.match_number_pattern(field_name)
            if pattern_match:
                self.stats["pattern_matches"] += 1
            inferred = self._extract_numeric_constraints(field_name, schema, pattern_match)
            if inferred:
                self.stats["number_constraints"] += 1

        elif field_type == "object":
            inferred = self._extract_object_constraints(field_name, schema)
            if inferred:
                self.stats["object_constraints"] += 1

        # Reconcile constraints from all sources
        constraints = ConstraintReconciler.reconcile(
            existing=existing,
            discovery=discovery,
            inferred=inferred,
        )

        # Apply constraints if found
        if constraints:
            schema["x-f5xc-constraints"] = constraints
            self.stats["constraints_added"] += 1

            # Track confidence
            if "metadata" in constraints and "confidence" in constraints["metadata"]:
                self.stats["confidence_scores"].append(constraints["metadata"]["confidence"])

    def _extract_string_constraints(
        self,
        field_name: str,
        schema: dict,
        pattern_match: dict | None,
    ) -> dict | None:
        """Extract string constraints and build x-f5xc-constraints structure."""
        string_constraints = StringConstraintExtractor.extract(field_name, schema, pattern_match)
        if not string_constraints:
            return None

        result: dict[str, Any] = {
            "constraintType": "string",
            "category": pattern_match.get("metadata", {}).get("category", "general")
            if pattern_match
            else "general",
            **string_constraints,
        }

        # Add metadata from pattern
        if pattern_match and "metadata" in pattern_match:
            metadata: dict[str, Any] = pattern_match["metadata"].copy()
            metadata["validatedAt"] = datetime.now(timezone.utc).isoformat()
            result["metadata"] = metadata

            # Set deterministic flag based on confidence
            confidence = metadata.get("confidence", 0)
            threshold = (
                self.config.get("metadata", {}).get("confidence_thresholds", {}).get("high", 0.9)
            )
            if confidence >= threshold:
                result["deterministic"] = True

        return result

    def _extract_array_constraints(
        self,
        field_name: str,
        schema: dict,
        pattern_match: dict | None,
    ) -> dict | None:
        """Extract array constraints and build x-f5xc-constraints structure."""
        array_constraints = ArrayConstraintExtractor.extract(field_name, schema, pattern_match)
        if not array_constraints:
            return None

        result: dict[str, Any] = {
            "constraintType": "array",
            "category": pattern_match.get("metadata", {}).get("category", "general")
            if pattern_match
            else "general",
            **array_constraints,
        }

        # Add metadata from pattern
        if pattern_match and "metadata" in pattern_match:
            metadata: dict[str, Any] = pattern_match["metadata"].copy()
            metadata["validatedAt"] = datetime.now(timezone.utc).isoformat()
            result["metadata"] = metadata

            # Set deterministic flag based on confidence
            confidence = metadata.get("confidence", 0)
            threshold = (
                self.config.get("metadata", {}).get("confidence_thresholds", {}).get("high", 0.9)
            )
            if confidence >= threshold:
                result["deterministic"] = True

        return result

    def _extract_numeric_constraints(
        self,
        field_name: str,
        schema: dict,
        pattern_match: dict | None,
    ) -> dict | None:
        """Extract numeric constraints and build x-f5xc-constraints structure."""
        # Type guard: numeric name-inference (number_patterns) must only apply to
        # numeric-typed fields. A pattern like `\bid$` otherwise stamps numeric
        # bounds onto a string field named `id`.
        if schema.get("type") not in ("integer", "number"):
            return None

        numeric_constraints = NumericConstraintExtractor.extract(field_name, schema, pattern_match)
        if not numeric_constraints:
            return None

        result: dict[str, Any] = {
            "constraintType": "number",
            "category": pattern_match.get("metadata", {}).get("category", "general")
            if pattern_match
            else "general",
            **numeric_constraints,
        }

        # Add metadata from pattern
        if pattern_match and "metadata" in pattern_match:
            metadata: dict[str, Any] = pattern_match["metadata"].copy()
            metadata["validatedAt"] = datetime.now(timezone.utc).isoformat()
            result["metadata"] = metadata

            # Set deterministic flag based on confidence
            confidence = metadata.get("confidence", 0)
            threshold = (
                self.config.get("metadata", {}).get("confidence_thresholds", {}).get("high", 0.9)
            )
            if confidence >= threshold:
                result["deterministic"] = True

        return result

    def _extract_object_constraints(self, field_name: str, schema: dict) -> dict | None:
        """Extract object constraints and build x-f5xc-constraints structure."""
        object_constraints = ObjectConstraintExtractor.extract(field_name, schema)
        if not object_constraints:
            return None

        return {
            "constraintType": "object",
            "category": "general",
            **object_constraints,
            "metadata": {
                "source": "inferred",
                "confidence": 0.75,
                "validatedAt": datetime.now(timezone.utc).isoformat(),
            },
        }

    def _merge_discovery_constraints(
        self,
        schema: dict,
        discovery_data: dict | None,
    ) -> dict | None:
        """Merge constraints from discovery data.

        Args:
            schema: Property schema
            discovery_data: Discovery validation rules

        Returns:
            Merged constraints or None
        """
        if not discovery_data:
            return None

        # Extract x-ves-validation-rules
        ves_rules = schema.get("x-ves-validation-rules", {})
        if not ves_rules:
            return None

        self.stats["discovery_merges"] += 1

        constraints = {}
        field_type = schema.get("type")

        # Map based on field type
        if field_type == "string":
            constraints = self._map_string_discovery_rules(ves_rules)
        elif field_type == "array":
            constraints = self._map_array_discovery_rules(ves_rules)
        elif field_type in ("integer", "number"):
            constraints = self._map_numeric_discovery_rules(ves_rules)

        if constraints:
            constraint_type = "number" if field_type in ("integer", "number") else field_type
            return {
                "constraintType": constraint_type,
                "category": "discovery",
                **constraints,
                "metadata": {
                    "source": "discovery",
                    "confidence": 0.99,
                    "validatedAt": datetime.now(timezone.utc).isoformat(),
                },
                "deterministic": True,
            }

        return None

    def _map_string_discovery_rules(self, ves_rules: dict) -> dict:
        """Map x-ves-validation-rules to string constraints."""
        mapping = self.config.get("discovery_mapping", {}).get("string_rules", [])
        constraints = {}

        for rule in mapping:
            ves_rule = rule.get("ves_rule")
            constraint_field = rule.get("constraint_field")
            constraint_value = rule.get("constraint_value")

            if ves_rule in ves_rules:
                if constraint_value:
                    constraints[constraint_field] = constraint_value
                else:
                    constraints[constraint_field] = ves_rules[ves_rule]

        return constraints

    def _map_array_discovery_rules(self, ves_rules: dict) -> dict:
        """Map x-ves-validation-rules to array constraints."""
        mapping = self.config.get("discovery_mapping", {}).get("array_rules", [])
        constraints = {}

        for rule in mapping:
            ves_rule = rule.get("ves_rule")
            constraint_field = rule.get("constraint_field")
            constraint_value = rule.get("constraint_value")

            if ves_rule in ves_rules:
                if constraint_value is not None:
                    constraints[constraint_field] = constraint_value
                else:
                    constraints[constraint_field] = ves_rules[ves_rule]

        return constraints

    def _map_numeric_discovery_rules(self, ves_rules: dict) -> dict:
        """Map x-ves-validation-rules to numeric constraints."""
        mapping = self.config.get("discovery_mapping", {}).get("number_rules", [])
        constraints = {}

        for rule in mapping:
            ves_rule = rule.get("ves_rule")
            constraint_field = rule.get("constraint_field")
            exclusive = rule.get("exclusive", False)

            if ves_rule in ves_rules:
                value = ves_rules[ves_rule]
                if exclusive:
                    # Adjust for exclusive bounds
                    if "minimum" in constraint_field:
                        constraints["minimum"] = value + 1
                    elif "maximum" in constraint_field:
                        constraints["maximum"] = value - 1
                else:
                    constraints[constraint_field] = value

        return constraints

    def get_stats(self) -> dict[str, Any]:
        """Get enrichment statistics.

        Returns:
            Statistics dictionary
        """
        stats = self.stats.copy()

        # Calculate average confidence
        scores = list(stats["confidence_scores"])
        if scores:
            stats["average_confidence"] = sum(scores) / len(scores)
        else:
            stats["average_confidence"] = 0.0

        # Calculate coverage percentage
        analyzed = int(stats["properties_analyzed"])
        if analyzed > 0:
            stats["coverage_percentage"] = int(stats["constraints_added"]) / analyzed * 100
        else:
            stats["coverage_percentage"] = 0.0

        return stats


if __name__ == "__main__":
    # Test the enricher (json imported at module top)
    # Load a test spec
    test_spec_path = Path("specs/enriched/dns.json")
    if test_spec_path.exists():
        with test_spec_path.open() as f:
            spec = json.load(f)

        # Run enricher
        enricher = ConstraintEnricher(config_path=Path("config/constraint_patterns.yaml"))
        enriched_spec = enricher.enrich_spec(spec)

        # Print statistics
        stats = enricher.get_stats()
        print(json.dumps(stats, indent=2))

        # Save enriched spec
        output_path = Path("specs/enriched/dns_with_constraints.json")
        with output_path.open("w") as f:
            json.dump(enriched_spec, f, indent=2)
        print(f"Enriched spec saved to {output_path}")
    else:
        print(f"Test spec not found: {test_spec_path}")
