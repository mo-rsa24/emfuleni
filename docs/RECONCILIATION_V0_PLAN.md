# Reconciliation Engine v0.000001 — Build Plan

**Status:** draft for review. No code yet. Written to be edited.
**Audience:** Primeserve internal team.
**Source of truth:** [`emfuleni/docs/ProblemStatementDoc.docx`](../emfuleni/docs/ProblemStatementDoc.docx), [`emfuleni/docs/Emfuleni-Phase1-Solution-Summary.docx`](../emfuleni/docs/Emfuleni-Phase1-Solution-Summary.docx), [`emfuleni/docs/PresoEMF.pdf`](../emfuleni/docs/PresoEMF.pdf).
**Job of v0:** in a live meeting, a municipal CFO or Revenue Management head looks at a dashboard and immediately sees the gap between what Emfuleni currently bills and what it should be billing. Success = the room asks "how soon can we run this on our real data?"

---

## 0. Source comparison — the repo's approach vs. this brief's approach

Both the repo context (problem statement + solution summary + pitch deck) and the brief describe a reconciliation engine for the Emfuleni Finance Cluster. They agree on more than they disagree. The disagreements are mostly about what the **v0 demo** should look like — not about what the production system should do.

### Where they agree

- **Customer.** Emfuleni Finance Cluster — CFO and Head of Revenue Management. The SPV (VCWU) takes water operations on 1 July 2026 but **does not** take billing. The repo is explicit (problem doc §6.2, solution doc §01); the brief mirrors it.
- **Wedge.** A reconciliation-and-accuracy layer that sits **beside** the existing billing engine. Not a replacement. Both sources are emphatic on this.
- **Five discrepancy archetypes.** Repo solution doc §03 and the brief's section 2 list the same five: unbilled property, tariff misclassification, meter-vs-billed mismatch, stale/wrong owner or value, duplicate/orphaned account.
- **Statutory grounding.** Each discrepancy carries a rand impact **and** a statutory ground. Repo cites MPRA + Municipal Systems Act generally; brief pins specific sections (MPRA s49, MSA s78).
- **Dashboard language.** Speaks only in rands, properties, and recoverable revenue. Never schemas, joins, or match scores. Self-explanatory, no walkthrough.
- **Ingestion model in production.** Monthly file extracts over SFTP, not a live API. Designed for messy, partial, format-drifting inputs.

### Where they diverge

| Dimension | Repo (solution summary) | Brief | Judgment |
|---|---|---|---|
| **Data sources** | Four registers: Valuation Roll, Deeds Office, Surveyor-General cadastre, Billing System. Plus VCWU bulk meter post-July 2026. Cadastral Land Parcel Identifier (LPI) as the canonical join key. | Four sources: Billing extract, Valuation/property roll, Meter-reading log, one "awkward" source (scanned PDF statement or mock API). | **Combine.** The repo's framing is correct for the production design (Deeds + SG cadastre matter for ownership and boundary truth). The brief's framing is correct for a **demo** — meter readings and an awkward format are what produce visible, instructive discrepancies in a live room. v0 uses the brief's four sources; the plan flags Deeds + SG as the next sources to add post-pilot. |
| **Format diversity in v0** | All CSV over SFTP — uniform inputs assumed. | Heterogeneous: CSV + PDF table + scanned-style PDF or mock API response. | **Brief wins for v0.** A demo that ingests only CSVs reads as a CSV-diff tool. Heterogeneous inputs show the engine coping with real-world drift, which is the harder, more credible story. Production can still standardise on CSV/SFTP later. |
| **Scope** | "One or two test suburbs" with self-sourced public data. | 1–5 households and businesses. | **Brief wins.** 1–5 properties is the right size for a 25-minute meeting — every flagged discrepancy fits on screen. A suburb-scale demo dilutes the "look at *this* property" moment. |
| **Public data sourcing** | Build the demo on **real** valuation roll + cadastre + Deeds data for one or two suburbs, with synthetic billing data standing in. | All data synthetic. | **Brief wins for v0**, but with a caveat. Real data is more credible — but also risks POPIA exposure, scraping load on public services, and getting bogged down in a data-collection sub-project. For v0 (a demo, not a pilot run), fully synthetic is faster and safer. Switch to real-public + synthetic-billing for the **pilot** stage, as the repo proposes. |
| **Statutory specificity** | MPRA + MSA generically. | MPRA s49, MSA s78. | **Brief is better.** Specific sections read as legally literate to a CFO. Use them. |
| **Worked example** | None concrete. | 247 Houtkop Road, Vanderbijlpark — tariff misclassification + stale valuation. | **Use the brief's example.** It is fully specified and demonstrably plausible (Vanderbijlpark is in Emfuleni; Houtkop Road exists). |

### Synthesised v0 approach

The plan below uses the **brief's demo-shape choices** (4 sources, heterogeneous formats, 1–5 properties, fully synthetic data, MPRA s49 / MSA s78) **on top of the repo's architectural skeleton** (reconciliation engine + correction-aware dashboard, sitting beside the billing engine, framed for the Finance Cluster). Departures from either source are flagged inline.

**One conflict to surface, not silently resolve.** The repo treats meter readings as part of the billing system's input. The brief treats the meter-reading log as a separate source. The plan follows the brief — separating meter readings makes the "meter says 41 kℓ, bill says 12 kℓ" contradiction directly visible on the dashboard, which is the v0's whole job. Production may collapse this back into the billing system depending on what Solar/Munsoft/Sebata actually exposes (see Open Questions).

---

## 1. Synthetic data sources

Four sources for 1–5 properties. Each is internally plausible but **deliberately disagrees with the others** in instructive ways. All names, ID numbers, account numbers, and erf numbers are fabricated — no real ratepayer data.

| # | Source | Format | Stands in for | Plausible owner in production |
|---|---|---|---|---|
| 1 | Billing-system extract | CSV | Solar / Munsoft / Sebata nightly export | Emfuleni Finance Cluster (mSCOA billing engine) |
| 2 | Property / valuation roll | PDF with embedded table | Statutory valuation roll, published periodically | Municipal Valuer |
| 3 | Meter-reading log | CSV | Field meter-reader handheld export or smart-meter dump | Metsi-a-Lekoa (water dept) / VCWU post-July 2026 |
| 4 | Account statement | Scanned-style PDF (text-positioned, not OCR'd) | Statement printed by the billing system and re-scanned for a query | Emfuleni Finance Cluster |

**Why these four, in this combination.** Sources 1 + 2 give us the structural mismatches (unbilled properties, tariff misclassification, stale values). Source 3 gives us the consumption-vs-bill contradiction. Source 4 is the awkward one — it forces the engine to extract structured fields from an unstructured PDF, which is the credibility test in a live demo: "yes, even *this* messy thing goes in."

**Deliberate messiness, by source.**

- **Billing extract (CSV).** Trailing whitespace on identifiers. Mixed-case tariff codes (`Residential`, `RESIDENTIAL`, `res`). Inconsistent date formats (`2026/03/01`, `01-Mar-26`). Account numbers sometimes formatted with spaces, sometimes not. One row missing the erf number entirely.
- **Valuation roll (PDF table).** Table embedded in a PDF with a header and a footer page — extraction must find the table, not just dump the page. Erf numbers as strings (`E12345`, `Erf 12345`, `12345/IR`). One property has an alternative spelling of the suburb (`Vanderbijl Park` vs `Vanderbijlpark`).
- **Meter-reading log (CSV).** Two columns of the same value under different names (`reading_kl` and `consumption`). One row is a re-read on the same date. One row has a reading of `0` for a property the billing extract shows as actively consuming. Timezone-naive timestamps.
- **Account statement (PDF).** A one-page rendered statement with the account number, property address, tariff, last reading, and balance laid out positionally. Address punctuation differs from the billing extract (`247 Houtkop Rd` vs `247 Houtkop Road`). No machine-readable metadata — the engine must locate fields by position or pattern.

**Identifier soup is the point.** No single ID is consistent across all four sources. Erf number is the closest to a join key, but it is missing, formatted differently, or wrong in at least one source per property. The engine must reconcile probabilistically — by erf + address + owner name + meter serial, with a confidence score that the dashboard hides from the CFO and shows to the team behind the scenes. *(Repo emphasises the cadastral LPI as the join key — in v0 we substitute erf number because v0 is not pulling from the SG cadastre. Flag this as a departure to revisit in Phase 1 pilot.)*

---

## 2. Designed discrepancies (the answer key)

Each property in the 1–5 set carries at least one designed discrepancy. The complete set across the demo covers all five archetypes from the repo solution doc §03 and the brief.

| Archetype | What is wrong | Where it shows up | Rand impact (illustrative) | Statutory ground |
|---|---|---|---|---|
| **A. Unbilled property** | Property present on valuation roll, no corresponding billing account | Source 2 has the erf; Source 1 does not | Full monthly tariff × annualised | MPRA s49 (general valuation roll must be the basis for rating) |
| **B. Tariff misclassification** | Billed Residential; valued and used as Business | Source 1 tariff = Residential; Source 2 category = Business; Source 3 consumption pattern matches Business | Δ(business − residential) × monthly volume | MPRA s49 (rates must match category); MSA s78 (service-delivery decisions must follow proper categorisation) |
| **C. Meter-vs-billed mismatch** | Meter log shows ~3× the consumption billed; or estimate has run >6 months | Source 3 vs Source 1 | Δ(actual kℓ − billed kℓ) × tariff | Water Services Act + MSA §95 (estimates capped at 6 months) — *flagged for legal review of exact section* |
| **D. Stale property value / wrong owner** | Billing uses a R430k valuation; current roll says R1.18M | Source 1 vs Source 2 | Δ(current value − stale value) × rate-in-rand | MPRA s49 (current general valuation roll governs) |
| **E. Duplicate or orphaned account** | Two billing accounts pointing at one erf, or an account with no matching property | Source 1 internal; cross-check against Source 2 | Revenue-neutral; flagged as audit + debtor-book fix | MSA — accurate debtor records under credit control policy |

**How the 1–5 properties carry the archetypes.** A minimum viable demo set is three properties, one of them being the worked example. A fuller set spreads the archetypes:

- **Property 1 (the worked example, see §8):** carries B + D simultaneously — tariff misclassification *and* stale valuation. One property, two leaks, biggest single rand impact in the demo.
- **Property 2:** carries A — sits on the valuation roll but has no billing account at all. The "pure lost revenue" headline number.
- **Property 3:** carries C — a meter log showing sustained high consumption against a billed estimate that has run >6 months. The "statutory cap breach" flag.
- **Property 4 (optional):** carries E — duplicate account from a historical sub-division that was never cleaned up.
- **Property 5 (optional):** carries no discrepancy — included so the dashboard can show a clean property, which is what makes the flagged ones credible.

---

## 3. Reconciliation routine

A single batch run, end to end, idempotent. No streaming, no incremental state. The CFO presses one button and waits ~2 seconds.

1. **Ingest.** Each source is loaded by a format-specific adapter:
   - CSV adapters normalise whitespace, case, date formats, and column aliases.
   - PDF-table adapter extracts the valuation roll using positional table detection.
   - Scanned-statement adapter extracts the five required fields by anchor patterns.
   Adapters output a uniform internal record format: `{source, raw_row, property_candidate, owner_candidate, account_candidate, meter_candidate, confidence}`.

2. **Match records into properties.** Group records across sources by an iterative match: erf number (when present and well-formed) → fall back to normalised address → fall back to owner name + suburb. Each property gets a synthetic `property_id` for the run. The dashboard never shows this — it shows the human-readable address.

3. **Apply discrepancy rules.** For each property, run five rule checks corresponding to archetypes A–E. Each rule emits zero or more **findings**, each carrying:
   - The archetype label
   - The conflicting values, quoted directly from the source records
   - The rand impact (monthly + annualised)
   - The statutory ground
   - A recommended correction, in one sentence

4. **Aggregate.** Sum monthly under-billing across all findings. Multiply by 12 for the annualised headline. Count properties checked, count properties flagged.

5. **Render.** Findings + aggregates → JSON → dashboard.

**Determinism.** The engine is deterministic for a given input set. No ML, no fuzzy thresholds that drift between runs. Matching is rule-based with explicit fallbacks. This matters because the repo's pitch deck (slide 11) is explicit that tariff calculation, ledger operations, and compliance reporting must never be AI-driven — they must be deterministic and auditable. The same discipline applies to the v0 engine's outputs.

**What the engine does not do in v0.** No write-back to the billing system. No workflow, no clerk queue, no tickets. v0 produces a read-only report. The "light correction workflow" the repo solution doc proposes is Phase 1 pilot territory, not v0. *(Departure from the repo, justified by v0's "demo, not pilot" scope.)*

---

## 4. Dashboard

One web page. Opens cold, no login, no walkthrough. Designed to be projected.

**Top of page — the headline (always visible).**

> **R 47,300 / month** of under-billing detected across **5 properties checked**.
> **R 567,600 / year** of recoverable revenue at current rates.

Big numbers, rands. Nothing else competing for attention above the fold.

**Middle of page — the property list.**

A row per property. Each row shows: address, owner name (from whichever source is most current), number of findings, monthly rand impact. Rows with findings are flagged with a coloured pill labelled by archetype ("Tariff misclassification", "Stale valuation", "Unbilled property"). Clean rows are listed plainly so the CFO can see the engine is not just confirming bias.

**Drill-down — the property detail.**

Clicking any flagged row expands an in-page panel showing:

- A side-by-side comparison of what each source says about this property. Three or four columns, one per source, with the conflicting fields highlighted.
- The findings list. Each finding written as one sentence in plain English: *"This property is billed as Residential. The valuation roll categorises it as Business. The meter log shows commercial-pattern consumption (41 kℓ/month average, vs. 12 kℓ residential norm)."*
- The recommended correction, also one sentence: *"Reclassify account 88231104 from Residential to Business tariff; update the property value from R430,000 to R1,180,000."*
- The statutory basis, plainly: *"Municipal Property Rates Act, section 49 — properties must be rated according to the category on the current general valuation roll."*
- The rand impact for that finding, monthly and annualised.

**What the dashboard never shows.** Match scores, confidence percentages, schema field names, source row IDs, internal property IDs, JSON. The CFO sees rands, properties, and recoverable revenue. Anything more technical lives in the team's internal logs, not the demo surface.

**Polish bar.** This is a demo for a non-technical decision-maker. The dashboard must look intentional — typography sized for projection, table rows readable from across a meeting room, colour used sparingly (one accent colour for flagged findings, no rainbow). No charts unless a chart genuinely communicates something the table cannot.

---

## 5. Proposed repo structure and build sequence

### Proposed layout

```
primeserve/
├── docs/                              # context + plans (this file lives here)
│   ├── RECONCILIATION_V0_PLAN.md      # ← this document
│   └── ANSWER_KEY.md                  # the discrepancy answer key, for QA against engine output
├── emfuleni/
│   └── docs/                          # original source-of-truth context (unchanged)
└── reconciliation-v0/                 # new — all v0 code and assets
    ├── data/
    │   ├── billing_extract.csv
    │   ├── valuation_roll.pdf
    │   ├── meter_log.csv
    │   └── statement_property1.pdf
    ├── engine/
    │   ├── adapters/                  # one per source format
    │   ├── matching.py                # property-grouping logic
    │   ├── rules.py                   # the five archetype checks
    │   └── run.py                     # batch entrypoint
    ├── dashboard/                     # web app — framework TBD (see Open Questions)
    └── tests/
        └── test_answer_key.py         # engine output must match docs/ANSWER_KEY.md
```

*(Note: there is already a `website/` directory at the repo root. The plan assumes it is empty / placeholder and v0 lives under `reconciliation-v0/`. Confirm before building — see Open Questions.)*

### Build sequence

Build in the order findings flow through the system, so each step can be verified end-to-end against the answer key.

1. **Synthetic data generation, by hand.** Author the four files for the 1–5 properties. This is the most important step and the one most likely to be done badly if rushed. The designed discrepancies must be exact, traceable, and match the answer key written into `docs/ANSWER_KEY.md`. Spend disproportionate time here.
2. **Adapters.** One per source format. Test each in isolation against its file. Output normalised records.
3. **Matching.** Group records into properties. Verify by hand that all five demo properties resolve correctly.
4. **Rules.** Implement archetypes A–E. Run against the matched data; compare output to `docs/ANSWER_KEY.md`. Iterate until they match exactly.
5. **JSON output.** Stable, dashboard-shaped JSON. Snapshot-test it.
6. **Dashboard.** Build against the JSON. Polish for projection last — typography, spacing, colour.
7. **Demo dry-run.** Run the engine cold, project the dashboard, time how long it takes to explain. Iterate on language until the dashboard speaks for itself.

Steps 1–5 are sequential. Step 6 can start in parallel with step 5 if the JSON shape is fixed early.

---

## 6. Open questions

The plan can be edited and improved on most of these without blocking — but some will block the build.

**About scope and audience**

1. Which **suburb(s)** should the synthetic properties sit in? Vanderbijlpark is implied by the worked example, but spreading across Vereeniging / Sebokeng / Evaton might read as more representative of Emfuleni's footprint. Tradeoff: more suburbs = more believable; one suburb = tighter story.
2. Should the demo include any **business-only properties** (e.g. a small factory in the Industrial belt), or is residential + mixed-use sufficient? Affects which tariff schedule we synthesise.
3. Is the **CFO the only demo audience**, or do we also pitch to Council / SALGA / CoGTA? Same dashboard, but framing language shifts.

**About the data**

4. Should the v0 use **real public data** (valuation roll PDF from the municipal website, cadastral data from the SG portal) with synthetic billing layered on top — as the repo proposes for the proof-of-concept — or stay fully synthetic? Real-public makes the demo more credible but introduces POPIA risk and data-collection drag.
5. The repo names the **cadastral Land Parcel Identifier (LPI)** as the canonical join key. v0 substitutes erf number because v0 has no SG cadastre source. Is this departure acceptable, or should we add a cadastre source to v0 to preserve the LPI story?
6. Are there **real tariff schedules** (residential, business, indigent) for Emfuleni that we should use, or do we synthesise plausible-but-illustrative ones? Rand impacts on the dashboard are only credible if the tariffs are.

**About statutory grounding**

7. **MPRA s49** is correctly cited for tariff / valuation discrepancies. The **6-month estimate cap** is broadly attributed to MSA / Water Services Act but the exact section needs legal review. Who confirms?
8. Do we cite the **Emfuleni IDP 2026/2027** directly on the dashboard (with page references), or keep statutory grounds at the Act level?

**About the build**

9. **Web framework** for the dashboard — Next.js / SvelteKit / plain HTML+JS? The repo's pitch deck names Python/FastAPI + Node/TS as the production stack but v0 has no production requirement. Choice driven by who is building and how fast it must come together.
10. **Does the existing `website/` directory at the repo root have a planned purpose?** It is currently empty. If it is reserved for a marketing site, v0 stays under `reconciliation-v0/`. If not, the dashboard could live there.
11. **Demo hosting** — is a local laptop projector acceptable, or does v0 need to be on a hosted URL the CFO can revisit after the meeting? Affects whether v0 needs deploy infrastructure at all.

**About what comes after v0**

12. After the demo lands, what is the **gate to start the pilot**? A signed letter of intent? A funded scoping engagement? Clarity here changes how much polish v0 needs.

---

## 7. Worked example — 247 Houtkop Road, Vanderbijlpark

Carried verbatim from the brief; chosen because it stacks two of the five archetypes onto a single property, which is the most demo-effective opening row.

**What each source says about the property:**

| Source | Field | Value |
|---|---|---|
| Billing extract (CSV) | Account number | 88231104 |
| Billing extract (CSV) | Tariff | Residential |
| Billing extract (CSV) | Monthly water charge | R 310 |
| Billing extract (CSV) | Property value (used for rates) | R 430,000 |
| Valuation roll (PDF table) | Erf valuation | R 1,180,000 |
| Valuation roll (PDF table) | Category | Business |
| Meter-reading log (CSV) | Average monthly consumption | 41 kℓ |
| Meter-reading log (CSV) | Consumption pattern | Sustained — consistent with commercial use, not a household |

**What the engine flags:**

- **Finding 1 — Tariff misclassification (archetype B).** Billed Residential; valued and used as Business. Recommended correction: reclassify account 88231104 from Residential to Business. Statutory ground: MPRA s49.
- **Finding 2 — Stale property value (archetype D).** Billing uses R 430,000; current valuation roll states R 1,180,000. Recommended correction: refresh the property value used by the billing system to R 1,180,000. Statutory ground: MPRA s49 (current general valuation roll governs).

**Combined rand impact (illustrative — exact numbers depend on tariff schedule chosen):**

- Estimated under-billing: **≈ R 2,900 / month**
- Annualised: **≈ R 34,800 / year**
- One property. One demonstrable, recurring leak. The headline number on the dashboard is the sum of leaks like this across the demo set.

This is the row the CFO clicks first.

---

## 8. Constraints — recap

- **All data synthetic.** No real ratepayer data in v0. (Possible move to real-public + synthetic-billing at pilot stage — see Open Question 4.)
- **Scope:** 1–5 properties. The minimum useful demo is 3; the maximum is 5. More than 5 dilutes the story.
- **Ingestion model:** v0 reads files from a local directory. Production assumes monthly file extracts over SFTP. The v0 engine is structured so that swapping the file source for an SFTP poller is a one-adapter change.
- **Dashboard:** self-explanatory, no walkthrough required, projection-ready.
- **Out of scope for v0:** production hardening, real-system integration, authentication, scale, write-back to the billing system, clerk correction workflow, resident channels (web, WhatsApp, USSD), payments.
