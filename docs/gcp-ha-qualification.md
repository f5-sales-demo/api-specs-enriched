# GCP three-node HA qualification

Issue: <https://github.com/f5-sales-demo/api-specs-enriched/issues/1951>.

The immutable v12.1.3 OpenAPI declares `enable_ha` on create, read and replace
specifications. It also declares a GET component-status operation returning
`status[].node`, `name`, `status` and `message`. The candidate checker validates
these exact shapes and parameters without changing runtime admission.

The private API-only campaign retained `enable_ha`, created three distinct
original token identities with node labels and exact site binding, then rechecked
each original identity before deletion. Four independent absence checks passed.
The sanitized receipt is `config/evidence/gcp-ha-api-only-20261019.json`.

The component-status request returned HTTP 502 on the node-less site. This result
provides no component-health evidence. Schema examples such as `Ready` cannot
establish exhaustive critical-service coverage or healthy HA nodes.

Before HA admission, qualification still requires an isolated three-node boot,
ONLINE registration and both physical NIC joins for each exact owned VM,
qualified per-node or complete cluster health observations, a no-change plan,
and complete and partial cleanup with original identities and owning state.
The public global-health observation remains limited to one matching hostname.
HA, routing, upgrades and failover remain unavailable in the owning contract.
