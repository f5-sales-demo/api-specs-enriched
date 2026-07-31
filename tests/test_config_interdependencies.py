"""The config cross-references must hold, and CI must be the thing that says so.

``scripts/validate_configs.py`` checks that every resource named in
``minimum_configs.yaml`` and ``operation_descriptions.yaml`` exists in
``resource_metadata.yaml``, and that each minimum-config entry carries the fields
the pipeline expects.

It ran nowhere. Not in a workflow, not in the Makefile — only in
``scripts/pre-commit-local.sh``, and there its output went to ``2>/dev/null`` and
its exit code to ``|| echo "failed or not configured"``. So it failed on ``main``
for as long as ``securemesh_site_v2`` had been in ``minimum_configs.yaml`` without a
matching ``resource_metadata.yaml`` entry, printing its complaint into a log nobody
read, while the hook reported that all repo-specific checks passed.

Running it from the test suite is what makes it a gate: the suite is a required
check, so a config that no longer cross-references cannot merge.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent


def run_validator() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "scripts.validate_configs"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_config_interdependencies_are_consistent():
    result = run_validator()
    assert result.returncode == 0, (
        "scripts.validate_configs failed. Every resource in minimum_configs.yaml and "
        "operation_descriptions.yaml must exist in resource_metadata.yaml, and each "
        "minimum-config entry needs description, required_fields and example_yaml.\n\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_the_validator_reports_rather_than_failing_silently():
    # A validator that exits non-zero with no explanation is only marginally better
    # than one nobody runs — the failure above has to name the resource and the
    # missing field, or the next person is left bisecting config files.
    result = run_validator()
    assert result.stdout.strip() or result.stderr.strip(), (
        "scripts.validate_configs produced no output at all; a silent gate cannot be "
        "acted on when it fires"
    )
