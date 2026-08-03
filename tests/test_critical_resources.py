# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Unit tests for critical resources configuration and loading.

Tests the x-ves-critical-resources extension added to index.json
for downstream tooling (e.g., xcsh CLI).
"""

from pathlib import Path

import pytest
import yaml

from scripts.utils.critical_resources import (
    CriticalResourcesConfigError,
    load_critical_resources,
)


class TestLoadCriticalResources:
    """Test loading critical resources from configuration."""

    def test_load_returns_list(self) -> None:
        """Verify load function returns a list."""
        result = load_critical_resources()
        assert isinstance(result, list)

    def test_load_returns_non_empty_list(self) -> None:
        """Verify load function returns a non-empty list."""
        result = load_critical_resources()
        assert len(result) > 0

    def test_load_returns_strings(self) -> None:
        """Verify load function returns list of strings."""
        result = load_critical_resources()
        for item in result:
            assert isinstance(item, str)

    def test_load_includes_core_resources(self) -> None:
        """Verify loaded resources include core load balancing."""
        result = load_critical_resources()
        assert "http_loadbalancer" in result
        assert "origin_pool" in result


class TestCriticalResourcesConfig:
    """Test critical resources configuration file."""

    @pytest.fixture
    def config_path(self) -> Path:
        """Get path to critical resources config file."""
        return Path(__file__).parent.parent / "config" / "critical_resources.yaml"

    def test_config_file_exists(self, config_path: Path) -> None:
        """Verify configuration file exists."""
        assert config_path.exists(), f"Config file not found: {config_path}"

    def test_config_is_valid_yaml(self, config_path: Path) -> None:
        """Verify configuration file is valid YAML."""
        with config_path.open() as f:
            config = yaml.safe_load(f)
        assert config is not None

    def test_config_has_resources_key(self, config_path: Path) -> None:
        """Verify configuration has resources key."""
        with config_path.open() as f:
            config = yaml.safe_load(f)
        assert "resources" in config

    def test_config_resources_is_list(self, config_path: Path) -> None:
        """Verify resources is a list."""
        with config_path.open() as f:
            config = yaml.safe_load(f)
        assert isinstance(config["resources"], list)

    def test_config_has_version(self, config_path: Path) -> None:
        """Verify configuration has version."""
        with config_path.open() as f:
            config = yaml.safe_load(f)
        assert "version" in config

    def test_config_has_description(self, config_path: Path) -> None:
        """Verify configuration has description."""
        with config_path.open() as f:
            config = yaml.safe_load(f)
        assert "description" in config

    def test_loader_matches_the_single_config_contract(self, config_path: Path) -> None:
        """Verify the loader has no second fallback source of truth."""
        with config_path.open() as f:
            config = yaml.safe_load(f)
        assert load_critical_resources(config_path) == config["resources"]

    @pytest.mark.parametrize(
        "content",
        [
            "version: '1'\ndescription: one\nresources: [a]\nresources: [b]\n",
            "version: '1'\ndescription: one\nresources: []\n",
            "version: '1'\ndescription: one\nresources: [a, a]\n",
            "version: '1'\ndescription: one\nresources: [a]\nunknown: true\n",
        ],
    )
    def test_invalid_or_ambiguous_config_fails_closed(self, tmp_path: Path, content: str) -> None:
        path = tmp_path / "critical_resources.yaml"
        path.write_text(content)
        with pytest.raises(CriticalResourcesConfigError):
            load_critical_resources(path)
