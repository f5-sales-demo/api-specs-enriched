# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for extension naming constants and validation utilities.

Version: v3.0.0 - Clean break, no backward compatibility
"""

from __future__ import annotations

import pytest

from scripts.utils.extension_constants import (
    PRESERVED_NATIVE_EXTENSIONS,
    VALID_X_F5XC_EXTENSIONS,
    X_F5XC_ACTION,
    X_F5XC_API_REFERENCE_URL,
    X_F5XC_CATEGORY,
    X_F5XC_CLI_DOMAIN,
    X_F5XC_DESCRIPTION_MEDIUM,
    X_F5XC_DESCRIPTION_SHORT,
    X_F5XC_ERROR_RESOLUTION,
    X_F5XC_FIELD_EXAMPLES,
    X_F5XC_MINIMUM_CONFIGURATION,
    X_F5XC_NAMESPACE_PROFILE,
    X_F5XC_OPERATION_METADATA,
    X_F5XC_PREFIX,
    X_F5XC_PRIMARY_RESOURCES,
    X_F5XC_SUMMARY,
    is_preserved_native,
    is_valid_extension,
    validate_no_invalid_extensions,
)


class TestNamespacePrefix:
    """Tests for the namespace prefix constant."""

    def test_prefix_value(self) -> None:
        """Verify the prefix is exactly 'x-f5xc-'."""
        assert X_F5XC_PREFIX == "x-f5xc-"

    def test_prefix_starts_with_x(self) -> None:
        """Prefix must start with 'x-' for OpenAPI compliance."""
        assert X_F5XC_PREFIX.startswith("x-")

    def test_prefix_lowercase(self) -> None:
        """Prefix must be lowercase."""
        assert X_F5XC_PREFIX.lower() == X_F5XC_PREFIX


class TestExtensionConstants:
    """Tests for individual extension constant values."""

    @pytest.mark.parametrize(
        "constant",
        [
            X_F5XC_CLI_DOMAIN,
            X_F5XC_MINIMUM_CONFIGURATION,
            X_F5XC_NAMESPACE_PROFILE,
            X_F5XC_ACTION,
            X_F5XC_OPERATION_METADATA,
            X_F5XC_CATEGORY,  # Single category field (DRY)
            X_F5XC_PRIMARY_RESOURCES,
            X_F5XC_DESCRIPTION_SHORT,
            X_F5XC_DESCRIPTION_MEDIUM,
            X_F5XC_SUMMARY,
            X_F5XC_API_REFERENCE_URL,
            X_F5XC_FIELD_EXAMPLES,
            X_F5XC_ERROR_RESOLUTION,
        ],
    )
    def test_constant_has_prefix(self, constant: str) -> None:
        """All extension constants must use x-f5xc- prefix."""
        assert constant.startswith(X_F5XC_PREFIX), f"Constant '{constant}' missing x-f5xc- prefix"

    @pytest.mark.parametrize(
        "constant",
        [
            X_F5XC_CLI_DOMAIN,
            X_F5XC_MINIMUM_CONFIGURATION,
            X_F5XC_NAMESPACE_PROFILE,
            X_F5XC_ACTION,
            X_F5XC_OPERATION_METADATA,
            X_F5XC_CATEGORY,  # Single category field (DRY)
            X_F5XC_PRIMARY_RESOURCES,
            X_F5XC_DESCRIPTION_SHORT,
            X_F5XC_DESCRIPTION_MEDIUM,
        ],
    )
    def test_constant_is_lowercase(self, constant: str) -> None:
        """Extension names must be lowercase with hyphens."""
        assert constant == constant.lower(), f"Constant '{constant}' must be lowercase"

    @pytest.mark.parametrize(
        "constant",
        [
            X_F5XC_CLI_DOMAIN,
            X_F5XC_MINIMUM_CONFIGURATION,
            X_F5XC_NAMESPACE_PROFILE,
            X_F5XC_ACTION,
            X_F5XC_OPERATION_METADATA,
            X_F5XC_CATEGORY,  # Single category field (DRY)
            X_F5XC_PRIMARY_RESOURCES,
            X_F5XC_DESCRIPTION_SHORT,
            X_F5XC_DESCRIPTION_MEDIUM,
        ],
    )
    def test_constant_no_underscores(self, constant: str) -> None:
        """Extension names should use hyphens, not underscores (after prefix)."""
        after_prefix = constant[len(X_F5XC_PREFIX) :]
        assert "_" not in after_prefix, f"Constant '{constant}' should use hyphens, not underscores"


class TestPreservedNativeExtensions:
    """Tests for preserved F5 native extensions."""

    def test_preserved_extensions_not_empty(self) -> None:
        """Should have preserved extensions."""
        assert len(PRESERVED_NATIVE_EXTENSIONS) > 0

    @pytest.mark.parametrize(
        "extension",
        [
            "x-ves-proto-package",
            "x-ves-proto-file",
            "x-ves-proto-message",
            "x-displayname",
        ],
    )
    def test_known_preserved_extensions(self, extension: str) -> None:
        """Known F5 native extensions should be in preserved set."""
        assert extension in PRESERVED_NATIVE_EXTENSIONS


class TestValidExtensions:
    """Tests for valid extension set."""

    def test_valid_extensions_not_empty(self) -> None:
        """Valid extensions set should not be empty."""
        assert len(VALID_X_F5XC_EXTENSIONS) > 0

    def test_all_valid_have_prefix(self) -> None:
        """All valid extensions must have x-f5xc- prefix."""
        for ext in VALID_X_F5XC_EXTENSIONS:
            assert ext.startswith(X_F5XC_PREFIX), f"Valid extension '{ext}' missing prefix"

    def test_generated_contract_fields_are_valid(self) -> None:
        """Fields emitted by the release pipeline must be registry members."""
        contract_fields = [
            X_F5XC_API_REFERENCE_URL,
            X_F5XC_FIELD_EXAMPLES,
            X_F5XC_ERROR_RESOLUTION,
            X_F5XC_OPERATION_METADATA,
        ]
        for field in contract_fields:
            assert field in VALID_X_F5XC_EXTENSIONS, f"New field '{field}' not in valid set"


class TestValidationFunctions:
    """Tests for validation utility functions."""

    def test_is_valid_extension_true(self) -> None:
        """Valid extensions should return True."""
        assert is_valid_extension(X_F5XC_CLI_DOMAIN)
        assert is_valid_extension(X_F5XC_OPERATION_METADATA)
        assert is_valid_extension(X_F5XC_API_REFERENCE_URL)

    @pytest.mark.parametrize(
        "removed_extension",
        [
            "x-f5xc-required-fields",
            "x-f5xc-danger-level",
            "x-f5xc-confirmation-required",
            "x-f5xc-side-effects",
        ],
    )
    def test_flat_operation_metadata_extensions_are_invalid(
        self,
        removed_extension: str,
    ) -> None:
        """Operation metadata has one registered wrapper, not parallel flat fields."""
        assert removed_extension not in VALID_X_F5XC_EXTENSIONS
        assert not is_valid_extension(removed_extension)

    def test_is_valid_extension_false(self) -> None:
        """Invalid extensions should return False."""
        assert not is_valid_extension("x-ves-cli-domain")  # old name
        assert not is_valid_extension("domain_category")  # non-prefixed
        assert not is_valid_extension("x-random-extension")  # unknown

    def test_is_preserved_native_true(self) -> None:
        """Preserved native extensions should return True."""
        assert is_preserved_native("x-ves-proto-package")
        assert is_preserved_native("x-displayname")

    def test_is_preserved_native_false(self) -> None:
        """Non-native extensions should return False."""
        assert not is_preserved_native(X_F5XC_CLI_DOMAIN)
        assert not is_preserved_native("x-ves-cli-domain")


class TestValidateNoInvalidExtensions:
    """Tests for the spec validation function."""

    def test_valid_spec_no_errors(self) -> None:
        """Valid spec should have no errors."""
        valid_spec = {
            "openapi": "3.0.0",
            "info": {
                "title": "Test API",
                "version": "1.0.0",
                X_F5XC_CLI_DOMAIN: "test",
                X_F5XC_SUMMARY: "Test API contract",
            },
            "x-ves-proto-package": "native.field",  # preserved
        }
        errors = validate_no_invalid_extensions(valid_spec)
        assert len(errors) == 0

    def test_old_x_ves_field_error(self) -> None:
        """Old x-ves-* fields should produce errors."""
        spec = {
            "info": {
                "x-ves-cli-domain": "test",  # old field - not in valid or preserved sets
            },
        }
        errors = validate_no_invalid_extensions(spec)
        assert len(errors) == 1
        assert "x-ves-cli-domain" in errors[0]
        assert "x-f5xc-*" in errors[0]  # Generic namespace guidance

    def test_preserved_native_no_error(self) -> None:
        """Preserved native fields should not produce errors."""
        spec = {
            "x-ves-proto-package": "ves.io.schema.test",
            "x-displayname": "Test API",
        }
        errors = validate_no_invalid_extensions(spec)
        assert len(errors) == 0

    def test_nested_errors(self) -> None:
        """Nested invalid fields should be caught."""
        spec = {
            "components": {
                "schemas": {
                    "TestSchema": {
                        "type": "object",
                        "x-ves-minimum-configuration": {},  # old field
                    },
                },
            },
        }
        errors = validate_no_invalid_extensions(spec)
        assert len(errors) == 1
        assert "components.schemas.TestSchema" in errors[0]

    def test_array_nested_errors(self) -> None:
        """Errors in arrays should include index."""
        spec = {
            "tags": [
                {"name": "valid"},
                {"name": "invalid", "x-ves-cli-domain": "test"},
            ],
        }
        errors = validate_no_invalid_extensions(spec)
        assert len(errors) == 1
        assert "[1]" in errors[0]

    def test_unknown_x_extension_error(self) -> None:
        """Unknown x-* extensions should produce errors."""
        spec = {
            "x-random-vendor-field": "value",  # not in our namespace
        }
        errors = validate_no_invalid_extensions(spec)
        assert len(errors) == 1
        assert "x-random-vendor-field" in errors[0]
