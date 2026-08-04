"""Tests for the apiOperations / apiExclusions sections of api-catalog.json.

The catalog publishes, for every ``ves.io.schema.*`` identity, the exact operations
the API exposes: method, path, operationId and surface. Consumers read those
verbatim instead of inferring a method and path from an operation name.

Publishing the identity explicitly is the whole point. Deriving it by parsing an
operationId looks trivial and is not: the specifications use 58 distinct RPC
container names, and four of them spell it ``Api`` rather than ``API``
(UpgradeStatusCustomApi, SoftwareVersionOsImageCustomApi,
WafSignatureChangelogCustomApi, SignatureCustomApi). A consumer that splits on a
literal ``.API.`` silently loses seven operations across three identities, and a
consumer that handles only ``API``/``CustomAPI`` loses more. Every case below that
looks redundant is guarding one of those real spellings.

See f5-sales-demo/api-specs-enriched#1321 and
f5-sales-demo/terraform-provider-xcsh#1460.
"""

# pylint: disable=missing-function-docstring
import json

import pytest

from scripts.compile_catalog import (
    build_api_exclusions,
    build_api_operations,
    compile_catalog,
    extract_api_identity,
    surface_from_path,
)


def _operation(operation_id, request_ref=None):
    operation = {"operationId": operation_id, "responses": {"200": {"description": "ok"}}}
    if request_ref is not None:
        operation["requestBody"] = {
            "content": {"application/json": {"schema": {"$ref": request_ref}}},
        }
    return operation


# --------------------------------------------------------------------------- #
# Identity extraction
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("operation_id", "identity", "container", "rpc"),
    [
        (
            "ves.io.schema.namespace.API.Create",
            "ves.io.schema.namespace",
            "API",
            "Create",
        ),
        (
            "ves.io.schema.api_sec.api_crawler.API.Create",
            "ves.io.schema.api_sec.api_crawler",
            "API",
            "Create",
        ),
        (
            "ves.io.schema.views.http_loadbalancer.CustomAPI.Delete",
            "ves.io.schema.views.http_loadbalancer",
            "CustomAPI",
            "Delete",
        ),
        # Lowercase 'pi'. Four containers in the published specs spell it this way;
        # a consumer matching a literal "API" token drops them.
        (
            "ves.io.schema.upgrade_status.UpgradeStatusCustomApi.PreUpgradeCheck",
            "ves.io.schema.upgrade_status",
            "UpgradeStatusCustomApi",
            "PreUpgradeCheck",
        ),
        (
            "ves.io.schema.virtual_appliance.SoftwareVersionOsImageCustomApi.GetImage",
            "ves.io.schema.virtual_appliance",
            "SoftwareVersionOsImageCustomApi",
            "GetImage",
        ),
        # Container names that contain no "API"/"Api" substring at all.
        (
            "ves.io.schema.sahaya.SahayaAPI.List",
            "ves.io.schema.sahaya",
            "SahayaAPI",
            "List",
        ),
        (
            "ves.io.schema.uam.UamKubeConfigAPI.Get",
            "ves.io.schema.uam",
            "UamKubeConfigAPI",
            "Get",
        ),
    ],
)
def test_extract_api_identity_handles_every_container_spelling(
    operation_id, identity, container, rpc
):
    assert extract_api_identity(operation_id) == (identity, container, rpc)


@pytest.mark.parametrize(
    "operation_id",
    [
        "",
        "not.an.identity",
        "ves.io.schema.namespace",  # no RPC container
        "ves.io.schema.namespace.API",  # container but no method
        "ves.io.notschema.thing.API.Create",  # wrong root
        "ves.io.schema.Namespace.API.Create",  # identity segments are lowercase
    ],
)
def test_extract_api_identity_rejects_malformed_ids(operation_id):
    assert extract_api_identity(operation_id) is None


# --------------------------------------------------------------------------- #
# Surface
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("path", "surface"),
    [
        ("/api/config/namespaces/{namespace}/api_crawlers", "config"),
        ("/api/web/namespaces/{namespace}/api_credentials", "web"),
        ("/api/maurice/upgradable_sw_versions", "maurice"),
        ("/api/ml/data/namespaces/{namespace}/staged", "ml"),
        ("/api/data-intelligence/namespaces/{namespace}/x", "data-intelligence"),
    ],
)
def test_surface_from_path(path, surface):
    assert surface_from_path(path) == surface


def test_surface_from_path_rejects_paths_without_a_surface():
    for path in ("/", "/api", "/api/"):
        with pytest.raises(ValueError, match="does not carry an API surface"):
            surface_from_path(path)


def test_surface_from_path_handles_roots_outside_api():
    """Not every published path sits under /api/ — /no_auth/... is a real one."""
    assert surface_from_path("/no_auth/namespaces/system/billing/plan_transition") == "no_auth"


# --------------------------------------------------------------------------- #
# apiOperations
# --------------------------------------------------------------------------- #


def _sample_paths():
    return {
        "/api/config/namespaces/{namespace}/api_crawlers": {
            "post": _operation(
                "ves.io.schema.api_sec.api_crawler.API.Create",
                "#/components/schemas/api_crawlerCreateRequest",
            ),
            "get": _operation("ves.io.schema.api_sec.api_crawler.API.List"),
        },
        "/api/web/namespaces/{namespace}/alerts": {
            "get": _operation("ves.io.schema.alert.API.List"),
        },
        "/api/maurice/upgradable_sw_versions": {
            "get": _operation(
                "ves.io.schema.upgrade_status.UpgradeStatusCustomApi.GetUpgradableSWVersions"
            ),
        },
    }


def test_groups_operations_by_identity_in_sorted_order():
    grouped = build_api_operations(_sample_paths())
    identities = [entry["apiIdentity"] for entry in grouped]
    assert identities == sorted(identities)
    assert identities == [
        "ves.io.schema.alert",
        "ves.io.schema.api_sec.api_crawler",
        "ves.io.schema.upgrade_status",
    ]
    assert len(identities) == len(set(identities)), "an identity appears twice"


def test_each_operation_carries_an_explicit_method_path_and_surface():
    grouped = build_api_operations(_sample_paths())
    crawler = next(e for e in grouped if e["apiIdentity"] == "ves.io.schema.api_sec.api_crawler")
    assert [(o["method"], o["path"]) for o in crawler["operations"]] == [
        ("GET", "/api/config/namespaces/{namespace}/api_crawlers"),
        ("POST", "/api/config/namespaces/{namespace}/api_crawlers"),
    ]
    for operation in crawler["operations"]:
        assert operation["method"] == operation["method"].upper()
        assert operation["path"].startswith("/")
        assert operation["surface"] == "config"
        assert operation["operationId"]


def test_operations_are_sorted_by_method_then_path():
    paths = {
        "/api/config/b": {"post": _operation("ves.io.schema.thing.API.CreateSecond")},
        "/api/config/a": {
            "post": _operation("ves.io.schema.thing.API.CreateFirst"),
            "get": _operation("ves.io.schema.thing.API.GetFirst"),
        },
    }
    operations = build_api_operations(paths)[0]["operations"]
    assert [(o["method"], o["path"]) for o in operations] == [
        ("GET", "/api/config/a"),
        ("POST", "/api/config/a"),
        ("POST", "/api/config/b"),
    ]


def test_request_schema_is_recorded_only_when_the_operation_takes_a_body():
    grouped = build_api_operations(_sample_paths())
    crawler = next(e for e in grouped if e["apiIdentity"] == "ves.io.schema.api_sec.api_crawler")
    post = next(o for o in crawler["operations"] if o["method"] == "POST")
    get = next(o for o in crawler["operations"] if o["method"] == "GET")
    assert post["requestSchema"] == "api_crawlerCreateRequest"
    assert "requestSchema" not in get


def test_duplicate_operation_ids_are_rejected():
    """One operationId must identify one operation, or a consumer cannot key on it."""
    paths = {
        "/api/config/a": {"post": _operation("ves.io.schema.thing.API.Create")},
        "/api/config/b": {"post": _operation("ves.io.schema.thing.API.Create")},
    }
    with pytest.raises(ValueError, match="duplicate operationId"):
        build_api_operations(paths)


def test_malformed_schema_qualified_operation_id_is_rejected_rather_than_dropped():
    """This is the casing-and-format bug class the inventory exists to eliminate.

    An id that claims to be schema-qualified and does not parse must fail loudly:
    skipping it is how an identity disappears from a contract that still looks
    complete.
    """
    paths = {"/api/config/a": {"post": _operation("ves.io.schema.thing.lowercase.container")}}
    with pytest.raises(ValueError, match="does not parse"):
        build_api_operations(paths)


def test_operation_ids_that_are_not_f5_schema_apis_are_not_inventoried():
    """A hand-written or fixture endpoint has no ves.io.schema identity to publish.

    It is omitted rather than rejected — the section inventories F5 schema APIs, and
    imposing that format on every other operation would be a contract this
    compiler has no business enforcing.
    """
    paths = {
        "/api/config/a": {"post": _operation("delete_lb")},
        "/api/config/b": {"post": _operation("ves.io.schema.thing.API.Create")},
    }
    grouped = build_api_operations(paths)
    assert [entry["apiIdentity"] for entry in grouped] == ["ves.io.schema.thing"]


def test_non_operation_keys_are_ignored():
    """`parameters` and `$ref` sit beside methods in a path item and are not operations."""
    paths = {
        "/api/config/a": {
            "parameters": [{"name": "x", "in": "query"}],
            "post": _operation("ves.io.schema.thing.API.Create"),
        },
    }
    grouped = build_api_operations(paths)
    assert len(grouped) == 1
    assert len(grouped[0]["operations"]) == 1


def test_output_is_deterministic():
    first = json.dumps(build_api_operations(_sample_paths()), sort_keys=False)
    second = json.dumps(build_api_operations(_sample_paths()), sort_keys=False)
    assert first == second


# --------------------------------------------------------------------------- #
# apiExclusions
# --------------------------------------------------------------------------- #


def test_exclusions_are_empty_without_a_configuration(tmp_path):
    """Absent configuration means nothing is deliberately withheld — not unknown."""
    assert build_api_exclusions(tmp_path / "missing.yaml", {"ves.io.schema.thing"}) == []


def test_exclusions_are_read_from_configuration(tmp_path):
    config = tmp_path / "api_exclusions.yaml"
    config.write_text(
        "exclusions:\n"
        "  - apiIdentity: ves.io.schema.internal_thing\n"
        "    classification: internal\n"
        "    reason: Internal control-plane API, not tenant-facing\n",
    )
    exclusions = build_api_exclusions(config, {"ves.io.schema.thing"})
    assert exclusions == [
        {
            "apiIdentity": "ves.io.schema.internal_thing",
            "classification": "internal",
            "reason": "Internal control-plane API, not tenant-facing",
        },
    ]


def test_exclusion_that_is_also_published_is_rejected(tmp_path):
    """The two sections must partition identities, or 'excluded' means nothing."""
    config = tmp_path / "api_exclusions.yaml"
    config.write_text(
        "exclusions:\n"
        "  - apiIdentity: ves.io.schema.thing\n"
        "    classification: internal\n"
        "    reason: contradictory\n",
    )
    with pytest.raises(ValueError, match="both published and excluded"):
        build_api_exclusions(config, {"ves.io.schema.thing"})


def test_exclusions_require_a_classification_and_reason(tmp_path):
    config = tmp_path / "api_exclusions.yaml"
    config.write_text("exclusions:\n  - apiIdentity: ves.io.schema.internal_thing\n")
    with pytest.raises(ValueError, match="classification"):
        build_api_exclusions(config, set())


def test_exclusions_are_sorted_and_deduplicated(tmp_path):
    config = tmp_path / "api_exclusions.yaml"
    config.write_text(
        "exclusions:\n"
        "  - apiIdentity: ves.io.schema.zzz\n"
        "    classification: internal\n"
        "    reason: z\n"
        "  - apiIdentity: ves.io.schema.aaa\n"
        "    classification: internal\n"
        "    reason: a\n",
    )
    exclusions = build_api_exclusions(config, set())
    assert [e["apiIdentity"] for e in exclusions] == ["ves.io.schema.aaa", "ves.io.schema.zzz"]

    config.write_text(
        "exclusions:\n"
        "  - apiIdentity: ves.io.schema.aaa\n"
        "    classification: internal\n"
        "    reason: a\n"
        "  - apiIdentity: ves.io.schema.aaa\n"
        "    classification: internal\n"
        "    reason: again\n",
    )
    with pytest.raises(ValueError, match="duplicate"):
        build_api_exclusions(config, set())


# --------------------------------------------------------------------------- #
# Catalog integration
# --------------------------------------------------------------------------- #


def test_catalog_publishes_both_sections_and_they_partition_identities():
    catalog = compile_catalog({"paths": _sample_paths(), "components": {"schemas": {}}})
    assert "apiOperations" in catalog
    assert "apiExclusions" in catalog

    published = {entry["apiIdentity"] for entry in catalog["apiOperations"]}
    excluded = {entry["apiIdentity"] for entry in catalog["apiExclusions"]}
    assert published == {
        "ves.io.schema.alert",
        "ves.io.schema.api_sec.api_crawler",
        "ves.io.schema.upgrade_status",
    }
    assert not published & excluded, "an identity is both published and excluded"


def test_catalog_sections_are_byte_identical_across_runs():
    spec = {"paths": _sample_paths(), "components": {"schemas": {}}}
    first = compile_catalog(spec)
    second = compile_catalog(spec)
    assert json.dumps(first["apiOperations"]) == json.dumps(second["apiOperations"])
    assert json.dumps(first["apiExclusions"]) == json.dumps(second["apiExclusions"])


# --------------------------------------------------------------------------- #
# Path collisions
# --------------------------------------------------------------------------- #
#
# Two source specifications can claim the same path and method with different
# operationIds. Merging is last-writer-wins by filename sort order, so one identity
# used to disappear from the catalog on alphabetical accident alone — measured:
# ves.io.schema.discovery_cloud lost /api/discovery/namespaces/{namespace}/
# suggest-values to ves.io.schema.discovered_service, leaving 282 of 283 identities.
#
# A silent loss is the one outcome this section exists to prevent, so the loser is
# published as an exclusion instead: still absent from apiOperations, but now stated,
# with the winner named.


def _spec_file(tmp_path, name, path, method, operation_id):
    spec = {"openapi": "3.0.3", "paths": {path: {method: _operation(operation_id)}}}
    (tmp_path / name).write_text(json.dumps(spec))


def test_merge_records_a_collision_when_operation_ids_differ(tmp_path):
    from scripts.compile_catalog import merge_spec_files

    _spec_file(tmp_path, "aaa.json", "/api/x/y", "post", "ves.io.schema.first.CustomAPI.Do")
    _spec_file(tmp_path, "zzz.json", "/api/x/y", "post", "ves.io.schema.second.CustomAPI.Do")

    merged = merge_spec_files(tmp_path)
    collisions = merged["x-f5xc-path-collisions"]
    assert len(collisions) == 1
    collision = collisions[0]
    # Later filename wins the merge, so the earlier one is the loser.
    assert collision["losingOperationId"] == "ves.io.schema.first.CustomAPI.Do"
    assert collision["winningOperationId"] == "ves.io.schema.second.CustomAPI.Do"
    assert collision["path"] == "/api/x/y"
    assert collision["method"] == "POST"


def test_merge_records_no_collision_for_an_identical_duplicate(tmp_path):
    """The same operation declared twice is harmless — nothing is lost."""
    from scripts.compile_catalog import merge_spec_files

    _spec_file(tmp_path, "aaa.json", "/api/x/y", "post", "ves.io.schema.same.CustomAPI.Do")
    _spec_file(tmp_path, "zzz.json", "/api/x/y", "post", "ves.io.schema.same.CustomAPI.Do")

    assert merge_spec_files(tmp_path).get("x-f5xc-path-collisions", []) == []


def test_collision_loser_is_published_as_an_exclusion(tmp_path):
    from scripts.compile_catalog import merge_spec_files

    _spec_file(tmp_path, "aaa.json", "/api/x/y", "post", "ves.io.schema.first.CustomAPI.Do")
    _spec_file(tmp_path, "zzz.json", "/api/x/y", "post", "ves.io.schema.second.CustomAPI.Do")

    catalog = compile_catalog(merge_spec_files(tmp_path))
    published = {e["apiIdentity"] for e in catalog["apiOperations"]}
    excluded = {e["apiIdentity"] for e in catalog["apiExclusions"]}

    assert published == {"ves.io.schema.second"}
    assert excluded == {"ves.io.schema.first"}
    assert not published & excluded

    entry = catalog["apiExclusions"][0]
    assert entry["classification"] == "path-collision"
    # The reason has to name the winner, or the exclusion is unactionable.
    assert "ves.io.schema.second" in entry["reason"]
    assert "/api/x/y" in entry["reason"]


def test_collision_loser_that_also_wins_elsewhere_stays_published(tmp_path):
    """An identity losing one path but owning another is not excluded.

    Excluding it would claim the whole API is unavailable when only one operation
    was displaced.
    """
    from scripts.compile_catalog import merge_spec_files

    _spec_file(tmp_path, "aaa.json", "/api/x/y", "post", "ves.io.schema.first.CustomAPI.Do")
    _spec_file(tmp_path, "bbb.json", "/api/x/other", "get", "ves.io.schema.first.CustomAPI.List")
    _spec_file(tmp_path, "zzz.json", "/api/x/y", "post", "ves.io.schema.second.CustomAPI.Do")

    catalog = compile_catalog(merge_spec_files(tmp_path))
    published = {e["apiIdentity"] for e in catalog["apiOperations"]}
    excluded = {e["apiIdentity"] for e in catalog["apiExclusions"]}
    assert published == {"ves.io.schema.first", "ves.io.schema.second"}
    assert excluded == set()
