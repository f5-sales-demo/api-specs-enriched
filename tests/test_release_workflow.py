"""Regression coverage for the evidence-selected release handoff workflow."""

from pathlib import Path

WORKFLOW = Path(".github/workflows/sync-and-enrich.yml")
COORDINATOR = Path("scripts/release/coordinator.sh")
BUILDER = Path("scripts/release/build-package.sh")
PUBLISHER = Path("scripts/release/publish-package.sh")


def _workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _job(name: str, following: str) -> str:
    workflow = _workflow()
    return workflow.split(f"\n  {name}:\n", maxsplit=1)[1].split(f"\n  {following}:\n", maxsplit=1)[
        0
    ]


def test_specification_validation_is_release_blocking_and_secretless() -> None:
    workflow = _workflow()
    validation = workflow.split("- name: Validate specifications", maxsplit=1)[1].split(
        "- name: Detect release-worthy changes", maxsplit=1
    )[0]

    assert "continue-on-error" not in validation
    assert "runner-profile --name validation" in validation
    assert "python -m scripts.validate --dry-run" in validation
    assert "F5XC_API_TOKEN" not in validation
    assert "F5XC_API_URL" not in validation


def test_enrichment_uses_qualified_compute_route_and_eight_workers() -> None:
    job = _job("sync-and-enrich", "release-coordinator")

    assert "name: Sync and Enrich Specifications" in job
    assert "runs-on: api-specs-enriched-compute" in job
    assert "permissions:\n      contents: read" in job
    assert "environment: release" not in job
    assert "VERSION_BUMP_PAT" not in job
    assert "persist-credentials: false" in job
    assert "python -m scripts.pipeline --workers 8" in job


def test_release_merge_trigger_cannot_cancel_active_handoff() -> None:
    assert "cancel-in-progress: false" in _workflow()


def test_compute_jobs_have_no_release_authority() -> None:
    workflow = _workflow()
    sync = _job("sync-and-enrich", "release-coordinator")
    build = _job("build-release-package", "release-publisher")

    for job in (sync, build):
        assert "runs-on: api-specs-enriched-compute" in job
        assert "permissions:\n      contents: read" in job
        assert "environment: release" not in job
        assert "VERSION_BUMP_PAT" not in job
        assert "persist-credentials: false" in job
    assert "GH_TOKEN:" not in build

    assert (
        "runs-on: managed-socketless"
        in workflow.split("\n  check-updates:\n", maxsplit=1)[1].split(
            "\n  sync-and-enrich:\n", maxsplit=1
        )[0]
    )


def test_candidate_handoff_is_strict_and_complete() -> None:
    sync = _job("sync-and-enrich", "release-coordinator")

    assert "python -m scripts.release_handoff candidate-create" in sync
    for generated_root in (
        "CHANGELOG.md",
        ".github_release",
        "docs/specifications/api",
        "docs/api-reference",
        "release/api-catalog.json",
        "release/upstream-contract-removals.json",
        "release/upstream-contract-removals.md",
    ):
        assert f"--path {generated_root}" in sync
    assert "include-hidden-files: true" in sync
    assert "candidate_manifest_digest:" in sync
    assert "upstream_digest:" in sync
    assert "pipeline_fingerprint:" in sync


def test_release_coordinator_owns_pr_and_tag_operations() -> None:
    job = _job("release-coordinator", "build-release-package")
    script = COORDINATOR.read_text(encoding="utf-8")

    assert "runs-on: managed-socketless" in job
    assert "environment: release" in job
    assert "contents: write" in job
    assert "statuses: write" in job
    assert "VERSION_BUMP_PAT" in job
    assert "run: bash scripts/release/coordinator.sh" in job
    assert "gh pr create --base main --head" in script
    assert '--title "$expected_title"' in script
    assert '--body "' in script
    assert "ownership_marker=" in script
    assert "source=${SOURCE_COMMIT}" in script
    assert "candidate=${candidate_digest}" in script
    assert 'git push origin "v${VERSION}"' in script


def test_release_retry_is_idempotent_and_rejects_ambiguous_or_foreign_prs() -> None:
    script = COORDINATOR.read_text(encoding="utf-8")

    assert "gh pr close" not in script
    assert 'gh pr list --state all --head "$branch"' in script
    assert "Multiple release PRs claim" in script
    assert "isCrossRepository" in script
    assert "foreign or has unexpected ownership metadata" in script
    assert "MERGED)" in script
    assert "OPEN)" in script
    assert "CLOSED)" in script
    assert "closed without merging; refusing to supersede it" in script
    assert script.count("WAIT_FOR_MERGE_MAX_TOTAL=7200 bash scripts/release/wait-for-merge.sh") == 2


def test_tag_is_bound_to_the_exact_merged_commit() -> None:
    workflow = _workflow()
    script = COORDINATOR.read_text(encoding="utf-8")
    notify = _job("notify-downstream", "monitor-failures")

    assert 'git merge-base --is-ancestor "$target_commit" origin/main' in script
    assert 'git tag -a "v${VERSION}" "$target_commit"' in script
    assert 'git push origin "v${VERSION}"' in script
    assert "printf 'target_commit=%s\\n'" in script
    assert "target_commit: ${{ steps.release.outputs.target_commit }}" in workflow
    assert "SOURCE_TARGET_COMMIT: ${{ needs.release-coordinator.outputs.target_commit }}" in notify


def test_package_build_uses_exact_commit_and_deterministic_archive() -> None:
    job = _job("build-release-package", "release-publisher")
    script = BUILDER.read_text(encoding="utf-8")

    assert "ref: ${{ needs.release-coordinator.outputs.target_commit }}" in job
    assert "Verify merged candidate identity" in job
    assert "python -m scripts.release_handoff candidate-verify" in job
    assert "python -m scripts.release_handoff archive" in script
    assert 'commit_epoch=$(git show -s --format=%ct "$RELEASE_COMMIT")' in script
    assert '[ "$(git rev-parse HEAD)" = "$RELEASE_COMMIT" ]' in script
    assert "python -m scripts.verify_release_version" in script
    assert "python -m scripts.release_handoff package-create" in job
    assert "artifact_digest: ${{ steps.payload.outputs.artifact-digest }}" in job


def test_publisher_verifies_every_identity_before_github_write() -> None:
    job = _job("release-publisher", "deploy-docs")
    script = PUBLISHER.read_text(encoding="utf-8")

    assert "runs-on: managed-socketless" in job
    assert "environment: release" in job
    assert "actions: read" in job
    assert "contents: write" in job
    assert "server_digest=$(gh api" in job
    assert '[ "$server_digest" = "$ARTIFACT_DIGEST" ]' in job
    verify = "python -m scripts.release_handoff package-verify"
    publish = 'gh release create "v$VERSION"'
    assert verify in script
    assert publish in script
    assert script.index(verify) < script.index("gh release view") < script.index(publish)
    assert '[ "$(git rev-parse HEAD)" = "$RELEASE_COMMIT" ]' in script
    assert '[ "$(git rev-list -n 1 "v${VERSION}")" = "$RELEASE_COMMIT" ]' in script


def test_no_change_path_skips_every_release_stage() -> None:
    workflow = _workflow()
    for job_name in (
        "release-coordinator",
        "build-release-package",
        "release-publisher",
        "deploy-docs",
        "build-downstream-matrix",
    ):
        start = workflow.split(f"\n  {job_name}:\n", maxsplit=1)[1]
        header = start.split("\n    steps:", maxsplit=1)[0]
        assert "if: needs.sync-and-enrich.outputs.has_changes == 'true'" in header


def test_failure_monitor_covers_all_new_stages() -> None:
    monitor = _workflow().split("\n  monitor-failures:\n", maxsplit=1)[1]

    for dependency in (
        "release-coordinator",
        "build-release-package",
        "release-publisher",
        "deploy-docs",
    ):
        assert dependency in monitor
    for result in (
        "RELEASE_COORDINATOR_RESULT",
        "BUILD_RELEASE_PACKAGE_RESULT",
        "RELEASE_PUBLISHER_RESULT",
        "DEPLOY_DOCS_RESULT",
    ):
        assert f'[ "${result}" = "failure" ]' in monitor


def test_forced_events_always_download_fresh_upstream_specs() -> None:
    sync = _job("sync-and-enrich", "release-coordinator")

    assert "Restore specs cache" not in sync
    assert "Save specs cache" not in sync
    assert "path: specs/original" not in sync
    assert "run: python -m scripts.download --force" in sync


def test_downstream_matrix_installs_declared_dependencies_before_use() -> None:
    job = _job("build-downstream-matrix", "notify-downstream")
    setup = "uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"
    install = "run: pip install -r requirements.txt"
    verify = 'run: python -c "import yaml; print(yaml.__version__)"'

    assert setup in job
    assert "python-version: ${{ env.PYTHON_VERSION }}" in job
    assert "cache: 'pip'" in job
    assert install in job
    assert verify in job
    assert job.index(setup) < job.index(install) < job.index(verify)
