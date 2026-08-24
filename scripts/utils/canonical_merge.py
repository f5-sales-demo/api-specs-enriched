"""Fail-closed, deterministic merging for F5 XC OpenAPI source graphs.

The upstream distribution is a collection of service-local OpenAPI documents.  Component
names are local to those documents and therefore cannot be union-merged safely.  This
module assigns a canonical global name to every schema occurrence, rewrites its local
references, and rejects every path or component collision that is not explicitly modeled.
"""

from __future__ import annotations

import copy
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
OPERATION_ALIASES_EXTENSION = "x-f5xc-operation-aliases"


class CanonicalMergeError(ValueError):
    """Raised when source contracts cannot be represented without losing information."""


@dataclass(frozen=True)
class OperationAlias:
    """An explicitly reviewed duplicate operation identity."""

    path: str
    method: str
    canonical_operation_id: str
    alternate_operation_id: str
    identity_replacements: tuple[tuple[str, str], ...]


SUGGEST_VALUES_ALIAS = OperationAlias(
    path="/api/discovery/namespaces/{namespace}/suggest-values",
    method="post",
    canonical_operation_id="ves.io.schema.discovery_cloud.CustomAPI.SuggestValues",
    alternate_operation_id="ves.io.schema.discovered_service.CustomAPI.SuggestValues",
    identity_replacements=(("discovered_service", "discovery_cloud"),),
)
OPERATION_ALIASES = (SUGGEST_VALUES_ALIAS,)


@dataclass(frozen=True)
class MergeAccounting:
    """Proof that each schema and operation occurrence survived canonicalization."""

    schema_occurrences: int
    schema_assignments: int
    canonical_schemas: int
    operation_occurrences: int
    canonical_operations: int
    operation_aliases: int
    exact_operation_duplicates: int


@dataclass(frozen=True)
class CanonicalMergeResult:
    """Canonical rewritten sources, merged graph, and occurrence accounting."""

    sources: dict[str, dict[str, Any]]
    merged: dict[str, Any]
    accounting: MergeAccounting
    schema_keys: dict[tuple[str, str], str]


def canonical_bytes(value: Any) -> bytes:
    """Return stable JSON bytes for equality and ordering."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def rewrite_sources_with_schema_keys(
    specs: dict[str, dict[str, Any]],
    schema_keys: dict[tuple[str, str], str],
) -> dict[str, dict[str, Any]]:
    """Rewrite source-local schema keys and refs using an established assignment.

    Documentation projections use this after canonicalization so their additional
    annotations cannot influence provider-facing key selection.
    """
    rewritten: dict[str, dict[str, Any]] = {}
    for source in sorted(specs):
        spec = specs[source]
        schemas = spec.get("components", {}).get("schemas", {})
        local_map: dict[str, str] = {}
        for local in schemas:
            assignment = schema_keys.get((source, local))
            if assignment is None:
                raise CanonicalMergeError(f"{source}: schema {local!r} has no canonical assignment")
            local_map[local] = assignment
        rewritten_spec = _rewrite_local_schema_refs(spec, local_map)
        rewritten_schemas = rewritten_spec.setdefault("components", {}).setdefault("schemas", {})
        rewritten_spec["components"]["schemas"] = {
            local_map[name]: schema for name, schema in rewritten_schemas.items()
        }
        rewritten[source] = rewritten_spec
    return rewritten


def source_service_slug(source: str) -> str:
    """Return the deterministic full service slug used in qualified schema names."""
    stem = source.removesuffix(".json")
    # Release filenames wrap the authoritative service identity in distribution
    # metadata. Keep the complete service name while excluding mutable packaging data.
    stem = re.sub(r"^docs-cloud-f5-com\.\d+\.public\.", "", stem)
    stem = re.sub(r"\.ves-swagger(?:_processed)?$", "", stem)
    # OpenAPI component keys flow into Go/provider schema-name validators, whose
    # portable identifier grammar permits underscores but not hyphens.
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", stem).strip("_").lower()
    if not slug:
        raise CanonicalMergeError(f"source name has no usable service identity: {source!r}")
    return slug


def _rewrite_local_schema_refs(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, dict):
        rewritten: dict[str, Any] = {}
        for key, child in value.items():
            if (
                key == "$ref"
                and isinstance(child, str)
                and child.startswith("#/components/schemas/")
            ):
                local_name = child.removeprefix("#/components/schemas/")
                if local_name not in mapping:
                    raise CanonicalMergeError(f"unresolved local schema reference: {child}")
                rewritten[key] = f"#/components/schemas/{mapping[local_name]}"
            else:
                rewritten[key] = _rewrite_local_schema_refs(child, mapping)
        return rewritten
    if isinstance(value, list):
        return [_rewrite_local_schema_refs(item, mapping) for item in value]
    return value


def _schema_assignments(
    sources: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], str]:
    occurrences: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    original_names: set[str] = set()
    for source, spec in sources.items():
        schemas = spec.get("components", {}).get("schemas", {})
        if not isinstance(schemas, dict):
            raise CanonicalMergeError(f"{source}: components.schemas must be an object")
        for name, schema in schemas.items():
            if not isinstance(schema, dict):
                raise CanonicalMergeError(f"{source}: schema {name} must be an object")
            original_names.add(name)
            occurrences[name].append((source, schema))

    assignments: dict[tuple[str, str], str] = {}
    for name, items in occurrences.items():
        groups: dict[bytes, list[str]] = defaultdict(list)
        for source, schema in items:
            groups[canonical_bytes(schema)].append(source)
        if len(groups) == 1:
            for source, _schema in items:
                assignments[(source, name)] = name
        else:
            for group_sources in groups.values():
                qualified = f"{name}__{source_service_slug(min(group_sources))}"
                if qualified in original_names:
                    raise CanonicalMergeError(
                        f"generated schema key collides with upstream key: {qualified}"
                    )
                for source in group_sources:
                    assignments[(source, name)] = qualified

    # Partition refinement: a byte-identical parent that points at service-specific
    # children is not globally identical after local references are rewritten.
    for _iteration in range(len(assignments) + 1):
        changed = False
        for name, items in occurrences.items():
            groups: dict[bytes, list[str]] = defaultdict(list)
            for source, schema in items:
                local_map = {
                    local: assigned
                    for (mapped_source, local), assigned in assignments.items()
                    if mapped_source == source
                }
                groups[canonical_bytes(_rewrite_local_schema_refs(schema, local_map))].append(
                    source
                )
            if len(groups) == 1 and all(assignments[(source, name)] == name for source, _ in items):
                continue
            if len(groups) == 1:
                # Once a raw conflict forced qualification, never silently coalesce it.
                continue
            for group_sources in groups.values():
                qualified = f"{name}__{source_service_slug(min(group_sources))}"
                if qualified in original_names:
                    raise CanonicalMergeError(
                        f"generated schema key collides with upstream key: {qualified}"
                    )
                for source in group_sources:
                    key = (source, name)
                    if assignments[key] != qualified:
                        assignments[key] = qualified
                        changed = True
        if not changed:
            return assignments
    raise CanonicalMergeError("schema reference partition did not converge")


def _resolve_for_comparison(
    value: Any,
    spec: dict[str, Any],
    replacements: tuple[tuple[str, str], ...],
    stack: tuple[str, ...] = (),
) -> Any:
    if isinstance(value, str):
        for old, new in replacements:
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [_resolve_for_comparison(item, spec, replacements, stack) for item in value]
    if not isinstance(value, dict):
        return value
    ref = value.get("$ref")
    if isinstance(ref, str) and ref.startswith("#/"):
        if ref in stack:
            return {"$recursiveRef": _resolve_for_comparison(ref, spec, replacements, stack)}
        target: Any = spec
        try:
            for segment in ref[2:].split("/"):
                target = target[segment.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            raise CanonicalMergeError(
                f"unresolved local reference while comparing aliases: {ref}"
            ) from None
        siblings = {key: child for key, child in value.items() if key != "$ref"}
        resolved = _resolve_for_comparison(target, spec, replacements, (*stack, ref))
        if siblings:
            return {
                "$resolved": resolved,
                "$siblings": _resolve_for_comparison(siblings, spec, replacements, stack),
            }
        return resolved
    return {
        key: _resolve_for_comparison(child, spec, replacements, stack)
        for key, child in value.items()
    }


def _alias_for(path: str, method: str, first_id: str, second_id: str) -> OperationAlias | None:
    identities = {first_id, second_id}
    for alias in OPERATION_ALIASES:
        if (
            alias.path == path
            and alias.method == method.lower()
            and identities == {alias.canonical_operation_id, alias.alternate_operation_id}
        ):
            return alias
    return None


def _operation_wire_contract(
    operation: dict[str, Any],
    spec: dict[str, Any],
    replacements: tuple[tuple[str, str], ...],
) -> Any:
    """Resolve only request, response, and parameter contracts for alias proof."""
    contract = {
        key: operation[key]
        for key in ("parameters", "requestBody", "responses")
        if key in operation
    }

    def strip_local_enrichment(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: strip_local_enrichment(child)
                for key, child in value.items()
                if not key.startswith("x-f5xc-")
            }
        if isinstance(value, list):
            return [strip_local_enrichment(child) for child in value]
        return value

    return strip_local_enrichment(_resolve_for_comparison(contract, spec, replacements))


def _merge_paths(
    rewritten: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], int, int, int]:
    paths: dict[str, Any] = {}
    owners: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
    operation_occurrences = 0
    aliases = 0
    exact_duplicates = 0
    for source, spec in rewritten.items():
        source_paths = spec.get("paths", {})
        if not isinstance(source_paths, dict):
            raise CanonicalMergeError(f"{source}: paths must be an object")
        for path, incoming_item in source_paths.items():
            if not isinstance(incoming_item, dict):
                raise CanonicalMergeError(f"{source}: path item {path} must be an object")
            target_item = paths.setdefault(path, {})
            for member, incoming in incoming_item.items():
                method = member.lower()
                if method not in HTTP_METHODS:
                    if member in target_item and canonical_bytes(
                        target_item[member]
                    ) != canonical_bytes(incoming):
                        # The reviewed suggest-values alias also carries service-owned
                        # path annotations. Preserve the canonical service's values;
                        # every other path-member conflict remains fatal.
                        alias = next(
                            (item for item in OPERATION_ALIASES if item.path == path), None
                        )
                        source_operation = incoming_item.get(alias.method) if alias else None
                        source_id = (
                            source_operation.get("operationId")
                            if isinstance(source_operation, dict)
                            else None
                        )
                        if alias is None or member not in {"x-displayname", "x-ves-proto-service"}:
                            raise CanonicalMergeError(f"conflicting path member {path} {member}")
                        if source_id == alias.canonical_operation_id:
                            target_item[member] = copy.deepcopy(incoming)
                        continue
                    target_item.setdefault(member, copy.deepcopy(incoming))
                    continue
                operation_occurrences += 1
                if member not in target_item:
                    target_item[member] = copy.deepcopy(incoming)
                    owners[(path, method)] = (source, spec)
                    continue
                existing = target_item[member]
                if not isinstance(existing, dict) or not isinstance(incoming, dict):
                    raise CanonicalMergeError(
                        f"malformed duplicate operation {method.upper()} {path}"
                    )
                if canonical_bytes(existing) == canonical_bytes(incoming):
                    exact_duplicates += 1
                    continue
                first_id = existing.get("operationId", "")
                second_id = incoming.get("operationId", "")
                alias = _alias_for(path, method, first_id, second_id)
                if alias is None:
                    raise CanonicalMergeError(
                        f"unclassified duplicate operation {method.upper()} {path}: "
                        f"{first_id!r} versus {second_id!r}"
                    )
                previous_source, previous_spec = owners[(path, method)]
                previous_contract = _operation_wire_contract(
                    existing, previous_spec, alias.identity_replacements
                )
                incoming_contract = _operation_wire_contract(
                    incoming, spec, alias.identity_replacements
                )
                if canonical_bytes(previous_contract) != canonical_bytes(incoming_contract):
                    raise CanonicalMergeError(
                        f"classified operation aliases diverged at {method.upper()} {path}"
                    )
                candidates = {
                    first_id: (existing, previous_source, previous_spec),
                    second_id: (incoming, source, spec),
                }
                winner, winner_source, winner_spec = candidates[alias.canonical_operation_id]
                target_item[member] = copy.deepcopy(winner)
                target_item[member][OPERATION_ALIASES_EXTENSION] = [alias.alternate_operation_id]
                owners[(path, method)] = (winner_source, winner_spec)
                aliases += 1
    return paths, operation_occurrences, aliases, exact_duplicates


def _validate_refs(value: Any, schemas: dict[str, Any]) -> None:
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            name = ref.removeprefix("#/components/schemas/")
            if name not in schemas:
                raise CanonicalMergeError(f"canonical graph contains unresolved schema ref: {ref}")
        for child in value.values():
            _validate_refs(child, schemas)
    elif isinstance(value, list):
        for child in value:
            _validate_refs(child, schemas)


def canonical_merge_sources(
    specs: dict[str, dict[str, Any]] | Iterable[tuple[str, dict[str, Any]]],
) -> CanonicalMergeResult:
    """Canonicalize and merge source-local OpenAPI graphs without information loss."""
    source_map = dict(specs)
    ordered = {name: copy.deepcopy(source_map[name]) for name in sorted(source_map)}
    assignments = _schema_assignments(ordered)
    rewritten: dict[str, dict[str, Any]] = {}
    occurrence_count = 0
    for source, spec in ordered.items():
        schemas = spec.get("components", {}).get("schemas", {})
        occurrence_count += len(schemas)
        local_map = {
            local: assigned
            for (mapped_source, local), assigned in assignments.items()
            if mapped_source == source
        }
        rewritten_spec = _rewrite_local_schema_refs(spec, local_map)
        rewritten_schemas = rewritten_spec.setdefault("components", {}).setdefault("schemas", {})
        rewritten_spec["components"]["schemas"] = {
            local_map[name]: schema for name, schema in rewritten_schemas.items()
        }
        rewritten[source] = rewritten_spec

    merged_paths, operation_occurrences, alias_count, duplicate_count = _merge_paths(rewritten)
    components: dict[str, dict[str, Any]] = {}
    for source, spec in rewritten.items():
        for bucket, values in spec.get("components", {}).items():
            if not isinstance(values, dict):
                raise CanonicalMergeError(f"{source}: components.{bucket} must be an object")
            target = components.setdefault(bucket, {})
            for key, value in values.items():
                if key in target and canonical_bytes(target[key]) != canonical_bytes(value):
                    raise CanonicalMergeError(
                        f"canonical component collision at components.{bucket}.{key}"
                    )
                target.setdefault(key, copy.deepcopy(value))

    merged = {"paths": merged_paths, "components": components}
    _validate_refs(merged, components.get("schemas", {}))
    canonical_operations = sum(
        1 for item in merged_paths.values() for method in item if method.lower() in HTTP_METHODS
    )
    accounting = MergeAccounting(
        schema_occurrences=occurrence_count,
        schema_assignments=len(assignments),
        canonical_schemas=len(components.get("schemas", {})),
        operation_occurrences=operation_occurrences,
        canonical_operations=canonical_operations,
        operation_aliases=alias_count,
        exact_operation_duplicates=duplicate_count,
    )
    if accounting.schema_occurrences != accounting.schema_assignments:
        raise CanonicalMergeError("not every schema occurrence received a canonical assignment")
    classified_operations = accounting.canonical_operations + alias_count + duplicate_count
    if accounting.operation_occurrences != classified_operations:
        raise CanonicalMergeError("not every operation occurrence was preserved or classified")
    return CanonicalMergeResult(rewritten, merged, accounting, assignments)
