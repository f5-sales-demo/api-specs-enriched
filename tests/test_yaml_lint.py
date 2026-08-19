"""Repository YAML inventory lint gate (Issue #1232)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def test_repository_yaml_inventory_passes_configured_linter() -> None:
    """Keep generated console inventories compatible with repository YAML policy."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "yamllint",
            "-f",
            "parsable",
            "--no-warnings",
            "-c",
            ".yamllint.yaml",
            ".",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        "The generated console inventories must pass the repository's configured YAML "
        f"linter.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
