"""Regression tests for generated API-reference Markdown."""

from __future__ import annotations

import json

from scripts import generate_api_viewer


def test_catalog_uses_h2_category_sections() -> None:
    """Catalog category headings follow the document hierarchy."""
    rendered = generate_api_viewer.generate_catalog_mdx(
        [
            {
                "domain": "example",
                "title": "Example",
                "x-f5xc-category": "Security",
            },
        ],
    )

    assert "\n## Security\n" in rendered
    assert "\n### Security\n" not in rendered


def test_domain_summary_emits_consistent_table_spacing(tmp_path) -> None:
    """Generated endpoint tables satisfy markdownlint's compact table style."""
    (tmp_path / "example.json").write_text(
        json.dumps(
            {
                "paths": {
                    "/v1/widgets": {
                        "get": {"summary": "List widgets."},
                    },
                },
            },
        ),
    )

    rendered = generate_api_viewer.generate_domain_summary(
        {
            "domain": "example",
            "title": "Example",
            "path_count": 1,
            "schema_count": 0,
        },
        spec_dir=tmp_path,
    )

    assert "| Method | Path | Description |\n| --- | --- | --- |" in rendered


def test_domain_summary_omits_blank_generated_sections(tmp_path) -> None:
    """Empty optional sections do not emit consecutive blank lines."""
    rendered = generate_api_viewer.generate_domain_summary(
        {
            "domain": "example",
            "title": "Example",
            "path_count": 0,
            "schema_count": 0,
        },
        spec_dir=tmp_path,
    )

    assert "\n\n\n" not in rendered
    assert "## Use Cases" not in rendered
    assert "## Primary Resources" not in rendered
