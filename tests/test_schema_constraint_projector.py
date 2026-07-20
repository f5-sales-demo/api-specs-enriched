#!/usr/bin/env python3
"""Tests for SchemaConstraintProjector (plan item D).

The projector runs dead-last in the pipeline and mirrors naming constraints
from the ``x-f5xc-constraints`` vendor extension up to the standard JSON-Schema
property level (``pattern``/``minLength``/``maxLength``/``format``), so a
standard OpenAPI/JSON-Schema consumer can pull them deterministically without
knowing about the vendor extension.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.utils.schema_constraint_projector import SchemaConstraintProjector  # noqa: E402

DNS_1035 = "^[a-z]([-a-z0-9]*[a-z0-9])?$"
DNS_1123 = "^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"


def _naming_constraint(pattern: str = DNS_1035, max_len: int = 63) -> dict:
    return {
        "constraintType": "string",
        "category": "naming",
        "minLength": 1,
        "maxLength": max_len,
        "pattern": pattern,
        "format": "dns-label",
        "metadata": {"source": "discovery", "confidence": 0.99},
    }


def test_projects_naming_constraint_to_standard_keys():
    spec = {
        "components": {
            "schemas": {
                "Meta": {
                    "properties": {
                        "name": {"type": "string", "x-f5xc-constraints": _naming_constraint()},
                    }
                }
            }
        }
    }
    SchemaConstraintProjector().enrich_spec(spec)
    name = spec["components"]["schemas"]["Meta"]["properties"]["name"]
    assert name["pattern"] == DNS_1035
    assert name["minLength"] == 1
    assert name["maxLength"] == 63
    assert name["format"] == "dns-label"


def test_overwrites_stale_standard_values():
    """A stale generic maxLength (1024) at the property level is corrected to 63."""
    spec = {
        "components": {
            "schemas": {
                "Meta": {
                    "properties": {
                        "name": {
                            "type": "string",
                            "minLength": 6,
                            "maxLength": 1024,
                            "x-f5xc-constraints": _naming_constraint(),
                        },
                    }
                }
            }
        }
    }
    SchemaConstraintProjector().enrich_spec(spec)
    name = spec["components"]["schemas"]["Meta"]["properties"]["name"]
    assert name["minLength"] == 1
    assert name["maxLength"] == 63


def test_projects_nested_volume_name_dns1123():
    """Nested workload volume names (DNS-1123) are projected recursively."""
    spec = {
        "components": {
            "schemas": {
                "Workload": {
                    "properties": {
                        "volumes": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {
                                        "type": "string",
                                        "x-f5xc-constraints": _naming_constraint(pattern=DNS_1123),
                                    }
                                },
                            },
                        }
                    }
                }
            }
        }
    }
    SchemaConstraintProjector().enrich_spec(spec)
    vol_name = spec["components"]["schemas"]["Workload"]["properties"]["volumes"]["items"][
        "properties"
    ]["name"]
    assert vol_name["pattern"] == DNS_1123


def test_projects_dns_label_format_even_when_category_discovery():
    """Volume names arrive via discovery (category='discovery') but format='dns-label'.

    These must still be mirrored so the downstream DNS-1123 use case is covered.
    """
    spec = {
        "components": {
            "schemas": {
                "Vol": {
                    "properties": {
                        "name": {
                            "type": "string",
                            "x-f5xc-constraints": {
                                "constraintType": "string",
                                "category": "discovery",
                                "minLength": 1,
                                "maxLength": 63,
                                "pattern": DNS_1123,
                                "format": "dns-label",
                            },
                        }
                    }
                }
            }
        }
    }
    SchemaConstraintProjector().enrich_spec(spec)
    name = spec["components"]["schemas"]["Vol"]["properties"]["name"]
    assert name["pattern"] == DNS_1123
    assert name["maxLength"] == 63


def test_does_not_touch_non_naming_constraints():
    """Only naming-category constraints are mirrored (scoped change)."""
    spec = {
        "components": {
            "schemas": {
                "T": {
                    "properties": {
                        "timeout": {
                            "type": "integer",
                            "x-f5xc-constraints": {
                                "constraintType": "number",
                                "category": "timing",
                                "minimum": 1,
                                "maximum": 600,
                            },
                        },
                    }
                }
            }
        }
    }
    SchemaConstraintProjector().enrich_spec(spec)
    timeout = spec["components"]["schemas"]["T"]["properties"]["timeout"]
    # Numeric/timing constraints are left in the vendor extension only.
    assert "minimum" not in timeout
