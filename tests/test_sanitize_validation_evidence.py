"""Tests for validation evidence secret scrubbing."""

from pathlib import Path

import pytest

from scripts.sanitize_validation_evidence import (
    sanitize_evidence,
    sanitize_file,
    sensitive_values_from_environment,
)


def test_sanitizer_removes_token_url_and_tenant_hostname(
    monkeypatch,
    tmp_path: Path,
) -> None:
    token = "validation-token-placeholder"
    tenant = "example-tenant"
    url = f"https://{tenant}.console.ves.volterra.io/api"
    monkeypatch.setenv("F5XC_API_TOKEN", token)
    monkeypatch.setenv("F5XC_API_URL", url)
    raw_evidence = tmp_path / "validation-report.md"
    raw_content = (
        f"token={token}\nurl={url}\nhost={tenant}.console.ves.volterra.io\ntenant={tenant}\n"
    )
    raw_evidence.write_text(raw_content, encoding="utf-8")
    sanitized_evidence = tmp_path / "sanitized.md"

    sanitize_file(
        raw_evidence,
        sanitized_evidence,
        sensitive_values_from_environment(),
    )

    sanitized = sanitized_evidence.read_text(encoding="utf-8")
    assert token not in sanitized
    assert url not in sanitized
    assert tenant not in sanitized
    assert sanitized.count("[REDACTED]") == 4
    assert raw_evidence.read_text(encoding="utf-8") == raw_content


def test_sanitizer_tolerates_absent_optional_reports_but_requires_console_log(
    tmp_path: Path,
) -> None:
    console_log = tmp_path / "validation-console.log"
    console_log.write_text("measured output\n", encoding="utf-8")
    missing_json = tmp_path / "validation-report.json"
    missing_markdown = tmp_path / "validation-report.md"
    output_dir = tmp_path / "sanitized"

    sanitize_evidence(
        [console_log],
        [missing_json, missing_markdown],
        output_dir,
        set(),
    )

    assert console_log.read_text(encoding="utf-8") == "measured output\n"
    assert (output_dir / "validation-console.log").read_text(encoding="utf-8") == (
        "measured output\n"
    )
    with pytest.raises(FileNotFoundError):
        sanitize_evidence(
            [tmp_path / "missing-console.log"],
            [missing_json, missing_markdown],
            tmp_path / "failed-output",
            set(),
        )
    assert not (tmp_path / "failed-output").exists()
