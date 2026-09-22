## Slack Read Tools

**Note:** You must connect your Slack account first by visiting `/auth/slack`

Read Slack through dedicated dynamic tools invoked via `tool_call`:

| Tool | Description |
|------|-------------|
| `list_slack_teams` | List all accessible workspaces (team IDs are T-prefixed) |
| `list_slack_conversations` | List channels/DMs/group messages visible to the user |
| `get_slack_conversation_history` | Get messages from a channel or DM |
| `get_slack_conversation_replies` | Get the replies in a thread |
| `search_slack_messages` | Search messages (Slack search syntax) |
| `get_slack_user_info` | Get information about a user by user ID |
| `list_slack_users` | List all users in a workspace |
| `find_slack_channel` | Look up channel IDs by name substring (for sends) |

**Workspace Selection (team_id):**
- `list_slack_conversations`, `search_slack_messages`, and `list_slack_users` accept an optional `team_id` to target a specific workspace
- Use `list_slack_teams` to discover available workspaces
- If `team_id` is omitted, the user's default workspace is used

**Example calls:**

```
# List available workspaces in the organization
tool_call(tool_name="list_slack_teams", arguments={})

# Search for messages
tool_call(tool_name="search_slack_messages", arguments={"query": "project update"})

# List channels in a specific workspace
tool_call(tool_name="list_slack_conversations", arguments={"team_id": "T1234567890"})

# List all channels and DMs (default workspace)
tool_call(tool_name="list_slack_conversations", arguments={"types": "public_channel,private_channel,im,mpim"})

# Get channel history
tool_call(tool_name="get_slack_conversation_history", arguments={"channel": "C1234567890"})

# Get thread replies
tool_call(tool_name="get_slack_conversation_replies", arguments={"channel": "C1234567890", "ts": "1234567890.123456"})

# Get user info
tool_call(tool_name="get_slack_user_info", arguments={"user_id": "U1234567890"})

# List all users
tool_call(tool_name="list_slack_users", arguments={})
```

**Important Notes:**
- Channel IDs start with 'C' (e.g., C1234567890)
- User IDs start with 'U' (e.g., U1234567890)
- Timestamps are in Unix timestamp format with microseconds (e.g., 1234567890.123456)
- List results are capped at 50 per call -- **paginate** with the `cursor` from `response_metadata.next_cursor` while the response has `has_more: true` (`search_slack_messages` paginates by `page` number instead)
- Slack search syntax: https://slack.com/help/articles/202528808-Search-in-Slack

### Slack reads from scripts (run_python / run_script)

There are no per-method HTTP proxy endpoints, but sandbox code can invoke the
same tools through the script tool-call bridge: POST to
`/api/tool-call` on the sandbox tool API (base URL built from the injected
`QUEST_PORT` environment variable) with a JSON body of
`{"tool_name": "...", "arguments": {...}}`, authenticated with the
injected `QUEST_API_KEY` environment variable. Example:

```python
import os, requests
resp = requests.post(
    f"http://localhost:{os.environ['QUEST_PORT']}/api/tool-call",
    headers={"Authorization": f"Bearer {os.environ['QUEST_API_KEY']}"},
    json={"tool_name": "get_slack_conversation_history",
          "arguments": {"channel": "C1234567890", "limit": 50}},
)
data = resp.json()
```

---

## Slack Write Operations (via create_action_request)

Sending Slack messages to other people or channels goes through
`create_action_request` so the user can approve the outgoing message
before it is posted. Never POST to `chat.postMessage` (or any Slack
write endpoint) directly. `create_action_request` is top-level only --
if you are running as a sub-agent, do not call it; return the proposed
`request_type` and `params` to the parent via `agent_task_response`
instead.

**Use `find_slack_channel(search)` (via `tool_call`) to look up channel
ids by name before sending.**

### `send_slack_message` — send to any channel or DM (preferred)

Parameters:
- `channel_id` (string, required): Either a channel id (starts with
  `C`) or a user id (starts with `U`). When a user id is passed, a DM
  conversation is opened automatically.
- `message` (string, required): The message body. Uses Slack mrkdwn
  formatting — see "Slack mrkdwn" below.
- `thread_ts` (string, optional): A Slack message timestamp (e.g.
  `"1234567890.123456"`). When provided, the new message is posted as a
  threaded reply to that parent message.

Example params:
```json
{"channel_id": "C1234567", "message": "Hello!", "thread_ts": "1234567890.123456"}
```

### `send_slack_dm` — legacy DM send

Parameters:
- `user_id` (string, required): A Slack user id (`U...`).
- `message` (string, required).

Prefer `send_slack_message` with a `U`-prefixed `channel_id`.

### Slack mrkdwn (NOT standard Markdown)

Slack messages use Slack's mrkdwn format, which differs from regular
Markdown. When composing messages:
- bold = `*single asterisks*` (NOT `**double asterisks**`)
- italic = `_underscores_`
- strikethrough = `~tildes~`
- inline code = backticks; code blocks = triple backticks
- links = `<https://example.com|link text>` (angle brackets + pipe)
- bullets use plain `- ` or `• `

### Exception: messaging the user themselves
Use the `send_slack_dm_to_self` dynamic tool (via `tool_call`). It does
NOT require approval and is the right choice for "send me a reminder" /
"DM me" style requests and for scheduled routines. It optionally attaches
workspace files (`files`: workspace-relative paths, max 10, 50 MB each):

```
tool_call(tool_name="send_slack_dm_to_self", arguments={"message": "Report ready!", "files": ["output/report.pdf"]})
```

Messages are delivered by the Quest bot, not sent as the user. Sandbox
scripts can send text-only self-DMs through the script tool-call bridge
("Slack reads from scripts" above -- the same `POST /api/tool-call`
shape); file attachments need conversation context and are
conversation-tool-only.
