"""Tests for ingest.services."""

import tempfile
from datetime import date
from pathlib import Path

from django.test import TestCase, override_settings
from django.utils import timezone

from common.models import Municipality
from ingest.importers import (
    ExtractImportError,
    ImportResult,
    import_extract_from_file,
)
from ingest.inbox import FileSystemInboxAdapter, hash_file
from ingest.models import (
    Extract,
    MeterReading,
    MunicipalAccount,
    MunicipalBill,
    MunicipalLedgerEntry,
)
from ingest.services import enqueue_import_file, enqueue_poll, get_account


CSV_HEADER = (
    "account_number,holder_name,service_address,account_class,period,"
    "opening_balance,closing_balance,water_kl,rates,refuse,payments_received,"
    "latest_meter_reading_kl,latest_meter_reading_date\n"
)


def _two_row_csv() -> str:
    return (
        CSV_HEADER
        + "88231104,Mrs N Dlamini,\"12 Marigold St, Sebokeng\",residential,"
          "2026-06,2410.00,2940.00,18,800,220,0,4127,2026-06-01\n"
        + "88231205,Mr T Khumalo,\"44 Vanderbijl Rd, Vanderbijlpark\",residential,"
          "2026-06,1880.00,2110.00,16,800,220,0,2283,2026-06-01\n"
    )


def _write_csv(tmp_dir: Path, name: str, body: str) -> Path:
    target = tmp_dir / name
    target.write_text(body, encoding="utf-8")
    return target


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


class GetAccountTests(TestCase):
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

    def test_returns_account_for_known_number(self):
        result = get_account(self.emfuleni, "88231104")

        self.assertIsNotNone(result)
        self.assertEqual(result.pk, self.emfuleni_account.pk)
        self.assertEqual(result.account_number, "88231104")
        self.assertEqual(result.holder_name, "Emfuleni Holder")

    def test_returns_none_for_unknown_account_number(self):
        result = get_account(self.emfuleni, "99999999")

        self.assertIsNone(result)

    def test_does_not_leak_account_across_tenants(self):
        # Same account_number string, different tenants — the unique
        # constraint is (municipality, account_number), so both can coexist.
        sedibeng_account = _make_account(
            self.sedibeng,
            account_number="88231104",
            extract=self.sedibeng_extract,
            holder_name="Sedibeng Holder",
        )

        emfuleni_result = get_account(self.emfuleni, "88231104")
        sedibeng_result = get_account(self.sedibeng, "88231104")

        self.assertEqual(emfuleni_result.pk, self.emfuleni_account.pk)
        self.assertEqual(emfuleni_result.holder_name, "Emfuleni Holder")

        self.assertEqual(sedibeng_result.pk, sedibeng_account.pk)
        self.assertEqual(sedibeng_result.holder_name, "Sedibeng Holder")

        self.assertNotEqual(emfuleni_result.pk, sedibeng_result.pk)

    def test_returns_none_when_account_only_exists_under_other_tenant(self):
        # Account exists only under Sedibeng — lookup under Emfuleni must miss.
        _make_account(
            self.sedibeng,
            account_number="55555555",
            extract=self.sedibeng_extract,
        )

        result = get_account(self.emfuleni, "55555555")

        self.assertIsNone(result)


class ImportExtractFromFileTests(TestCase):
    def setUp(self):
        self.emfuleni = Municipality.objects.create(slug="emfuleni", name="Emfuleni")
        self.sedibeng = Municipality.objects.create(slug="sedibeng", name="Sedibeng")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_dir = Path(self._tmp.name)

    def test_imports_csv_into_upstream_models(self):
        path = _write_csv(self.tmp_dir, "billing.csv", _two_row_csv())

        result = import_extract_from_file(self.emfuleni, path)

        self.assertIsInstance(result, ImportResult)
        self.assertFalse(result.skipped_duplicate)
        self.assertEqual(result.accounts_touched, 2)
        self.assertEqual(result.bills_created, 2)
        self.assertEqual(result.ledger_entries_created, 2)
        self.assertEqual(result.meter_readings_created, 2)

        self.assertEqual(Extract.objects.count(), 1)
        self.assertEqual(MunicipalAccount.objects.count(), 2)
        self.assertEqual(MunicipalBill.objects.count(), 2)
        self.assertEqual(MunicipalLedgerEntry.objects.count(), 2)
        self.assertEqual(MeterReading.objects.count(), 2)

        extract = Extract.objects.get()
        self.assertEqual(extract.municipality_id, self.emfuleni.id)
        self.assertEqual(extract.status, Extract.STATUS_IMPORTED)
        self.assertEqual(extract.row_count, 2)
        self.assertEqual(extract.filename, "billing.csv")

    def test_is_idempotent_on_content_hash(self):
        path = _write_csv(self.tmp_dir, "billing.csv", _two_row_csv())

        first = import_extract_from_file(self.emfuleni, path)
        second = import_extract_from_file(self.emfuleni, path)

        self.assertFalse(first.skipped_duplicate)
        self.assertTrue(second.skipped_duplicate)
        self.assertEqual(second.accounts_touched, 0)
        self.assertEqual(second.bills_created, 0)
        self.assertEqual(second.ledger_entries_created, 0)
        self.assertEqual(second.meter_readings_created, 0)
        self.assertEqual(second.extract.pk, first.extract.pk)

        self.assertEqual(Extract.objects.count(), 1)
        self.assertEqual(MunicipalAccount.objects.count(), 2)
        self.assertEqual(MunicipalBill.objects.count(), 2)

    def test_raises_on_missing_required_column(self):
        # Drop holder_name from header AND row.
        bad_header = (
            "account_number,service_address,account_class,period,"
            "opening_balance,closing_balance,water_kl,rates,refuse,"
            "payments_received,latest_meter_reading_kl,latest_meter_reading_date\n"
        )
        body = (
            bad_header
            + "88231104,\"12 Marigold St\",residential,2026-06,"
              "2410.00,2940.00,18,800,220,0,4127,2026-06-01\n"
        )
        path = _write_csv(self.tmp_dir, "broken.csv", body)

        with self.assertRaises(ExtractImportError):
            import_extract_from_file(self.emfuleni, path)

        # Atomic rollback: no Extract or domain rows created.
        self.assertEqual(Extract.objects.count(), 0)
        self.assertEqual(MunicipalAccount.objects.count(), 0)
        self.assertEqual(MunicipalBill.objects.count(), 0)
        self.assertEqual(MunicipalLedgerEntry.objects.count(), 0)
        self.assertEqual(MeterReading.objects.count(), 0)

    def test_raises_on_bad_period(self):
        body = (
            CSV_HEADER
            + "88231104,Mrs N Dlamini,\"12 Marigold St\",residential,"
              "not-a-date,2410.00,2940.00,18,800,220,0,4127,2026-06-01\n"
        )
        path = _write_csv(self.tmp_dir, "badperiod.csv", body)

        with self.assertRaises(ExtractImportError):
            import_extract_from_file(self.emfuleni, path)

        self.assertEqual(Extract.objects.count(), 0)
        self.assertEqual(MunicipalAccount.objects.count(), 0)
        self.assertEqual(MunicipalBill.objects.count(), 0)
        self.assertEqual(MunicipalLedgerEntry.objects.count(), 0)
        self.assertEqual(MeterReading.objects.count(), 0)

    def test_period_normalised_to_day_one(self):
        body = (
            CSV_HEADER
            + "88231104,Mrs N Dlamini,\"12 Marigold St\",residential,"
              "2026-06-15,2410.00,2940.00,18,800,220,0,4127,2026-06-01\n"
        )
        path = _write_csv(self.tmp_dir, "midmonth.csv", body)

        import_extract_from_file(self.emfuleni, path)

        bill = MunicipalBill.objects.get()
        self.assertEqual(bill.period, date(2026, 6, 1))

    def test_tenant_isolation_on_same_account_number(self):
        path = _write_csv(self.tmp_dir, "shared.csv", _two_row_csv())

        result_a = import_extract_from_file(self.emfuleni, path)
        result_b = import_extract_from_file(self.sedibeng, path)

        self.assertFalse(result_a.skipped_duplicate)
        self.assertFalse(result_b.skipped_duplicate)

        # Two extracts (one per tenant), both with the same content_hash.
        self.assertEqual(Extract.objects.count(), 2)
        self.assertEqual(
            Extract.objects.filter(municipality=self.emfuleni).count(), 1
        )
        self.assertEqual(
            Extract.objects.filter(municipality=self.sedibeng).count(), 1
        )

        # Same account_number string under each tenant — distinct rows.
        emf_acct = MunicipalAccount.objects.get(
            municipality=self.emfuleni, account_number="88231104"
        )
        sed_acct = MunicipalAccount.objects.get(
            municipality=self.sedibeng, account_number="88231104"
        )
        self.assertNotEqual(emf_acct.pk, sed_acct.pk)

        # Four accounts total (two per tenant), no cross-tenant interference.
        self.assertEqual(MunicipalAccount.objects.count(), 4)
        self.assertEqual(MunicipalBill.objects.count(), 4)


class EnqueuePollSyncTests(TestCase):
    """RQ ASYNC=False under tests, so enqueue_* runs inline."""

    def setUp(self):
        self.emfuleni = Municipality.objects.create(slug="emfuleni", name="Emfuleni")
        self.sedibeng = Municipality.objects.create(slug="sedibeng", name="Sedibeng")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.inbox_root = Path(self._tmp.name)

    def _seed_tenant_file(self, tenant, name="billing.csv", body=None):
        tenant_dir = self.inbox_root / tenant.slug
        tenant_dir.mkdir(parents=True, exist_ok=True)
        return _write_csv(tenant_dir, name, body or _two_row_csv())

    def test_enqueue_poll_imports_files_in_tenant_inbox(self):
        self._seed_tenant_file(self.emfuleni)

        with override_settings(INGEST_INBOX_DIR=str(self.inbox_root)):
            enqueue_poll(self.emfuleni)

        # One Extract under Emfuleni, two accounts from the two-row CSV.
        self.assertEqual(
            Extract.objects.filter(municipality=self.emfuleni).count(), 1
        )
        self.assertTrue(
            MunicipalAccount.objects.filter(
                municipality=self.emfuleni, account_number="88231104"
            ).exists()
        )
        self.assertTrue(
            MunicipalAccount.objects.filter(
                municipality=self.emfuleni, account_number="88231205"
            ).exists()
        )
        # Sedibeng untouched.
        self.assertEqual(
            Extract.objects.filter(municipality=self.sedibeng).count(), 0
        )

    def test_enqueue_poll_is_idempotent_via_inbox_hash_filter(self):
        self._seed_tenant_file(self.emfuleni)

        with override_settings(INGEST_INBOX_DIR=str(self.inbox_root)):
            enqueue_poll(self.emfuleni)
            enqueue_poll(self.emfuleni)

        # Hash filter on the inbox side prevents a second Extract.
        self.assertEqual(
            Extract.objects.filter(municipality=self.emfuleni).count(), 1
        )
        self.assertEqual(
            MunicipalAccount.objects.filter(municipality=self.emfuleni).count(),
            2,
        )


class FileSystemInboxAdapterTests(TestCase):
    def setUp(self):
        self.emfuleni = Municipality.objects.create(slug="emfuleni", name="Emfuleni")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.inbox_root = Path(self._tmp.name)

    def test_returns_empty_when_tenant_dir_missing(self):
        # No subdir for the tenant; root exists but is empty.
        with override_settings(INGEST_INBOX_DIR=str(self.inbox_root)):
            adapter = FileSystemInboxAdapter()
            files = adapter.list_new_files(self.emfuleni)

        self.assertEqual(files, [])

    def test_skips_dotfiles_and_subdirs(self):
        tenant_dir = self.inbox_root / self.emfuleni.slug
        tenant_dir.mkdir(parents=True)
        # A real CSV, a dotfile, and a subdirectory.
        real = _write_csv(tenant_dir, "billing.csv", _two_row_csv())
        _write_csv(tenant_dir, ".hidden.csv", _two_row_csv())
        (tenant_dir / "subdir").mkdir()

        with override_settings(INGEST_INBOX_DIR=str(self.inbox_root)):
            adapter = FileSystemInboxAdapter()
            files = adapter.list_new_files(self.emfuleni)

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].path, real)
        self.assertEqual(files[0].filename, "billing.csv")

    def test_filters_already_imported_files_by_hash(self):
        tenant_dir = self.inbox_root / self.emfuleni.slug
        tenant_dir.mkdir(parents=True)
        path = _write_csv(tenant_dir, "billing.csv", _two_row_csv())

        # Pre-record an Extract under this tenant with the file's hash.
        Extract.objects.create(
            municipality=self.emfuleni,
            filename="billing.csv",
            received_at=timezone.now(),
            content_hash=hash_file(path),
            row_count=2,
            status=Extract.STATUS_IMPORTED,
        )

        with override_settings(INGEST_INBOX_DIR=str(self.inbox_root)):
            adapter = FileSystemInboxAdapter()
            files = adapter.list_new_files(self.emfuleni)

        self.assertEqual(files, [])
