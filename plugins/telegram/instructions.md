## Telegram

**Note:** The user connects their Telegram account in Settings > Data
Connections > Telegram (phone number, then the login code Telegram sends
to their app). If a call returns `telegram_not_connected` or
`telegram_session_expired`, tell the user to (re)connect there.

Telegram reads are dedicated dynamic tools invoked through `tool_call`.
There are NO `/api/telegram/*` HTTP endpoints -- do not use
`curl_proxy_get` for Telegram.

**Read tools (via `tool_call`):**

| Tool | Description |
|------|-------------|
| `telegram_get_me` | Get your Telegram profile info |
| `telegram_list_dialogs` | List chats, groups, and channels (`limit`, default 20) |
| `telegram_get_messages` | Get messages from one dialog (`dialog_id` required; `limit`, `offset_id`, `offset_date`, `reverse`) |
| `telegram_list_contacts` | List your Telegram contacts |

**Example calls:**

```
# Your Telegram profile
tool_call(tool_name="telegram_get_me", arguments={})

# Recent dialogs (chats, groups, channels)
tool_call(tool_name="telegram_list_dialogs", arguments={"limit": 50})

# Latest messages from a dialog (newest first)
tool_call(tool_name="telegram_get_messages", arguments={"dialog_id": 123456789, "limit": 100})

# Next page: pass the id of the last message received
tool_call(tool_name="telegram_get_messages", arguments={"dialog_id": 123456789, "limit": 100, "offset_id": 5000})

# Messages before a specific date
tool_call(tool_name="telegram_get_messages", arguments={"dialog_id": 123456789, "offset_date": "2024-01-15T10:30:00Z"})

# Chronological order starting from a date
tool_call(tool_name="telegram_get_messages", arguments={"dialog_id": 123456789, "offset_date": "2024-01-13T00:00:00Z", "reverse": true, "limit": 200})

# Your contacts
tool_call(tool_name="telegram_list_contacts", arguments={})
```

**`telegram_get_me` result:**
```json
{
  "id": 123456789,
  "username": "johndoe",
  "first_name": "John",
  "last_name": "Doe",
  "phone": "+1234567890",
  "is_premium": false
}
```

**`telegram_list_dialogs` result:**
```json
{
  "dialogs": [
    {
      "id": 123456789,
      "name": "John Doe",
      "is_user": true,
      "is_group": false,
      "is_channel": false,
      "unread_count": 5,
      "last_message_date": "2025-01-15T10:30:00+00:00"
    }
  ]
}
```

**`telegram_get_messages` result:**
```json
{
  "messages": [
    {
      "id": 12345,
      "date": "2025-01-15T10:30:00+00:00",
      "text": "Hello, how are you?",
      "sender_id": 987654321,
      "is_outgoing": false,
      "reply_to_msg_id": null,
      "has_media": false
    }
  ]
}
```

**Important Notes:**
- Dialog IDs are numeric and come from `telegram_list_dialogs` (or `telegram_list_contacts` for users)
- Messages are returned in reverse chronological order (newest first) unless `reverse` is true
- Use `offset_id` for pagination by passing the ID of the last message received
- Telegram sessions are kept alive for the duration of the server - no reconnection needed per call
- Errors come back as `{"error": "...", "message": "..."}` (e.g. `telegram_not_connected`, `telegram_session_expired`, `invalid_date`)

### Telegram reads from scripts (run_python / run_script)

There are no HTTP proxy endpoints, but sandbox code can invoke the same
tools through the script tool-call bridge: POST to `/api/tool-call` on
the sandbox tool API (base URL built from the injected `QUEST_PORT`
environment variable) with a JSON body of
`{"tool_name": "...", "arguments": {...}}`, authenticated with the
injected `QUEST_API_KEY` environment variable. Example:

```python
import os, requests
resp = requests.post(
    f"http://localhost:{os.environ['QUEST_PORT']}/api/tool-call",
    headers={"Authorization": f"Bearer {os.environ['QUEST_API_KEY']}"},
    json={"tool_name": "telegram_get_messages",
          "arguments": {"dialog_id": 123456789, "limit": 100}},
)
data = resp.json()
```

---

## Telegram Write Operations (via create_action_request)

Sending Telegram messages goes through `create_action_request` so the
user can approve the outgoing text before it is delivered. There is no
direct send tool or endpoint. `create_action_request` is top-level only
-- if you are running as a sub-agent, do not call it; return the
proposed `request_type` and `params` to the parent via
`agent_task_response` instead.

### `send_telegram_message` — send a message to a Telegram dialog

Parameters:
- `dialog_id` (int, required): Numeric Telegram dialog id (user, group,
  or channel). Look it up with `telegram_list_dialogs` or
  `telegram_list_contacts` before sending.
- `message` (string, required): The plain-text message body.

Example params:
```json
{"dialog_id": 123456789, "message": "Hello!"}
```

Notes:
- Telegram messages are plain text — no Markdown / mrkdwn / HTML
  formatting. Line breaks are real newline characters.
- Emoji should be direct Unicode characters, not escape sequences.
