# Iru (Kandji) Setup

How to connect Quest to an Iru (formerly Kandji) tenant for the `plugins/iru` plugin. See [iru-api.md](iru-api.md) for what the integration does.

## Requirements

- An Iru tenant. Both the new `iru.com` API hosts and the legacy `kandji.io` hosts work; the API itself is unchanged by the rebrand.
- The Quest server must reach the tenant's API host over the internet (`https://<subdomain>.api.iru.com`, or `.api.eu.iru.com` for EU tenants).
- Each Quest user needs an Iru API token. Tokens are created by an Iru admin; the permissions attached to a token decide which Quest tools work for that user.

## Admin: tenant API URL

Settings > Service Credentials > Iru (admin only):

- **Tenant API URL** -- your tenant's API origin, e.g. `https://acme.api.iru.com` (US) or `https://acme.api.eu.iru.com` (EU). The Iru web app shows this URL next to each API token (Settings > Access > API Token). Bare hosts get `https://` added and an `/api/v1` suffix is ignored; the legacy `https://acme.api.kandji.io` / `.api.eu.kandji.io` forms are accepted too. The web-app address (`https://acme.kandji.io` / `https://acme.iru.com`) is *not* the API host and is rejected.

The connector row appears in every user's Settings > Data Connections once the URL is saved.

## User: API token

1. In the Iru web app (as an Iru admin, or ask one): Settings > Access > **Add API Token**. Give it a name, copy the token (it is shown once), then open its **Permissions** tab and grant the read permissions the user needs.
   - Everything the plugin calls is a GET; a useful read-only set is Device list / Device ID / Device details / Device status / Device apps / Device activity / Device library items / Device commands / Device parameters, Blueprints (list, get, list library items), Library item status, Users, Tags, Prism (all categories + count), Threat details, Vulnerabilities, Audit events, ADE devices, and Licensing.
   - Do **not** grant the device *secrets* permissions or any device-action permission: the plugin never calls them, and a token that cannot read secrets cannot leak them.
2. In Quest: Settings > Data Connections > "+ Add Connection" > **Iru (Kandji)**, paste the token, save. The token is stored encrypted for that user only.
3. Ask Quest "Is Iru connected?" -- `iru_get_tenant_info` confirms the token is accepted and shows the licensing counts.

To replace a token (rotated or re-scoped), paste the new one over it in the same row; **Disconnect** removes it.

## Troubleshooting

- *"Iru rejected the API token"* (401) -- the token was mistyped, revoked, or belongs to another tenant. Re-create or re-paste it.
- *"Iru refused this request ... lacks the permission for this endpoint"* (403) on one tool while others work -- the token's Permissions tab is missing that endpoint. Grant it in the Iru web app; the change applies on the next call, no re-paste needed.
- *"Tenant API URL must be your tenant's API host"* on the admin card -- you entered the web-app host. Use `https://<subdomain>.api.iru.com` (or the EU / kandji.io variant).
- *"Could not connect to the Iru tenant"* -- the Quest server cannot reach the API host (DNS, egress firewall). The tenant is a public SaaS endpoint; check outbound HTTPS from the server.
- *"Iru rate limit reached"* (429) -- 10,000 requests/hour per tenant across every token. Wait for the `retry_after_seconds`; prefer Prism reports over per-device loops for fleet-wide questions.
