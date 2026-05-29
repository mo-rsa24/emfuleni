"""Tests for identity.services OTP functions (issue_otp, verify_otp)."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from common.models import Municipality
from identity.models import OtpCode, Ratepayer
from identity.services import OTP_CODE_DIGITS, issue_otp, verify_otp


def _make_ratepayer(municipality, *, full_name="Jane Doe", msisdn=None, id_last4=None):
    return Ratepayer.objects.create(
        municipality=municipality,
        full_name=full_name,
        msisdn=msisdn,
        id_last4=id_last4,
    )


class IssueOtpTests(TestCase):
    def setUp(self):
        self.emfuleni = Municipality.objects.create(slug="emfuleni", name="Emfuleni")
        self.ratepayer = _make_ratepayer(
            self.emfuleni, full_name="Issuer Person", msisdn="+27820000001"
        )

    def test_issue_otp_creates_unconsumed_row_with_future_expiry(self):
        before = timezone.now()
        otp = issue_otp(self.ratepayer)
        after = timezone.now()

        self.assertIsInstance(otp, OtpCode)
        self.assertEqual(otp.ratepayer_id, self.ratepayer.pk)
        self.assertEqual(otp.municipality_id, self.emfuleni.pk)
        self.assertIsNone(otp.consumed_at)
        # Code is the configured number of digits, all digits.
        self.assertEqual(len(otp.code), OTP_CODE_DIGITS)
        self.assertTrue(otp.code.isdigit())
        # Expires roughly 5 minutes in the future. Allow a small window
        # for clock drift / test overhead.
        self.assertGreater(otp.expires_at, before + timedelta(minutes=4, seconds=50))
        self.assertLess(otp.expires_at, after + timedelta(minutes=5, seconds=10))
        # Reloaded row matches.
        self.assertEqual(OtpCode.objects.count(), 1)

    def test_issue_otp_returns_different_code_each_call(self):
        first = issue_otp(self.ratepayer)
        second = issue_otp(self.ratepayer)

        self.assertNotEqual(first.pk, second.pk)
        self.assertNotEqual(first.code, second.code)


class VerifyOtpTests(TestCase):
    def setUp(self):
        self.emfuleni = Municipality.objects.create(slug="emfuleni", name="Emfuleni")
        self.sedibeng = Municipality.objects.create(slug="sedibeng", name="Sedibeng")

        self.emfuleni_ratepayer = _make_ratepayer(
            self.emfuleni, full_name="Emfuleni Person", msisdn="+27820000001"
        )
        self.sedibeng_ratepayer = _make_ratepayer(
            self.sedibeng, full_name="Sedibeng Person", msisdn="+27820000002"
        )

    def test_verify_otp_consumes_on_correct_code(self):
        otp = issue_otp(self.emfuleni_ratepayer)

        result = verify_otp(self.emfuleni_ratepayer, otp.code)

        self.assertTrue(result)
        otp.refresh_from_db()
        self.assertIsNotNone(otp.consumed_at)

    def test_verify_otp_returns_false_on_wrong_code(self):
        otp = issue_otp(self.emfuleni_ratepayer)
        wrong = "0" * OTP_CODE_DIGITS if otp.code != "0" * OTP_CODE_DIGITS else "1" * OTP_CODE_DIGITS

        result = verify_otp(self.emfuleni_ratepayer, wrong)

        self.assertFalse(result)
        otp.refresh_from_db()
        self.assertIsNone(otp.consumed_at)

    def test_verify_otp_returns_false_on_expired_code(self):
        otp = issue_otp(self.emfuleni_ratepayer)
        # Backdate expiry by an hour.
        otp.expires_at = timezone.now() - timedelta(hours=1)
        otp.save(update_fields=["expires_at"])

        result = verify_otp(self.emfuleni_ratepayer, otp.code)

        self.assertFalse(result)
        otp.refresh_from_db()
        self.assertIsNone(otp.consumed_at)

    def test_verify_otp_returns_false_on_already_consumed_code(self):
        otp = issue_otp(self.emfuleni_ratepayer)

        first = verify_otp(self.emfuleni_ratepayer, otp.code)
        second = verify_otp(self.emfuleni_ratepayer, otp.code)

        self.assertTrue(first)
        self.assertFalse(second)

    def test_verify_otp_is_tenant_scoped(self):
        # OTP belongs to a ratepayer in tenant A. Passing tenant B's
        # ratepayer must never resolve the code, regardless of value.
        otp = issue_otp(self.emfuleni_ratepayer)

        result = verify_otp(self.sedibeng_ratepayer, otp.code)

        self.assertFalse(result)
        otp.refresh_from_db()
        self.assertIsNone(otp.consumed_at)
