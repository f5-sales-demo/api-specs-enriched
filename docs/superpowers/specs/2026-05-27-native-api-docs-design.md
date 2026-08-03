# Native Starlight API Documentation

**Date:** 2026-05-27
**Status:** Draft

## Problem

The API reference documentation at `/api-reference/` uses iframe-embedded Scalar viewers to render OpenAPI specs. This approach has three issues:

1. **SEO** — Content inside iframes is not indexed by search engines
2. **UX** — The iframe creates a visual boundary that breaks the Starlight theme experience
3. **Navigation** — No deep linking to specific endpoints; no sidebar integration

## Solution

Replace iframe+Scalar rendering with the `starlight-openapi` Starlight plugin, which generates static `.astro` pages at build time from OpenAPI JSON specs. Pages inherit Starlight's theme, search indexing, sidebar, and table of contents.

## Architecture

### Current flow

```text
Python pipeline → enriched JSON → Scalar HTML viewer → iframe in MDX → Starlight page
```

### Target flow

```text
Python pipeline → enriched JSON → starlight-openapi → native .astro pages
```

### Repo responsibilities

**api-specs-enriched (this repo):**

- Python pipeline continues generating enriched JSON specs at `docs/specifications/api/{domain}.json`
- `generate_api_viewer.py` is simplified:
  - Removes `generate_viewer_html()` — no more Scalar HTML files
  - Removes `generate_domain_mdx()` — no more iframe wrapper pages
  - Keeps `generate_catalog_mdx()` — card catalog stays, hrefs updated to plugin routes
- `docs/specifications/api/viewer/` directory and its HTML files are deleted
- `docs/api-reference/{domain}.mdx` iframe wrappers are deleted (replaced by plugin-generated pages)

**docs-theme (sibling repo):**

- Add `starlight-openapi` to `package.json`
- Configure in `astro.config.mjs` to read specs dynamically from `index.json`
- Each domain gets its own route under `/api-reference/{domain}/`

**docs-builder:** No direct changes — runs `npm install` during build, which pulls in the plugin from docs-theme's `package.json` automatically.

## Plugin configuration

The plugin reads each domain's enriched JSON spec and generates:

- **Overview page** per domain — lists all endpoints grouped by tag
- **Operation pages** — one per endpoint with parameters, request/response schemas, expandable tabbed panels
- **Schema pages** — dedicated pages for complex types with anchored sections and searchable selector

All pages inherit Starlight theme, dark mode, search indexing, and sidebar navigation.

### Dynamic config generation

Since there are 38 domains, the `astro.config.mjs` entry generates the plugin config array dynamically by reading `index.json` at config load time rather than hardcoding 38 entries:

```javascript
import { readFileSync } from 'fs';
import { starlightOpenAPINavigator } from 'starlight-openapi';

const index = JSON.parse(readFileSync('./docs/specifications/api/index.json', 'utf-8'));
const schemas = index.specifications.map(spec => ({
  base: `api-reference/${spec.domain}`,
  schema: `./docs/specifications/api/${spec.domain}.json`,
  sidebar: {
    label: spec.title,
    collapsed: true,
  }
}));

export default defineConfig({
  integrations: [
    starlight({
      plugins: [starlightOpenAPINavigator(schemas)],
    }),
  ],
});
```

## Card catalog

The existing `docs/api-reference/index.mdx` card catalog page stays. Cards link to plugin-generated domain overview pages. The `generate_catalog_mdx()` function in `generate_api_viewer.py` continues generating this page, with `href` values updated if the plugin uses a different route pattern.

## Changes to generate_api_viewer.py

| Function | Action |
| ---------- | -------- |
| `generate_viewer_html()` | Remove |
| `generate_domain_mdx()` | Remove |
| `generate_catalog_mdx()` | Keep — update `href` to match plugin routes |
| `main()` | Simplify — stop generating HTML/MDX per domain, only generate catalog |
| Stale file cleanup | Update to not clean plugin-generated files |

## Files removed

- `docs/specifications/api/viewer/*.html` — 38 Scalar HTML viewers
- `docs/api-reference/{domain}.mdx` — 38 iframe wrapper pages
- Scalar CDN reference (`SCALAR_CDN_VERSION`, `SCALAR_CDN_URL` constants)

## Build time considerations

- 38 specs totaling ~77MB of enriched OpenAPI JSON
- ~1,200 total API paths across all domains
- Plugin generates static pages at build time — initial build may take longer
- Subsequent builds benefit from Astro's incremental build cache

Mitigation: Start with one small spec (`vpm_and_node_management.json`, 1 path, 17KB) to validate, then scale to the full set.

## Verification plan

1. **Proof of concept (docs-theme):** Install plugin, configure with one small spec, confirm native page generation
2. **Scale test:** Add a large spec (`sites.json` — 133 paths, 1015 schemas) and verify build succeeds in reasonable time
3. **Full integration:** Configure all 38 specs, verify card catalog links resolve correctly
4. **Search verification:** Confirm endpoint content appears in Starlight's search index
5. **Theme consistency:** Verify pages match Starlight's dark/light mode, typography, and spacing
6. **Deep linking:** Confirm individual operations have stable, bookmarkable URLs

## Open questions (resolved during proof-of-concept)

- The `astro.config.mjs` config example is illustrative — exact plugin API (import names, config shape) will be determined during the docs-theme proof-of-concept step
- Whether the plugin supports "one page per domain" mode or only "one page per operation" — if it only does per-operation, the domain overview page becomes the entry point from the card catalog and individual operations are sub-pages
- Build time with 38 specs (~77MB total) — may require tuning or a pre-processing step if builds exceed acceptable time

## Implementation order

1. Add `starlight-openapi` to docs-theme (PR to docs-theme)
2. Validate with small spec, then full spec set
3. Simplify `generate_api_viewer.py` in this repo (remove iframe/HTML generation)
4. Remove stale files (viewer HTML, domain MDX wrappers)
5. Update card catalog hrefs if plugin route pattern differs
6. End-to-end test on GitHub Pages deployment
