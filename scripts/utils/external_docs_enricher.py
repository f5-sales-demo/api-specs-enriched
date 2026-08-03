# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""External documentation enricher for OpenAPI specifications.

This enricher adds externalDocs metadata to OpenAPI specs,
providing direct links to F5's official documentation.

Adds the standard OpenAPI root-level externalDocs field with:
- url: Link to relevant F5 XC documentation
- description: Brief description of the documentation

Configuration is loaded from config/external_docs.yaml.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode
from yaml.resolver import BaseResolver

from scripts.utils.domain_categorizer import categorize_spec
from scripts.utils.extension_constants import X_F5XC_API_REFERENCE_URL, X_F5XC_CLI_DOMAIN

logger = logging.getLogger(__name__)

_EXPLICIT_OTHER_DOCUMENTS = frozenset({"openapi.json", "other.json"})


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as error:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass
class ExternalDocsStats:
    """Statistics for external docs enrichment."""

    specs_enriched: int = 0
    docs_added: int = 0
    already_had_docs: int = 0
    api_links_rewritten: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert stats to dictionary."""
        return {
            "specs_enriched": self.specs_enriched,
            "docs_added": self.docs_added,
            "already_had_docs": self.already_had_docs,
            "api_links_rewritten": self.api_links_rewritten,
            "error_count": len(self.errors),
            "errors": self.errors,
        }


class ExternalDocsEnricher:
    """Enrich OpenAPI specs with external documentation links.

    Adds the standard OpenAPI externalDocs field at the document root,
    providing direct links to F5's official documentation based on
    the spec's domain categorization.

    Uses config/external_docs.yaml for URL mappings.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize enricher with configuration.

        Args:
            config_path: Optional path to config file.
                        Defaults to config/external_docs.yaml
        """
        self.config_path = (
            config_path or Path(__file__).parent.parent.parent / "config" / "external_docs.yaml"
        )
        self.config: dict[str, Any] = {}
        self.domain_docs: dict[str, dict[str, str]] = {}
        self.api_reference_base_url: str = ""
        self.api_reference_old_prefix: str = ""
        self.stats = ExternalDocsStats()

        self._load_config()

    def _load_config(self) -> None:
        """Load and strictly validate the required YAML configuration."""
        if not self.config_path.is_file():
            raise FileNotFoundError(f"external docs configuration not found: {self.config_path}")
        with self.config_path.open(encoding="utf-8") as config_file:
            loader = _UniqueKeySafeLoader(config_file)
            try:
                config = loader.get_single_data()
            finally:
                loader.dispose()
        self.config = self._validate_config(config)

        for domain, doc_info in self.config["domains"].items():
            self.domain_docs[domain] = {
                "url": doc_info["url"],
                "description": doc_info["description"],
            }

        self.api_reference_base_url = self.config["api_reference_base_url"]
        self.api_reference_old_prefix = self.config["api_reference_rewrite"]["old_prefix"]

        logger.info("Loaded external_docs config from %s", self.config_path)
        logger.info("Found %d domain documentation mappings", len(self.domain_docs))
        if self.api_reference_base_url and self.api_reference_old_prefix:
            logger.info(
                "API reference rewrite enabled: %s -> %s/{domain}/",
                self.api_reference_old_prefix,
                self.api_reference_base_url,
            )

    @staticmethod
    def _validate_config(config: Any) -> dict[str, Any]:
        """Reject incomplete or ignored external-documentation configuration."""
        required = {
            "api_reference_base_url",
            "api_reference_rewrite",
            "domains",
        }
        if not isinstance(config, dict) or set(config) != required:
            raise ValueError(f"external docs configuration must contain exactly {sorted(required)}")
        base_url = config["api_reference_base_url"]
        ExternalDocsEnricher._validate_https_url(base_url, "api_reference_base_url")
        rewrite = config["api_reference_rewrite"]
        if not isinstance(rewrite, dict) or set(rewrite) != {"old_prefix"}:
            raise ValueError("external docs api_reference_rewrite must contain only old_prefix")
        ExternalDocsEnricher._validate_https_url(
            rewrite["old_prefix"],
            "api_reference_rewrite.old_prefix",
        )
        if not isinstance(config["domains"], dict) or not config["domains"]:
            raise ValueError("external docs domains must be a non-empty object")
        for domain, entry in config["domains"].items():
            if not isinstance(domain, str) or not domain:
                raise ValueError("external docs domain names must be non-empty strings")
            ExternalDocsEnricher._validate_doc_entry(entry, f"domains.{domain}")
        return config

    @staticmethod
    def _validate_doc_entry(entry: Any, location: str) -> None:
        if not isinstance(entry, dict) or set(entry) != {"url", "description"}:
            raise ValueError(f"external docs {location} must contain only url and description")
        if any(not isinstance(value, str) or not value.strip() for value in entry.values()):
            raise ValueError(f"external docs {location} values must be non-empty strings")
        ExternalDocsEnricher._validate_https_url(entry["url"], f"{location}.url")

    @staticmethod
    def _validate_https_url(value: Any, location: str) -> None:
        if not isinstance(value, str) or not value or any(char.isspace() for char in value):
            raise ValueError(f"external docs {location} must be a valid HTTPS URL")
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as error:
            raise ValueError(f"external docs {location} must be a valid HTTPS URL") from error
        has_https_host = parsed.scheme == "https" and bool(parsed.hostname)
        has_credentials = parsed.username is not None or parsed.password is not None
        has_valid_port = port is None or 1 <= port <= 65535
        if not has_https_host or has_credentials or not has_valid_port:
            raise ValueError(f"external docs {location} must be a valid HTTPS URL")

    def enrich_spec(
        self,
        spec: dict[str, Any],
        filename: str | None = None,
    ) -> dict[str, Any]:
        """Enrich OpenAPI specification with external documentation link.

        Adds externalDocs to the document root based on
        the domain detected from the spec title or filename.

        Args:
            spec: OpenAPI specification dictionary
            filename: Optional filename for domain detection

        Returns:
            Enriched specification
        """
        if not isinstance(spec, dict):
            raise TypeError("OpenAPI specification must be an object")
        info = spec.setdefault("info", {})
        if not isinstance(info, dict):
            raise TypeError("OpenAPI info must be an object")
        if "externalDocs" in info:
            raise ValueError("invalid info.externalDocs placement; externalDocs must be root-level")

        domain = self._detect_domain(spec, filename)
        if "externalDocs" in spec:
            self._validate_doc_entry(spec["externalDocs"], "document externalDocs")
            self.stats.already_had_docs += 1
            self.stats.specs_enriched += 1
        else:
            external_docs = self._get_docs_for_domain(domain)
            spec["externalDocs"] = external_docs
            self.stats.specs_enriched += 1
            self.stats.docs_added += 1
            logger.debug("Added externalDocs for domain '%s': %s", domain, external_docs["url"])

        api_ref_url = self._get_api_reference_url(domain)
        if api_ref_url:
            info[X_F5XC_API_REFERENCE_URL] = api_ref_url

        self._rewrite_operation_docs(spec, domain)

        return spec

    def _get_api_reference_url(self, domain: str) -> str | None:
        """Build the API reference URL for a domain.

        Args:
            domain: Domain slug (e.g. 'blindfold', 'virtual')

        Returns:
            Full API reference URL, or None if base URL is not configured.
        """
        if self.api_reference_base_url:
            return f"{self.api_reference_base_url}/{domain}/"
        return None

    def _rewrite_operation_docs(self, spec: dict[str, Any], domain: str) -> None:
        """Rewrite upstream API reference URLs in operation-level externalDocs.

        Walks all paths.{path}.{method}.externalDocs.url fields and replaces
        upstream docs.cloud.f5.com API reference links with our published site.

        Args:
            spec: OpenAPI specification dictionary (modified in place)
            domain: Detected domain slug for constructing the new URL
        """
        if not self.api_reference_base_url or not self.api_reference_old_prefix:
            return

        new_url = f"{self.api_reference_base_url}/{domain}/"
        paths = spec.get("paths", {})

        for path_ops in paths.values():
            if not isinstance(path_ops, dict):
                continue
            for op in path_ops.values():
                if not isinstance(op, dict):
                    continue
                ext_docs = op.get("externalDocs")
                if not isinstance(ext_docs, dict):
                    continue
                url = ext_docs.get("url", "")
                if url.startswith(self.api_reference_old_prefix):
                    ext_docs["url"] = new_url
                    self.stats.api_links_rewritten += 1

    def _detect_domain(self, spec: dict[str, Any], filename: str | None = None) -> str:
        """Detect domain from filename or spec metadata.

        Uses multiple strategies to determine the domain:
        1. Use filename with DomainCategorizer if available
        2. Extract from x-f5xc-cli-domain extension if present
        3. Extract from spec title

        Args:
            spec: OpenAPI specification
            filename: Optional filename for categorization

        Returns:
            Detected domain name

        Raises:
            ValueError: If the document is neither explicitly ``other`` nor
                categorizable to a mapped domain.
        """
        # Strategy 1: Use filename with DomainCategorizer
        if filename:
            if Path(filename).name in _EXPLICIT_OTHER_DOCUMENTS:
                return "other"
            domain = categorize_spec(filename)
            if domain and domain != "other":
                return domain

        # Strategy 2: Use x-f5xc-cli-domain if present
        info = spec.get("info", {})
        cli_domain = info.get(X_F5XC_CLI_DOMAIN)
        if cli_domain:
            return cli_domain

        # Strategy 3: Try title-based categorization
        title = info.get("title", "")
        if title:
            # Create a pseudo-filename from title for categorization
            pseudo_filename = title.lower().replace(" ", "_").replace("-", "_")
            domain = categorize_spec(pseudo_filename)
            if domain and domain != "other":
                return domain

        raise ValueError(
            "unable to detect an explicitly mapped external docs domain"
            + (f" for {filename!r}" if filename else "")
        )

    def _get_docs_for_domain(self, domain: str) -> dict[str, str]:
        """Get external docs configuration for a domain.

        Args:
            domain: Domain name

        Returns:
            Dictionary with url and description keys
        """
        if domain in self.domain_docs:
            return self.domain_docs[domain].copy()
        raise KeyError(f"no external docs mapping for domain {domain!r}")

    def get_docs_for_domain(self, domain: str) -> dict[str, str]:
        """Get external docs for a specific domain.

        Public method for external use (e.g., by other enrichers or tools).

        Args:
            domain: Domain name

        Returns:
            Dictionary with url and description keys
        """
        return self._get_docs_for_domain(domain)

    def get_stats(self) -> dict[str, Any]:
        """Get enrichment statistics.

        Returns:
            Statistics dictionary
        """
        return self.stats.to_dict()

    def reset_stats(self) -> None:
        """Reset enrichment statistics."""
        self.stats = ExternalDocsStats()
