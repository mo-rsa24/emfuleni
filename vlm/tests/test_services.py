"""Tests for vlm.services (Slice 6 — VLM meter-photo extraction).

The provider client is faked end-to-end. No network. No `anthropic`
import. The `_default_client()` factory is monkey-patched for the
auto-trigger path; the `client=` kwarg is passed directly elsewhere.
"""

from __future__ import annotations

import tempfile
import types
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from common.models import Municipality
from identity.models import Ratepayer, RatepayerAccountLink
from ingest.models import Extract, MunicipalAccount
from portal.services import record_evidence
from vlm.models import VlmExtraction
from vlm.services import (
    enqueue_extraction,
    extract_meter_reading,
    get_extraction_for_evidence,
)


# --- Fake provider helpers -------------------------------------------------


def _fake_response(text: str):
    """Build an Anthropic-Messages-shaped response object."""
    return types.SimpleNamespace(
        content=[types.SimpleNamespace(type="text", text=text)]
    )


def _fake_client(text=None, raise_exc=None):
    """Build a fake `anthropic.Anthropic`-shaped client."""
    client = MagicMock()
    if raise_exc is not None:
        client.messages.create.side_effect = raise_exc
    else:
        client.messages.create.return_value = _fake_response(text)
    return client


# --- Tenant + Evidence fixtures --------------------------------------------


def _png_bytes(n: int = 32) -> bytes:
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


def _make_photo_evidence(
    *,
    slug="emfuleni",
    name="Emfuleni",
    account_number="88231104",
    msisdn="+27820000001",
    full_name="Jane Doe",
    filename="meter.png",
):
    """One-shot factory: tenant + extract + account + ratepayer + link + evidence."""
    municipality = Municipality.objects.create(slug=slug, name=name)
    content_hash = (account_number + "x" * 64)[:64]
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
    upload = SimpleUploadedFile(filename, _png_bytes(64), content_type="image/png")
    # Use the real portal service to construct Evidence so the on-disk file is
    # written under the tenant slug, mirroring production. We patch
    # _default_client to a sentinel so the auto-trigger run does not call the
    # real provider during fixture setup.
    with patch("vlm.services._default_client", return_value=_fake_client('{"reading_kl": 0, "confidence": 0.0, "notes": ""}')):
        evidence = record_evidence(
            ratepayer=ratepayer,
            account=account,
            kind="photo",
            uploaded_file=upload,
        )
    # The auto-trigger left a row behind — clear it so each test starts clean.
    VlmExtraction.objects.all().delete()
    return {
        "municipality": municipality,
        "ratepayer": ratepayer,
        "account": account,
        "evidence": evidence,
    }


# --- Shared base ------------------------------------------------------------


class _TempMediaTestCase(TestCase):
    """Redirect MEDIA_ROOT into a per-test temp directory."""

    def setUp(self):
        self._media_tmp = tempfile.TemporaryDirectory()
        self._override = override_settings(MEDIA_ROOT=self._media_tmp.name)
        self._override.enable()

    def tearDown(self):
        self._override.disable()
        self._media_tmp.cleanup()


# --- extract_meter_reading -------------------------------------------------


class ExtractMeterReadingTests(_TempMediaTestCase):
    def setUp(self):
        super().setUp()
        self.fixture = _make_photo_evidence()
        self.evidence = self.fixture["evidence"]
        self.tenant = self.fixture["municipality"]

    def test_happy_path_persists_extracted_row(self):
        client = _fake_client(
            text='{"reading_kl": 4127, "confidence": 0.94, "notes": "clear"}'
        )

        extraction = extract_meter_reading(self.evidence, client=client)

        self.assertEqual(extraction.status, VlmExtraction.STATUS_EXTRACTED)
        self.assertEqual(extraction.reading_kl, 4127)
        self.assertAlmostEqual(extraction.confidence, 0.94)
        self.assertEqual(extraction.municipality_id, self.evidence.municipality_id)
        from django.conf import settings

        self.assertEqual(extraction.model_name, settings.VLM_MODEL)
        self.assertEqual(extraction.raw_response["parsed"]["reading_kl"], 4127)
        self.assertEqual(extraction.notes, "clear")
        # The provider was actually invoked.
        client.messages.create.assert_called_once()

    def test_low_confidence_marks_status_low_confidence(self):
        client = _fake_client(
            text='{"reading_kl": 4127, "confidence": 0.30, "notes": "blurry"}'
        )

        extraction = extract_meter_reading(self.evidence, client=client)

        self.assertEqual(extraction.status, VlmExtraction.STATUS_LOW_CONFIDENCE)
        self.assertEqual(extraction.reading_kl, 4127)
        self.assertAlmostEqual(extraction.confidence, 0.30)

    def test_confidence_exactly_at_threshold_is_extracted(self):
        # VLM_MIN_CONFIDENCE defaults to 0.50; the comparison is strict `<`.
        client = _fake_client(
            text='{"reading_kl": 100, "confidence": 0.50, "notes": ""}'
        )

        extraction = extract_meter_reading(self.evidence, client=client)

        self.assertEqual(extraction.status, VlmExtraction.STATUS_EXTRACTED)

    def test_provider_exception_marks_failed_and_captures_error(self):
        client = _fake_client(raise_exc=RuntimeError("network down"))

        extraction = extract_meter_reading(self.evidence, client=client)

        self.assertEqual(extraction.status, VlmExtraction.STATUS_FAILED)
        self.assertIsNone(extraction.reading_kl)
        self.assertIsNone(extraction.confidence)
        self.assertIn("network down", extraction.raw_response["error"])

    def test_unparseable_response_marks_failed(self):
        client = _fake_client(text="not valid json")

        extraction = extract_meter_reading(self.evidence, client=client)

        self.assertEqual(extraction.status, VlmExtraction.STATUS_FAILED)
        self.assertIn("could not be parsed", extraction.raw_response["error"])
        self.assertEqual(extraction.raw_response["text"], "not valid json")

    def test_markdown_fenced_json_is_parsed(self):
        # Sonnet sometimes wraps its answer in ```json ... ``` fences
        # even when prompted for raw JSON. Should still extract cleanly.
        fenced = (
            "```json\n"
            '{"reading_kl": 4127, "confidence": 0.94, "notes": "clear"}\n'
            "```"
        )
        client = _fake_client(text=fenced)

        extraction = extract_meter_reading(self.evidence, client=client)

        self.assertEqual(extraction.status, VlmExtraction.STATUS_EXTRACTED)
        self.assertEqual(extraction.reading_kl, 4127)
        self.assertAlmostEqual(extraction.confidence, 0.94)

    @override_settings(ANTHROPIC_API_KEY="")
    def test_missing_api_key_marks_failed_when_no_client_passed(self):
        extraction = extract_meter_reading(self.evidence)

        self.assertEqual(extraction.status, VlmExtraction.STATUS_FAILED)
        self.assertIn("ANTHROPIC_API_KEY", extraction.raw_response["error"])

    def test_idempotent_on_retry(self):
        first = _fake_client(
            text='{"reading_kl": 4127, "confidence": 0.94, "notes": "first"}'
        )
        second = _fake_client(
            text='{"reading_kl": 9999, "confidence": 0.20, "notes": "second"}'
        )

        extract_meter_reading(self.evidence, client=first)
        extract_meter_reading(self.evidence, client=second)

        rows = VlmExtraction.objects.filter(evidence=self.evidence)
        self.assertEqual(rows.count(), 1)
        row = rows.first()
        self.assertEqual(row.reading_kl, 9999)
        self.assertEqual(row.status, VlmExtraction.STATUS_LOW_CONFIDENCE)
        self.assertEqual(row.notes, "second")

    def test_tenancy_isolation_via_get_extraction_for_evidence(self):
        # Second tenant + evidence.
        other_fixture = _make_photo_evidence(
            slug="sedibeng",
            name="Sedibeng",
            account_number="55512345",
            full_name="Sedibeng Person",
            msisdn="+27820000088",
            filename="other.png",
        )
        evidence_b = other_fixture["evidence"]
        tenant_b = other_fixture["municipality"]

        client_a = _fake_client(
            text='{"reading_kl": 100, "confidence": 0.90, "notes": "A"}'
        )
        client_b = _fake_client(
            text='{"reading_kl": 200, "confidence": 0.90, "notes": "B"}'
        )
        extract_meter_reading(self.evidence, client=client_a)
        extract_meter_reading(evidence_b, client=client_b)

        row_a = get_extraction_for_evidence(self.evidence)
        row_b = get_extraction_for_evidence(evidence_b)

        self.assertIsNotNone(row_a)
        self.assertIsNotNone(row_b)
        self.assertNotEqual(row_a.pk, row_b.pk)
        self.assertEqual(row_a.reading_kl, 100)
        self.assertEqual(row_b.reading_kl, 200)

        # Manager-level scoping: tenant_a sees only A's row.
        in_a = list(VlmExtraction.objects.for_tenant(self.tenant))
        self.assertEqual(in_a, [row_a])
        in_b = list(VlmExtraction.objects.for_tenant(tenant_b))
        self.assertEqual(in_b, [row_b])


# --- enqueue_extraction -----------------------------------------------------


class EnqueueExtractionTests(_TempMediaTestCase):
    def setUp(self):
        super().setUp()
        self.fixture = _make_photo_evidence()
        self.evidence = self.fixture["evidence"]

    def test_enqueue_extraction_persists_row(self):
        fake = _fake_client(
            text='{"reading_kl": 4127, "confidence": 0.94, "notes": "from worker"}'
        )

        with patch("vlm.services._default_client", return_value=fake):
            enqueue_extraction(self.evidence)

        row = VlmExtraction.objects.get(evidence=self.evidence)
        self.assertEqual(row.status, VlmExtraction.STATUS_EXTRACTED)
        self.assertEqual(row.reading_kl, 4127)
        self.assertEqual(row.notes, "from worker")
