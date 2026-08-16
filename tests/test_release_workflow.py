"""Regression coverage for automated release pull-request creation."""

from pathlib import Path

WORKFLOW = Path(".github/workflows/sync-and-enrich.yml")


def test_release_pr_create_is_non_interactive() -> None:
    """The release workflow must supply every prompt-required PR field."""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    command = workflow.split("PR_URL=$(gh pr create", maxsplit=1)[1].split(
        "# `release/**` is exempt", maxsplit=1
    )[0]

    assert '--title "chore: release v${VERSION} (${BUMP_TYPE})"' in command
    assert (
        '--body "Automated release v${VERSION} (${BUMP_TYPE}). Created by the sync-and-enrich workflow."'
        in command
    )


def test_downstream_matrix_sets_up_python_before_installing_pyyaml() -> None:
    """The minimal release runner must not rely on a system ``pip`` binary."""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    job = workflow.split("\n  build-downstream-matrix:\n", maxsplit=1)[1].split(
        "\n  notify-downstream:\n", maxsplit=1
    )[0]

    setup_python = "uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"
    install = "run: python -m pip install pyyaml"

    assert setup_python in job
    assert "python-version: ${{ env.PYTHON_VERSION }}" in job
    assert install in job
    assert job.index(setup_python) < job.index(install)
