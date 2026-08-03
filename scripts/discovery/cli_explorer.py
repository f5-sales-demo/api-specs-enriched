# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""xcsh CLI integration for API discovery.

Uses the xcsh command-line tool (F5 Distributed Cloud Shell) to:
- List available resources
- Execute RPC calls
- Get CLI specification
- Explore API structure
"""

import asyncio
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CLIResult:
    """Result from CLI command execution."""

    success: bool
    data: Any = None
    error: str | None = None
    raw_output: str = ""
    return_code: int = 0


@dataclass
class ResourceInfo:
    """Information about a discovered resource."""

    name: str
    namespace: str
    kind: str
    metadata: dict = field(default_factory=dict)
    spec: dict = field(default_factory=dict)


class CLIExplorer:
    """Explore F5 XC API using xcsh CLI (F5 Distributed Cloud Shell).

    Provides:
    - Resource listing and discovery
    - RPC command execution
    - CLI specification parsing
    - Async subprocess execution
    """

    def __init__(
        self,
        executable: str = "xcsh",
        output_format: str = "json",
        timeout: int = 30,
    ) -> None:
        """Initialize CLI explorer.

        Args:
            executable: Path to xcsh executable
            output_format: Output format (json or yaml)
            timeout: Command timeout in seconds
        """
        self.executable = executable
        self.output_format = output_format
        self.timeout = timeout
        self._cli_available: bool | None = None

    def is_available(self) -> bool:
        """Check if xcsh CLI is available."""
        if self._cli_available is not None:
            return self._cli_available

        self._cli_available = shutil.which(self.executable) is not None
        return self._cli_available

    async def _run_command(self, args: list[str]) -> CLIResult:
        """Run a CLI command asynchronously.

        Args:
            args: Command arguments (without executable)

        Returns:
            CLIResult with output or error
        """
        if not self.is_available():
            return CLIResult(
                success=False,
                error=f"CLI executable '{self.executable}' not found",
            )

        full_cmd = [self.executable, *args]

        try:
            process = await asyncio.create_subprocess_exec(
                *full_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.timeout,
            )

            stdout_str = stdout.decode("utf-8").strip()
            stderr_str = stderr.decode("utf-8").strip()

            if process.returncode != 0:
                return CLIResult(
                    success=False,
                    error=stderr_str or f"Command failed with code {process.returncode}",
                    raw_output=stdout_str,
                    return_code=process.returncode or 0,
                )

            # Try to parse JSON output
            if self.output_format == "json" and stdout_str:
                try:
                    data = json.loads(stdout_str)
                    return CLIResult(success=True, data=data, raw_output=stdout_str)
                except json.JSONDecodeError:
                    # Return raw output if not JSON
                    return CLIResult(success=True, data=stdout_str, raw_output=stdout_str)

            return CLIResult(success=True, data=stdout_str, raw_output=stdout_str)

        except TimeoutError:
            return CLIResult(
                success=False,
                error=f"Command timed out after {self.timeout} seconds",
            )
        except Exception as e:
            return CLIResult(success=False, error=str(e))

    async def get_cli_spec(self) -> CLIResult:
        """Get CLI specification showing available commands.

        Returns:
            CLIResult with CLI spec data
        """
        return await self._run_command(["--spec", "--output-format", "json"])

    async def list_namespaces(self) -> CLIResult:
        """List all available namespaces.

        Returns:
            CLIResult with namespace list
        """
        return await self._run_command(
            ["configuration", "list", "namespace", "--output-format", "json"],
        )

    async def list_resources(
        self,
        resource_type: str,
        namespace: str = "system",
    ) -> CLIResult:
        """List resources of a specific type.

        Args:
            resource_type: Type of resource (e.g., http_loadbalancer)
            namespace: Namespace to list from

        Returns:
            CLIResult with resource list
        """
        return await self._run_command(
            [
                "configuration",
                "list",
                resource_type,
                "-n",
                namespace,
                "--output-format",
                "json",
            ],
        )

    async def get_resource(
        self,
        resource_type: str,
        name: str,
        namespace: str = "system",
    ) -> CLIResult:
        """Get a specific resource.

        Args:
            resource_type: Type of resource
            name: Resource name
            namespace: Namespace

        Returns:
            CLIResult with resource data
        """
        return await self._run_command(
            [
                "configuration",
                "get",
                resource_type,
                name,
                "-n",
                namespace,
                "--output-format",
                "json",
            ],
        )

    async def execute_rpc(
        self,
        command: str,
        data: dict | None = None,
    ) -> CLIResult:
        """Execute an RPC command.

        Args:
            command: RPC command name (e.g., api_credential.CustomAPI.List)
            data: Optional request data

        Returns:
            CLIResult with RPC response
        """
        args = ["request", "rpc", command, "--output-format", "json"]

        if data:
            args.extend(["--data", json.dumps(data)])

        return await self._run_command(args)

    async def get_subscription(self) -> CLIResult:
        """Get subscription information.

        Returns:
            CLIResult with subscription data
        """
        return await self._run_command(
            ["subscription", "show", "--output-format", "json"],
        )

    async def discover_resource_types(self) -> list[str]:
        """Discover available resource types from CLI spec.

        Returns:
            List of resource type names
        """
        result = await self.get_cli_spec()
        if not result.success or not result.data:
            return []

        resource_types = []

        # Parse CLI spec to find resource types
        if isinstance(result.data, dict):
            commands = result.data.get("commands", [])
            for cmd in commands:
                if cmd.get("name") == "configuration":
                    sub_commands = cmd.get("commands", [])
                    for sub_cmd in sub_commands:
                        if sub_cmd.get("name") == "list":
                            # Extract resource types from list command
                            args = sub_cmd.get("arguments", [])
                            for arg in args:
                                if arg.get("name") == "object_type":
                                    choices = arg.get("choices", [])
                                    resource_types.extend(choices)

        return resource_types

    async def discover_rpc_commands(self) -> list[str]:
        """Discover available RPC commands.

        Returns:
            List of RPC command names
        """
        result = await self._run_command(["request", "rpc", "--help"])

        if not result.success:
            return []

        # Parse help output to extract RPC commands
        rpc_commands = []
        for line in result.raw_output.split("\n"):
            line = line.strip()
            # RPC commands are typically in format: api.CustomAPI.Method
            if "." in line and line[0].isalpha():
                # Extract command name (first word)
                parts = line.split()
                if parts and "." in parts[0]:
                    rpc_commands.append(parts[0])

        return rpc_commands

    async def explore_namespace(self, namespace: str) -> dict[str, list[ResourceInfo]]:
        """Explore all resources in a namespace.

        Args:
            namespace: Namespace to explore

        Returns:
            Dict mapping resource types to lists of resources
        """
        resources: dict[str, list[ResourceInfo]] = {}

        # Get resource types
        resource_types = await self.discover_resource_types()

        for resource_type in resource_types[:20]:  # Limit to avoid too many requests
            result = await self.list_resources(resource_type, namespace)

            if result.success and result.data:
                items = []
                data = result.data

                # Handle different response formats
                if isinstance(data, dict):
                    items = data.get("items", []) or data.get("objects", [])
                elif isinstance(data, list):
                    items = data

                resources[resource_type] = [
                    ResourceInfo(
                        name=item.get("metadata", {}).get("name", "unknown"),
                        namespace=namespace,
                        kind=resource_type,
                        metadata=item.get("metadata", {}),
                        spec=item.get("spec", {}),
                    )
                    for item in items
                    if isinstance(item, dict)
                ]

        return resources

    def get_curl_command(self, args: list[str]) -> str:
        """Get equivalent curl command for debugging.

        Args:
            args: CLI arguments

        Returns:
            Equivalent curl command string
        """
        full_cmd = [self.executable, *args, "--show-curl"]

        try:
            result = subprocess.run(
                full_cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.stdout.strip()
        except Exception:
            return f"# Could not generate curl command for: {' '.join(args)}"
