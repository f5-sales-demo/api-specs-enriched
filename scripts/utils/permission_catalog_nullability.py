"""Observed response-only nullability for permission catalogs and CE health (#1984)."""

from __future__ import annotations

import copy
from typing import Any

from scripts.utils.smsv2_response_nullability import qualify_response_nullability

KINDS = ("api_group", "api_group_element", "role")
PATHS = tuple(
    ("items", "*", field) for field in ("get_spec", "metadata", "owner_view", "system_metadata")
) + tuple(
    ("items", "*", "system_metadata", field)
    for field in ("deletion_timestamp", "modification_timestamp", "initializers", "owner_view")
)
PROVENANCE = {"issue": "#1984", "paths": [".".join(path) for path in PATHS]}


def qualify_permission_catalog_nullability(spec: dict[str, Any]) -> None:
    """Apply exact list-response repairs transactionally across catalog roots."""
    working = copy.deepcopy(spec)
    for kind in KINDS:
        qualify_response_nullability(
            working,
            kind + "ListResponse",
            "ves.io.schema." + kind + ".API.List",
            PATHS if kind != "role" else PATHS[:4],
            {
                **PROVENANCE,
                "paths": [".".join(path) for path in (PATHS if kind != "role" else PATHS[:4])],
            },
            "catalog_read",
        )
    if working != spec:
        spec["components"]["schemas"] = working["components"]["schemas"]


def qualify_health_gpu_nullability(spec: dict[str, Any]) -> None:
    """Preserve shared OS/GPU/request schemas and all other health fields."""
    paths = (("os_info", "gpu"),)
    qualify_response_nullability(
        spec,
        "debugHealthResponse",
        "ves.io.schema.operate.debug.CustomPublicAPI.HealthPublic",
        paths,
        {"issue": "#1984", "paths": ["os_info.gpu"]},
        "health_read",
    )
