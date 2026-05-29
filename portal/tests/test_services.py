"""Tests for portal.services (Slice 5 — evidence upload)."""

from __future__ import annotations

import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from common.models import Municipality
from identity.models import Ratepayer, RatepayerAccountLink
from ingest.models import Extract, MunicipalAccount
from portal.models import Evidence
from portal.services import (
    EvidenceValidationError,
    get_evidence,
    list_evidence_for_account,
    record_evidence,
)


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


def _png_bytes(n: int = 32) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + (b"\x00" * n)


class _TempMediaTestCase(TestCase):
    """Shared base: redirect MEDIA_ROOT into a per-test temp directory."""

    def setUp(self):
        self._media_tmp = tempfile.TemporaryDirectory()
        self._override = override_settings(MEDIA_ROOT=self._media_tmp.name)
        self._override.enable()

    def tearDown(self):
        self._override.disable()
        self._media_tmp.cleanup()


class RecordEvidenceTests(_TempMediaTestCase):
    def setUp(self):
        super().setUp()
        self.fixture = _make_ratepayer_with_account()
        self.tenant = self.fixture["municipality"]
        self.ratepayer = self.fixture["ratepayer"]
        self.account = self.fixture["account"]

    def test_creates_evidence_row_for_valid_photo(self):
        upload = SimpleUploadedFile(
            "meter.png", _png_bytes(64), content_type="image/png"
        )

        evidence = record_evidence(
            ratepayer=self.ratepayer,
            account=self.account,
            kind="photo",
            uploaded_file=upload,
        )

        self.assertEqual(evidence.kind, "photo")
        self.assertEqual(evidence.original_filename, "meter.png")
        self.assertEqual(evidence.content_type, "image/png")
        self.assertEqual(evidence.size_bytes, len(_png_bytes(64)))
        self.assertEqual(evidence.municipality_id, self.ratepayer.municipality_id)
        # File lives under <tenant_slug>/evidence/ and is UUID-named, not "meter.png".
        self.assertTrue(evidence.file.name.startswith(f"{self.tenant.slug}/evidence/"))
        self.assertNotIn("meter.png", evidence.file.name)

    def test_creates_evidence_row_for_csv(self):
        upload = SimpleUploadedFile(
            "readings.csv", b"date,reading\n2026-01-01,123\n", content_type="text/csv"
        )

        evidence = record_evidence(
            ratepayer=self.ratepayer,
            account=self.account,
            kind="csv",
            uploaded_file=upload,
        )

        self.assertEqual(evidence.kind, "csv")
        self.assertEqual(evidence.content_type, "text/csv")
        self.assertTrue(evidence.file.name.endswith(".csv"))

    def test_creates_evidence_row_for_pdf(self):
        upload = SimpleUploadedFile(
            "statement.pdf", b"%PDF-1.4\n%fake\n", content_type="application/pdf"
        )

        evidence = record_evidence(
            ratepayer=self.ratepayer,
            account=self.account,
            kind="statement_pdf",
            uploaded_file=upload,
        )

        self.assertEqual(evidence.kind, "statement_pdf")
        self.assertEqual(evidence.content_type, "application/pdf")
        self.assertTrue(evidence.file.name.endswith(".pdf"))

    def test_unknown_kind_raises(self):
        upload = SimpleUploadedFile(
            "meter.png", _png_bytes(), content_type="image/png"
        )

        with self.assertRaises(EvidenceValidationError):
            record_evidence(
                ratepayer=self.ratepayer,
                account=self.account,
                kind="not_a_kind",
                uploaded_file=upload,
            )
        self.assertEqual(Evidence.objects.count(), 0)

    @override_settings(EVIDENCE_MAX_BYTES=128)
    def test_rejects_oversize_file(self):
        upload = SimpleUploadedFile(
            "huge.png", _png_bytes(256), content_type="image/png"
        )

        with self.assertRaises(EvidenceValidationError) as ctx:
            record_evidence(
                ratepayer=self.ratepayer,
                account=self.account,
                kind="photo",
                uploaded_file=upload,
            )
        self.assertIn("too large", str(ctx.exception))
        self.assertEqual(Evidence.objects.count(), 0)

    def test_rejects_empty_file(self):
        upload = SimpleUploadedFile("empty.png", b"", content_type="image/png")

        with self.assertRaises(EvidenceValidationError) as ctx:
            record_evidence(
                ratepayer=self.ratepayer,
                account=self.account,
                kind="photo",
                uploaded_file=upload,
            )
        self.assertIn("empty", str(ctx.exception))
        self.assertEqual(Evidence.objects.count(), 0)

    def test_rejects_extension_mismatch_for_kind(self):
        upload = SimpleUploadedFile(
            "evil.exe", b"MZ\x90\x00malware", content_type="image/png"
        )

        with self.assertRaises(EvidenceValidationError):
            record_evidence(
                ratepayer=self.ratepayer,
                account=self.account,
                kind="photo",
                uploaded_file=upload,
            )
        self.assertEqual(Evidence.objects.count(), 0)

    def test_rejects_mime_mismatch_for_kind(self):
        upload = SimpleUploadedFile(
            "meter.png", _png_bytes(), content_type="application/octet-stream"
        )

        with self.assertRaises(EvidenceValidationError):
            record_evidence(
                ratepayer=self.ratepayer,
                account=self.account,
                kind="photo",
                uploaded_file=upload,
            )
        self.assertEqual(Evidence.objects.count(), 0)

    def test_accepts_when_content_type_is_empty(self):
        upload = SimpleUploadedFile("photo.png", _png_bytes(), content_type="")

        evidence = record_evidence(
            ratepayer=self.ratepayer,
            account=self.account,
            kind="photo",
            uploaded_file=upload,
        )
        self.assertEqual(evidence.kind, "photo")
        self.assertEqual(evidence.content_type, "")

    def test_path_traversal_filename_stripped_by_storage(self):
        upload = SimpleUploadedFile(
            "../../../etc/passwd.png", _png_bytes(), content_type="image/png"
        )

        evidence = record_evidence(
            ratepayer=self.ratepayer,
            account=self.account,
            kind="photo",
            uploaded_file=upload,
        )

        self.assertTrue(evidence.file.name.startswith(f"{self.tenant.slug}/evidence/"))
        self.assertNotIn("..", evidence.file.name)
        self.assertNotIn("/etc/", evidence.file.name)
        # original_filename is preserved (Django's UploadedFile strips
        # leading path components, so it ends up as "passwd.png").
        self.assertEqual(evidence.original_filename, "passwd.png")

    def test_tenant_isolation_on_evidence_file_path(self):
        other = _make_ratepayer_with_account(
            slug="sedibeng",
            name="Sedibeng",
            account_number="55512345",
            full_name="Sedibeng Person",
            msisdn="+27820000088",
        )

        ev_a = record_evidence(
            ratepayer=self.ratepayer,
            account=self.account,
            kind="photo",
            uploaded_file=SimpleUploadedFile(
                "a.png", _png_bytes(), content_type="image/png"
            ),
        )
        ev_b = record_evidence(
            ratepayer=other["ratepayer"],
            account=other["account"],
            kind="photo",
            uploaded_file=SimpleUploadedFile(
                "b.png", _png_bytes(), content_type="image/png"
            ),
        )

        self.assertTrue(ev_a.file.name.startswith("emfuleni/evidence/"))
        self.assertTrue(ev_b.file.name.startswith("sedibeng/evidence/"))
        self.assertEqual(ev_a.municipality_id, self.tenant.pk)
        self.assertEqual(ev_b.municipality_id, other["municipality"].pk)


class GetEvidenceTests(_TempMediaTestCase):
    def setUp(self):
        super().setUp()
        self.fixture = _make_ratepayer_with_account()
        self.tenant = self.fixture["municipality"]
        self.ratepayer = self.fixture["ratepayer"]
        self.account = self.fixture["account"]
        self.evidence = record_evidence(
            ratepayer=self.ratepayer,
            account=self.account,
            kind="photo",
            uploaded_file=SimpleUploadedFile(
                "meter.png", _png_bytes(), content_type="image/png"
            ),
        )

    def test_returns_evidence_for_known_pk_and_tenant(self):
        result = get_evidence(self.tenant, self.evidence.pk)
        self.assertEqual(result, self.evidence)

    def test_returns_none_for_unknown_pk(self):
        self.assertIsNone(get_evidence(self.tenant, 999_999))

    def test_returns_none_for_garbage_pk(self):
        self.assertIsNone(get_evidence(self.tenant, "abc"))
        self.assertIsNone(get_evidence(self.tenant, None))
        self.assertIsNone(get_evidence(self.tenant, ""))

    def test_does_not_leak_evidence_across_tenants(self):
        other = _make_ratepayer_with_account(
            slug="sedibeng",
            name="Sedibeng",
            account_number="55512345",
            full_name="Sedibeng Person",
            msisdn="+27820000088",
        )

        result = get_evidence(other["municipality"], self.evidence.pk)
        self.assertIsNone(result)


class ListEvidenceForAccountTests(_TempMediaTestCase):
    def setUp(self):
        super().setUp()
        self.fixture = _make_ratepayer_with_account()
        self.tenant = self.fixture["municipality"]
        self.ratepayer = self.fixture["ratepayer"]
        self.account = self.fixture["account"]

    def _record_photo(self, name, account=None, ratepayer=None):
        return record_evidence(
            ratepayer=ratepayer or self.ratepayer,
            account=account or self.account,
            kind="photo",
            uploaded_file=SimpleUploadedFile(
                name, _png_bytes(), content_type="image/png"
            ),
        )

    def test_returns_empty_for_account_with_no_evidence(self):
        self.assertEqual(list_evidence_for_account(self.account), [])

    def test_returns_newest_first(self):
        ev1 = self._record_photo("first.png")
        ev2 = self._record_photo("second.png")
        ev3 = self._record_photo("third.png")

        result = list_evidence_for_account(self.account)

        self.assertEqual(result, [ev3, ev2, ev1])

    def test_does_not_leak_other_accounts_evidence(self):
        # Second account under the same tenant, linked to a second ratepayer.
        extract2 = _make_extract(
            self.tenant, content_hash=("hash-2" + "y" * 64)[:64]
        )
        account_b = _make_account(
            self.tenant,
            account_number="77777777",
            extract=extract2,
            holder_name="Account B Holder",
        )
        ratepayer_b = _make_ratepayer(
            self.tenant, full_name="Other Person", msisdn="+27820000099"
        )
        RatepayerAccountLink.objects.create(
            municipality=self.tenant,
            ratepayer=ratepayer_b,
            municipal_account=account_b,
        )

        ev_a = self._record_photo("a.png")
        self._record_photo("b.png", account=account_b, ratepayer=ratepayer_b)

        result = list_evidence_for_account(self.account)
        self.assertEqual(result, [ev_a])
