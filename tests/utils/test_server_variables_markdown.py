# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for server variables markdown rendering."""

import sys
import tempfile
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts" / "utils"))

from server_variables_markdown import ServerVariablesMarkdownHelper


@pytest.fixture
def temp_config():
    """Create temporary server_variables.yaml for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        config_content = {
            "version": "2.0.0",
            "server": {
                "url_template": "https://{tenant}.{console_url}/api/v1",
                "description": "Test API Server",
            },
            "variables": {
                "tenant": {
                    "default": "test-corp",
                    "description": "Tenant identifier",
                    "examples": ["test-corp", "example-corp"],
                    "env_var": "XCSH_TENANT",
                },
                "console_url": {
                    "default": "console.example.io",
                    "description": "Console URL base",
                    "examples": ["console.example.io", "staging.example.io"],
                    "env_var": "XCSH_CONSOLE_URL",
                },
            },
            "github_branch_mapping": {
                "enabled": True,
                "patterns": {
                    "main": "main",
                    "staging": "staging",
                },
            },
        }

        config_path = tmpdir_path / "server_variables.yaml"
        with config_path.open("w") as f:
            yaml.dump(config_content, f)

        yield config_path


def test_server_variables_loads_config(temp_config):
    """Test that ServerVariablesMarkdownHelper loads config."""
    helper = ServerVariablesMarkdownHelper(temp_config)

    assert helper.config is not None
    assert helper.config.get("version") == "2.0.0"


def test_render_variables_summary_table(temp_config):
    """Test rendering variables summary table."""
    helper = ServerVariablesMarkdownHelper(temp_config)
    table = helper.render_variables_summary_table()

    assert "| Variable | Default | Description |" in table
    assert "| tenant | test-corp | Tenant identifier |" in table
    assert "| console_url | console.example.io | Console URL base |" in table


def test_render_variables_detailed_table(temp_config):
    """Test rendering detailed variables table."""
    helper = ServerVariablesMarkdownHelper(temp_config)
    table = helper.render_variables_detailed_table()

    assert "| Variable | Default | Examples | Environment Var |" in table
    assert "test-corp" in table
    assert "XCSH_TENANT" in table


def test_render_url_template_section(temp_config):
    """Test rendering URL template section."""
    helper = ServerVariablesMarkdownHelper(temp_config)
    section = helper.render_url_template_section()

    assert "API URL Template" in section
    assert "https://{tenant}.{console_url}/api/v1" in section
    assert "Test API Server" in section


def test_render_github_branch_mapping_section(temp_config):
    """Test rendering GitHub branch mapping section."""
    helper = ServerVariablesMarkdownHelper(temp_config)
    section = helper.render_github_branch_mapping_section()

    assert "GitHub Branch Mapping" in section
    assert "| Git Branch | Namespace" in section
    assert "| main | main |" in section
    assert "| staging | staging |" in section


def test_render_server_configuration_section(temp_config):
    """Test rendering comprehensive server configuration section."""
    helper = ServerVariablesMarkdownHelper(temp_config)
    section = helper.render_server_configuration_section()

    assert "Server Variables" in section
    assert "API URL Template" in section
    assert "GitHub Branch Mapping" in section


def test_render_variable_constraints_section(temp_config):
    """Test rendering constraint analysis section."""
    helper = ServerVariablesMarkdownHelper(temp_config)
    section = helper.render_variable_constraints_section()

    assert "Server Configuration" in section
    assert "Constraint" in section or "constraint" in section
    assert "tenant" in section


def test_render_server_configuration_validation_section(temp_config):
    """Test rendering validation section."""
    helper = ServerVariablesMarkdownHelper(temp_config)
    section = helper.render_server_configuration_validation_section()

    assert "Server Configuration" in section
    assert "server variables" in section
    assert "URL template" in section or "url_template" in section


def test_render_test_configuration_section(temp_config):
    """Test rendering test configuration section."""
    helper = ServerVariablesMarkdownHelper(temp_config)
    section = helper.render_test_configuration_section()

    assert "Server Configuration" in section
    assert "variable configuration" in section
    assert "Testing" in section or "testing" in section


def test_get_variables_metadata(temp_config):
    """Test retrieving variables metadata as dictionary."""
    helper = ServerVariablesMarkdownHelper(temp_config)
    metadata = helper.get_variables_metadata()

    assert isinstance(metadata, dict)
    assert "url_template" in metadata
    assert "variables" in metadata
    assert "github_branch_mapping" in metadata
    assert metadata["url_template"] == "https://{tenant}.{console_url}/api/v1"


def test_server_variables_markdown_with_missing_config():
    """Test that helper handles missing config gracefully."""
    missing_path = Path("/nonexistent/server_variables.yaml")
    helper = ServerVariablesMarkdownHelper(missing_path)

    # Should not raise, should handle gracefully
    assert helper.config is not None or helper.config == {}


def test_server_variables_all_rendering_methods(temp_config):
    """Test that all rendering methods return non-empty strings."""
    helper = ServerVariablesMarkdownHelper(temp_config)

    summary = helper.render_variables_summary_table()
    assert summary is not None
    assert len(summary) > 0

    detailed = helper.render_variables_detailed_table()
    assert detailed is not None
    assert len(detailed) > 0

    url_template = helper.render_url_template_section()
    assert url_template is not None
    assert len(url_template) > 0

    branch_mapping = helper.render_github_branch_mapping_section()
    assert branch_mapping is not None
    assert len(branch_mapping) > 0
