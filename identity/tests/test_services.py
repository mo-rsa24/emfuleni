"""Tests for identity.services."""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from common.models import Municipality
from identity.models import OtpCode, Ratepayer, RatepayerAccountLink
from identity.services import (
    bind_account,
    find_ratepayer_by_msisdn,
    issue_otp,
    verify_otp,
)
from ingest.models import Extract, MunicipalAccount


def _make_extract(municipality, *, content_hash):
    return Extract.objects.create(
        municipality=municipality,
        filename="extract.csv",
        received_at=timezone.now(),
        content_hash=content_hash,
        row_count=1,
        status=Extract.STATUS_IMPORTED,
    )


def _make_account(municipality, *, account_number, extract, holder_name="Holder Name"):
    return MunicipalAccount.objects.create(
        municipality=municipality,
        account_number=account_number,
        holder_name=holder_name,
        service_address="1 Test Street",
        account_class="residential",
        source_extract=extract,
    )


def _make_ratepayer(municipality, *, full_name="Jane Doe", msisdn=None, id_last4=None):
    return Ratepayer.objects.create(
        municipality=municipality,
        full_name=full_name,
        msisdn=msisdn,
        id_last4=id_last4,
    )


class BindAccountTests(TestCase):
    def setUp(self):
        self.emfuleni = Municipality.objects.create(slug="emfuleni", name="Emfuleni")
        self.sedibeng = Municipality.objects.create(slug="sedibeng", name="Sedibeng")

        self.emfuleni_extract = _make_extract(self.emfuleni, content_hash="a" * 64)
        self.sedibeng_extract = _make_extract(self.sedibeng, content_hash="b" * 64)

        self.emfuleni_account = _make_account(
            self.emfuleni,
            account_number="88231104",
            extract=self.emfuleni_extract,
            holder_name="Emfuleni Holder",
        )

        self.emfuleni_ratepayer = _make_ratepayer(
            self.emfuleni, full_name="Emfuleni Person", msisdn="+27820000001"
        )
        self.sedibeng_ratepayer = _make_ratepayer(
            self.sedibeng, full_name="Sedibeng Person", msisdn="+27820000002"
        )

    def test_creates_link_for_known_account(self):
        link = bind_account(self.emfuleni_ratepayer, "88231104")

        self.assertIsNotNone(link)
        self.assertEqual(link.ratepayer_id, self.emfuleni_ratepayer.pk)
        self.assertEqual(link.municipal_account_id, self.emfuleni_account.pk)
        self.assertEqual(link.municipality_id, self.emfuleni.pk)
        self.assertEqual(RatepayerAccountLink.objects.count(), 1)

    def test_returns_none_for_unknown_account_number(self):
        result = bind_account(self.emfuleni_ratepayer, "99999999")

        self.assertIsNone(result)
        self.assertEqual(RatepayerAccountLink.objects.count(), 0)

    def test_is_idempotent(self):
        first = bind_account(self.emfuleni_ratepayer, "88231104")
        second = bind_account(self.emfuleni_ratepayer, "88231104")

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            RatepayerAccountLink.objects.filter(
                ratepayer=self.emfuleni_ratepayer,
                municipal_account=self.emfuleni_account,
            ).count(),
            1,
        )

    def test_does_not_bind_account_from_other_tenant(self):
        # Account "77777777" exists only under Emfuleni. The Sedibeng
        # ratepayer must not be able to bind it — the lookup is scoped
        # to ratepayer.municipality.
        _make_account(
            self.emfuleni,
            account_number="77777777",
            extract=self.emfuleni_extract,
        )

        result = bind_account(self.sedibeng_ratepayer, "77777777")

        self.assertIsNone(result)
        self.assertEqual(RatepayerAccountLink.objects.count(), 0)


class FindRatepayerByMsisdnTests(TestCase):
    def setUp(self):
        self.emfuleni = Municipality.objects.create(slug="emfuleni", name="Emfuleni")
        self.sedibeng = Municipality.objects.create(slug="sedibeng", name="Sedibeng")

        self.emfuleni_ratepayer = _make_ratepayer(
            self.emfuleni, full_name="Emfuleni Person", msisdn="+27820000001"
        )

    def test_returns_ratepayer_for_known_msisdn(self):
        result = find_ratepayer_by_msisdn(self.emfuleni, "+27820000001")

        self.assertIsNotNone(result)
        self.assertEqual(result.pk, self.emfuleni_ratepayer.pk)
        self.assertEqual(result.full_name, "Emfuleni Person")

    def test_returns_none_for_unknown_msisdn(self):
        result = find_ratepayer_by_msisdn(self.emfuleni, "+27829999999")

        self.assertIsNone(result)

    def test_does_not_leak_ratepayer_across_tenants(self):
        # Same MSISDN string exists under both tenants — the unique
        # constraint is per-(municipality, msisdn), so this is allowed.
        # Each tenant lookup must return its own ratepayer.
        shared_msisdn = "+27820001234"

        emfuleni_rp = _make_ratepayer(
            self.emfuleni, full_name="Emfuleni Shared", msisdn=shared_msisdn
        )
        sedibeng_rp = _make_ratepayer(
            self.sedibeng, full_name="Sedibeng Shared", msisdn=shared_msisdn
        )

        emfuleni_result = find_ratepayer_by_msisdn(self.emfuleni, shared_msisdn)
        sedibeng_result = find_ratepayer_by_msisdn(self.sedibeng, shared_msisdn)

        self.assertIsNotNone(emfuleni_result)
        self.assertIsNotNone(sedibeng_result)
        self.assertEqual(emfuleni_result.pk, emfuleni_rp.pk)
        self.assertEqual(sedibeng_result.pk, sedibeng_rp.pk)
        self.assertNotEqual(emfuleni_result.pk, sedibeng_result.pk)


class IssueOtpTests(TestCase):
    def setUp(self):
        self.emfuleni = Municipality.objects.create(slug="emfuleni", name="Emfuleni")
        self.ratepayer = _make_ratepayer(
            self.emfuleni, full_name="Issue Person", msisdn="+27820000010"
        )

    def test_issue_otp_creates_six_digit_code(self):
        otp = issue_otp(self.ratepayer)

        self.assertIsNotNone(otp.pk)
        self.assertTrue(OtpCode.objects.filter(pk=otp.pk).exists())
        self.assertEqual(len(otp.code), 6)
        self.assertTrue(otp.code.isdigit())

    def test_issue_otp_sets_expiry_five_minutes_out(self):
        before = timezone.now()
        otp = issue_otp(self.ratepayer)
        after = timezone.now()

        # expires_at should be ~5 minutes after issue. Compare against the
        # window [before, after] + 5min.
        expected_lower = before + timedelta(minutes=5)
        expected_upper = after + timedelta(minutes=5)
        # assertAlmostEqual on seconds difference vs midpoint.
        midpoint = before + (after - before) / 2 + timedelta(minutes=5)
        diff_seconds = (otp.expires_at - midpoint).total_seconds()
        self.assertAlmostEqual(diff_seconds, 0, delta=5)
        self.assertGreaterEqual(otp.expires_at, expected_lower)
        self.assertLessEqual(otp.expires_at, expected_upper)

    def test_issue_otp_inherits_tenant_from_ratepayer(self):
        otp = issue_otp(self.ratepayer)

        self.assertEqual(otp.municipality_id, self.ratepayer.municipality_id)

    def test_issue_otp_two_calls_create_two_rows(self):
        first = issue_otp(self.ratepayer)
        second = issue_otp(self.ratepayer)

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(
            OtpCode.objects.filter(ratepayer=self.ratepayer).count(), 2
        )
        first.refresh_from_db()
        self.assertIsNone(first.consumed_at)


class VerifyOtpTests(TestCase):
    def setUp(self):
        self.emfuleni = Municipality.objects.create(slug="emfuleni", name="Emfuleni")
        self.ratepayer = _make_ratepayer(
            self.emfuleni, full_name="Verify Person", msisdn="+27820000020"
        )

    def test_verify_otp_consumes_matching_live_code(self):
        otp = issue_otp(self.ratepayer)

        result = verify_otp(self.ratepayer, otp.code)

        self.assertTrue(result)
        otp.refresh_from_db()
        self.assertIsNotNone(otp.consumed_at)

    def test_verify_otp_rejects_wrong_code(self):
        # Force a deterministic code so we can build a known-wrong one.
        with patch("identity.services.secrets.choice", return_value="1"):
            otp = issue_otp(self.ratepayer)
        # Stored code is "111111"; submit something different.
        self.assertEqual(otp.code, "111111")

        result = verify_otp(self.ratepayer, "222222")

        self.assertFalse(result)
        otp.refresh_from_db()
        self.assertIsNone(otp.consumed_at)

    def test_verify_otp_rejects_consumed_code(self):
        otp = issue_otp(self.ratepayer)

        first = verify_otp(self.ratepayer, otp.code)
        second = verify_otp(self.ratepayer, otp.code)

        self.assertTrue(first)
        self.assertFalse(second)

    def test_verify_otp_rejects_expired_code(self):
        otp = issue_otp(self.ratepayer)
        otp.expires_at = timezone.now() - timedelta(minutes=1)
        otp.save(update_fields=["expires_at"])

        result = verify_otp(self.ratepayer, otp.code)

        self.assertFalse(result)
        otp.refresh_from_db()
        self.assertIsNone(otp.consumed_at)

    def test_verify_otp_strips_whitespace(self):
        with patch("identity.services.secrets.choice", side_effect=list("123456")):
            otp = issue_otp(self.ratepayer)
        self.assertEqual(otp.code, "123456")

        result = verify_otp(self.ratepayer, " 123456 ")

        self.assertTrue(result)

    def test_verify_otp_does_not_match_other_ratepayers_code(self):
        other_ratepayer = _make_ratepayer(
            self.emfuleni, full_name="Other Person", msisdn="+27820000021"
        )
        otp_a = issue_otp(self.ratepayer)

        result = verify_otp(other_ratepayer, otp_a.code)

        self.assertFalse(result)
        otp_a.refresh_from_db()
        self.assertIsNone(otp_a.consumed_at)
