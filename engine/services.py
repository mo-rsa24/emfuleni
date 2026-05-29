"""Service-layer functions for the engine app.

Other apps MUST call into this module — never import models from
this app directly. See CLAUDE.md hard rules.

The central function is `build_reconciliation(account, period)` — the
single canonical assembler mandated by the reconciliation-contract
skill. Every consumer calls this. The contract is a **projection**
computed from the relational rows; it is NOT a model.

Notes:
  - `ReconciledPosition` (in `ledger/models.py`) is shipped this slice as
    a future-cache for the our_balance + delta top-line, but Slice 7 does
    NOT write to it. The contract is recomputed end-to-end on every call.
    The cache write lands in Slice 8 when payment paths need fast reads.
  - engine is a **leaf**: it imports `ingest.services`, `portal.services`,
    `vlm.services` at module level. Nothing imports back into engine.
    Keep it that way — restructure rather than fall back to inline imports.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from django.db import transaction
from pydantic import BaseModel, ConfigDict, Field

from ingest import services as ingest_services
from portal import services as portal_services
from vlm import services as vlm_services

from .models import Finding


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tariff (MVP — hardcoded per design-note worked example)
# ---------------------------------------------------------------------------

# Residential water tariff, kept here for now.
# TODO(slice-8+): replace with a per-tenant TariffService (Emfuleni's real
# tariff is stepped; this flat-above-free-allowance shape is an MVP cheat).
# Numbers loosely match the design-note §2.5 example: 14 kl × R 130 = R 1,820
# (the example uses no free allowance; this file's FREE_KL=6 reflects what a
# more typical municipal tariff would do).
RESIDENTIAL_WATER_FREE_KL = 6
RESIDENTIAL_WATER_RATE_PER_KL = Decimal("130.00")

# How big a kl discrepancy between municipality-charged and meter-evidenced
# is "material" enough to flag an estimate_cap_breach. Below this we let it
# slide — meters disagree at the edges, and disputing a 1 kl difference is
# noise.
ESTIMATE_BREACH_MIN_KL_DELTA = 2


def residential_water_charge(kl_consumed: int) -> Decimal:
    """Compute the water portion of a bill from a kl consumption number.

    First `RESIDENTIAL_WATER_FREE_KL` are free; everything above that is
    charged at `RESIDENTIAL_WATER_RATE_PER_KL`. Returns a `Decimal` (so
    the contract never carries float-precision artefacts).
    """
    billable = max(0, int(kl_consumed) - RESIDENTIAL_WATER_FREE_KL)
    return Decimal(billable) * RESIDENTIAL_WATER_RATE_PER_KL


# ---------------------------------------------------------------------------
# The reconciliation contract — Pydantic schema matching design-note §5
# ---------------------------------------------------------------------------


class MunicipalClaim(BaseModel):
    """What the municipality says, read from `MunicipalBill`."""

    opening_balance: Decimal
    closing_balance: Decimal
    water_kl: int
    period_label: str


class EvidenceRef(BaseModel):
    """A reference to one piece of evidence (with VLM output if it's a photo)."""

    evidence_id: int
    kind: str
    original_filename: str
    vlm_reading_kl: Optional[int] = None
    vlm_confidence: Optional[float] = None


class FindingRef(BaseModel):
    """One archetype-tagged conclusion produced by the engine."""

    archetype: str
    delta_amount: Decimal
    statutory_ref: str
    evidence_id: Optional[int] = None
    explanation: str


class LedgerView(BaseModel):
    """What we (Primeserve) say the ratepayer owes after applying findings."""

    our_balance: Decimal
    delta: Decimal  # our_balance - municipality_balance


class ReconciliationContract(BaseModel):
    """The §5 projection. Computed on read; never persisted as a row.

    Slice 7 covers identifiers, municipal_claim, evidence, findings, and
    ledger_view. `dispute_file` (Slice 9) and `payment_instruction`
    (Slice 8) are added as those slices land.
    """

    model_config = ConfigDict(frozen=True)

    # identifiers
    municipality_slug: str
    account_number: str
    period: str

    # the four shipped sub-records
    municipal_claim: MunicipalClaim
    evidence: list[EvidenceRef]
    findings: list[FindingRef]
    ledger_view: LedgerView

    # transient state — derived from findings, not stored
    status: str  # "awaiting_payment" | "disputed_no_payment"


# ---------------------------------------------------------------------------
# The canonical assembler
# ---------------------------------------------------------------------------


def build_reconciliation(account, period: date) -> ReconciliationContract | None:
    """Assemble the reconciliation contract for one (account, period).

    Reads from relational rows in `ingest`, `portal`, `vlm`, and our own
    `engine` `Finding` table. Returns a `ReconciliationContract` Pydantic
    instance. Returns `None` if the municipality has no bill for that
    period — there is nothing to reconcile.

    This is the canonical assembler per the reconciliation-contract
    skill. No other code re-assembles the contract.
    """
    bill = ingest_services.get_bill(account, period)
    if bill is None:
        return None

    municipal_claim = _municipal_claim_from_bill(bill)

    evidence_rows = portal_services.list_evidence_for_account(account)
    period_evidence = [
        ev for ev in evidence_rows if _belongs_to_period(ev, period)
    ]
    evidence_refs = [_evidence_ref(ev) for ev in period_evidence]

    # Persist findings as a side-effect — the engine OWNS Finding rows,
    # they are not part of the contract projection itself, but the
    # contract references them. Idempotent: re-running clears and rewrites
    # findings for this (account, period).
    findings = _compute_findings(account, period, bill, period_evidence)
    finding_refs = [_finding_ref(f) for f in findings]

    delta = sum((f.delta_amount for f in findings), Decimal("0"))
    our_balance = bill.closing_balance + delta
    ledger_view = LedgerView(our_balance=our_balance, delta=delta)

    status = (
        "disputed_no_payment" if delta != Decimal("0") else "awaiting_payment"
    )

    return ReconciliationContract(
        municipality_slug=account.municipality.slug,
        account_number=account.account_number,
        period=bill.period_label,
        municipal_claim=municipal_claim,
        evidence=evidence_refs,
        findings=finding_refs,
        ledger_view=ledger_view,
        status=status,
    )


def get_findings(account, period: date) -> list[Finding]:
    """Tenant-scoped read for other apps (corrections, portal, tests)."""
    return list(
        Finding.objects.for_tenant(account.municipality)
        .filter(municipal_account=account, period=period)
        .order_by("created_at")
    )


# ---------------------------------------------------------------------------
# Internals — finding production
# ---------------------------------------------------------------------------


@dataclass
class _MeterEvidence:
    """One piece of evidence interesting to the engine."""

    evidence_id: int
    reading_kl: int
    is_photo: bool


def _compute_findings(account, period, bill, period_evidence) -> list[Finding]:
    """Run every archetype rule, persist findings, return the list.

    Idempotent: deletes any existing findings for this (account, period)
    before rewriting. The contract is a projection — Finding rows are
    the source of truth, and re-running the engine is supposed to
    reproduce them.

    Wrapped in a single transaction so a partial failure during rule
    execution does NOT leave the table half-wiped; either every rule
    persists or the pre-call state is preserved. Also closes the
    visibility window where a portal reader could see an empty result
    between the wipe and the rewrite (under READ COMMITTED or stricter).
    """
    with transaction.atomic():
        # Wipe prior findings — re-running the engine must be deterministic.
        # Goes through for_tenant to satisfy the tenancy rule on the read.
        Finding.objects.for_tenant(account.municipality).filter(
            municipal_account=account,
            period=period,
        ).delete()

        findings: list[Finding] = []
        findings.extend(_archetype_estimate_cap_breach(account, period, bill, period_evidence))
        # Future archetypes append here.
    return findings


def _archetype_estimate_cap_breach(account, period, bill, period_evidence) -> list[Finding]:
    """Detect when the municipality's charged kl materially exceeds the
    consumption implied by ratepayer-supplied photo evidence.

    The corroboration rule (design note §6 Q3): a photo-anchored finding
    requires ≥1 other source. Here we treat the prior month's *municipal*
    meter reading as the corroborator — we use it as the baseline to
    compute consumption from the photo's current reading. If no prior
    municipal reading exists, the rule cannot fire and we surface no
    finding (rather than fabricate one from a single source).
    """
    # MVP: residential tariff only. Business accounts get a noop until
    # Slice 8+ adds business_water_charge() and per-tenant tariff lookup.
    if account.account_class != "residential":
        return []

    photo_evidence = [ev for ev in period_evidence if _has_photo_extraction(ev)]
    if not photo_evidence:
        return []

    prior_reading = _latest_municipal_reading_before(account, period)
    if prior_reading is None:
        # Single source (the photo) — cannot satisfy Q3. Hold.
        return []

    # Pick the photo with the highest-confidence extraction. Secondary key:
    # most recent uploaded wins on confidence ties so the choice is
    # deterministic regardless of the queryset's natural ordering.
    chosen = max(
        photo_evidence,
        key=lambda ev: (
            vlm_services.get_extraction_for_evidence(ev).confidence or 0,
            ev.created_at,
        ),
    )
    extraction = vlm_services.get_extraction_for_evidence(chosen)
    inferred_consumption_kl = max(0, extraction.reading_kl - prior_reading.reading_kl)

    municipality_charged_kl = int(bill.charges.get("water_kl", 0) or 0)
    overcharge_kl = municipality_charged_kl - inferred_consumption_kl

    if overcharge_kl < ESTIMATE_BREACH_MIN_KL_DELTA:
        return []

    # Re-price using the corrected consumption number.
    municipality_water_charge = residential_water_charge(municipality_charged_kl)
    corrected_water_charge = residential_water_charge(inferred_consumption_kl)
    delta_amount = corrected_water_charge - municipality_water_charge  # negative

    explanation = (
        f"Your meter shows {inferred_consumption_kl} kl used this period, "
        f"not the {municipality_charged_kl} kl the municipality charged for. "
        f"At the residential tariff (R {RESIDENTIAL_WATER_RATE_PER_KL}/kl above "
        f"{RESIDENTIAL_WATER_FREE_KL} kl free) this is "
        f"R {corrected_water_charge}, a R {abs(delta_amount)} reduction. "
        f"Section 95 of the Municipal Systems Act caps estimates at 6 months."
    )

    finding = Finding.objects.create(
        municipality=account.municipality,
        municipal_account=account,
        period=period,
        archetype=Finding.ARCHETYPE_ESTIMATE_CAP_BREACH,
        delta_amount=delta_amount,
        statutory_ref="MSA s95",
        evidence=chosen,
        explanation=explanation,
    )
    return [finding]


# ---------------------------------------------------------------------------
# Contract sub-builders
# ---------------------------------------------------------------------------


def _municipal_claim_from_bill(bill) -> MunicipalClaim:
    return MunicipalClaim(
        opening_balance=bill.opening_balance,
        closing_balance=bill.closing_balance,
        water_kl=int(bill.charges.get("water_kl", 0) or 0),
        period_label=bill.period_label,
    )


def _evidence_ref(ev) -> EvidenceRef:
    extraction = vlm_services.get_extraction_for_evidence(ev) if ev.kind == "photo" else None
    return EvidenceRef(
        evidence_id=ev.pk,
        kind=ev.kind,
        original_filename=ev.original_filename,
        vlm_reading_kl=extraction.reading_kl if extraction else None,
        vlm_confidence=extraction.confidence if extraction else None,
    )


def _finding_ref(f: Finding) -> FindingRef:
    return FindingRef(
        archetype=f.archetype,
        delta_amount=f.delta_amount,
        statutory_ref=f.statutory_ref,
        evidence_id=f.evidence_id,
        explanation=f.explanation,
    )


# ---------------------------------------------------------------------------
# Tenant-safe cross-app reads (used by the assembler, not exposed)
# ---------------------------------------------------------------------------


def _belongs_to_period(ev, period: date) -> bool:
    """Does this Evidence row belong to the same billing period?

    MVP rule: created_at year+month matches the period's year+month.
    Future enhancement: an explicit `period` field on Evidence so
    backdated uploads can be associated with prior periods.
    """
    created = ev.created_at.date()
    return created.year == period.year and created.month == period.month


def _has_photo_extraction(ev) -> bool:
    """Photo with a usable extraction (extracted OR low_confidence, not failed).

    Goes through vlm.services rather than the reverse OneToOne accessor
    so the rule does not depend on the related_name spelling — a
    boundary-cleaner read pattern.
    """
    if ev.kind != "photo":
        return False
    extraction = vlm_services.get_extraction_for_evidence(ev)
    if extraction is None or extraction.reading_kl is None:
        return False
    return extraction.status in ("extracted", "low_confidence")


def _latest_municipal_reading_before(account, period: date):
    """Most recent `MeterReading` from `ingest` strictly before this period.

    Goes through `ingest.services` rather than touching `ingest.models`
    directly. Returns None if there is no prior reading on file.
    """
    return ingest_services.latest_meter_reading_before(account, period)
