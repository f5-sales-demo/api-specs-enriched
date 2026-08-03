import copy

import pytest

from scripts.utils.component_canonicalization import (
    SUPPORTED_COMPONENT_CATEGORIES,
    ComponentCanonicalizationError,
    canonicalize_source_components,
    source_owner_identity,
)


def _source_id(index: int, owner: str) -> str:
    return f"docs-cloud-f5-com.{index:04d}.public.{owner}.ves-swagger.json"


def _source(
    owner: str,
    components: dict,
    *,
    path_ref: str | None = None,
) -> dict:
    document = {
        "openapi": "3.0.3",
        "info": {
            "title": f"F5 Distributed Cloud Services API for {owner}",
            "version": "fixture",
        },
        "paths": {},
        "components": components,
    }
    if path_ref is not None:
        document["paths"]["/fixture"] = {
            "get": {
                "operationId": f"{owner}.API.GetFixture",
                "responses": {"200": {"$ref": path_ref}},
            }
        }
    return document


def test_exact_duplicates_share_original_key_and_source_order_is_irrelevant():
    alpha = "ves.io.schema.alpha"
    beta = "ves.io.schema.beta"
    alpha_id = _source_id(9, alpha)
    beta_id = _source_id(2, beta)
    sources = {
        alpha_id: _source(alpha, {"schemas": {"Shared": {"type": "string"}}}),
        beta_id: _source(beta, {"schemas": {"Shared": {"type": "string"}}}),
    }
    original = copy.deepcopy(sources)

    forward = canonicalize_source_components(sources)
    reverse = canonicalize_source_components(dict(reversed(list(sources.items()))))

    assert forward.canonical_bytes() == reverse.canonical_bytes()
    assert sources == original
    assert list(forward.documents) == sorted(sources)
    assert forward.components["schemas"] == {"Shared": {"type": "string"}}
    assert forward.bindings[alpha_id]["schemas"] == {"Shared": "Shared"}
    assert forward.bindings[beta_id]["schemas"] == {"Shared": "Shared"}
    assert forward.accounting.categories["schemas"].__dict__ == {
        "occurrences": 2,
        "name_groups": 1,
        "duplicate_name_groups": 1,
        "duplicate_occurrences": 1,
        "raw_conflict_name_groups": 0,
        "raw_conflict_occurrences": 0,
        "conflict_name_groups": 0,
        "propagated_conflict_name_groups": 0,
        "distinct_contracts": 1,
        "canonical_keys": 1,
        "renamed_occurrences": 0,
        "shared_occurrences": 1,
    }


@pytest.mark.parametrize(
    ("alpha_value", "beta_value"),
    [
        (1, 2),
        (True, 1),
        ({"type": "string"}, ["type", "string"]),
        (["first", "second"], ["second", "first"]),
    ],
    ids=("scalar", "scalar-type", "object-list-type", "list-order"),
)
def test_distinct_scalar_type_and_list_contracts_receive_owner_qualified_keys(
    alpha_value,
    beta_value,
):
    alpha = "ves.io.schema.alpha"
    beta = "ves.io.schema.beta"
    alpha_id = _source_id(1, alpha)
    beta_id = _source_id(2, beta)
    result = canonicalize_source_components(
        {
            alpha_id: _source(alpha, {"schemas": {"Thing": alpha_value}}),
            beta_id: _source(beta, {"schemas": {"Thing": beta_value}}),
        }
    )

    alpha_key = f"{alpha}__Thing"
    beta_key = f"{beta}__Thing"
    assert result.bindings[alpha_id]["schemas"]["Thing"] == alpha_key
    assert result.bindings[beta_id]["schemas"]["Thing"] == beta_key
    assert result.components["schemas"] == {
        alpha_key: alpha_value,
        beta_key: beta_value,
    }
    accounting = result.accounting.categories["schemas"]
    assert accounting.raw_conflict_name_groups == 1
    assert accounting.conflict_name_groups == 1
    assert accounting.distinct_contracts == 2
    assert accounting.renamed_occurrences == 2


def test_dependency_conflicts_propagate_and_equivalent_variants_still_share():
    alpha = "ves.io.schema.alpha"
    beta = "ves.io.schema.beta"
    gamma = "ves.io.schema.gamma"
    ids = {
        owner: _source_id(index, owner) for index, owner in enumerate((alpha, beta, gamma), start=1)
    }

    def components(target_type: str) -> dict:
        return {
            "schemas": {
                "Target": {"type": target_type},
                "Wrapper": {
                    "type": "object",
                    "properties": {
                        "target": {"$ref": "#/components/schemas/Target"},
                    },
                },
            }
        }

    result = canonicalize_source_components(
        {
            ids[alpha]: _source(
                alpha,
                components("string"),
                path_ref="#/components/schemas/Wrapper",
            ),
            ids[beta]: _source(
                beta,
                components("integer"),
                path_ref="#/components/schemas/Wrapper",
            ),
            ids[gamma]: _source(
                gamma,
                components("string"),
                path_ref="#/components/schemas/Wrapper",
            ),
        }
    )

    alpha_target = f"{alpha}__Target"
    beta_target = f"{beta}__Target"
    alpha_wrapper = f"{alpha}__Wrapper"
    beta_wrapper = f"{beta}__Wrapper"
    assert result.bindings[ids[alpha]]["schemas"] == {
        "Target": alpha_target,
        "Wrapper": alpha_wrapper,
    }
    assert result.bindings[ids[gamma]]["schemas"] == {
        "Target": alpha_target,
        "Wrapper": alpha_wrapper,
    }
    assert result.bindings[ids[beta]]["schemas"] == {
        "Target": beta_target,
        "Wrapper": beta_wrapper,
    }
    assert result.components["schemas"][alpha_wrapper]["properties"]["target"] == {
        "$ref": f"#/components/schemas/{alpha_target}",
    }
    assert result.components["schemas"][beta_wrapper]["properties"]["target"] == {
        "$ref": f"#/components/schemas/{beta_target}",
    }
    assert result.documents[ids[gamma]]["paths"]["/fixture"]["get"]["responses"]["200"] == {
        "$ref": f"#/components/schemas/{alpha_wrapper}",
    }
    accounting = result.accounting.categories["schemas"]
    assert accounting.raw_conflict_name_groups == 1
    assert accounting.conflict_name_groups == 2
    assert accounting.propagated_conflict_name_groups == 1
    assert accounting.distinct_contracts == 4
    assert accounting.shared_occurrences == 2


def test_conflicts_and_reference_propagation_cover_all_supported_categories():
    alpha = "ves.io.schema.alpha"
    beta = "ves.io.schema.beta"

    def components(schema_type: str) -> dict:
        schema_ref = {"$ref": "#/components/schemas/Core"}
        return {
            "schemas": {"Core": {"type": schema_type}},
            "responses": {"Reply": {"content": {"application/json": schema_ref}}},
            "parameters": {"Filter": {"schema": schema_ref}},
            "requestBodies": {"Body": {"content": {"application/json": schema_ref}}},
        }

    source_documents = {
        _source_id(1, alpha): _source(alpha, components("string")),
        _source_id(2, beta): _source(beta, components("integer")),
    }
    for owner, source in zip((alpha, beta), source_documents.values(), strict=True):
        source["paths"]["/fixture"] = {
            "get": {
                "operationId": f"{owner}.API.GetFixture",
                "parameters": [{"$ref": "#/components/parameters/Filter"}],
                "responses": {"200": {"$ref": "#/components/responses/Reply"}},
            },
            "post": {
                "operationId": f"{owner}.API.CreateFixture",
                "requestBody": {"$ref": "#/components/requestBodies/Body"},
                "responses": {"200": {"$ref": "#/components/responses/Reply"}},
            },
        }

    result = canonicalize_source_components(source_documents)

    assert tuple(result.components) == SUPPORTED_COMPONENT_CATEGORIES
    for category in SUPPORTED_COMPONENT_CATEGORIES:
        accounting = result.accounting.categories[category]
        assert accounting.occurrences == 2
        assert accounting.name_groups == 1
        assert accounting.conflict_name_groups == 1
        assert accounting.distinct_contracts == 2
        assert accounting.canonical_keys == 2
        assert accounting.renamed_occurrences == 2
    assert result.accounting.categories["schemas"].raw_conflict_name_groups == 1
    for category in ("responses", "parameters", "requestBodies"):
        accounting = result.accounting.categories[category]
        assert accounting.raw_conflict_name_groups == 0
        assert accounting.propagated_conflict_name_groups == 1
    assert result.accounting.totals.occurrences == 8
    assert result.accounting.totals.name_groups == 4
    assert result.accounting.totals.distinct_contracts == 8
    for field in result.accounting.totals.__dict__:
        assert getattr(result.accounting.totals, field) == sum(
            getattr(accounting, field) for accounting in result.accounting.categories.values()
        )
    alpha_paths = result.documents[_source_id(1, alpha)]["paths"]["/fixture"]
    assert alpha_paths["get"]["parameters"] == [
        {"$ref": f"#/components/parameters/{alpha}__Filter"}
    ]
    assert alpha_paths["get"]["responses"]["200"] == {
        "$ref": f"#/components/responses/{alpha}__Reply"
    }
    assert alpha_paths["post"]["requestBody"] == {
        "$ref": f"#/components/requestBodies/{alpha}__Body"
    }


def test_dependency_partition_refines_through_reference_cycles():
    alpha = "ves.io.schema.alpha"
    beta = "ves.io.schema.beta"

    def components(leaf_type: str) -> dict:
        return {
            "schemas": {
                "CycleA": {"$ref": "#/components/schemas/CycleB"},
                "CycleB": {
                    "type": leaf_type,
                    "properties": {
                        "next": {"$ref": "#/components/schemas/CycleA"},
                    },
                },
            }
        }

    result = canonicalize_source_components(
        {
            _source_id(1, alpha): _source(alpha, components("string")),
            _source_id(2, beta): _source(beta, components("integer")),
        }
    )

    accounting = result.accounting.categories["schemas"]
    assert accounting.raw_conflict_name_groups == 1
    assert accounting.propagated_conflict_name_groups == 1
    assert accounting.conflict_name_groups == 2
    assert result.components["schemas"][f"{alpha}__CycleA"] == {
        "$ref": f"#/components/schemas/{alpha}__CycleB"
    }
    assert result.components["schemas"][f"{beta}__CycleB"]["properties"]["next"] == {
        "$ref": f"#/components/schemas/{beta}__CycleA"
    }


@pytest.mark.parametrize(
    ("ref", "message"),
    [
        ("#/components/schemas/Missing", "unresolved component reference"),
        ("https://example.test/schema.json", "non-local reference"),
        ("#/components/schemas/Present/type", "unsupported local component reference"),
        ("#/components/headers/Present", "unsupported component category"),
        (123, "OpenAPI \\$ref must be a string"),
    ],
)
def test_invalid_component_references_fail_closed(ref, message):
    owner = "ves.io.schema.alpha"
    source = _source(
        owner,
        {
            "schemas": {
                "Present": {"type": "string"},
                "Wrapper": {"$ref": ref},
            }
        },
    )

    with pytest.raises(ComponentCanonicalizationError, match=message):
        canonicalize_source_components({_source_id(1, owner): source})


def test_owner_identity_requires_matching_stable_filename_and_title():
    owner = "ves.io.schema.alpha"
    source = _source(owner, {"schemas": {}})
    assert source_owner_identity(_source_id(97, owner), source) == owner

    with pytest.raises(ComponentCanonicalizationError, match="no stable service owner"):
        source_owner_identity("alpha.json", source)

    with pytest.raises(ComponentCanonicalizationError, match="ambiguous owners"):
        source_owner_identity(_source_id(1, "ves.io.schema.beta"), source)


def test_duplicate_owner_identity_is_rejected_even_when_ordinals_differ():
    owner = "ves.io.schema.alpha"
    source = _source(owner, {"schemas": {}})

    with pytest.raises(ComponentCanonicalizationError, match=r"owner .* is ambiguous"):
        canonicalize_source_components(
            {
                _source_id(1, owner): source,
                _source_id(2, owner): copy.deepcopy(source),
            }
        )


def test_generated_owner_qualified_key_cannot_shadow_an_existing_component():
    alpha = "ves.io.schema.alpha"
    beta = "ves.io.schema.beta"
    generated_key = f"{alpha}__Thing"

    with pytest.raises(ComponentCanonicalizationError, match="canonical schemas key collision"):
        canonicalize_source_components(
            {
                _source_id(1, alpha): _source(
                    alpha,
                    {
                        "schemas": {
                            "Thing": {"type": "string"},
                            generated_key: {"type": "boolean"},
                        }
                    },
                ),
                _source_id(2, beta): _source(
                    beta,
                    {"schemas": {"Thing": {"type": "integer"}}},
                ),
            }
        )
