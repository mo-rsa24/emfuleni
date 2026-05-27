---
name: reconciliation-contract
description: The shape and rules of the reconciliation record — the §5 contract from the design note. Load this whenever code touches reconciliation results, findings, ledger entries, the dispute file, the payment instruction, or the correction message. Covers what the contract contains, why it is a projection not a table, who produces it, and who consumes it.
---

# The Reconciliation Contract

This skill defines the shared object that flows between `engine`, `portal`,
`payments`, and `corrections`. Four components depend on this shape. If
they drift, the system breaks. Treat this file as the single source of truth.

For the longer narrative, see `emfuleni/plans/MVP_DESIGN_NOTE.md` section 5.

## The single most important rule

**The reconciliation record is a PROJECTION, not a table.**

It is computed on read from the underlying relational rows. It is NOT
persisted as the live source of truth.

Why this matters: if you persist it as a JSON blob and also persist the
parts, the two will drift. The parts are the source of truth. The contract
is what you assemble from them when a consumer asks.

One exception: at the moment of payment, store an immutable snapshot of
the contract as JSONB. That snapshot is audit history — it represents
"what the customer agreed they owed at the moment they paid." It is
write-once and never updated.

## What the contract contains

Conceptually, one record per (account, period). The fields:

- **identifiers** — `municipality_id`, `account_number`, `period`
- **municipal claim** — the read-only upstream view: opening balance,
  charges, payments, closing balance (as the municipality computed it)
- **evidence** — what the ratepayer provided: meter photos, prior bills,
  ID checks. References to `Evidence` rows. Includes the VLM extraction
  output where applicable.
- **findings** — what the reconciliation engine concluded. Each finding
  has: a type (e.g. `tariff_misapplied`, `meter_reading_disputed`,
  `prescription_window_exceeded`), an amount delta (signed), and a
  citation back to the evidence and the rule that produced it.
- **ledger view** — the writable side. What we (Primeserve) hold as the
  reconciled position: corrected charges, accepted payments, computed
  amount owing.
- **dispute file** — the formal package the correction adapter sends to
  the municipality. References the same findings; renders them in the
  format the destination channel expects.
- **payment instruction** — the amount the ratepayer is being asked to
  pay right now, derived from the ledger view. May differ from the
  municipal claim.

## Who produces what

| Field group         | Produced by    | Read by                          |
|---------------------|----------------|----------------------------------|
| municipal claim     | `ingest`       | `engine`, `portal`               |
| evidence            | `portal`, `vlm`| `engine`, `corrections`          |
| findings            | `engine`       | `ledger`, `portal`, `corrections`|
| ledger view         | `ledger`       | `portal`, `payments`             |
| dispute file        | `corrections`  | (sent outbound, not consumed)    |
| payment instruction | `ledger`       | `payments`, `portal`             |

If a component needs a field it does not produce, it gets it by calling
the producing app's `services.py`. Never by importing models across apps.

## How the contract is assembled

A single function — `engine.services.build_reconciliation(account, period)`
— is the canonical assembler. Every consumer calls this. No consumer
assembles its own version. If you find yourself re-assembling pieces of
the contract somewhere else, stop — extend the assembler instead.

The assembler reads from the relational rows. It returns a Python dict
(or a Pydantic model — decide once and stay consistent) shaped per this
spec. JSON serialization happens at the edges (HTTP response, snapshot
storage), not in the assembler.

## Versioning

The contract WILL change as we learn. When a field changes shape:

- Add new fields; never silently change a field's type.
- If a field must be removed, deprecate it first and keep it populated
  for one release.
- The snapshot stored at payment time captures whatever version was live
  at that moment. Snapshots are never migrated.

## What this skill is NOT

- Not a reference to Django model field types — see the `data-model`
  skill for that.
- Not a reference to the dispute-file outbound format — that lives in
  the `channel-adapter` skill (to be written when channel work starts).
- Not a security/auth spec.