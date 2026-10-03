"""Deterministic source-to-catalog enrichment coverage, failing on lost map rules."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from scripts.compile_catalog import _walk_schema, normalize_path_placeholders
from scripts.utils.map_constraints import (
    NATIVE_VALUE_KEYS,
    PREFIX,
    RULES,
    map_rules,
    normalize_map,
    schema_nodes,
)

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "patch", "head", "options", "trace"})


def sha256(path: Path) -> str:
    """Compute a receipt digest of exact input bytes."""
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(spec: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    """Inventory unique physical map sites and deduplicated operation exposures."""
    sites = []
    families: Counter[str] = Counter()
    for path, node in schema_nodes(spec):
        rules = map_rules(node)
        if not rules:
            continue
        families.update(rules.keys())
        normalized = normalize_map(node)
        assert normalized is not None
        native = {
            key: node[key]
            for key in ("minProperties", "maxProperties", "additionalProperties")
            if key in node
        }
        dispositions = []
        for rule, original in rules.items():
            scope, keyword = RULES[rule[len(PREFIX) :]]
            expected_value = normalized.get(scope, {}).get(keyword)
            if scope == "cardinality":
                equivalent = node.get(keyword) == expected_value
                classification = "exact-native" if equivalent else "missing-native"
                reason = (
                    "Native object pair bound"
                    if equivalent
                    else "Canonical native bound requires upstream correction"
                )
            elif scope == "values" and keyword in NATIVE_VALUE_KEYS:
                additional = node.get("additionalProperties")
                equivalent = (
                    isinstance(additional, dict) and additional.get(keyword) == expected_value
                )
                classification = "exact-native" if equivalent else "extension-only"
                reason = (
                    "Native string map-value bound"
                    if equivalent
                    else "Typed/ref/composed value requires contract-compatible native projection"
                )
            else:
                classification = "extension-only"
                reason = (
                    "OpenAPI 3.0 has no exact property-name/cross-entry or agreed format contract"
                )
            dispositions.append(
                {
                    "rule": rule,
                    "originalValue": original,
                    "scope": scope,
                    "classification": classification,
                    "reason": reason,
                    "owner": "f5-sales-demo/api-specs-enriched#1854",
                }
            )
        sites.append(
            {
                "path": path,
                "rules": rules,
                "normalized": node.get("x-f5xc-constraints"),
                "expected": normalized,
                "native": native,
                "dispositions": dispositions,
            }
        )
    # Object identity ties expanded catalog/request traversal to physical source sites.
    site_rules = {json.dumps(site["rules"], sort_keys=True) for site in sites}
    request_fields = set()
    key_value_fields = set()
    request_methods = set()
    all_methods = set()
    request_identities = set()
    all_identities = set()
    request_site_rules = set()
    route_identities = {
        (op["method"], normalize_path_placeholders(op["path"])): entry["apiIdentity"]
        for entry in catalog.get("apiOperations", [])
        for op in entry["operations"]
    }
    for path, item in sorted(spec.get("paths", {}).items()):
        for method, operation in sorted(item.items()):
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            identity = operation.get("operationId", "").split(".API.")[0]
            operation_key = (method.upper(), normalize_path_placeholders(path))
            identity = route_identities.get(operation_key, identity)
            schemas = []
            schemas = [
                (True, media.get("schema", {}))
                for media in operation.get("requestBody", {}).get("content", {}).values()
            ]
            for response in operation.get("responses", {}).values():
                schemas.extend(
                    (False, media.get("schema", {}))
                    for media in response.get("content", {}).values()
                )
            for request, schema in schemas:
                for field, node in _walk_schema(schema, spec.get("components")):
                    rules = map_rules(node)
                    if not rules:
                        continue
                    assert json.dumps(rules, sort_keys=True) in site_rules
                    all_methods.add(operation_key)
                    all_identities.add(identity)
                    if request:
                        request_fields.add((*operation_key, field))
                        request_methods.add(operation_key)
                        request_identities.add(identity)
                        request_site_rules.add(json.dumps(rules, sort_keys=True))
                        if any(".keys." in key or ".values." in key for key in rules):
                            key_value_fields.add((*operation_key, field))
    catalog_fields = {}
    retained = []
    for category in catalog.get("categories", []):
        for operation in category.get("operations", []):
            key = (operation["method"].upper(), normalize_path_placeholders(operation["path"]))
            for field, metadata in operation.get("fieldMetadata", {}).items():
                catalog_fields[(*key, field)] = metadata
            for field, node in schema_nodes(
                {"components": {"schemas": {"body": operation.get("bodySchema", {})}}}
            ):
                if map_rules(node):
                    retained.append({"method": key[0], "path": key[1], "field": field})
    missing = sorted(
        field
        for field in request_fields
        if catalog_fields.get(field, {}).get("constraints", {}).get("constraintType") != "map"
    )
    return {
        "counts": {
            "componentSchemas": len(spec.get("components", {}).get("schemas", {})),
            "directProperties": sum(
                len(v.get("properties", {}))
                for v in spec.get("components", {}).get("schemas", {}).values()
            ),
            "mapSites": len(sites),
            "mapRuleOccurrences": sum(families.values()),
            "mapRuleFamilies": len(families),
            "catalogCategories": len(catalog.get("categories", [])),
            "catalogApiIdentities": len(
                {op["apiIdentity"] for op in catalog.get("apiOperations", [])}
            ),
            "catalogMethodPaths": len(
                {
                    (op["method"], op["path"])
                    for cat in catalog.get("categories", [])
                    for op in cat["operations"]
                }
            ),
            "catalogFieldEntries": len(catalog_fields),
            "requestMapFields": len(request_fields),
            "requestMapMethods": len(request_methods),
            "requestApiIdentities": len(request_identities),
            "requestKeyValueFields": len(key_value_fields),
            "requestKeyValueMethods": len({field[:2] for field in key_value_fields}),
            "requestOrResponseMethods": len(all_methods),
            "requestOrResponseIdentities": len(all_identities),
        },
        "families": dict(sorted(families.items())),
        "sites": sites,
        "requestFields": [list(field) for field in sorted(request_fields)],
        "missingCatalogMapFields": [list(field) for field in missing],
        "retainedBodyExceptions": retained,
    }


def export_coverage(master_path: Path, catalog_path: Path, output_path: Path) -> dict[str, Any]:
    """Bind the coverage report to immutable inputs and reject unexplained loss."""
    master = json.loads(master_path.read_text())
    catalog = json.loads(catalog_path.read_text())
    report = inventory(master, catalog)
    failures = [site["path"] for site in report["sites"] if site["normalized"] != site["expected"]]
    if failures or report["missingCatalogMapFields"]:
        raise ValueError(
            f"Map enrichment coverage failed: sites={failures[:5]}, catalog={report['missingCatalogMapFields'][:5]}"
        )
    report["version"] = master["info"]["version"]
    report["dialect"] = master["openapi"]
    report["inputs"] = {
        "openapi.json": sha256(master_path),
        "api-catalog.json": sha256(catalog_path),
        "upstreamReceipt": json.loads(Path(".github_release").read_text()),
    }
    report["counting"] = (
        "Physical schema sites; identical aliases counted once; method/path/field exposures deduplicated; requests separate from responses"
    )
    from scripts.utils.enrichment_orchestrator import validate_stage_inventory  # noqa: PLC0415

    validate_stage_inventory()
    console_config = yaml.safe_load(Path("config/console_field_metadata.yaml").read_text())
    report["consoleTargets"] = {
        "configured": sum(len(fields) for fields in console_config["resources"].values()),
        "exclusions": console_config.get("exclusions", {}),
    }
    report["stages"] = yaml.safe_load(Path("config/enrichment_stages.yaml").read_text())
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    markdown = [
        "# Enrichment coverage",
        "",
        f"Version: {report['version']}; dialect: {report['dialect']}",
        "",
        "| Measurement | Count |",
        "| --- | ---: |",
    ]
    markdown.extend(f"| {key} | {value} |" for key, value in sorted(report["counts"].items()))
    output_path.with_suffix(".md").write_text("\n".join(markdown) + "\n")
    return report


if __name__ == "__main__":
    export_coverage(
        Path("docs/specifications/api/openapi.json"),
        Path("release/api-catalog.json"),
        Path("release/enrichment-coverage.json"),
    )
