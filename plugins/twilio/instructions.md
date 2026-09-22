## Twilio SMS

**Note:** The user must first verify their mobile number in Settings > Data Connections > Twilio SMS (they type their number, receive a code by text, and type it back). Nothing here works until that number is verified.

There are two very different ways to send a text, and the distinction matters:

| Tool | Recipient | Approval |
|------|-----------|----------|
| `twilio_send_self_sms` (via `tool_call`) | The user's OWN verified number only | None -- but what you may send depends on the trusted-channel setting below |
| `twilio_send_sms` (via `create_action_request`) | Anyone else | Always -- the user reviews recipient + exact text before it goes out |

### Texting the user (`twilio_send_self_sms`)

Use this for "text me when...", reminders, alerts, and scheduled routines that should ping the user's phone. The recipient is fixed to the user's verified number; you never pass a phone number.

The admin decides whether SMS is a **trusted channel** (Settings > Service Credentials > Twilio SMS):

- **Trusted:** pass `body` with any plain-text message (max 1600 characters). You may also pass a `template` name instead.
- **Not trusted (the default):** free-form `body` is refused. You must pass `template` -- the name of one of the user's pre-written messages, which is sent **verbatim**. There are no placeholders and no way to append text. The user writes these messages themselves in Settings > SMS Messages; if the list is empty, tell the user to add one there rather than trying to work around it.

Always call `twilio_list_sms_templates` first when you are unsure which mode applies or which templates exist; it returns `trusted_channel`, `free_form_allowed`, and the `templates` list (`name` + `body`).

```
# What can I send?
tool_call(tool_name="twilio_list_sms_templates", arguments={})

# Trusted channel: free-form text
tool_call(tool_name="twilio_send_self_sms", arguments={"body": "Build finished: 2 tests failed in quest5."})

# Any mode: a pre-written message by name
tool_call(tool_name="twilio_send_self_sms", arguments={"template": "Deploy done"})
```

Pass exactly one of `body` or `template`. An `untrusted_channel` error means the admin has not marked SMS as trusted -- pick a template from `available_templates` (or ask the user to create one); do not retry with a different `body`.

### Texting someone else (`twilio_send_sms`, requires approval)

To text any other number, create an action request via the `create_action_request` tool. Never try to text a third party through `twilio_send_self_sms` -- it cannot address anyone but the user. `create_action_request` is top-level only -- if you are running as a sub-agent, do not call it; return the proposed `request_type` and `params` to the parent via `agent_task_response` instead.

Action request type: `twilio_send_sms`

Parameters:
- `to` (required): The recipient's phone number in E.164 format with country code, e.g. `+15551234567`. Numbers without a country code are rejected -- ask the user rather than guessing one.
- `body` (required): The plain-text message (max 1600 characters; longer texts are rejected, not split).
- `recipient_name` (optional): A display name for the approval card (e.g. "Dana (dentist)") when the user referred to the person by name.

The user sees the recipient and the exact text on the approval card and can approve, revise, or deny it. Sending is done from the admin-configured Twilio number, not from the user's phone, so replies go to that Twilio number -- mention this if the user expects a reply.

**Important Notes:**
- SMS is plain text: no Markdown, no links formatting. Keep messages short; carriers segment long texts.
- Twilio's own delivery failures (unreachable number, blocked region, trial-account restrictions) come back as `sms_send_failed` with Twilio's message; report them to the user rather than retrying blindly.
- The user's phone number is never returned to you in full (only the last 4 digits) and you do not need it.
