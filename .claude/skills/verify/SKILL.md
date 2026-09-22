---
name: verify
description: Build, launch, and drive a local Quest instance to verify changes end-to-end (API via curl with dev-login cookies, UI via playwright-core screenshots).
---

# Verifying changes against a local Quest instance

## Build & launch

```bash
# Frontend (the server serves frontend/dist, so rebuild after FE changes)
cd frontend && npm run build

# Backend, local mode: port 9000, throwaway seeded DB, no credentials needed.
# Set QUEST_DATA_DIR to control where the run's data dir lands.
QUEST_DATA_DIR=/path/to/scratch/questdata uv run python run.py --local
```

`uv` may not be on PATH in agent shells — `export PATH="$HOME/.local/bin:$PATH"`
first. Full `python3 run.py --local` (no pre-build) also works — it runs
`npm install` + build itself, but that may churn `frontend/package-lock.json`
(npm-version noise; `git checkout` it before committing).

Wait for readiness by polling `GET http://localhost:9000/auth/dev-accounts`
(returns the canned accounts: admin@quest.local, alice@quest.local,
bob@quest.local; alice has seeded memories/guide/skill/project data).

## Drive the API

```bash
curl -s -c cookies.txt -X POST http://localhost:9000/auth/dev-login \
  -H 'Content-Type: application/json' -d '{"email":"alice@quest.local"}'
curl -s -b cookies.txt http://localhost:9000/app/api/guides
```

All REST endpoints live under `/app/api/...` and accept the session cookie.

To seed extra rows the API can't create, run a store function against the
same data dir, e.g.:

```bash
QUEST_DATA_DIR=... uv run python -c "
import asyncio
from db.guide_store import create_guide
asyncio.run(create_guide(2, 'Name', content='...', is_default=False))"
```

(User IDs: admin=1, alice=2, bob=3 in a fresh seed.)

## Drive the UI

`playwright-core` is preinstalled at the user level (`~/.npm-global`, with
the chromium-headless-shell browser cached in `~/.cache/ms-playwright`) —
no per-run npm install. Run scripts from any directory with:

```bash
NODE_PATH=$HOME/.npm-global/lib/node_modules node ui-check.js
```

(`~/.bashrc` also exports NODE_PATH/PATH for `~/.npm-global`, but passing it
inline works even in shells that didn't load it. For ESM `.mjs` scripts
NODE_PATH is ignored — symlink instead:
`mkdir -p node_modules && ln -sfn ~/.npm-global/lib/node_modules/playwright-core node_modules/playwright-core`.)

If `require('playwright-core')` ever fails (fresh machine), bootstrap once:

```bash
npm config set prefix ~/.npm-global
npm install -g playwright-core
~/.npm-global/bin/playwright-core install chromium-headless-shell
```

Then script with `playwright-core`: create a context, POST
`/auth/dev-login` via `ctx.request` (cookie lands in the context), goto
`http://localhost:9000/`, screenshot.

Gotchas:
- With no data connections configured, the **Settings modal auto-opens on
  page load** and its `.settings-overlay` swallows clicks — don't hunt for
  a settings trigger, just use the open `.settings-modal` (nav items:
  `.settings-nav-item`). Pressing Escape closes it.
- Sending chat messages needs LLM credentials. Local mode reads the
  repo-root `server_credentials.json` like any mode — this box has a
  Gemini-only key, so `GET /app/api/config` reports `available_models` as
  the three genapi Gemini models and a Gemini send would hit the real API.
  Anthropic/Vertex models are uncredentialed; verify around live sends.
- The composer model dropdown is `#model-select`; per-user settings can be
  probed via same-origin `fetch('/app/api/settings', {method:'PUT', ...})`
  from `page.evaluate` (the server validates `default_model` — unknown ids
  get 400).
