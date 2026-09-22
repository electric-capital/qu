# UniFi Setup

How to connect Quest to a UniFi OS console (UDM / UDR / UCG / UNVR / Cloud Key Gen2+ / self-hosted Network application on UniFi OS) for the `plugins/unifi` plugin. See [unifi-api.md](unifi-api.md) for what the integration does.

## Requirements

- The Quest server must reach the console directly over the network (`https://<controller>`); the plugin never goes through unifi.ui.com / Site Manager.
- UniFi Network 9.0 or newer (the local Network Integration API) and, for cameras, UniFi Protect 5.3 or newer (the local Protect Integration API). Older releases have no API-key support.
- Each application issues its own API key. A Network key does not work on Protect and vice versa.

## Admin: controller host

Settings > Service Credentials > UniFi (admin only):

- **Controller host** -- the console's address, e.g. `https://192.168.1.1` or `unifi.example.lan` (the `https://` scheme is added automatically; a custom port is allowed, a path is not).
- **Verify the controller's TLS certificate** -- leave off for the default self-signed certificate. Turn on only if the console serves a certificate the Quest server trusts.

The connector row appears in every user's Settings > Data Connections once the host is saved.

## User: API keys

1. **Network key:** open the Network application on the console, then Settings > Control Plane > Integrations > Create API Key. Copy the key (it is shown once).
2. **Protect key:** open the Protect application, then Settings > Control Plane > Integrations > Create API Key.
3. In Quest, Settings > Data Connections > "+ Add Connection" > UniFi opens the connect popup. Paste one or both keys and click **Test & save** -- each key is checked against its application before it is stored, so a typo is reported immediately.

Return to the same popup later to add the second key, replace a regenerated key (paste the new one; blank fields keep the stored key), remove one key (its "Remove" checkbox), or **Disconnect** both.

## Troubleshooting

- *"The Network/Protect application rejected that API key"* -- the key was created in the other application, was revoked, or was pasted with a typo. Create a fresh key in the right application.
- *"Could not connect to the UniFi controller"* -- the Quest server cannot reach the host; check the address, firewall rules, and that the console is on the same network or routable.
- A TLS error with verification on -- the console's certificate is not trusted by the Quest server; turn verification off or install a trusted certificate on the console.
- Network status without WAN/LAN/WLAN subsystem detail -- the console does not accept the API key on the legacy health endpoint; device and gateway state are still reported.
