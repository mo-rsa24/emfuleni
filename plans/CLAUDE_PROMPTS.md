# Claude Prompts & Terminal Commands — Daily Reference

Copy-paste cheat sheet for working on Emfuleni. Pair this with
[BUILD_SEQUENCE.md](BUILD_SEQUENCE.md) (the slice plan) and
[../CLAUDE.md](../CLAUDE.md) (the architecture rules).

---

## Terminal — daily workflow

```bash
# Activate env (or skip if ~/.bashrc auto-activates on cd)
cd /home/molef/Work/primeserve/emfuleni
eval "$(micromamba shell hook --shell bash)" && micromamba activate primeserve

# Fast sanity loop
python manage.py check
python manage.py test ingest                # one app
python manage.py test                       # full suite

# Schema work
python manage.py makemigrations <app>
python manage.py migrate

# Reset one app's tables (drops + recreates)
python manage.py migrate <app> zero && python manage.py migrate <app>

# Reload dev fixtures
python manage.py seed_emfuleni

# Interactive shell
python manage.py shell

# Lint / format (the hook runs these on save anyway)
ruff check .
ruff format .
```

---

## Claude prompts — per slice

Open a Claude session on `/home/molef/Work/primeserve/emfuleni/` (not the
company root) so the project's CLAUDE.md, .mcp.json, and skills all load.

### Start a slice

> Open `plans/BUILD_SEQUENCE.md` and walk me through Slice **N**. Mirror its
> tasks into TodoWrite, then start with the first unchecked item. Stop before
> any commit so I can sanity-check.

### Generate tests after writing a new services function

> Use the test-writer subagent to add tests for the new function(s) in
> `<app>/services.py`. Include a tenancy-boundary test (different
> Municipality, same identifier — verify isolation).

### Review staged changes before every commit

> Use the code-reviewer subagent to review my staged changes against the
> CLAUDE.md hard rules. Stop me if anything blocks the commit.

### Commit (uses the commit-commands plugin)

> /commit

### Open a PR (when the project has a remote)

> /commit-push-pr

---

## Postgres MCP prompts

The Postgres MCP server is wired in [`.mcp.json`](../.mcp.json). It
autoconnects on a fresh `claude` session opened in this directory. If it
drops mid-session, restart the session to reconnect.

### Quick state checks

> Using the Postgres MCP, list every table in the `public` schema with its
> row count. Highlight any `ingest_*`, `ledger_*`, `common_*`, or
> `identity_*` table.

> Using the Postgres MCP, show me the columns of `ingest_municipalaccount`
> and confirm `municipality_id` is indexed.

### Tenancy-leak audit (run after every schema change)

> Using the Postgres MCP, find any domain row where `municipality_id IS NULL`
> across all `ingest_*`, `common_*`, `ledger_*`, `identity_*`,
> `corrections_*`, `payments_*`, `portal_*`, `vlm_*`, `engine_*`,
> `whatsapp_*`, and `ussd_*` tables. There should be zero. Report any hits.

### Cross-tenant collision check

> Using the Postgres MCP, group `ingest_municipalaccount.account_number`
> by `municipality_id` and find any `account_number` value that exists under
> more than one tenant. That's expected (different municipalities can reuse
> numbers); confirm the unique constraint
> `ingest_municipalaccount_unique_account_per_tenant` still exists.

### Drift detection

> Using the Postgres MCP, show me the columns of `<table>`. Compare against
> `<app>/models.py`. Flag any drift (column missing, type changed, index
> dropped). This catches manual schema edits and skipped `makemigrations`.

### After running `seed_emfuleni`

> Using the Postgres MCP, count rows per `ingest_*` table for the Emfuleni
> tenant. Expected: 1 extract, 3 accounts, 6 bills, 6 ledger entries, 18
> meter readings.

---

## Skill-loading prompts

Skills auto-trigger by description, but you can force-load one when you
want it in context up-front:

> Load the `data-model` skill before we touch any `models.py`.

> Load the `reconciliation-contract` skill — we're about to write
> engine/services.

> Use `/new-django-app <name>` to scaffold a new app. (Explicit-only;
> this skill is `disable-model-invocation: true`.)

---

## DBeaver connection (for the human)

| Field | Value |
|---|---|
| Driver | PostgreSQL |
| Host | `localhost` |
| Port | `5432` |
| Database | `primeserve_dev` |
| Username | `primeserve` |
| Password | `primeserve_dev_only` |

Useful sanity SQL after a seed:

```sql
SELECT a.account_number, a.holder_name, m.name AS tenant
FROM ingest_municipalaccount a
JOIN common_municipality m ON m.id = a.municipality_id
ORDER BY a.account_number;

SELECT b.period, b.opening_balance, b.closing_balance, a.account_number
FROM ingest_municipalbill b
JOIN ingest_municipalaccount a ON a.id = b.municipal_account_id
ORDER BY a.account_number, b.period;
```

---

## End-of-slice ritual

Every slice closes the same way — these four prompts in order:

1. > Run `python manage.py test` and confirm the whole suite is green, not
>    just the new app.

2. > Use the code-reviewer subagent to review my staged changes against the
>    CLAUDE.md hard rules.

3. > /commit

4. > Tick the slice off in `plans/BUILD_SEQUENCE.md` and re-read the
>    design-note section it implemented. Tell me if the implementation
>    drifted from the design.

---

## When you're stuck

> Read `plans/BUILD_SEQUENCE.md` and the last 5 commits. Tell me where I am
> in the build and what's the next concrete action — one specific task, not
> a menu.
