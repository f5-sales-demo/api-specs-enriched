# API Specs Enriched — Repo-Specific Instructions

## Project Overview

Python-based OpenAPI enrichment pipeline for F5 Distributed Cloud. Downloads pre-validated specs from f5-sales-demo/api-specs, enriches them with descriptions, metadata, and branding, then publishes developer-friendly documentation.

## Key Commands

- `make install` — production setup
- `make dev-install` — dev setup with testing tools
- `make download` — download specs from upstream (api-specs)
- `make enrich` — run enrichment pipeline
- `make release` — build release package
- `make test` — run pytest suite
- `make all` — full pipeline: download → enrich → release

## Directory Structure

- `scripts/` — Python pipeline scripts
- `config/` — 40 configuration files (enrichment, descriptions, metadata, etc.)
- `specs/original/` — Downloaded source specs (from api-specs releases)
- `specs/discovered/` — API discovery data
- `docs/` — MDX documentation (Starlight format)
- `docs/specifications/api/` — Generated enriched spec files
- `tests/` — Test suite
- `examples/` — Example constraint outputs

## Upstream/Downstream

- **Upstream**: f5-sales-demo/api-specs (pre-validated OpenAPI specs)
- **Downstream**: f5-sales-demo/xcsh, f5-sales-demo/vscode-xcsh

## Environment Variables

```bash
F5XC_API_URL=https://${XC_TENANT}.console.ves.volterra.io
F5XC_API_TOKEN=<your-api-token>
```
