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
