# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for scripts/release/detect-release-changes.sh.

Covers the release gate that decides whether a sync-and-enrich run publishes
an enriched release. The previous implementation lived inline in
`.github/workflows/sync-and-enrich.yml` and asked "did the *previous commit*
change the inputs?" via `git diff HEAD~1 HEAD -- specs/original/ ...`. Two
independent defects made an upstream release incapable of ever producing an
enriched release (api-specs-enriched#1094):

1. `specs/original/` is gitignored with zero tracked files, so a diff on it
   can never report anything.
2. `.github_release` is tracked, but the download step rewrites it in the
   *working tree*, which a `HEAD~1 HEAD` comparison of two committed
   revisions cannot see.

The replacement asks the only correct question -- "does what we just
generated differ from what is committed?" -- and lives in a script so these
tests can pin it. Fixtures are throwaway git repos; the real repository's git
state is never mutated.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_RELATIVE = "scripts/release/detect-release-changes.sh"
_SCRIPT = _PROJECT_ROOT / _SCRIPT_RELATIVE
_WORKFLOW = _PROJECT_ROOT / ".github/workflows/sync-and-enrich.yml"

_OUTPUT_DIR = "docs/specifications/api"
_INDEX = f"{_OUTPUT_DIR}/index.json"
_API_REFERENCE_DIR = "docs/api-reference"
_OPENAPI_CONFIG = "docs/openapi-specs-config.json"
_UPSTREAM_STATE = ".github_release"
_CATALOG = "release/api-catalog.json"
_VALIDATION = f"{_OUTPUT_DIR}/validation.json"
_RELEASE_README = "release/README.md"

# The paths the detector is allowed to treat as a change signal. Every one of
# them must have at least one file tracked in the real repository, otherwise
# `git diff` on it is structurally silent -- defect (1) above.
_SIGNAL_PATHS = (
    _OUTPUT_DIR,
    _API_REFERENCE_DIR,
    _OPENAPI_CONFIG,
    _UPSTREAM_STATE,
    _CATALOG,
    _RELEASE_README,
)

# `.gitignore` matches both signal paths; they are tracked only because the
# release commit force-adds them. Fixtures reproduce that exact shape so the
# tests exercise reality rather than a friendlier variant.
_FIXTURE_GITIGNORE = f"{_OUTPUT_DIR}/\n{_UPSTREAM_STATE}\nspecs/\n"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _index_json(*, version: str = "2.1.0", timestamp: str = "2026-01-01T00:00:00+00:00") -> str:
    return (
        json.dumps(
            {
                "version": version,
                "timestamp": timestamp,
                "specifications": [{"domain": "network", "file": "network.json"}],
            },
            indent=2,
        )
        + "\n"
    )


def _release_state(
    *,
    tag: str = "v2026.01.01-1",
    downloaded_at: str = "2026-01-01T00:00:00Z",
    digest: str = "sha256:" + "a" * 64,
) -> str:
    return (
        json.dumps(
            {
                "version": tag.lstrip("v"),
                "tag_name": tag,
                "published_at": "2026-01-01T00:00:00Z",
                "asset_name": f"api-specs-{tag}.zip",
                "asset_size": 12345,
                "asset_digest": digest,
                "downloaded_at": downloaded_at,
            },
            indent=2,
        )
        + "\n"
    )


def _write_fake_gh(tmp_path: Path, *, release_lines: int) -> Path:
    """Fake `gh` implementing only `gh release list --limit 1`.

    `release_lines=0` reproduces a repository with no releases yet, which the
    detector must treat as "force an initial release".
    """
    payload = "".join(f"v1.0.{i}\tLatest\tv1.0.{i}\t2026-01-01\n" for i in range(release_lines))
    listing = tmp_path / "fake_gh_releases"
    listing.write_text(payload)

    fake_gh = tmp_path / "fake_gh.sh"
    fake_gh.write_text(
        f"""#!/usr/bin/env bash
set -e
if [ "$1" = "release" ] && [ "$2" = "list" ]; then
  cat {listing.as_posix()!r}
  exit 0
fi
echo "fake gh: unsupported invocation: $*" >&2
exit 1
"""
    )
    fake_gh.chmod(fake_gh.stat().st_mode | stat.S_IEXEC)
    return fake_gh


def _setup_repo(
    tmp_path: Path,
    *,
    with_gitignore: bool = True,
    track_output: bool = True,
    track_catalog: bool = True,
) -> Path:
    """Build a throwaway repo shaped like the runner's checkout.

    Two commits: a base commit holding the generated output, then a commit
    that touches only `README.md`. That second commit is what makes the old
    `HEAD~1 HEAD` comparison silent -- the previous commit changed nothing the
    old check looked at, even though the working tree may hold brand-new
    generated output.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")

    (repo / _OUTPUT_DIR).mkdir(parents=True)
    (repo / _INDEX).write_text(_index_json())
    (repo / f"{_OUTPUT_DIR}/openapi.json").write_text(
        json.dumps({"openapi": "3.0.3", "paths": {"/a": {}}}, indent=2) + "\n"
    )
    (repo / f"{_OUTPUT_DIR}/network.json").write_text(
        json.dumps({"openapi": "3.0.3", "info": {"version": "2.1.0"}}, indent=2) + "\n"
    )
    (repo / _UPSTREAM_STATE).write_text(_release_state())
    (repo / _CATALOG).parent.mkdir(parents=True)
    (repo / _CATALOG).write_text(
        json.dumps({"version": "2.1.0", "categories": []}, indent=2) + "\n"
    )
    (repo / _VALIDATION).write_text(
        json.dumps({"version": "2.1.0", "schema": "validation-report"}, indent=2) + "\n"
    )
    (repo / _RELEASE_README).write_text("API release {VERSION}\n")
    (repo / _API_REFERENCE_DIR).mkdir(parents=True)
    (repo / _API_REFERENCE_DIR / "network-api.mdx").write_text("generated network docs\n")
    (repo / _OPENAPI_CONFIG).write_text(
        json.dumps([{"base": "api-reference/network", "schema": "network.json"}], indent=2) + "\n"
    )
    (repo / "README.md").write_text("base\n")
    (repo / "specs" / "original").mkdir(parents=True)
    (repo / "specs" / "original" / "raw.json").write_text("{}\n")

    base_files = ["README.md"]
    if with_gitignore:
        (repo / ".gitignore").write_text(_FIXTURE_GITIGNORE)
        base_files.append(".gitignore")

    _git(repo, "add", *base_files)
    if track_output:
        generated_paths = [
            _OUTPUT_DIR,
            _API_REFERENCE_DIR,
            _OPENAPI_CONFIG,
            _UPSTREAM_STATE,
            _RELEASE_README,
        ]
        if track_catalog:
            generated_paths.append(_CATALOG)
        _git(
            repo,
            "add",
            "-f",
            *generated_paths,
        )
    _git(repo, "commit", "-q", "-m", "init")

    # A follow-up commit that touches nothing the detector cares about.
    (repo / "README.md").write_text("second commit\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "docs: unrelated")
    return repo


def _run(
    repo: Path,
    fake_gh: Path,
    *,
    base_ref: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    """Run the detector inside ``repo`` and parse its ``$GITHUB_OUTPUT``."""
    script_copy = repo / _SCRIPT_RELATIVE
    script_copy.parent.mkdir(parents=True, exist_ok=True)
    script_copy.write_text(_SCRIPT.read_text())
    script_copy.chmod(script_copy.stat().st_mode | stat.S_IEXEC)

    output_file = repo / "github_output"
    output_file.write_text("")
    env = {
        **os.environ,
        "GITHUB_OUTPUT": str(output_file),
        "DETECT_RELEASE_GH": str(fake_gh),
    }
    if base_ref is not None:
        env["DETECT_RELEASE_BASE"] = base_ref
    proc = subprocess.run(
        ["bash", _SCRIPT_RELATIVE],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    outputs = {}
    for line in output_file.read_text().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            outputs[key] = value
    return proc, outputs


# ---------------------------------------------------------------------------
# AC1 -- a working-tree output change is detected even though HEAD~1..HEAD
#        touched nothing the old check looked at.
# ---------------------------------------------------------------------------


def test_worktree_output_change_is_detected(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    # What the enrichment pipeline just produced: renamed property, new
    # annotation -- exactly the api-specs v2026.07.24-2 wire-name change. The
    # version stamp moves too (the pipeline writes the previous tag), which
    # must not mask the real content change.
    (repo / f"{_OUTPUT_DIR}/network.json").write_text(
        json.dumps(
            {
                "openapi": "3.0.3",
                "info": {"version": "2.0.9"},
                "blocked_service": {"x-f5xc-wire-name": "blocked_sevice"},
            },
            indent=2,
        )
        + "\n"
    )

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert outputs["has_changes"] == "true", proc.stdout
    assert outputs["change_type"] == "pipeline", proc.stdout


def test_legacy_head_tilde_comparison_is_blind_to_that_change(tmp_path: Path) -> None:
    """Pin the defect the fix removes.

    In the very fixture where `test_worktree_output_change_is_detected`
    demands `has_changes=true`, the retired `HEAD~1 HEAD` comparison reports
    no change at all. This test exists so a regression back to input-guessing
    across commit history is caught as a behavioural difference, not just a
    diff review.
    """
    repo = _setup_repo(tmp_path)
    (repo / f"{_OUTPUT_DIR}/network.json").write_text('{"changed": true}\n')

    legacy = subprocess.run(
        [
            "git",
            "diff",
            "--quiet",
            "HEAD~1",
            "HEAD",
            "--",
            "specs/original/",
            ".github_release",
            "scripts/",
            "config/",
            "requirements.txt",
            ".github/workflows/sync-and-enrich.yml",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert legacy.returncode == 0, "expected the legacy check to see nothing"

    current = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", _OUTPUT_DIR, _UPSTREAM_STATE],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert current.returncode == 1, "expected the output-driven check to see the change"


# ---------------------------------------------------------------------------
# AC2 -- nothing changed means no release.
# ---------------------------------------------------------------------------


def test_identical_worktree_reports_no_changes(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert outputs["has_changes"] == "false", proc.stdout


def test_volatile_generator_timestamps_alone_are_not_a_change(tmp_path: Path) -> None:
    """`index.json` carries a wall-clock `timestamp` from every pipeline run.

    Treating that as a content change would publish a release for every run
    that regenerates byte-identical specs -- the "empty release" the gate
    exists to prevent.
    """
    repo = _setup_repo(tmp_path)
    (repo / _INDEX).write_text(_index_json(timestamp="2026-07-25T23:59:59.999999+00:00"))

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert outputs["has_changes"] == "false", proc.stdout


def test_version_stamp_alone_is_not_a_change(tmp_path: Path) -> None:
    """The release process assigns `version` *after* this gate runs.

    The pipeline writes the previous tag and the committed files carry the tag
    they shipped as, so a cache-restored tree always differs by exactly one
    bump. Counting that would make every run publish a content-free release.
    """
    repo = _setup_repo(tmp_path)
    (repo / _INDEX).write_text(_index_json(version="2.0.9"))

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert outputs["has_changes"] == "false", proc.stdout


def test_domain_spec_info_version_stamp_alone_is_not_a_change(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / f"{_OUTPUT_DIR}/network.json").write_text(
        json.dumps({"openapi": "3.0.3", "info": {"version": "2.0.9"}}, indent=2) + "\n"
    )

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert outputs["has_changes"] == "false", proc.stdout


def test_validation_schema_format_version_is_a_release_change(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / _VALIDATION).write_text(
        json.dumps({"version": "3.0.0", "schema": "validation-report"}, indent=2) + "\n"
    )

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert outputs["has_changes"] == "true", proc.stdout
    assert outputs["change_type"] == "pipeline", proc.stdout


def test_real_content_change_in_index_is_still_a_change(tmp_path: Path) -> None:
    """Normalising the volatile timestamp must not blind the detector."""
    repo = _setup_repo(tmp_path)
    payload = json.loads((repo / _INDEX).read_text())
    payload["timestamp"] = "2026-07-25T23:59:59.999999+00:00"
    payload["specifications"].append({"domain": "new_domain", "file": "new_domain.json"})
    (repo / _INDEX).write_text(json.dumps(payload, indent=2) + "\n")

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert outputs["has_changes"] == "true", proc.stdout


def test_catalog_only_content_change_is_detected(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / _CATALOG).write_text(
        json.dumps(
            {
                "version": "2.1.0",
                "categories": [{"name": "new-category", "operations": []}],
            },
            indent=2,
        )
        + "\n"
    )

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert outputs["has_changes"] == "true", proc.stdout
    assert outputs["change_type"] == "pipeline", proc.stdout


def test_catalog_generated_when_absent_at_base_is_a_release_addition(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path, track_catalog=False)
    assert not _git(repo, "ls-tree", "-r", "--name-only", "HEAD", "--", _CATALOG).strip()
    assert (repo / _CATALOG).is_file()

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert "was added" in proc.stdout
    assert outputs == {"has_changes": "true", "change_type": "pipeline"}


def test_release_readme_change_is_detected(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / _RELEASE_README).write_text("Changed package documentation for {VERSION}\n")

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert outputs["has_changes"] == "true", proc.stdout
    assert outputs["change_type"] == "pipeline", proc.stdout


def test_committed_release_readme_change_is_compared_to_push_base(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").strip()
    (repo / _RELEASE_README).write_text("Committed package documentation for {VERSION}\n")
    _git(repo, "add", _RELEASE_README)
    _git(repo, "commit", "-q", "-m", "docs: update release package template")

    proc, outputs = _run(
        repo,
        _write_fake_gh(tmp_path, release_lines=1),
        base_ref=base,
    )

    assert proc.returncode == 0, proc.stderr
    assert outputs == {"has_changes": "true", "change_type": "pipeline"}


def test_generated_api_reference_change_is_detected(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / _API_REFERENCE_DIR / "network-api.mdx").write_text("new generated docs\n")

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert outputs == {"has_changes": "true", "change_type": "pipeline"}


def test_openapi_plugin_config_change_is_detected(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / _OPENAPI_CONFIG).write_text(
        json.dumps([{"base": "api-reference/new", "schema": "new.json"}], indent=2) + "\n"
    )

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert outputs == {"has_changes": "true", "change_type": "pipeline"}


def test_release_comparison_base_must_be_ancestor_commit(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)

    proc, outputs = _run(
        repo,
        _write_fake_gh(tmp_path, release_lines=1),
        base_ref="0" * 40,
    )

    assert proc.returncode != 0
    assert "comparison base is not a commit" in proc.stderr
    assert outputs == {}


def test_new_gitignored_generated_file_is_detected(tmp_path: Path) -> None:
    """A new generated domain is ignored by Git and must still trigger release."""
    repo = _setup_repo(tmp_path)
    added = repo / _OUTPUT_DIR / "new-domain.json"
    added.write_text(
        json.dumps({"openapi": "3.0.3", "info": {"version": "2.1.0"}}, indent=2) + "\n"
    )
    assert _git(repo, "status", "--short", "--", str(added.relative_to(repo))) == ""

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert outputs["has_changes"] == "true", proc.stdout
    assert outputs["change_type"] == "pipeline", proc.stdout


# ---------------------------------------------------------------------------
# AC3 -- change_type classification.
# ---------------------------------------------------------------------------


def test_new_upstream_release_tag_reports_source(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / _UPSTREAM_STATE).write_text(
        _release_state(tag="v2026.07.24-2", downloaded_at="2026-07-25T01:37:00Z")
    )

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert outputs["has_changes"] == "true", proc.stdout
    assert outputs["change_type"] == "source", proc.stdout


def test_redownloaded_same_tag_is_not_a_source_change(tmp_path: Path) -> None:
    """`.github_release` always differs after a download -- `downloaded_at`.

    Classifying on the file's bytes would therefore report `source` on every
    run and never `pipeline`. The upstream release *identity* is the signal.
    """
    repo = _setup_repo(tmp_path)
    (repo / _UPSTREAM_STATE).write_text(
        _release_state(tag="v2026.01.01-1", downloaded_at="2026-07-25T01:37:00Z")
    )
    (repo / f"{_OUTPUT_DIR}/network.json").write_text('{"changed": true}\n')

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert outputs["has_changes"] == "true", proc.stdout
    assert outputs["change_type"] == "pipeline", proc.stdout


def test_new_upstream_tag_alone_releases_even_without_output_change(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / _UPSTREAM_STATE).write_text(_release_state(tag="v2026.07.24-2"))

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert outputs["has_changes"] == "true", proc.stdout
    assert outputs["change_type"] == "source", proc.stdout


def test_same_tag_with_different_asset_digest_is_a_source_change(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / _UPSTREAM_STATE).write_text(
        _release_state(tag="v2026.01.01-1", digest="sha256:" + "b" * 64)
    )

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert outputs["has_changes"] == "true", proc.stdout
    assert outputs["change_type"] == "source", proc.stdout


# ---------------------------------------------------------------------------
# Preserved early paths.
# ---------------------------------------------------------------------------


def test_missing_generated_index_fails_loudly(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / _INDEX).unlink()

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode != 0, proc.stdout
    assert "Required generated artifact is missing" in proc.stderr
    assert _INDEX in proc.stderr
    assert "has_changes" not in outputs


def test_missing_generated_catalog_fails_loudly(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)
    (repo / _CATALOG).unlink()

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode != 0, proc.stdout
    assert "Required generated artifact is missing" in proc.stderr
    assert _CATALOG in proc.stderr
    assert "has_changes" not in outputs


@pytest.mark.parametrize(
    "state",
    [
        "not json\n",
        json.dumps(
            {
                "version": "2026.01.01-1",
                "tag_name": "v2026.01.01-1",
                "published_at": "2026-01-01T00:00:00Z",
                "asset_name": "api-specs.zip",
                "asset_size": 123,
                "asset_digest": "not-a-sha256",
            }
        ),
    ],
)
def test_malformed_current_upstream_identity_fails_loudly(tmp_path: Path, state: str) -> None:
    repo = _setup_repo(tmp_path)
    (repo / _UPSTREAM_STATE).write_text(state)

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode != 0, proc.stdout
    assert "upstream release state" in proc.stderr
    assert "has_changes" not in outputs


def test_no_existing_releases_forces_a_source_release(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path)

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=0))

    assert proc.returncode == 0, proc.stderr
    assert outputs["has_changes"] == "true", proc.stdout
    assert outputs["change_type"] == "source", proc.stdout


# ---------------------------------------------------------------------------
# AC4 -- a signal absent from both comparison sides cannot be measured.
# ---------------------------------------------------------------------------


def test_signal_absent_from_base_and_worktree_fails_loudly(tmp_path: Path) -> None:
    """The shape of defect (1), turned into a hard failure.

    `specs/original/` was silently useless as a signal because it has zero
    tracked or generated files. Any future signal path in that state must
    break the run instead of quietly reporting "no changes".
    """
    repo = _setup_repo(tmp_path, track_output=False)
    shutil.rmtree(repo / _OUTPUT_DIR)

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode != 0, proc.stdout
    assert _OUTPUT_DIR in proc.stderr + proc.stdout
    assert "has_changes" not in outputs


def test_generated_signals_absent_at_base_are_release_additions(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path, track_output=False)

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert "was added" in proc.stdout
    assert outputs == {"has_changes": "true", "change_type": "source"}


def test_gitignored_but_tracked_signal_path_is_accepted(tmp_path: Path) -> None:
    """Both signal paths match `.gitignore` yet are force-added by the release
    commit. Trackedness, not `.gitignore` membership, is the invariant."""
    repo = _setup_repo(tmp_path, with_gitignore=True, track_output=True)
    assert (
        subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", _OUTPUT_DIR],
            cwd=repo,
            check=False,
        ).returncode
        == 0
    ), "fixture must keep the signal path gitignored"

    proc, outputs = _run(repo, _write_fake_gh(tmp_path, release_lines=1))

    assert proc.returncode == 0, proc.stderr
    assert outputs["has_changes"] == "false", proc.stdout


def test_signal_paths_must_exist_at_base_or_in_generated_worktree(tmp_path: Path) -> None:
    repo = _setup_repo(tmp_path, track_catalog=False)

    for path in _SIGNAL_PATHS:
        tracked = _git(repo, "ls-tree", "-r", "--name-only", "HEAD", "--", path).split()
        assert tracked or (repo / path).exists(), f"{path} has no measurable state"


def test_detector_never_uses_a_zero_tracked_path_or_commit_history() -> None:
    """Assert on executable lines only.

    The script's header documents the retired `HEAD~1 HEAD` comparison and the
    `specs/original/` pathspec on purpose, so comments are stripped before the
    guard runs -- otherwise the guard would forbid explaining the bug.
    """
    code = [
        line
        for line in _SCRIPT.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    body = "\n".join(code)
    assert "specs/original" not in body, "specs/original has zero tracked files"
    assert "HEAD~1" not in body, "the release gate must not compare two committed revisions"


def test_workflow_delegates_to_the_detector() -> None:
    body = _WORKFLOW.read_text()
    assert _SCRIPT_RELATIVE in body, "the workflow must call the detector script"
    assert "HEAD~1 HEAD" not in body, "no step may gate on a HEAD~1..HEAD comparison"
