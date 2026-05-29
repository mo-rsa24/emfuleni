"""Tests for ingest.services."""

from django.test import TestCase
from django.utils import timezone

from common.models import Municipality
from ingest.models import Extract, MunicipalAccount
from ingest.services import get_account


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
