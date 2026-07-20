#!/usr/bin/env python3
"""Cross-spec invariant tests over the generated enriched specs (plan item F).

Guards against regression of the naming-constraint fixes by scanning the
generated ``docs/specifications/api/*.json`` output. Skips if the specs have not
been generated yet.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SPECS_DIR = REPO_ROOT / "docs" / "specifications" / "api"

DNS_1035 = "^[a-z]([-a-z0-9]*[a-z0-9])?$"
DNS_1123 = "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"

pytestmark = pytest.mark.skipif(
    not SPECS_DIR.exists() or not any(SPECS_DIR.glob("*.json")),
    reason="enriched specs not generated (run `make enrich`)",
)


def _domain_specs() -> list[Path]:
    skip = {"index.json", "validation.json", "minimal-export-defaults.json", "other.json"}
    return [p for p in sorted(SPECS_DIR.glob("*.json")) if p.name not in skip]


def _iter_create_meta_name() -> list[tuple[str, dict]]:
    """Yield (file, name-property) for every schemaObjectCreateMetaType.name."""
    out = []
    for path in _domain_specs():
        data = json.loads(path.read_text())
        schemas = data.get("components", {}).get("schemas", {})
        meta = schemas.get("schemaObjectCreateMetaType")
        if isinstance(meta, dict):
            name = meta.get("properties", {}).get("name")
            if isinstance(name, dict):
                out.append((path.name, name))
    return out


def test_create_meta_name_is_dns1035_everywhere():
    entries = _iter_create_meta_name()
    assert entries, "expected schemaObjectCreateMetaType in generated specs"
    for filename, name in entries:
        # Standard JSON-Schema keys (projected — pullable by any consumer).
        assert name.get("pattern") == DNS_1035, f"{filename}: standard pattern"
        assert name.get("minLength") == 1, f"{filename}: standard minLength"
        assert name.get("maxLength") == 63, f"{filename}: standard maxLength (API enforces 63)"
        assert name.get("format") == "dns-label", f"{filename}: standard format"
        # Vendor extension agrees.
        c = name.get("x-f5xc-constraints", {})
        assert c.get("pattern") == DNS_1035, f"{filename}: constraint pattern"
        assert c.get("maxLength") == 63, f"{filename}: constraint maxLength"


def test_no_native_name_claims_1024_maxlength():
    """The stale generic 1024 default must not survive on native name fields."""
    for filename, name in _iter_create_meta_name():
        assert name.get("maxLength") != 1024, f"{filename}: name still claims maxLength 1024"
        assert name.get("x-f5xc-constraints", {}).get("maxLength") != 1024, filename


def test_dns1123_volume_names_are_alnum_first():
    """Workload volume names (dns_1123_label) keep the alnum-first pattern and are
    projected to standard keys so the DNS-1123 use case is deterministically pullable."""
    path = SPECS_DIR / "container_services.json"
    if not path.exists():
        pytest.skip("container_services.json not present")
    data = json.loads(path.read_text())
    schemas = data.get("components", {}).get("schemas", {})
    checked = 0
    for schema in schemas.values():
        if not isinstance(schema, dict):
            continue
        name = schema.get("properties", {}).get("name")
        if not isinstance(name, dict):
            continue
        rules = name.get("x-ves-validation-rules", {}) or {}
        if "ves.io.schema.rules.string.dns_1123_label" in rules:
            c = name.get("x-f5xc-constraints", {})
            assert c.get("pattern") == DNS_1123, "volume name must be DNS-1123 in constraint"
            assert name.get("pattern") == DNS_1123, "volume name DNS-1123 must be projected"
            checked += 1
    assert checked > 0, "expected at least one dns_1123_label volume name"
