# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Server variables helper for F5 XC API OpenAPI specifications.

Centralizes server variable configuration and URL template construction for the
single unified pipeline.
"""

import logging
import os
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class ServerVariableHelper:
    """Manages OpenAPI server variables configuration and URL construction.

    Supports 6 server variables:
    - tenant: Explicit tenant identifier
    - console_url: Console URL base (e.g., console.ves.volterra.io)
    - namespace: Kubernetes-style namespace
    - environment: Environment designation (production/staging/development)
    - region: Geographic region for multi-region deployments
    - domain_prefix: Industry-standard naming conventions

    URL template: https://{tenant}.{console_url}/api/v1/namespaces/{namespace}
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize helper with configuration file.

        Args:
            config_path: Path to server_variables.yaml config file.
                        Defaults to config/server_variables.yaml
        """
        self.config_path = (
            config_path or Path(__file__).parent.parent.parent / "config" / "server_variables.yaml"
        )
        self.config: dict[str, Any] = {}
        self.variables: dict[str, dict[str, Any]] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load server variables configuration from YAML file."""
        try:
            with self.config_path.open() as f:
                self.config = yaml.safe_load(f) or {}
                self.variables = self.config.get("variables", {})
                logger.info("Loaded server variables from %s", self.config_path)
                logger.info("Available variables: %s", list(self.variables.keys()))
        except FileNotFoundError:
            logger.warning("Config file not found: %s", self.config_path)
            self.config = {}
            self.variables = {}
        except yaml.YAMLError:
            logger.exception("Error parsing configuration")
            self.config = {}
            self.variables = {}

    def _get_variable_default(self, var_name: str) -> str:
        """Get default value for a variable from config or environment.

        Priority:
        1. Environment variable (F5XC_*)
        2. Config default value
        3. Hardcoded fallback

        Args:
            var_name: Name of the variable (e.g., 'api_url', 'namespace')

        Returns:
            Default value for the variable
        """
        # Check config for variable definition
        if var_name in self.variables:
            var_config = self.variables[var_name]
            env_var = var_config.get("env_var")

            # Priority 1: Environment variable
            if env_var and os.getenv(env_var):
                return os.getenv(env_var, "")

            # Priority 2: Config default
            if "default" in var_config:
                return var_config["default"]

        # Priority 3: Hardcoded fallbacks
        fallbacks = {
            "api_url": "https://example-corp.console.ves.volterra.io",
            "tenant": "example-corp",
            "console_url": "console.ves.volterra.io",
            "namespace": "default",
            "environment": "production",
            "region": "us-east-1",
            "domain_prefix": "api",
        }
        return fallbacks.get(var_name, "")

    def get_server_url_template(self) -> str:
        """Get the OpenAPI server URL template.

        Returns:
            URL template string with variable placeholders
        """
        return self.config.get("server", {}).get(
            "url_template",
            "https://{tenant}.{console_url}/api/v1/namespaces/{namespace}",
        )

    def get_server_description(self) -> str:
        """Get the OpenAPI server description.

        Returns:
            Server description string
        """
        return self.config.get("server", {}).get(
            "description",
            "F5 Distributed Cloud Console",
        )

    def build_variables_dict(self) -> dict[str, dict[str, str]]:
        """Build OpenAPI server variables dictionary.

        Returns:
            Dictionary of variable configurations suitable for OpenAPI spec
        """
        variables_dict = {}

        # Build each variable with defaults and descriptions
        for var_name, var_config in self.variables.items():
            variables_dict[var_name] = {
                "default": self._get_variable_default(var_name),
                "description": var_config.get(
                    "description",
                    f"F5 XC {var_name} configuration",
                ),
            }

            # Add enum if specified
            if "enum" in var_config:
                variables_dict[var_name]["enum"] = var_config["enum"]

        return variables_dict

    def create_base_spec(
        self,
        title: str,
        description: str,
        version: str,
    ) -> dict[str, Any]:
        """Create a base OpenAPI specification with server variables.

        Args:
            title: API title
            description: API description
            version: Full version string

        Returns:
            Base OpenAPI specification dictionary
        """
        info: dict[str, Any] = {
            "title": title,
            "description": description,
            "version": version,
            "contact": {
                "name": "F5 Distributed Cloud",
                "url": "https://docs.cloud.f5.com",
            },
            "license": {
                "name": "Proprietary",
                "url": "https://www.f5.com/company/policies/eula",
            },
        }

        return {
            "openapi": "3.0.3",
            "info": info,
            "servers": [
                {
                    "url": self.get_server_url_template(),
                    "description": self.get_server_description(),
                    "variables": self.build_variables_dict(),
                },
            ],
            "security": [
                {"ApiToken": []},
            ],
            "tags": [],
            "paths": {},
            "components": {
                "securitySchemes": {
                    "ApiToken": {
                        "type": "apiKey",
                        "name": "Authorization",
                        "in": "header",
                        "description": "API Token authentication. Format: 'APIToken <your-token>'",
                    },
                },
                "schemas": {},
                "responses": {},
                "parameters": {},
                "requestBodies": {},
            },
        }
