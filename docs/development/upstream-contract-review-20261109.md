# Upstream contract qualification

Reviewed transition: `v2026.10.28-1` to `v2026.11.09-1`.

Tracking issue: #2021.

The upstream release identities and archive hashes were verified before rebuilding.
This review covers the exported source and canonical publication contract.
It does not establish live-server retirement or namespace CRUD acceptance.

## Reviewed removals

### GRE connectors

5 removed source entries.

Current upstream removes GRE alternatives; the current CE reference and IPsec fields are retained.

### HTTP caching

3 removed source entries.

Current upstream removes caching_policy; exported caching and CDN choices are retained without inventing a replacement.

### OIDC discovery

3 removed source entries.

Current upstream no longer exports this discovery operation and its request/response schemas.

### Access sessions

12 removed source entries.

Current upstream removes this access-session surface; obsolete routes are not carried into the canonical bundle.

### Application metrics

5 removed source entries.

Current upstream removes the legacy timeseries-analysis setting and associated metric schemas.

### Bot infrastructure

5 removed source entries.

Current upstream replaces legacy fields with ip_address_list, production_v2 and testing_v2.

### Crawler metadata

3 removed source entries.

Current upstream removes obsolete crawler metadata and recalculates shared schema aliases.

### Device intelligence

7 removed source entries.

Current upstream changes device data shapes and relocates history/summary operations to POST device-history/device-summary.

### Legacy big-ip/tmm surfaces

47 removed source entries.

Current upstream removes these legacy routes and schemas; the retained DDoS profile is preserved.

### Security schema ownership

22 removed source entries.

The canonical source merger recalculates schema ownership; retained rule-suggestion definitions and references are checked independently.

### Service credentials

4 removed source entries.

Current upstream replaces legacy password/type/virtual-Kubernetes inputs with the exported certificate, token and kubeconfig generators.

### Shared schema aliases

4 removed source entries.

Current upstream removes this shared alias; current canonical reference closure is validated by the repository pipeline.

### Virtual-server associations

2 removed source entries.

Current upstream removes the virtual-server association operation and response schema.

## Reviewed additions

SecureMesh v2 provider guidance includes all 16 exported choices. New choices are
AKS Kubernetes, GKE Kubernetes, Hyper-V and OCI Kubernetes.

ACME issuer, bot fullsite policy and entitlement have explicit namespace profiles.
Their placement remains unverified and advisory; existing verified constraints are retained.

## Evidence and verification

All 122 fingerprints, dispositions and retained-definition alternatives are recorded in
`config/upstream_review_20261109.json`. Dated acknowledgements reference issue #2021.
The complete values and operation changes are in the generated upstream contract reports.

Publication requires the canonical pipeline, reference and coverage checks, contract-diff
gate, tests, and lint to pass. Failed candidates retain the prior published specification tree.
