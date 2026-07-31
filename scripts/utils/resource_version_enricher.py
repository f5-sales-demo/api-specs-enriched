# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Declare ``resource_version``, the API's optimistic-concurrency token.

The F5 XC API implements optimistic concurrency. The specs do not describe it, so no
generated client can use it, and every update is an unconditional overwrite: a client
that read an object, took a minute to decide, and wrote it back silently discards
whatever changed in between.

Measured against the live tenant on a disposable site:

===========================  =====================================================
``resource_version`` sent    result
===========================  =====================================================
omitted                      ``200``, unconditional replace — what callers do today
current                      ``200``, the version increments
stale                        ``409 RESOURCE_VERSION_MISMATCH``, write rejected
===========================  =====================================================

Wire position is not guesswork. The field is returned at the **top level of the GET
response** — present in 12 of 12 captured responses in ``terraform-provider-xcsh``'s
``tools/api-defaults.json``, absent from none — and read from the top level of the
replace request body, which is how the stale-value probe produced its ``409``.

It belongs to **neither** metadata schema. ``schemaObjectGetMetaType`` carries
``annotations``/``description``/``disable``/``labels``/``name``/``namespace``;
``schemaSystemObjectGetMetaType`` carries ``creation_timestamp``/``uid``/``tenant`` and
friends. ``resource_version`` is a sibling of ``metadata`` and ``spec``. Worth stating,
because a metadata field is the intuitive guess and it would be wrong.

Scope is deliberately the two shapes whose behaviour was measured:

* ``*GetResponse``  — so a client can read the token
* ``*ReplaceRequest`` — so a client can send it back

``*CreateRequest`` has no prior version to guard, and ``*ReplaceResponse`` was never
probed; declaring the field there would document behaviour nobody verified.

The field is **never required**. Omitting it has to keep working, because that is what
every existing caller does.

Issues: api-specs-enriched#1159 (declare it), terraform-provider-xcsh#1399 (use it).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Only these two shapes are touched. See the module docstring.
TARGET_SUFFIXES = ("GetResponse", "ReplaceRequest")

PROPERTY_NAME = "resource_version"

_DESCRIPTION = (
    "Opaque token identifying this version of the object, used for optimistic "
    "concurrency. Returned on every read. Send it back on a replace to make the write "
    "conditional: if the object changed in the meantime the API rejects the request "
    "with 409 RESOURCE_VERSION_MISMATCH instead of overwriting those changes. Omit it "
    "to replace unconditionally."
)


def _property_schema() -> dict[str, Any]:
    """Build the declaration. A fresh dict per call — never share mutable state."""
    return {
        "type": "string",
        "title": PROPERTY_NAME,
        "description": _DESCRIPTION,
        "x-displayname": "Resource Version",
        "x-f5xc-description-short": "Optimistic-concurrency token for conditional replace.",
        "x-f5xc-example": "12345678",
    }


@dataclass
class ResourceVersionEnrichmentStats:
    """Statistics for resource_version enrichment."""

    schemas_processed: int = 0
    schemas_stamped: int = 0
    schemas_already_declared: int = 0
    error_count: int = 0
    errors: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert stats to a serializable dictionary."""
        return {
            "schemas_processed": self.schemas_processed,
            "schemas_stamped": self.schemas_stamped,
            "schemas_already_declared": self.schemas_already_declared,
            "error_count": self.error_count,
            "errors": self.errors or [],
        }


class ResourceVersionEnricher:
    """Add a ``resource_version`` declaration to read and replace shapes."""

    def __init__(self) -> None:
        """Initialise with empty statistics."""
        self.stats = ResourceVersionEnrichmentStats(errors=[])

    def enrich_spec(self, spec: dict[str, Any]) -> dict[str, Any]:
        """Stamp ``resource_version`` onto every targeted schema in ``spec``.

        Args:
            spec: OpenAPI 3 (``components.schemas``) or Swagger 2 (``definitions``) spec.

        Returns:
            The same spec object, mutated in place and returned for chaining.
        """
        schemas = (spec.get("components") or {}).get("schemas")
        if not isinstance(schemas, dict):
            schemas = spec.get("definitions")
        if not isinstance(schemas, dict):
            return spec

        for name, schema in schemas.items():
            if not name.endswith(TARGET_SUFFIXES):
                continue
            self.stats.schemas_processed += 1

            # A malformed entry must not abort the rest of the spec.
            if not isinstance(schema, dict):
                self.stats.error_count += 1
                assert self.stats.errors is not None
                self.stats.errors.append(
                    f"{name}: expected a schema object, got {type(schema).__name__}"
                )
                continue

            properties = schema.setdefault("properties", {})
            if not isinstance(properties, dict):
                self.stats.error_count += 1
                assert self.stats.errors is not None
                self.stats.errors.append(
                    f"{name}: 'properties' is {type(properties).__name__}, not an object"
                )
                continue

            # If upstream ever ships this field, upstream wins. Overwriting it would
            # turn an additive enrichment into a contract change.
            if PROPERTY_NAME in properties:
                self.stats.schemas_already_declared += 1
                continue

            properties[PROPERTY_NAME] = _property_schema()
            self.stats.schemas_stamped += 1

        return spec

    def get_stats(self) -> dict[str, Any]:
        """Return enrichment statistics as a serializable dictionary."""
        return self.stats.to_dict()
