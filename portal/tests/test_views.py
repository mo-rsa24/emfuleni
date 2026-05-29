"""Tests for portal views (Slice 4 + Slice 5 evidence upload)."""

import tempfile
from datetime import date

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from common.models import Municipality
from identity.models import OtpCode, Ratepayer, RatepayerAccountLink
from ingest.models import Extract, MunicipalAccount, MunicipalBill
from portal.models import Evidence


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


def _make_ratepayer(municipality, *, full_name="Jane Doe", msisdn=None):
    return Ratepayer.objects.create(
        municipality=municipality,
        full_name=full_name,
        msisdn=msisdn,
    )


def _make_bill(
    municipality,
    *,
    account,
    period,
    extract,
    opening_balance="1000.00",
    closing_balance="1500.00",
):
    return MunicipalBill.objects.create(
        municipality=municipality,
        municipal_account=account,
        period=period,
        opening_balance=opening_balance,
        closing_balance=closing_balance,
        source_extract=extract,
    )


def _make_ratepayer_with_account(
    *,
    municipality=None,
    slug="emfuleni",
    name="Emfuleni",
    account_number="88231104",
    full_name="Jane Doe",
    msisdn="+27820000001",
    content_hash=None,
):
    """One-shot factory: tenant + extract + account + ratepayer + link."""
    if municipality is None:
        municipality = Municipality.objects.create(slug=slug, name=name)
    if content_hash is None:
        # Hash of length 64 — derive a unique one from the account number
        # so multiple calls in the same test don't clash.
        content_hash = (account_number + "x" * 64)[:64]
    extract = _make_extract(municipality, content_hash=content_hash)
    account = _make_account(
        municipality,
        account_number=account_number,
        extract=extract,
        holder_name=full_name,
    )
    ratepayer = _make_ratepayer(municipality, full_name=full_name, msisdn=msisdn)
    link = RatepayerAccountLink.objects.create(
        municipality=municipality,
        ratepayer=ratepayer,
        municipal_account=account,
    )
    return {
        "municipality": municipality,
        "extract": extract,
        "account": account,
        "ratepayer": ratepayer,
        "link": link,
    }


def _login(client, ratepayer):
    session = client.session
    session[settings.PORTAL_SESSION_KEY] = ratepayer.pk
    session.save()


class LookupViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.fixture = _make_ratepayer_with_account()
        self.tenant = self.fixture["municipality"]

    def test_get_renders_form(self):
        response = self.client.get("/lookup/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "account_number")
        self.assertContains(response, "Send code")

    def test_post_with_unknown_account_renders_error(self):
        response = self.client.post("/lookup/", {"account_number": "99999999"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No matching account found")
        # Submitted value preserved in the form.
        self.assertContains(response, 'value="99999999"')

    def test_post_with_empty_account_renders_error(self):
        response = self.client.post("/lookup/", {"account_number": ""})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please enter your account number")

    def test_post_with_account_without_ratepayer_renders_error(self):
        # An account in the same tenant but with no ratepayer link.
        _make_account(
            self.tenant,
            account_number="77777777",
            extract=self.fixture["extract"],
        )

        response = self.client.post("/lookup/", {"account_number": "77777777"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "not yet bound to a ratepayer")

    def test_post_with_valid_account_issues_otp_and_renders_verify(self):
        account_number = self.fixture["account"].account_number
        ratepayer = self.fixture["ratepayer"]

        self.assertEqual(OtpCode.objects.filter(ratepayer=ratepayer).count(), 0)

        response = self.client.post("/lookup/", {"account_number": account_number})

        self.assertEqual(response.status_code, 200)
        # Verify form: hidden ratepayer_id + code field.
        self.assertContains(response, 'name="ratepayer_id"')
        self.assertContains(response, 'name="code"')
        self.assertContains(response, account_number)
        # A fresh OTP was issued.
        self.assertEqual(OtpCode.objects.filter(ratepayer=ratepayer).count(), 1)


class VerifyViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.fixture = _make_ratepayer_with_account()
        self.tenant = self.fixture["municipality"]
        self.ratepayer = self.fixture["ratepayer"]
        self.account = self.fixture["account"]

    def _issue_otp(self):
        # Drive it through the lookup view so we exercise the same code path.
        self.client.post(
            "/lookup/", {"account_number": self.account.account_number}
        )
        return OtpCode.objects.filter(ratepayer=self.ratepayer).latest("created_at")

    def test_post_with_correct_otp_sets_session_and_redirects(self):
        otp = self._issue_otp()

        response = self.client.post(
            "/verify/",
            {
                "ratepayer_id": self.ratepayer.pk,
                "code": otp.code,
                "account_number": self.account.account_number,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], f"/account/{self.account.pk}/")
        self.assertEqual(
            self.client.session.get(settings.PORTAL_SESSION_KEY),
            self.ratepayer.pk,
        )

    def test_post_with_wrong_otp_renders_error(self):
        self._issue_otp()

        response = self.client.post(
            "/verify/",
            {
                "ratepayer_id": self.ratepayer.pk,
                "code": "000000",
                "account_number": self.account.account_number,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "incorrect or expired")
        self.assertNotIn(settings.PORTAL_SESSION_KEY, self.client.session)

    def test_post_with_missing_ratepayer_redirects_to_lookup(self):
        response = self.client.post(
            "/verify/",
            {
                "ratepayer_id": 99999,
                "code": "123456",
                "account_number": self.account.account_number,
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/lookup/")


class AccountDetailViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.fixture = _make_ratepayer_with_account()
        self.tenant = self.fixture["municipality"]
        self.ratepayer = self.fixture["ratepayer"]
        self.account = self.fixture["account"]
        self.bill = _make_bill(
            self.tenant,
            account=self.account,
            period=date(2026, 6, 1),
            extract=self.fixture["extract"],
            closing_balance="2940.00",
        )

    def test_logged_in_and_linked_renders_bill(self):
        _login(self.client, self.ratepayer)

        response = self.client.get(f"/account/{self.account.pk}/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.account.account_number)
        self.assertContains(response, "2940.00")

    def test_not_logged_in_redirects_to_lookup(self):
        response = self.client.get(f"/account/{self.account.pk}/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/lookup/")

    def test_logged_in_but_not_linked_redirects_to_home(self):
        # Ratepayer B exists in the same tenant but is not linked to
        # ratepayer A's account.
        ratepayer_b = _make_ratepayer(
            self.tenant, full_name="Other Person", msisdn="+27820000099"
        )
        _login(self.client, ratepayer_b)

        response = self.client.get(f"/account/{self.account.pk}/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")

    def test_logged_in_account_belongs_to_other_tenant_redirects_to_home(self):
        # Tenant B (different slug) with its own account.
        other = _make_ratepayer_with_account(
            slug="sedibeng",
            name="Sedibeng",
            account_number="88888888",
            full_name="Sedibeng Person",
            msisdn="+27820000088",
        )

        # Log in as the Emfuleni ratepayer. The current tenant resolves
        # to "emfuleni", so the Sedibeng account is invisible via
        # get_account_by_pk(tenant=...). The view collapses "no such
        # account" and "account not linked to you" to the same redirect
        # response so the two cases cannot be distinguished externally.
        _login(self.client, self.ratepayer)

        response = self.client.get(f"/account/{other['account'].pk}/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")


class ChallengePanelTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.fixture = _make_ratepayer_with_account()
        self.ratepayer = self.fixture["ratepayer"]
        self.account = self.fixture["account"]

    def test_post_when_logged_in_returns_upload_form(self):
        _login(self.client, self.ratepayer)

        response = self.client.post(f"/account/{self.account.pk}/challenge/")

        self.assertEqual(response.status_code, 200)
        # Renders the upload partial with the file input.
        self.assertContains(response, 'name="file"')
        self.assertContains(response, 'name="kind"')

    def test_post_when_not_logged_in_returns_401(self):
        response = self.client.post(f"/account/{self.account.pk}/challenge/")

        self.assertEqual(response.status_code, 401)


def _png_bytes(n: int = 32) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + (b"\x00" * n)


class EvidenceUploadViewTests(TestCase):
    def setUp(self):
        self._media_tmp = tempfile.TemporaryDirectory()
        self._override = override_settings(MEDIA_ROOT=self._media_tmp.name)
        self._override.enable()
        self.client = Client()
        self.fixture = _make_ratepayer_with_account()
        self.tenant = self.fixture["municipality"]
        self.ratepayer = self.fixture["ratepayer"]
        self.account = self.fixture["account"]

    def tearDown(self):
        self._override.disable()
        self._media_tmp.cleanup()

    def _upload_url(self, account_id):
        return f"/account/{account_id}/evidence/upload/"

    def test_post_with_valid_photo_persists_row_and_renders_success(self):
        _login(self.client, self.ratepayer)
        file = SimpleUploadedFile(
            "meter.png", _png_bytes(), content_type="image/png"
        )

        response = self.client.post(
            self._upload_url(self.account.pk),
            {"kind": "photo", "file": file},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Uploaded")
        self.assertEqual(Evidence.objects.count(), 1)
        evidence = Evidence.objects.first()
        self.assertEqual(evidence.kind, "photo")
        self.assertEqual(evidence.municipality_id, self.tenant.pk)

    def test_post_with_invalid_extension_does_not_persist_and_shows_error(self):
        _login(self.client, self.ratepayer)
        file = SimpleUploadedFile(
            "evil.exe", b"MZ\x90\x00malware", content_type="image/png"
        )

        response = self.client.post(
            self._upload_url(self.account.pk),
            {"kind": "photo", "file": file},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "accepted for this kind")
        self.assertEqual(Evidence.objects.count(), 0)

    def test_post_with_no_file_shows_error(self):
        _login(self.client, self.ratepayer)

        response = self.client.post(
            self._upload_url(self.account.pk),
            {"kind": "photo"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a file")
        self.assertEqual(Evidence.objects.count(), 0)

    def test_post_when_not_logged_in_returns_401(self):
        file = SimpleUploadedFile(
            "meter.png", _png_bytes(), content_type="image/png"
        )

        response = self.client.post(
            self._upload_url(self.account.pk),
            {"kind": "photo", "file": file},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Evidence.objects.count(), 0)

    def test_post_when_logged_in_but_not_linked_to_account_returns_401(self):
        # Ratepayer B exists in the same tenant but is not linked to
        # ratepayer A's account.
        ratepayer_b = _make_ratepayer(
            self.tenant, full_name="Other Person", msisdn="+27820000099"
        )
        _login(self.client, ratepayer_b)
        file = SimpleUploadedFile(
            "meter.png", _png_bytes(), content_type="image/png"
        )

        response = self.client.post(
            self._upload_url(self.account.pk),
            {"kind": "photo", "file": file},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Evidence.objects.count(), 0)

    def test_post_when_account_belongs_to_other_tenant_returns_401(self):
        other = _make_ratepayer_with_account(
            slug="sedibeng",
            name="Sedibeng",
            account_number="55512345",
            full_name="Sedibeng Person",
            msisdn="+27820000088",
        )

        # Log in as the Emfuleni ratepayer; POST to the Sedibeng account.
        _login(self.client, self.ratepayer)
        file = SimpleUploadedFile(
            "meter.png", _png_bytes(), content_type="image/png"
        )

        response = self.client.post(
            self._upload_url(other["account"].pk),
            {"kind": "photo", "file": file},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(Evidence.objects.count(), 0)


class LogoutTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.fixture = _make_ratepayer_with_account()
        self.ratepayer = self.fixture["ratepayer"]

    def test_clears_session_and_redirects(self):
        _login(self.client, self.ratepayer)
        self.assertEqual(
            self.client.session.get(settings.PORTAL_SESSION_KEY),
            self.ratepayer.pk,
        )

        response = self.client.get("/logout/")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "/")
        self.assertNotIn(settings.PORTAL_SESSION_KEY, self.client.session)
