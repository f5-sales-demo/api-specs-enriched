# Upstream contract changes

- Baseline: `v2026.10.28-1` / `api-specs-v2026.10.28-1.zip` / `sha256:bab6b9c106f95c0f13c954a2163ef1e96a2dde7acde485c50910ad026ba23f0e`
- Target: `v2026.11.09-1` / `api-specs-v2026.11.09-1.zip` / `sha256:fad1c18fbc11b37ca759a2ca06064972ac17b7d7705d81293757196072a85d1a`
- Operations: 1840 baseline, 1888 target, 61 added, 13 removed
- Schema properties: 808 added, 370 removed, 951 modified

## Added operations

- `POST /api/config/dns/namespaces/system/dns_zone/namespace_rbac`
- `POST /api/config/namespaces/{metadata.namespace}/acme_issuers`
- `PUT /api/config/namespaces/{metadata.namespace}/acme_issuers/{metadata.name}`
- `GET /api/config/namespaces/{namespace}/acme_issuers`
- `DELETE /api/config/namespaces/{namespace}/acme_issuers/{name}`
- `GET /api/config/namespaces/{namespace}/acme_issuers/{name}`
- `POST /api/config/namespaces/{namespace}/certificates/{name}/retry`
- `POST /api/config/namespaces/{namespace}/certificates/{name}/revoke`
- `POST /api/config/namespaces/{namespace}/dynamic-default-values`
- `GET /api/config/namespaces/{namespace}/site_signaturess`
- `GET /api/config/namespaces/{namespace}/site_signaturess/{name}`
- `POST /api/data/namespaces/{namespace}/l7_ddos/incidents`
- `POST /api/data/namespaces/{namespace}/tmm_session/top/virtual_servers`
- `POST /api/device-intelligence/custom/namespaces/{namespace}/di_rules`
- `GET /api/device-intelligence/custom/namespaces/{namespace}/di_rules/deployments/pending`
- `POST /api/device-intelligence/namespaces/{namespace}/devices/device-history`
- `POST /api/device-intelligence/namespaces/{namespace}/devices/device-summary`
- `GET /api/device-intelligence/namespaces/{namespace}/di_rules`
- `GET /api/device-intelligence/namespaces/{namespace}/di_rules/{id}/history`
- `GET /api/device-intelligence/namespaces/{namespace}/di_rules/{name}`
- `POST /api/shape/bot/custom/namespaces/{namespace}/bot_allowlist_policys/{name}/deploy`
- `GET /api/shape/bot/custom/namespaces/{namespace}/bot_deployments`
- `GET /api/shape/bot/custom/namespaces/{namespace}/bot_deployments/stats`
- `GET /api/shape/bot/custom/namespaces/{namespace}/bot_deployments/{deployment_id}/details`
- `POST /api/shape/bot/custom/namespaces/{namespace}/bot_endpoint_policys/{name}/deploy`
- `GET /api/shape/bot/custom/namespaces/{namespace}/bot_infrastructures`
- `POST /api/shape/bot/custom/namespaces/{namespace}/bot_network_policys/{name}/deploy`
- `POST /api/shape/bot/namespaces/{metadata.namespace}/bot_fullsite_policys`
- `PUT /api/shape/bot/namespaces/{metadata.namespace}/bot_fullsite_policys/{metadata.name}`
- `GET /api/shape/bot/namespaces/{namespace}/bot_fullsite_policies/mitigation_handling_status`
- `GET /api/shape/bot/namespaces/{namespace}/bot_fullsite_policies/top_known_bots_allowed`
- `GET /api/shape/bot/namespaces/{namespace}/bot_fullsite_policies/top_known_bots_denied`
- `GET /api/shape/bot/namespaces/{namespace}/bot_fullsite_policys`
- `DELETE /api/shape/bot/namespaces/{namespace}/bot_fullsite_policys/{name}`
- `GET /api/shape/bot/namespaces/{namespace}/bot_fullsite_policys/{name}`
- `POST /api/shape/bot/namespaces/{namespace}/bot_infrastructures/get_infra_deployment_status`
- `GET /api/shape/bot/namespaces/{namespace}/feature_flags`
- `POST /api/shape/csd/namespaces/{namespace}/scripts/{id}/cookie-operations`
- `POST /api/shape/csd/namespaces/{namespace}/scripts/{id}/dom-mutations`
- `POST /api/shape/csd/namespaces/{namespace}/scripts/{id}/storage-operations`
- `POST /api/web/namespaces/system/activate/site_global_kube_configs/{name}`
- `POST /api/web/namespaces/system/revoke/site_global_kube_configs/{name}`
- `GET /api/web/namespaces/system/site_global_kube_configs`
- `POST /api/web/namespaces/system/site_global_kube_configs`
- `GET /api/web/namespaces/system/site_global_kube_configs/{name}`
- `PUT /api/web/namespaces/system/user/settings/time_zone`
- `POST /api/web/namespaces/{metadata.namespace}/entitlements`
- `PUT /api/web/namespaces/{metadata.namespace}/entitlements/{metadata.name}`
- `POST /api/web/namespaces/{namespace}/activate/kube_configs/{name}`
- `POST /api/web/namespaces/{namespace}/activate/service_kube_configs/{name}`
- `GET /api/web/namespaces/{namespace}/entitlements`
- `DELETE /api/web/namespaces/{namespace}/entitlements/{name}`
- `GET /api/web/namespaces/{namespace}/entitlements/{name}`
- `GET /api/web/namespaces/{namespace}/kube_configs`
- `POST /api/web/namespaces/{namespace}/kube_configs`
- `GET /api/web/namespaces/{namespace}/kube_configs/{name}`
- `POST /api/web/namespaces/{namespace}/revoke/kube_configs/{name}`
- `POST /api/web/namespaces/{namespace}/revoke/service_kube_configs/{name}`
- `GET /api/web/namespaces/{namespace}/service_kube_configs`
- `POST /api/web/namespaces/{namespace}/service_kube_configs`
- `GET /api/web/namespaces/{namespace}/service_kube_configs/{name}`

## Removed operations

- `POST /api/config/namespaces/{metadata.namespace}/application_profiless`
- `PUT /api/config/namespaces/{metadata.namespace}/application_profiless/{metadata.name}`
- `GET /api/config/namespaces/{namespace}/application_profiless`
- `DELETE /api/config/namespaces/{namespace}/application_profiless/{name}`
- `GET /api/config/namespaces/{namespace}/application_profiless/{name}`
- `POST /api/config/namespaces/{namespace}/oidc_oauth_discovery`
- `GET /api/config/namespaces/{namespace}/virtual_servers/{name}/associated-objects`
- `GET /api/data/namespaces/{namespace}/access/active-sessions`
- `POST /api/data/namespaces/{namespace}/access/active-sessions/terminate`
- `DELETE /api/data/namespaces/{namespace}/access/active-sessions/{id}`
- `GET /api/data/namespaces/{namespace}/access/active-sessions/{id}`
- `POST /api/device-intelligence/namespaces/{namespace}/devices/{device_id}/history`
- `POST /api/device-intelligence/namespaces/{namespace}/devices/{device_id}/summary`

The complete sorted operation inventories and schema-property additions, removals, and modifications (including full before/after values) are in `upstream-contract-changes.json`.
