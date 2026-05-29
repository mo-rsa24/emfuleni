"""Tests for engine.services (Slice 7 — reconciliation assembler).

Cover the canonical assembler `build_reconciliation(account, period)` and
the single archetype that ships with Slice 7 (`estimate_cap_breach`).

The VLM provider is never invoked here. Photo Evidence rows are built
through `portal.services.record_evidence` (so on-disk files live where
production expects them), and the auto-trigger client is patched out;
each test then writes its own `VlmExtraction` row directly so the test
controls the reading_kl / confidence / status without going through the
network or the Anthropic SDK.
"""

from __future__ import annotations

import datetime as dt
import tempfile
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from common.models import Municipality
from engine import services as engine_services
from engine.models import Finding
from engine.services import (
    ESTIMATE_BREACH_MIN_KL_DELTA,
    RESIDENTIAL_WATER_FREE_KL,
    RESIDENTIAL_WATER_RATE_PER_KL,
    build_reconciliation,
    get_findings,
    residential_water_charge,
)
from identity.models import Ratepayer, RatepayerAccountLink
from ingest.models import Extract, MeterReading, MunicipalAccount, MunicipalBill
from portal.models import Evidence
from portal.services import record_evidence
from vlm.models import VlmExtraction


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _png_bytes(n: int = 64) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + (b"\x00" * n)


def _make_extract(municipality, *, content_hash):
    return Extract.objects.create(
        municipality=municipality,
        filename="extract.csv",
        received_at=timezone.now(),
        content_hash=content_hash,
        row_count=1,
        status=Extract.STATUS_IMPORTED,
    )


def _make_account(municipality, *, account_number, extract, holder_name="Holder"):
    return MunicipalAccount.objects.create(
        municipality=municipality,
        account_number=account_number,
        holder_name=holder_name,
        service_address="1 Test Street",
        account_class="residential",
        source_extract=extract,
    )


def _make_ratepayer(municipality, *, full_name="Jane Doe", msisdn=None):
    return Ratepayer.objects.create(
        municipality=municipality,
        full_name=full_name,
        msisdn=msisdn,
    )


def _make_bill(
    municipality,
    account,
    extract,
    *,
    period,
    water_kl,
    opening_balance=Decimal("0.00"),
    closing_balance=None,
):
    if closing_balance is None:
        # Use the residential tariff so municipal_balance = what the
        # municipality "charged" for that kl block at the same rate the
        # engine uses to re-price.
        closing_balance = residential_water_charge(water_kl)
    return MunicipalBill.objects.create(
        municipality=municipality,
        municipal_account=account,
        period=period,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        charges={"water_kl": water_kl},
        source_extract=extract,
    )


def _make_meter_reading(municipality, account, extract, *, reading_date, reading_kl):
    return MeterReading.objects.create(
        municipality=municipality,
        municipal_account=account,
        reading_date=reading_date,
        reading_kl=reading_kl,
        source_extract=extract,
    )


def _record_photo_evidence(ratepayer, account, *, filename="meter.png", created_at=None):
    """Use the real portal service so the on-disk path is production-shaped.

    The VLM auto-trigger is patched out; the test then writes its own
    VlmExtraction directly with the values it needs. If `created_at` is
    provided, the Evidence row's `created_at` is updated post-insert
    (bypassing `auto_now_add`) so the row falls inside the desired
    billing period.
    """
    upload = SimpleUploadedFile(filename, _png_bytes(64), content_type="image/png")
    fake_client = MagicMock()
    fake_client.messages.create.return_value = MagicMock(content=[])
    with patch("vlm.services._default_client", return_value=fake_client):
        evidence = record_evidence(
            ratepayer=ratepayer,
            account=account,
            kind="photo",
            uploaded_file=upload,
        )
    # The auto-trigger left a (likely failed) extraction row behind. Wipe so
    # each test is in charge of its own VlmExtraction shape.
    VlmExtraction.objects.filter(evidence=evidence).delete()
    if created_at is not None:
        Evidence.objects.filter(pk=evidence.pk).update(created_at=created_at)
        evidence.refresh_from_db()
    return evidence


def _make_extraction(
    evidence,
    *,
    reading_kl,
    confidence,
    status=VlmExtraction.STATUS_EXTRACTED,
):
    return VlmExtraction.objects.create(
        municipality=evidence.municipality,
        evidence=evidence,
        status=status,
        reading_kl=reading_kl,
        confidence=confidence,
        model_name="test-model",
        raw_response={"stub": True},
        notes="",
    )


def _build_scenario(
    *,
    slug="emfuleni",
    name="Emfuleni",
    account_number="88231104",
    msisdn="+27820000001",
    full_name="Jane Doe",
    period=dt.date(2026, 3, 1),
    bill_water_kl=18,
    bill_opening_balance=Decimal("0.00"),
    bill_closing_balance=None,
    prior_reading_kl=4119,
    prior_reading_date=dt.date(2026, 2, 1),
    include_photo=True,
    photo_filename="meter.png",
    photo_reading_kl=4127,
    photo_confidence=0.94,
    photo_status=VlmExtraction.STATUS_EXTRACTED,
    include_prior_reading=True,
    include_bill=True,
):
    """Wire a full reconciliation fixture under one tenant.

    Defaults reproduce the design-note worked-example shape: municipality
    says 18 kl, prior reading 4119, photo reads 4127 → inferred 8 kl, a
    10 kl overcharge, well above the 2 kl threshold.
    """
    municipality = Municipality.objects.create(slug=slug, name=name)
    content_hash = (slug + "x" * 64)[:64]
    extract = _make_extract(municipality, content_hash=content_hash)
    account = _make_account(
        municipality,
        account_number=account_number,
        extract=extract,
        holder_name=full_name,
    )
    ratepayer = _make_ratepayer(municipality, full_name=full_name, msisdn=msisdn)
    RatepayerAccountLink.objects.create(
        municipality=municipality,
        ratepayer=ratepayer,
        municipal_account=account,
    )

    bill = None
    if include_bill:
        bill = _make_bill(
            municipality,
            account,
            extract,
            period=period,
            water_kl=bill_water_kl,
            opening_balance=bill_opening_balance,
            closing_balance=bill_closing_balance,
        )

    if include_prior_reading:
        _make_meter_reading(
            municipality,
            account,
            extract,
            reading_date=prior_reading_date,
            reading_kl=prior_reading_kl,
        )

    evidence = None
    extraction = None
    if include_photo:
        # Anchor the evidence inside the billing period — the engine
        # filters by created_at.year/month matching the period.
        in_period = timezone.make_aware(
            dt.datetime(period.year, period.month, 15, 12, 0)
        )
        evidence = _record_photo_evidence(
            ratepayer, account, filename=photo_filename, created_at=in_period
        )
        extraction = _make_extraction(
            evidence,
            reading_kl=photo_reading_kl,
            confidence=photo_confidence,
            status=photo_status,
        )

    return {
        "municipality": municipality,
        "extract": extract,
        "account": account,
        "ratepayer": ratepayer,
        "bill": bill,
        "evidence": evidence,
        "extraction": extraction,
        "period": period,
    }


class _TempMediaTestCase(TestCase):
    """Per-test MEDIA_ROOT — every Evidence row writes a real file."""

    def setUp(self):
        self._media_tmp = tempfile.TemporaryDirectory()
        self._override = override_settings(MEDIA_ROOT=self._media_tmp.name)
        self._override.enable()

    def tearDown(self):
        self._override.disable()
        self._media_tmp.cleanup()


# ---------------------------------------------------------------------------
# Tariff helper
# ---------------------------------------------------------------------------


class TariffTests(TestCase):
    def test_residential_water_charge_matches_design_note_example(self):
        # The design-note example is 14 kl at R 130/kl. The constant for
        # the free allowance lives in services.py — compute the expected
        # against whatever it currently is, so the formula is locked even
        # if the free allowance moves later.
        billable = max(0, 14 - RESIDENTIAL_WATER_FREE_KL)
        expected = Decimal(billable) * RESIDENTIAL_WATER_RATE_PER_KL

        result = residential_water_charge(14)

        self.assertEqual(result, expected)
        self.assertIsInstance(result, Decimal)

        # And lock the non-zero case explicitly: with the current
        # free-allowance of 6 kl, 14 kl bills 8 kl × R130 = R1040.
        if RESIDENTIAL_WATER_FREE_KL == 6:
            self.assertEqual(result, Decimal("1040.00"))
        elif RESIDENTIAL_WATER_FREE_KL == 0:
            self.assertEqual(result, Decimal("1820.00"))

    def test_residential_water_charge_zero_below_free_allowance(self):
        self.assertEqual(residential_water_charge(0), Decimal("0.00"))
        # Anything inside the free allowance should also be zero.
        if RESIDENTIAL_WATER_FREE_KL > 0:
            self.assertEqual(
                residential_water_charge(RESIDENTIAL_WATER_FREE_KL), Decimal("0.00")
            )


# ---------------------------------------------------------------------------
# Contract shape
# ---------------------------------------------------------------------------


class BuildReconciliationShapeTests(_TempMediaTestCase):
    def test_returns_none_when_no_bill_exists(self):
        scenario = _build_scenario(include_bill=False, include_photo=False)
        account = scenario["account"]
        period = scenario["period"]

        contract = build_reconciliation(account, period)

        self.assertIsNone(contract)
        # And: nothing was persisted.
        self.assertEqual(
            Finding.objects.filter(municipal_account=account, period=period).count(),
            0,
        )

    def test_no_evidence_no_findings_status_awaiting(self):
        scenario = _build_scenario(include_photo=False)
        account = scenario["account"]
        bill = scenario["bill"]
        period = scenario["period"]

        contract = build_reconciliation(account, period)

        self.assertIsNotNone(contract)
        self.assertEqual(contract.status, "awaiting_payment")
        self.assertEqual(contract.findings, [])
        self.assertEqual(contract.evidence, [])
        self.assertEqual(contract.ledger_view.delta, Decimal("0"))
        self.assertEqual(contract.ledger_view.our_balance, bill.closing_balance)

    def test_idempotent_across_calls(self):
        scenario = _build_scenario()
        account = scenario["account"]
        period = scenario["period"]

        c1 = build_reconciliation(account, period)
        c2 = build_reconciliation(account, period)

        self.assertIsNotNone(c1)
        self.assertIsNotNone(c2)
        # Slice 7's contract has no timestamp fields, so this is strict.
        self.assertEqual(c1.model_dump(), c2.model_dump())

    def test_contract_identifiers_match_inputs(self):
        scenario = _build_scenario(
            slug="emfuleni",
            account_number="88231104",
            period=dt.date(2026, 3, 1),
            include_photo=False,
        )
        account = scenario["account"]
        period = scenario["period"]

        contract = build_reconciliation(account, period)

        self.assertEqual(contract.municipality_slug, "emfuleni")
        self.assertEqual(contract.account_number, "88231104")
        self.assertEqual(contract.period, "2026-03")


# ---------------------------------------------------------------------------
# Estimate cap breach archetype
# ---------------------------------------------------------------------------


class EstimateCapBreachRuleTests(_TempMediaTestCase):
    def test_fires_when_photo_consumption_is_lower_than_municipality_charged(self):
        scenario = _build_scenario(
            bill_water_kl=18,
            prior_reading_kl=4119,
            photo_reading_kl=4127,  # → inferred 8 kl, overcharge 10 kl
        )
        account = scenario["account"]
        bill = scenario["bill"]
        period = scenario["period"]
        evidence = scenario["evidence"]

        contract = build_reconciliation(account, period)

        self.assertIsNotNone(contract)
        self.assertEqual(len(contract.findings), 1)
        finding = contract.findings[0]

        self.assertEqual(finding.archetype, "estimate_cap_breach")
        self.assertLess(finding.delta_amount, Decimal("0"))
        self.assertEqual(finding.statutory_ref, "MSA s95")
        self.assertEqual(finding.evidence_id, evidence.pk)

        self.assertEqual(contract.status, "disputed_no_payment")
        self.assertEqual(contract.ledger_view.delta, finding.delta_amount)
        self.assertEqual(
            contract.ledger_view.our_balance,
            bill.closing_balance + finding.delta_amount,
        )

        # And: persisted finding row matches.
        rows = list(
            Finding.objects.filter(municipal_account=account, period=period)
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].archetype, Finding.ARCHETYPE_ESTIMATE_CAP_BREACH)

    def test_does_not_fire_without_prior_municipal_reading(self):
        scenario = _build_scenario(include_prior_reading=False)
        account = scenario["account"]
        period = scenario["period"]

        contract = build_reconciliation(account, period)

        self.assertIsNotNone(contract)
        self.assertEqual(contract.findings, [])
        self.assertEqual(contract.status, "awaiting_payment")
        self.assertEqual(
            Finding.objects.filter(municipal_account=account, period=period).count(),
            0,
        )

    def test_does_not_fire_when_no_extraction_exists(self):
        scenario = _build_scenario()
        # Strip the extraction the fixture made for us.
        VlmExtraction.objects.filter(evidence=scenario["evidence"]).delete()
        account = scenario["account"]
        period = scenario["period"]

        contract = build_reconciliation(account, period)

        self.assertEqual(contract.findings, [])
        self.assertEqual(contract.status, "awaiting_payment")
        # Evidence still appears in the projection (it exists), but the
        # VLM-derived fields are null.
        self.assertEqual(len(contract.evidence), 1)
        self.assertIsNone(contract.evidence[0].vlm_reading_kl)
        self.assertIsNone(contract.evidence[0].vlm_confidence)

    def test_does_not_fire_when_extraction_status_failed(self):
        # Build a scenario WITHOUT a usable extraction, then attach a
        # failed one (reading_kl must be None to satisfy the engine guard).
        scenario = _build_scenario(include_photo=True)
        VlmExtraction.objects.filter(evidence=scenario["evidence"]).delete()
        VlmExtraction.objects.create(
            municipality=scenario["municipality"],
            evidence=scenario["evidence"],
            status=VlmExtraction.STATUS_FAILED,
            reading_kl=None,
            confidence=None,
            model_name="test-model",
            raw_response={"error": "boom"},
            notes="",
        )

        contract = build_reconciliation(scenario["account"], scenario["period"])

        self.assertEqual(contract.findings, [])
        self.assertEqual(contract.status, "awaiting_payment")

    def test_does_not_fire_below_min_kl_delta_threshold(self):
        # Charge 9 kl, infer 8 kl → overcharge 1 kl, below the 2 kl floor.
        scenario = _build_scenario(
            bill_water_kl=9,
            prior_reading_kl=4119,
            photo_reading_kl=4127,  # → inferred 8 kl
        )
        # Sanity: this scenario is only meaningful while the threshold is 2.
        self.assertEqual(ESTIMATE_BREACH_MIN_KL_DELTA, 2)

        contract = build_reconciliation(scenario["account"], scenario["period"])

        self.assertEqual(contract.findings, [])
        self.assertEqual(contract.status, "awaiting_payment")

    def test_chooses_highest_confidence_photo_when_multiple(self):
        scenario = _build_scenario(
            photo_filename="first.png",
            photo_reading_kl=4130,
            photo_confidence=0.55,
        )
        # Add a second photo on the same account in the same period with
        # higher confidence; that one should win.
        period = scenario["period"]
        in_period = timezone.make_aware(
            dt.datetime(period.year, period.month, 20, 12, 0)
        )
        second_evidence = _record_photo_evidence(
            scenario["ratepayer"],
            scenario["account"],
            filename="second.png",
            created_at=in_period,
        )
        _make_extraction(
            second_evidence,
            reading_kl=4127,
            confidence=0.92,
        )

        contract = build_reconciliation(scenario["account"], scenario["period"])

        self.assertEqual(len(contract.findings), 1)
        self.assertEqual(contract.findings[0].evidence_id, second_evidence.pk)

    def test_writes_finding_idempotently(self):
        scenario = _build_scenario()
        account = scenario["account"]
        period = scenario["period"]

        build_reconciliation(account, period)
        build_reconciliation(account, period)

        count = Finding.objects.filter(
            municipal_account=account, period=period
        ).count()
        self.assertEqual(count, 1)

    def test_explanation_mentions_kl_and_tariff(self):
        scenario = _build_scenario(
            bill_water_kl=18,
            prior_reading_kl=4119,
            photo_reading_kl=4127,
        )

        contract = build_reconciliation(scenario["account"], scenario["period"])

        self.assertEqual(len(contract.findings), 1)
        text = contract.findings[0].explanation
        self.assertIn("8 kl", text)  # inferred consumption
        self.assertIn("18 kl", text)  # municipality-charged
        # Statutory ref is surfaced — engine spells it out as
        # "Section 95 of the Municipal Systems Act" in prose; the
        # finding row carries "MSA s95" as its `statutory_ref`.
        self.assertIn("Section 95", text)


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------


class TenancyTests(_TempMediaTestCase):
    def test_findings_are_tenant_scoped(self):
        a = _build_scenario(
            slug="emfuleni",
            name="Emfuleni",
            account_number="88231104",
            msisdn="+27820000001",
        )
        b = _build_scenario(
            slug="sedibeng",
            name="Sedibeng",
            account_number="55512345",
            msisdn="+27820000088",
            full_name="Sedi Person",
            photo_filename="b.png",
        )

        build_reconciliation(a["account"], a["period"])
        build_reconciliation(b["account"], b["period"])

        a_findings = get_findings(a["account"], a["period"])
        b_findings = get_findings(b["account"], b["period"])

        self.assertEqual(len(a_findings), 1)
        self.assertEqual(len(b_findings), 1)
        self.assertEqual(a_findings[0].municipality_id, a["municipality"].pk)
        self.assertEqual(b_findings[0].municipality_id, b["municipality"].pk)
        self.assertNotEqual(a_findings[0].pk, b_findings[0].pk)

        # Manager-level scoping: only one row per tenant.
        self.assertEqual(
            Finding.objects.for_tenant(a["municipality"]).count(), 1
        )
        self.assertEqual(
            Finding.objects.for_tenant(b["municipality"]).count(), 1
        )
        # No bleed: tenant A's manager view never sees B's finding.
        a_ids = set(
            Finding.objects.for_tenant(a["municipality"]).values_list("pk", flat=True)
        )
        b_ids = set(
            Finding.objects.for_tenant(b["municipality"]).values_list("pk", flat=True)
        )
        self.assertTrue(a_ids.isdisjoint(b_ids))

    def test_evidence_in_other_tenants_account_not_referenced(self):
        a = _build_scenario(
            slug="emfuleni",
            account_number="88231104",
            msisdn="+27820000001",
        )
        b = _build_scenario(
            slug="sedibeng",
            name="Sedibeng",
            account_number="55512345",
            msisdn="+27820000088",
            full_name="Sedi Person",
            photo_filename="b.png",
        )

        contract_a = build_reconciliation(a["account"], a["period"])
        contract_b = build_reconciliation(b["account"], b["period"])

        a_evidence_ids = {e.evidence_id for e in contract_a.evidence}
        b_evidence_ids = {e.evidence_id for e in contract_b.evidence}

        self.assertEqual(a_evidence_ids, {a["evidence"].pk})
        self.assertEqual(b_evidence_ids, {b["evidence"].pk})
        self.assertTrue(a_evidence_ids.isdisjoint(b_evidence_ids))


# ---------------------------------------------------------------------------
# Period matching
# ---------------------------------------------------------------------------


class PeriodMatchingTests(_TempMediaTestCase):
    def test_only_evidence_in_period_is_included(self):
        scenario = _build_scenario(
            period=dt.date(2026, 3, 1),
            photo_filename="in_period.png",
        )
        account = scenario["account"]
        ratepayer = scenario["ratepayer"]
        in_period_ev = scenario["evidence"]

        # A second photo whose created_at sits outside the period.
        out_ev = _record_photo_evidence(
            ratepayer, account, filename="out_of_period.png"
        )
        Evidence.objects.filter(pk=out_ev.pk).update(
            created_at=timezone.make_aware(dt.datetime(2026, 1, 15, 12, 0))
        )

        contract = build_reconciliation(account, scenario["period"])

        included_ids = {e.evidence_id for e in contract.evidence}
        self.assertIn(in_period_ev.pk, included_ids)
        self.assertNotIn(out_ev.pk, included_ids)
