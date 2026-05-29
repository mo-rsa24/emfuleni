# Claude Tooling — Primeserve MVP

A reference for every Claude Code item configured in this project. Use this when
you forget which subagent to invoke, what a skill does, or how a layer is meant
to debug. Lives in `.claude/TOOLING.md` so Claude can read it on demand.

## How the layers fit together

A normal task flows through this chain. Each layer has a job; if something goes
wrong, the layer's debug section tells you where to look.

```
   you edit code
        │
        ▼
   PostToolUse hook  ──── format, lint, manage.py check
        │
        ▼
   main Claude agent  ──── CLAUDE.md always loaded
        │                  skills load on match
        │                  MCPs callable as tools
        │                  plugins extend tools
        ▼
   subagents (on demand)  ──── code-reviewer, test-writer
        │                       isolated context
        │                       summary returned
        ▼
   you commit
```

When something misbehaves, work outward from the cheapest layer first: CLAUDE.md,
then skill descriptions, then hook output, then subagent briefing, then MCP
status. Each is a file in `.claude/`; `git stash` is the bisect tool.

---

## 1. Always-on context

### `CLAUDE.md`

- **Type**: project context file.
- **Location**: project root, `/home/molef/Work/primeserve/CLAUDE.md`.
- **Scope**: project-local. Loads only when VS Code is opened on this folder.
- **Owns**: project-wide facts and hard rules that must be true in every session.

**When it's used**

Every Claude session in this project. You never need to invoke it.

**How to use it**

You don't — it loads automatically. To verify it's working, start a session and
ask: `What architecture has this project already decided on?` Claude should
answer with modular monolith, Postgres, RQ, HTMX, the source-of-truth rule, and
the `services.py` rule, without you pasting anything.

To change it: open in VS Code, edit, commit. Sharpen rules that get violated;
shorten sections that get ignored. Keep under ~200 lines.

**How to debug**

If Claude ignores a rule, the rule is either buried or too soft. Move it higher
in the file or rewrite it more directly. If `CLAUDE.md` grows past ~200 lines,
move the detail into a skill and leave a one-line reference.

---

## 2. Hooks (deterministic enforcement)

### `post_edit_check.sh`

- **Type**: `PostToolUse` shell hook.
- **Location**: `.claude/hooks/post_edit_check.sh`.
- **Registered in**: `.claude/settings.json`.
- **Owns**: mechanical checks on every Python file edit.

**What it does**

After Claude edits any file, the hook fires:

1. If the file is `.py`, run `ruff format`.
2. Run `ruff check` for lint issues.
3. Run `python manage.py check` if `manage.py` exists.

Output is fed back into Claude's context as a message Claude reads in the same
turn. Non-Python edits exit silently. Missing tools are skipped, not failed.

**When it's used**

Automatically, every `Edit`, `Write`, `MultiEdit`, `str_replace`, or
`create_file` event. You never invoke it.

**Example output**

```
[post-edit] checks on: ledger/services.py
[post-edit] ruff format: would reformat — applying
[post-edit] ruff check: F401 'os' imported but unused
[post-edit] django check failed: ...
```

**How to debug**

Symptom: hook never seems to run. Test it in isolation:

```bash
echo '{"tool_input":{"file_path":"/tmp/test.py"}}' \
  | .claude/hooks/post_edit_check.sh
```

If it works in the terminal but not as a hook, it's a path or working-directory
problem in `.claude/settings.json`. If line endings are CRLF (downloaded from
Windows), fix with `sed -i 's/\r$//' .claude/hooks/post_edit_check.sh`.

The hook exits 0 even on check failures — Claude reads the output and decides.
To make it hard-block, change the final `exit 0` to `exit $fail`.

---

## 3. Skills

Markdown files in `.claude/skills/<name>/SKILL.md`. Two types:

- **Reference skills** auto-load when Claude matches the task. No side effects.
- **Action skills** generate files. Manual-invoke only (`disable-model-invocation: true`).

### `reconciliation-contract` (reference)

- **Owns**: the shape and rules of the §5 reconciliation record.
- **Loads when**: code touches reconciliation results, findings, ledger
  entries, dispute files, payment instructions, or correction messages.

**Key idea**: the reconciliation record is a *projection*, not a table. Persist
the parts as relational rows; compute the JSON object on read. One exception:
store an immutable snapshot at the moment of payment, for audit.

**Example use**

```
> I'm adding a new finding type for prescription-window violations.
> Where does it live and what does it look like?

Claude (with skill loaded): A new finding type lives in engine —
specifically as a new value in the Finding.type enum, owned by engine's
models. It writes Finding rows; the reconciliation contract picks them up
on read via engine.services.build_reconciliation(account, period). Don't
add a new model — findings are already part of the projection.
```

### `data-model` (reference)

- **Owns**: the Django model layout. Three groups (Upstream/Owned/Derived),
  the tenancy rule, the `TenantTimestamped` abstract base, migrations rules.
- **Loads when**: code touches `models.py`, migrations, querysets, the ORM,
  or database schema.

**Example use**

```
> Write a new model for storing meter photos uploaded by ratepayers.

Claude (with skill loaded): That's an Evidence row, owned by the portal app
(Derived group). It inherits from TenantTimestamped, so it gets
municipality_id, created_at, updated_at, and the TenantManager
automatically. Queries must use Evidence.objects.for_tenant(municipality)
rather than .filter() directly.
```

### `new-django-app` (action)

- **Owns**: scaffolding a new Django app to the project's conventional layout.
- **Loads when**: never automatically. You invoke `/new-django-app <name>`.
- **`disable-model-invocation: true`** — generative skills should not auto-fire.

**What it creates** (per app)

```
<app>/
  __init__.py
  apps.py
  models.py         # with commented-out TenantTimestamped import
  services.py       # docstring + cross-app API rule reminder
  tasks.py          # RQ task module
  migrations/__init__.py
  tests/__init__.py
  tests/test_services.py   # passing placeholder
```

**Example use**

```
> /new-django-app ledger

Claude: [verifies manage.py exists, confirms ledger/ doesn't exist,
creates all seven files, reminds you to add "ledger" to INSTALLED_APPS]
```

Nested apps work the same way: `/new-django-app channels/whatsapp` creates
`channels/whatsapp/` plus `channels/__init__.py` if missing.

**How to debug skills generally**

Symptom: wrong skill loaded, or no skill loaded. Failure is almost always in
the `description` field. Two skills with overlapping descriptions confuse the
match; vague descriptions don't trigger at all. Fix: sharpen the description,
name the trigger conditions ("Load this whenever code touches X, Y, Z").

For skills with side effects, double-check `disable-model-invocation: true` is
in the front matter — without it, Claude may fire generative skills on its own.

---

## 4. Subagents (context isolation)

Markdown files in `.claude/agents/<name>.md`. Each one is a separate Claude
session with its own context window. They don't see your conversation; they
return only a summary.

### `code-reviewer`

- **Tools**: `Read, Grep, Glob, Bash` — read-only by tool restriction.
- **Owns**: checking a diff against the project's hard rules.
- **Returns**: a structured verdict (Blocking / Concerns / Clean).

**What it checks**

1. The `services.py` boundary — no cross-app model imports.
2. Tenancy — every domain model has `municipality_id`; every query uses the
   tenant-scoped manager.
3. Source of truth — no writes to upstream models outside `ingest`; no
   reconciliation record persisted as a single row.
4. Migrations — no edits to already-run migrations.
5. Hard "never do" rules from `CLAUDE.md`.
6. The diff itself — print statements, leftover TODOs, unrelated changes.

**Example use**

```
> Use the code-reviewer subagent to review my staged changes.

[Subagent spawns, reads CLAUDE.md, both skills, the diff, returns:]

## Review verdict

### Blocking (fix before commit)
- ledger/services.py:42 — calls portal.models.Evidence.objects.filter()
  directly; should go through portal.services.get_evidence().
- engine/models.py:18 — new model lacks municipality_id; will leak across
  tenants.

### Concerns (worth a second look)
- portal/views.py:67 — print() statement left in.

### Clean
- migrations, source-of-truth, never-do rules.
```

### `test-writer`

- **Tools**: `Read, Write, Edit, Grep, Glob, Bash` — can create test files.
- **Owns**: writing tests against an app's `services.py`.
- **Returns**: a summary of what it wrote, results, and anything flagged for you.

**What it does**

For each public function in the target `services.py`:

1. Happy-path test with realistic inputs.
2. Tenancy-boundary test — confirms no cross-tenant leakage.
3. Edge cases — empty input, missing optional fields, stale references.
4. Declared error paths — tests that the right exception fires.

What it does NOT test: internal helpers, model field validation, framework
code (HTMX rendering, URL routing, RQ mechanics).

**Example use**

```
> Use the test-writer subagent on ledger.services.record_payment.

[Subagent reads ledger/services.py, ledger/models.py, existing tests,
writes ledger/tests/test_services.py, runs python manage.py test ledger,
returns:]

## Tests written
Target: ledger.services.record_payment
File: ledger/tests/test_services.py

### Added
- test_records_payment_happy_path
- test_returns_only_current_tenant_rows
- test_raises_on_negative_amount
- test_handles_missing_account

### Results
- 4 tests, 4 passed.
```

**How to debug subagents**

Symptom: subagent ignored a decision you made earlier in the conversation. It
doesn't inherit your context. Fix: pass the context explicitly in the spawning
prompt, or add it to the agent's `.md` body so it loads every time.

If a subagent's verdict feels wrong (over- or under-zealous), the rules in
its body are tuned wrong — edit `.claude/agents/<name>.md`.

---

## 5. MCP servers (external connections)

Project-scoped in `.mcp.json`. Tool names load at session start; full schemas
load on call. Run `/mcp` inside a session to check connection status.

### `playwright`

- **Source**: `@playwright/mcp` via `npx`.
- **Owns**: browser automation. Loading pages, clicking, filling forms,
  uploading files, reading rendered content.
- **Use it for**: testing the HTMX portal as Claude builds it. Visual feedback.

**Example use**

```
> Using the Playwright MCP, open http://localhost:8000/challenge,
> upload /tmp/test_meter.jpg, click "Submit challenge", and tell me
> what the page renders.

[Claude calls Playwright tools, returns rendered text and any flash
messages, reports HTTP status and form errors.]
```

### `postgres`

- **Source**: `@modelcontextprotocol/server-postgres` via `npx`.
- **Connection**: `postgresql://primeserve:primeserve_dev_only@localhost:5432/primeserve_dev`.
- **Owns**: introspection of the live development database. Read-only.
- **Use it for**: confirming actual schema vs. what `models.py` says.

**Example use**

```
> Using the Postgres MCP, show me the columns of ledger_ledgerentry and
> compare with ledger/models.py.

[Claude introspects the live table, reads the model, reports drift —
e.g., "the model adds a `void_reason` field that has no migration yet".]
```

**How to debug MCPs**

Symptom: a tool that worked earlier in the session is now unavailable. The MCP
connection dropped. Run `/mcp` to check status. Reconnect by restarting the
session.

Symptom: Postgres MCP returns connection errors. Confirm Postgres is running
(`sudo service postgresql status`); confirm the string matches the role and
database; if `peer` authentication is enforced, edit `pg_hba.conf` to
`scram-sha-256` for `127.0.0.1`.

---

## 6. Plugins (from `claude-plugins-official`)

Installed via `/plugin install <name>@claude-plugins-official` at project scope.

### `pyright-lsp`

- **What it adds**: Python language server — go-to-definition, find-references,
  real-time type errors.
- **Requires**: `pyright` binary installed (`pip install pyright` inside the
  `primeserve` env).
- **Why we have it**: text-grep navigation across an 8-app codebase is slow and
  misses things. LSP gives Claude IDE-quality navigation in milliseconds.

**Example use**

```
> Find all callers of ledger.services.record_payment across the project.

[Claude uses LSP find-references, returns the exact list with file:line.
Faster and more accurate than grep, catches aliased imports.]
```

### `commit-commands`

- **What it adds**: slash commands for the git workflow — typically `/commit`,
  `/push`, sometimes `/pr`.
- **Use it for**: tightening the edit→review→commit loop.

**Example use**

```
> /commit

[Claude reviews the staged diff, suggests a commit message following the
plugin's conventions, runs the commit.]
```

### `hookify`

- **What it adds**: helpers for authoring more hooks. Includes a
  `conversation-analyzer` subagent and `/hookify:hookify`, `/hookify:configure`,
  `/hookify:list` commands.
- **Use it for**: when you want to add a new deterministic check and don't want
  to write the shell script by hand.

**Example use**

```
> /hookify:hookify
> Block edits to .env files.

[Claude generates a PreToolUse hook with the right matcher and command,
adds it to .claude/settings.json.]
```

**How to debug plugins**

Symptom: plugin installed but tools don't appear. Check the `/plugin` UI's
Errors tab — usually "Executable not found in $PATH" (install the binary in
the active environment). Symptom: duplicate capability (e.g. plugin Playwright
+ project MCP Playwright) — pick one, uninstall the other. Project MCP wins.

---

## 7. Built-in Claude Code primitives we rely on

Things that ship with Claude Code — no install needed, but worth knowing.

### Plan mode

A pre-edit thinking mode. Claude proposes a plan; you accept, reject, or
amend before any file is touched.

**When to use**: any non-trivial change. Architecture work, multi-file
refactors, new features. Skip for one-line fixes.

### Built-in slash skills

`/debug`, `/simplify`, `/batch`, `/loop`. From Claude Code's bundled skills.
Especially useful: `/debug` for stepping through an issue, `/batch` for
running independent work in parallel worktrees.

### Explore agent (built-in subagent)

Use this rather than building your own `explorer`. Ask "use the Explore
agent to find where ratepayer_id is referenced" — it does the broad read,
returns a summary.

### Task tool

The mechanism subagents use to spawn. You rarely call it directly —
invoking `code-reviewer` or `test-writer` uses it internally.

---

## 8. Environment-level integrations

Outside `.claude/`, but part of the working setup.

### micromamba env `primeserve`

- Python 3.11, Django, ruff, psycopg2-binary, pyright.
- Pinned in `environment.yml` (micromamba/conda) and `requirements.txt` (pip).
- Activated automatically by the `~/.bashrc` block when you `cd` into the
  project folder.

**Restore from a fresh clone**

```bash
micromamba create -n primeserve -f environment.yml
# or: pip install -r requirements.txt inside any 3.11 env
```

### `~/.bashrc` auto-activate block

The one piece of config outside the repo. Activates `primeserve` env when
you enter `/home/molef/Work/primeserve` (or any subfolder); reverts to `base`
on exit. Backed up before edit; reversible by deleting the block.

### Native WSL2 PostgreSQL

- Role: `primeserve`. Database: `primeserve_dev`. Password: `primeserve_dev_only`.
- Auth: `scram-sha-256` over `127.0.0.1`.
- Start manually each WSL session: `sudo service postgresql start`.
- Will be replaced by a Dockerized Postgres when you add `docker-compose.yml`.

### Redis (native on WSL2 — preferred for dev)

Added in Slice 3 — RQ needs a broker. Two ways to get Redis listening on
`127.0.0.1:6379`, which is where Django's `RQ_QUEUES['default']` points.

**Native (recommended for dev).** WSL2 already has redis-server installed
and autostarting via systemd. Verify:

```bash
redis-cli ping                                # PONG
sudo systemctl status redis-server | head -3  # active (running)
```

That's it — no compose needed. Django/RQ talk to it directly.

**Containerised (the `docker-compose.yml` option).** The compose file
exists primarily as the *deploy reference* for Slice 13. Locally it
conflicts with the native Redis on port 6379. To use it instead:

```bash
sudo systemctl stop redis-server     # free the port
sudo systemctl disable redis-server  # optional — keep it stopped on reboot
docker compose up -d redis
```

Settings:

- `RQ_QUEUES['default']['HOST']` = `localhost`, port `6379`, no auth, no DB index.
- In tests, `ASYNC: False` runs jobs inline so the suite needs no live Redis.

Worker (either backend):

```bash
python manage.py rqworker default     # foreground worker, Ctrl+C to stop
```

### Anthropic API (Slice 6 onward)

VLM extraction (`vlm.services.extract_meter_reading`) calls Claude Sonnet 4.6
vision per design-note Q3. Needs an API key:

```bash
export ANTHROPIC_API_KEY='sk-ant-...'   # in your shell or ~/.bashrc
```

Without the key set, the VLM job marks the extraction as `failed` and writes
the reason to its `raw_response` JSONB — the rest of the pipeline still works.

Tests do NOT need a live key — they monkey-patch `anthropic.Anthropic` with a
stub client. The settings.py default for `ANTHROPIC_API_KEY` is the empty
string, so tests run without env wiring.

### Git

- Repo initialized on `main`. `.gitignore` covers Python, Django, Docker,
  Python virtualenvs, OS files, secrets.
- `.claude/` IS committed; only `.claude/settings.local.json` is excluded.
- `.mcp.json` is committed — anyone cloning gets the same MCP setup.

---

## Cheat sheet

Common moves, one-liners.

| Want to... | Do this |
|---|---|
| Start a Claude session in the project | `claude` |
| List all skills | Ask: "what skills are available?" |
| List all subagents | Ask: "what subagents are available?" |
| Check MCP status | `/mcp` inside a session |
| List installed plugins | `/plugin list` |
| Browse marketplace | `/plugin` |
| Scaffold a new Django app | `/new-django-app <name>` |
| Review staged changes | "Use the code-reviewer on my staged changes." |
| Write tests for a service function | "Use the test-writer on `<app>.services.<fn>`." |
| Force the hook to run as a sanity check | Edit any `.py` file (Claude or yourself). |
| Verify Postgres connection | `psql "$DATABASE_URL" -c "SELECT 1"` |
| Run Django checks | `python manage.py check` |
| Run tests | `python manage.py test` |
| Bisect a misbehaving Claude config | `git stash` `.claude/`, reproduce, restore |

---

## What we deliberately do NOT have

For completeness, so you don't go searching for these:

- **No `security-reviewer` subagent.** Explicitly deferred until post-MVP.
- **No agent teams.** Experimental, not needed at 1–3 developers.
- **No custom plugins.** Not needed until a second repo or external sharing.
- **No `code-review` / `pr-review-toolkit` / `feature-dev` plugins.** They
  would duplicate or conflict with our subagents and skills.
- **No `claude-django` (third-party) plugin.** Its slash commands fight
  `/new-django-app`.
- **No Celery, Kubernetes, GraphQL, microservices.** Decided against in
  Part 1.
