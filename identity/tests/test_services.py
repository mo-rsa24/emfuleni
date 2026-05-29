"""Tests for identity.services."""

from django.test import TestCase
from django.utils import timezone

from common.models import Municipality
from identity.models import Ratepayer, RatepayerAccountLink
from identity.services import bind_account, find_ratepayer_by_msisdn
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
