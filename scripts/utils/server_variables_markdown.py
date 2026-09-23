# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Server variables markdown rendering utilities.

Centralizes server variables rendering for all documentation generators,
ensuring consistent presentation across discovery, constraint analysis,
linting, and validation reports.
"""

import logging
from pathlib import Path
from typing import Any

import yaml
from report_base import BaseReporter

logger = logging.getLogger(__name__)


class ServerVariablesMarkdownHelper:
    """Renders server variables configuration as markdown sections.

    Supports multiple presentation formats for different report types:
    - Summary tables for quick reference
    - Detailed tables with examples
    - URL template explanations
    - GitHub branch mapping documentation
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize with server variables configuration.

        Args:
            config_path: Path to config/server_variables.yaml. Defaults to
                        config/server_variables.yaml relative to project root.
        """
        self.config_path = config_path or (
            Path(__file__).parent.parent.parent / "config" / "server_variables.yaml"
        )
        self.config: dict[str, Any] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load server variables configuration from YAML."""
        try:
            with self.config_path.open() as f:
                self.config = yaml.safe_load(f) or {}
                logger.info("Loaded server variables config from %s", self.config_path)
        except FileNotFoundError:
            logger.warning("Server variables config not found: %s", self.config_path)
            self.config = {}
        except yaml.YAMLError:
            logger.exception("Error parsing server variables config")
            self.config = {}

    def render_variables_summary_table(self) -> str:
        """Render compact summary table of all server variables.

        Returns:
            Markdown table with variable names, defaults, and descriptions
        """
        variables = self.config.get("variables", {})
        if not variables:
            return ""

        headers = ["Variable", "Default", "Description"]
        rows = []

        for var_name, var_config in variables.items():
            default = var_config.get("default", "N/A")
            desc = var_config.get("description", "").split("\n")[0]  # First line only
            rows.append([var_name, default, desc])

        return BaseReporter.markdown_table(headers, rows)

    def render_variables_detailed_table(self) -> str:
        """Render detailed table with variables, defaults, and examples.

        Returns:
            Markdown table with comprehensive variable information
        """
        variables = self.config.get("variables", {})
        if not variables:
            return ""

        headers = ["Variable", "Default", "Examples", "Environment Var"]
        rows = []

        for var_name, var_config in variables.items():
            default = var_config.get("default", "N/A")
            examples = var_config.get("examples", [])
            examples_str = ", ".join(examples[:2]) if examples else "N/A"  # First 2 examples
            env_var = var_config.get("env_var", "N/A")
            rows.append([var_name, default, examples_str, env_var])

        return BaseReporter.markdown_table(headers, rows)

    def render_url_template_section(self) -> str:
        """Render section explaining the URL template and variable placement.

        Returns:
            Formatted markdown section
        """
        server_config = self.config.get("server", {})
        url_template = server_config.get("url_template", "")
        description = server_config.get("description", "")

        if not url_template:
            return ""

        content = f"```\n{url_template}\n```\n\n"
        if description:
            content += f"{description}\n\n"

        return BaseReporter.markdown_section(
            "API URL Template",
            content,
            level=3,
        )

    def render_github_branch_mapping_section(self) -> str:
        """Render section explaining GitHub branch to namespace mapping.

        Returns:
            Formatted markdown section
        """
        branch_mapping = self.config.get("github_branch_mapping", {})
        if not branch_mapping.get("enabled"):
            return ""

        patterns = branch_mapping.get("patterns", {})
        if not patterns:
            return ""

        content = "Branch → Namespace Mapping:\n\n"
        headers = ["Git Branch", "Namespace", "Use Case"]
        rows = []

        mapping_info = {
            "main": "Production deployments from main branch",
            "master": "Alternate production branch name",
            "feature/*": "Feature branch with automatic namespace",
            "bugfix/*": "Bugfix branch with automatic namespace",
            "hotfix/*": "Hotfix branch for critical issues",
            "staging": "Staging environment testing",
            "develop": "Development/pre-release testing",
            "default": "Fallback for unmapped branches",
        }

        for branch, namespace in patterns.items():
            use_case = mapping_info.get(branch, "")
            rows.append([branch, namespace, use_case])

        content += BaseReporter.markdown_table(headers, rows)

        return BaseReporter.markdown_section(
            "GitHub Branch Mapping",
            content,
            level=3,
        )

    def render_server_configuration_section(self) -> str:
        """Render comprehensive server configuration section for discovery reports.

        Returns:
            Formatted markdown section with variables, URL template, and mapping
        """
        sections = []

        # Variables summary
        sections.append(
            BaseReporter.markdown_section(
                "Server Variables",
                self.render_variables_detailed_table(),
                level=3,
            ),
        )

        # URL template
        url_section = self.render_url_template_section()
        if url_section:
            sections.append(url_section)

        # GitHub branch mapping
        mapping_section = self.render_github_branch_mapping_section()
        if mapping_section:
            sections.append(mapping_section)

        return "".join(sections)

    def render_variable_constraints_section(
        self,
        discovered_constraints: dict[str, Any] | None = None,
    ) -> str:
        """Render section explaining server variables in constraint analysis context.

        Args:
            discovered_constraints: Optional discovered constraint data

        Returns:
            Formatted markdown section
        """
        content = "This analysis compares constraints discovered through live API testing "
        content += "against the OpenAPI specification.\n\n"
        content += "Server variables configuration:\n\n"
        content += self.render_variables_summary_table()

        if discovered_constraints:
            content += "\n**Variables with Discovered Constraints**:\n\n"
            for var_name, constraints in discovered_constraints.items():
                content += f"- **{var_name}**: {constraints.get('description', 'N/A')}\n"

        return BaseReporter.markdown_section(
            "Server Configuration",
            content,
            level=3,
        )

    def render_server_configuration_validation_section(self) -> str:
        """Render section for linting/validation reports.

        Returns:
            Formatted markdown section
        """
        content = "The following server variables are configured for this API:\n\n"
        content += self.render_variables_summary_table()
        content += "\nAll specifications validate against the configured URL template:\n\n"

        server_config = self.config.get("server", {})
        url_template = server_config.get("url_template", "")
        if url_template:
            content += f"```\n{url_template}\n```\n"

        return BaseReporter.markdown_section(
            "Server Configuration",
            content,
            level=3,
        )

    def render_test_configuration_section(self) -> str:
        """Render section for test/validation reports.

        Returns:
            Formatted markdown section
        """
        content = "Validation tests use the following server variable configuration:\n\n"
        content += self.render_variables_detailed_table()

        # Add testing guidance
        content += "\n**Testing Notes**:\n\n"
        content += "- Tests execute against each configured variable value\n"
        content += "- Default values shown above are used for automated testing\n"
        content += "- Environment variables (XCSH_*) can override defaults\n"
        content += "- Multi-environment testing validates across all namespaces\n"

        return BaseReporter.markdown_section(
            "Server Configuration",
            content,
            level=3,
        )

    def get_variables_metadata(self) -> dict[str, Any]:
        """Get server variables configuration as dictionary for JSON reports.

        Returns:
            Dictionary with variables, URL template, and branch mappings
        """
        return {
            "url_template": self.config.get("server", {}).get("url_template"),
            "variables": self.config.get("variables", {}),
            "github_branch_mapping": self.config.get("github_branch_mapping", {}),
        }
