"""Exercise release Pages selection against actual divergent Git histories."""

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/release/verify-release-docs-ref.sh"


@pytest.mark.parametrize(
    "case",
    [
        "current",
        "historical",
        "docs-changed",
        "catalog-changed",
        "import-changed",
        "foreign-tag",
        "foreign-release",
        "foreign-workflow",
        "malformed",
    ],
)
def test_release_docs_selection(tmp_path: Path, case: str) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()

    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    (repo / "docs").mkdir()
    (repo / "release").mkdir()
    (repo / "docs/index.md").write_text("release documentation\n")
    (repo / "release/api-catalog.json").write_text("{}\n")
    if case == "import-changed":
        (repo / "docs/_imports").write_text(
            "# documentation import\nconfig.json -> data/config.json\n"
        )
        (repo / "config.json").write_text("{}\n")
    git("add", ".")
    git("commit", "-m", "chore: release")
    release = git("rev-parse", "HEAD")
    workflow = release
    git("tag", "-a", "v12.2.2", "-m", "release")
    if case != "current":
        (repo / "pipeline.txt").write_text("repair\n")
        git("add", ".")
        git("commit", "-m", "fix: workflow repair")
    if case in {"docs-changed", "catalog-changed", "import-changed"}:
        name = {
            "docs-changed": "docs/index.md",
            "catalog-changed": "release/api-catalog.json",
            "import-changed": "config.json",
        }[case]
        (repo / name).write_text("changed\n")
        git("commit", "-am", "docs: changed release content")
    main = git("rev-parse", "HEAD")
    git("update-ref", "refs/remotes/origin/main", main)
    if case == "foreign-tag":
        git("tag", "-f", "v12.2.2", main)
    if case in {"foreign-release", "foreign-workflow"}:
        git("checkout", "--orphan", "foreign")
        git("commit", "-m", "chore: unrelated history")
        foreign = git("rev-parse", "HEAD")
        if case == "foreign-release":
            release = foreign
            git("tag", "-f", "v12.2.2", foreign)
        else:
            workflow = foreign
    if case == "malformed":
        release = "main"
    (repo / "unrelated.txt").write_text("preserve dirty work\n")
    before = git("status", "--porcelain")
    result = subprocess.run(
        ["bash", str(SCRIPT), "v12.2.2", release, workflow],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if case in {"current", "historical"}:
        assert result.returncode == 0
        assert result.stdout.strip() == main
    else:
        assert result.returncode != 0
    assert git("status", "--porcelain") == before
    assert (repo / "unrelated.txt").read_text() == "preserve dirty work\n"
