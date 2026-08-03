# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Structural contracts for asynchronous release production/publication."""

import re
import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PRODUCER = _ROOT / ".github" / "workflows" / "sync-and-enrich.yml"
_PUBLISHER = _ROOT / ".github" / "workflows" / "publish-release.yml"
_PAGES = _ROOT / ".github" / "workflows" / "github-pages-deploy.yml"


def test_producer_always_downloads_and_never_restores_generated_input_caches() -> None:
    workflow = _PRODUCER.read_text()

    assert 'python -m scripts.download --receipt "$PINNED_RECEIPT" --force' in workflow
    assert "Restore specs cache" not in workflow
    assert "Restore enriched output cache" not in workflow
    assert "actions/cache/restore" not in workflow


def test_producer_does_not_wait_for_or_delete_release_pull_requests() -> None:
    workflow = _PRODUCER.read_text()

    assert "wait-for-merge" not in workflow
    assert "gh pr close" not in workflow
    assert 'git push origin ":$BRANCH"' not in workflow
    assert "gh release create" not in workflow
    assert "deploy-docs:" not in workflow
    assert "notify-downstream:" not in workflow
    assert workflow.count("scripts.release.build_release_tree") == 2


def test_producer_fails_closed_and_verifies_every_release_version() -> None:
    workflow = _PRODUCER.read_text()
    builder = (_ROOT / "scripts" / "release" / "build_release_tree.py").read_text()

    assert "Unknown change type - defaulting" not in workflow
    assert "Unsupported change type: $CHANGE_TYPE" in workflow
    assert '"--check-version",' in builder
    assert '"scripts.compile_catalog",' in builder
    assert 'catalog = root / "release" / "api-catalog.json"' in builder


def test_producer_uploads_sanitized_validation_evidence_before_failing() -> None:
    workflow = _PRODUCER.read_text()

    validation = workflow.index("      - name: Validate specifications")
    sanitize = workflow.index("      - name: Sanitize validation evidence")
    upload = workflow.index("      - name: Upload validation evidence")
    enforce = workflow.index("      - name: Enforce validation result")

    assert validation < sanitize < upload < enforce
    assert "if: always()" in workflow[sanitize:upload]
    assert "if: always()" in workflow[upload:enforce]
    assert "if: always()" in workflow[enforce:]
    assert "tee reports/validation-console.log" in workflow[validation:sanitize]
    assert "status=${PIPESTATUS[0]}" in workflow[validation:sanitize]
    assert "python -m scripts.sanitize_validation_evidence" in workflow[sanitize:upload]
    assert "--required reports/validation-console.log" in workflow[sanitize:upload]
    assert "--output-dir reports/sanitized-validation-evidence" in workflow[sanitize:upload]
    assert "steps.sanitize_evidence.outcome == 'success'" in workflow[upload:enforce]
    assert "path: reports/sanitized-validation-evidence/" in workflow[upload:enforce]
    assert "reports/validation-console.log" not in workflow[upload:enforce]
    assert "reports/validation-report.json" not in workflow[upload:enforce]
    assert "reports/validation-report.md" not in workflow[upload:enforce]
    assert 'exit "$VALIDATION_STATUS"' in workflow[enforce:]


def test_repository_dispatch_requires_an_exact_api_specs_receipt() -> None:
    workflow = _PRODUCER.read_text()

    assert workflow.count('payload.get("trigger_source") != "f5-sales-demo/api-specs"') == 2
    assert workflow.count('payload.get("release_receipt")') == 2
    assert "validate_release_receipt" in workflow
    assert workflow.count("require_receipt_progression") == 4
    assert workflow.count('load_release_receipt(Path(".github_release"))') == 3
    assert "--resolve-latest-receipt" in workflow
    assert "Created a pinned upstream release event" in workflow
    assert 'python -m scripts.download --receipt "$PINNED_RECEIPT" --force' in workflow
    assert "::notice::Triggered by upstream release" not in workflow


def test_changelog_uses_validated_upstream_receipt_and_real_asset_name() -> None:
    workflow = _PRODUCER.read_text()

    assert "UPSTREAM_DIGEST=$(jq -er '.asset_digest' .github_release)" in workflow
    assert "f5xc-api-specs-v${VERSION}.zip" in workflow
    assert "Corrected source: f5-sales-demo/api-specs immutable release asset" in workflow
    assert "Source: F5 Distributed Cloud OpenAPI specifications" not in workflow
    assert "x-upstream-timestamp" not in workflow
    assert "x-upstream-etag" not in workflow
    assert "Fixed orphan \\$ref references" not in workflow


def test_changelog_prepend_preserves_the_immediately_previous_release() -> None:
    workflow = _PRODUCER.read_text()
    command = ["sed", "-n", "/^## Version /,$p"]
    existing = """# Changelog

## Version 2.1.208 (2026-08-01)

first entry

## Version 2.1.207 (2026-07-31)

second entry
"""

    result = subprocess.run(command, input=existing, text=True, capture_output=True, check=True)

    assert result.stdout.startswith("## Version 2.1.208")
    assert "## Version 2.1.207" in result.stdout
    assert "sed -n '/^## Version /,$p' CHANGELOG.md" in workflow
    assert "if(++c==2)" not in workflow


def test_release_inputs_use_push_base_and_every_generated_docs_output_is_promoted() -> None:
    workflow = _PRODUCER.read_text()
    detector = (_ROOT / "scripts" / "release" / "detect-release-changes.sh").read_text()
    publisher = (_ROOT / "scripts" / "release" / "reconcile_publication.py").read_text()

    assert "- 'release/README.md'" in workflow
    assert 'RELEASE_README="release/README.md"' in detector
    assert "DETECT_RELEASE_BASE:" in workflow
    assert "github.event.before" in workflow
    assert 'API_REFERENCE_DIR="docs/api-reference"' in detector
    assert 'OPENAPI_CONFIG="docs/openapi-specs-config.json"' in detector
    assert "docs/openapi-specs-config.json" in workflow
    assert (
        'openapi_config = root / "docs" / "openapi-specs-config.json"'
        in (_ROOT / "scripts" / "release" / "build_release_tree.py").read_text()
    )
    assert 'Path("docs/openapi-specs-config.json")' in publisher


def test_producer_monitor_measures_expected_and_unexpected_skips() -> None:
    workflow = _PRODUCER.read_text()

    monitor = workflow[workflow.index("  monitor-failures:") :]
    assert "EXPECTED_SKIPPED_JOBS:" in monitor
    assert "needs.check-updates.outputs.has_updates != 'true'" in monitor
    assert "JOB_SYNC_ENRICH: ${{ needs.sync-and-enrich.result }}" in monitor
    assert "steps.check_failure.outputs.has_failure" not in monitor


def test_required_contract_gate_builds_isolated_candidate_and_binds_release_pr_bytes() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "tests.yml").read_text()

    assert "scripts.release.verify_reproducible_build" in workflow
    assert "--input-dir specs/original" in workflow
    assert '--first-root "$RUNNER_TEMP/api-specs-candidate"' in workflow
    assert '--second-root "$RUNNER_TEMP/api-specs-rebuild"' in workflow
    assert '"$RUNNER_TEMP/api-specs-candidate/docs/specifications/api"' in workflow
    assert '[[ "$HEAD_REF" =~ ^release/v[0-9]+\\.[0-9]+\\.[0-9]+$ ]]' in workflow
    assert 'verify_args+=(--committed-root "$GITHUB_WORKSPACE")' in workflow
    assert "Release branch is not an exact semantic release branch" in workflow
    assert "Verify the committed specs match a clean rebuild" not in workflow
    assert "make pipeline PYTHON=python" not in workflow


def test_required_source_pr_gate_enforces_release_branch_generated_output_ownership() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "tests.yml").read_text()
    pytest_job = workflow[workflow.index("  pytest:") : workflow.index("  contract-diff:")]

    assert "Verify generated-output ownership" in pytest_job
    assert "scripts/release/verify-generated-ownership.sh" in pytest_job
    assert '--base "origin/$BASE_REF"' in pytest_job
    assert "--head HEAD" in pytest_job
    assert '--branch "$HEAD_REF"' in pytest_job


def test_contract_diff_runs_for_semantic_release_prs_too() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "tests.yml").read_text()
    contract_job = workflow[workflow.index("  contract-diff:") :]

    assert "startsWith(github.head_ref, 'release/')" not in contract_job
    assert "Run contract-diff gate" in contract_job


def test_publication_is_reconcilable_after_the_producer_job_ends() -> None:
    workflow = _PUBLISHER.read_text()

    assert "workflow_dispatch:" in workflow
    assert "schedule:" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "scripts.release.reconcile_publication" in workflow
    assert "scripts.release.verify_pages" in workflow
    assert "Verify Published Pages Artifact" in workflow
    assert "publish_needed" in workflow
    assert "audit_needed" in workflow
    assert "needs_post_publish" not in workflow
    assert "- 'docs/en/**'" in workflow
    assert "- 'docs/specifications/**'" in workflow
    assert "- 'docs/api-reference/**'" in workflow
    assert "- 'docs/**'" not in workflow
    assert "- 'scripts/release/**'" in workflow
    assert "- 'scripts/utils/github_release.py'" in workflow
    assert "- 'scripts/stamp_release_version.py'" in workflow
    assert "- 'scripts/compile_catalog.py'" in workflow
    assert "- 'pyproject.toml'" in workflow
    assert "- 'uv.lock'" in workflow
    assert "docs_commit: ${{ steps.publish.outputs.docs_commit }}" in workflow
    assert "target-commit: ${{ needs.reconcile.outputs.docs_commit }}" in workflow
    assert "scripts.release.dispatch_downstreams" in workflow
    assert "delivery_id" not in workflow, "delivery payload belongs in the tested dispatcher"
    assert "peter-evans/repository-dispatch" not in workflow


def test_publisher_only_verifies_preprovisioned_immutable_releases() -> None:
    workflow = _PUBLISHER.read_text()
    verify = workflow.index('gh api "repos/${REPOSITORY}/immutable-releases"')
    reconcile = workflow.index("python -m scripts.release.reconcile_publication")
    assert verify < reconcile
    assert 'gh api --method PUT "repos/${REPOSITORY}/immutable-releases"' not in workflow
    assert 'gh api --method DELETE "repos/${REPOSITORY}/immutable-releases"' not in workflow
    verification_step = workflow[workflow.rindex("Verify immutable releases are pre-provisioned") :]
    assert "GH_TOKEN: ${{ secrets.REPO_SETTINGS_TOKEN }}" in verification_step
    assert ".enabled == true" in workflow


def test_post_publication_jobs_use_the_verified_publisher_commit_and_permissions() -> None:
    workflow = _PUBLISHER.read_text()

    assert workflow.count("ref: ${{ needs.reconcile.outputs.docs_commit }}") == 4
    notify = workflow[workflow.index("  notify-downstream:") : workflow.index("  mark-complete:")]
    assert (
        "contents: write # checkout the publisher and append verified delivery receipts" in notify
    )
    assert "Checkout immutable publisher implementation" in notify
    assert "needs.reconcile.outputs.publish_needed == 'true'" in notify
    audit = workflow[workflow.index("  audit-downstream:") : workflow.index("  mark-complete:")]
    assert "--verify-only" in audit
    assert "needs.reconcile.outputs.audit_needed == 'true'" in audit
    assert "needs.reconcile.outputs.publish_needed == 'false'" in audit
    assert "needs.notify-downstream.result == 'success'" in audit
    assert "contents: read" in audit
    assert "contents: write" not in audit
    assert "failure_detail: ${{ steps.audit.outputs.failure_detail }}" in audit
    verify = workflow[workflow.index("  verify-pages:") : workflow.index("  notify-downstream:")]
    assert "actions: read # download the exact github-pages artifact produced by this run" in verify
    assert "contents: read" in verify
    assert "\n      packages:" not in verify
    assert "\n      pages:" not in verify
    assert "\n      id-token:" not in verify
    assert "\n      issues:" not in verify


def test_pages_can_only_be_called_after_release_verification() -> None:
    workflow = _PAGES.read_text()

    assert "workflow_call:" in workflow
    assert "  push:" not in workflow
    assert "workflow_dispatch:" not in workflow
    assert "target-commit:" in workflow
    assert "content-ref: ${{ inputs.target-commit }}" in workflow
    governed = re.search(
        r"uses: f5-sales-demo/docs-control/\.github/workflows/"
        r"github-pages-deploy\.yml@([0-9a-f]{40})",
        workflow,
    )
    assert governed is not None
    assert "docs-builder@sha256:905d2398" in workflow
    assert "@main" not in workflow
    assert "docs-builder:latest" not in workflow


def test_pages_and_failure_monitor_are_bound_to_measured_results() -> None:
    publisher = _PUBLISHER.read_text()

    deploy = publisher[publisher.index("  deploy-docs:") : publisher.index("  verify-pages:")]
    verify = publisher[publisher.index("  verify-pages:") : publisher.index("  notify-downstream:")]
    for job in (deploy, verify):
        assert "needs.reconcile.outputs.publish_needed == 'true'" in job
        assert "needs.reconcile.outputs.audit_needed == 'true'" in job
    assert "python -m scripts.release.verify_pages" in verify
    assert '--repository "$GITHUB_REPOSITORY"' in verify
    assert '--run-id "$GITHUB_RUN_ID"' in verify
    assert '--target-revision "$TARGET_REVISION"' in verify
    assert "--docs-root docs/en" in verify
    assert "--api-reference-root docs/api-reference" in verify
    assert "--specs-root docs/specifications/api" in verify
    assert "--openapi-config docs/openapi-specs-config.json" in verify
    assert "PAGES_BASE_URL:" in verify
    assert "curl " not in verify
    assert "sha256sum" not in verify
    for result in (
        "JOB_RECONCILE",
        "JOB_DEPLOY_DOCS",
        "JOB_VERIFY_PAGES",
        "JOB_NOTIFY_DOWNSTREAM",
        "JOB_AUDIT_DOWNSTREAM",
        "JOB_MARK_COMPLETE",
    ):
        assert f"{result}: ${{{{ needs." in publisher
    assert "needs.reconcile.result == 'success'" in publisher
    assert "needs.reconcile.outputs.publish_needed == 'false'" in publisher
    assert "needs.reconcile.outputs.audit_needed == 'true'" in publisher
    assert "'JOB_DEPLOY_DOCS,JOB_VERIFY_PAGES,JOB_NOTIFY_DOWNSTREAM,JOB_MARK_COMPLETE'" in publisher
    assert "JOB_AUDIT_DOWNSTREAM: ${{ needs.audit-downstream.result }}" in publisher
    assert "AUDIT_FAILURE_DETAIL: ${{ needs.audit-downstream.outputs.failure_detail }}" in publisher
    assert "EXPECTED_SKIPPED_JOBS:" in publisher


def test_automated_release_pull_request_creates_and_closes_an_exact_issue() -> None:
    workflow = _PRODUCER.read_text()

    assert 'ISSUE_TITLE="release: publish v${VERSION}"' in workflow
    assert "gh issue list --state all" in workflow
    assert "gh issue create" in workflow
    assert '"Closes #${RELEASE_ISSUE}"' in workflow
    assert '--body "$PR_BODY"' in workflow
    assert "release/* branches are excluded from linked-issue check" not in workflow


def test_every_workflow_installs_the_committed_python_lock_in_frozen_mode() -> None:
    workflows = (_PRODUCER, _PUBLISHER, _ROOT / ".github" / "workflows" / "tests.yml")
    setup_uv = "astral-sh/setup-uv@c771a70e6277c0a99b617c7a806ffedaca235ff9"

    for path in workflows:
        workflow = path.read_text()
        assert "pip install -r requirements.txt" not in workflow
        assert "python-version: '3.13'" not in workflow
        assert workflow.count(setup_uv) == workflow.count("actions/setup-python@")
        assert workflow.count("version: '0.12.1'") == workflow.count(setup_uv)
        assert workflow.count("uv sync --frozen") >= workflow.count(setup_uv)
        if "PYTHON_VERSION:" in workflow:
            assert "PYTHON_VERSION: '3.13.14'" in workflow
            assert workflow.count("python-version: ${{ env.PYTHON_VERSION }}") == workflow.count(
                "actions/setup-python@"
            )
        else:
            assert workflow.count("python-version: '3.13.14'") == workflow.count(
                "actions/setup-python@"
            )
    producer = _PRODUCER.read_text()
    assert "- 'pyproject.toml'" in producer
    assert "- 'uv.lock'" in producer

    makefile = (_ROOT / "Makefile").read_text()
    assert "$(UV) sync --frozen --extra dev" in makefile
    assert "install -r requirements.txt" not in makefile
    assert "PYTHON_VERSION := 3.13.14" in makefile

    project = (_ROOT / "pyproject.toml").read_text()
    assert 'requires-python = "==3.13.14"' in project
    assert 'target-version = "py313"' in project
    assert 'python_version = "3.13"' in project
    assert 'requires = ["setuptools==83.0.0", "wheel==0.47.0"]' in project
    assert not (_ROOT / "requirements.txt").exists()


def test_production_and_verification_use_one_exact_biome_and_compare_two_builds() -> None:
    producer = _PRODUCER.read_text()
    verifier = (_ROOT / ".github" / "workflows" / "tests.yml").read_text()
    setup_biome = "biomejs/setup-biome@4c91541eaada48f67d7dbd7833600ce162b68f51"

    assert "version: latest" not in producer
    assert producer.count(setup_biome) == 1
    assert verifier.count(setup_biome) == 1
    assert producer.count("version: 2.5.6") == 1
    assert verifier.count("version: 2.5.6") == 1
    assert "API_SPECS_SKIP_BIOME" not in verifier
    assert "python -m scripts.release.verify_reproducible_build" in verifier
    assert '--first-root "$RUNNER_TEMP/api-specs-candidate"' in verifier
    assert '--second-root "$RUNNER_TEMP/api-specs-rebuild"' in verifier
    assert '--first-python "$RUNNER_TEMP/api-specs-build-env-1/bin/python"' in verifier
    assert '--second-python "$RUNNER_TEMP/api-specs-build-env-2/bin/python"' in verifier
    assert verifier.count("uv sync --frozen --no-cache") == 2
    assert "reproducibility-manifest.json" in verifier


def test_node_and_spectral_are_exact_and_lock_installed_without_java() -> None:
    producer = _PRODUCER.read_text()
    verifier = (_ROOT / ".github" / "workflows" / "tests.yml").read_text()
    package = (_ROOT / "package.json").read_text()
    lock = (_ROOT / "package-lock.json").read_text()

    for workflow in (producer, verifier):
        assert "node-version: '22.23.2'" in workflow
        assert "npm ci --ignore-scripts --no-audit --no-fund" in workflow
        assert "npm install -g" not in workflow
    assert "default-jre" not in producer
    assert "Install Java" not in producer
    assert '"node": "22.23.2"' in package
    assert '"npm": "10.9.8"' in package
    assert '"@stoplight/spectral-cli": "6.16.3"' in package
    assert '"@rollup/plugin-commonjs": "29.0.3"' in package
    assert '"node_modules/@stoplight/spectral-cli"' in lock


def test_every_byte_builder_uses_the_same_digest_pinned_container() -> None:
    image = (
        "python:3.13.14-bookworm@"
        "sha256:353cf2106d143e1d28f5d7c10c5f5c0387085bba22ef0f7f7e52c2c330fb1779"
    )
    producer = _PRODUCER.read_text()
    verifier = (_ROOT / ".github" / "workflows" / "tests.yml").read_text()
    publisher = _PUBLISHER.read_text()

    assert producer.count(f"image: {image}") == 1
    assert verifier.count(f"image: {image}") == 1
    assert publisher.count(f"image: {image}") == 1
    for workflow in (producer, verifier, publisher):
        assert f"BUILDER_IMAGE: {image}" in workflow
        assert "runs-on: ubuntu-latest" in workflow
        assert "shell: bash" in workflow


def test_direct_actions_in_owned_production_workflows_are_commit_pinned() -> None:
    for path in (_PRODUCER, _ROOT / ".github" / "workflows" / "tests.yml"):
        action_refs = re.findall(r"^\s*uses:\s+([^\s@]+)@([^\s#]+)", path.read_text(), re.MULTILINE)
        assert action_refs
        for action, ref in action_refs:
            assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{path}: {action}@{ref} is mutable"


def test_retired_merge_waiter_is_deleted() -> None:
    assert not (_ROOT / "scripts" / "release" / "wait-for-merge.sh").exists()
    assert not (_ROOT / "tests" / "shell" / "test_wait_for_merge.py").exists()
