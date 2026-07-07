# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Namespace Profiles Exporter.

Exports the authoritative resource->namespace-profile map to a standalone JSON
artifact (``namespace_profiles.json``) for downstream consumers (vscode-xcsh, xcsh).

This artifact is the single source of truth the extension consumes to decide which
resource types may exist in which namespaces (built-in vs custom). It is built from
``config/namespace_profile.yaml`` via :class:`NamespaceProfileEnricher`, reusing the
exact same merge logic that embeds ``x-f5xc-namespace-profile`` into the specs, so the
flat map and the per-spec extensions can never drift apart.

Usage:
    python -m scripts.utils.namespace_profiles_exporter
    python -m scripts.utils.namespace_profiles_exporter --output path/to/output.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.utils.json_writer import write_json_file
from scripts.utils.namespace_profile_enricher import NamespaceProfileEnricher


class NamespaceProfilesExporter:
    """Export the resource->namespace-profile map to standalone JSON.

    Reads ``config/namespace_profile.yaml`` (via the enricher) and emits a flat,
    self-contained map: a ``default`` profile plus one fully merged profile per
    resource override. Downstream consumers resolve a resource as
    ``resources[key]`` falling back to ``default``.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize with the namespace profile configuration.

        Args:
            config_path: Path to ``namespace_profile.yaml``. Defaults to the
                enricher's own default (``config/namespace_profile.yaml``).
        """
        self.enricher = NamespaceProfileEnricher(config_path=config_path)

    def build(self, version: str | None = None) -> dict[str, Any]:
        """Build the namespace profiles artifact dictionary.

        Args:
            version: Pipeline version to stamp into the artifact.

        Returns:
            The artifact with ``default`` and per-resource merged profiles.
        """
        config = self.enricher.config
        resource_keys = config.get("resources", {})

        resources: dict[str, Any] = {}
        verified_count = 0
        assumed_count = 0
        unverified_count = 0

        for name in sorted(resource_keys):
            profile = self.enricher.get_profile_for_resource(name)
            verification = self.enricher.get_verification_status(name)

            entry: dict[str, Any] = {**profile}
            if verification:
                status = verification.get("status", "unverified")
                entry["_meta"] = {
                    "explicit": True,
                    "verification": status,
                    "method": verification.get("method", ""),
                }
                if status == "verified":
                    verified_count += 1
                elif status == "assumed":
                    assumed_count += 1
                else:
                    unverified_count += 1
            else:
                entry["_meta"] = {"explicit": True, "verification": "unverified", "method": ""}
                unverified_count += 1

            resources[name] = entry

        default_profile = config["default_profile"]
        default_profile.pop("_verification", None)

        return {
            "version": version or "0.0.0",
            "generated_at": datetime.now(tz=timezone.utc).isoformat(),
            "source": "api-specs-enriched/config/namespace_profile.yaml",
            "default": default_profile,
            "resources": resources,
            "_coverage": {
                "total_explicit": len(resources),
                "verified": verified_count,
                "assumed": assumed_count,
                "unverified": unverified_count,
            },
        }

    def export(self, output_path: Path, version: str | None = None) -> dict[str, Any]:
        """Build the artifact and write it to ``output_path``.

        Delegates to :func:`write_json_file` so formatting matches Biome
        expectations (same as the other downstream JSON artifacts).

        Args:
            output_path: Destination JSON path.
            version: Pipeline version to stamp into the artifact.

        Returns:
            The artifact dictionary that was written.
        """
        artifact = self.build(version=version)
        write_json_file(artifact, output_path, indent=2, ensure_ascii=False)
        return artifact


def main() -> int:
    """Main entry point for standalone namespace profiles export."""
    parser = argparse.ArgumentParser(
        description="Export the F5 XC resource->namespace-profile map to standalone JSON",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to namespace_profile.yaml (defaults to config/namespace_profile.yaml)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/specifications/api/namespace_profiles.json"),
        help="Output path for namespace_profiles.json",
    )
    parser.add_argument(
        "--version",
        type=str,
        default=None,
        help="Version string to stamp into the artifact",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print output to stdout without writing a file",
    )

    args = parser.parse_args()

    try:
        exporter = NamespaceProfilesExporter(config_path=args.config)

        if args.dry_run:
            artifact = exporter.build(version=args.version)
            print(json.dumps(artifact, indent=2, ensure_ascii=False))
        else:
            artifact = exporter.export(args.output, version=args.version)
            print(
                f"Namespace profiles exported to: {args.output} "
                f"({len(artifact['resources'])} resource overrides + default)",
            )
        return 0
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error exporting namespace profiles: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
