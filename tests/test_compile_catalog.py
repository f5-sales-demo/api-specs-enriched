"""Tests for scripts.compile_catalog — catalog compiler unit and integration tests."""

# pylint: disable=missing-function-docstring
import json
import sys
import tempfile
from pathlib import Path

import pytest

from scripts.compile_catalog import (
    CANONICAL_INPUT,
    assign_danger_level,
    compile_catalog,
    extract_category_name,
    extract_parameters,
    extract_response_schema,
    generate_operation_name,
    group_paths_by_resource,
    main,
    write_catalog,
)
from scripts.release.build_release_tree import canonical_commands
from scripts.release.verify_reproducible_build import build_commands


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
        generate_operation_name({"operationId": "ves.io.schema.views.http_loadbalancer.API.List"})
        == "list_views_http_loadbalancer_api"
    )


def test_generate_operation_name_get_item():
    assert (
        generate_operation_name({"operationId": "ves.io.schema.views.http_loadbalancer.API.Get"})
        == "get_views_http_loadbalancer_api"
    )


def test_generate_operation_name_post():
    assert (
        generate_operation_name({"operationId": "ves.io.schema.views.http_loadbalancer.API.Create"})
        == "create_views_http_loadbalancer_api"
    )


def test_generate_operation_name_put():
    assert (
        generate_operation_name(
            {"operationId": "ves.io.schema.views.http_loadbalancer.API.Replace"}
        )
        == "replace_views_http_loadbalancer_api"
    )


def test_generate_operation_name_patch():
    assert (
        generate_operation_name(
            {"operationId": "ves.io.schema.views.http_loadbalancer.CustomAPI.Update"}
        )
        == "update_views_http_loadbalancer_custom_api"
    )


def test_generate_operation_name_delete():
    assert (
        generate_operation_name({"operationId": "ves.io.schema.views.http_loadbalancer.API.Delete"})
        == "delete_views_http_loadbalancer_api"
    )


def test_generate_operation_name_rejects_unqualified_operation_id():
    with pytest.raises(ValueError, match="operationId is not fully qualified"):
        generate_operation_name({"operationId": "list_widgets"})


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
                "get": {
                    "operationId": (
                        "ves.io.schema.fixture.load_balancer.CustomAPI.ListLoadBalancers"
                    ),
                    "responses": {},
                },
            },
            "/api/config/namespaces/{namespace}/http_loadbalancers/{name}": {
                "delete": {
                    "operationId": (
                        "ves.io.schema.fixture.load_balancer.CustomAPI.DeleteLoadBalancer"
                    ),
                    "responses": {},
                },
            },
        },
        "components": {"schemas": {"CatalogFixture": {"type": "object"}}},
    }
    catalog = compile_catalog(openapi, "2.1.208")
    assert catalog["service"] == "f5xc"
    assert catalog["auth"]["type"] == "api_token"
    assert catalog["auth"]["headerTemplate"] == "APIToken {token}"
    assert len(catalog["categories"]) >= 1
    cat = next(c for c in catalog["categories"] if c["name"] == "http-loadbalancers")
    op_names = [op["name"] for op in cat["operations"]]
    assert op_names == [
        "list_load_balancers_fixture_load_balancer_custom_api",
        "delete_load_balancer_fixture_load_balancer_custom_api",
    ]


def test_compile_catalog_operation_fields():
    openapi = {
        "openapi": "3.0.3",
        "paths": {
            "/api/config/namespaces/{namespace}/http_loadbalancers/{name}": {
                "delete": {
                    "operationId": (
                        "ves.io.schema.fixture.load_balancer.CustomAPI.DeleteLoadBalancer"
                    ),
                    "responses": {},
                },
            },
        },
        "components": {"schemas": {"CatalogFixture": {"type": "object"}}},
    }
    catalog = compile_catalog(openapi, "2.1.208")
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
            "/api/config/namespaces/{namespace}/http_loadbalancers": {
                "get": {
                    "operationId": (
                        "ves.io.schema.fixture.load_balancer.CustomAPI.ListLoadBalancers"
                    ),
                    "responses": {},
                },
            },
            "/api/config/namespaces/{namespace}/origin_pools": {
                "get": {
                    "operationId": ("ves.io.schema.fixture.origin_pool.CustomAPI.ListOriginPools"),
                    "responses": {},
                },
            },
        },
        "components": {"schemas": {"CatalogFixture": {"type": "object"}}},
    }
    result1 = compile_catalog(openapi, "2.1.208")
    result2 = compile_catalog(openapi, "2.1.208")
    assert result1 == result2


@pytest.mark.parametrize(
    ("openapi", "message"),
    [
        (
            {
                "openapi": "3.0.3",
                "paths": {},
                "components": {"schemas": {"Fixture": {"type": "object"}}},
            },
            "paths graph is empty",
        ),
        (
            {
                "openapi": "3.0.3",
                "paths": {"/api/items": {}},
                "components": [],
            },
            "components must be an object",
        ),
        (
            {
                "openapi": "3.0.3",
                "paths": {"/api/items": {}},
                "components": {"schemas": []},
            },
            "components.schemas must be an object",
        ),
        (
            {
                "openapi": "3.0.3",
                "paths": {"/api/items": {}},
                "components": {"schemas": {}},
            },
            "components.schemas graph is empty",
        ),
    ],
)
def test_compile_catalog_rejects_malformed_or_empty_graph(openapi, message):
    with pytest.raises((TypeError, ValueError), match=message):
        compile_catalog(openapi, "2.1.208")


def test_main_cli_writes_output_file():
    """main() reads input OpenAPI spec and writes valid api-catalog.json."""
    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = Path(tmpdir) / "input.json"
        output_path = Path(tmpdir) / "output" / "api-catalog.json"
        spec = {
            "openapi": "3.0.3",
            "paths": {
                "/api/config/namespaces/{namespace}/widgets": {
                    "get": {
                        "operationId": "ves.io.schema.fixture.widget.CustomAPI.ListWidgets",
                        "responses": {"200": {}},
                    },
                },
            },
            "components": {"schemas": {"CatalogFixture": {"type": "object"}}},
        }
        input_path.write_text(json.dumps(spec), encoding="utf-8")

        original_argv = sys.argv
        sys.argv = [
            "compile_catalog",
            "--version",
            "2.1.208",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ]
        try:
            exit_code = main()
        finally:
            sys.argv = original_argv

        assert exit_code == 0
        assert output_path.exists()
        catalog = json.loads(output_path.read_text())
        assert catalog["service"] == "f5xc"
        assert len(catalog["categories"]) >= 1


def test_catalog_writer_preserves_canonical_unicode_bytes(tmp_path):
    output = tmp_path / "api-catalog.json"

    write_catalog({"displayName": "Café"}, output)

    expected = '{\n  "displayName": "Café"\n}\n'.encode()
    assert output.read_bytes() == expected


def test_compile_catalog_handles_extension_fields():
    """compile_catalog() ignores OpenAPI extension fields (x-*) without crashing."""
    openapi = {
        "openapi": "3.0.3",
        "paths": {
            "/api/config/namespaces/{namespace}/widgets": {
                "get": {
                    "operationId": "ves.io.schema.fixture.widget.CustomAPI.ListWidgets",
                    "responses": {"200": {}},
                    "x-response-time-ms": 159.81,
                },
                "x-displayname": "Widget Management",
                "x-ves-proto-service": "ves.io.schema.widget.API",
            },
        },
        "components": {"schemas": {"CatalogFixture": {"type": "object"}}},
    }
    catalog = compile_catalog(openapi, "2.1.208")
    assert len(catalog["categories"]) >= 1
    cat = catalog["categories"][0]
    assert len(cat["operations"]) >= 1
    assert cat["operations"][0]["name"] == "list_widgets_fixture_widget_custom_api"


def test_compile_catalog_from_enriched_specs():
    master_spec = Path("docs/specifications/api/openapi.json")
    if not master_spec.exists():
        pytest.skip("Enriched specs not available")
    openapi = json.loads(master_spec.read_text(encoding="utf-8"))
    catalog = compile_catalog(openapi, "2.1.208")
    assert catalog["service"] == "f5xc"
    total_ops = sum(len(c["operations"]) for c in catalog["categories"])
    assert total_ops > 100, f"Expected >100 operations, got {total_ops}"
    assert len(catalog["categories"]) > 10


def test_production_catalog_wiring_uses_only_the_canonical_master():
    assert Path("docs/specifications/api/openapi.json") == CANONICAL_INPUT
    compiler = Path("scripts/compile_catalog.py").read_text(encoding="utf-8")
    assert "merge_spec_files" not in compiler
    assert "--input-dir" not in compiler
    assert "specs/discovered" not in compiler

    expected_inputs = {
        Path("Makefile"): "--input docs/specifications/api/openapi.json",
        Path("scripts/hooks/pre-commit-pipeline.sh"): '--input "$TEMP_OUTPUT/openapi.json"',
    }
    for path, expected in expected_inputs.items():
        content = path.read_text(encoding="utf-8")
        assert expected in content
        lines = content.splitlines()
        for index, line in enumerate(lines):
            if "scripts.compile_catalog" not in line:
                continue
            compile_call = "\n".join(lines[index : index + 5])
            assert "--input-dir" not in compile_call
            assert "openapi.json" in compile_call

    producer_workflow = Path(".github/workflows/sync-and-enrich.yml").read_text(encoding="utf-8")
    assert producer_workflow.count("scripts.release.build_release_tree") == 2
    assert "scripts.compile_catalog" not in producer_workflow

    test_workflow = Path(".github/workflows/tests.yml").read_text(encoding="utf-8")
    assert "python -m scripts.release.verify_reproducible_build" in test_workflow
    candidate_root = Path("candidate")
    reproducible_commands = build_commands(
        root=candidate_root,
        input_dir=Path("specs/original"),
        version="2.1.208",
        python="python",
        biome="biome",
    )
    assert len(reproducible_commands) == 1
    assert reproducible_commands[0][:3] == (
        "python",
        "-m",
        "scripts.release.build_release_tree",
    )

    production_commands = canonical_commands(
        root=candidate_root,
        input_dir=Path("specs/original"),
        version="2.1.208",
        python="python",
        biome="biome",
    )
    catalog_commands = [
        command for command in production_commands if "scripts.compile_catalog" in command
    ]
    assert len(catalog_commands) == 1
    catalog_command = catalog_commands[0]
    input_index = catalog_command.index("--input")
    assert catalog_command[input_index + 1] == str(candidate_root / CANONICAL_INPUT)
    assert "--input-dir" not in catalog_command


def test_main_cli_rejects_removed_directory_merge_surface(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["compile_catalog", "--version", "2.1.208", "--input-dir", "specs"],
    )

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 2


def test_compile_catalog_rejects_duplicate_exact_operation_names_globally():
    openapi = {
        "openapi": "3.0.3",
        "paths": {
            "/api/config/namespaces/{namespace}/widgets": {
                "post": {
                    "operationId": "ves.io.schema.fixture.resource.CustomAPI.CreateResource",
                    "responses": {},
                },
            },
            "/api/config/namespaces/{namespace}/gadgets": {
                "post": {
                    "operationId": "ves.io.schema.fixture.resource.CustomAPI.CreateResource",
                    "responses": {},
                },
            },
        },
        "components": {"schemas": {"CatalogFixture": {"type": "object"}}},
    }

    with pytest.raises(
        ValueError,
        match="duplicate catalog operation name 'create_resource_fixture_resource_custom_api'",
    ):
        compile_catalog(openapi, "2.1.208")


def test_extract_parameters_normalizes_dotted_params():
    path = "/api/config/namespaces/{metadata.namespace}/http_loadbalancers"
    params = extract_parameters(path, {})
    ns_param = next(p for p in params if p["name"] == "namespace")
    assert ns_param["default"] == "$F5XC_NAMESPACE"
    assert ns_param["in"] == "path"


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


def test_extract_response_schema_rejects_missing_response_ref():
    operation = {
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/MissingResponse"},
                    },
                },
            },
        },
    }

    with pytest.raises(ValueError, match="unresolved schema reference"):
        extract_response_schema(operation, {"schemas": {}})


def test_extract_response_schema_rejects_missing_nested_property_ref():
    operation = {
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "item": {"$ref": "#/components/schemas/MissingItem"},
                            },
                        },
                    },
                },
            },
        },
    }

    with pytest.raises(ValueError, match="unresolved schema reference"):
        extract_response_schema(operation, {"schemas": {}})


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
                "get": {
                    "operationId": (
                        "ves.io.schema.fixture.load_balancer.CustomAPI.ListLoadBalancers"
                    ),
                    "responses": {},
                },
            },
        },
        "components": {"schemas": {"CatalogFixture": {"type": "object"}}},
    }
    catalog = compile_catalog(openapi, "2.1.208")
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
                    "operationId": "ves.io.schema.fixture.item.CustomAPI.CreateItem",
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
    catalog = compile_catalog(openapi, "2.1.208")
    op = catalog["categories"][0]["operations"][0]
    assert op.get("bodySchema") is not None
    assert "$ref" not in op["bodySchema"]
    assert op["bodySchema"]["type"] == "object"


def test_resolve_body_schema_rejects_missing_request_ref():
    from scripts.compile_catalog import _resolve_body_schema

    operation = {
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/MissingRequest"},
                },
            },
        },
    }

    with pytest.raises(ValueError, match="unresolved schema reference"):
        _resolve_body_schema(operation, {"schemas": {}})


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


def test_resolve_schema_ref_follows_recursive_single_ref_allof_wrappers():
    from scripts.compile_catalog import _resolve_schema_ref

    terminal = {"type": "object", "properties": {"enabled": {"type": "boolean"}}}
    components = {
        "schemas": {
            "Outer": {"allOf": [{"$ref": "#/components/schemas/Middle"}]},
            "Middle": {"$ref": "#/components/schemas/Terminal"},
            "Terminal": terminal,
        },
    }

    result = _resolve_schema_ref(
        {"allOf": [{"$ref": "#/components/schemas/Outer"}]},
        components,
    )

    assert result is terminal


@pytest.mark.parametrize(
    "schema",
    [
        {"allOf": []},
        {
            "allOf": [
                {"$ref": "#/components/schemas/Terminal"},
                {"$ref": "#/components/schemas/Terminal"},
            ],
        },
        {"allOf": [{"type": "object"}]},
        {
            "allOf": [
                {
                    "$ref": "#/components/schemas/Terminal",
                    "description": "ambiguous reference member",
                },
            ],
        },
        {
            "$ref": "#/components/schemas/Terminal",
            "allOf": [{"$ref": "#/components/schemas/Terminal"}],
        },
    ],
)
def test_resolve_schema_ref_rejects_ambiguous_or_malformed_allof(schema):
    from scripts.compile_catalog import _resolve_schema_ref

    components = {"schemas": {"Terminal": {"type": "object"}}}
    with pytest.raises(ValueError, match="reference wrapper"):
        _resolve_schema_ref(schema, components)


def test_resolve_schema_ref_rejects_reference_cycles():
    from scripts.compile_catalog import _resolve_schema_ref

    components = {
        "schemas": {
            "First": {"$ref": "#/components/schemas/Second"},
            "Second": {"allOf": [{"$ref": "#/components/schemas/First"}]},
        },
    }

    with pytest.raises(ValueError, match="cyclic schema reference"):
        _resolve_schema_ref({"$ref": "#/components/schemas/First"}, components)


@pytest.mark.parametrize(
    "schema",
    [
        {
            "allOf": [{"$ref": "#/components/schemas/Terminal"}],
            "type": "array",
        },
        {
            "allOf": [{"$ref": "#/components/schemas/Terminal"}],
            "properties": {"ignored": {"type": "string"}},
        },
        {
            "$ref": "#/components/schemas/Terminal",
            "items": {"type": "string"},
        },
    ],
)
def test_resolve_schema_ref_rejects_structural_wrapper_siblings(schema):
    from scripts.compile_catalog import _resolve_schema_ref

    components = {"schemas": {"Terminal": {"type": "object"}}}
    with pytest.raises(ValueError, match="unsupported structural sibling"):
        _resolve_schema_ref(schema, components)


@pytest.mark.parametrize(
    ("target_assertion", "wrapper_assertion"),
    [
        ({"maximum": 5}, {"maximum": 10}),
        ({"enum": [1, 2, 3, 4, 5]}, {"enum": [4, 5, 6]}),
        ({"pattern": "^[a-z]+$"}, {"pattern": "^[a-z0-9]+$"}),
    ],
)
def test_resolve_schema_ref_rejects_conflicting_assertion_siblings(
    target_assertion,
    wrapper_assertion,
):
    from scripts.compile_catalog import _resolve_schema_ref

    components = {
        "schemas": {"Terminal": {"type": "integer", **target_assertion}},
    }
    schema = {
        "allOf": [{"$ref": "#/components/schemas/Terminal"}],
        **wrapper_assertion,
    }

    with pytest.raises(ValueError, match="conflicting assertion"):
        _resolve_schema_ref(schema, components)


def test_resolve_schema_ref_returns_original_if_no_ref():
    from scripts.compile_catalog import _resolve_schema_ref

    schema = {"type": "string", "description": "test"}
    result = _resolve_schema_ref(schema, {})
    assert result is schema


def test_resolve_schema_ref_rejects_missing_target():
    from scripts.compile_catalog import _resolve_schema_ref

    schema = {"$ref": "#/components/schemas/Missing"}
    with pytest.raises(ValueError, match="unresolved schema reference"):
        _resolve_schema_ref(schema, {"schemas": {}})


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


def test_build_operation_rejects_invalid_minimum_payload_json():
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
    with pytest.raises(ValueError, match="malformed minimum-configuration example_json"):
        _build_operation(
            "/api/config/namespaces/{namespace}/resources",
            "post",
            operation,
            "create_resource",
            None,
        )


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


def test_extract_field_metadata_projects_defaults_from_allof_wrappers():
    from scripts.compile_catalog import _extract_field_metadata

    components = {
        "schemas": {
            "Toggle": {"type": "boolean"},
            "Mode": {"type": "string"},
        },
    }
    schema = {
        "type": "object",
        "properties": {
            "enabled": {
                "allOf": [{"$ref": "#/components/schemas/Toggle"}],
                "default": False,
                "x-f5xc-server-default": True,
            },
            "mode": {
                "allOf": [{"$ref": "#/components/schemas/Mode"}],
                "x-f5xc-server-default-value": {"type": "null", "value": None},
                "x-f5xc-server-default": True,
            },
        },
    }

    result = _extract_field_metadata(schema, components)

    assert result["enabled"]["serverDefault"] is True
    assert result["enabled"]["default"] is False
    assert result["mode"]["serverDefault"] is True
    assert "default" in result["mode"]
    assert result["mode"]["default"] is None


def test_extract_field_metadata_preserves_intermediate_wrapper_annotations():
    from scripts.compile_catalog import _extract_field_metadata

    components = {
        "schemas": {
            "Outer": {"$ref": "#/components/schemas/Intermediate"},
            "Intermediate": {
                "allOf": [{"$ref": "#/components/schemas/Terminal"}],
                "default": 0,
                "x-f5xc-server-default": True,
            },
            "Terminal": {"type": "integer"},
        },
    }
    schema = {
        "type": "object",
        "properties": {"count": {"$ref": "#/components/schemas/Outer"}},
    }

    result = _extract_field_metadata(schema, components)

    assert result["count"]["serverDefault"] is True
    assert result["count"]["default"] == 0


def test_extract_field_metadata_rejects_malformed_typed_server_default():
    from scripts.compile_catalog import _extract_field_metadata

    schema = {
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "x-f5xc-server-default": True,
                "x-f5xc-server-default-value": {"type": "string", "value": ""},
            },
        },
    }

    with pytest.raises(ValueError, match="malformed x-f5xc-server-default-value"):
        _extract_field_metadata(schema, {})


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


def test_canonical_catalog_projects_measured_server_defaults():
    openapi = json.loads(CANONICAL_INPUT.read_text(encoding="utf-8"))
    catalog = compile_catalog(openapi, "2.1.208")

    affected_operations: set[str] = set()
    server_defaults: list[dict] = []
    for category in catalog["categories"]:
        for operation in category["operations"]:
            operation_defaults = [
                metadata
                for metadata in operation.get("fieldMetadata", {}).values()
                if metadata.get("serverDefault") is True
            ]
            if operation_defaults:
                affected_operations.add(operation["name"])
                server_defaults.extend(operation_defaults)

    assert len(affected_operations) >= 43
    assert len(server_defaults) >= 170
    assert all("default" in metadata for metadata in server_defaults)
    assert sum(metadata["default"] is None for metadata in server_defaults) >= 6
