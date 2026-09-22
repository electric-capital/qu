Reset the repository to a clean state so a new task can start fresh. Follow the steps in order.

Keep output minimal: work through the steps quietly and end with a single short confirmation
line. Only say more when something went wrong or is unexpected (real work at risk of being
discarded, a dismissed PR, a process that won't die) -- those need the user's attention.

## 1. Safeguard work in progress

Run `git status` and `git log origin/main..HEAD --oneline`. If there are uncommitted changes or
unpushed commits that look like real work (not build/run artifacts), stop and ask the user before
discarding anything.

## 2. Check upstream PR status

For each local feature branch, check its PR with `gh pr list --state all --head <branch>`:

- **Merged** -- safe to delete the local branch in step 4.
- **Closed without merging (dismissed)** -- flag it to the user and keep the branch; the work was
  rejected upstream and deleting it silently would lose the only copy of that context.
- **Open** -- keep the branch; mention it in the final confirmation.
- **No PR** -- treat the branch as unpushed work (step 1 rules apply).

## 3. Kill background processes you started

- Stop any background tasks started during the session: the local server (`run.py` / `uvicorn`),
  npm dev servers, log tails, monitors. Prefer stopping them by the PID you got when you started
  them.
- Double-check for a leftover local server by this checkout's port (from `dev_config.json`, see
  the port convention in CLAUDE.md) and terminate whatever holds it:

  ```bash
  PORT=$(python3 -c 'import json; print(json.load(open("dev_config.json")).get("dev_port", 9000))')
  ss -ltnp "sport = :$PORT"          # show what is listening, if anything
  fuser -k -TERM "$PORT/tcp"         # SIGTERM; add -KILL only if it does not exit
  ```

- **Never kill by a broad command-line pattern.** All checkouts live under `/home/<user>/questN`,
  and the terminal multiplexer, pty host, and ssh session that host every Claude Code session on
  this machine carry that path (or the word `quest`) in their argv. A `pkill -f quest`,
  `pkill -f codyquest`, or `ps | grep quest | xargs kill` kills every session on the box at once.
  If you must match by name, match the exact command (`pgrep -af "run\.py --local"`), print the
  matches first, and confirm each PID's cwd is this checkout (`readlink /proc/<pid>/cwd`) before
  signalling it. Servers belonging to other checkouts are not yours to stop.

## 4. Get back onto main

```bash
git checkout main
git fetch origin
git pull --ff-only origin main
```

Delete local feature branches whose PRs are merged (step 2), plus any others already merged into
`origin/main` (`git branch --merged origin/main`, excluding `main` itself). Leave open, dismissed,
and unmerged branches alone.

## 5. Reset repo state

- Discard tracked modifications: `git checkout -- .`
  (a common artifact is `frontend/package-lock.json` rewritten by a local `npm install`)
- Remove untracked non-ignored leftovers: `git clean -fd`
- **Never use `git clean -x` or `-X`.** Gitignored files must survive cleanup -- they hold local
  config and state that future development cycles need: `server_config.json`,
  `server_credentials.json`, `twitter_credentials.json`, `dev_config.json`, `data/` (including
  `data/local-runs/`), `devplans/`, `frontend/node_modules/`, `.venv/`.

## 6. Verify

`git status` should show `On branch main`, up to date with `origin/main`, and a clean working
tree, with no session-started processes still running. Then confirm in one line, e.g.:
"Repo reset: on main at <short-sha>, clean tree, no background processes." Append anything the
user must know (open PRs/branches kept, dismissed PRs flagged).
