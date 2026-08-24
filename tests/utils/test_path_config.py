# Copyright (c) 2026 Robin Mordasiewicz. MIT License.
# pylint: disable=protected-access  # Tests intentionally verify deterministic internal helpers.

"""Tests for path configuration management."""

import sys
import tempfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts" / "utils"))

from path_config import PathConfig


@pytest.fixture
def temp_config_dir():
    """Create temporary directory with test config."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # Create test paths.yaml
        config_content = {
            "version": "1.0.0",
            "reports": {
                "directory": "test_reports",
                "discovery_report": "test-discovery.md",
            },
            "specs": {
                "original_dir": "test_specs/original",
                "discovered_dir": "test_specs/discovered",
            },
            "output": {
                "docs_api_dir": "test_docs/api",
            },
        }

        config_path = tmpdir_path / "paths.yaml"
        with config_path.open("w") as f:
            yaml.dump(config_content, f)

        yield tmpdir_path, config_path


def test_path_config_loads_yaml(temp_config_dir):
    """Test that PathConfig loads YAML configuration."""
    _, config_path = temp_config_dir

    # Create new instance with test config
    config = PathConfig(config_path)

    assert config.config is not None
    assert config.config.get("version") == "1.0.0"


def test_path_config_singleton():
    """Test that PathConfig implements singleton pattern."""
    config1 = PathConfig()
    config2 = PathConfig()

    assert config1 is config2


def test_path_config_reports_dir_property(temp_config_dir):
    """Test that reports_dir property works."""
    _, config_path = temp_config_dir

    # Reset singleton for test
    PathConfig._instance = None

    config = PathConfig(config_path)
    assert str(config.reports_dir) == "test_reports"


def test_path_config_specs_original_dir_property(temp_config_dir):
    """Test that specs_original_dir property works."""
    _, config_path = temp_config_dir

    # Reset singleton for test
    PathConfig._instance = None

    config = PathConfig(config_path)
    assert str(config.specs_original_dir) == "test_specs/original"


def test_path_config_output_dir_property(temp_config_dir):
    """Test that output directory properties work."""
    _, config_path = temp_config_dir

    # Reset singleton for test
    PathConfig._instance = None

    config = PathConfig(config_path)
    assert str(config.docs_api_dir) == "test_docs/api"


def test_path_config_handles_missing_file():
    """Test that PathConfig handles missing config file gracefully."""
    missing_path = Path("/nonexistent/paths.yaml")

    # Reset singleton for test
    PathConfig._instance = None

    config = PathConfig(missing_path)
    # Should not raise, should use defaults
    assert config.config is not None or config.config == {}


def test_path_config_ensure_dir_exists(temp_config_dir):
    """Test that ensure_report_dir_exists creates directory."""
    _, config_path = temp_config_dir

    # Reset singleton for test
    PathConfig._instance = None

    config = PathConfig(config_path)
    result = config.ensure_report_dir_exists()

    assert result.exists()
    assert result.is_dir()


def test_path_config_all_paths_defined():
    """Test that all expected path properties are defined."""
    config = PathConfig()

    # Check key properties exist
    assert hasattr(config, "reports_dir")
    assert hasattr(config, "discovery_report")
    assert hasattr(config, "constraint_analysis")
    assert hasattr(config, "lint_report")
    assert hasattr(config, "validation_report")
    assert hasattr(config, "specs_original_dir")
    assert hasattr(config, "specs_discovered_dir")
    assert hasattr(config, "docs_api_dir")
    assert hasattr(config, "config_dir")
