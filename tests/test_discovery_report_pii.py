"""PII regression coverage for persisted discovery-report metadata."""

from __future__ import annotations

import json
from types import SimpleNamespace

from scripts.discovery.report_generator import DiscoverySession, ReportGenerator
from scripts.utils.extension_constants import X_F5XC_API_URL


def test_report_generator_sanitizes_live_api_url_in_all_persisted_outputs(tmp_path) -> None:
    captured_url = "https://" + "captured-tenant" + ".console.example.invalid/api"
    reports_dir = tmp_path / "reports"
    generator = ReportGenerator(
        output_dir=tmp_path / "discovered",
        path_config=SimpleNamespace(
            reports_dir=reports_dir,
            discovery_report=reports_dir / "discovery-report.md",
        ),
    )
    captured_namespace = "captured-" + "namespace"
    session = DiscoverySession(api_url=captured_url, namespaces=[captured_namespace, "shared"])

    openapi_path = generator.generate_openapi(session)
    markdown_path = generator.generate_markdown_report(session)
    summary_path = generator.generate_session_summary(session)

    assert openapi_path is not None
    assert markdown_path is not None
    assert summary_path is not None
    openapi = json.loads(openapi_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")

    assert openapi["info"][X_F5XC_API_URL] == "https://api.example.com/api"
    assert openapi["servers"] == [{"url": "https://api.example.com/api"}]
    assert summary["api_url"] == "https://api.example.com/api"
    assert summary["namespaces"] == ["default", "shared"]
    assert "https://api.example.com/api" in markdown
    assert captured_url not in openapi_path.read_text(encoding="utf-8")
    assert captured_url not in summary_path.read_text(encoding="utf-8")
    assert captured_url not in markdown
    assert captured_namespace not in summary_path.read_text(encoding="utf-8")
    assert captured_namespace not in markdown
