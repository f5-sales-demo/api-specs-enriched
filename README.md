🌐 English

# API Specs Enriched

[![GitHub Pages Deploy](https://github.com/f5-sales-demo/api-specs-enriched/actions/workflows/github-pages-deploy.yml/badge.svg)](https://github.com/f5-sales-demo/api-specs-enriched/actions/workflows/github-pages-deploy.yml)
[![Repository Settings](https://github.com/f5-sales-demo/api-specs-enriched/actions/workflows/enforce-repo-settings.yml/badge.svg)](https://github.com/f5-sales-demo/api-specs-enriched/actions/workflows/enforce-repo-settings.yml)
[![Tests](https://github.com/f5-sales-demo/api-specs-enriched/actions/workflows/tests.yml/badge.svg)](https://github.com/f5-sales-demo/api-specs-enriched/actions/workflows/tests.yml)
[![Sync and Enrich](https://github.com/f5-sales-demo/api-specs-enriched/actions/workflows/sync-and-enrich.yml/badge.svg)](https://github.com/f5-sales-demo/api-specs-enriched/actions/workflows/sync-and-enrich.yml)
[![License](https://img.shields.io/github/license/f5-sales-demo/api-specs-enriched)](LICENSE)

Enriched OpenAPI specifications for F5 Distributed Cloud

## Role in the API supply chain

[`api-specs`](https://github.com/f5-sales-demo/api-specs) is the correction
layer for the source API specifications. This repository consumes an immutable
`api-specs` release, applies deterministic enrichment, and publishes the
canonical enriched specification bundle used by
[`terraform-provider-xcsh`](https://github.com/f5-sales-demo/terraform-provider-xcsh)
and other downstream projects.

Changes advance in that order: source corrections land in `api-specs`,
enrichment lands and publishes here, and consumers update to the exact enriched
release. The specification leads provider implementation.

Documentation publication is English-only until the production release.
Translation generation is deliberately suspended during prerelease iteration.

## Documentation

Full documentation is available at **[https://f5-sales-demo.github.io/api-specs-enriched/](https://f5-sales-demo.github.io/api-specs-enriched/)**.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for workflow rules,
branch naming, and CI requirements.

## License

See [LICENSE](LICENSE).
