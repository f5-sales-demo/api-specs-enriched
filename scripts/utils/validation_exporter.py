# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Validation Specification Exporter.

Exports centralized validation rules to a standalone JSON format
for consumption by downstream projects (CLI tools, Terraform providers, AI assistants).

Usage:
    python -m scripts.utils.validation_exporter
    python -m scripts.utils.validation_exporter --output path/to/output.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scripts.utils.json_writer import write_json_file

from .build_stamp import artifact_timestamp


@dataclass
class ExporterStats:
    """Statistics from validation export."""

    resources_processed: int = 0
    required_fields_exported: int = 0
    enum_values_exported: int = 0
    constraints_exported: int = 0
    server_defaults_exported: int = 0
    recommended_values_exported: int = 0
    nested_recommended_exported: int = 0
    oneof_recommended_exported: int = 0
    advanced_options_exported: int = 0
    conditional_requirements_exported: int = 0
    minimum_configs_exported: int = 0
    oneof_defaults_exported: int = 0
    ui_vs_server_defaults_exported: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert stats to dictionary."""
        return {
            "resources_processed": self.resources_processed,
            "required_fields_exported": self.required_fields_exported,
            "enum_values_exported": self.enum_values_exported,
            "constraints_exported": self.constraints_exported,
            "server_defaults_exported": self.server_defaults_exported,
            "recommended_values_exported": self.recommended_values_exported,
            "nested_recommended_exported": self.nested_recommended_exported,
            "oneof_recommended_exported": self.oneof_recommended_exported,
            "advanced_options_exported": self.advanced_options_exported,
            "conditional_requirements_exported": self.conditional_requirements_exported,
            "minimum_configs_exported": self.minimum_configs_exported,
            "oneof_defaults_exported": self.oneof_defaults_exported,
            "ui_vs_server_defaults_exported": self.ui_vs_server_defaults_exported,
            "warnings": self.warnings,
        }


class ValidationExporter:
    """Export validation schema to standalone JSON format.

    Reads the centralized validation_schema.yaml and generates
    a machine-readable validation.json for downstream consumers.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize exporter with validation schema configuration.

        Args:
            config_path: Path to validation_schema.yaml config.
                        Defaults to config/validation_schema.yaml.
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / "config" / "validation_schema.yaml"

        self.config_path = config_path
        self.config: dict[str, Any] = {}
        self.stats = ExporterStats()

        self._load_config()

    def _load_config(self) -> None:
        """Load validation schema from YAML config."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Validation schema not found: {self.config_path}")

        with self.config_path.open() as f:
            self.config = yaml.safe_load(f) or {}

    def export(self, output_path: Path | None = None) -> dict[str, Any]:
        """Export validation schema to JSON format.

        Args:
            output_path: Optional path to write output. If provided,
                        writes JSON to file. Always returns the dict.

        Returns:
            Validation specification dictionary.
        """
        # Build the exported validation spec
        validation_spec = self._build_validation_spec()

        # Write to file if path provided — delegate to write_json_file so
        # the resulting docs/specifications/api/validation.json matches
        # Super-Linter's BIOME_FORMAT expectations.
        if output_path:
            write_json_file(validation_spec, output_path, indent=2, ensure_ascii=False)

        return validation_spec

    def _build_validation_spec(self) -> dict[str, Any]:
        """Build the complete validation specification."""
        spec: dict[str, Any] = {
            "$schema": self.config.get("export", {}).get(
                "json_schema_version",
                "https://json-schema.org/draft/2020-12/schema",
            ),
            "version": "2.1.0",  # Bumped for unified defaults structure
            "description": self.config.get("description", "F5 XC API Validation Specification"),
            "generated_at": artifact_timestamp(),
            "source": "api-specs-enriched",
        }

        # Build sections based on export configuration
        include_sections = self.config.get("export", {}).get("include_sections", [])

        if "required_fields" in include_sections or not include_sections:
            spec["required_fields"] = self._export_required_fields()

        if "enum_values" in include_sections or not include_sections:
            spec["enum_values"] = self._export_enum_values()

        if "constraints" in include_sections or not include_sections:
            spec["constraints"] = self._export_constraints()

        if "patterns" in include_sections or not include_sections:
            spec["patterns"] = self._export_patterns()

        if "conditional_requirements" in include_sections or not include_sections:
            spec["conditional_requirements"] = self._export_conditional_requirements()

        if "minimum_configurations" in include_sections or not include_sections:
            spec["minimum_configurations"] = self._export_minimum_configurations()

        # NEW: Unified defaults section (replaces 5 separate sections)
        # Consolidates server_defaults, recommended, advanced_options, oneof_defaults, ui_vs_server
        spec["defaults"] = self._export_defaults()

        # Add extension mapping for downstream tools
        spec["extensions"] = self._export_extension_mapping()

        # Add reconciliation strategy info
        spec["reconciliation"] = self.config.get("reconciliation", {})

        # Add export statistics
        spec["_stats"] = self.stats.to_dict()

        return spec

    def _export_required_fields(self) -> dict[str, Any]:
        """Export required fields by resource and operation."""
        required_fields_config = self.config.get("required_fields", {})
        result: dict[str, Any] = {
            "common": required_fields_config.get("common", {}),
            "resources": {},
        }

        resources = required_fields_config.get("resources", {})
        for resource_name, operations in resources.items():
            result["resources"][resource_name] = {}
            for operation, fields in operations.items():
                result["resources"][resource_name][operation] = fields
                self.stats.required_fields_exported += len(fields)
            self.stats.resources_processed += 1

        return result

    def _export_enum_values(self) -> dict[str, Any]:
        """Export enumeration/allowed values."""
        enum_config = self.config.get("enum_values", {})
        result: dict[str, Any] = {}

        for enum_name, enum_def in enum_config.items():
            result[enum_name] = {
                "description": enum_def.get("description", ""),
                "values": [],
                "default": None,
            }

            for value_def in enum_def.get("values", []):
                value_entry = {
                    "value": value_def.get("value"),
                    "description": value_def.get("description", ""),
                }
                result[enum_name]["values"].append(value_entry)
                self.stats.enum_values_exported += 1

                if value_def.get("is_default"):
                    result[enum_name]["default"] = value_def.get("value")

        return result

    def _export_constraints(self) -> dict[str, Any]:
        """Export type-level validation constraints."""
        constraints_config = self.config.get("constraints", {})
        result: dict[str, Any] = {
            "type_defaults": {},
        }

        type_defaults = constraints_config.get("type_defaults", {})
        for type_name, defaults in type_defaults.items():
            # Filter out comment fields
            result["type_defaults"][type_name] = {
                k: v for k, v in defaults.items() if k != "comment"
            }
            self.stats.constraints_exported += 1

        return result

    def _export_patterns(self) -> list[dict[str, Any]]:
        """Export pattern-based validation rules."""
        constraints_config = self.config.get("constraints", {})
        patterns = constraints_config.get("patterns", [])
        result: list[dict[str, Any]] = []

        for pattern_def in patterns:
            pattern_entry = {
                "pattern": pattern_def.get("pattern"),
                "constraints": pattern_def.get("constraints", {}),
                "confidence": pattern_def.get("confidence", 0.9),
            }
            # Optionally include comment for documentation
            if "comment" in pattern_def:
                pattern_entry["description"] = pattern_def["comment"]

            result.append(pattern_entry)
            self.stats.constraints_exported += 1

        return result

    def _export_server_defaults(self) -> dict[str, Any]:
        """Export server-applied default values."""
        defaults_config = self.config.get("server_defaults", {})
        result: dict[str, Any] = {
            "description": defaults_config.get("description", ""),
            "resources": {},
        }

        resources = defaults_config.get("resources", {})
        for resource_name, defaults in resources.items():
            result["resources"][resource_name] = defaults
            self.stats.server_defaults_exported += 1

        return result

    def _export_conditional_requirements(self) -> dict[str, Any]:
        """Export conditional and mutually exclusive field requirements."""
        conditional_config = self.config.get("conditional_requirements", {})
        result: dict[str, Any] = {
            "description": conditional_config.get("description", ""),
            "resources": {},
        }

        resources = conditional_config.get("resources", {})
        for resource_name, requirements in resources.items():
            result["resources"][resource_name] = {
                "mutually_exclusive": requirements.get("mutually_exclusive", []),
                "conditional": requirements.get("conditional", []),
            }
            self.stats.conditional_requirements_exported += len(
                requirements.get("mutually_exclusive", []),
            ) + len(requirements.get("conditional", []))

        return result

    def _export_minimum_configurations(self) -> dict[str, Any]:
        """Export minimum viable configurations."""
        min_config = self.config.get("minimum_configurations", {})
        result: dict[str, Any] = {
            "description": min_config.get("description", ""),
            "resources": {},
        }

        resources = min_config.get("resources", {})
        for resource_name, config in resources.items():
            result["resources"][resource_name] = {
                "description": config.get("description", ""),
            }

            # Parse and include example JSON if present
            example_json = config.get("example_json", "")
            if example_json:
                try:
                    result["resources"][resource_name]["example"] = json.loads(example_json)
                except json.JSONDecodeError as e:
                    self.stats.warnings.append(
                        f"Invalid JSON in minimum config for {resource_name}: {e}",
                    )
                    result["resources"][resource_name]["example_raw"] = example_json

            self.stats.minimum_configs_exported += 1

        return result

    def _export_defaults(self) -> dict[str, Any]:
        """Export all defaults in unified resource-centric structure.

        Consolidates server_applied, recommended, nested_recommended,
        oneof_recommended, advanced_options, oneof_choices, and ui_vs_server
        into per-resource structure.

        Returns:
            Unified defaults dictionary with resources as top-level keys.
        """
        result: dict[str, Any] = {
            "description": "All default values organized by resource type",
            "resources": {},
        }

        # Load server_defaults from validation_schema.yaml
        server_defaults_config = self.config.get("server_defaults", {})
        server_defaults_resources = server_defaults_config.get("resources", {})

        # Load discovered_defaults.yaml for recommended, advanced_options, oneof, ui_vs_server
        discovered_path = self.config_path.parent / "discovered_defaults.yaml"
        discovered_config: dict[str, Any] = {}
        if discovered_path.exists():
            with discovered_path.open() as f:
                discovered_config = yaml.safe_load(f) or {}

        discovered_resources = discovered_config.get("resources", {})

        # Build unified structure per resource
        all_resources = set(server_defaults_resources.keys()) | set(discovered_resources.keys())

        for resource_name in sorted(all_resources):
            resource_entry: dict[str, Any] = {}

            # Server-applied defaults (from validation_schema.yaml)
            if resource_name in server_defaults_resources:
                server_data = server_defaults_resources[resource_name]
                # Extract spec-level defaults, flattening the structure
                if isinstance(server_data, dict) and "spec" in server_data:
                    resource_entry["server_applied"] = server_data["spec"]
                else:
                    resource_entry["server_applied"] = server_data
                self.stats.server_defaults_exported += 1

            # From discovered_defaults.yaml
            discovered = discovered_resources.get(resource_name, {})

            # Recommended values (for required fields with sensible defaults)
            if "recommended" in discovered:
                resource_entry["recommended"] = discovered["recommended"]
                self.stats.recommended_values_exported += len(discovered["recommended"])

            # OneOf recommended variants (which variant is recommended for each OneOf group)
            if "oneof_recommended" in discovered:
                resource_entry["oneof_recommended"] = discovered["oneof_recommended"]
                self.stats.oneof_recommended_exported += len(discovered["oneof_recommended"])

            # Nested recommended values (recommended values within nested objects)
            nested = discovered.get("nested", {})
            nested_recommended: dict[str, Any] = {}
            for nested_name, nested_config in nested.items():
                if isinstance(nested_config, dict) and "recommended" in nested_config:
                    nested_recommended[nested_name] = nested_config["recommended"]
                    self.stats.nested_recommended_exported += len(nested_config["recommended"])
            if nested_recommended:
                resource_entry["nested_recommended"] = nested_recommended

            # Advanced options defaults
            if "advanced_options_defaults" in discovered:
                resource_entry["advanced_options"] = discovered["advanced_options_defaults"]
                self.stats.advanced_options_exported += 1

            # OneOf default selections
            if "oneof_defaults" in discovered:
                resource_entry["oneof_choices"] = discovered["oneof_defaults"]
                self.stats.oneof_defaults_exported += len(discovered["oneof_defaults"])

            # UI vs Server default discrepancies
            if "ui_vs_server_defaults" in discovered:
                resource_entry["ui_vs_server"] = discovered["ui_vs_server_defaults"]
                self.stats.ui_vs_server_defaults_exported += len(
                    discovered["ui_vs_server_defaults"],
                )

            # Also check for top-level defaults in discovered_defaults.yaml
            # (for resources like healthcheck that have defaults at resource level)
            # Combine conditions: resource has defaults AND not in server_defaults AND not already set
            if (
                "defaults" in discovered
                and resource_name not in server_defaults_resources
                and "server_applied" not in resource_entry
            ):
                resource_entry["server_applied"] = discovered["defaults"]
                self.stats.server_defaults_exported += 1

            if resource_entry:
                result["resources"][resource_name] = resource_entry

        return result

    def _export_extension_mapping(self) -> dict[str, str]:
        """Export OpenAPI extension field name mapping."""
        return self.config.get("extension_mapping", {})

    def _export_oneof_defaults(self) -> dict[str, Any]:
        """Export OneOf field default selections from discovered_defaults.yaml."""
        # Load discovered_defaults.yaml for oneof_defaults
        discovered_path = self.config_path.parent / "discovered_defaults.yaml"
        if not discovered_path.exists():
            return {}

        with discovered_path.open() as f:
            discovered_config = yaml.safe_load(f) or {}

        result: dict[str, Any] = {}
        resources = discovered_config.get("resources", {})

        for resource_name, resource_config in resources.items():
            oneof_defaults = resource_config.get("oneof_defaults", {})
            if oneof_defaults:
                result[resource_name] = oneof_defaults
                self.stats.oneof_defaults_exported += len(oneof_defaults)

        return result

    def _export_ui_vs_server_defaults(self) -> dict[str, Any]:
        """Export UI vs Server default discrepancies from discovered_defaults.yaml."""
        # Load discovered_defaults.yaml for ui_vs_server_defaults
        discovered_path = self.config_path.parent / "discovered_defaults.yaml"
        if not discovered_path.exists():
            return {}

        with discovered_path.open() as f:
            discovered_config = yaml.safe_load(f) or {}

        result: dict[str, Any] = {}
        resources = discovered_config.get("resources", {})

        for resource_name, resource_config in resources.items():
            ui_vs_server = resource_config.get("ui_vs_server_defaults", {})
            if ui_vs_server:
                result[resource_name] = ui_vs_server
                self.stats.ui_vs_server_defaults_exported += len(ui_vs_server)

        return result

    def _export_advanced_options_defaults(self) -> dict[str, Any]:
        """Export advanced_options defaults from discovered_defaults.yaml."""
        # Load discovered_defaults.yaml for advanced_options_defaults
        discovered_path = self.config_path.parent / "discovered_defaults.yaml"
        if not discovered_path.exists():
            return {}

        with discovered_path.open() as f:
            discovered_config = yaml.safe_load(f) or {}

        result: dict[str, Any] = {}
        resources = discovered_config.get("resources", {})

        for resource_name, resource_config in resources.items():
            advanced_defaults = resource_config.get("advanced_options_defaults", {})
            if advanced_defaults:
                result[resource_name] = advanced_defaults

        return result

    def get_stats(self) -> dict[str, Any]:
        """Get export statistics.

        Returns:
            Dictionary with export metrics.
        """
        return self.stats.to_dict()


def main() -> int:
    """Main entry point for validation export."""
    parser = argparse.ArgumentParser(
        description="Export F5 XC validation schema to standalone JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/validation_schema.yaml"),
        help="Path to validation schema config",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output path (defaults to config export.output_path)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print output to stdout without writing file",
    )

    args = parser.parse_args()

    try:
        exporter = ValidationExporter(config_path=args.config)

        # Determine output path
        output_path = args.output
        if not output_path and not args.dry_run:
            output_path = Path(
                exporter.config.get("export", {}).get(
                    "output_path",
                    "docs/specifications/api/validation.json",
                ),
            )

        # Export validation spec
        if args.dry_run:
            validation_spec = exporter.export()
            print(json.dumps(validation_spec, indent=2, ensure_ascii=False))
            # Print stats to stderr in dry-run mode to keep stdout clean for piping
            stats = exporter.get_stats()
            print(f"Statistics: {json.dumps(stats, indent=2)}", file=sys.stderr)
        else:
            exporter.export(output_path)
            print(f"Validation spec exported to: {output_path}")
            # Print stats to stdout when writing to file
            stats = exporter.get_stats()
            print(f"Statistics: {json.dumps(stats, indent=2)}")

        return 0

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error exporting validation: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
