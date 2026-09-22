# Quest Inference API — Integration Guide

This guide is for applications and agents that integrate with Quest's one-shot inference
endpoint. You send a single prompt; it runs inside Quest as a specific user — with that user's
connected services (Gmail, Drive, Slack, GitHub, ...), skill library, and sandboxed code
execution — and you get back one final markdown answer. No streaming, no session state, no
follow-up turns.

Use it when your application needs Quest's data reach but renders the result on its own surface
(a dashboard, a report generator, another agent's tool call, ...).

## Getting a key

1. The Quest user whose data access you need opens **Settings > Inference API** in the Quest web
   app.
2. They click **Generate Key** with a name identifying your application (e.g.
   `deal-dashboard`).
3. The raw token (`qst_...`) is shown **exactly once** — copy it immediately. Afterwards only
   the name and the last 4 characters are visible.

Treat the token like a password: it grants read access to everything the owning user has
connected. Store it in a secrets manager, never in code or logs. To rotate, generate a new key
and delete the old one (deletion revokes it immediately). Each user can hold up to 20 keys.

Only these tokens work on the inference endpoint — Quest session cookies and other Quest API
keys are rejected, and inference tokens do not work anywhere else in Quest.

## Endpoint

```
POST https://<your-quest-host>/api/inference
Authorization: Bearer qst_...
Content-Type: application/json
```

### Request body

| Field    | Type   | Required | Notes                                                                 |
|----------|--------|----------|-----------------------------------------------------------------------|
| `prompt` | string | yes      | The complete task, max 100,000 characters. See "Writing prompts".     |
| `model`  | string | no       | A Quest model id. Omit unless you need a specific model — the default is the user's own default model, falling back to the server default. Unknown ids are rejected with `invalid_model`. |

### Success response (200)

```json
{
  "response": "# Findings\n\n...",
  "conversation_id": "3eddf149-dc69-479c-bfee-c103b0851740",
  "model": "gemini-3.5-flash-lite"
}
```

- `response` — the agent's final answer as GitHub-flavored markdown, verbatim. This is the only
  output channel; intermediate reasoning and tool activity are never included.
- `conversation_id` — the id of the run's transcript. Every call creates a read-only
  conversation in the owning user's Quest account (hidden behind the sidebar's "Show Inference
  API Runs" filter), so runs are auditable after the fact. Include this id when reporting
  problems.
- `model` — the model that actually served the run (`null` only if no model could be resolved,
  which does not happen on a configured server).

### Errors

Errors use FastAPI's envelope: `{"detail": {"error": "<code>", "message": "<human text>"}}`.
Dispatch on `detail.error`, not the message text.

| HTTP | `error`             | Meaning / what to do                                                        |
|------|---------------------|------------------------------------------------------------------------------|
| 400  | `invalid_prompt`    | Missing/empty prompt, or over 100,000 characters. Fix the request.           |
| 400  | `invalid_model`     | Unknown `model` id. Omit the field or use a valid id.                        |
| 401  | `invalid_api_key`   | Missing/malformed `Authorization` header, unknown token, or the key was revoked. Do not retry with the same token. |
| 403  | `access_denied`     | The key's owner is outside the server's allowed login domain. Not retryable. |
| 502  | `no_response`       | The model finished without delivering a final answer (rare; the server nudges once before giving up). `detail.conversation_id` points at the transcript. Retrying is safe. |
| 502  | `inference_failed`  | The run raised an unexpected error (upstream model failure, etc.). Retrying is reasonable; use backoff. |
| 504  | `inference_timeout` | The run exceeded the 15-minute server cap and was cancelled. Simplify or split the task before retrying. |

Retries are safe in the sense that every call is independent (a fresh conversation each time),
but each retry costs a full model run — use bounded retries with backoff, and never retry 4xx.

### Timeouts and latency

The server caps a run at **15 minutes** wall-clock; set your HTTP client timeout slightly above
that (e.g. 16 minutes). Actual latency scales with the task: a trivial prompt returns in a few
seconds, while a task that reads mail, queries external APIs, or runs code can take minutes.
The connection blocks for the duration — there is no polling API, so plan your calling context
accordingly (background job, task queue, generous serverless timeout).

There is currently no server-side rate limiting or concurrency cap on this endpoint. Be a good
citizen: run calls sequentially or with low concurrency, since every call is a full agent run
billed against the owning account's model usage.

## What the agent can and cannot do

Inside a run, the Quest agent has:

- **Read access** to the owning user's connected services (whatever they have linked: Gmail,
  Google Drive/Docs/Sheets/Calendar, Slack, GitHub, Ramp, ...), plus public data sources.
- The user's **skill library** (their auto-loaded skills are active, and it can load others on
  demand).
- A **sandboxed workspace** with file tools and Python/script execution.

It cannot:

- **Write to external services.** Sending messages, creating calendar invites, editing
  spreadsheets, Drive uploads, and memory/skill saves all require an interactive
  approval card, which does not exist in a headless run. The approval-free write tools a normal
  chat has (Gmail drafts, send-to-self emails, archiving and labelling, Slack self-DMs, self-SMS,
  Outlook drafts) are switched off in inference runs as well, including from sandboxed scripts,
  so a run cannot change anything outside its own scratch workspace. If your task implies a
  write, the agent will instead describe what the user should do — design your integration
  around read/analyze/compose tasks.
- **Ask you anything.** There are no follow-up turns. Ambiguity gets resolved by the agent's
  best judgment, stated inside the response.
- **Return files.** The only output is the markdown string. If the task produces tabular or
  structured data, ask for it inline (markdown tables, or a fenced code block — see below).

## Writing prompts

The prompt is the entire interface, so make it self-contained:

- **State the task, the scope, and the output format.** Example: "Summarize all emails from the
  last 7 days that mention 'Series B'. Output a markdown table with columns Date, From, Subject,
  One-line summary. If there are none, output exactly 'No matching emails.'"
- **Pin down time ranges and entities explicitly** ("between 2026-08-01 and 2026-08-15 UTC",
  "the GitHub repo acme/widgets"). The agent knows the current date but not your application's
  context.
- **Say what to do when data is missing** ("if the spreadsheet has no 'Q3' tab, say so and
  stop"). Otherwise the agent improvises and explains its improvisation in the response.
- **For machine-parseable output**, ask for a single fenced code block and parse that. Example:
  "Respond with ONLY a fenced ```json code block containing an array of {date, from, subject,
  summary} objects." The response is markdown by contract, so extract the fence rather than
  JSON-parsing the whole string. Expect occasional drift and validate.
- **Don't ask for progress updates or streaming** — intermediate output is discarded by design.

## Examples

curl:

```bash
curl -s https://<your-quest-host>/api/inference \
  -H "Authorization: Bearer $QUEST_INFERENCE_KEY" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "List the titles of my 5 most recent Google Docs as a markdown list."}'
```

Python:

```python
import os, httpx

resp = httpx.post(
    "https://<your-quest-host>/api/inference",
    headers={"Authorization": f"Bearer {os.environ['QUEST_INFERENCE_KEY']}"},
    json={"prompt": "List the titles of my 5 most recent Google Docs as a markdown list."},
    timeout=960,  # above the 15-minute server cap
)
if resp.status_code == 200:
    print(resp.json()["response"])
else:
    detail = resp.json().get("detail", {})
    raise RuntimeError(f"{resp.status_code} {detail.get('error')}: {detail.get('message')}")
```

TypeScript:

```ts
const resp = await fetch("https://<your-quest-host>/api/inference", {
  method: "POST",
  headers: {
    Authorization: `Bearer ${process.env.QUEST_INFERENCE_KEY}`,
    "Content-Type": "application/json",
  },
  body: JSON.stringify({ prompt: "..." }),
  signal: AbortSignal.timeout(960_000),
});
const body = await resp.json();
if (!resp.ok) throw new Error(`${body.detail?.error}: ${body.detail?.message}`);
console.log(body.response);
```

## Exposing Quest as a tool to another agent

A common integration is wrapping this endpoint as a tool in another agent framework. A minimal
tool definition:

- **Name:** `quest_inference`
- **Description:** "Run a one-shot research/analysis task against the user's Quest account
  (email, Drive, Slack, GitHub, ...). Input is a complete, self-contained task description;
  output is a markdown answer. Read-only: cannot send messages or modify anything. May take
  minutes."
- **Input schema:** `{ "prompt": string }` (keep `model` out of the schema; let your wrapper pin
  it if needed).
- **Wrapper behavior:** call the endpoint, return `response` on 200; on error, return the
  `detail.error` code and message as the tool result so the calling agent can adapt instead of
  crashing.

Because each call is expensive (a full agent run), instruct the calling agent to batch related
questions into one prompt rather than issuing many small calls.

## Implementation reference

For Quest developers: the server-side implementation and architecture notes live in
[docs/api/inference-api.md](docs/api/inference-api.md) (endpoint + key store + run mechanics).
