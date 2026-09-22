# Documentation Style Guide

This guide defines how documentation should be written in the `./docs` folder. These docs are written **for AI agents, not humans**. An agent can read any source file directly, so documentation should point to code rather than explain or reproduce it.

## Purpose

Documentation in this project serves as a **navigation index for agents**. Agents read code; docs tell them *which* files to read and *why* the system is designed a certain way. The goal is:

1. **Locate source files** - Know which files to read for a given feature
2. **Understand data flow** - Trace requests through frontend → backend → external services
3. **Learn constraints** - Environment requirements, auth flows, deployment specifics
4. **Understand design decisions** - Why things are built a certain way

## Core Principles

### 1. Reference Files, Don't Duplicate Code

**Do this:**
```
The WebSocket streaming logic is in `frontend/src/services/PersistentWebSocket.ts`.
Message types are defined in `frontend/src/api/types.ts` (see `StreamMessage` interface).
```

**Don't do this:**
```typescript
// Don't paste the actual code
export interface StreamMessage {
  type: 'text' | 'tool_use' | 'tool_result';
  content?: string;
  // ...
}
```

### 2. Document Flows with File References

Show request/data flows as sequences of file references:

```
## Message Send Flow

1. User submits message → `frontend/src/components/ChatPanel.tsx` (handleSendMessage)
2. WebSocket frame sent → `frontend/src/services/PersistentWebSocket.ts` (send)
3. Backend receives → `chat/realtime/socket.py` (_handle_send_message)
4. Model invoked → `chat/gemini_api/conversation.py` (run_conversation_turn)
5. Response streamed back through same path
```

### 3. State Where Things Are Configured

Instead of showing config examples, point to the source:

```
## Configuration

- Default chat model: `server_config.json` (key: `gemini.model`)
- API endpoints: `frontend/src/api/config.ts`
- Auth domain restriction: `chat/auth.py` (ALLOWED_DOMAIN constant)
```

### 4. Document Environment Constraints

Be explicit about runtime requirements:

```
## Environment Requirements

- Docker must be running (Gemini CLI runs in containers)
- Network: Docker uses `--network=host` to reach localhost:8000
- Ports: Backend 8000 (serves both API and frontend static files)
- Storage: SQLite database (`data/quest.db`) for user data, JSON files for chat history
```

### 5. Explain Design Decisions Briefly

Capture the "why" to prevent agents from re-architecting:

```
## Design Decisions

**Why SQLite for user data instead of PostgreSQL?**
No separate server required, single-file deployment, Alembic handles migrations.

**Why Docker for Gemini?**
Workspace isolation per conversation, reproducible environment, API key isolation.

**Why WebSocket for messages?**
Real-time streaming from Gemini CLI, better UX than polling.
```

## Document Structure

### Required Sections

Each doc should have:

1. **Overview** - One paragraph describing what this doc covers
2. **Key Files** - List of relevant source files with brief descriptions
3. **Flows** (if applicable) - Step-by-step with file references
4. **Constraints** (if applicable) - Environment/runtime requirements
5. **Design Decisions** (if applicable) - Brief rationale for major choices

### File Naming

- Use kebab-case: `api-client-usage.md`, `chat-api.md`
- Group by category in folders: `architecture/`, `setup/`, `api/`

### Cross-References

Link to other docs and source files:

```
See [Architecture Overview](architecture/overview.md) for system diagram.
Authentication is handled in `chat/auth.py` (see `get_current_user` dependency).
```

## What NOT to Include

- **Inline code blocks** - Reference the file instead
- **Full API response examples** - Just describe the shape and reference types
- **"Workflow Recommendations" or tutorial sections** — Don't write numbered-step instructions showing how to call an API or perform a task. Agents read code and `get_instructions()` directly. Sections titled "Workflow Recommendations" are banned
- **Generated content** - No auto-generated API docs
- **Changelogs** - Use git history
- **Phase labels or chronological organization** - Organize by topic; use git history for timeline
- **Module dependency lists** - Import dependencies are apparent from the code itself
- **Function/endpoint inventories or parameter listings** — Don't list out functions, endpoints, query parameters, request body fields, or response fields. These are in the code (Pydantic models, FastAPI signatures, function docstrings). Just reference the file and function name: "See `chat/routes/telegram.py` (`list_dialogs`)"
- **Human-oriented instructional content** — No curl examples, no "To create X, do Y" instructions, no step-by-step procedures aimed at a human reader. The audience is an agent that reads and executes code directly
- **"Related Documentation" sections** - Don't add sections dedicated solely to linking other docs. The top-level `docs/index.md` (auto-included in `CLAUDE.md`) already serves as a cross-reference index

## Organization Principles

### Organize by Topic, Not by Timeline

Documentation must be organized by **topic** (what a feature is, how it works) rather than **chronological development order** (when it was built).

**Don't do this** (phase/timeline organization):
```
## Phase 1: Backend Foundation
- Chat message storage
- User data stored in SQLite

## Phase 2: API Endpoints
- REST endpoints for conversation management
- WebSocket endpoint for streaming

## Phase 3: Frontend
- Vite + React + TypeScript configuration
```

**Do this** (topic-based organization):
```
## Chat Storage
- Chat message storage with JSON-based persistence
- User data stored in SQLite via SQLAlchemy ORM

## API Layer
- REST endpoints for conversation management
- WebSocket endpoint for real-time chat streaming

## Frontend
- Vite + React + TypeScript configuration
- Build pipeline for static asset generation
```

Specific rules:
- **Never use "Phase N.N" labels** as section headers or organizational markers
- **Never organize content chronologically** by development order -- group by what the feature *is*
- **Remove parenthetical phase references** like "(Phase 4.1)" from file/component descriptions. These are development history, not documentation
- **Remove phase labels from link descriptions** -- e.g., write "Route Dispatch Architecture" not "Route Dispatch Architecture (Phase 1)"
- **Use git history for changelogs** -- development chronology belongs in version control, not in reference docs

## Example: Good vs Bad

### Bad (too much code)

```markdown
## Creating a Conversation

To create a conversation, call the API:

\`\`\`bash
curl -X POST -H "Authorization: Bearer KEY" http://localhost:8000/app/api/conversations
\`\`\`

Response:
\`\`\`json
{
  "id": "uuid-here",
  "created_at": "2026-01-30T12:00:00"
}
\`\`\`
```

### Good (reference-based)

```markdown
## Creating a Conversation

**Endpoint:** POST `/app/api/conversations` (see `chat/routes/conversations.py`)

**Auth:** Bearer token required (validated in `chat/auth.py`)

**Response type:** `ConversationCreateResponse` in `frontend/src/api/types.ts`

**Side effects:**
- Creates directory at `data/chats/{id}/`
- Creates a row in the `conversations` table (see `db/conversation_store.py`)
```

### Bad (parameter listing + workflow steps)

```markdown
### Event List Parameters

- `maxResults` - Maximum number of events per page (default: 250)
- `timeMin` - Lower bound for event start time (RFC3339)
- `timeMax` - Upper bound for event end time (RFC3339)
- `q` - Free text search terms

## Workflow Recommendations

**Searching for events:**
1. Call `authed_get` with `timeMin` and `timeMax` parameters
2. If `nextPageToken` in response, add `pageToken` to next request
```

### Good (pointer to code)

```markdown
## Calendar Read Access

Calendar reads use `authed_get` with the Google Calendar API v3. Allowed paths and parameters are defined in the `_SERVICE_REGISTRY` entry in `chat/gemini_api/authed_get.py`. Usage instructions provided to the LLM are in `chat/gemini_api/services/calendar.py` (`get_instructions()`).
```

## Docs Index (`docs/index.md`)

The file `docs/index.md` is a quick-reference index containing a <150 character summary for every doc file in `docs/` **and every plugin doc under `plugins/<dir>/docs/`** (listed in the index's "Plugins" section). It is auto-included into `CLAUDE.md` via `@docs/index.md`, so its contents are always available in agent context. When adding, removing, or renaming a doc file, **always update `docs/index.md`** to keep it in sync. Each entry is a table row with a relative link to the file and a short summary of what it covers.

## Plugin Docs Live in the Plugin Directory

Documentation specific to a single plugin (its API reference, upstream app setup guide, etc.) lives inside that plugin's directory under `plugins/<dir>/docs/`, not under `docs/` -- the plugin directory is self-contained (code, `instructions.md`, and docs move together). Cross-cutting docs stay in `docs/` (e.g. `docs/architecture/plugins.md` for the plugin loader itself, and setup guides for non-plugin core services like Slack). Plugin docs follow all the same style rules and are indexed in `docs/index.md`'s "Plugins" section.

## Updating Documentation

When making code changes:

1. Update docs if file paths change
2. Update docs if flows change (new steps, removed steps)
3. Update docs if constraints change (new env vars, new dependencies)
4. Update `docs/index.md` if any doc file is added, removed, or renamed
5. Don't update docs for implementation details that don't affect the above
