# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Canonicalize source-owned OpenAPI components without discarding conflicts.

The upstream F5 XC export repeats component names across service documents.  A
plain dictionary union is safe only when every occurrence of a name has the same
contract, including the contracts reached through local component references.
This module computes that recursive equivalence relation, assigns stable keys to
every distinct contract, and rewrites each source document to use those keys.

Only whole-component references in the four reusable OpenAPI component categories
are accepted.  Failing closed here is intentional: silently retaining an external,
partial, unsupported, or unresolved reference would make a later merge depend on
source order again.
"""

from __future__ import annotations

import copy
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

SUPPORTED_COMPONENT_CATEGORIES = (
    "schemas",
    "responses",
    "parameters",
    "requestBodies",
)

_TITLE_PREFIX = "F5 Distributed Cloud Services API for "
_OWNER = re.compile(r"ves\.io\.schema(?:\.[A-Za-z0-9_-]+)+")
_SOURCE_FILENAME = re.compile(
    r"docs-cloud-f5-com\.\d+\.public\."
    r"(?P<owner>ves\.io\.schema(?:\.[A-Za-z0-9_-]+)+)\.ves-swagger\.json"
)
_COMPONENT_KEY = re.compile(r"[A-Za-z0-9._-]+")


class ComponentCanonicalizationError(ValueError):
    """A source graph cannot be canonicalized without losing contract data."""


@dataclass(frozen=True, order=True)
class _Node:
    owner: str
    category: str
    name: str


@dataclass(frozen=True)
class CategoryAccounting:
    """Complete occurrence/group accounting for one component category."""

    occurrences: int
    name_groups: int
    duplicate_name_groups: int
    duplicate_occurrences: int
    raw_conflict_name_groups: int
    raw_conflict_occurrences: int
    conflict_name_groups: int
    propagated_conflict_name_groups: int
    distinct_contracts: int
    canonical_keys: int
    renamed_occurrences: int
    shared_occurrences: int


@dataclass(frozen=True)
class CanonicalizationAccounting:
    """Per-category and aggregate accounting for one canonicalization run."""

    categories: dict[str, CategoryAccounting]
    totals: CategoryAccounting
    refinement_rounds: int

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable, deterministically ordered representation."""
        return {
            "categories": {
                category: asdict(self.categories[category])
                for category in SUPPORTED_COMPONENT_CATEGORIES
            },
            "totals": asdict(self.totals),
            "refinement_rounds": self.refinement_rounds,
        }


@dataclass(frozen=True)
class CanonicalizationResult:
    """Canonical source documents, their bindings, and the global component set."""

    documents: dict[str, dict[str, Any]]
    components: dict[str, dict[str, Any]]
    bindings: dict[str, dict[str, dict[str, str]]]
    accounting: CanonicalizationAccounting

    def to_dict(self) -> dict[str, Any]:
        """Return the complete deterministic result as JSON-compatible data."""
        return {
            "documents": self.documents,
            "components": self.components,
            "bindings": self.bindings,
            "accounting": self.accounting.to_dict(),
        }

    def canonical_bytes(self) -> bytes:
        """Serialize the result for order-independence assertions and evidence."""
        return (
            json.dumps(
                self.to_dict(),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode()


@dataclass(frozen=True)
class _Source:
    source_id: str
    owner: str
    document: dict[str, Any]


def _fail(message: str) -> ComponentCanonicalizationError:
    return ComponentCanonicalizationError(f"component canonicalization: {message}")


def source_owner_identity(source_id: str, document: Mapping[str, Any]) -> str:
    """Return the stable service owner, rejecting ambiguous source metadata.

    The numeric position in an upstream filename is deliberately ignored.  The
    stable owner encoded after ``public.`` must agree with the service identity in
    ``info.title``; accepting only one signal would make an accidental rename or a
    mismatched document silently change every qualified component key.
    """
    if not isinstance(source_id, str) or not source_id:
        raise _fail("source identifier must be a non-empty string")
    filename_match = _SOURCE_FILENAME.fullmatch(Path(source_id).name)
    if filename_match is None:
        raise _fail(f"source identifier has no stable service owner: {source_id!r}")
    filename_owner = filename_match.group("owner")

    info = document.get("info")
    title = info.get("title") if isinstance(info, Mapping) else None
    if not isinstance(title, str) or not title.startswith(_TITLE_PREFIX):
        raise _fail(f"source {source_id!r} has no stable owner in info.title")
    title_owner = title.removeprefix(_TITLE_PREFIX)
    if _OWNER.fullmatch(title_owner) is None:
        raise _fail(f"source {source_id!r} has invalid owner identity {title_owner!r}")
    if filename_owner != title_owner:
        raise _fail(
            f"source {source_id!r} has ambiguous owners: "
            f"filename={filename_owner!r}, title={title_owner!r}"
        )
    return title_owner


def _validate_component_key(category: str, name: Any, owner: str) -> str:
    if not isinstance(name, str) or _COMPONENT_KEY.fullmatch(name) is None:
        raise _fail(f"{owner} has invalid {category} component key {name!r}")
    return name


def _decode_pointer_token(token: str, ref: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(token):
        if token[index] != "~":
            result.append(token[index])
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise _fail(f"component reference has invalid JSON Pointer escape: {ref!r}")
        result.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(result)


def _encode_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _parse_component_ref(ref: Any) -> tuple[str, str]:
    if not isinstance(ref, str):
        raise _fail(f"OpenAPI $ref must be a string, got {type(ref).__name__}")
    if not ref.startswith("#/"):
        raise _fail(f"non-local reference is not supported: {ref!r}")
    parts = ref.split("/")
    if len(parts) != 4 or parts[0] != "#" or parts[1] != "components":
        raise _fail(f"unsupported local component reference: {ref!r}")
    category = _decode_pointer_token(parts[2], ref)
    if category not in SUPPORTED_COMPONENT_CATEGORIES:
        raise _fail(f"unsupported component category in reference: {ref!r}")
    name = _decode_pointer_token(parts[3], ref)
    if _COMPONENT_KEY.fullmatch(name) is None:
        raise _fail(f"component reference has unsupported key {name!r}: {ref!r}")
    return category, name


def _walk_refs(value: Any, visit: Callable[[str], None]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise _fail(f"JSON object key must be a string, got {key!r}")
            if key == "$ref":
                visit(child)
            else:
                _walk_refs(child, visit)
    elif isinstance(value, list):
        for child in value:
            _walk_refs(child, visit)
    elif isinstance(value, float) and not math.isfinite(value):
        raise _fail("non-finite JSON number is not supported")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise _fail(f"non-JSON value is not supported: {type(value).__name__}")


def _freeze_json(value: Any) -> Any:
    """Return an exact, hashable JSON value that distinguishes Python scalar types."""
    if isinstance(value, dict):
        return (
            "object",
            tuple((key, _freeze_json(child)) for key, child in sorted(value.items())),
        )
    if isinstance(value, list):
        return ("array", tuple(_freeze_json(child) for child in value))
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, int):
        return ("integer", value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _fail("non-finite JSON number is not supported")
        return ("number", value.hex())
    if isinstance(value, str):
        return ("string", value)
    raise _fail(f"non-JSON value is not supported: {type(value).__name__}")


def _component_signature(
    value: Any,
    owner: str,
    occurrences: Mapping[_Node, Any],
    target_label: Callable[[_Node], Any],
) -> Any:
    """Freeze one contract while substituting its referenced target partitions."""
    if isinstance(value, dict):
        items = []
        for key, child in sorted(value.items()):
            if key == "$ref":
                category, name = _parse_component_ref(child)
                target = _Node(owner, category, name)
                if target not in occurrences:
                    raise _fail(f"{owner} has unresolved component reference {child!r}")
                items.append((key, ("component-ref", category, name, target_label(target))))
            else:
                items.append((key, _component_signature(child, owner, occurrences, target_label)))
        return ("object", tuple(items))
    if isinstance(value, list):
        return (
            "array",
            tuple(_component_signature(child, owner, occurrences, target_label) for child in value),
        )
    return _freeze_json(value)


def _partition_nodes(
    groups: Mapping[tuple[str, str], list[_Node]],
    occurrences: Mapping[_Node, Any],
) -> tuple[
    dict[_Node, frozenset[_Node]],
    dict[tuple[str, str], list[frozenset[_Node]]],
    int,
]:
    """Compute recursive contract equivalence by monotonic partition refinement."""

    def regroup(
        labels: Mapping[_Node, frozenset[_Node]] | None,
    ) -> tuple[
        dict[_Node, frozenset[_Node]],
        dict[tuple[str, str], list[frozenset[_Node]]],
    ]:
        by_node: dict[_Node, frozenset[_Node]] = {}
        by_group: dict[tuple[str, str], list[frozenset[_Node]]] = {}
        for group_key in sorted(groups):
            signature_members: dict[Any, list[_Node]] = {}
            for node in sorted(groups[group_key]):
                signature = _component_signature(
                    occurrences[node],
                    node.owner,
                    occurrences,
                    (
                        (lambda target: (target.category, target.name))
                        if labels is None
                        else (lambda target: tuple(sorted(labels[target])))
                    ),
                )
                signature_members.setdefault(signature, []).append(node)
            classes = [
                frozenset(members)
                for _, members in sorted(
                    signature_members.items(),
                    key=lambda item: tuple(item[1]),
                )
            ]
            by_group[group_key] = classes
            for members in classes:
                for node in members:
                    by_node[node] = members
        return by_node, by_group

    labels, raw_groups = regroup(None)
    rounds = 0
    while True:
        refined, _ = regroup(labels)
        rounds += 1
        if all(refined[node] == labels[node] for node in labels):
            return refined, raw_groups, rounds
        for node, members in refined.items():
            if not members <= labels[node]:
                raise AssertionError("component equivalence partition unexpectedly merged")
        if rounds > len(occurrences) + 1:
            raise AssertionError("component equivalence partition did not converge")
        labels = refined


def _rewrite_refs(
    value: Any,
    owner: str,
    bindings: Mapping[_Node, str],
) -> Any:
    if isinstance(value, dict):
        rewritten: dict[str, Any] = {}
        for key, child in value.items():
            if key == "$ref":
                category, name = _parse_component_ref(child)
                target = _Node(owner, category, name)
                canonical_name = bindings.get(target)
                if canonical_name is None:
                    raise _fail(f"{owner} has unresolved component reference {child!r}")
                rewritten[key] = f"#/components/{category}/{_encode_pointer_token(canonical_name)}"
            else:
                rewritten[key] = _rewrite_refs(child, owner, bindings)
        return rewritten
    if isinstance(value, list):
        return [_rewrite_refs(child, owner, bindings) for child in value]
    return copy.deepcopy(value)


def _sum_accounting(
    categories: Mapping[str, CategoryAccounting],
) -> CategoryAccounting:
    field_names = (field.name for field in fields(CategoryAccounting))
    return CategoryAccounting(
        **{
            field: sum(getattr(accounting, field) for accounting in categories.values())
            for field in field_names
        }
    )


def canonicalize_source_components(
    sources: Mapping[str, dict[str, Any]],
) -> CanonicalizationResult:
    """Canonicalize reusable components and rewrite every source-local reference.

    Args:
        sources: Mapping from the original upstream filename (or path ending in that
            filename) to its complete OpenAPI document.

    Returns:
        Rewritten source documents keyed by the same identifiers, the exact global
        canonical component union, per-source original-to-canonical bindings, and
        full occurrence/group accounting.

    The input mapping and its documents are never mutated.  Source mapping order has
    no effect on any returned byte when :meth:`CanonicalizationResult.canonical_bytes`
    is used.
    """
    if not isinstance(sources, Mapping):
        raise TypeError("sources must be a mapping of source identifiers to documents")

    ordered_sources: list[_Source] = []
    owners: dict[str, str] = {}
    for source_id, document in sources.items():
        if not isinstance(document, dict):
            raise _fail(f"source {source_id!r} must contain a JSON object")
        owner = source_owner_identity(source_id, document)
        previous_source = owners.get(owner)
        if previous_source is not None:
            raise _fail(
                f"owner {owner!r} is ambiguous across sources {previous_source!r} and {source_id!r}"
            )
        owners[owner] = source_id
        ordered_sources.append(_Source(source_id, owner, document))
    ordered_sources.sort(key=lambda source: (source.owner, source.source_id))

    occurrences: dict[_Node, Any] = {}
    groups: dict[tuple[str, str], list[_Node]] = {}
    for source in ordered_sources:
        components = source.document.get("components", {})
        if not isinstance(components, dict):
            raise _fail(f"source {source.source_id!r} components must be an object")
        for category in SUPPORTED_COMPONENT_CATEGORIES:
            category_values = components.get(category, {})
            if not isinstance(category_values, dict):
                raise _fail(f"source {source.source_id!r} components.{category} must be an object")
            for unvalidated_name, value in category_values.items():
                name = _validate_component_key(category, unvalidated_name, source.owner)
                node = _Node(source.owner, category, name)
                occurrences[node] = value
                groups.setdefault((category, name), []).append(node)

    for source in ordered_sources:

        def validate_ref(ref: str, *, owner: str = source.owner) -> None:
            category, name = _parse_component_ref(ref)
            if _Node(owner, category, name) not in occurrences:
                raise _fail(f"{owner} has unresolved component reference {ref!r}")

        _walk_refs(source.document, validate_ref)

    partitions, raw_partitions, refinement_rounds = _partition_nodes(groups, occurrences)
    final_groups: dict[tuple[str, str], set[frozenset[_Node]]] = {}
    for group_key, nodes in groups.items():
        final_groups[group_key] = {partitions[node] for node in nodes}

    bindings_by_node: dict[_Node, str] = {}
    key_claims: dict[str, dict[str, frozenset[_Node]]] = {
        category: {} for category in SUPPORTED_COMPONENT_CATEGORIES
    }
    for category, name in sorted(groups):
        classes = sorted(final_groups[(category, name)], key=lambda members: tuple(sorted(members)))
        conflicting = len(classes) > 1
        for members in classes:
            representative = min(members)
            canonical_name = f"{representative.owner}__{name}" if conflicting else name
            _validate_component_key(category, canonical_name, representative.owner)
            previous_members = key_claims[category].get(canonical_name)
            if previous_members is not None and previous_members != members:
                previous_names = sorted({node.name for node in previous_members})
                current_names = sorted({node.name for node in members})
                raise _fail(
                    f"canonical {category} key collision at {canonical_name!r}: "
                    f"{previous_names!r} versus {current_names!r}"
                )
            key_claims[category][canonical_name] = members
            for node in members:
                bindings_by_node[node] = canonical_name

    rewritten_documents: dict[str, dict[str, Any]] = {}
    public_bindings: dict[str, dict[str, dict[str, str]]] = {}
    for source in sorted(ordered_sources, key=lambda item: item.source_id):
        rewritten = _rewrite_refs(source.document, source.owner, bindings_by_node)
        components = rewritten.get("components", {})
        source_bindings: dict[str, dict[str, str]] = {}
        for category in SUPPORTED_COMPONENT_CATEGORIES:
            category_values = components.get(category, {})
            renamed: dict[str, Any] = {}
            category_bindings: dict[str, str] = {}
            for original_name, value in sorted(category_values.items()):
                node = _Node(source.owner, category, original_name)
                canonical_name = bindings_by_node[node]
                if canonical_name in renamed:
                    raise _fail(
                        f"{source.owner} maps multiple {category} components to {canonical_name!r}"
                    )
                renamed[canonical_name] = value
                category_bindings[original_name] = canonical_name
            if category in components:
                components[category] = renamed
            source_bindings[category] = category_bindings
        rewritten_documents[source.source_id] = rewritten
        public_bindings[source.source_id] = source_bindings

    canonical_components: dict[str, dict[str, Any]] = {
        category: {} for category in SUPPORTED_COMPONENT_CATEGORIES
    }
    for source_id in sorted(rewritten_documents):
        components = rewritten_documents[source_id].get("components", {})
        for category in SUPPORTED_COMPONENT_CATEGORIES:
            for name, value in components.get(category, {}).items():
                if name not in canonical_components[category]:
                    canonical_components[category][name] = copy.deepcopy(value)
                elif _freeze_json(canonical_components[category][name]) != _freeze_json(value):
                    raise AssertionError(
                        f"canonical {category} key {name!r} has non-identical values"
                    )
    canonical_components = {
        category: dict(sorted(values.items())) for category, values in canonical_components.items()
    }

    category_accounting: dict[str, CategoryAccounting] = {}
    for category in SUPPORTED_COMPONENT_CATEGORIES:
        category_groups = {
            group_key: nodes for group_key, nodes in groups.items() if group_key[0] == category
        }
        raw_classes = {group_key: raw_partitions[group_key] for group_key in category_groups}
        category_final_groups = {
            group_key: final_groups[group_key] for group_key in category_groups
        }
        occurrences_count = sum(len(nodes) for nodes in category_groups.values())
        distinct_contracts = sum(len(classes) for classes in category_final_groups.values())
        renamed_occurrences = sum(
            bindings_by_node[node] != node.name
            for nodes in category_groups.values()
            for node in nodes
        )
        category_accounting[category] = CategoryAccounting(
            occurrences=occurrences_count,
            name_groups=len(category_groups),
            duplicate_name_groups=sum(len(nodes) > 1 for nodes in category_groups.values()),
            duplicate_occurrences=sum(
                len(nodes) - 1 for nodes in category_groups.values() if len(nodes) > 1
            ),
            raw_conflict_name_groups=sum(len(classes) > 1 for classes in raw_classes.values()),
            raw_conflict_occurrences=sum(
                len(category_groups[group_key]) - 1
                for group_key, classes in raw_classes.items()
                if len(classes) > 1
            ),
            conflict_name_groups=sum(
                len(classes) > 1 for classes in category_final_groups.values()
            ),
            propagated_conflict_name_groups=sum(
                len(raw_classes[group_key]) == 1 and len(classes) > 1
                for group_key, classes in category_final_groups.items()
            ),
            distinct_contracts=distinct_contracts,
            canonical_keys=len(canonical_components[category]),
            renamed_occurrences=renamed_occurrences,
            shared_occurrences=occurrences_count - distinct_contracts,
        )

    accounting = CanonicalizationAccounting(
        categories=category_accounting,
        totals=_sum_accounting(category_accounting),
        refinement_rounds=refinement_rounds,
    )
    return CanonicalizationResult(
        documents=rewritten_documents,
        components=canonical_components,
        bindings=public_bindings,
        accounting=accounting,
    )
