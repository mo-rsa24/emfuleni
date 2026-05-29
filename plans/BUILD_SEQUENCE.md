# Build Sequence — Emfuleni MVP

**Status:** living document. Update at the start of each slice and on completion.
**Audience:** Primeserve internal team + Claude Code sessions.
**Relationship to MVP_DESIGN_NOTE.md:** the design note is *what* and *why*; this
file is *what next* and *in what order*. When the two disagree, the design note
wins and this file is updated.

## How to use this file

- One slice at a time. Each slice is a vertical cut through the architecture that
  produces something testable end-to-end (even if the next slice is the one that
  makes it user-visible).
- Start a slice by mirroring its task list into `TodoWrite` for the session.
- Finish a slice by ticking it off below, committing, and re-reading the design
  note section it implemented before moving on.
- If a slice surfaces an open question (§6 in the design note), pause and
  resolve it in the design note first. Do not build past an unanswered Q.

## Claude primitives — what we use when

| Primitive | Name | When |
|---|---|---|
| Hook | `post_edit_check.sh` | Auto: ruff format + lint + `manage.py check` on every Python save |
| Skill | `data-model` | Auto-triggered when touching `models.py`, migrations, ORM, or schema |
| Skill | `reconciliation-contract` | Auto-triggered when touching the engine output, the projection, or `PaymentSnapshot` |
| Skill | `new-django-app` | Explicit: `/new-django-app <name>` only when adding a new app |
| Subagent | `code-reviewer` | Before every commit: stage changes, then "review my staged changes" |
| Subagent | `test-writer` | After every new `services.py` function: "write tests for <module>" |
| Plugin | `pyright-lsp` | Background type-checking — always on |
| Plugin | `commit-commands` | `/commit` per slice (use the agentic flow, not hand-typed) |
| Plugin | `hookify` | Add new hooks only when a recurring mistake earns one |

A commit that has not been through `code-reviewer` is unfinished.
A `services.py` function that has not been through `test-writer` is unfinished.

## Architectural ground rules (do not re-litigate)

Repeated here so this file can stand alone as the day-to-day reference. The
authoritative version is in [CLAUDE.md](../CLAUDE.md).

- `ingest/` is the only writer of upstream models. Everything else reads through
  `ingest.services`.
- `ledger/` is the only writer of our system-of-record models.
- Apps never import each other's models. Cross-app reads go through
  `<app>.services`.
- Every domain model inherits `TenantTimestamped`. Every query goes through
  `Model.objects.for_tenant(municipality)`. The only exception is `Municipality`
  itself (the tenant root).
- The reconciliation record is a **projection**, computed on read. The only
  exception is `PaymentSnapshot`, frozen at payment moment for audit.

## Open questions still in the way

| Q | Status | Blocks which slice |
|---|---|---|
| Q4 | Default chosen (SFTP), real channel pending Emfuleni negotiation | Slice 8 ships against the default; switch is config-only |
| Q6 | POPIA / DPA — open, contractual | Blocks **prod cutover** (Slice 12), not any build slice |

Everything else in §6 is decided.

## Naming reconciliation needed before Slice 1

The data-model skill lists `RatepayerAccount` under upstream models. The
design note §6 Q5 introduces a different decomposition:

- `Ratepayer` — the person (one row per human; writable, owned by us)
- `MunicipalAccount` — the billed thing (upstream mirror; one per property/erf)
- `RatepayerAccountLink` — M2M

The design note's decomposition is the right one (a landlord with five erven
is one ratepayer with five links). Action in Slice 1: rename the skill's
`RatepayerAccount` → `MunicipalAccount`, and add `Ratepayer` +
`RatepayerAccountLink` to the writable-owned group. Update
`.claude/skills/data-model/SKILL.md` in the same commit so the skill stops
contradicting the design note.

---

## Slices

### Slice 1 — Upstream ingest models

**Design-note step:** §2.1 (municipal data comes in — model side only, no SFTP yet)
**Forces:** the naming reconciliation above
**App:** `ingest/`

**Tasks**
- [ ] Resolve naming: `MunicipalAccount` (not `RatepayerAccount`)
- [ ] `MunicipalAccount` model — account number, holder name, service address, account class, `source_extract_id`
- [ ] `MunicipalBill` model — (account, period), opening/closing balance, JSONB for charges/payments blocks
- [ ] `MunicipalLedgerEntry` model — line items, FK to bill, JSONB for raw row
- [ ] `MeterReading` model — date, reading, source = "municipality"
- [ ] `Extract` model — the SFTP drop itself (filename, received_at, row_count, status)
- [ ] `ingest.services.get_account(municipality, account_number)` — first real cross-app surface
- [ ] `makemigrations ingest && migrate`
- [ ] `test-writer` on the services module — include a tenancy-boundary test (account from another tenant must not leak)
- [ ] `code-reviewer` on staged changes
- [ ] Update `data-model` skill to match the rename
- [ ] One commit per model is fine; one commit for the whole slice is also fine — your call

**Done when:** `python manage.py shell` can `MunicipalAccount.objects.for_tenant(emfuleni).count()` and tests are green.

### Slice 2 — Writable ratepayer identity

**Design-note step:** §6 Q5 (one ratepayer, many accounts)
**Forces:** picking which app owns identity — recommend a new `identity/` app rather than overloading `portal/`, so the same models serve web + WhatsApp + USSD. Decide before scaffolding.
**App:** `identity/` (proposed — confirm before `/new-django-app identity`)

**Tasks**
- [ ] Confirm app name; `/new-django-app identity`
- [ ] `Ratepayer` model — name, MSISDN (nullable), web credentials placeholder
- [ ] `RatepayerAccountLink` model — FK to `Ratepayer`, FK to `MunicipalAccount` (cross-app FK is fine — it's a real schema relationship)
- [ ] `identity.services.bind_account(ratepayer, account_number)` — manual-bind path
- [ ] `identity.services.find_ratepayer_by_msisdn(msisdn)` — channel auth path
- [ ] Migrations + tests + review

**Done when:** a ratepayer can be created in the shell, bound to two municipal accounts, and queried back through services from another (test) app.

### Slice 3 — SFTP ingestion worker

**Design-note step:** §2.1 (real input flow)
**Forces:** Q8 stack on the VM — Redis must be running locally for RQ
**App:** `ingest/`

**Tasks**
- [ ] Add `rq` and `django-rq` to requirements
- [ ] Set up Redis locally (Docker Compose service); document in `TOOLING.md`
- [ ] `ingest.tasks.poll_sftp()` — RQ task; lists new files in inbox
- [ ] `ingest.tasks.import_extract(file)` — parses one extract into the upstream models
- [ ] `ingest.services.enqueue_poll()` — public entry point
- [ ] Sample fixture extract (CSV) for tests
- [ ] Idempotency: re-importing the same extract is a no-op (use `Extract.filename` or content hash)

**Done when:** a sample CSV dropped into a fixtures inbox is picked up by the task and produces the expected rows under the Emfuleni tenant.

### Slice 4 — Portal: account lookup + bill display

**Design-note step:** §2.2 (ratepayer opens the portal)
**Forces:** Q5 web auth scheme — account number + OTP-to-mobile. OTP delivery stub for now; real SMS in Slice 10/11 alongside USSD.
**App:** `portal/`

**Tasks**
- [ ] Base template + HTMX wiring
- [ ] `/lookup/` view — POST account number → OTP stub
- [ ] `/verify/` view — POST OTP → session
- [ ] `/account/<id>/` view — shows current bill from `ingest.services.get_bill(account, period)`
- [ ] Session-aware tenancy: views know the current municipality
- [ ] HTMX partial for "Challenge" button (no-op for now)

**Done when:** a Selenium-free Django test client walk-through completes lookup → verify → see bill for a seeded Emfuleni account.

### Slice 5 — Evidence upload

**Design-note step:** §2.3
**Forces:** Q8 evidence storage on VM disk + Django storage backend; later swap to S3 is a config change
**App:** `portal/`

**Tasks**
- [ ] `Evidence` model — owned by `portal`, FK to `Ratepayer` + `MunicipalAccount`, kind enum (`photo` / `csv` / `statement_pdf`), file field, uploaded_at
- [ ] `portal.services.record_evidence(...)`
- [ ] Upload view with file size + MIME validation
- [ ] HTMX progress indicator
- [ ] Tests including a malicious-filename case

**Done when:** an evidence file is on disk, an `Evidence` row exists, and another app can fetch it via `portal.services.get_evidence(id)`.

### Slice 6 — VLM extraction

**Design-note step:** §2.4
**Forces:** Q3 — Claude Sonnet 4.6 vision via Anthropic SDK; load the `claude-api` skill
**App:** `vlm/`

**Tasks**
- [ ] Add `anthropic` to requirements; ANTHROPIC_API_KEY env var documented in TOOLING.md
- [ ] `VlmExtraction` model — FK to `Evidence`, raw JSONB response, parsed `reading_kl`, `confidence`, `captured_at`
- [ ] `vlm.services.extract_meter_reading(evidence_id)` — the swappable single function from Q3
- [ ] `vlm.tasks.run_extraction(evidence_id)` — RQ task; triggered when evidence is recorded
- [ ] Confidence threshold constant; low-confidence flows to a "needs retake" status
- [ ] Tests with a mocked Anthropic client (the SDK call is the seam)

**Done when:** a uploaded meter photo produces a `VlmExtraction` row with a parsed reading.

### Slice 7 — Engine: reconciliation contract

**Design-note step:** §2.5 + §5
**Forces:** loading the `reconciliation-contract` skill explicitly before writing engine code
**Apps:** `engine/`, `ledger/`

**Tasks**
- [ ] `Finding` model in `engine/` — archetype, signed amount delta, FK to evidence, statutory citation
- [ ] `LedgerEntry` model in `ledger/` — append-only, our authoritative line items
- [ ] `ReconciledPosition` model in `ledger/` — rolled-up per (account, period)
- [ ] `engine.services.reconcile(account, period) -> ReconciliationContract` — returns the projection (Python dict matching the §5 shape), does NOT persist a record
- [ ] Q3 corroboration rule: photo-anchored findings require ≥1 other source
- [ ] First archetype: `estimate_cap_breach` (MSA s95). Ports from v0.
- [ ] Heavy `code-reviewer` pass — projection-not-table is the rule most likely to slip here
- [ ] `test-writer` with a fixture-based table of input → expected projection

**Done when:** for a seeded account with a municipal bill, an uploaded reading, and one v0 finding, `engine.services.reconcile(...)` returns a contract dict that matches the §5 shape exactly.

### Slice 8 — Payment via Ozow

**Design-note step:** §2.6
**Forces:** Q9 — Ozow merchant credentials (sandbox for build, prod creds for cutover)
**App:** `payments/`

**Tasks**
- [ ] Ozow sandbox account; credentials in env
- [ ] `Payment` model — provider, provider reference, amount, status enum, raw webhook JSONB
- [ ] `payments.services.create_payment_link(account, period, amount)` — calls Ozow PaymentRequest API
- [ ] `payments.views.webhook` — Ozow callback; signature-verifies; updates `Payment.status`
- [ ] Idempotent on duplicate webhooks
- [ ] Pay button in portal renders the Ozow URL

**Done when:** a sandbox payment completes end-to-end and the webhook updates the `Payment` row.

### Slice 9 — Ledger write + correction dispatch

**Design-note step:** §2.7
**Forces:** Q4 — default SFTP transport; build the dispatcher with pluggable transports
**Apps:** `ledger/`, `corrections/`

**Tasks**
- [ ] `PaymentSnapshot` model in `ledger/` — JSONB of the reconciliation contract at payment moment; write-once
- [ ] On Payment webhook success: `ledger.services.record_payment(payment)` — writes `LedgerEntry`, `ReconciledPosition`, and the immutable `PaymentSnapshot`
- [ ] `Correction` model in `corrections/` — target channel, payload, status (queued/sent/acknowledged/failed)
- [ ] `corrections.services.queue_correction(snapshot)` — builds the outbound record
- [ ] `corrections.dispatchers.SftpDispatcher` (default) + `EmailDispatcher` (stub) + `TicketApiDispatcher` (stub)
- [ ] RQ task: `dispatch_pending_corrections` — runs hourly

**Done when:** a completed sandbox payment triggers a `PaymentSnapshot` and a queued `Correction`, which the SftpDispatcher drops into a fixtures outbox.

### Slice 10 — WhatsApp channel

**Design-note step:** §1.5 (channel parity)
**Forces:** Q10 — Twilio WhatsApp sandbox; ratepayer MSISDN binding via `identity.services`
**App:** `channels/whatsapp/`

**Tasks**
- [ ] Twilio sandbox; webhook endpoint
- [ ] First-contact flow: prompt to bind an account number, then call `identity.services.bind_account(...)`
- [ ] Commands: `bill`, `pay`, `photo` (upload triggers same `record_evidence` + VLM flow)
- [ ] Reuse all services from web slices — no business logic in the channel adapter

**Done when:** the sandbox WhatsApp number completes the loop end-to-end.

### Slice 11 — USSD channel

**Design-note step:** §1.5
**Forces:** Q11 — Africa's Talking sandbox; no-photo flow (self-reported readings only)
**App:** `channels/ussd/`

**Tasks**
- [ ] AT sandbox; USSD callback view
- [ ] Menu tree: lookup → bill → enter reading → request payment link → SMS delivery
- [ ] Reuse `identity.services` for MSISDN auth (strongest of the three channels)
- [ ] Tests against the AT simulator

**Done when:** the USSD simulator completes the loop end-to-end.

### Slice 12 — History + projection

**Design-note step:** §2.8
**Apps:** `portal/`, `engine/`

**Tasks**
- [ ] `engine.services.consumption_history(account)` — pulls from `MunicipalLedgerEntry` + ratepayer readings
- [ ] `engine.services.project_next_period(account)` — simple linear projection with explicit caveats
- [ ] HTMX partial: line chart (use a no-JS-build chart lib — e.g. Chart.js via CDN, or server-rendered SVG)
- [ ] History view in portal

**Done when:** the portal shows a chart and a projection paragraph for a seeded account.

### Slice 13 — Deployment + cutover prerequisites

**Design-note step:** §6 Q6, Q8
**Forces:** Q6 — DPA must be signed before any real ratepayer touches the system

**Tasks**
- [ ] Docker Compose file: Postgres, Redis, Django (gunicorn), RQ worker, Nginx
- [ ] GitHub Actions: build → push → SSH deploy
- [ ] EC2 t3.medium in `af-south-1`; security group; TLS via Let's Encrypt
- [ ] Postgres nightly dump → S3 `af-south-1`
- [ ] DPA with Emfuleni — drafted, reviewed, signed
- [ ] One-page runbook in `docs/`

**Done when:** an internal user completes one full loop on the deployed instance against the Ozow sandbox; DPA is signed.

---

## Definition of MVP done

All three are required:

1. **Functional.** A real Emfuleni ratepayer can, through any one of the three
   channels, look up their account, challenge a bill with evidence, pay the
   corrected amount via Ozow, see the result in their history, and have a
   correction record queued for Emfuleni's revenue team.
2. **Operational.** The system is deployed on the AWS Cape Town VM per §6 Q8,
   with nightly backups and a runbook.
3. **Contractual.** The DPA with Emfuleni is signed (§6 Q6).

Anything else is post-MVP.

## What we will NOT do during MVP

Restated from CLAUDE.md so it's visible in the build sequence:

- No Celery (use RQ).
- No Kubernetes (Docker Compose on one VM).
- No GraphQL.
- No new framework or major dependency without asking.
- No designing for scale, traffic, or multi-region we do not have.
- No write API into Solar / Munsoft / Sebata.
