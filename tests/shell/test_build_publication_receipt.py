# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Tests for scripts/release/build-publication-receipt.sh.

Every release publishes a receipt in its body binding the exact asset digests
to the commit the tag resolves to:

    <!-- publication-receipt:{"assets":{...},"commit":"<40-hex>","version":"x.y.z"} -->

Consumers verify what they downloaded against it. terraform-provider-xcsh's
`download-api-specs` action and `sync-openapi.yml` both refuse to proceed
without one (f5-sales-demo/terraform-provider-xcsh#1460), so a release without
a receipt is undeliverable downstream.

The receipt is generated here rather than inline in the workflow so the
contract is testable without publishing a release.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import yaml

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "release" / "build-publication-receipt.sh"
)

_VERSION = "2.1.214"
_COMMIT = "a" * 40
_RECEIPT_LINE = re.compile(r"^<!-- publication-receipt:(.*) -->$")


def _asset_names(version: str) -> list[str]:
    """The release asset contract, exactly as consumers require it."""
    return [
        "api-catalog.json",
        "enrichment-coverage.json",
        "concurrency_contracts.json",
        f"f5xc-api-specs-v{version}.zip",
        "index.json",
        "minimal-export-defaults.json",
        "openapi.json",
        "smsv2-contract.json",
        "smsv2-evidence-receipt.json",
        "smsv2-contract-manifest.json",
        "smsv2_parity_manifest.json",
        "upstream-contract-changes.json",
        "upstream-contract-removals.json",
    ]


def _write_assets(tmp_path: Path, names: list[str]) -> list[Path]:
    paths = []
    for index, name in enumerate(names):
        path = tmp_path / name
        path.write_bytes(f"payload-{index}-{name}".encode())
        paths.append(path)
    return paths


def _run(paths: list[Path], version: str = _VERSION, commit: str = _COMMIT):
    return subprocess.run(
        ["bash", str(_SCRIPT), version, commit, *[str(p) for p in paths]],
        capture_output=True,
        text=True,
        check=False,
    )


def _parse(stdout: str) -> dict:
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"expected exactly one output line, got {lines!r}"
    match = _RECEIPT_LINE.match(lines[0])
    assert match, f"output is not a publication-receipt comment: {lines[0]!r}"
    return json.loads(match.group(1))


def test_emits_receipt_binding_digests_to_commit(tmp_path: Path) -> None:
    paths = _write_assets(tmp_path, _asset_names(_VERSION))
    result = _run(paths)
    assert result.returncode == 0, result.stderr

    receipt = _parse(result.stdout)
    assert sorted(receipt) == ["assets", "commit", "version"]
    assert receipt["commit"] == _COMMIT
    assert receipt["version"] == _VERSION
    assert sorted(receipt["assets"]) == sorted(_asset_names(_VERSION))

    for path in paths:
        expected = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        assert receipt["assets"][path.name] == expected, f"wrong digest for {path.name}"


def test_digests_are_prefixed_algorithm_qualified(tmp_path: Path) -> None:
    """Digests are "sha256:<hex>", matching GitHub's own .assets[].digest.

    Two reasons this format is pinned by a test rather than left to taste.

    A consumer compares the receipt against GitHub's reported digest directly;
    with bare hex it has to reassemble the prefix at every call site, and one
    site forgetting to is a comparison that silently never matches.

    It also keeps the value away from entropy-based secret scanners. Gitleaks'
    generic-api-key rule fires on a secret-ish keyword adjacent to a
    high-entropy value, and the asset filenames supply the keyword: measured on
    bare hex, "api-catalog.json" and "openapi.json" were reported while
    "index.json" was not. Reverting to bare hex would hand every consumer that
    commits this data a false positive it cannot suppress without a governance
    exception.
    """
    paths = _write_assets(tmp_path, _asset_names(_VERSION))
    result = _run(paths)
    assert result.returncode == 0, result.stderr

    receipt = _parse(result.stdout)
    qualified = re.compile(r"^sha256:[0-9a-f]{64}$")
    for name, value in receipt["assets"].items():
        assert qualified.match(value), f"{name} digest is not algorithm-qualified: {value!r}"


def test_receipt_is_a_single_line_of_compact_json(tmp_path: Path) -> None:
    """The consumer matches the comment with a line-anchored regex.

    Pretty-printed JSON would span lines and never match, so the release would
    look receipted while being unusable.
    """
    paths = _write_assets(tmp_path, _asset_names(_VERSION))
    result = _run(paths)
    assert result.returncode == 0, result.stderr
    assert len(result.stdout.strip().splitlines()) == 1
    assert "\n" not in result.stdout.strip()


def test_output_is_deterministic(tmp_path: Path) -> None:
    """Byte-identical inputs must produce a byte-identical receipt."""
    paths = _write_assets(tmp_path, _asset_names(_VERSION))
    first = _run(paths)
    second = _run(paths)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout


def test_rejects_missing_asset(tmp_path: Path) -> None:
    paths = _write_assets(tmp_path, _asset_names(_VERSION))
    paths[0].unlink()
    result = _run(paths)
    assert result.returncode != 0
    assert "does not exist" in result.stderr or "missing" in result.stderr.lower()


def test_rejects_incomplete_asset_set(tmp_path: Path) -> None:
    """A partial receipt would attest to a release nobody can verify."""
    names = _asset_names(_VERSION)[:-1]
    paths = _write_assets(tmp_path, names)
    result = _run(paths)
    assert result.returncode != 0
    assert "release asset" in result.stderr or "asset set" in result.stderr


def test_rejects_unexpected_asset(tmp_path: Path) -> None:
    names = [*_asset_names(_VERSION), "stowaway.json"]
    paths = _write_assets(tmp_path, names)
    result = _run(paths)
    assert result.returncode != 0
    assert "release asset" in result.stderr or "asset set" in result.stderr


def test_rejects_bundle_named_for_another_version(tmp_path: Path) -> None:
    """The zip carries the version in its name; a mismatch means crossed releases."""
    names = _asset_names("2.1.999")
    paths = _write_assets(tmp_path, names)
    result = _run(paths)
    assert result.returncode != 0
    assert "release asset" in result.stderr or "asset set" in result.stderr


def test_rejects_malformed_version(tmp_path: Path) -> None:
    paths = _write_assets(tmp_path, _asset_names(_VERSION))
    result = _run(paths, version="v2.1.214")
    assert result.returncode != 0
    assert "version" in result.stderr.lower()


def test_rejects_malformed_commit(tmp_path: Path) -> None:
    paths = _write_assets(tmp_path, _asset_names(_VERSION))
    result = _run(paths, commit="deadbeef")
    assert result.returncode != 0
    assert "commit" in result.stderr.lower()


def test_rejects_uppercase_commit(tmp_path: Path) -> None:
    """Consumers test against ^[0-9a-f]{40}$; uppercase would fail there, not here."""
    paths = _write_assets(tmp_path, _asset_names(_VERSION))
    result = _run(paths, commit="A" * 40)
    assert result.returncode != 0
    assert "commit" in result.stderr.lower()


_OPERATIONAL_ASSETS = ["gcp-operational-health-v2.json", "gcp-operational-qualification-v2.json"]


def test_emits_complete_operational_asset_pair(tmp_path: Path) -> None:
    paths = _write_assets(tmp_path, [*_asset_names(_VERSION), *_OPERATIONAL_ASSETS])
    result = _run(paths)
    assert result.returncode == 0, result.stderr
    receipt = _parse(result.stdout)
    for path in paths:
        assert (
            receipt["assets"][path.name]
            == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        )


def test_rejects_partial_operational_asset_pair(tmp_path: Path) -> None:
    for name in _OPERATIONAL_ASSETS:
        result = _run(_write_assets(tmp_path, [*_asset_names(_VERSION), name]))
        assert result.returncode != 0
        assert "asset set" in result.stderr


def test_operational_pair_does_not_admit_foreign_assets(tmp_path: Path) -> None:
    result = _run(
        _write_assets(tmp_path, [*_asset_names(_VERSION), *_OPERATIONAL_ASSETS, "foreign.json"])
    )
    assert result.returncode != 0
    assert "asset set" in result.stderr


def test_resume_uses_current_receipt_builder_with_exact_tagged_assets(tmp_path: Path) -> None:
    """A resume must not restore the broken publisher from the release tag."""
    root = _SCRIPT.parents[2]
    workflow = yaml.safe_load((root / ".github/workflows/sync-and-enrich.yml").read_text())
    steps = workflow["jobs"]["sync-and-enrich"]["steps"]
    snapshot = next(
        (step for step in steps if step.get("name") == "Freeze publication receipt builder"), None
    )
    assert snapshot is not None, "receipt builder must survive exact release checkout"
    create = next(step["run"] for step in steps if step.get("name") == "Create release")
    # YAML removes the workflow indentation; match the actual receipt invocation.
    invocation = re.search(r"(?m)^\s*bash (.+) \\$", create)
    assert invocation is not None
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()

    git("init")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    builder = repo / "scripts/release/build-publication-receipt.sh"
    builder.parent.mkdir(parents=True)
    builder.write_text("#!/usr/bin/env bash\nexit 1\n")
    paths = _write_assets(repo, [*_asset_names(_VERSION), *_OPERATIONAL_ASSETS])
    git("add", ".")
    git("commit", "-m", "chore: original tagged release")
    target = git("rev-parse", "HEAD")
    git("tag", "-a", f"v{_VERSION}", "-m", "release")
    tag = git("rev-parse", f"v{_VERSION}")
    builder.write_bytes(_SCRIPT.read_bytes())
    git("commit", "-am", "fix: receipt publisher")
    publisher_commit = git("rev-parse", "HEAD")
    temp = tmp_path / "runner"
    temp.mkdir()
    env = os.environ | {"RUNNER_TEMP": str(temp), "GITHUB_ENV": str(temp / "env")}
    subprocess.run(
        ["bash", "-euo", "pipefail", "-c", snapshot["run"]], cwd=repo, env=env, check=True
    )
    subprocess.run(
        ["bash", str(root / "scripts/release/checkout-release-merge.sh"), target],
        cwd=repo,
        env=env,
        check=True,
    )
    result = subprocess.run(
        [
            "bash",
            "-euo",
            "pipefail",
            "-c",
            "bash " + invocation.group(1) + ' "$@"',
            "receipt",
            _VERSION,
            target,
            *map(str, paths),
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    receipt = _parse(result.stdout)
    assert receipt["commit"] == target
    assert sorted(receipt["assets"]) == sorted(path.name for path in paths)
    for path in paths:
        assert (
            receipt["assets"][path.name]
            == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        )
    assert git("rev-parse", "HEAD") == target
    assert git("rev-parse", f"v{_VERSION}") == tag
    assert git("status", "--porcelain") == ""
    evidence = temp / "publication-receipt-builder"
    assert (evidence / "source-commit.txt").read_text().strip() == publisher_commit
    assert (evidence / "build-publication-receipt.sh").read_bytes() == _SCRIPT.read_bytes()
    subprocess.run(["sha256sum", "-c", "builder.sha256"], cwd=evidence, check=True)
