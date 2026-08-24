# F5 XC API Enrichment Pipeline Makefile
# Local builds produce identical output to GitHub Actions workflow
#
# Simplified two-folder architecture:
#   specs/original/          - READ-ONLY source from F5 (gitignored, downloaded on demand)
#   docs/specifications/api/ - Merged domain specs (served directly by GitHub Pages)
#
# Release-based caching uses the stable upstream identity and six-field receipt.
#
# Usage:
#   make build          - Full pipeline (download → enrich → normalize → merge)
#   make download       - Download the pinned stable upstream release
#   make download-force - Force download even if unchanged
#   make pipeline       - Run unified pipeline (enrich + normalize + merge)
#   make serve          - Serve docs locally
#   make clean          - Remove generated files
#   make install        - Install dependencies
#
# The pipeline ensures deterministic output:
#   specs/original/          → READ-ONLY source from F5
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

.PHONY: all build clean install download download-force pipeline enrich normalize merge lint pylint validate validate-domains serve help check-deps venv pre-commit-install pre-commit-run pre-commit-uninstall discover discover-namespace discover-dry-run discover-cli enrich-with-discovery constraint-report build-enriched pipeline-enriched push-discovery discover-and-push api-viewer catalog test

# Virtual environment
VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

# Default target
all: build

# Full pipeline - matches GitHub Actions workflow exactly
build: check-deps download pipeline
	@echo ""
	@echo "Build complete. Output in:"
	@echo "  docs/specifications/api/  - Merged domain API specifications (GitHub Pages)"
	@echo ""
	@echo "Run 'make serve' to preview locally"

# Create virtual environment
venv: $(VENV)/bin/activate

$(VENV)/bin/activate:
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip

# Check dependencies are installed
check-deps:
	@test -d $(VENV) || { echo "Virtual environment missing. Run: make install"; exit 1; }
	@$(PYTHON) -c "import rich" 2>/dev/null || { echo "Python dependencies missing. Run: make install"; exit 1; }

# Install all dependencies
install: venv
	$(PIP) install -r requirements.txt
	@echo "Dependencies installed successfully"

# Download specifications from the latest stable upstream GitHub release
download:
	$(PYTHON) -m scripts.download

# Force download even if the recorded release identity is unchanged
download-force:
	$(PYTHON) -m scripts.download --force

# Run unified pipeline (enrich → normalize → merge → api-viewer)
pipeline:
	$(PYTHON) -m scripts.pipeline
	$(PYTHON) -m scripts.generate_api_viewer

# Individual steps (for debugging or development)
enrich:
	$(PYTHON) -m scripts.enrich

normalize:
	$(PYTHON) -m scripts.normalize

merge:
	$(PYTHON) -m scripts.merge_specs

# Generate Scalar API viewer pages and Starlight MDX wrappers
api-viewer:
	$(PYTHON) -m scripts.generate_api_viewer

# Compile API catalog from enriched specs
catalog: ## Compile API catalog from the canonical OpenAPI master
	@echo "Compiling API catalog..."
	$(PYTHON) -m scripts.compile_catalog --input docs/specifications/api/openapi.json --output release/api-catalog.json
	@echo "Catalog compiled to release/api-catalog.json"

# Run the pytest suite (honours pyproject.toml addopts, including coverage)
test: check-deps
	$(PYTHON) -m pytest

pylint: check-deps
	$(PYTHON) -m pylint scripts tests

# Lint specifications with Spectral (requires: npm install -g @stoplight/spectral-cli)
lint:
	$(PYTHON) scripts/lint.py --input-dir docs/specifications/api

# Validate specifications with live API (optional, requires credentials)
validate:
	@if [ -z "$$F5XC_API_TOKEN" ]; then \
		echo "F5XC_API_TOKEN not set. Skipping live validation."; \
	else \
		$(PYTHON) -m scripts.validate --dry-run; \
	fi

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

# Enrichment with discovery data (adds x-discovered-* extensions)
enrich-with-discovery:
	$(PYTHON) -m scripts.enrich --use-discovery

# Generate constraint comparison report
constraint-report:
	$(PYTHON) -m scripts.analyze_constraints

# Push discovery data to GitHub (run after make discover)
# Commits full openapi.json (25MB) for git diff visibility and CI/CD enrichment
push-discovery:
	@if [ ! -f specs/discovered/session.json ]; then \
		echo "No discovery data. Run 'make discover' first."; \
		exit 1; \
	fi
	git add specs/discovered/session.json specs/discovered/openapi.json
	git commit -m "chore: update API discovery data"
	git push
	@echo ""
	@echo "Discovery data pushed (uncompressed for diff visibility)."

# Full local discovery workflow (discover + push)
discover-and-push: discover push-discovery

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

# Full pipeline with discovery enrichment
build-enriched: check-deps download discover pipeline-enriched
	@echo ""
	@echo "Build complete with discovery enrichment. Output in:"
	@echo "  docs/specifications/api/  - Enriched domain API specifications"
	@echo "  reports/                  - Constraint analysis report"
	@echo ""
	@echo "Run 'make serve' to preview locally"

# Pipeline with discovery enrichment
pipeline-enriched:
	$(PYTHON) -m scripts.pipeline
	$(PYTHON) -m scripts.generate_api_viewer
	$(PYTHON) -m scripts.analyze_constraints

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
# The release workflow commits the explicit build version with generated artifacts.
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
	@echo "  specs/original/          - READ-ONLY source from F5 (gitignored, downloaded on demand)"
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
	@echo "Download (stable release identity and verified digest):"
	@echo "  download       Download specs when the upstream release changes"
	@echo "  download-force Force download even if release identity is unchanged"
	@echo ""
	@echo "Pipeline:"
	@echo "  pipeline       Run unified pipeline (enrich → normalize → merge)"
	@echo ""
	@echo "Individual steps (for debugging):"
	@echo "  enrich         Apply branding, acronyms, grammar"
	@echo "  normalize      Fix orphan refs, clean operations"
	@echo "  merge          Combine specs by domain"
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
	@echo "Discovery Enrichment (enhance specs with real API behavior):"
	@echo "  enrich-with-discovery  Enrich specs with discovery data (x-discovered-* extensions)"
	@echo "  constraint-report      Generate constraint comparison report"
	@echo "  build-enriched         Full pipeline with discovery (download → discover → pipeline)"
	@echo "  push-discovery         Commit and push discovery data to GitHub (for CI consumption)"
	@echo "  discover-and-push      Full workflow: discover + push (behind VPN → GitHub Actions)"
	@echo ""
	@echo "Setup:"
	@echo "  install        Install Python and Node.js dependencies"
	@echo "  check-deps     Verify all dependencies are installed"
	@echo ""
	@echo "Pre-commit:"
	@echo "  pre-commit-install    Install git pre-commit hooks"
	@echo "  pre-commit-run        Run all pre-commit hooks manually"
	@echo "  pre-commit-uninstall  Remove pre-commit hooks"
