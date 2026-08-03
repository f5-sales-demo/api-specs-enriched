# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Canonical fail-closed validation for source OpenAPI contract graphs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scripts.utils.raw_manifest import RawManifestError, validate_raw_manifest

if TYPE_CHECKING:
    from collections.abc import Iterable

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head", "trace"})


class SpecSelectionError(ValueError):
    """A directory does not define one complete, unambiguous OpenAPI file set."""


@dataclass(frozen=True)
class SpecSelection:
    """The exact OpenAPI set declared by one directory contract."""

    contract_name: str
    contract_bytes: bytes
    files: tuple[Path, ...]

    @property
    def names(self) -> tuple[str, ...]:
        """Return selected filenames in deterministic order."""
        return tuple(path.name for path in self.files)


class SourceGraphValidationError(ValueError):
    """The source graph cannot be processed without inventing or deleting contract data."""

    def __init__(self, errors: Iterable[str]) -> None:
        """Initialize with deterministic, de-duplicated contract errors."""
        self.errors = tuple(sorted(set(errors)))
        super().__init__("; ".join(self.errors))


def source_spec_files(input_dir: Path) -> list[Path]:
    """Return only OpenAPI artifacts declared by the directory's selector contract."""
    return list(select_source_specs(input_dir).files)


def select_source_specs(input_dir: Path) -> SpecSelection:
    """Strictly parse exactly one raw manifest or generated index selector."""
    manifest_path = input_dir / "manifest.json"
    index_path = input_dir / "index.json"
    selectors = [path for path in (manifest_path, index_path) if path.is_file()]
    if len(selectors) != 1:
        raise SpecSelectionError(
            f"{input_dir} must contain exactly one selector: manifest.json or index.json"
        )

    selector_path = selectors[0]
    try:
        contract_bytes = selector_path.read_bytes()
        document = json.loads(contract_bytes)
    except (OSError, json.JSONDecodeError) as error:
        raise SpecSelectionError(f"invalid {selector_path.name}: {error}") from error

    if selector_path.name == "manifest.json":
        names = _manifest_spec_names(document, input_dir)
    else:
        names = _index_spec_names(document)
    files = _resolve_declared_specs(input_dir, names)
    _reject_undeclared_openapi(input_dir, set(names), selector_path.name)
    return SpecSelection(selector_path.name, contract_bytes, files)


def _manifest_spec_names(document: Any, input_dir: Path) -> tuple[str, ...]:
    try:
        return validate_raw_manifest(document, source_dir=input_dir).files
    except RawManifestError as error:
        raise SpecSelectionError(str(error)) from error


def _index_spec_names(document: Any) -> tuple[str, ...]:
    if not isinstance(document, dict):
        raise SpecSelectionError("index.json root must be an object")
    for field in ("version", "timestamp"):
        if not isinstance(document.get(field), str) or not document[field].strip():
            raise SpecSelectionError(f"index.json {field!r} must be a non-empty string")
    specifications = document.get("specifications")
    if not isinstance(specifications, list) or not specifications:
        raise SpecSelectionError("index.json specifications must be a non-empty array")
    declared: list[str] = []
    for position, entry in enumerate(specifications):
        if not isinstance(entry, dict) or not isinstance(entry.get("file"), str):
            raise SpecSelectionError(f"index.json specifications[{position}].file must be a string")
        declared.append(entry["file"])
    names = _unique_spec_names(declared, "index.json specifications")
    if "openapi.json" in names:
        raise SpecSelectionError(
            "index.json specifications must not duplicate the canonical openapi.json asset"
        )
    return tuple(sorted((*names, "openapi.json")))


def _unique_spec_names(value: Any, location: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise SpecSelectionError(f"{location} must be a non-empty array")
    names: list[str] = []
    for position, name in enumerate(value):
        if (
            not isinstance(name, str)
            or not name.endswith(".json")
            or name in {"manifest.json", "index.json"}
            or Path(name).name != name
        ):
            raise SpecSelectionError(f"{location}[{position}] is not a safe JSON filename")
        names.append(name)
    duplicates = sorted(name for name in set(names) if names.count(name) > 1)
    if duplicates:
        raise SpecSelectionError(f"{location} contains duplicate filenames: {duplicates}")
    return tuple(sorted(names))


def _resolve_declared_specs(input_dir: Path, names: tuple[str, ...]) -> tuple[Path, ...]:
    files: list[Path] = []
    for name in names:
        path = input_dir / name
        if not path.is_file() or path.is_symlink():
            raise SpecSelectionError(f"declared OpenAPI file is missing or not regular: {name}")
        try:
            document = json.loads(path.read_bytes())
        except (OSError, json.JSONDecodeError) as error:
            raise SpecSelectionError(
                f"declared OpenAPI file is invalid JSON: {name}: {error}"
            ) from error
        if not isinstance(document, dict) or not isinstance(document.get("openapi"), str):
            raise SpecSelectionError(f"declared file is not an OpenAPI document: {name}")
        files.append(path)
    return tuple(files)


def _reject_undeclared_openapi(
    input_dir: Path, declared_names: set[str], selector_name: str
) -> None:
    undeclared: list[str] = []
    for path in sorted(input_dir.glob("*.json")):
        if path.name == selector_name or path.name in declared_names:
            continue
        try:
            document = json.loads(path.read_bytes())
        except (OSError, json.JSONDecodeError) as error:
            raise SpecSelectionError(
                f"cannot classify undeclared JSON file {path.name}: {error}"
            ) from error
        if isinstance(document, dict) and ("openapi" in document or "swagger" in document):
            undeclared.append(path.name)
    if undeclared:
        raise SpecSelectionError(f"undeclared OpenAPI files are present: {undeclared}")


def _local_pointer_error(document: dict[str, Any], ref: str) -> str | None:
    if ref == "#":
        return None
    if not ref.startswith("#/"):
        return f"nonlocal reference is not supported: {ref!r}"

    current: Any = document
    for encoded_token in ref[2:].split("/"):
        token = encoded_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and token in current:
            current = current[token]
            continue
        if isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
            continue
        return f"unresolved local reference: {ref!r}"
    return None


def _validate_references(spec: dict[str, Any], errors: list[str]) -> None:
    pending: list[tuple[str, Any]] = [("$", spec)]
    while pending:
        location, current = pending.pop()
        if isinstance(current, dict):
            if "$ref" in current:
                ref = current["$ref"]
                if not isinstance(ref, str) or not ref:
                    errors.append(f"{location} has a malformed $ref")
                else:
                    if len(current) != 1:
                        errors.append(f"{location} reference {ref!r} has sibling fields")
                    if pointer_error := _local_pointer_error(spec, ref):
                        errors.append(f"{location} has {pointer_error}")
            pending.extend((f"{location}/{key}", value) for key, value in current.items())
        elif isinstance(current, list):
            pending.extend((f"{location}/{index}", value) for index, value in enumerate(current))


def _validate_operation(path: str, method: str, operation: Any, errors: list[str]) -> str | None:
    location = f"{method.upper()} {path}"
    if not isinstance(operation, dict) or not operation:
        errors.append(f"{location} operation must be a non-empty object")
        return None

    operation_id = operation.get("operationId")
    valid_operation_id: str | None = None
    if not isinstance(operation_id, str) or not operation_id.strip():
        errors.append(f"{location} has no operationId")
    else:
        parts = operation_id.split(".")
        if (
            len(parts) < 6
            or parts[:3] != ["ves", "io", "schema"]
            or any(not part for part in parts)
        ):
            errors.append(f"{location} operationId is not fully qualified: {operation_id!r}")
        else:
            valid_operation_id = operation_id

    tags = operation.get("tags")
    if (
        not isinstance(tags, list)
        or not tags
        or any(not isinstance(tag, str) or not tag.strip() for tag in tags)
    ):
        errors.append(f"{location} must have at least one non-empty tag")

    responses = operation.get("responses")
    if not isinstance(responses, dict) or not responses:
        errors.append(f"{location} responses must be a non-empty object")

    if "requestBody" in operation:
        request_body = operation["requestBody"]
        if not isinstance(request_body, dict) or not request_body:
            errors.append(f"{location} requestBody must be a non-empty object")
        elif "$ref" not in request_body:
            content = request_body.get("content")
            if not isinstance(content, dict) or not content:
                errors.append(f"{location} requestBody has no content")
    return valid_operation_id


def validate_source_graph(spec: Any) -> None:
    """Validate the canonical references and executable-operation contract."""
    if not isinstance(spec, dict):
        raise SourceGraphValidationError(["document root must be an object"])

    errors: list[str] = []
    openapi = spec.get("openapi")
    if not isinstance(openapi, str) or not openapi.strip():
        errors.append("document has no OpenAPI version")

    paths = spec.get("paths")
    operation_count = 0
    operation_locations: dict[str, list[str]] = {}
    if not isinstance(paths, dict) or not paths:
        errors.append("paths must be a non-empty object")
    else:
        for path, path_item in paths.items():
            if not isinstance(path, str) or not isinstance(path_item, dict):
                errors.append("paths must map string keys to path-item objects")
                continue
            for member, operation in path_item.items():
                if not isinstance(member, str):
                    errors.append(f"path {path!r} has a non-string member name")
                    continue
                method = member.lower()
                if method not in HTTP_METHODS:
                    continue
                operation_count += 1
                operation_id = _validate_operation(path, method, operation, errors)
                if operation_id is not None:
                    operation_locations.setdefault(operation_id, []).append(
                        f"{method.upper()} {path}"
                    )

    if operation_count == 0:
        errors.append("document has no executable operations")
    for operation_id, locations in operation_locations.items():
        if len(locations) > 1:
            errors.append(
                f"duplicate operationId {operation_id!r} at {', '.join(sorted(locations))}"
            )

    _validate_references(spec, errors)
    if errors:
        raise SourceGraphValidationError(errors)


def validate_source_files(spec_files: Iterable[Path]) -> list[dict[str, str]]:
    """Validate every source file and return deterministic per-file failures."""
    return [
        failure
        for spec_file in sorted(spec_files)
        if (failure := _validate_source_file(spec_file)) is not None
    ]


def _validate_source_file(spec_file: Path) -> dict[str, str] | None:
    try:
        spec = json.loads(spec_file.read_text(encoding="utf-8"))
        validate_source_graph(spec)
    except (OSError, json.JSONDecodeError, SourceGraphValidationError) as error:
        return {"file": spec_file.name, "error": str(error)}
    return None
