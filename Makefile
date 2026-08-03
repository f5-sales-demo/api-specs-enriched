# F5 XC API Enrichment Pipeline Makefile
# Local builds produce identical output to GitHub Actions workflow
#
# Simplified two-folder architecture:
#   specs/original/          - READ-ONLY extraction of an immutable corrected
#                              f5-sales-demo/api-specs release asset (gitignored)
#   docs/specifications/api/ - Merged domain specs (served directly by GitHub Pages)
#
# Immutable-release caching: downloads are keyed by the api-specs release tag,
# asset name, and published SHA-256 digest recorded in .github_release.
#
# Usage:
#   make build          - Full pipeline (download → enrich → normalize → merge)
#   make download       - Download specs (only if the immutable release changed)
#   make download-force - Force download even if unchanged
#   make pipeline       - Run unified pipeline (enrich + normalize + merge)
#   make serve          - Serve docs locally
#   make clean          - Remove generated files
#   make install        - Install dependencies
#
# The pipeline ensures deterministic output:
#   specs/original/          → immutable corrected api-specs release asset
#   docs/specifications/api/ → Merged domain specs (GitHub Pages)
#       ├── api_security.json
#       ├── applications.json
#       ├── bigip.json
#       ├── billing.json
#       ├── cdn.json
#       ├── config.json
#       ├── identity.json
#       ├── infrastructure.json
#       ├── infrastructure_protection.json
#       ├── load_balancer.json
#       ├── networking.json
#       ├── nginx.json
#       ├── observability.json
#       ├── security.json
#       ├── service_mesh.json
#       ├── shape_security.json
#       ├── subscriptions.json
#       ├── tenant_management.json
#       ├── vpn.json
#       ├── openapi.json    (master combined spec)
#       └── index.json      (spec metadata)

.PHONY: all build clean install download download-force pipeline enrich normalize lint validate validate-domains serve help check-deps venv pre-commit-install pre-commit-run pre-commit-uninstall discover discover-namespace discover-dry-run discover-cli constraint-report api-viewer catalog test

# Virtual environment
VENV := .venv
PYTHON := $(VENV)/bin/python
UV ?= uv
UV_VERSION := 0.12.1
PYTHON_VERSION := 3.13.14
BIOME ?= biome
BIOME_VERSION := 2.5.6
NODE ?= node
NODE_VERSION := 22.23.2
NPM ?= npm
NPM_VERSION := 10.9.8
SPECTRAL := node_modules/.bin/spectral
SPECTRAL_VERSION := 6.16.3

# Default target
all: build

# Full pipeline - matches GitHub Actions workflow exactly
build: check-deps download pipeline
	@echo ""
	@echo "Build complete. Output in:"
	@echo "  docs/specifications/api/  - Merged domain API specifications (GitHub Pages)"
	@echo ""
	@echo "Run 'make serve' to preview locally"

# Create the exact locked development environment.
venv:
	@command -v $(UV) >/dev/null || { echo "uv is required. Install uv $(UV_VERSION)."; exit 1; }
	@test "$$($(UV) --version | awk '{print $$2}')" = "$(UV_VERSION)" || { \
		echo "uv $(UV_VERSION) is required."; exit 1; }
	UV_PYTHON=$(PYTHON_VERSION) $(UV) sync --frozen --extra dev

# Check dependencies are installed
check-deps:
	@test -d $(VENV) || { echo "Virtual environment missing. Run: make install"; exit 1; }
	@command -v $(UV) >/dev/null || { echo "uv $(UV_VERSION) is required."; exit 1; }
	@test "$$($(UV) --version | awk '{print $$2}')" = "$(UV_VERSION)" || { echo "uv $(UV_VERSION) is required."; exit 1; }
	@test "$$($(PYTHON) --version 2>&1)" = "Python $(PYTHON_VERSION)" || { echo "Python $(PYTHON_VERSION) is required."; exit 1; }
	@command -v $(BIOME) >/dev/null || { echo "Biome $(BIOME_VERSION) is required."; exit 1; }
	@test "$$($(BIOME) --version | awk '{print $$2}')" = "$(BIOME_VERSION)" || { echo "Biome $(BIOME_VERSION) is required."; exit 1; }
	@command -v $(NODE) >/dev/null || { echo "Node.js $(NODE_VERSION) is required."; exit 1; }
	@test "$$($(NODE) --version)" = "v$(NODE_VERSION)" || { echo "Node.js $(NODE_VERSION) is required."; exit 1; }
	@command -v $(NPM) >/dev/null || { echo "npm $(NPM_VERSION) is required."; exit 1; }
	@test "$$($(NPM) --version)" = "$(NPM_VERSION)" || { echo "npm $(NPM_VERSION) is required."; exit 1; }
	@test -x $(SPECTRAL) || { echo "Spectral $(SPECTRAL_VERSION) is required. Run: make install"; exit 1; }
	@test "$$($(SPECTRAL) --version)" = "$(SPECTRAL_VERSION)" || { echo "Spectral $(SPECTRAL_VERSION) is required."; exit 1; }
	@$(UV) sync --frozen --check --extra dev

# Install all dependencies
install: venv
	$(NPM) ci --ignore-scripts --no-audit --no-fund
	@echo "Dependencies installed successfully"

# Download specifications from the immutable api-specs GitHub release.
download:
	$(PYTHON) -m scripts.download

# Force a fresh download and digest verification of the selected release asset.
download-force:
	$(PYTHON) -m scripts.download --force

# Run unified pipeline (enrich → normalize → merge → api-viewer)
pipeline: check-deps
	@set -eu; \
	export PYTHONHASHSEED=0 PYTHONUTF8=1 TZ=UTC LC_ALL=C LANG=C; \
	VERSION=$$($(PYTHON) -m scripts.utils.version_calculator); \
	$(PYTHON) -m scripts.release.build_release_tree \
		--version "$$VERSION" \
		--root . \
		--input-dir specs/original \
		--biome $(BIOME)

# Individual steps (for debugging or development)
enrich:
	$(PYTHON) -m scripts.enrich

normalize:
	$(PYTHON) -m scripts.normalize

# Generate Scalar API viewer pages and Starlight MDX wrappers
api-viewer:
	$(PYTHON) -m scripts.generate_api_viewer

# Compile API catalog from enriched specs
catalog: ## Compile API catalog from enriched specs
	@echo "Compiling API catalog..."
	VERSION=$$($(PYTHON) -m scripts.utils.version_calculator); \
		$(PYTHON) -m scripts.compile_catalog --version "$$VERSION" \
		--input docs/specifications/api/openapi.json --output release/api-catalog.json
	@echo "Catalog compiled to release/api-catalog.json"

# Run the pytest suite (honours pyproject.toml addopts, including coverage)
test: check-deps
	$(PYTHON) -m pytest

# Lint specifications with the package-lock-pinned Spectral CLI.
lint:
	$(PYTHON) scripts/lint.py --input-dir docs/specifications/api

# Validate specifications with the live API. Production validation never skips.
validate:
	@test -n "$${F5XC_API_TOKEN:-}" || { echo "F5XC_API_TOKEN is required."; exit 1; }
	@test -n "$${F5XC_API_URL:-}" || { echo "F5XC_API_URL is required."; exit 1; }
	$(PYTHON) -m scripts.validate

# Validate domain categorization against natural identifiers in original specs
validate-domains:
	$(PYTHON) scripts/validate_domain_categorization.py

# API Discovery - explore live API to find undocumented behavior
discover:
	@if [ -z "$$F5XC_API_TOKEN" ]; then \
		echo "F5XC_API_TOKEN not set. Set credentials first."; \
		exit 1; \
	fi
	$(PYTHON) -m scripts.discover

# Discover specific namespace (usage: make discover-namespace NS=system)
discover-namespace:
	@if [ -z "$$F5XC_API_TOKEN" ]; then \
		echo "F5XC_API_TOKEN not set. Set credentials first."; \
		exit 1; \
	fi
	$(PYTHON) -m scripts.discover --namespace $(NS)

# Dry run discovery (list endpoints without making requests)
discover-dry-run:
	$(PYTHON) -m scripts.discover --dry-run

# CLI-only discovery using xcsh
discover-cli:
	@if [ -z "$$F5XC_API_TOKEN" ]; then \
		echo "F5XC_API_TOKEN not set. Set credentials first."; \
		exit 1; \
	fi
	$(PYTHON) -m scripts.discover --cli-only

# Generate constraint comparison report
constraint-report:
	$(PYTHON) -m scripts.analyze_constraints

# Constraint boundary audit targets
audit: check-deps ## Probe healthcheck constraints against live API (requires F5XC_API_TOKEN)
	@if [ -z "$$F5XC_API_TOKEN" ]; then \
		echo "F5XC_API_TOKEN not set. Set credentials first."; \
		exit 1; \
	fi
	@mkdir -p reports/audit
	$(PYTHON) -m scripts.discovery.constraint_prober --resource healthcheck --output reports/audit/healthcheck.json

audit-dry-run: check-deps ## Generate probes without API calls
	$(PYTHON) -m scripts.discovery.constraint_prober --resource healthcheck --dry-run

audit-report: ## Display results from last audit run
	@if [ -f reports/audit/healthcheck.json ]; then \
		$(PYTHON) -c "import json; r=json.load(open('reports/audit/healthcheck.json')); print(f'Fields probed: {len(r[\"fields\"])}'); print(f'Probes: {r[\"probes_executed\"]} executed, {r[\"probes_accepted\"]} accepted, {r[\"probes_rejected\"]} rejected'); print(f'Server defaults: {list(r[\"server_default_fields\"].keys())}')"; \
	else \
		echo "No audit report found. Run 'make audit' or 'make audit-dry-run' first."; \
	fi

# Serve documentation locally
serve:
	@echo "Starting local server at http://localhost:8000"
	@echo "Press Ctrl+C to stop"
	@cd docs && $(PYTHON) -m http.server 8000

# Clean generated files (preserves original specs)
clean:
	rm -rf docs/specifications/api/*.json
	rm -rf docs/specifications/api/viewer
	rm -rf docs/api-reference
	rm -rf reports
	@echo "Cleaned generated files. Original specs preserved."

# Deep clean - removes everything including downloaded specs
# The committed generated index is the build-version authority.
clean-all: clean
	rm -rf specs/original
	rm -rf specs/discovered
	@echo "Deep clean complete. Run 'make download' to fetch specs."

# Quick rebuild - skip download, run pipeline only
rebuild: pipeline

# Install pre-commit hooks
pre-commit-install: check-deps
	$(PYTHON) -m pre_commit install
	chmod +x scripts/hooks/pre-commit-pipeline.sh
	@echo "Pre-commit hooks installed successfully"

# Run pre-commit on all files (for CI or manual check)
pre-commit-run: check-deps
	$(PYTHON) -m pre_commit run --all-files

# Uninstall pre-commit hooks
pre-commit-uninstall:
	$(PYTHON) -m pre_commit uninstall
	@echo "Pre-commit hooks uninstalled"

# Help
help:
	@echo "F5 XC API Enrichment Pipeline"
	@echo ""
	@echo "Simplified two-folder architecture:"
	@echo "  specs/original/          - READ-ONLY immutable corrected api-specs release asset"
	@echo "  docs/specifications/api/ - Merged domain specs (GitHub Pages)"
	@echo ""
	@echo "Usage: make [target]"
	@echo ""
	@echo "Main targets:"
	@echo "  build          Full pipeline (download → pipeline)"
	@echo "  rebuild        Quick rebuild (skip download, use existing original specs)"
	@echo "  serve          Start local server to preview docs"
	@echo "  clean          Remove generated files (keeps original specs)"
	@echo "  clean-all      Remove all generated files including downloads"
	@echo ""
	@echo "Download (immutable api-specs releases):"
	@echo "  download       Download specs when the release identity changes"
	@echo "  download-force Download and verify the release asset again"
	@echo ""
	@echo "Pipeline:"
	@echo "  pipeline       Run unified pipeline (enrich → normalize → merge)"
	@echo ""
	@echo "Individual steps (for debugging):"
	@echo "  enrich         Apply branding, acronyms, grammar"
	@echo "  normalize      Fix orphan refs, clean operations"
	@echo "  api-viewer     Generate API catalog page and plugin config"
	@echo "  lint           Validate specs with Spectral OpenAPI linter"
	@echo "  test           Run the pytest suite"
	@echo "  validate       Test with live API (needs credentials)"
	@echo "  validate-domains  Validate domain patterns against natural identifiers"
	@echo ""
	@echo "API Discovery (explore live API for undocumented behavior):"
	@echo "  discover           Full API discovery (needs F5XC_API_TOKEN)"
	@echo "  discover-namespace Discover specific namespace (NS=system)"
	@echo "  discover-dry-run   List endpoints without making requests"
	@echo "  discover-cli       CLI-only discovery using xcsh"
	@echo ""
	@echo "Discovery Evidence (non-publishable local analysis only):"
	@echo "  constraint-report      Generate constraint comparison report"
	@echo ""
	@echo "Setup:"
	@echo "  install        Install Python and Node.js dependencies"
	@echo "  check-deps     Verify all dependencies are installed"
	@echo ""
	@echo "Pre-commit:"
	@echo "  pre-commit-install    Install git pre-commit hooks"
	@echo "  pre-commit-run        Run all pre-commit hooks manually"
	@echo "  pre-commit-uninstall  Remove pre-commit hooks"
