"""Fail-closed contract for the deliberately suspended translation hook."""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
PRE_COMMIT_CONFIG = REPO_ROOT / ".pre-commit-config.yaml"
README = REPO_ROOT / "README.md"


def _translation_entry() -> str:
    config = yaml.safe_load(PRE_COMMIT_CONFIG.read_text())
    hooks = [
        hook
        for repo in config["repos"]
        if repo["repo"] == "local"
        for hook in repo["hooks"]
        if hook["id"] == "docs-translate"
    ]
    assert len(hooks) == 1
    return hooks[0]["entry"]


def _run_hook(
    *, enabled: str | None, api_key: str | None, path: str
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = path
    if enabled is None:
        env.pop("TRANSLATIONS_ENABLED", None)
    else:
        env["TRANSLATIONS_ENABLED"] = enabled
    if api_key is None:
        env.pop("ANTHROPIC_API_KEY", None)
    else:
        env["ANTHROPIC_API_KEY"] = api_key
    return subprocess.run(
        shlex.split(_translation_entry()),
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_translation_generation_is_suspended_by_default() -> None:
    result = _run_hook(enabled=None, api_key=None, path="/usr/bin:/bin")

    assert result.returncode == 0
    assert "translations suspended" in result.stdout


def test_readme_advertises_only_the_published_english_site() -> None:
    readme = README.read_text()

    assert "Documentation publication is English-only" in readme
    for locale in (
        "ar",
        "de",
        "es",
        "fr",
        "hi",
        "it",
        "ja",
        "ko",
        "pt-br",
        "th",
        "zh-cn",
        "zh-tw",
    ):
        assert f"api-specs-enriched/{locale}/" not in readme


def test_invalid_translation_switch_fails_closed() -> None:
    result = _run_hook(enabled="enabled", api_key=None, path="/usr/bin:/bin")

    assert result.returncode != 0
    assert "must be true or false" in result.stderr


def test_enabled_translation_requires_tool() -> None:
    result = _run_hook(enabled="true", api_key="EXAMPLE_API_KEY", path="/usr/bin:/bin")

    assert result.returncode != 0
    assert "requires docs-translate" in result.stderr


def test_enabled_translation_requires_api_key(tmp_path: Path) -> None:
    tool = tmp_path / "docs-translate"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)

    result = _run_hook(enabled="true", api_key=None, path=f"{tmp_path}:/usr/bin:/bin")

    assert result.returncode != 0
    assert "requires ANTHROPIC_API_KEY" in result.stderr


def test_enabled_translation_invokes_generator(tmp_path: Path) -> None:
    marker = tmp_path / "args"
    tool = tmp_path / "docs-translate"
    tool.write_text(f"#!/bin/sh\nprintf '%s' \"$*\" > {marker}\n")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)

    result = _run_hook(
        enabled="true",
        api_key="EXAMPLE_API_KEY",
        path=f"{tmp_path}:/usr/bin:/bin",
    )

    assert result.returncode == 0, result.stderr
    assert marker.read_text() == "--staged"
