## Iru (formerly Kandji) -- Apple device management

**Note:** An admin sets the tenant API URL in Settings > Service Credentials > Iru, and the user pastes their own Iru API token in Settings > Data Connections > Iru. Tokens are minted by an Iru admin (Iru web app: Settings > Access > API Token) with **per-endpoint permissions**, so a tool can answer `iru_auth_failed` with status 403 even though other tools work: the token lacks that endpoint's permission. Say which permission is missing rather than retrying. Status 401 means the token itself was rejected (revoked / mistyped): ask the user to re-enter it.

Everything here is **read-only**: nothing on the tenant is changed. Device actions (restart, lock, erase, blank push, rename, ...) and record edits are not available yet. Device *secrets* (FileVault recovery keys, activation-lock bypass codes, recovery-lock passwords, unlock PINs) are deliberately **not** exposed -- if asked, say they must be read in the Iru web app. All tools are called via `tool_call`.

### Which tool

| Question | Tool |
|----------|------|
| Is Iru connected? How many devices / licenses? | `iru_get_tenant_info` |
| Which devices do we manage? Which Macs on macOS 13? Whose device is X? Devices in blueprint Y? | `iru_list_devices` (filters + `limit`/`offset`) |
| Everything about one device (hardware, OS, security, FileVault, users, apps, status, history) | `iru_get_device` (`device` = id, serial, or name; `sections`) |
| Is app / profile / script X installed everywhere? Where did it fail? | `iru_get_library_item_status` (`library_item_id`, `status: "ERROR"`) |
| What blueprints exist / what is in blueprint X? | `iru_list_blueprints`, `iru_get_blueprint` |
| Who is user X, which devices do they have? | `iru_get_user` (id or exact email), `iru_list_users` |
| Which tags exist? | `iru_list_tags` |
| Fleet-wide reports: FileVault, OS versions, installed apps, certificates, profiles, extensions, local users, ... | `iru_prism_query` (`category`, `filter`, `fields`) |
| Malware / PUP detections | `iru_list_threats` |
| Which CVEs affect us / which devices have CVE X? | `iru_list_vulnerabilities`, `iru_get_vulnerability` |
| Who changed what in Iru, and when? | `iru_list_audit_events` |
| Which ABM / ADE devices are assigned but never enrolled? | `iru_list_ade_devices` (`profile_status`) |

```
tool_call(tool_name="iru_list_devices", arguments={"platform": "Mac", "os_version": "13.", "ordering": "-last_check_in"})
tool_call(tool_name="iru_get_device", arguments={"device": "C02XYZ123456", "sections": ["details", "library_items"]})
tool_call(tool_name="iru_get_device", arguments={"device": "Ada's MacBook Pro", "sections": ["apps"], "app_search": "zoom"})
tool_call(tool_name="iru_get_library_item_status", arguments={"library_item_id": "4a98cb3d-...", "status": "ERROR"})
tool_call(tool_name="iru_prism_query", arguments={"category": "filevault", "filter": {"filevault_enabled": {"eq": false}}, "fields": ["device__name", "serial_number", "device__user_email", "filevault_enabled"]})
tool_call(tool_name="iru_prism_query", arguments={"category": "apps", "filter": {"name": {"like": ["Zoom"]}}, "fields": ["device__name", "name", "version"]})
```

### Working with devices

- `iru_list_devices` text filters are **contains** matches (serial, name, model, OS version, user email / name); `platform` and `tag_name` are exact. It returns compact rows; use `iru_get_device` for detail. `next_offset` is set when another page may exist -- keep paging (max 300 rows per call) when the user wants the whole fleet, and say how many you scanned.
- `iru_get_device` resolves `device` by UUID, then by serial number, then by device name. `iru_ambiguous_device` lists `candidates`; pick by `device_id` or ask the user. Sections: `details` (default -- hardware, general, MDM, security incl. FileVault / SIP / firewall, users, network), `status` / `library_items` (each library item's PASS / ERROR / PENDING / AVAILABLE / INCOMPATIBLE state with its audit log), `apps` (use `app_search` to avoid a 300-app dump), `activity`, `commands`, `parameters`, `lost_mode`. Request only the sections you need.
- Library item ids come from `iru_get_blueprint` (what a blueprint deploys) or a device's `library_items` / `status` section (`item_id`). `iru_get_library_item_status` then shows every device's state for that item; `status` filtering scans up to 3000 rows.

### Prism reports (`iru_prism_query`)

One row per device (or per app / certificate / profile / extension / local user) with the device's blueprint and assigned user on every row. Rows are wide: pass `fields` to keep only the columns you need (the result then lists `fields_available`). Filter grammar (server-side): `{"<column>": {"<op>": <value>}}` with `eq` (booleans), `in` / `not_in` (list of exact strings), `like` / `not_like` (list of substrings), `lt` / `gt` / `lte` / `gte` (dates), `or` (date window); several columns AND together. Column names are the row keys, e.g. `device__name`, `device__user_email`, `serial_number`, `os_version`, `apple_silicon`, `filevault_enabled`, `last_changed_at`. `total_unfiltered` is the whole category's row count (only reported on an unfiltered first page).

**Important Notes:**
- Rate limit: 10,000 requests/hour per tenant (`iru_rate_limited` carries `retry_after_seconds`); prefer one Prism query over hundreds of `iru_get_device` calls when reporting across the fleet.
- Timestamps are UTC ISO strings. `last_check_in` older than a day or two usually means the device is off / offline; `is_missing: true` is Iru's own missing flag.
- Users are directory users (Okta / Google / Microsoft / SCIM) -- `iru_list_users(email)` is a contains match, `iru_get_user` needs the id or exact email.
- Do not paste serial numbers, user emails, or file hashes into third-party services; they identify people's devices.
