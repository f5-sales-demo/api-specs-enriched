"""Namespace Profile Enricher for F5 XC OpenAPI specifications.

Adds x-f5xc-namespace-profile extension with constraint, recommendation,
and classification metadata. Replaces the old x-f5xc-namespace-scope string.
"""

from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from scripts.utils.extension_constants import X_F5XC_NAMESPACE_PROFILE


@dataclass
class NamespaceProfileStats:
    """Tracks statistics during namespace profile enrichment."""

    specs_enriched: int = 0
    system_only: int = 0
    shared_only: int = 0
    tenant: int = 0
    shared_ref: int = 0
    overridden: int = 0
    explicit_count: int = 0
    default_fallback_count: int = 0
    verified_count: int = 0
    assumed_count: int = 0
    unverified_count: int = 0
    default_fallback_resources: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge override into base, returning a new dict."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class NamespaceProfileEnricher:
    """Enriches OpenAPI specs with x-f5xc-namespace-profile extension."""

    def __init__(self, config_path: Path | None = None) -> None:  # noqa: D107
        if config_path is None:
            config_path = Path("config/namespace_profile.yaml")
        self.config_path = config_path
        self.config: dict[str, Any] = {}
        self._load_config()
        self.stats = NamespaceProfileStats()

    def _load_config(self) -> None:
        with self.config_path.open() as f:
            self.config = yaml.safe_load(f)

    def get_profile_for_resource(
        self, resource_type: str, *, track_stats: bool = False
    ) -> dict[str, Any]:
        """Get the full namespace profile for a resource type, merged with defaults."""
        default = self.config["default_profile"]
        resources = self.config.get("resources", {})

        lookup = resource_type
        if lookup.startswith("views_") and lookup not in resources:
            lookup_without_views = lookup[len("views_") :]
            if lookup_without_views in resources:
                lookup = lookup_without_views

        override = resources.get(lookup, {})
        is_explicit = bool(override)

        if track_stats:
            if is_explicit:
                self.stats.explicit_count += 1
                verification = override.get("_verification", {})
                status = verification.get("status", "unverified")
                if status == "verified":
                    self.stats.verified_count += 1
                elif status == "assumed":
                    self.stats.assumed_count += 1
                else:
                    self.stats.unverified_count += 1
            else:
                self.stats.default_fallback_count += 1
                self.stats.default_fallback_resources.append(resource_type)

        profile = _deep_merge(default, override)
        profile.pop("_verification", None)
        return profile

    def is_resource_explicit(self, resource_type: str) -> bool:
        """Check whether a resource has an explicit entry in the config."""
        resources = self.config.get("resources", {})
        lookup = resource_type
        if lookup.startswith("views_") and lookup not in resources:
            lookup_without_views = lookup[len("views_") :]
            if lookup_without_views in resources:
                lookup = lookup_without_views
        return lookup in resources

    def get_verification_status(self, resource_type: str) -> dict[str, Any]:
        """Get the verification metadata for a resource."""
        resources = self.config.get("resources", {})
        lookup = resource_type
        if lookup.startswith("views_") and lookup not in resources:
            lookup_without_views = lookup[len("views_") :]
            if lookup_without_views in resources:
                lookup = lookup_without_views
        override = resources.get(lookup, {})
        return override.get("_verification", {})

    def enrich_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Add x-f5xc-namespace-profile to a spec. Removes old x-f5xc-namespace-scope.

        Writes profiles at two levels:
        1. spec.info['x-f5xc-namespace-profile'] - domain-level default profile
        2. components.schemas.<Name>['x-f5xc-namespace-profile'] - per-schema profiles
           for multi-resource domain specs (e.g., virtual.json with http_loadbalancers,
           app_firewalls, origin_pools)
        """
        try:
            if "info" not in spec:
                spec["info"] = {}

            info = spec["info"]

            if "x-f5xc-namespace-scope" in info:
                del info["x-f5xc-namespace-scope"]

            resource_type = self._detect_resource_type(spec)
            profile = self.get_profile_for_resource(resource_type, track_stats=True)

            info[X_F5XC_NAMESPACE_PROFILE] = profile

            # Write per-schema profiles for multi-resource domain specs
            self._enrich_schemas(spec, profile)

            self.stats.specs_enriched += 1
            self._update_category_stats(profile)

        except Exception as e:
            title = spec.get("info", {}).get("title", "unknown")
            self.stats.errors.append({"spec": title, "error": str(e)})

        return spec

    def _enrich_schemas(self, spec: dict[str, Any], default_profile: dict[str, Any]) -> None:
        """Write per-schema namespace profiles to components.schemas entries.

        For each schema that maps to a known resource type in config, write its
        own profile. Otherwise, inherit the domain-level default.
        """
        schemas = spec.get("components", {}).get("schemas", {})
        resources = self.config.get("resources", {})

        for schema_name, schema_obj in schemas.items():
            if not isinstance(schema_obj, dict):
                continue

            resource_type = self._match_schema_to_resource(schema_name, resources)

            if resource_type:
                schema_profile = self.get_profile_for_resource(resource_type)
            else:
                schema_profile = default_profile

            schema_obj[X_F5XC_NAMESPACE_PROFILE] = schema_profile

    def _match_schema_to_resource(self, schema_name: str, resources: dict[str, Any]) -> str | None:
        """Match a schema name to a config resource key.

        Tries exact match first, then longest-prefix match against known
        resource names to handle schema names like 'api_definitionAPInventoryReq'.
        """
        full_type = self._schema_name_to_resource_type(schema_name)
        if full_type in resources:
            return full_type

        snake = self._schema_name_to_resource_type(schema_name)
        best_match = ""
        for resource_name in resources:
            if snake.startswith(resource_name) and len(resource_name) > len(best_match):
                best_match = resource_name
        return best_match or None

    @staticmethod
    def _schema_name_to_resource_type(schema_name: str) -> str:
        """Convert a schema name to a resource type for config lookup.

        Handles patterns like 'viewshttp_loadbalancerObject' or
        'http_loadbalancerCreateSpecType' by extracting the core resource name.
        """
        # Strip common suffixes
        name = schema_name
        for suffix in (
            "Object",
            "CreateSpecType",
            "ReplaceSpecType",
            "GetSpecType",
            "StatusObject",
            "SpecType",
        ):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break

        # Strip common prefixes. Some spec schema keys are 'schema'-prefixed
        # (e.g. schemadns_load_balancerCreateSpecType); without stripping it the
        # name would never match its resource and would inherit the domain default.
        for prefix in ("views", "schema"):
            name = name.removeprefix(prefix)

        s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
        s2 = re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1)
        return s2.lower().strip("_")

    def _update_category_stats(self, profile: dict[str, Any]) -> None:
        allowed = profile["constraint"]["allowed"]
        pattern = profile["classification"]["multi_tenant_pattern"]

        if allowed == ["system"]:
            self.stats.system_only += 1
        elif allowed == ["shared"]:
            self.stats.shared_only += 1
        elif pattern == "shared-ref":
            self.stats.shared_ref += 1
        else:
            self.stats.tenant += 1

    def _detect_resource_type(self, spec: dict[str, Any]) -> str:
        """Extract resource type from spec using 3-level fallback."""
        info = spec.get("info", {})

        title = info.get("title", "")
        if title:
            result = self._extract_resource_from_title(title)
            if result and result in self.config.get("resources", {}):
                return result

        paths = spec.get("paths", {})
        if paths:
            result = self._extract_resource_from_paths(paths)
            if result:
                return result

        cli_domain = info.get("x-f5xc-cli-domain", "")
        if cli_domain:
            return cli_domain

        if title:
            return self._extract_resource_from_title(title)

        return "unknown"

    def _extract_resource_from_title(self, title: str) -> str:
        cleaned = re.sub(r"\s+API$", "", title, flags=re.IGNORECASE).strip()
        words = re.findall(r"[A-Z]{2,}(?=[A-Z][a-z]|\b)|[A-Z][a-z]+|[a-z]+|[A-Z]", cleaned)
        if not words:
            return cleaned.lower()
        return "_".join(w.lower() for w in words)

    def _extract_resource_from_paths(self, paths: dict[str, Any]) -> str:
        for path in paths:
            match = re.search(r"/namespaces/(?:\{namespace\}|system|shared)/([^/]+)/?$", path)
            if match:
                return re.sub(r"s$", "", match.group(1))
        return ""

    def get_stats(self) -> dict[str, Any]:
        """Return enrichment statistics as a dictionary."""
        return asdict(self.stats)

    def reset_stats(self) -> None:
        """Reset all enrichment statistics to zero."""
        self.stats = NamespaceProfileStats()
