"""Remove explicitly deprecated HTTP operations from OpenAPI documents."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


@dataclass(frozen=True)
class DeprecatedOperationStats:
    """Counts from one deprecated-operation normalization pass."""

    operations_removed: int = 0
    paths_removed: int = 0


def remove_deprecated_operations(spec: dict[str, Any]) -> DeprecatedOperationStats:
    """Remove operations whose ``deprecated`` field is exactly ``True``.

    Non-HTTP path-item fields never count as operations. A path item with no
    HTTP methods after filtering is removed, including one that retains only
    path-level parameters or extensions. Components are intentionally untouched.
    """
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return DeprecatedOperationStats()

    operations_removed = 0
    empty_paths: list[str] = []
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if isinstance(operation, dict) and operation.get("deprecated") is True:
                del path_item[method]
                operations_removed += 1
        if not any(method in path_item for method in HTTP_METHODS):
            empty_paths.append(path)

    for path in empty_paths:
        del paths[path]
    return DeprecatedOperationStats(operations_removed, len(empty_paths))
