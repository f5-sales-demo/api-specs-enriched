"""Tests for scripts.compile_catalog — catalog compiler unit and integration tests."""
# pylint: disable=use-implicit-booleaness-not-comparison  # Explicit empty results document the expected shape.

# pylint: disable=missing-function-docstring
import copy
import json
import sys
import tempfile
from pathlib import Path

import pytest

from scripts.compile_catalog import (
    assign_danger_level,
    compile_catalog,
    extract_category_name,
    extract_parameters,
    extract_response_schema,
    generate_operation_name,
    group_paths_by_resource,
    main,
    merge_spec_files,
)


def test_assign_danger_level_get():
    assert assign_danger_level("GET") == "low"


def test_assign_danger_level_options():
    assert assign_danger_level("OPTIONS") == "low"


def test_assign_danger_level_post():
    assert assign_danger_level("POST") == "medium"


def test_assign_danger_level_put():
    assert assign_danger_level("PUT") == "medium"


def test_assign_danger_level_patch():
    assert assign_danger_level("PATCH") == "medium"


def test_assign_danger_level_delete():
    assert assign_danger_level("DELETE") == "high"


def test_extract_category_name_namespace_path():
    path = "/api/config/namespaces/{namespace}/http_loadbalancers"
    assert extract_category_name(path) == "http-loadbalancers"


def test_extract_category_name_web_path():
    path = "/api/web/namespaces"
    assert extract_category_name(path) == "namespaces"


def test_extract_category_name_item_path():
    path = "/api/config/namespaces/{namespace}/http_loadbalancers/{name}"
    assert extract_category_name(path) == "http-loadbalancers"


def test_generate_operation_name_list():
    assert (
        generate_operation_name("GET", "/api/config/namespaces/{namespace}/http_loadbalancers")
        == "list_http_loadbalancers"
    )


def test_generate_operation_name_get_item():
    assert (
        generate_operation_name(
            "GET",
            "/api/config/namespaces/{namespace}/http_loadbalancers/{name}",
        )
        == "get_http_loadbalancer"
    )


def test_generate_operation_name_post():
    assert (
        generate_operation_name("POST", "/api/config/namespaces/{namespace}/http_loadbalancers")
        == "create_http_loadbalancer"
    )


def test_generate_operation_name_put():
    assert (
        generate_operation_name(
            "PUT",
            "/api/config/namespaces/{namespace}/http_loadbalancers/{name}",
        )
        == "replace_http_loadbalancer"
    )


def test_generate_operation_name_patch():
    assert (
        generate_operation_name(
            "PATCH",
            "/api/config/namespaces/{namespace}/http_loadbalancers/{name}",
        )
        == "update_http_loadbalancer"
    )


def test_generate_operation_name_delete():
    assert (
        generate_operation_name(
            "DELETE",
            "/api/config/namespaces/{namespace}/http_loadbalancers/{name}",
        )
        == "delete_http_loadbalancer"
    )


def test_extract_parameters_path_params():
    path = "/api/config/namespaces/{namespace}/http_loadbalancers/{name}"
    params = extract_parameters(path, {})
    assert any(
        p["name"] == "namespace" and p["in"] == "path" and p["required"] is True for p in params
    )
    assert any(p["name"] == "name" and p["in"] == "path" and p["required"] is True for p in params)


def test_extract_parameters_namespace_gets_default():
    path = "/api/config/namespaces/{namespace}/http_loadbalancers"
    params = extract_parameters(path, {})
    ns_param = next(p for p in params if p["name"] == "namespace")
    assert ns_param["default"] == "$F5XC_NAMESPACE"


def test_group_paths_by_resource():
    paths = {
        "/api/config/namespaces/{namespace}/http_loadbalancers": {"get": {}},
        "/api/config/namespaces/{namespace}/http_loadbalancers/{name}": {"delete": {}},
        "/api/config/namespaces/{namespace}/origin_pools": {"get": {}},
    }
    groups = group_paths_by_resource(paths)
    assert "http-loadbalancers" in groups
    assert "origin-pools" in groups
    assert len(groups["http-loadbalancers"]) == 2


def test_compile_catalog_structure():
    openapi = {
        "openapi": "3.0.3",
        "paths": {
            "/api/config/namespaces/{namespace}/http_loadbalancers": {
                "get": {"operationId": "list_lbs", "responses": {}},
            },
            "/api/config/namespaces/{namespace}/http_loadbalancers/{name}": {
                "delete": {"operationId": "delete_lb", "responses": {}},
            },
        },
    }
    catalog = compile_catalog(openapi)
    assert catalog["service"] == "f5xc"
    assert catalog["auth"]["type"] == "api_token"
    assert catalog["auth"]["headerTemplate"] == "APIToken {token}"
    assert len(catalog["categories"]) >= 1
    cat = next(c for c in catalog["categories"] if c["name"] == "http-loadbalancers")
    op_names = [op["name"] for op in cat["operations"]]
    assert "list_http_loadbalancers" in op_names
    assert "delete_http_loadbalancer" in op_names


def test_compile_catalog_operation_fields():
    openapi = {
        "openapi": "3.0.3",
        "paths": {
            "/api/config/namespaces/{namespace}/http_loadbalancers/{name}": {
                "delete": {"operationId": "delete_lb", "responses": {}},
            },
        },
    }
    catalog = compile_catalog(openapi)
    cat = catalog["categories"][0]
    op = cat["operations"][0]
    assert op["method"] == "DELETE"
    assert op["dangerLevel"] == "high"
    assert op["path"] == "/api/config/namespaces/{namespace}/http_loadbalancers/{name}"
    assert any(p["name"] == "namespace" for p in op["parameters"])
    assert any(p["name"] == "name" for p in op["parameters"])


def test_compile_catalog_deterministic():
    openapi = {
        "openapi": "3.0.3",
        "paths": {
            "/api/config/namespaces/{namespace}/http_loadbalancers": {"get": {"responses": {}}},
            "/api/config/namespaces/{namespace}/origin_pools": {"get": {"responses": {}}},
        },
    }
    result1 = compile_catalog(openapi)
    result2 = compile_catalog(openapi)
    assert result1 == result2


def test_main_cli_writes_output_file():
    """main() reads input OpenAPI spec and writes valid api-catalog.json."""
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / "input.json"
        output_path = Path(tmpdir) / "output" / "api-catalog.json"
        spec = {
            "openapi": "3.0.3",
            "paths": {
                "/api/config/namespaces/{namespace}/widgets": {
                    "get": {"operationId": "list_widgets", "responses": {"200": {}}},
                },
            },
        }
        input_path.write_text(json.dumps(spec), encoding="utf-8")

        original_argv = sys.argv
        sys.argv = ["compile_catalog", "--input", str(input_path), "--output", str(output_path)]
        try:
            exit_code = main()
        finally:
            sys.argv = original_argv

        assert exit_code == 0
        assert output_path.exists()
        catalog = json.loads(output_path.read_text())
        assert catalog["service"] == "f5xc"
        assert len(catalog["categories"]) >= 1


def test_compile_catalog_against_real_spec():
    """compile_catalog() processes the real specs/discovered/openapi.json without error."""
    import pytest

    spec_path = Path("specs/discovered/openapi.json")
    if not spec_path.exists():
        pytest.skip("Real spec not available")
    with spec_path.open(encoding="utf-8") as f:
        openapi = json.load(f)
    catalog = compile_catalog(openapi)
    assert catalog["service"] == "f5xc"
    assert catalog["auth"]["type"] == "api_token"
    assert len(catalog["categories"]) > 0
    for cat in catalog["categories"]:
        assert "name" in cat
        assert "displayName" in cat
        for op in cat["operations"]:
            assert "name" in op
            assert "method" in op
            assert "path" in op
            assert "dangerLevel" in op
            assert "parameters" in op


def test_compile_catalog_handles_extension_fields():
    """compile_catalog() ignores OpenAPI extension fields (x-*) without crashing."""
    openapi = {
        "openapi": "3.0.3",
        "paths": {
            "/api/config/namespaces/{namespace}/widgets": {
                "get": {
                    "operationId": "list_widgets",
                    "responses": {"200": {}},
                    "x-response-time-ms": 159.81,
                },
                "x-displayname": "Widget Management",
                "x-ves-proto-service": "ves.io.schema.widget.API",
            },
        },
    }
    catalog = compile_catalog(openapi)
    assert len(catalog["categories"]) >= 1
    cat = catalog["categories"][0]
    assert len(cat["operations"]) >= 1
    assert cat["operations"][0]["name"] == "list_widgets"


def test_merge_spec_files_combines_paths_from_multiple_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        spec1 = {"openapi": "3.0.3", "paths": {"/api/widgets": {"get": {"responses": {}}}}}
        spec2 = {"openapi": "3.0.3", "paths": {"/api/gadgets": {"post": {"responses": {}}}}}
        Path(tmpdir, "widgets.json").write_text(json.dumps(spec1), encoding="utf-8")
        Path(tmpdir, "gadgets.json").write_text(json.dumps(spec2), encoding="utf-8")
        merged = merge_spec_files(Path(tmpdir))
        assert "/api/widgets" in merged["paths"]
        assert "/api/gadgets" in merged["paths"]
        assert len(merged["paths"]) == 2


def test_merge_spec_files_preserves_the_release_version(tmp_path):
    spec = {
        "openapi": "3.0.3",
        "info": {"version": "2.1.220"},
        "paths": {"/api/widgets": {"get": {"responses": {}}}},
    }
    (tmp_path / "widgets.json").write_text(json.dumps(spec), encoding="utf-8")
    second = copy.deepcopy(spec)
    second["paths"] = {"/api/other-widgets": {"get": {"responses": {}}}}
    (tmp_path / "other.json").write_text(json.dumps(second), encoding="utf-8")

    assert merge_spec_files(tmp_path)["info"]["version"] == "2.1.220"


def test_merge_spec_files_rejects_inconsistent_release_versions(tmp_path):
    for name, version in (("one.json", "2.1.219"), ("two.json", "2.1.220")):
        spec = {
            "openapi": "3.0.3",
            "info": {"version": version},
            "paths": {f"/api/{name}": {"get": {"responses": {}}}},
        }
        (tmp_path / name).write_text(json.dumps(spec), encoding="utf-8")

    with pytest.raises(ValueError, match="inconsistent versions"):
        merge_spec_files(tmp_path)


def test_merge_spec_files_skips_non_spec_files():
    with tempfile.TemporaryDirectory() as tmpdir:
        spec = {"openapi": "3.0.3", "paths": {"/api/items": {"get": {"responses": {}}}}}
        non_spec = {"metadata": {"version": "1.0"}}
        Path(tmpdir, "items.json").write_text(json.dumps(spec), encoding="utf-8")
        Path(tmpdir, "index.json").write_text(json.dumps(non_spec), encoding="utf-8")
        Path(tmpdir, "config.json").write_text(json.dumps(non_spec), encoding="utf-8")
        merged = merge_spec_files(Path(tmpdir))
        assert "/api/items" in merged["paths"]
        assert len(merged["paths"]) == 1


def test_merge_spec_files_handles_duplicate_paths():
    with tempfile.TemporaryDirectory() as tmpdir:
        spec1 = {"openapi": "3.0.3", "paths": {"/api/items": {"get": {"operationId": "list"}}}}
        spec2 = {"openapi": "3.0.3", "paths": {"/api/items": {"post": {"operationId": "create"}}}}
        Path(tmpdir, "spec1.json").write_text(json.dumps(spec1), encoding="utf-8")
        Path(tmpdir, "spec2.json").write_text(json.dumps(spec2), encoding="utf-8")
        merged = merge_spec_files(Path(tmpdir))
        assert "get" in merged["paths"]["/api/items"]
        assert "post" in merged["paths"]["/api/items"]


def test_compile_catalog_from_enriched_specs():
    enriched_dir = Path("docs/specifications/api")
    if not enriched_dir.exists():
        pytest.skip("Enriched specs not available")
    merged = merge_spec_files(enriched_dir)
    catalog = compile_catalog(merged)
    assert catalog["service"] == "f5xc"
    total_ops = sum(len(c["operations"]) for c in catalog["categories"])
    assert total_ops > 100, f"Expected >100 operations, got {total_ops}"
    assert len(catalog["categories"]) > 10


def test_compile_catalog_deduplicates_operation_names_globally():
    # /api/.../sites  -> category "sites",       op "create_site"
    # /api/.../foos   -> category "foos",         op "create_foo"  (singular strips 's')
    # Two different categories that happen to produce identical op names after singularisation
    # We use "widgets" vs "widget_things" — both produce "create_widget" when singularised.
    # Actually the cleaner way: use paths that map to different categories but the same final
    # op name.  "namespaces/{ns}/things" -> category "things", op "create_thing"
    # and "namespaces/{ns}/other_things" -> category "other-things", op "create_other_thing"
    # — those differ.  Instead, use explicitly colliding resource roots:
    # paths under different prefixes that both collapse to the same category resource name.
    # Easiest: put the same resource name under two different top-level API paths so they land
    # in different categories but produce the same op name.
    openapi = {
        "openapi": "3.0.3",
        "paths": {
            # category: "sites", op: "create_site"
            "/api/config/namespaces/{namespace}/sites": {"post": {"responses": {}}},
            # category: "sites" (same) but a different prefix segment outside namespace scope
            # To force two DIFFERENT categories with identical op names, use a path segment
            # that is not filtered by the prefix rules and lands in a different category.
            # The simplest approach: one path has extra sub-resource that changes category
            # but the resource stem is the same word.
            # "/api/ml/namespaces/{namespace}/sites" -> category "sites", op "create_site"
            # Both land in category "sites" so the per-category dedup catches them.
            # Use a path where the resource segment differs only by a trailing 's' quirk:
            # "managed_sites" singularizes to "managed_site"; category "managed-sites"
            # vs plain "sites" -> "create_site". These differ.
            # REAL collision scenario: two paths where extract_category_name gives different
            # strings but generate_operation_name gives the same string.
            # "http_loadbalancers" -> category "http-loadbalancers", op "create_http_loadbalancer"
            # "http_loadbalancer"  -> category "http-loadbalancer",  op "create_http_loadbalancer"
            "/api/config/namespaces/{namespace}/http_loadbalancers": {"post": {"responses": {}}},
            "/api/config/namespaces/{namespace}/http_loadbalancer": {"post": {"responses": {}}},
        },
    }
    catalog = compile_catalog(openapi)
    all_op_names = [op["name"] for cat in catalog["categories"] for op in cat["operations"]]
    assert len(all_op_names) == len(set(all_op_names)), f"Duplicate names found: {all_op_names}"


def test_extract_parameters_normalizes_dotted_params():
    path = "/api/config/namespaces/{metadata.namespace}/http_loadbalancers"
    params = extract_parameters(path, {})
    ns_param = next(p for p in params if p["name"] == "namespace")
    assert ns_param["default"] == "$F5XC_NAMESPACE"
    assert ns_param["in"] == "path"


def test_main_cli_with_input_dir_flag():
    with tempfile.TemporaryDirectory() as tmpdir:
        specs_dir = Path(tmpdir) / "specs"
        specs_dir.mkdir()
        output_path = Path(tmpdir) / "catalog.json"
        spec = {
            "openapi": "3.0.3",
            "paths": {
                "/api/config/namespaces/{namespace}/widgets": {"get": {"responses": {}}},
                "/api/config/namespaces/{namespace}/gadgets": {"delete": {"responses": {}}},
            },
        }
        (specs_dir / "test.json").write_text(json.dumps(spec), encoding="utf-8")
        original_argv = sys.argv
        sys.argv = ["compile_catalog", "--input-dir", str(specs_dir), "--output", str(output_path)]
        try:
            exit_code = main()
        finally:
            sys.argv = original_argv
        assert exit_code == 0
        assert output_path.exists()
        catalog = json.loads(output_path.read_text())
        assert catalog["service"] == "f5xc"
        total_ops = sum(len(c["operations"]) for c in catalog["categories"])
        assert total_ops >= 2


def test_extract_response_schema_from_200():
    operation = {
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "items": {"type": "array", "items": {"type": "string"}},
                                "errors": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["items"],
                        },
                    },
                },
            },
        },
    }
    schema = extract_response_schema(operation)
    assert schema is not None
    assert schema["type"] == "object"
    assert "items" in schema["properties"]
    assert schema["properties"]["items"]["type"] == "array"
    assert schema["required"] == ["items"]


def test_extract_response_schema_from_201_for_post():
    operation = {
        "responses": {
            "201": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "metadata": {"type": "object"},
                            },
                        },
                    },
                },
            },
        },
    }
    schema = extract_response_schema(operation)
    assert schema is not None
    assert schema["type"] == "object"


def test_extract_response_schema_simplifies_nested_refs():
    """$ref and description fields are stripped; only type/properties/required kept."""
    operation = {
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "description": "A list response",
                            "properties": {
                                "items": {
                                    "type": "array",
                                    "description": "The items",
                                    "items": {"$ref": "#/components/schemas/Item"},
                                },
                                "count": {"type": "integer", "description": "Total count"},
                            },
                            "required": ["items"],
                            "additionalProperties": True,
                        },
                    },
                },
            },
        },
    }
    schema = extract_response_schema(operation)
    assert schema is not None
    assert "description" not in schema
    assert "additionalProperties" not in schema
    assert schema["properties"]["items"]["type"] == "array"
    assert schema["properties"]["count"]["type"] == "integer"


def test_extract_response_schema_returns_none_when_missing():
    operation = {"responses": {"404": {"description": "Not found"}}}
    schema = extract_response_schema(operation)
    assert schema is None


def test_extract_response_schema_resolves_ref():
    """$ref in response schema is resolved via components."""
    components = {
        "schemas": {
            "ListResponse": {
                "type": "object",
                "properties": {
                    "items": {"type": "array"},
                    "errors": {"type": "array"},
                },
                "required": ["items"],
            },
        },
    }
    operation = {
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/ListResponse"},
                    },
                },
            },
        },
    }
    schema = extract_response_schema(operation, components)
    assert schema is not None
    assert schema["type"] == "object"
    assert "items" in schema["properties"]
    assert schema["required"] == ["items"]


def test_merge_spec_files_includes_components():
    """merge_spec_files merges components.schemas from all spec files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spec1 = {
            "openapi": "3.0.3",
            "paths": {"/api/a": {"get": {"responses": {}}}},
            "components": {"schemas": {"TypeA": {"type": "object"}}},
        }
        spec2 = {
            "openapi": "3.0.3",
            "paths": {"/api/b": {"get": {"responses": {}}}},
            "components": {"schemas": {"TypeB": {"type": "string"}}},
        }
        Path(tmpdir, "spec1.json").write_text(json.dumps(spec1), encoding="utf-8")
        Path(tmpdir, "spec2.json").write_text(json.dumps(spec2), encoding="utf-8")

        merged = merge_spec_files(Path(tmpdir))
        assert "TypeA" in merged["components"]["schemas"]
        assert "TypeB" in merged["components"]["schemas"]


# ── Bug 1: deep path hierarchy ──────────────────────────────────────────────


def test_extract_category_name_deep_path():
    path = "/api/shape/dip/namespaces/system/app_provision"
    name = extract_category_name(path)
    assert "shape" in name
    assert "app-provision" in name


def test_extract_category_name_preserves_simple_paths():
    """Simple namespace paths still work the same."""
    path = "/api/config/namespaces/{namespace}/http_loadbalancers"
    assert extract_category_name(path) == "http-loadbalancers"


# ── Bug 2: dotted placeholders normalized in path ───────────────────────────


def test_compile_catalog_normalizes_dotted_placeholders_in_path():
    openapi = {
        "openapi": "3.0.3",
        "paths": {
            "/api/config/namespaces/{metadata.namespace}/http_loadbalancers": {
                "get": {"responses": {}},
            },
        },
    }
    catalog = compile_catalog(openapi)
    op = catalog["categories"][0]["operations"][0]
    assert "{metadata.namespace}" not in op["path"]
    assert "{namespace}" in op["path"]


# ── Bug 3: bodySchema $ref resolved ─────────────────────────────────────────


def test_compile_catalog_resolves_body_schema_ref():
    openapi = {
        "openapi": "3.0.3",
        "paths": {
            "/api/items": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/CreateItem"},
                            },
                        },
                    },
                    "responses": {},
                },
            },
        },
        "components": {
            "schemas": {
                "CreateItem": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            },
        },
    }
    catalog = compile_catalog(openapi)
    op = catalog["categories"][0]["operations"][0]
    assert op.get("bodySchema") is not None
    assert "$ref" not in op["bodySchema"]
    assert op["bodySchema"]["type"] == "object"


# ── Task 2: _resolve_schema_ref ──────────────────────────────────────────────


def test_resolve_schema_ref_follows_chain():
    """Resolves a $ref to its target schema."""
    components = {
        "schemas": {
            "OuterType": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "nested": {"$ref": "#/components/schemas/InnerType"},
                },
            },
            "InnerType": {
                "type": "object",
                "properties": {"value": {"type": "integer"}},
            },
        }
    }
    from scripts.compile_catalog import _resolve_schema_ref

    result = _resolve_schema_ref({"$ref": "#/components/schemas/OuterType"}, components)
    assert result["type"] == "object"
    assert "name" in result["properties"]


def test_resolve_schema_ref_returns_original_if_no_ref():
    from scripts.compile_catalog import _resolve_schema_ref

    schema = {"type": "string", "description": "test"}
    result = _resolve_schema_ref(schema, {})
    assert result is schema


def test_resolve_schema_ref_returns_original_if_ref_not_found():
    from scripts.compile_catalog import _resolve_schema_ref

    schema = {"$ref": "#/components/schemas/Missing"}
    result = _resolve_schema_ref(schema, {"schemas": {}})
    assert result is schema


def test_resolve_schema_ref_unwraps_scalar_allof_reference():
    from scripts.compile_catalog import _resolve_schema_ref

    components = {"schemas": {"Scalar": {"type": "string", "format": "hostname"}}}
    wrapped = {
        "allOf": [{"$ref": "#/components/schemas/Scalar"}],
        "x-f5xc-wire-name": "scalar",
    }
    result = _resolve_schema_ref(wrapped, components)
    assert result["type"] == "string"
    assert result["format"] == "hostname"


# ── Task 3: minimumPayload ───────────────────────────────────────────────────


def test_build_operation_extracts_minimum_payload():
    """Operations with x-f5xc-minimum-configuration get minimumPayload."""
    from scripts.compile_catalog import _build_operation

    operation = {
        "summary": "Create a resource",
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "x-f5xc-minimum-configuration": {
                            "description": "Minimum config for test",
                            "required_fields": ["metadata", "spec"],
                            "example_json": '{"metadata": {"name": "example"}, "spec": {}}',
                        },
                    }
                }
            }
        },
    }
    result = _build_operation(
        "/api/config/namespaces/{namespace}/resources", "post", operation, "create_resource", None
    )
    assert "minimumPayload" in result
    assert result["minimumPayload"]["json"] == {"metadata": {"name": "example"}, "spec": {}}
    assert result["minimumPayload"]["requiredFields"] == ["metadata", "spec"]
    assert result["minimumPayload"]["description"] == "Minimum config for test"


def test_build_operation_skips_minimum_payload_when_absent():
    from scripts.compile_catalog import _build_operation

    operation = {"summary": "Get a resource"}
    result = _build_operation(
        "/api/config/namespaces/{namespace}/resources/{name}",
        "get",
        operation,
        "get_resource",
        None,
    )
    assert "minimumPayload" not in result


def test_build_operation_skips_minimum_payload_on_invalid_json():
    from scripts.compile_catalog import _build_operation

    operation = {
        "summary": "Create a resource",
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "x-f5xc-minimum-configuration": {
                            "description": "Bad JSON",
                            "required_fields": ["metadata"],
                            "example_json": "NOT VALID JSON {{{",
                        },
                    }
                }
            }
        },
    }
    result = _build_operation(
        "/api/config/namespaces/{namespace}/resources", "post", operation, "create_resource", None
    )
    assert "minimumPayload" not in result


# ── Fix 2: $ref wrapper extension merge ────────────────────────────────────


def test_extract_field_metadata_merges_ref_wrapper_inline_extensions():
    """Inline x-f5xc-* extensions on a $ref wrapper dict appear in extracted metadata."""
    from scripts.compile_catalog import _extract_field_metadata

    components = {
        "schemas": {
            "TlsConfig": {
                "type": "object",
                "properties": {
                    "default_security": {"type": "object"},
                },
            },
        }
    }
    schema = {
        "type": "object",
        "properties": {
            "tls_config": {
                "$ref": "#/components/schemas/TlsConfig",
                "x-f5xc-constraints": {
                    "constraintType": "object",
                    "category": "tls",
                    "metadata": {
                        "note": "Required when use_tls is selected",
                    },
                },
            },
        },
    }
    result = _extract_field_metadata(schema, components, prefix="", depth=0, max_depth=3)
    assert "tls_config" in result
    assert "constraints" in result["tls_config"]
    assert result["tls_config"]["constraints"]["category"] == "tls"


def test_extract_field_metadata_ref_without_inline_extensions_resolves_normally():
    """A $ref property with no inline extensions still resolves via the component schema."""
    from scripts.compile_catalog import _extract_field_metadata

    components = {
        "schemas": {
            "MetaType": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "x-f5xc-constraints": {"maxLength": 64},
                    },
                },
            },
        }
    }
    schema = {
        "type": "object",
        "properties": {
            "metadata": {"$ref": "#/components/schemas/MetaType"},
        },
    }
    result = _extract_field_metadata(schema, components, prefix="", depth=0, max_depth=3)
    assert "metadata.name" in result
    assert result["metadata.name"]["constraints"]["maxLength"] == 64


def test_extract_field_metadata_ref_merge_does_not_mutate_component_schema():
    """Merging inline extensions from a $ref wrapper must not mutate the component schema."""
    from scripts.compile_catalog import _extract_field_metadata

    component_schema = {
        "type": "object",
        "properties": {
            "value": {"type": "string"},
        },
    }
    components = {"schemas": {"Shared": component_schema}}
    schema = {
        "type": "object",
        "properties": {
            "field_a": {
                "$ref": "#/components/schemas/Shared",
                "x-f5xc-constraints": {"note": "only for field_a"},
            },
        },
    }
    _extract_field_metadata(schema, components, prefix="", depth=0, max_depth=3)
    assert "x-f5xc-constraints" not in component_schema


# ── Task 4: _extract_field_metadata ─────────────────────────────────────────


def test_extract_field_metadata_from_enriched_properties():
    from scripts.compile_catalog import _extract_field_metadata

    schema = {
        "type": "object",
        "required": ["name"],
        "properties": {
            "name": {
                "type": "string",
                "description": "Resource name",
                "x-f5xc-constraints": {
                    "constraintType": "string",
                    "pattern": "^[a-z0-9][-a-z0-9]*$",
                    "maxLength": 64,
                    "deterministic": True,
                },
                "x-f5xc-required-for": {
                    "minimum_config": True,
                    "create": True,
                    "update": False,
                    "read": False,
                },
            },
            "labels": {"type": "object", "description": "User labels"},
        },
    }
    result = _extract_field_metadata(schema, {}, prefix="metadata")
    assert "metadata.name" in result
    assert result["metadata.name"]["type"] == "string"
    assert result["metadata.name"]["description"] == "Resource name"
    assert result["metadata.name"]["constraints"]["pattern"] == "^[a-z0-9][-a-z0-9]*$"
    assert result["metadata.name"]["required_for"]["create"] is True
    assert "metadata.labels" not in result


def test_extract_field_metadata_resolves_refs():
    from scripts.compile_catalog import _extract_field_metadata

    components = {
        "schemas": {
            "MetaType": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "x-f5xc-constraints": {"maxLength": 64}},
                },
            },
        }
    }
    schema = {
        "type": "object",
        "properties": {"metadata": {"$ref": "#/components/schemas/MetaType"}},
    }
    result = _extract_field_metadata(schema, components, prefix="", depth=0, max_depth=3)
    assert "metadata.name" in result
    assert result["metadata.name"]["constraints"]["maxLength"] == 64


def test_extract_field_metadata_handles_circular_refs():
    from scripts.compile_catalog import _extract_field_metadata

    components = {
        "schemas": {
            "SelfRef": {
                "type": "object",
                "properties": {
                    "child": {"$ref": "#/components/schemas/SelfRef"},
                    "value": {"type": "string", "x-f5xc-constraints": {"maxLength": 10}},
                },
            },
        }
    }
    schema = {"$ref": "#/components/schemas/SelfRef"}
    result = _extract_field_metadata(schema, components, prefix="", depth=0, max_depth=3)
    assert "value" in result


def test_extract_field_metadata_respects_max_depth():
    from scripts.compile_catalog import _extract_field_metadata

    schema = {
        "type": "object",
        "properties": {
            "level1": {
                "type": "object",
                "properties": {
                    "level2": {
                        "type": "object",
                        "properties": {
                            "level3": {
                                "type": "object",
                                "properties": {
                                    "level4": {
                                        "type": "string",
                                        "x-f5xc-constraints": {"maxLength": 5},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    }
    result = _extract_field_metadata(schema, {}, prefix="", depth=0, max_depth=3)
    assert "level1.level2.level3.level4" not in result


# ── Task 5: _collect_oneof_recommendations ───────────────────────────────────


def test_collect_oneof_recommendations_from_nested_schemas():
    from scripts.compile_catalog import _collect_oneof_recommendations

    components = {
        "schemas": {
            "SpecType": {
                "type": "object",
                "x-f5xc-recommended-oneof-variant": {
                    "health_check": "http_health_check",
                    "tls_choice": "no_tls",
                },
                "properties": {"pool": {"$ref": "#/components/schemas/PoolType"}},
            },
            "PoolType": {
                "type": "object",
                "x-f5xc-recommended-oneof-variant": {"port_choice": "port"},
                "properties": {"name": {"type": "string"}},
            },
        }
    }
    root_schema = {
        "type": "object",
        "properties": {
            "metadata": {"type": "object", "properties": {"name": {"type": "string"}}},
            "spec": {"$ref": "#/components/schemas/SpecType"},
        },
    }
    result = _collect_oneof_recommendations(root_schema, components)
    assert result["spec.health_check"] == "http_health_check"
    assert result["spec.tls_choice"] == "no_tls"
    assert result["spec.pool.port_choice"] == "port"


def test_collect_oneof_recommendations_empty_when_none():
    from scripts.compile_catalog import _collect_oneof_recommendations

    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    result = _collect_oneof_recommendations(schema, {})
    assert result == {}


# ── Task 6: wire enrichment into _build_operation ───────────────────────────


def test_build_operation_includes_field_metadata():
    from scripts.compile_catalog import _build_operation

    components = {
        "schemas": {
            "CreateReq": {
                "type": "object",
                "properties": {
                    "metadata": {"$ref": "#/components/schemas/MetaType"},
                    "spec": {"type": "object", "properties": {}},
                },
            },
            "MetaType": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string", "x-f5xc-constraints": {"maxLength": 64}},
                },
            },
        }
    }
    operation = {
        "summary": "Create",
        "requestBody": {
            "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CreateReq"}}}
        },
    }
    result = _build_operation("/api/test", "post", operation, "create_test", components)
    assert "fieldMetadata" in result
    assert "metadata.name" in result["fieldMetadata"]
    assert result["fieldMetadata"]["metadata.name"]["constraints"]["maxLength"] == 64


def test_build_operation_includes_oneof_recommendations():
    from scripts.compile_catalog import _build_operation

    components = {
        "schemas": {
            "SpecType": {
                "type": "object",
                "x-f5xc-recommended-oneof-variant": {"tls_choice": "no_tls"},
                "properties": {"port": {"type": "integer"}},
            },
        }
    }
    operation = {
        "summary": "Create",
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"spec": {"$ref": "#/components/schemas/SpecType"}},
                    }
                }
            }
        },
    }
    result = _build_operation("/api/test", "post", operation, "create_test", components)
    assert "oneOfRecommendations" in result
    assert result["oneOfRecommendations"]["spec.tls_choice"] == "no_tls"


def test_build_operation_includes_response_summary():
    from scripts.compile_catalog import _build_operation

    components = {
        "schemas": {
            "ResponseType": {
                "type": "object",
                "properties": {
                    "metadata": {"type": "object", "description": "Resource identity"},
                    "spec": {"type": "object", "description": "Resource spec"},
                },
            },
        }
    }
    operation = {
        "summary": "Create",
        "responses": {
            "200": {
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/ResponseType"}}
                }
            }
        },
    }
    result = _build_operation("/api/test", "post", operation, "create_test", components)
    assert "responseSummary" in result
    fields = {f["field"] for f in result["responseSummary"]}
    assert "metadata" in fields
    assert "spec" in fields


def test_build_operation_skips_enrichment_for_get():
    from scripts.compile_catalog import _build_operation

    operation = {"summary": "List resources"}
    result = _build_operation("/api/test", "get", operation, "list_test", None)
    assert "fieldMetadata" not in result
    assert "oneOfRecommendations" not in result
    assert "minimumPayload" not in result


def test_delete_operation_has_no_minimum_payload():
    """DELETE operations should not have a minimumPayload — query params are not body fields."""
    from scripts.compile_catalog import _build_operation

    operation = {
        "parameters": [
            {"name": "name", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "namespace", "in": "query", "required": True, "schema": {"type": "string"}},
            {"name": "fail_if_referred", "in": "query", "schema": {"type": "boolean"}},
        ],
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "fail_if_referred": {"type": "boolean"},
                            "name": {"type": "string"},
                            "namespace": {"type": "string"},
                        },
                        "x-f5xc-minimum-configuration": {
                            "required_fields": ["name", "namespace"],
                            "example_json": '{"metadata": {"name": "test"}, "spec": {"name": "value"}}',
                        },
                    }
                }
            }
        },
    }
    result = _build_operation(
        path="/api/config/namespaces/{namespace}/resources/{name}",
        method="delete",
        operation=operation,
        op_name="delete_resource",
        components={},
    )
    assert "minimumPayload" not in result


def test_post_operation_still_gets_minimum_payload():
    """POST operations should still get minimumPayload as before."""
    from scripts.compile_catalog import _build_operation

    operation = {
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                        },
                        "x-f5xc-minimum-configuration": {
                            "required_fields": ["name"],
                            "example_json": '{"metadata": {"name": "test"}, "spec": {}}',
                        },
                    }
                }
            }
        },
    }
    result = _build_operation(
        path="/api/config/namespaces/{namespace}/resources",
        method="post",
        operation=operation,
        op_name="create_resource",
        components={},
    )
    assert "minimumPayload" in result


def test_operation_aliases_are_published_on_catalog_operations():
    canonical = "ves.io.schema.discovery_cloud.CustomAPI.SuggestValues"
    alternate = "ves.io.schema.discovered_service.CustomAPI.SuggestValues"
    spec = {
        "info": {"version": "1.0.0"},
        "paths": {
            "/api/discovery/namespaces/{namespace}/suggest-values": {
                "post": {
                    "operationId": canonical,
                    "x-f5xc-operation-aliases": [alternate],
                    "responses": {},
                }
            }
        },
        "components": {"schemas": {}},
    }
    catalog = compile_catalog(spec)
    category_operation = catalog["categories"][0]["operations"][0]
    assert category_operation["operationAliases"] == [alternate]
    identity = next(
        entry
        for entry in catalog["apiOperations"]
        if entry["apiIdentity"] == "ves.io.schema.discovery_cloud"
    )
    # The narrowed apiOperations receipt remains backward-compatible with strict
    # downstream parsers; the public alias belongs to the browsable operation.
    assert "operationAliases" not in identity["operations"][0]
