# CLAUDE.md — Primeserve MVP

Project context for Claude Code. This file is project-local. It loads only
when VS Code is opened on this folder. Keep it under ~200 lines.

## What this project is

A ratepayer-facing layer on top of the Reconciliation v0 demo. A ratepayer
can challenge a municipal bill with evidence, pay what they actually owe,
and the municipality receives a clean correction message to action on their
side. Launch tenant: Emfuleni. See `emfuleni/plans/MVP_DESIGN_NOTE.md` for
the full design note — that note is the source of truth for product scope.

## What we are optimizing for

Read these before proposing anything. They decide trade-offs.

- Scale: one tenant, ~84,000 accounts, tens of concurrent users, spiky at
  month-end. Pre-scale. Do not design for scale we do not have.
- Latency tolerance: high. Two-second pages are fine. A 10s VLM call is fine.
- Budget: MVP. Fixed cloud floor target under ~R3,500/month.
- Team size: 1-3 developers.
- Requirements volatility: high. Several decisions are still open.
- Dominant constraints: small team and high volatility. Both mean: fewer
  moving parts, boring proven tech, clean seams. Novelty is a cost, not a goal.

## Architecture decisions (decided — do not re-litigate)

- Backend: Django, modular monolith. One Django project, multiple Django
  apps, one deployable. Not microservices.
- Apps: ingest, engine, vlm, ledger, payments, corrections, portal,
  channels (whatsapp, ussd). One app per concern.
- Database: PostgreSQL, single database, one schema. SQLite for local dev
  is acceptable. Use JSONB columns inside relational tables for genuinely
  variable data (raw VLM responses, provider webhook payloads).
- Background work: Django + RQ + Redis. Not Celery.
- Web: Django + HTMX. Views return rendered HTML fragments. No separate JS
  frontend. No GraphQL.
- API: plain Django views plus a thin service layer now. Add Django REST
  Framework only when a separate process needs the data over HTTP. Not yet.
- Language: Python everywhere.
- Hosting: one VM, Docker Compose, managed Postgres, South African region.
  Build against local Docker Compose. Defer the exact cloud choice.

## Hard rules

- Source of truth. Upstream municipal data (the SFTP extract) is READ-ONLY.
  The `ledger` app is the ONLY writable system of record we own. Never write
  to municipal data models. Never simulate write-back to Solar/Munsoft/Sebata.
- Service layer. Apps communicate ONLY through each other's `services.py`.
  Never import another app's models directly.
- Tenancy. Every domain model has a `municipality_id` field. Every query
  goes through the tenant-scoped manager — never a bare `.objects.filter()`
  on a domain model.
- The reconciliation record (see the design note section 5) is a PROJECTION,
  not a table. Persist its parts as relational rows. Compute the JSON object
  on read. Exception: store one immutable snapshot at the moment of payment,
  for audit.
- Migrations. Never edit a migration that has run anywhere real. Add a new one.

## Never do (without asking first)

- No Kubernetes. One VM, Docker Compose.
- No microservices. Modular monolith.
- No GraphQL.
- No Celery. Use RQ.
- No new framework, library, or major dependency without asking.
- No designing for scale, traffic, or multi-region we do not have.

## Tooling

Fill the bracketed values in once, then leave them.

- Python version: 3.11 (via micromamba env `primeserve`)
- Dependency manager: pip inside micromamba env `primeserve`
- Activate env: micromamba activate primeserve
- Run the app: docker compose up (when compose file exists)
- Run tests: python manage.py test
- Run migrations: python manage.py migrate
- Lint / format: ruff check . && ruff format .
- Django check: python manage.py check

## How to work in this repo

- New to a task? Read the design note section relevant to the component
  before writing code.
- Default to the smallest change that works. Prefer reversible choices.
- When a decision is expensive to reverse (data model, tenancy, source of
  truth), stop and confirm before proceeding.