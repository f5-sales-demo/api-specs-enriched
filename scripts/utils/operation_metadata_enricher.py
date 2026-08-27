# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Operation Metadata Enricher for OpenAPI specifications.

Adds operation-level metadata for CLI tooling and AI assistants:
- Required fields extraction (from request body schema)
- Danger level classification (low/medium/high)
- Side effects determination
- Operation purpose descriptions

Conservative approach: only applies well-established patterns.
Uses x-f5xc-* extensions to store operation metadata.

Issue: #292 - Migrated from x-ves-* to x-f5xc-* namespace
"""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class OperationEnrichmentStats:
    """Statistics from operation metadata enrichment."""

    operations_enriched: int = 0
    required_fields_added: int = 0
    danger_levels_assigned: int = 0
    side_effects_documented: int = 0

    def to_dict(self) -> dict[str, int]:
        """Convert stats to dictionary."""
        return {
            "operations_enriched": self.operations_enriched,
            "required_fields_added": self.required_fields_added,
            "danger_levels_assigned": self.danger_levels_assigned,
            "side_effects_documented": self.side_effects_documented,
        }


class OperationMetadataEnricher:
    """Add operation-level metadata for API operations.

    Enriches operations with:
    - x-f5xc-required-fields: List of required field paths
    - x-f5xc-danger-level: low/medium/high risk classification
    - x-f5xc-confirmation-required: Boolean for dangerous operations
    - x-f5xc-side-effects: Create/modify/delete effects

    Configuration-driven from operation_metadata.yaml.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize enricher with operation metadata configuration.

        Args:
            config_path: Path to operation_metadata.yaml config.
                        Defaults to config/operation_metadata.yaml.
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / "config" / "operation_metadata.yaml"

        self.config_path = config_path
        self.danger_levels: dict[str, Any] = {}
        self.required_fields_config: dict[str, Any] = {}
        self.query_operations: dict[str, Any] = {}
        self.issuance_operations: dict[str, Any] = {}
        self.collection_operations: dict[str, Any] = {}
        self.action_operations: dict[str, Any] = {}
        self.extension_prefix = "x-f5xc"
        self.stats = OperationEnrichmentStats()

        self._load_config()

    def _load_config(self) -> None:
        """Load operation metadata configuration from YAML config."""
        if not self.config_path.exists():
            self._use_default_config()
            return

        try:
            with self.config_path.open() as f:
                config = yaml.safe_load(f) or {}

            self.danger_levels = config.get("danger_levels", {})
            self.required_fields_config = config.get("required_fields", {})
            self.query_operations = config.get("query_operations", {})
            self.issuance_operations = config.get("issuance_operations", {})
            self.collection_operations = config.get("collection_operations", {})
            self.action_operations = config.get("action_operations", {})
            self.extension_prefix = config.get("extension_prefix", "x-f5xc")
        except Exception:
            self._use_default_config()

    def _use_default_config(self) -> None:
        """Use built-in default operation metadata rules."""
        self.danger_levels = {
            "method_base_levels": {
                "GET": "low",
                "HEAD": "low",
                "OPTIONS": "low",
                "POST": "medium",
                "PUT": "medium",
                "PATCH": "medium",
                "DELETE": "high",
            },
            "escalation_patterns": [
                {
                    "pattern": r"DELETE.*/namespace",
                    "level": "high",
                    "reason": "Deletes entire namespace",
                },
                {
                    "pattern": r"DELETE.*/(security|firewall|policy)",
                    "level": "high",
                    "reason": "Security-critical deletion",
                },
                {
                    "pattern": r"POST.*/(system|global)_",
                    "level": "medium",
                    "reason": "System-level operation",
                },
            ],
        }

        self.required_fields_config = {
            "standard_create_fields": ["metadata.name", "metadata.namespace"],
        }
        self.query_operations = {}
        self.issuance_operations = {}
        self.collection_operations = {}
        self.action_operations = {}

        self.extension_prefix = "x-f5xc"

    def enrich_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Enrich OpenAPI specification with operation metadata.

        Args:
            spec: OpenAPI specification dictionary

        Returns:
            Specification with added operation metadata
        """
        if "paths" not in spec:
            return spec

        spec_copy = spec.copy()
        paths = spec_copy.get("paths", {})

        for path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue

            for method, operation in path_item.items():
                if not isinstance(operation, dict):
                    continue

                # Skip non-operation items like parameters, servers, etc.
                if method.lower() not in [
                    "get",
                    "post",
                    "put",
                    "patch",
                    "delete",
                    "head",
                    "options",
                    "trace",
                ]:
                    continue

                self._enrich_operation(operation, method.upper(), path)

        return spec_copy

    def _enrich_operation(
        self,
        operation: dict[str, Any],
        method: str,
        path: str,
    ) -> None:
        """Enrich a single operation with metadata.

        Modifies operation dict in-place.

        Args:
            operation: Operation definition
            method: HTTP method (GET, POST, DELETE, etc.)
            path: API path
        """
        self.stats.operations_enriched += 1
        operation_id = operation.get("operationId", "")
        operation_role, special_contract = self._response_operation_contract(operation_id)
        if operation_role is not None:
            allowed_methods = {"POST"} if operation_role == "action" else {"GET", "POST"}
            if method not in allowed_methods:
                raise ValueError(
                    f"{operation_role} operation {operation_id} must use "
                    f"{'POST' if operation_role == 'action' else 'GET or POST'}, got {method}"
                )
            if method == "POST" and not self._request_schema_ref(operation):
                raise ValueError(f"POST operation {operation_id} must have a request schema")
            if (
                method == "GET"
                and operation_role in {"query", "issuance"}
                and not self._query_parameters(operation)
            ):
                raise ValueError(f"GET operation {operation_id} must have query parameters")
            if (
                method == "GET"
                and operation_role == "collection"
                and not self._input_parameters(operation)
            ):
                raise ValueError(
                    f"GET collection operation {operation_id} must have path or query parameters"
                )
            if not self._response_schema_refs(operation):
                raise ValueError(f"operation {operation_id} must have a response schema")
            operation[f"{self.extension_prefix}-operation-role"] = operation_role
            operation[f"{self.extension_prefix}-terraform-name"] = special_contract[
                "terraform_name"
            ]

        # Extract and add required fields
        required_fields = (
            list(special_contract.get("required_fields", []))
            if operation_role is not None
            else self._extract_required_fields(operation, method)
        )
        if required_fields:
            operation[f"{self.extension_prefix}-required-fields"] = required_fields
            self.stats.required_fields_added += 1

        # Calculate and assign danger level
        danger_level = (
            "low"
            if operation_role in {"query", "collection"}
            else "medium"
            if operation_role in {"issuance", "action"}
            else self._calculate_danger_level(method, path, operation)
        )
        operation[f"{self.extension_prefix}-danger-level"] = danger_level
        self.stats.danger_levels_assigned += 1

        # Add confirmation requirement for dangerous operations
        if danger_level == "high":
            operation[f"{self.extension_prefix}-confirmation-required"] = True

        # Determine and add side effects
        side_effects = (
            {}
            if operation_role in {"query", "collection"}
            else {"creates": list(special_contract.get("creates", []))}
            if operation_role == "issuance"
            else {"modifies": list(special_contract.get("modifies", []))}
            if operation_role == "action"
            else self._determine_side_effects(method, path, operation)
        )
        if side_effects:
            operation[f"{self.extension_prefix}-side-effects"] = side_effects
            self.stats.side_effects_documented += 1

        # Build and add comprehensive metadata (dual-format approach)
        comprehensive_metadata = self._build_comprehensive_metadata(
            method,
            path,
            operation,
            required_fields,
            danger_level,
            side_effects,
        )
        if operation_role in {"query", "collection"}:
            comprehensive_metadata["conditions"]["postconditions"] = [
                "Requested data returned",
                "Tenant state unchanged",
            ]
        elif operation_role == "issuance":
            comprehensive_metadata["conditions"]["postconditions"] = [
                "One-time site node token issued",
                "Credential returned only in the response",
            ]
        elif operation_role == "action":
            comprehensive_metadata["conditions"]["postconditions"] = [
                "Action request accepted by the API",
                "Asynchronous convergence not implied",
            ]
        if comprehensive_metadata:
            operation[f"{self.extension_prefix}-operation-metadata"] = comprehensive_metadata

    @staticmethod
    def _request_schema_ref(operation: dict[str, Any]) -> str | None:
        schema = (
            operation.get("requestBody", {})
            .get("content", {})
            .get("application/json", {})
            .get("schema", {})
        )
        ref = schema.get("$ref") if isinstance(schema, dict) else None
        return ref if isinstance(ref, str) and ref else None

    @staticmethod
    def _response_schema_refs(operation: dict[str, Any]) -> set[str]:
        refs: set[str] = set()
        for status, response in operation.get("responses", {}).items():
            if not str(status).startswith("2") or not isinstance(response, dict):
                continue
            schema = response.get("content", {}).get("application/json", {}).get("schema", {})
            ref = schema.get("$ref") if isinstance(schema, dict) else None
            if isinstance(ref, str) and ref:
                refs.add(ref)
        return refs

    @staticmethod
    def _query_parameters(operation: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            parameter
            for parameter in operation.get("parameters", [])
            if isinstance(parameter, dict) and parameter.get("in") == "query"
        ]

    @staticmethod
    def _input_parameters(operation: dict[str, Any]) -> list[dict[str, Any]]:
        """Return explicit path and query inputs for a response operation."""
        return [
            parameter
            for parameter in operation.get("parameters", [])
            if isinstance(parameter, dict) and parameter.get("in") in {"path", "query"}
        ]

    def _response_operation_contract(self, operation_id: str) -> tuple[str | None, dict[str, Any]]:
        """Resolve one source-owned response-operation role and validate its public name."""
        role_maps = {
            "query": self.query_operations,
            "issuance": self.issuance_operations,
            "collection": self.collection_operations,
            "action": self.action_operations,
        }
        matches = [
            (role, operations[operation_id])
            for role, operations in role_maps.items()
            if operation_id in operations
        ]
        if len(matches) > 1:
            roles = ", ".join(role for role, _ in matches)
            raise ValueError(f"operation {operation_id} has multiple response roles: {roles}")
        if not matches:
            return None, {}

        role, contract = matches[0]
        if not isinstance(contract, dict):
            raise TypeError(f"{role} operation {operation_id} contract must be an object")
        terraform_name = contract.get("terraform_name")
        if not isinstance(terraform_name, str) or not re.fullmatch(
            r"[a-z][a-z0-9_]*", terraform_name
        ):
            raise ValueError(f"{role} operation {operation_id} requires a valid terraform_name")
        return role, contract

    def _build_comprehensive_metadata(
        self,
        method: str,
        path: str,
        operation: dict[str, Any],
        required_fields: list[str],
        danger_level: str,
        side_effects: dict[str, Any],
    ) -> dict[str, Any]:
        """Build comprehensive operation metadata object.

        Creates x-f5xc-operation-metadata containing all operation context and constraints.
        Preserves existing purpose field if set by OperationDescriptionEnricher.

        Args:
            method: HTTP method
            path: API path
            operation: Operation definition
            required_fields: List of required fields
            danger_level: Danger level classification
            side_effects: Side effects dictionary

        Returns:
            Comprehensive metadata dictionary
        """
        resource_type = self._extract_resource_type(path)
        optional_fields = self._identify_optional_fields(operation, method)
        field_docs = self._generate_field_docs(operation)
        prerequisites = self._determine_prerequisites(method, path)
        postconditions = self._determine_postconditions(method, path)
        common_errors = self._generate_common_errors(operation)
        performance_impact = self._assess_performance_impact(method, path, operation)

        # PRESERVE existing purpose from OperationDescriptionEnricher if present
        existing_metadata = operation.get(f"{self.extension_prefix}-operation-metadata", {})
        existing_purpose = existing_metadata.get("purpose")
        purpose = existing_purpose or self._generate_purpose(method, path, resource_type)

        return {
            "purpose": purpose,
            "required_fields": required_fields,
            "optional_fields": optional_fields,
            "field_docs": field_docs,
            "conditions": {
                "prerequisites": prerequisites,
                "postconditions": postconditions,
            },
            "side_effects": side_effects or {},
            "danger_level": danger_level,
            "confirmation_required": danger_level == "high",
            "common_errors": common_errors,
            "performance_impact": performance_impact,
        }

    def _generate_purpose(self, method: str, path: str, resource_type: str) -> str:
        """Generate fallback purpose description for an operation.

        This method generates verb-first descriptions as a fallback when
        OperationDescriptionEnricher has not provided a DRY-compliant,
        noun-first description.

        Priority:
        1. OperationDescriptionEnricher's noun-first description (if present)
        2. This method's verb-first fallback (if no prior description)

        Args:
            method: HTTP method
            path: API path
            resource_type: Resource type identifier

        Returns:
            Purpose description (verb-first fallback)
        """
        if method == "GET":
            if "{name}" in path or "{id}" in path:
                return f"Retrieve specific {resource_type}"
            return f"List all {resource_type}s"
        if method == "POST":
            return f"Create new {resource_type}"
        if method == "PUT":
            return f"Replace existing {resource_type}"
        if method == "PATCH":
            return f"Update {resource_type}"
        if method == "DELETE":
            return f"Delete {resource_type}"

        return f"Perform {resource_type} operation"

    def _identify_optional_fields(
        self,
        operation: dict[str, Any],
        method: str,  # noqa: ARG002
    ) -> list[str]:
        """Identify optional fields in request body.

        Args:
            operation: Operation definition
            method: HTTP method

        Returns:
            List of optional field names
        """
        optional = []

        # Get request body schema
        request_body = operation.get("requestBody", {})
        if request_body:
            content = request_body.get("content", {})
            for media_content in content.values():
                schema = media_content.get("schema", {})
                if not schema:
                    continue

                # Optional fields = all properties - required fields
                all_props = set(schema.get("properties", {}).keys())
                required = set(schema.get("required", []))
                optional.extend(list(all_props - required))

        return sorted(set(optional))

    def _generate_field_docs(self, operation: dict[str, Any]) -> dict[str, str]:
        """Extract documentation for request body fields.

        Args:
            operation: Operation definition

        Returns:
            Dictionary mapping field names to their descriptions
        """
        field_docs = {}

        request_body = operation.get("requestBody", {})
        if request_body:
            content = request_body.get("content", {})
            for media_content in content.values():
                schema = media_content.get("schema", {})
                if not schema:
                    continue

                # Extract field descriptions from properties
                for field_name, field_schema in schema.get("properties", {}).items():
                    if isinstance(field_schema, dict):
                        description = field_schema.get("description")
                        if description:
                            field_docs[field_name] = description

        return field_docs

    def _determine_prerequisites(self, method: str, path: str) -> list[str]:  # noqa: ARG002
        """Determine prerequisites for successful operation execution.

        Args:
            method: HTTP method
            path: API path

        Returns:
            List of prerequisite descriptions
        """
        prerequisites = []

        # Namespace requirement
        if "namespace" in path:
            prerequisites.append("Active namespace")

        # Resource-specific prerequisites
        if "origin_pools" in path or "pool" in path:
            prerequisites.append("Valid origin targets")
        if "certificate" in path:
            prerequisites.append("Certificate file or data")
        if "policy" in path or "rule" in path:
            prerequisites.append("Policy parameters defined")

        return prerequisites

    def _determine_postconditions(self, method: str, path: str) -> list[str]:
        """Determine postconditions after successful operation.

        Args:
            method: HTTP method
            path: API path

        Returns:
            List of postcondition descriptions
        """
        postconditions = []

        if method == "POST":
            resource_type = self._extract_resource_type(path)
            postconditions.append(f"{resource_type.capitalize()} resource created")
            postconditions.append("Resource assigned unique identifier")

        elif method in ["PUT", "PATCH"]:
            postconditions.append("Resource updated with new values")

        elif method == "DELETE":
            postconditions.append("Resource removed from system")
            if "namespace" in path:
                postconditions.append("Associated resources may be affected")

        return postconditions

    def _generate_common_errors(self, operation: dict[str, Any]) -> list[dict[str, str | int]]:
        """Generate documentation for common error codes.

        Args:
            operation: Operation definition

        Returns:
            List of error documentation dictionaries
        """
        errors: list[dict[str, str | int]] = []

        # Get response codes from operation
        responses = operation.get("responses", {})

        # Map common HTTP status codes to user-friendly messages
        error_mappings = {
            "400": {
                "message": "Invalid request parameters",
                "solution": "Verify request format and required fields",
            },
            "401": {
                "message": "Authentication required",
                "solution": "Provide valid API credentials",
            },
            "403": {
                "message": "Permission denied",
                "solution": "Check access permissions for this operation",
            },
            "404": {
                "message": "Resource not found",
                "solution": "Verify resource name, namespace, and path",
            },
            "409": {
                "message": "Resource already exists",
                "solution": "Use different name or update existing resource",
            },
            "422": {
                "message": "Validation failed",
                "solution": "Check field values against constraints",
            },
            "429": {
                "message": "Rate limit exceeded",
                "solution": "Wait before retrying the operation",
            },
            "500": {
                "message": "Server error",
                "solution": "Retry operation or contact support",
            },
        }

        # Include mappings for response codes in operation
        errors.extend(
            [
                {
                    "code": int(code) if code.isdigit() else code,
                    **error_mappings[code],
                }
                for code in responses
                if code in error_mappings
            ],
        )

        return errors

    def _assess_performance_impact(
        self,
        method: str,
        path: str,
        operation: dict[str, Any],  # noqa: ARG002
    ) -> dict[str, str]:
        """Assess performance impact of operation.

        Args:
            method: HTTP method
            path: API path
            operation: Operation definition

        Returns:
            Performance impact dictionary
        """
        impact = {"latency": "low", "resource_usage": "low"}

        # Check for bulk operations
        if "bulk" in path or "batch" in path:
            impact["latency"] = "high"
            impact["resource_usage"] = "high"

        # Check for list operations
        elif method == "GET" and "{" not in path.rsplit("/", maxsplit=1)[-1]:
            impact["latency"] = "moderate"
            impact["resource_usage"] = "moderate"

        # Check for expensive operations
        elif method == "DELETE" and "namespace" in path:
            impact["latency"] = "high"
            impact["resource_usage"] = "moderate"

        return impact

    def _extract_required_fields(
        self,
        operation: dict[str, Any],
        method: str,
    ) -> list[str]:
        """Extract required fields from operation request body.

        Args:
            operation: Operation definition
            method: HTTP method

        Returns:
            List of required field paths (e.g., ["metadata.name", "metadata.namespace"])
        """
        required = []

        # Get request body schema
        request_body = operation.get("requestBody", {})
        if request_body:
            # Get schema from request body content
            content = request_body.get("content", {})
            for media_content in content.values():
                schema = media_content.get("schema", {})
                if not schema:
                    continue

                # Extract schema.required fields
                if "required" in schema:
                    required.extend(schema["required"])

                # Handle nested properties with their own required fields
                for prop_name, prop_schema in schema.get("properties", {}).items():
                    if isinstance(prop_schema, dict) and "required" in prop_schema:
                        nested_required = prop_schema["required"]
                        required.extend(f"{prop_name}.{field}" for field in nested_required)

        # For path parameters, extract those that are required
        required.extend(
            f"path.{param.get('name')}"
            for param in operation.get("parameters", [])
            if param.get("in") == "path" and param.get("required")
        )

        # Add standard create fields for POST operations (applies even without requestBody)
        if method == "POST":
            for std_field in self.required_fields_config.get(
                "standard_create_fields",
                [],
            ):
                if std_field not in required:
                    required.append(std_field)

        return sorted(set(required))

    def _calculate_danger_level(
        self,
        method: str,
        path: str,
        operation: dict[str, Any],
    ) -> str:
        """Calculate danger level for an operation.

        Args:
            method: HTTP method
            path: API path
            operation: Operation definition

        Returns:
            Danger level: "low", "medium", or "high"
        """
        method_levels = self.danger_levels.get("method_base_levels", {})
        base_level = method_levels.get(method, "medium")

        # Check for escalation patterns
        path_method_str = f"{method} {path}"

        for escalation in self.danger_levels.get("escalation_patterns", []):
            pattern_str = escalation.get("pattern", "")
            try:
                if re.search(pattern_str, path_method_str):
                    return escalation.get("level", "high")
            except re.error:
                continue

        # Check for force/cascade flags in parameters
        has_dangerous_param = False
        for param in operation.get("parameters", []):
            param_name = param.get("name", "").lower()
            if param_name in ["force", "cascade", "delete_options"]:
                has_dangerous_param = True
                break

        # Escalate level if dangerous param found
        if has_dangerous_param:
            if base_level == "low":
                return "medium"
            if base_level == "medium":
                return "high"

        return base_level

    def _determine_side_effects(
        self,
        method: str,
        path: str,
        _operation: dict[str, Any],
    ) -> dict[str, Any]:
        """Determine side effects of an operation.

        Args:
            method: HTTP method
            path: API path
            operation: Operation definition

        Returns:
            Dictionary with creates/modifies/deletes arrays
        """
        side_effects: dict[str, Any] = {
            "creates": [],
            "modifies": [],
            "deletes": [],
        }

        # Infer from HTTP method and path
        resource_type = self._extract_resource_type(path)

        if method == "POST":
            side_effects["creates"].append(resource_type)
        elif method in ["PUT", "PATCH"]:
            side_effects["modifies"].append(resource_type)
        elif method == "DELETE":
            side_effects["deletes"].append(resource_type)

        # Check for related resources affected
        if "namespace" in path and method == "DELETE":
            # Deleting namespace affects contained resources
            side_effects["deletes"].append("contained_resources")

        # Remove empty arrays
        return {k: v for k, v in side_effects.items() if v}

    @staticmethod
    def _extract_resource_type(path: str) -> str:
        """Extract resource type from API path.

        Examples:
            /api/config/namespaces/{namespace}/http_loadbalancers → http-loadbalancer
            /api/config/namespaces/{namespace}/origin_pools → origin-pool

        Args:
            path: API path

        Returns:
            Resource type identifier in kebab-case
        """
        # Extract last path component that is not a parameter
        parts = [p for p in path.split("/") if p and not p.startswith("{")]

        if parts:
            # Take the last part and convert underscore to hyphen
            resource_type = parts[-1]
            # Remove trailing 's' for plurals
            resource_type = resource_type.removesuffix("s")
            return resource_type.replace("_", "-")

        return "resource"

    @staticmethod
    def _extract_domain(path: str) -> str:
        """Extract domain name from API path.

        Examples:
            /api/config/namespaces/{namespace}/http_loadbalancers → http-loadbalancer (implicit)
            /api/virtual/loadbalancers → virtual

        Args:
            path: API path

        Returns:
            Domain identifier
        """
        parts = path.split("/")

        # Try to find domain in path
        if len(parts) > 2 and parts[1] == "api":
            # /api/{domain}/...
            return parts[2]

        return "default"

    def get_stats(self) -> dict[str, int]:
        """Get enrichment statistics.

        Returns:
            Dictionary with enrichment metrics
        """
        return self.stats.to_dict()
