# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Error Resolution Enricher for OpenAPI specifications.

Applies HTTP error diagnostics from config/error_resolution.yaml
to OpenAPI specifications and the index.json metadata.

Error resolution data helps AI assistants and CLI tools provide
contextual troubleshooting guidance when API errors occur.

Usage:
    enricher = ErrorResolutionEnricher()
    index = enricher.enrich_index(index)
    stats = enricher.get_stats()
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .extension_constants import X_F5XC_ERROR_RESOLUTION


@dataclass
class ErrorResolutionEnrichmentStats:
    """Statistics from error resolution enrichment."""

    indexes_processed: int = 0
    http_errors_loaded: int = 0
    resource_errors_loaded: int = 0
    enrichment_applied: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert stats to dictionary."""
        return {
            "indexes_processed": self.indexes_processed,
            "http_errors_loaded": self.http_errors_loaded,
            "resource_errors_loaded": self.resource_errors_loaded,
            "enrichment_applied": self.enrichment_applied,
        }


@dataclass
class DiagnosticStep:
    """Represents a diagnostic step for troubleshooting."""

    step: int
    action: str
    description: str
    command: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result: dict[str, Any] = {
            "step": self.step,
            "action": self.action,
            "description": self.description,
        }
        if self.command:
            result["command"] = self.command
        return result


@dataclass
class HttpError:
    """Represents an HTTP error with resolution guidance."""

    code: int
    name: str
    description: str
    common_causes: list[str] = field(default_factory=list)
    diagnostic_steps: list[DiagnosticStep] = field(default_factory=list)
    prevention: list[str] = field(default_factory=list)
    related_errors: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "code": self.code,
            "name": self.name,
            "description": self.description,
            "common_causes": self.common_causes,
            "diagnostic_steps": [s.to_dict() for s in self.diagnostic_steps],
            "prevention": self.prevention,
            "related_errors": self.related_errors,
        }


@dataclass
class ResourceErrorPattern:
    """Represents an error pattern for a specific resource type."""

    error_code: int
    pattern: str
    resolution: str

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "error_code": self.error_code,
            "pattern": self.pattern,
            "resolution": self.resolution,
        }


class ErrorResolutionEnricher:
    """Enrich index.json with HTTP error resolution guidance.

    Loads error resolution data from config/error_resolution.yaml and adds it
    to the x-f5xc-error-resolution extension in index.json.

    Error resolution includes:
    - HTTP status code explanations
    - Diagnostic steps for troubleshooting
    - Prevention tips
    - Resource-specific error patterns

    Attributes:
        config_path: Path to error_resolution.yaml
        http_errors: Dictionary of status code -> HttpError
        resource_errors: Dictionary of resource -> list of patterns
        stats: Enrichment statistics
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize enricher with error resolution configuration.

        Args:
            config_path: Path to error_resolution.yaml config.
                        Defaults to config/error_resolution.yaml.
        """
        if config_path is None:
            config_path = Path(__file__).parent.parent.parent / "config" / "error_resolution.yaml"

        self.config_path = config_path
        self.http_errors: dict[int, HttpError] = {}
        self.resource_errors: dict[str, list[ResourceErrorPattern]] = {}
        self.stats = ErrorResolutionEnrichmentStats()
        self._config_version: str = "0.0.0"

        self._load_config()

    def _load_config(self) -> None:
        """Load error resolution from YAML configuration file."""
        if not self.config_path.exists():
            # No config file - will skip enrichment gracefully
            return

        try:
            with self.config_path.open() as f:
                config = yaml.safe_load(f) or {}

            self._config_version = config.get("version", "0.0.0")

            # Load HTTP errors
            http_errors_config = config.get("http_errors", {})
            for code_str, error_config in http_errors_config.items():
                if isinstance(error_config, dict):
                    code = int(code_str)
                    self.http_errors[code] = self._parse_http_error(code, error_config)
                    self.stats.http_errors_loaded += 1

            # Load resource-specific errors
            resource_errors_config = config.get("resource_errors", {})
            for resource_name, resource_config in resource_errors_config.items():
                if isinstance(resource_config, dict):
                    patterns = resource_config.get("common_patterns", [])
                    parsed_patterns: list[ResourceErrorPattern] = [
                        ResourceErrorPattern(
                            error_code=pattern.get("error_code", 0),
                            pattern=pattern.get("pattern", ""),
                            resolution=pattern.get("resolution", ""),
                        )
                        for pattern in patterns
                        if isinstance(pattern, dict)
                    ]
                    if parsed_patterns:
                        self.resource_errors[resource_name] = parsed_patterns
                        self.stats.resource_errors_loaded += len(parsed_patterns)

        except yaml.YAMLError:
            # Invalid YAML - skip enrichment gracefully
            pass

    def _parse_http_error(self, code: int, config: dict[str, Any]) -> HttpError:
        """Parse an HTTP error from configuration dictionary.

        Args:
            code: HTTP status code
            config: Dictionary with error configuration

        Returns:
            HttpError dataclass
        """
        diagnostic_steps: list[DiagnosticStep] = [
            DiagnosticStep(
                step=step.get("step", 0),
                action=step.get("action", ""),
                description=step.get("description", ""),
                command=step.get("command"),
            )
            for step in config.get("diagnostic_steps", [])
            if isinstance(step, dict)
        ]

        return HttpError(
            code=code,
            name=config.get("name", ""),
            description=config.get("description", ""),
            common_causes=config.get("common_causes", []),
            diagnostic_steps=sorted(diagnostic_steps, key=lambda s: s.step),
            prevention=config.get("prevention", []),
            related_errors=config.get("related_errors", []),
        )

    def enrich_index(self, index: dict[str, Any]) -> dict[str, Any]:
        """Enrich index.json with error resolution data.

        Adds x-f5xc-error-resolution extension to index.json containing
        HTTP error diagnostics and resource-specific error patterns.

        Args:
            index: index.json dictionary

        Returns:
            Index with enriched error resolution data
        """
        self.stats.indexes_processed += 1

        if not self.http_errors and not self.resource_errors:
            return index

        # Build error resolution extension
        error_resolution: dict[str, Any] = {
            "version": self._config_version,
            "http_errors": {},
            "resource_errors": {},
        }

        # Add HTTP errors
        for code, error in sorted(self.http_errors.items()):
            error_resolution["http_errors"][str(code)] = error.to_dict()

        # Add resource errors
        for resource, patterns in sorted(self.resource_errors.items()):
            error_resolution["resource_errors"][resource] = [p.to_dict() for p in patterns]

        # Apply to index
        index[X_F5XC_ERROR_RESOLUTION] = error_resolution
        self.stats.enrichment_applied = True

        return index

    def get_http_error(self, code: int) -> HttpError | None:
        """Get HTTP error by status code.

        Args:
            code: HTTP status code

        Returns:
            HttpError if found, None otherwise
        """
        return self.http_errors.get(code)

    def get_resource_errors(self, resource: str) -> list[ResourceErrorPattern]:
        """Get error patterns for a resource type.

        Args:
            resource: Resource type name (e.g., "http_loadbalancer")

        Returns:
            List of error patterns for the resource
        """
        return self.resource_errors.get(resource, [])

    def get_diagnostic_steps(self, code: int) -> list[DiagnosticStep]:
        """Get diagnostic steps for an HTTP error code.

        Args:
            code: HTTP status code

        Returns:
            List of diagnostic steps
        """
        error = self.get_http_error(code)
        if error:
            return error.diagnostic_steps
        return []

    def get_prevention_tips(self, code: int) -> list[str]:
        """Get prevention tips for an HTTP error code.

        Args:
            code: HTTP status code

        Returns:
            List of prevention tips
        """
        error = self.get_http_error(code)
        if error:
            return error.prevention
        return []

    def get_all_error_codes(self) -> list[int]:
        """Get all configured HTTP error codes.

        Returns:
            Sorted list of HTTP status codes
        """
        return sorted(self.http_errors.keys())

    def get_configured_resources(self) -> list[str]:
        """Get list of resources with configured error patterns.

        Returns:
            Sorted list of resource names
        """
        return sorted(self.resource_errors.keys())

    def get_config_version(self) -> str:
        """Get version of loaded error resolution configuration.

        Returns:
            Version string from config (e.g., "1.0.0")
        """
        return self._config_version

    def get_stats(self) -> dict[str, Any]:
        """Get enrichment statistics.

        Returns:
            Dictionary with enrichment metrics
        """
        return self.stats.to_dict()

    def enrich_spec(
        self,
        spec: dict[str, Any],
        domain: str | None = None,  # noqa: ARG002 - API consistency with other enrichers
    ) -> dict[str, Any]:
        """Enrich OpenAPI specification with error resolution.

        For API consistency with other enrichers. Error resolution is
        applied at the index level, so this is a pass-through.

        Args:
            spec: OpenAPI specification dictionary
            domain: Domain name (unused, for API consistency)

        Returns:
            Specification unchanged
        """
        return spec

    def has_errors(self, code: int) -> bool:
        """Check if error resolution exists for a status code.

        Args:
            code: HTTP status code to check

        Returns:
            True if resolution is configured for the code
        """
        return code in self.http_errors

    def get_configured_domains(self) -> list[str]:
        """Get list of resources with configured error patterns.

        For API consistency with other enrichers. Returns resource names
        instead of domains since error resolution is resource-based.

        Returns:
            Sorted list of resource names
        """
        return sorted(self.resource_errors.keys())


__all__ = [
    "DiagnosticStep",
    "ErrorResolutionEnricher",
    "ErrorResolutionEnrichmentStats",
    "HttpError",
    "ResourceErrorPattern",
]
