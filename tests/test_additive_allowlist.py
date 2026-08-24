# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Unit tests for the additive allowlist classifier (spec §4.3)."""

import pytest

from scripts.utils.additive_allowlist import is_additive_change


def t(pointer):
    return pointer


def test_added_x_extension_is_additive():
    assert is_additive_change(
        "dictionary_item_added",
        t("root['paths']['/x']['get']['x-f5xc-cli-help']"),
    )


def test_added_description_is_additive():
    assert is_additive_change(
        "dictionary_item_added",
        t("root['components']['schemas']['Foo']['properties']['bar']['description']"),
    )


def test_rewritten_description_is_additive():
    assert is_additive_change(
        "values_changed",
        t("root['paths']['/x']['get']['description']"),
    )


def test_rewritten_summary_is_additive():
    assert is_additive_change(
        "values_changed",
        t("root['paths']['/x']['get']['summary']"),
    )


def test_rewritten_example_is_additive():
    assert is_additive_change(
        "values_changed",
        t("root['components']['schemas']['Foo']['example']"),
    )


def test_values_changed_inside_x_extension_is_additive():
    assert is_additive_change(
        "values_changed",
        t("root['paths']['/x']['get']['x-f5xc-cli-help']['hint']"),
    )


def test_appended_tag_is_additive():
    assert is_additive_change(
        "iterable_item_added",
        t("root['paths']['/x']['get']['tags'][2]"),
    )


def test_added_to_required_is_not_additive():
    assert not is_additive_change(
        "iterable_item_added",
        t("root['components']['schemas']['Foo']['required'][1]"),
    )


def test_added_to_enum_is_additive():
    """New enum values from upstream duplicate files are additive."""
    assert is_additive_change(
        "iterable_item_added",
        t("root['components']['schemas']['Foo']['enum'][2]"),
    )


def test_removed_key_is_not_additive():
    assert not is_additive_change(
        "dictionary_item_removed",
        t("root['paths']['/x']['get']['parameters']"),
    )


def test_appended_server_is_additive():
    """servers is one of the allowlisted array parents (spec 4.3)."""
    assert is_additive_change("iterable_item_added", t("root['servers'][1]"))


def test_appended_examples_is_additive():
    """examples is one of the allowlisted array parents (spec 4.3)."""
    assert is_additive_change(
        "iterable_item_added",
        t("root['components']['schemas']['Foo']['examples'][0]"),
    )


def test_added_summary_is_additive():
    """Free-text non-description keys are also covered by the allowlist."""
    assert is_additive_change("dictionary_item_added", t("root['paths']['/x']['get']['summary']"))


def test_nested_tag_array_is_not_additive():
    """Rule applies only when the direct parent is an allowlisted array."""
    assert not is_additive_change(
        "iterable_item_added",
        t("root['paths']['/x']['get']['tags'][2][0]"),
    )


def test_changed_type_is_not_additive():
    assert not is_additive_change(
        "type_changes",
        t("root['components']['schemas']['Foo']['properties']['bar']['type']"),
    )


@pytest.mark.parametrize(
    ("constraint", "before", "after", "expected"),
    [
        ("minLength", 4, 3, True),
        ("minLength", 4, 5, False),
        ("minItems", 2, 1, True),
        ("minItems", 2, 3, False),
        ("minimum", 1, 0, True),
        ("minimum", 1, 2, False),
        ("maxLength", 4, 5, True),
        ("maxLength", 4, 3, False),
        ("maxItems", 2, 3, True),
        ("maxItems", 2, 1, False),
        ("maximum", 1, 2, True),
        ("maximum", 1, 0, False),
        ("maximum", 1, "2", False),
        ("minimum", "1", 0, False),
    ],
)
def test_only_numeric_constraint_widening_is_additive(
    constraint: str, before: object, after: object, expected: bool
) -> None:
    assert (
        is_additive_change(
            "values_changed",
            t(f"root['components']['schemas']['Foo']['properties']['bar']['{constraint}']"),
            before,
            after,
        )
        is expected
    )


@pytest.mark.parametrize("member", ["$ref", "operationId"])
def test_merge_order_contract_changes_are_not_additive(member: str) -> None:
    assert not is_additive_change(
        "values_changed",
        t(f"root['components']['schemas']['Foo']['properties']['bar']['{member}']"),
        "before",
        "after",
    )


# Rule 1 — error-response type additions (family 5)


def test_error_response_type_add_is_additive():
    pointer = (
        "root['paths']['/x']['get']['responses']['401']"
        "['content']['application/json']['schema']['type']"
    )
    assert is_additive_change("dictionary_item_added", pointer, None, "string")


def test_error_response_type_add_on_2xx_is_not_additive():
    pointer = (
        "root['paths']['/x']['get']['responses']['200']"
        "['content']['application/json']['schema']['type']"
    )
    assert not is_additive_change("dictionary_item_added", pointer, None, "string")


# Rule 2 — positive-int maxLength additions (family 6)


def test_positive_int_max_length_add_tightens_contract():
    pointer = "root['components']['schemas']['Foo']['properties']['bar']['maxLength']"
    assert not is_additive_change("dictionary_item_added", pointer, None, 128)


def test_zero_max_length_add_tightens_contract():
    """maxLength: 0 is a valid server-discovered constraint (e.g. bot_defense continue fields)."""
    pointer = "root['components']['schemas']['Foo']['properties']['bar']['maxLength']"
    assert not is_additive_change("dictionary_item_added", pointer, None, 0)


def test_non_int_max_length_add_is_not_additive():
    pointer = "root['components']['schemas']['Foo']['properties']['bar']['maxLength']"
    assert not is_additive_change("dictionary_item_added", pointer, None, "128")


# Rule 7 — additive constraint additions


def test_minlength_add_tightens_contract():
    pointer = "root['components']['schemas']['Foo']['properties']['bar']['minLength']"
    assert not is_additive_change("dictionary_item_added", pointer, None, 17)


def test_minlength_zero_add_tightens_contract():
    pointer = "root['components']['schemas']['Foo']['properties']['bar']['minLength']"
    assert not is_additive_change("dictionary_item_added", pointer, None, 0)


# Rule 8 — server-discovered default values


def test_default_add_is_additive():
    pointer = "root['components']['schemas']['Foo']['properties']['bar']['default']"
    assert is_additive_change("dictionary_item_added", pointer, None, [])


def test_default_string_add_is_additive():
    pointer = "root['components']['schemas']['Foo']['properties']['bar']['default']"
    assert is_additive_change("dictionary_item_added", pointer, None, "DISTRIBUTED")


def test_default_bool_add_is_additive():
    pointer = "root['components']['schemas']['Foo']['properties']['bar']['default']"
    assert is_additive_change("dictionary_item_added", pointer, None, False)


# Rule 3 — recursive dict-node rewrites (family 7)


def test_additive_dict_rewrite_is_accepted():
    """Whole-parameter rewrite where every inner change is additive."""
    pointer = "root['paths']['/x']['get']['parameters'][0]"
    before = {
        "name": "namespace",
        "in": "path",
        "description": "The namespace.\nx-example: system",
        "required": True,
    }
    after = {
        "name": "namespace",
        "in": "path",
        "description": "The namespace.",
        "required": True,
        "x-f5xc-example": "system",
    }
    assert is_additive_change("values_changed", pointer, before, after)


def test_non_additive_dict_rewrite_is_rejected():
    """Whole-parameter rewrite where one inner change flips a required flag."""
    pointer = "root['paths']['/x']['get']['parameters'][0]"
    before = {"name": "ns", "in": "path", "required": True}
    after = {"name": "ns", "in": "path", "required": False}
    assert not is_additive_change("values_changed", pointer, before, after)


# Rule 4 — known-format additions (family 8)


def test_known_format_add_is_additive():
    pointer = "root['components']['schemas']['Foo']['properties']['uid']['format']"
    assert is_additive_change("dictionary_item_added", pointer, None, "uuid")


def test_unknown_format_add_is_not_additive():
    pointer = "root['components']['schemas']['Foo']['properties']['uid']['format']"
    assert not is_additive_change("dictionary_item_added", pointer, None, "totally-made-up")


# Rule 5 — new property on a schema's `properties` dict


def test_property_add_is_additive():
    pointer = "root['components']['schemas']['Foo']['properties']['new_field']"
    after = {"type": "string", "maxLength": 1024}
    assert is_additive_change("dictionary_item_added", pointer, None, after)


def test_property_add_with_nested_extensions_is_additive():
    pointer = "root['components']['schemas']['Foo']['properties']['new_field']"
    after = {
        "type": "string",
        "maxLength": 1024,
        "x-f5xc-constraints": {
            "source": "inferred",
            "confidence": 0.85,
            "validatedAt": "2026-04-23T00:00:00",
        },
    }
    assert is_additive_change("dictionary_item_added", pointer, None, after)


# Rule 6 — dict-valued add that decomposes to all-additive inner adds


def test_bulk_properties_add_is_additive():
    """The whole `properties` dict being added to a schema is additive
    when each inner new property is itself additive (Rule 5)."""
    pointer = "root['components']['schemas']['routeRouteType']['properties']"
    after = {
        "bot_defense_javascript_injection": {
            "type": "boolean",
            "x-f5xc-constraints": {"source": "discovery"},
        },
        "service_policy": {
            "type": "string",
            "maxLength": 256,
        },
    }
    assert is_additive_change("dictionary_item_added", pointer, None, after)


def test_non_additive_dict_add_is_rejected():
    """A dict add whose inner keys aren't additive should fail."""
    pointer = "root['components']['schemas']['Foo']['required']"
    after = ["bar", "baz"]
    assert not is_additive_change("dictionary_item_added", pointer, None, after)


# Rule 9 — native `enum` transcribed from an upstream `string.in` rule


def test_added_string_enum_is_additive():
    """Creating the `enum` key from an upstream `string.in` rule is additive.

    `ConstraintEnricher._apply_string_in_enum` copies the values of
    `ves.io.schema.rules.string.in` onto the property's native `enum`. The
    server already rejects anything outside that set, so transcribing it
    documents an existing constraint rather than adding a new one.
    """
    pointer = (
        "root['components']['schemas']['routeRouteRedirect']"
        "['properties']['proto_redirect']['enum']"
    )
    assert is_additive_change(
        "dictionary_item_added",
        pointer,
        None,
        ["incoming-proto", "http", "https"],
    )


def test_added_empty_enum_is_not_additive():
    """An empty `enum` permits nothing — that narrows the contract to zero."""
    pointer = "root['components']['schemas']['Foo']['properties']['bar']['enum']"
    assert not is_additive_change("dictionary_item_added", pointer, None, [])


def test_added_non_string_enum_is_not_additive():
    """Only all-string enums come from `string.in`; anything else is unproven."""
    pointer = "root['components']['schemas']['Foo']['properties']['bar']['enum']"
    assert not is_additive_change("dictionary_item_added", pointer, None, [1, 2, 3])
    assert not is_additive_change("dictionary_item_added", pointer, None, [{"a": "b"}])


def test_added_non_list_enum_is_not_additive():
    """A scalar `enum` value is malformed, not additive."""
    pointer = "root['components']['schemas']['Foo']['properties']['bar']['enum']"
    assert not is_additive_change("dictionary_item_added", pointer, None, "http")


def test_enum_rule_does_not_widen_other_array_valued_adds():
    """Guard against blanket widening: other array-valued adds stay breaking."""
    for terminal in ("required", "oneOf", "anyOf", "allOf"):
        pointer = f"root['components']['schemas']['Foo']['{terminal}']"
        assert not is_additive_change("dictionary_item_added", pointer, None, ["bar"])
