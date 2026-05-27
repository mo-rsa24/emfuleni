---
name: data-model
description: The Django data model for the Primeserve MVP — which apps own which tables, the read-only-vs-writable split, the municipality_id tenancy rule, and how to write a new model. Load this whenever code touches models.py, migrations, querysets, the ORM, or database schema. Pairs with the Postgres MCP for live schema introspection.
---

# Data Model

This skill is the concrete data layout. Pair with the `reconciliation-contract`
skill (which is the projection built on top of these rows) and the
Postgres MCP (which can introspect the live schema).

For the architectural reasoning behind this layout, see `CLAUDE.md` and
`emfuleni/plans/MVP_DESIGN_NOTE.md` sections 4 and 5.

## The three model groups

Every model in this codebase belongs to exactly one of three groups. The
group dictates who can write to it.

### 1. Upstream (read-only)

Mirrors of what we receive from the municipality via SFTP. We READ these.
We never write to them in business logic — only the `ingest` app writes,
and only when refreshing from a new extract.

Lives in: `ingest/models.py`.

- `RatepayerAccount` — one row per municipal account number. `account_number`,
  `municipality_id`, holder name, service address, account class.
- `MunicipalBill` — one row per (account, period). Opening balance, charges
  block, payments block, closing balance. Period is a year-month.
- `MunicipalLedgerEntry` — line items the municipality recorded against
  the account (charges, payments, journals). Read-only mirror.
- `MeterReading` — readings the municipality has on file. Read-only.

These tables have a `source_extract_id` foreign key so we know which SFTP
drop they came from. New extracts insert new rows; we do not UPDATE
upstream rows in place.

### 2. Owned (writable — our source of truth)

The system of record we control. The `ledger` app owns these and is the
ONLY app that writes to them.

Lives in: `ledger/models.py`.

- `LedgerEntry` — our authoritative line items. Charges we accept, payments
  we received, corrections we applied. Append-only — corrections are new
  rows, not edits.
- `ReconciledPosition` — the rolled-up "what we say is owed right now" for
  one (account, period). Recomputed when underlying rows change.
- `PaymentSnapshot` — the immutable JSONB capture of the reconciliation
  contract at the moment of a payment. Write-once. Audit history.

### 3. Derived (writable, but by their producing app only)

Each producing app writes its own evidence and findings. These are inputs
to the ledger, not the ledger itself.

- `Evidence` — owned by `portal`. Photos, prior bills, ID uploads, with
  the originating ratepayer and timestamp.
- `VlmExtraction` — owned by `vlm`. Raw VLM response (JSONB), structured
  fields (reading, units, confidence), and a link back to the `Evidence`
  row it came from.
- `Finding` — owned by `engine`. Type, signed amount delta, citation to
  the evidence and rule. Many findings per (account, period).
- `Correction` — owned by `corrections`. The outbound dispute file: target
  channel, payload, status (queued/sent/acknowledged/failed).
- `Payment` — owned by `payments`. Provider, provider reference, amount,
  status, raw webhook JSONB.

## Hard rules — every model

These are non-negotiable. The `code-reviewer` subagent (Step 7) will check
for these.

1. **Every domain model has `municipality_id`.** No exceptions. Even tables
   that "feel global." If we ever onboard a second tenant, the absence of
   this field on one table is a data leak.

2. **Every queryset goes through a tenant-scoped manager.** Define a
   `TenantManager` once; every model uses it. Never call
   `Model.objects.filter(...)` directly on a domain model. Use
   `Model.objects.for_tenant(municipality).filter(...)`.

3. **Apps never import each other's models.** Cross-app reads go through
   the producing app's `services.py`. If `portal` needs an `Evidence`, it
   calls `portal.services.get_evidence(...)` — even though `Evidence`
   lives in `portal`, every other consumer goes through services.

4. **No app writes to upstream models.** `ingest` is the only writer to
   the upstream group.

5. **No model represents the reconciliation record itself.** It is a
   projection. See `reconciliation-contract` skill.

## Common fields on every domain model

Stamp these on every model via an abstract base:

```python
class TenantTimestamped(models.Model):
    municipality_id = models.ForeignKey(Municipality, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = TenantManager()

    class Meta:
        abstract = True
        indexes = [models.Index(fields=["municipality_id"])]
```

## Migrations

- Generate with `python manage.py makemigrations <app>`.
- Inspect the generated file before committing it. Catch surprises early.
- Never edit a migration that has run anywhere real. Add a new one.
- One commit per migration is the easiest pattern to debug later.

## When you have the database running

The Postgres MCP can introspect the live schema. Use it to verify what
actually exists vs what the models say. Two common drift modes the MCP
catches:

- A model was edited but `makemigrations` was not run.
- A migration was run on dev but a column was renamed manually somewhere.

A good check before writing query code: "Postgres MCP — show me the
columns of `ledger_ledgerentry`." Compare with `models.py`. If they
disagree, fix before continuing.

## What this skill is NOT

- Not the projection shape — see `reconciliation-contract`.
- Not exact field types for every column — those are decided in the
  models themselves. This skill names the tables and the rules; the
  fields are the working code.
- Not the service-layer API. That is `<app>/services.py` in each app.