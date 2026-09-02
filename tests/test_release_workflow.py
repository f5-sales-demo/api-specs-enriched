"""Regression coverage for automated release pull-request creation."""

from pathlib import Path

WORKFLOW = Path(".github/workflows/sync-and-enrich.yml")


def test_specification_validation_is_release_blocking() -> None:
    """A crashed or failed validator must prevent release publication."""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    validation_step = workflow.split("- name: Validate specifications", maxsplit=1)[1].split(
        "- name: Detect release-worthy changes", maxsplit=1
    )[0]

    assert "continue-on-error" not in validation_step
    assert "run: python -m scripts.validate --dry-run" in validation_step


def test_dry_run_validation_receives_no_f5xc_secrets() -> None:
    """Offline validation must not receive unnecessary tenant credentials."""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    validation_step = workflow.split("- name: Validate specifications", maxsplit=1)[1].split(
        "- name: Detect release-worthy changes", maxsplit=1
    )[0]

    assert "F5XC_API_TOKEN" not in validation_step
    assert "F5XC_API_URL" not in validation_step


def test_secret_consumers_are_bound_to_protected_environments() -> None:
    """Release and downstream credentials stay scoped to their environments."""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    release_job = workflow.split("\n  sync-and-enrich:\n", maxsplit=1)[1].split(
        "\n  deploy-docs:\n", maxsplit=1
    )[0]
    notify_job = workflow.split("\n  notify-downstream:\n", maxsplit=1)[1].split(
        "\n  # ==========================================================================",
        maxsplit=1,
    )[0]

    assert "\n    environment: release\n" in release_job
    assert "\n    environment: downstream-dispatch\n" in notify_job


def test_release_pr_create_is_non_interactive() -> None:
    """The release workflow must supply every prompt-required PR field."""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    command = workflow.split("PR_URL=$(gh pr create", maxsplit=1)[1].split(
        'echo "Created release PR:', maxsplit=1
    )[0]

    assert '--title "chore: release v${VERSION} (${BUMP_TYPE})"' in command
    assert '--body "$PR_BODY"' in command


def test_release_pr_is_linked_before_normal_check_runs() -> None:
    """Generated release PRs must use a native closing relationship without status races."""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    release_job = workflow.split("\n  sync-and-enrich:\n", maxsplit=1)[1].split(
        "\n  deploy-docs:\n", maxsplit=1
    )[0]

    create_issue = "RELEASE_ISSUE_URL=$(gh issue create"
    create_pr = "PR_URL=$(gh pr create"
    assert create_issue in release_job
    assert "Closes #%s" in release_job
    assert release_job.index(create_issue) < release_job.index(create_pr)
    assert "statuses/${PR_HEAD_SHA}" not in release_job
    assert "-f context='Check linked issues'" not in release_job


def test_release_retry_reuses_dedicated_issue() -> None:
    """A retry reuses and, when needed, reopens the exact version issue."""

    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'RELEASE_ISSUE_TITLE="Release v${VERSION}"' in workflow
    assert "gh issue list --state all" in workflow
    assert 'gh issue reopen "$RELEASE_ISSUE"' in workflow


def test_downstream_matrix_sets_up_python_before_installing_pyyaml() -> None:
    """The downstream matrix installs its declared dependencies before using PyYAML."""

    workflow = WORKFLOW.read_text(encoding="utf-8")
    job = workflow.split("\n  build-downstream-matrix:\n", maxsplit=1)[1].split(
        "\n  notify-downstream:\n", maxsplit=1
    )[0]

    setup_python = "uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"
    install = "run: pip install -r requirements.txt"
    verify = 'run: python -c "import yaml; print(yaml.__version__)"'

    assert setup_python in job
    assert "python-version: ${{ env.PYTHON_VERSION }}" in job
    assert "cache: 'pip'" in job
    assert install in job
    assert verify in job
    assert "python -m pip install pyyaml" not in job
    assert job.index(setup_python) < job.index(install) < job.index(verify)


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


def test_release_pr_commits_catalog_and_verifies_version_coherence() -> None:
    """The catalog uploaded as an asset must be the catalog committed at the tag."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    release_job = workflow.split("\n  sync-and-enrich:\n", maxsplit=1)[1].split(
        "\n  deploy-docs:\n", maxsplit=1
    )[0]

    assert "git add -f CHANGELOG.md .github_release release/api-catalog.json" in release_job
    assert release_job.count("python -m scripts.verify_release_version") >= 3
    assert '--receipt "$RECEIPT_JSON_FILE"' in release_job
