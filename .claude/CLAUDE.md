# API Specs Enriched — Repo-Specific Instructions

## Project Overview

Python-based OpenAPI enrichment pipeline for F5 Distributed Cloud. Downloads pre-validated specs from f5-sales-demo/api-specs, enriches them with descriptions, metadata, and branding, then publishes developer-friendly documentation.

## Key Commands

- `make install` — production setup
- `make download` — download specs from upstream (api-specs)
- `make download-force` — force a fresh immutable-asset download and digest check
- `make enrich` — run enrichment pipeline
- `make pipeline` — build the enriched specifications and API viewer
- `make validate` — validate against the configured live API
- `make test` — run pytest suite
- `make all` — full pipeline: download → enrich → normalize → merge

## Directory Structure

- `scripts/` — Python pipeline scripts
- `config/` — enrichment, description, metadata, and validation configuration
- `specs/original/` — Downloaded source specs (from api-specs releases)
- `docs/` — MDX documentation (Starlight format)
- `docs/specifications/api/` — Generated enriched spec files
- `tests/` — Test suite
- `examples/` — Example constraint outputs

## Upstream/Downstream

- **Upstream correction layer**: f5-sales-demo/api-specs (basic source-spec corrections and immutable input releases)
- **Canonical enriched publication**: this repository
- **Downstream**: f5-sales-demo/terraform-provider-xcsh and other API-spec, CLI, IDE, and documentation consumers

## Environment Variables

```bash
F5XC_API_URL=https://tenant-placeholder.console.ves.volterra.io
F5XC_API_TOKEN=<your-api-token>
```
