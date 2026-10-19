# GCP three-node HA qualification

Issue: <https://github.com/f5-sales-demo/api-specs-enriched/issues/1951>.

The immutable v12.1.3 OpenAPI declares `enable_ha` on create, read and replace
specifications. It also declares a GET component-status operation returning
`status[].node`, `name`, `status` and `message`. The candidate checker validates
these exact shapes and parameters without changing runtime admission.
It also checks the independent GET `list-service` operation and its
`service[].node`, `name` and `value` fields. Service identifiers use `value`;
the human display name is a separate field. Missing identities, duplicate
parameters and inferred node selectors fail candidate validation.

The private API-only campaign retained `enable_ha`, created three distinct
original token identities with node labels and exact site binding, then rechecked
each original identity before deletion. Four independent absence checks passed.
The sanitized receipt is `config/evidence/gcp-ha-api-only-20261019.json`.

The component-status request returned HTTP 502 on the node-less site. This result
provides no component-health evidence. Schema examples such as `Ready` cannot
establish exhaustive critical-service coverage or healthy HA nodes.
The [service inventory API documentation](https://docs.cloud.f5.com/docs-v2/platform/reference/api-ref/ves-io-schema-operate-debug-custompublicapi-listvolterraservices)
provides a separate inventory response. Its examples do not establish which
services are critical, inventory completeness or health acceptance. A live
qualification must compare inventory and status for each exact owned node,
preserve missing, duplicate, foreign and unavailable observations, and qualify
negative states before these observations can widen HA admission.

A [private Terraform candidate campaign](https://github.com/f5-sales-demo/marketplace/blob/4de636f7/docs/receipts/gcp-ha-candidate-deployment-20261019.md)
subsequently verified three ONLINE nodes, both physical NIC joins on each node,
two matching-node observations, a no-change plan and all eighteen original
identities. Its separate delete-only cleanup ended with an empty destroy plan
and eighteen independent absences. Recovery preserved the original submission.
The original executable source and the ownership repair are attributed separately
in that receipt. Every returned component row was `Ready`; exhaustive health
and public HA admission remain unqualified.

Before HA admission, qualification still requires an isolated three-node boot,
ONLINE registration and both physical NIC joins for each exact owned VM,
qualified per-node or complete cluster health observations, a no-change plan,
and complete and partial cleanup with original identities and owning state.
The public global-health observation remains limited to one matching hostname.
HA, routing, upgrades and failover remain unavailable in the owning contract.
