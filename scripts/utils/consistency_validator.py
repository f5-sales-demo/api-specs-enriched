#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Consistency validator for OpenAPI specifications.

Validates naming conventions and structural consistency,
reporting issues without auto-fixing them.
"""

import re
from collections import defaultdict
from pathlib import Path
from typing import Any, ClassVar

import yaml


class ConsistencyValidator:
    """Validates naming and structural consistency in OpenAPI specs.

    Reports issues with:
    - Parameter naming conventions
    - Schema naming patterns
    - OperationId consistency
    - Response structure patterns
    """

    # Common parameter naming patterns
    PARAMETER_PATTERNS: ClassVar[dict[str, re.Pattern[str]]] = {
        "path_params": re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$"),  # snake_case
        "query_params": re.compile(
            r"^[a-z][a-z0-9]*([._][a-z0-9]+)*$",
        ),  # snake_case or dot.notation
        "header_params": re.compile(r"^[A-Z][a-zA-Z0-9]*(-[A-Z][a-zA-Z0-9]*)*$"),  # Title-Case
    }

    # Schema naming patterns
    SCHEMA_PATTERNS: ClassVar[dict[str, re.Pattern[str]]] = {
        "request": re.compile(r"(Request|Input|Create|Update|Payload)$"),
        "response": re.compile(r"(Response|Output|Result|Reply)$"),
        "type": re.compile(r"(Type|Spec|Config|Settings|Options)$"),
    }

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize with configuration from file.

        Args:
            config_path: Path to enrichment.yaml config.
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / "config" / "enrichment.yaml"

        # Default configuration
        self._validate_parameters = True
        self._validate_schemas = True
        self._validate_operation_ids = True
        self._severity_threshold = "warning"  # info, warning, error

        self._load_config(config_path)

        # Issue collection
        self._issues: list[dict[str, Any]] = []

    def _load_config(self, config_path: Path) -> None:
        """Load configuration from YAML config."""
        if not config_path.exists():
            return

        with config_path.open() as f:
            config = yaml.safe_load(f) or {}

        consistency_config = config.get("consistency_validation", {})
        self._validate_parameters = consistency_config.get("validate_parameters", True)
        self._validate_schemas = consistency_config.get("validate_schemas", True)
        self._validate_operation_ids = consistency_config.get("validate_operation_ids", True)
        self._severity_threshold = consistency_config.get("severity_threshold", "warning")

    def validate(self, spec: dict[str, Any]) -> list[dict[str, Any]]:
        """Validate specification for consistency issues.

        Args:
            spec: OpenAPI specification dictionary.

        Returns:
            List of consistency issues found.
        """
        self._issues = []

        if self._validate_parameters:
            self._check_parameter_naming(spec)

        if self._validate_schemas:
            self._check_schema_naming(spec)

        if self._validate_operation_ids:
            self._check_operation_ids(spec)

        # Check for deprecated markers
        self._check_deprecation_markers(spec)

        # Check for duplicate operationIds
        self._check_duplicate_operation_ids(spec)

        return self._filter_issues_by_severity()

    def _add_issue(
        self,
        severity: str,
        category: str,
        message: str,
        location: str,
        suggestion: str | None = None,
    ) -> None:
        """Add an issue to the collection.

        Args:
            severity: Issue severity (info, warning, error).
            category: Issue category.
            message: Issue description.
            location: Where the issue was found.
            suggestion: Optional fix suggestion.
        """
        issue = {
            "severity": severity,
            "category": category,
            "message": message,
            "location": location,
        }
        if suggestion:
            issue["suggestion"] = suggestion

        self._issues.append(issue)

    def _filter_issues_by_severity(self) -> list[dict[str, Any]]:
        """Filter issues based on severity threshold."""
        severity_levels = {"info": 0, "warning": 1, "error": 2}
        threshold = severity_levels.get(self._severity_threshold, 1)

        return [
            issue
            for issue in self._issues
            if severity_levels.get(issue["severity"], 0) >= threshold
        ]

    def _check_parameter_naming(self, spec: dict[str, Any]) -> None:
        """Check parameter naming conventions."""
        for path, path_item in spec.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue

            # Check path-level parameters
            for param in path_item.get("parameters", []):
                self._validate_parameter(param, f"paths.{path}")

            # Check operation-level parameters
            for method, operation in path_item.items():
                if method.lower() not in (
                    "get",
                    "post",
                    "put",
                    "patch",
                    "delete",
                    "head",
                    "options",
                    "trace",
                ):
                    continue

                if not isinstance(operation, dict):
                    continue

                for param in operation.get("parameters", []):
                    self._validate_parameter(param, f"paths.{path}.{method}")

    def _validate_parameter(self, param: dict[str, Any], location: str) -> None:
        """Validate a single parameter."""
        if not isinstance(param, dict):
            return

        name = param.get("name", "")
        param_in = param.get("in", "")

        if not name:
            self._add_issue(
                severity="error",
                category="parameter",
                message="Parameter missing 'name' field",
                location=location,
            )
            return

        # Check naming pattern based on parameter location
        if param_in == "path":
            # Path parameters should use consistent naming (usually snake_case or camelCase)
            if "{" in name or "}" in name:
                self._add_issue(
                    severity="warning",
                    category="parameter",
                    message=f"Path parameter '{name}' contains braces",
                    location=location,
                    suggestion=f"Use '{name.strip('{}')}' without braces in parameter definition",
                )

        elif param_in == "query":
            # Check for inconsistent naming (e.g., metadata.namespace vs namespace)
            if "." in name and "_" in name:
                self._add_issue(
                    severity="info",
                    category="parameter",
                    message=f"Query parameter '{name}' mixes dot and underscore notation",
                    location=location,
                    suggestion="Consider using consistent notation",
                )

        elif param_in == "header" and not re.match(r"^[A-Z]", name) and not name.startswith("x-"):
            # Headers should typically be Title-Case
            self._add_issue(
                severity="info",
                category="parameter",
                message=f"Header parameter '{name}' should start with uppercase",
                location=location,
                suggestion=f"Consider using '{name.title()}'",
            )

    def _check_schema_naming(self, spec: dict[str, Any]) -> None:
        """Check schema naming conventions."""
        schemas = spec.get("components", {}).get("schemas", {})

        # Track naming pattern usage
        suffix_counts: dict[str, int] = defaultdict(int)
        no_suffix_count = 0

        for schema_name, schema_def in schemas.items():
            if not isinstance(schema_def, dict):
                continue

            # Check for common suffix patterns
            has_suffix = False
            for pattern_name, pattern in self.SCHEMA_PATTERNS.items():
                if pattern.search(schema_name):
                    suffix_counts[pattern_name] += 1
                    has_suffix = True
                    break

            if not has_suffix:
                no_suffix_count += 1

            # Check for mixed naming conventions
            # Skip known prefixes like "ves_io_"
            if (
                "_" in schema_name
                and any(c.isupper() for c in schema_name[1:])
                and not schema_name.startswith("ves_io_")
                and not schema_name.startswith("schema")
            ):
                self._add_issue(
                    severity="info",
                    category="schema",
                    message=f"Schema '{schema_name}' mixes snake_case and CamelCase",
                    location=f"components.schemas.{schema_name}",
                    suggestion="Consider using consistent naming convention",
                )

        # Report on naming pattern distribution
        total_schemas = len(schemas)
        if total_schemas > 100 and no_suffix_count > total_schemas * 0.5:
            # Only report for larger specs
            self._add_issue(
                severity="info",
                category="schema",
                message=f"{no_suffix_count}/{total_schemas} schemas lack type suffix (Type, Request, Response, etc.)",
                location="components.schemas",
                suggestion="Consider adding descriptive suffixes for clarity",
            )

    def _check_operation_ids(self, spec: dict[str, Any]) -> None:
        """Check operationId consistency."""
        operation_id_patterns: dict[str, int] = defaultdict(int)

        for path, path_item in spec.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue

            for method, operation in path_item.items():
                if method.lower() not in (
                    "get",
                    "post",
                    "put",
                    "patch",
                    "delete",
                    "head",
                    "options",
                    "trace",
                ):
                    continue

                if not isinstance(operation, dict):
                    continue

                operation_id = operation.get("operationId", "")
                if not operation_id:
                    self._add_issue(
                        severity="warning",
                        category="operationId",
                        message="Operation missing operationId",
                        location=f"paths.{path}.{method}",
                        suggestion="Add operationId for better SDK generation",
                    )
                    continue

                # Detect pattern style
                if "." in operation_id:
                    operation_id_patterns["dot.notation"] += 1
                elif "_" in operation_id:
                    operation_id_patterns["snake_case"] += 1
                elif operation_id[0].islower() and any(c.isupper() for c in operation_id[1:]):
                    operation_id_patterns["camelCase"] += 1
                else:
                    operation_id_patterns["other"] += 1

        # Check for mixed patterns
        if len([p for p, c in operation_id_patterns.items() if c > 0]) > 1:
            pattern_summary = ", ".join(
                f"{p}: {c}" for p, c in operation_id_patterns.items() if c > 0
            )
            self._add_issue(
                severity="info",
                category="operationId",
                message=f"Mixed operationId patterns detected: {pattern_summary}",
                location="global",
                suggestion="Consider standardizing operationId naming pattern",
            )

    def _check_deprecation_markers(self, spec: dict[str, Any]) -> None:
        """Check for deprecation markers."""
        deprecated_count = 0

        for path_item in spec.get("paths", {}).values():
            if not isinstance(path_item, dict):
                continue

            for method, operation in path_item.items():
                if method.lower() not in (
                    "get",
                    "post",
                    "put",
                    "patch",
                    "delete",
                    "head",
                    "options",
                    "trace",
                ):
                    continue

                if isinstance(operation, dict) and operation.get("deprecated"):
                    deprecated_count += 1

        # Report on deprecation usage
        total_operations = sum(
            1
            for path_item in spec.get("paths", {}).values()
            if isinstance(path_item, dict)
            for method, op in path_item.items()
            if method.lower()
            in ("get", "post", "put", "patch", "delete", "head", "options", "trace")
            and isinstance(op, dict)
        )

        if deprecated_count == 0 and total_operations > 50:
            self._add_issue(
                severity="info",
                category="deprecation",
                message=f"No deprecated operations found among {total_operations} operations",
                location="global",
                suggestion="Consider adding deprecated: true for operations being phased out",
            )

    def _check_duplicate_operation_ids(self, spec: dict[str, Any]) -> None:
        """Check for duplicate operationIds."""
        operation_ids: dict[str, list[str]] = defaultdict(list)

        for path, path_item in spec.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue

            for method, operation in path_item.items():
                if method.lower() not in (
                    "get",
                    "post",
                    "put",
                    "patch",
                    "delete",
                    "head",
                    "options",
                    "trace",
                ):
                    continue

                if not isinstance(operation, dict):
                    continue

                operation_id = operation.get("operationId", "")
                if operation_id:
                    operation_ids[operation_id].append(f"{method.upper()} {path}")

        # Report duplicates
        for op_id, locations in operation_ids.items():
            if len(locations) > 1:
                self._add_issue(
                    severity="error",
                    category="operationId",
                    message=f"Duplicate operationId '{op_id}' used in {len(locations)} operations",
                    location="; ".join(locations[:3]) + ("..." if len(locations) > 3 else ""),
                    suggestion="Each operation must have a unique operationId",
                )

    def get_stats(self) -> dict[str, Any]:
        """Return statistics about validation."""
        issue_counts: defaultdict[str, int] = defaultdict(int)
        for issue in self._issues:
            issue_counts[issue["severity"]] += 1
            issue_counts[f"category_{issue['category']}"] += 1

        return {
            "total_issues": len(self._issues),
            "errors": issue_counts.get("error", 0),
            "warnings": issue_counts.get("warning", 0),
            "info": issue_counts.get("info", 0),
            "by_category": {
                "parameter": issue_counts.get("category_parameter", 0),
                "schema": issue_counts.get("category_schema", 0),
                "operationId": issue_counts.get("category_operationId", 0),
                "deprecation": issue_counts.get("category_deprecation", 0),
            },
        }

    def get_report(self) -> dict[str, Any]:
        """Generate a detailed validation report.

        Returns:
            Dictionary with categorized issues and summary.
        """
        return {
            "summary": self.get_stats(),
            "issues": self._filter_issues_by_severity(),
        }
