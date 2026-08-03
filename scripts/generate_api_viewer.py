#!/usr/bin/env python3
# Copyright (c) 2026 Robin Mordasiewicz. MIT License.

"""Generate API catalog page and starlight-openapi plugin configuration.

Reads docs/specifications/api/index.json and generates:
    - docs/api-reference/index.mdx        (catalog landing page)
    - docs/openapi-specs-config.json       (starlight-openapi plugin config)

Usage:
    python -m scripts.generate_api_viewer
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

HTTP_METHODS = ("get", "post", "put", "delete", "patch", "head", "options", "trace")

SPEC_DIR = Path("docs/specifications/api")
MDX_DIR = Path("docs/api-reference")
INDEX_JSON = SPEC_DIR / "index.json"
OPENAPI_CONFIG_PATH = Path("docs/openapi-specs-config.json")


def generate_openapi_specs_config(specs: list[dict]) -> str:
    """Generate the starlight-openapi plugin configuration JSON."""
    config = []
    for spec in specs:
        domain = spec["domain"]
        title = spec["title"]
        if spec.get("path_count", 0) == 0:
            continue
        config.append(
            {
                "base": f"api-reference/{domain}",
                "schema": f"public/specifications/api/{domain}.json",
                "sidebar": {
                    "label": title,
                    "collapsed": True,
                },
            },
        )
    return json.dumps(config, indent=2) + "\n"


def generate_catalog_mdx(
    specs: list[dict],
) -> str:
    """Generate the API Reference catalog landing page."""
    categories: dict[str, list[dict]] = defaultdict(list)
    for spec in specs:
        cat = spec.get("x-f5xc-category", "Other")
        categories[cat].append(spec)

    category_order = [
        "Security",
        "Networking",
        "Infrastructure",
        "Platform",
        "Operations",
        "AI",
        "Other",
    ]

    cards_sections = []
    for cat in category_order:
        if cat not in categories:
            continue
        domain_list = sorted(categories[cat], key=lambda s: s["title"])

        cards = []
        for spec in domain_list:
            domain = spec["domain"]
            title = spec["title"]
            icon = spec.get("x-f5xc-icon", "")
            short_desc = spec.get("x-f5xc-description-short", "")
            complexity = spec.get("x-f5xc-complexity", "")
            path_count = spec.get("path_count", 0)
            schema_count = spec.get("schema_count", 0)

            badge = ""
            if spec.get("x-f5xc-is-preview"):
                badge = " (Preview)"

            description_line = short_desc or title
            stats_line = f"{path_count} paths, {schema_count} schemas"
            if complexity:
                stats_line += f", {complexity}"

            cards.append(
                f'  <LinkCard title="{icon} {title}{badge}" '
                f'description="{description_line} — {stats_line}" '
                f'href="./{domain}/" />',
            )

        cards_block = "\n".join(cards)
        cards_sections.append(f"## {cat}\n\n<CardGrid>\n{cards_block}\n</CardGrid>")

    all_sections = "\n\n".join(cards_sections)

    return f"""---
title: API Reference
description: API documentation for F5 Distributed Cloud services
tableOfContents: false
sidebar:
    order: 0
---

import {{ CardGrid, LinkCard }} from '@astrojs/starlight/components';

Explore the F5 Distributed Cloud API documentation. Each domain includes
endpoint details, request and response schemas, and code examples.

{all_sections}
"""


UPSTREAM_TYPOS = {
    "Con" + "nnect": "Connect",
    "JI" + "RA": "Jira",
    "Open" + "Api": "OpenAPI",
    "Subsci" + "ption": "Subscription",
    "Insatn" + "ce": "Instance",
    "WI" + "FI": "Wi-Fi",
    "avaialab" + "le": "available",
    "Sou" + "ce": "Source",
    "pro" + "xys": "proxies",
}


def _fix_upstream_typos(text: str) -> str:
    """Correct known typos from upstream F5 API definitions."""
    for typo, fix in UPSTREAM_TYPOS.items():
        text = text.replace(typo, fix)
    return text


def _escape_mdx(text: str) -> str:
    """Escape characters that break MDX rendering."""
    return (
        _fix_upstream_typos(text)
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("{", "&#123;")
        .replace("}", "&#125;")
    )


def generate_domain_summary(spec: dict) -> str:
    """Generate a per-domain summary MDX page for llms-txt indexing."""
    domain = spec["domain"]
    title = spec["title"]
    icon = spec.get("x-f5xc-icon", "")
    short_desc = spec.get("x-f5xc-description-short", "")
    medium_desc = spec.get("x-f5xc-description-medium", "")
    category = spec.get("x-f5xc-category", "Other")
    complexity = spec.get("x-f5xc-complexity", "")
    tier = spec.get("x-f5xc-requires-tier", "")
    path_count = spec.get("path_count", 0)
    schema_count = spec.get("schema_count", 0)
    use_cases = spec.get("x-f5xc-use-cases", [])
    related = spec.get("x-f5xc-related-domains", [])
    resources = spec.get("x-f5xc-primary-resources", [])

    spec_file = SPEC_DIR / f"{domain}.json"
    rows: list[str] = []
    if spec_file.exists():
        with spec_file.open() as f:
            full_spec = json.load(f)
        for path, methods in full_spec.get("paths", {}).items():
            for method, details in methods.items():
                if method not in HTTP_METHODS:
                    continue
                summary = _escape_mdx(
                    details.get("summary", details.get("operationId", "")),
                )
                escaped_path = _escape_mdx(path)
                rows.append(
                    f"| {method.upper()} | `{escaped_path}` | {summary} |",
                )

    endpoints_table = ""
    if rows:
        header = "| Method | Path | Description |\n| --- | --- | --- |"
        endpoints_table = f"## Endpoints\n\n{header}\n" + "\n".join(rows)

    use_cases_block = ""
    if use_cases:
        items = "\n".join(f"- {_escape_mdx(uc)}" for uc in use_cases)
        use_cases_block = f"## Use Cases\n\n{items}"

    resources_block = ""
    if resources:
        items = "\n".join(
            f"- **{_escape_mdx(r.get('name', ''))}**: {_escape_mdx(r.get('description', ''))}"
            for r in resources
        )
        resources_block = f"## Primary Resources\n\n{items}"

    related_block = ""
    if related:
        links = ", ".join(f"`{r}`" for r in related)
        related_block = f"**Related domains**: {links}"

    description_line = medium_desc or short_desc or title

    frontmatter = f"""---
title: "{icon} {title} API"
description: "{_escape_mdx(short_desc or title)}"
sidebar:
    hidden: true
---"""
    overview_lines = [
        _escape_mdx(description_line),
        "",
        f"- **Category**: {category}",
        f"- **Complexity**: {complexity}",
        f"- **Paths**: {path_count} | **Schemas**: {schema_count}",
        f"- **Tier**: {tier}",
    ]
    if related_block:
        overview_lines.append(f"- {related_block}")
    overview = "\n".join(overview_lines)
    links_block = f"""## Links

- [Interactive API Reference](../../../api-reference/{domain}/)
- [OpenAPI Specification JSON](../../../specifications/api/{domain}.json)"""
    sections = [
        frontmatter,
        overview,
        use_cases_block,
        resources_block,
        endpoints_table,
        links_block,
    ]
    return "\n\n".join(section for section in sections if section) + "\n"


def main() -> int:
    """Generate catalog page and plugin configuration."""
    if not INDEX_JSON.exists():
        print(f"Error: {INDEX_JSON} not found. Run 'make pipeline' first.")
        return 1

    with INDEX_JSON.open() as f:
        index = json.load(f)

    specs = index.get("specifications", [])
    if not specs:
        print("Error: No specifications found in index.json")
        return 1

    MDX_DIR.mkdir(parents=True, exist_ok=True)

    # Remove stale generated MDX files
    valid_stems = {"index"} | {f"{s['domain']}-api" for s in specs}
    for stale in MDX_DIR.glob("*.mdx"):
        if stale.stem not in valid_stems:
            stale.unlink()

    # Generate catalog landing page
    catalog_path = MDX_DIR / "index.mdx"
    catalog_path.write_text(generate_catalog_mdx(specs))

    # Generate per-domain summary pages for llms-txt indexing
    summary_count = 0
    for spec in specs:
        if spec.get("path_count", 0) == 0:
            continue
        summary_path = MDX_DIR / f"{spec['domain']}-api.mdx"
        summary_path.write_text(generate_domain_summary(spec))
        summary_count += 1
    # Generate starlight-openapi plugin configuration
    config_content = generate_openapi_specs_config(specs)
    OPENAPI_CONFIG_PATH.write_text(config_content)

    print(f"Generated catalog page at {catalog_path}")
    print(f"Generated {summary_count} domain summary pages")
    print(f"Generated OpenAPI specs config at {OPENAPI_CONFIG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
