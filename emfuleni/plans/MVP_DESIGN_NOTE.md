# MVP Design Note — One Step Beyond v0

**Status:** living document. Edited as topics are converged on, one at a time.
**Audience:** Primeserve internal team.
**Relationship to v0:** the [Reconciliation v0 plan](../../docs/RECONCILIATION_V0_PLAN.md) stays as-is and ships first. This MVP is the next layer on top: it extends v0 with a ratepayer-facing portal, photo-and-spreadsheet ingestion, VLM meter reading, payment, and a queued correction channel back to the municipality.
**Job of the MVP:** turn the v0 demo's "look at the gap" moment into a working loop — a ratepayer can challenge a bill with evidence, pay what they actually owe, and the municipality gets a clean correction message they can action on their side.

---

## 0. What is decided vs. what is still open

This plan is built up one topic at a time. Below, **decided** means the topic has been talked through and a position has been written down. **Open** means the topic is queued for a later conversation.

| Topic | Status | Section |
|---|---|---|
| Customer posture and authority model | **Decided** | §1 (Position C — hybrid) |
| Ratepayer-facing channels (web + WhatsApp + USSD) | **Decided** | §1.5 |
| End-to-end loop in plain English | **Decided** | §2 |
| Architectural implications of Position C and multi-channel | **Decided** | §3 |
| Payment flow (settlement direction) | **Decided** | §6 Q1 — pay-through |
| Web framework choice | **Decided** | §6 Q2 — Django + HTMX |
| VLM choice (trust threshold) | **Decided** | §6 Q3 — hosted frontier, swappable |
| Correction message channel to the municipality | Default chosen, pending negotiation | §6 Q4 |
| Auth / identity for the ratepayer (per channel) | **Decided** | §6 Q5 — channel-aware; build assumes MSISDN-on-file is absent; one ratepayer → many accounts |
| POPIA posture and data-processing agreement | Open | §6 Q6 |
| Multi-tenancy (one municipality vs many) | **Decided** | §6 Q7 — namespaced from day one, Emfuleni-only at launch |
| Hosting and deployment posture | **Decided** | §6 Q8 — single VM in AWS `af-south-1`, Docker Compose |
| Payment provider choice | **Decided** | §6 Q9 — Ozow (EFT-focused, card supported) |
| WhatsApp Business provider | **Decided** | §6 Q10 — Twilio for MVP |
| USSD aggregator | **Decided** | §6 Q11 — Africa's Talking for MVP |

---

## 1. Position — Hybrid (C)

The municipality is the contractual customer. We have read access to their billing data. We do **not** have write access to their billing database.

1. The municipality sends us their billing extract over SFTP. *Example: Emfuleni drops `billing_june.csv` into our inbox on the 1st of the month.*
2. We read it, never write back. *Example: even if we are certain account 88231104 is on the wrong tariff, we record the finding on our side — we do not touch Solar / Munsoft / Sebata.*
3. The ratepayer-facing portal is ours, run on behalf of the municipality. *Example: `pay.emfuleni.gov.za` is a CNAME pointing at our service.*
4. When a ratepayer pays a corrected amount, **two records update**: our own ledger (which we own and can write to), and a queued correction message to the municipality (which their clerk actions). *Example: our ledger flips account 88231104 for June 2026 to `paid`; an outbound file lands in the municipality's inbox listing the correction for a clerk to apply in Solar.*

**Why this position.** It is the only one consistent with the v0 plan's posture (no write-back, sits beside the billing engine) while being one honest step beyond it. The two alternatives both require commitments not in evidence: Position A needs a write API into the municipality's billing system; Position B needs ratepayers to trust a non-municipal intermediary to collect their rates, which is a much harder go-to-market.

**What this rules in.** A real SFTP feed in. A real ratepayer portal. A real payment provider. Our own ledger as the system of record *for the MVP* — the municipality's database catches up via the correction channel.

**What this rules out for the MVP.** Any direct read/write integration with Solar / Munsoga / Sebata. Any pretence that our number is the legally authoritative bill — it is a proposed correction, paid in good faith.

---

## 1.5 Three ratepayer-facing channels, one backend

The MVP exposes the same loop through three surfaces. The web portal is one of them, not the whole product.

1. **Web portal.** Django + HTMX (Q2). Full evidence types — photo, spreadsheet, statement. Best for first-time interactions and for users on a laptop or smartphone browser.
2. **WhatsApp Business.** Account lookup, photo-of-meter upload, payment link delivery, simple history snippets. WhatsApp's native photo handling makes it the best non-web channel for evidence collection. Identity is verified by WhatsApp's phone-number binding.
3. **USSD.** Account lookup, current-bill query, payment-link request (delivered to the user by SMS), self-reported reading entered on the keypad. No photos — USSD is text-only. Identity is verified by the MSISDN the network presents. The right channel for ratepayers without smartphones or data.

**What this forces.**

- **A headless internal API.** Django views serve both the HTMX portal *and* the WhatsApp and USSD adapters. The view returns JSON to a channel adapter and HTML to the portal — same business logic, different renderer.
- **Two new channel components.** `channels/whatsapp/` (Meta WhatsApp Cloud API or via Twilio — open Q10) and `channels/ussd/` (Africa's Talking or similar aggregator — open Q11).
- **A channel-aware evidence model.** Web and WhatsApp can submit photos; USSD cannot. The reconciliation engine still requires corroborating evidence (Q3) so a USSD-only ratepayer can challenge a bill by submitting self-reported readings, but a photo-anchored correction requires web or WhatsApp.
- **Channel-aware auth (Q5).** WhatsApp and USSD inherit identity from the network (verified phone number); the web portal needs its own scheme. The three are reconciled into one ratepayer account in our database.
- **One Ozow payment link, three delivery paths.** The link itself is identical regardless of channel — the web embeds it as a button, WhatsApp sends it as a message, USSD triggers an SMS to the user containing it. Settlement to the municipality is the same in all three cases.

---

## 2. End-to-end loop

### 2.1 Municipality data comes in

1. The municipality sends us their monthly billing extract over SFTP. *Example: on the 1st of June, Emfuleni drops `billing_june.csv` into our SFTP inbox.*
2. We read it into our database. *Example: we parse 84,000 account rows and store them, tagged with the period `2026-06`.*
3. We never write back to their database. *Example: if we think account 88231104 should be on a business tariff, we record the finding on our side only.*

### 2.2 Ratepayer opens the portal

1. The ratepayer finds their property. *Example: Mrs Dlamini enters her account number 88231104 and lands on her property page.*
2. They see the municipality's current bill. *Example: "Emfuleni says you owe R 2,940 for June 2026."*
3. They can accept and pay, or challenge it. *Example: Mrs Dlamini clicks "Challenge" because she thinks the bill is too high.*

### 2.3 Ratepayer uploads evidence

1. They can upload a spreadsheet of their own readings. *Example: a CSV with `date` and `reading_kl` taken weekly off their meter.*
2. They can upload a photo of the meter. *Example: a phone snap of a five-digit dial reading `04127`.*
3. They can upload the municipal statement they received. *Example: a PDF emailed to them, cross-checked against the SFTP extract.*

### 2.4 The vision model reads the photo

1. A VLM extracts the dial reading. *Example: prompt — "extract the dial reading as an integer in kilolitres." Response — `{"reading_kl": 4127, "confidence": 0.94}`.*
2. It returns a structured record. *Example: stored as `{reading_kl, confidence, captured_at, image_ref}`.*
3. Low-confidence reads are queued, not auto-applied. *Example: a blurry photo at confidence 0.41 is held and the ratepayer is asked to retake.*

### 2.5 The reconciliation engine produces one number

1. It takes three inputs: the municipality's figure, the ratepayer's evidence, and the v0 rule findings for that property. *Example: municipality says R 2,940, ratepayer's meter shows 14 kℓ used, v0 rules flag the account as a long-running estimate.*
2. It outputs the amount we believe the ratepayer actually owes. *Example: "Our calculation: R 1,820 for June 2026."*
3. It outputs a one-paragraph plain-English explanation. *Example: "Your meter shows 14 kℓ used, not the 41 kℓ estimated. At the residential tariff this is R 1,820. The municipality's higher figure appears to be a long-running estimate; section 95 of the Municipal Systems Act caps estimates at 6 months."*

### 2.6 Ratepayer pays through a link

1. The ratepayer sees both numbers and a payment button. *Example: Emfuleni's R 2,940 struck through, our R 1,820 highlighted, "Pay R 1,820" button.*
2. Clicking opens a hosted payment page. *Example: a Yoco / PayFast / Stitch checkout prefilled with the account reference.*
3. They complete payment by card or EFT. *Example: card payment confirmed in ~10 seconds.*

### 2.7 Two records update on successful payment

1. Our ledger marks the account paid for the period. *Example: account 88231104 for `2026-06` flips from `outstanding` to `paid` with evidence attached.*
2. A correction message goes to the municipality. *Example: `corrections_2026-06-28.csv` lands in their inbox with `{account, period, paid, billed, reason, evidence_ref}`.*
3. A clerk on the municipality side actions the message. *Example: a revenue clerk opens the file, reviews the evidence, and adjusts account 88231104 in Solar manually.*

### 2.8 Ratepayer sees history and projection

1. The ratepayer views their consumption over time. *Example: a line chart of monthly kℓ from January to June 2026, built from municipal extracts plus uploaded readings.*
2. We project next month's likely usage. *Example: "July is likely to be 12–15 kℓ, costing roughly R 1,600–R 2,000."*
3. The projection is honest about what it does not know. *Example: footnote — "assumes your usage pattern continues and the residential tariff remains in effect."*

---

## 3. What Position C forces architecturally

1. **The SFTP-in adapter from v0 is reused unchanged.** The MVP inherits v0's ingestion path. The MVP's job is what happens *after* ingestion, plus the ratepayer-facing surface.
2. **We need our own database.** It holds: ingested municipal data, ratepayer-uploaded evidence, VLM extractions, reconciliation findings, our ledger of corrected-amount payments, queued correction messages. This is genuinely new vs. v0 (which was batch-in-batch-out, no persistent state between runs).
3. **We need a real web app, not just a dashboard.** v0 is one read-only page. The MVP has account lookup, authenticated ratepayer sessions, file uploads, a payment redirect, and a history view. Framework choice is now a real decision (see §6 Q2).
4. **We need an outbound correction channel.** v0 ends at "find the gap." The MVP has to *tell* the municipality about the gap in a structured, auditable way. This is a small piece of code but a large piece of negotiation (see §6 Q4).
5. **We are now a payment intermediary, however briefly.** Even if the money does not touch us (see §6 Q1), we are the surface that initiates the transaction. That brings a regulator-shaped shadow over the MVP that v0 did not have.
6. **The "LoB record" is two ledgers, not one.** Our ledger updates synchronously on payment. The municipality's ledger updates asynchronously when a clerk actions the correction message. The MVP must be designed so that this asynchrony does not confuse ratepayers ("I paid — why is the municipality still sending me invoices?").

---

## 4. Component shape

A first cut, deliberately small. Each component maps to one concern.

```
primeserve/
├── docs/
│   └── RECONCILIATION_V0_PLAN.md          # unchanged
├── emfuleni/
│   ├── docs/                               # source-of-truth context
│   └── plans/
│       └── MVP_DESIGN_NOTE.md              # this file
├── reconciliation-v0/                      # v0 demo (per v0 plan)
└── mvp/                                    # new
    ├── ingest/                             # SFTP poller + v0 adapters, reused
    ├── engine/                             # v0 rules + ratepayer-evidence rules
    ├── vlm/                                # meter-photo extractor
    ├── ledger/                             # our LoB ledger (database models)
    ├── payments/                           # provider adapter, webhook handler
    ├── corrections/                        # outbound message builder + dispatcher
    ├── portal/                             # Django app + HTMX templates (web channel)
    ├── channels/
    │   ├── whatsapp/                       # Meta Cloud API or Twilio adapter (Q10)
    │   └── ussd/                           # Africa's Talking or similar (Q11)
    └── tests/
```

Three things to notice:

1. **`engine/` is a superset of v0.** v0's five archetype rules stay. New rules are added that take ratepayer-uploaded evidence into account.
2. **`ledger/` is the only system of record we own.** Everything else either reads from upstream (municipal SFTP) or writes downstream (corrections to municipality, payment to provider).
3. **`portal/` is the only user-facing piece.** Internal team views (the v0 dashboard) live separately and can stay in `reconciliation-v0/` for now.

---

## 5. Reconciliation contract (sketch)

The engine's job is to produce, for each `(account, period)` pair, a single record of this shape:

```
{
  account_ref:        "88231104",
  period:             "2026-06",
  municipality_says:  2940.00,
  we_say:             1820.00,
  delta:              -1120.00,
  explanation:        "<one paragraph plain English>",
  findings: [
    {archetype: "estimate_cap_breach", evidence_ref: "ev_8821", statutory: "MSA s95"},
    ...
  ],
  evidence: [
    {kind: "meter_photo", vlm_reading_kl: 4127, confidence: 0.94, image_ref: "..."},
    ...
  ],
  status:             "awaiting_payment" | "paid" | "disputed_no_payment"
}
```

The contract is deliberately flat and JSON-serialisable so the payment flow, the portal, and the correction-message dispatcher can all read it without translation.

---

## 6. Open questions — the queue

These are the topics to converge on next, in roughly the order they block the build.

### Q1. Payment flow and settlement — who actually receives the money?

**Decided: pay-through.** The ratepayer pays the municipality directly through a link we generate. The provider settles to the municipality's existing merchant account. We never touch the money. We remain a software vendor, not a payment institution.

**What this forces.**
- We need either the municipality's merchant credentials, *or* a payment provider that supports payments-on-behalf / split payments where we are the platform and the municipality is the sub-merchant. This is the open follow-on captured as **Q9**.
- The "successful payment" event we listen for is a webhook from the payment provider, not a balance change in our own ledger. Our ledger writes only after the webhook confirms.
- Refunds and chargebacks are the municipality's problem, not ours. The MVP must make this contractually explicit.

### Q2. Web framework

**Decided: Django + HTMX.** Django gives us auth, admin, ORM, and migrations on day one. HTMX gives the portal enough interactivity (file uploads with progress, drill-down panels, payment redirect) without a separate JS front-end.

**What this forces.**
- Python is the MVP's primary language. v0's engine (also Python) plugs in natively.
- The database is whatever Django talks to comfortably — **Postgres** is the default; SQLite is fine for local dev.
- Background work (SFTP polling, VLM calls, correction-message dispatch, webhook handling) runs out of band via a task queue. **Django + Celery + Redis** is the conventional stack; **Django + RQ** is the lighter alternative. Pick at build kickoff.
- A clean interior API boundary (Django REST framework or plain Django views returning JSON) is preserved so a richer JS front-end or a public municipal API can be bolted on later without rewriting.

### Q3. VLM choice and trust threshold

**Decided: hosted frontier model, behind a swappable interface.** Default to Claude Sonnet 4.6 vision for the MVP; the call site is a single function (`extract_meter_reading(image) -> {reading_kl, confidence}`) so swapping in GPT vision, Gemini vision, or a self-hosted Qwen2-VL later is a one-file change.

**Trust threshold — also decided.** Nothing auto-applies. Every VLM reading is logged as evidence with its confidence score. The reconciliation engine requires **at least one corroborating source** (the municipality's own meter log, the ratepayer's own spreadsheet, or a second photo on a different date) before producing a corrected amount based on the photo. This protects against single-photo misreads on both sides — under-billing the ratepayer (revenue loss) and over-billing them (trust loss).

### Q4. Correction message channel to the municipality

**Default: file drop on SFTP, with a human-readable Excel index file.** Email is the fallback. The exact channel is **still to be negotiated with Emfuleni's revenue team** — what they will actually open and process.

**Build implication.** The `corrections/` component is built as a *dispatcher* with pluggable transports (SFTP, email, ticket-API), not hard-wired to one channel. The default transport is SFTP; the others are stubs we light up if Emfuleni asks. This keeps the channel negotiation off the build's critical path.

### Q5. Auth / identity for the ratepayer (per channel)

**Decided.** Three channels, three identity stories that reconcile into one ratepayer.

- **Web.** Account number + OTP-to-mobile. Fallback for no-mobile-on-file: account number + last-4-of-ID.
- **WhatsApp Business.** The verified MSISDN *is* the identity (WhatsApp verifies it). First contact prompts the user to bind a municipal account number to that MSISDN.
- **USSD.** The MSISDN is presented by the network on every session — strongest identity of the three, free. Same first-contact binding to an account number, via keypad.

**Build assumption on MSISDN-on-file.** Whether Emfuleni includes the MSISDN-on-file in the billing extract is unknown. The MVP is built for the worse case (not supplied) — manual bind on first contact — and lights up the smoother path automatically if the field arrives in the extract. The adapter detects whether the column exists and downgrades to manual-bind when it does not.

**Data model — one ratepayer, many accounts (decided).** A landlord with several properties is a single ratepayer. The model is:

- `Ratepayer` — the person. Holds name, MSISDN (once learned), web credentials, OTP state.
- `MunicipalAccount` — the billed thing (a property, an erf, an account on Solar/Munsoft/Sebata). Holds account number, address, current tariff, latest reading.
- `RatepayerAccountLink` — many-to-many between the two. A landlord with five properties has five links. A single rented house has one link.

All three channels resolve to a `Ratepayer`. The portal shows the linked accounts as a list when there is more than one; WhatsApp and USSD prompt "which account?" when there is more than one. The reconciliation engine and ledger operate at the `MunicipalAccount` level, not the `Ratepayer` level — the gap is always against a specific billed account.

**Session and revocation.** Web "remember me" defaults to 30 days, with re-auth required at the payment step. An MSISDN bound to an account re-verifies after 90 days of inactivity to handle MNO number recycling.

### Q6. POPIA posture

We will be processing personal data (ratepayer name, address, contact, meter photos, payment metadata) on behalf of the municipality. This implies:

- A **data-processing agreement** between Primeserve and the municipality.
- A **clear data-retention policy** (how long we keep evidence, ledger, photos).
- **Subject-access machinery** (a ratepayer can request their data; we can produce it).

None of this is technically hard. It is a contractual prerequisite, not a build task.

### Q7. Multi-tenancy

**Decided: namespaced from day one, launched with Emfuleni only.** A `municipality_id` field on every table. A per-tenant SFTP inbox. A per-tenant correction channel config. A per-tenant payment-provider config (since each municipality's merchant or sub-merchant is distinct under pay-through).

**What this forces.**
- Every domain model carries `municipality_id`. Django's query manager defaults to filtering by the currently-scoped tenant.
- URLs are tenant-scoped where they need to be (e.g. `pay.<tenant>.gov.za` or `/<tenant>/...`). The exact convention is a small later decision.
- The admin UI shows one tenant at a time; a superuser can switch.

### Q8. Hosting and deployment

**Decided: single VM, AWS Cape Town (`af-south-1`), Docker Compose.** Postures considered were (1) single VM in-country, (2) full managed services in-country, (3) on-prem on a municipality VM. Posture 1 wins for MVP. Posture 3 (municipal infra) is a procurement trap at this stage and Posture 2 is overweight for a single tenant at MVP volume.

**Why AWS Cape Town over Hetzner ZA or Afrihost.** No existing Primeserve cloud account points the decision at "lowest activation cost + best procurement-board optics." Hetzner ZA is cheaper but answers a CFO's "where does our data live?" with a less recognisable brand. `af-south-1` answers with a sentence the procurement board will sign off without further questions. The cost difference at MVP scale is trivial; the optics difference is not.

**Stack on the VM.**
- Postgres (the database)
- Redis (Celery broker + Django cache)
- Django app (gunicorn behind Nginx)
- Celery worker (SFTP polling, VLM calls, correction dispatch, webhook handling)
- Nginx (TLS termination + reverse proxy)

All run as Docker Compose services on one EC2 instance (t3.medium tier is enough to start).

**Evidence-file storage.** On VM disk for MVP, behind Django's storage backend abstraction. Photos and scanned statements are the space-hungry piece; the migration to **S3 in `af-south-1`** is expected around month 3 and is a config change, not a code change.

**Deploy story.** GitHub Actions builds the Docker image, pushes to a registry, and SSHes to the VM to run `docker compose pull && docker compose up -d`. No Kubernetes, no Terraform-for-the-app at MVP. A one-page runbook for the VM is the operational artefact.

**Backups.** Nightly Postgres dump to S3 `af-south-1`. Evidence files snapshotted weekly. Retention 30 days at MVP; tuned later.

### Q9. Payment provider — given pay-through, which one?

**Decided: Ozow.** Instant EFT, widely recognised in South Africa, fits the EFT-first preference, and is already familiar to ratepayers from other government-adjacent billers. Settlement is to the municipality's Ozow merchant account; we never touch the funds.

**What this forces.**
- The municipality (Emfuleni) must hold an Ozow merchant account, or onboard onto one. Confirmation of the account state with Emfuleni is a prerequisite to going live, not to building.
- We integrate against Ozow's PaymentRequest API. We listen for the success webhook and only then write our ledger.
- The same Ozow link is delivered three ways: embedded as a button in the web portal, sent as a WhatsApp message, or SMSed to a USSD user after they request to pay.
- Card payments are supported via Ozow's complementary flow if needed; EFT is the default.

### Q10. WhatsApp Business provider

**Decided: Twilio for MVP.** Fastest path to a working WhatsApp loop; well-documented SDK; sandbox available for development without Business Verification. Adapter is one component (`channels/whatsapp/`) so swapping to Meta WhatsApp Cloud API direct in post-MVP is a contained change driven by per-message economics when volume justifies it.

### Q11. USSD aggregator

**Decided: Africa's Talking for MVP.** Strong developer experience, sandbox available, simplest onboarding for SA + pan-African coverage. Adapter is one component (`channels/ussd/`) so swapping to a direct MNO aggregator (Grapevine, Integrat) is a contained change if per-session economics later justify it.

---

## 7. What "next conversation would be 'let's start building'" means

**Resolved so far:** Q1 (pay-through), Q2 (Django + HTMX), Q3 (hosted frontier VLM, swappable, no auto-apply), Q7 (multi-tenant from day one, Emfuleni-only launch), Q9 (Ozow). Three ratepayer channels (web + WhatsApp + USSD) are now part of the design. Q4 has a default channel (SFTP file drop) but the actual channel is pending negotiation with Emfuleni — build proceeds with a pluggable dispatcher.

**Still to converge:**
- **Q6 — POPIA / data-processing agreement.** Contractual, not a build blocker, but needed before any real ratepayer touches the system. The data-processing agreement with Emfuleni is the artefact.

This is the last item between this plan and a build kickoff. Once Q6 is settled (or at least drafted), the next conversation is the build kickoff: data model, sprint zero, who owns which component.

---

## 8. Departures from the v0 plan, flagged

1. **v0 has no persistent database.** The MVP does. v0 stays batch-in-batch-out; the MVP adds a ledger and an evidence store alongside.
2. **v0 has no ratepayer surface.** The MVP adds one. The v0 dashboard remains as the *internal* / municipal-facing view.
3. **v0 explicitly defers "write-back to billing system" and "resident channels" to Phase 1 pilot.** The MVP picks up *resident channels* and *queued write-back via correction message* — but stops short of a direct write API into Solar / Munsoft / Sebata. Position C draws the line in the same place v0 does: we still do not touch the municipal database.
4. **v0's deliberate messiness in synthetic data is preserved.** The MVP inherits v0's adapters and matching logic; ratepayer-uploaded evidence is layered on top, not in place of, the municipal sources.
