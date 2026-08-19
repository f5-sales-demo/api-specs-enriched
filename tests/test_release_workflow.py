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


def test_tag_commit_is_exposed_and_supplied_to_dispatch() -> None:
    """Downstream delivery must identify the immutable annotated-tag commit."""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    release_job = workflow.split("\n  sync-and-enrich:\n", maxsplit=1)[1].split(
        "\n  deploy-docs:\n", maxsplit=1
    )[0]
    notify_job = workflow.split("\n  notify-downstream:\n", maxsplit=1)[1].split(
        "\n  # ==========================================================================",
        maxsplit=1,
    )[0]

    push_tag = 'git push origin "v${VERSION}"'
    tag_target = 'git tag -a "v${VERSION}" "$TARGET_COMMIT"'
    checkout_target = 'git checkout --detach "$TARGET_COMMIT"'
    expose_step = 'echo "target_commit=${TARGET_COMMIT}" >> "$GITHUB_OUTPUT"'
    expose_job = "target_commit: ${{ steps.release.outputs.target_commit }}"
    supply_dispatch = "SOURCE_TARGET_COMMIT: ${{ needs.sync-and-enrich.outputs.target_commit }}"

    assert "id: release" in release_job
    assert tag_target in release_job
    assert release_job.index(tag_target) < release_job.index(push_tag)
    assert release_job.index(push_tag) < release_job.index(checkout_target)
    assert release_job.index(checkout_target) < release_job.index(expose_step)
    assert expose_job in release_job
    assert supply_dispatch in notify_job
    assert notify_job.index(supply_dispatch) < notify_job.index(
        "run: bash scripts/release/dispatch-downstream.sh"
    )


def test_forced_events_always_download_fresh_upstream_specs() -> None:
    """A release-producing run must never use a stale specs/original cache."""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    sync_job = workflow.split("\n  sync-and-enrich:\n", maxsplit=1)[1].split(
        "\n  deploy-docs:\n", maxsplit=1
    )[0]

    assert "Restore specs cache" not in sync_job
    assert "Save specs cache" not in sync_job
    assert "path: specs/original" not in sync_job
    assert "run: python -m scripts.download --force" in sync_job


def test_release_retry_reuses_existing_pr_and_publishes_its_merge() -> None:
    """A timeout retry must preserve the release PR and tag its exact merge."""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    release_job = workflow.split("\n  sync-and-enrich:\n", maxsplit=1)[1].split(
        "\n  deploy-docs:\n", maxsplit=1
    )[0]

    assert "gh pr close" not in release_job
    assert 'gh pr list --state all --head "$BRANCH"' in release_job
    assert "MERGED)" in release_job
    assert 'git checkout --detach "$TARGET_COMMIT"' in release_job


def test_release_pr_wait_tolerates_shared_runner_queueing() -> None:
    """A queued release PR must not time out at the old 30-minute ceiling."""

    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert (
        workflow.count("WAIT_FOR_MERGE_MAX_TOTAL=7200 bash scripts/release/wait-for-merge.sh") == 2
    )
