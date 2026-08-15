# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Build the versioned resource coverage contract for downstream generators."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from scripts.utils.json_writer import write_json_file

if TYPE_CHECKING:
    from collections.abc import Iterable

CONTRACT_VERSION = 1
ALLOWED_EXCLUSION_REASONS = frozenset({"no_canonical_create"})
_CREATE_OPERATION = re.compile(r"^ves\.io\.schema\.(?P<identity>[a-zA-Z0-9_.]+)\.API\.Create$")


class ResourceCoverageError(ValueError):
    """Raised when resource coverage cannot be proven complete."""


@dataclass(frozen=True)
class CanonicalResource:
    """A canonical create identity and its collection route."""

    resource_key: str
    path: str
    operation_id: str


def _is_collection_path(path: str, resource_key: str) -> bool:
    """Return whether *path* has canonical namespace collection semantics."""
    segments = path.strip("/").split("/")
    if len(segments) < 3 or segments[0] != "api":
        return False

    namespace_indexes = [index for index, value in enumerate(segments) if value == "namespaces"]
    if not namespace_indexes:
        return False

    index = namespace_indexes[-1]
    # Namespace itself is the one tenant-level resource whose collection route
    # ends at /namespaces rather than below a selected namespace.
    if index == len(segments) - 1:
        return resource_key == "namespace"

    # All other canonical routes select one namespace and then one collection.
    if len(segments) != index + 3:
        return False
    namespace = segments[index + 1]
    collection = segments[index + 2]
    return bool(namespace and collection and not collection.startswith("{"))


def discover_canonical_resources(
    documents: Iterable[dict[str, Any]],
) -> dict[str, CanonicalResource]:
    """Discover canonical resources from exact create identity plus route semantics.

    The last schema-identity segment is the canonical resource key. This handles
    both ``views.<resource>`` and other qualified identities without guessing a
    singular name from the route.
    """
    candidates: dict[str, CanonicalResource] = {}
    for document in documents:
        paths = document.get("paths", {})
        if not isinstance(paths, dict):
            continue
        for api_path in sorted(paths):
            path_item = paths[api_path]
            if not isinstance(path_item, dict):
                continue
            post = path_item.get("post")
            if not isinstance(post, dict):
                continue
            operation_id = post.get("operationId")
            if not isinstance(operation_id, str):
                continue
            match = _CREATE_OPERATION.fullmatch(operation_id)
            if not match:
                continue
            resource_key = match.group("identity").split(".")[-1]
            if not _is_collection_path(api_path, resource_key):
                continue

            candidate = CanonicalResource(resource_key, api_path, operation_id)
            existing = candidates.get(resource_key)
            if existing and existing != candidate:
                raise ResourceCoverageError(
                    f"canonical resource {resource_key!r} has conflicting create routes: "
                    f"{existing.path!r} and {api_path!r}"
                )
            candidates[resource_key] = candidate

    return dict(sorted(candidates.items()))


class ResourceCoverageExporter:
    """Validate and export generated, manual, and excluded resource coverage."""

    def __init__(self, config_path: Path | None = None) -> None:
        """Load and validate the curated manual and exclusion policy."""
        self.config_path = config_path or Path("config/resource_coverage.yaml")
        try:
            raw = yaml.safe_load(self.config_path.read_text()) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ResourceCoverageError(
                f"failed to load resource coverage config {self.config_path}: {exc}"
            ) from exc
        if not isinstance(raw, dict):
            raise ResourceCoverageError("resource coverage config must be an object")
        if raw.get("version") != CONTRACT_VERSION:
            raise ResourceCoverageError(
                f"unsupported contract config version {raw.get('version')!r}; "
                f"expected {CONTRACT_VERSION}"
            )

        manual = raw.get("manual", {})
        if not isinstance(manual, dict):
            raise ResourceCoverageError("manual must be an object")
        parsed_manual: dict[str, str] = {}
        for resource_key, entry in manual.items():
            if not isinstance(resource_key, str) or not isinstance(entry, dict):
                raise ResourceCoverageError("each manual resource must be an object")
            manual_path = entry.get("path")
            if not isinstance(manual_path, str) or not manual_path.startswith("/api/"):
                raise ResourceCoverageError(
                    f"manual resource {resource_key!r} must declare an absolute API path"
                )
            if not _is_collection_path(manual_path, resource_key):
                raise ResourceCoverageError(
                    f"manual path {manual_path!r} for {resource_key!r} is not a collection route"
                )
            parsed_manual[resource_key] = manual_path
        self.manual = dict(sorted(parsed_manual.items()))

        exclusions = raw.get("exclusions", {})
        if not isinstance(exclusions, dict):
            raise ResourceCoverageError("exclusions must be an object")
        for resource_key, reason in exclusions.items():
            if reason not in ALLOWED_EXCLUSION_REASONS:
                raise ResourceCoverageError(
                    f"invalid exclusion reason {reason!r} for {resource_key!r}"
                )
        self.exclusions: dict[str, str] = dict(sorted(exclusions.items()))

    @property
    def manual_resource_keys(self) -> set[str]:
        """Return explicitly supported downstream manual resource keys."""
        return set(self.manual)

    def build(
        self,
        documents: Iterable[dict[str, Any]],
        namespace_profiles: dict[str, Any],
        *,
        version: str,
    ) -> dict[str, Any]:
        """Build and validate a complete deterministic coverage artifact."""
        documents = list(documents)
        candidates = discover_canonical_resources(documents)
        profile_version = namespace_profiles.get("version")
        if profile_version is not None and profile_version != version:
            raise ResourceCoverageError(
                f"namespace profile version {profile_version!r} does not match {version!r}"
            )
        profiles = namespace_profiles.get("resources")
        if not isinstance(profiles, dict):
            raise ResourceCoverageError("namespace_profiles resources must be an object")
        profile_keys = set(profiles)

        missing_profiles = sorted(set(candidates) - profile_keys)
        if missing_profiles:
            raise ResourceCoverageError(
                "canonical candidates lack explicit namespace profiles: "
                + ", ".join(missing_profiles)
            )
        missing_manual_profiles = sorted(set(self.manual) - profile_keys)
        if missing_manual_profiles:
            raise ResourceCoverageError(
                "manual resources lack explicit namespace profiles: "
                + ", ".join(missing_manual_profiles)
            )

        overlap = sorted(set(candidates) & set(self.manual))
        if overlap:
            raise ResourceCoverageError(
                "canonical resources must be generated, not manual: " + ", ".join(overlap)
            )

        path_items: dict[str, dict[str, Any]] = {}
        for document in documents:
            paths = document.get("paths", {})
            if isinstance(paths, dict):
                path_items.update(
                    (key, value) for key, value in paths.items() if isinstance(value, dict)
                )
        for resource_key, manual_path in self.manual.items():
            path_item = path_items.get(manual_path)
            if path_item is None:
                raise ResourceCoverageError(
                    f"manual path {manual_path!r} for {resource_key!r} does not exist"
                )
            if not isinstance(path_item.get("get"), dict):
                raise ResourceCoverageError(
                    f"manual path {manual_path!r} for {resource_key!r} has no GET list operation"
                )

        unknown_exclusions = sorted(set(self.exclusions) - profile_keys)
        if unknown_exclusions:
            raise ResourceCoverageError(
                "configured exclusions lack namespace profiles: " + ", ".join(unknown_exclusions)
            )
        invalid_exclusions = sorted(set(self.exclusions) & set(candidates))
        if invalid_exclusions:
            raise ResourceCoverageError(
                "canonical resources cannot be excluded: " + ", ".join(invalid_exclusions)
            )

        resources: dict[str, dict[str, Any]] = {}
        counts = {"excluded": 0, "generated": 0, "manual": 0}
        for resource_key in sorted(profile_keys):
            candidate = candidates.get(resource_key)
            if candidate:
                resources[resource_key] = {
                    "disposition": "generated",
                    "path": candidate.path,
                    "operation_id": candidate.operation_id,
                }
                counts["generated"] += 1
            elif resource_key in self.manual:
                resources[resource_key] = {
                    "disposition": "manual",
                    "path": self.manual[resource_key],
                }
                counts["manual"] += 1
            else:
                reason = self.exclusions.get(resource_key, "no_canonical_create")
                if reason not in ALLOWED_EXCLUSION_REASONS:
                    raise ResourceCoverageError(
                        f"invalid exclusion reason {reason!r} for {resource_key!r}"
                    )
                resources[resource_key] = {"disposition": "excluded", "reason": reason}
                counts["excluded"] += 1

        return {
            "version": version,
            "contract_version": CONTRACT_VERSION,
            "source": "api-specs-enriched/config/resource_coverage.yaml",
            "resources": resources,
            "coverage": {**counts, "total": len(resources)},
        }

    def export(
        self,
        documents: Iterable[dict[str, Any]],
        namespace_profiles: dict[str, Any],
        output_path: Path,
        *,
        version: str,
    ) -> dict[str, Any]:
        """Build, validate, and write the resource coverage artifact."""
        artifact = self.build(documents, namespace_profiles, version=version)
        write_json_file(artifact, output_path, indent=2, ensure_ascii=False)
        return artifact


def _load_documents(input_dir: Path) -> list[dict[str, Any]]:
    metadata = {
        "index.json",
        "minimal-export-defaults.json",
        "namespace_profiles.json",
        "resource_coverage.json",
        "validation.json",
    }
    return [
        json.loads(path.read_text())
        for path in sorted(input_dir.glob("*.json"))
        if path.name not in metadata
    ]


def main() -> int:
    """Export resource coverage from an existing generated-spec directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path("docs/specifications/api"))
    parser.add_argument("--profiles", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    profiles_path = args.profiles or args.input_dir / "namespace_profiles.json"
    output_path = args.output or args.input_dir / "resource_coverage.json"
    try:
        exporter = ResourceCoverageExporter(config_path=args.config)
        artifact = exporter.export(
            _load_documents(args.input_dir),
            json.loads(profiles_path.read_text()),
            output_path,
            version=args.version,
        )
    except (OSError, json.JSONDecodeError, ResourceCoverageError) as exc:
        print(f"Error exporting resource coverage: {exc}", file=sys.stderr)
        return 1
    print(
        f"Resource coverage exported to {output_path}: "
        f"{artifact['coverage']['generated']} generated, "
        f"{artifact['coverage']['manual']} manual, "
        f"{artifact['coverage']['excluded']} excluded"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
