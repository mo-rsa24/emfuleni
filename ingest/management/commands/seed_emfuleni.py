"""Seed the dev database with one Extract's worth of Emfuleni data.

Idempotent — running twice produces the same rows (uses get_or_create
on natural keys, and the Extract is keyed by content_hash).

Usage:
    python manage.py seed_emfuleni
"""

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from common.models import Municipality
from ingest.models import (
    Extract,
    MeterReading,
    MunicipalAccount,
    MunicipalBill,
    MunicipalLedgerEntry,
)


SEED_ACCOUNTS = [
    {
        "account_number": "88231104",
        "holder_name": "Mrs N Dlamini",
        "service_address": "12 Marigold St, Sebokeng, Emfuleni",
        "account_class": "residential",
        "may_closing": Decimal("2410.00"),
        "june_closing": Decimal("2940.00"),
        "readings": [(1, 4080), (2, 4092), (3, 4101), (4, 4112), (5, 4119), (6, 4127)],
    },
    {
        "account_number": "88231205",
        "holder_name": "Mr T Khumalo",
        "service_address": "44 Vanderbijl Rd, Vanderbijlpark, Emfuleni",
        "account_class": "residential",
        "may_closing": Decimal("1880.00"),
        "june_closing": Decimal("2110.00"),
        "readings": [(1, 2210), (2, 2225), (3, 2238), (4, 2252), (5, 2267), (6, 2283)],
    },
    {
        "account_number": "88231306",
        "holder_name": "ABC Spaza CC",
        "service_address": "9 Market Ln, Sharpeville, Emfuleni",
        "account_class": "business",
        "may_closing": Decimal("6420.00"),
        "june_closing": Decimal("7180.00"),
        "readings": [(1, 11820), (2, 11960), (3, 12100), (4, 12245), (5, 12390), (6, 12540)],
    },
]


class Command(BaseCommand):
    help = "Seed the dev DB with one Extract's worth of Emfuleni fixture data."

    @transaction.atomic
    def handle(self, *args, **opts):
        emfuleni, _ = Municipality.objects.get_or_create(
            slug="emfuleni",
            defaults={"name": "Emfuleni Local Municipality"},
        )
        self.stdout.write(self.style.SUCCESS(f"Tenant: {emfuleni}"))

        fixture_body = b"emfuleni-seed-fixture-v1"
        content_hash = hashlib.sha256(fixture_body).hexdigest()
        extract, created = Extract.objects.get_or_create(
            municipality=emfuleni,
            content_hash=content_hash,
            defaults={
                "filename": "billing_june_seed.csv",
                "received_at": datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc),
                "row_count": len(SEED_ACCOUNTS),
                "status": Extract.STATUS_IMPORTED,
            },
        )
        verb = "Created" if created else "Reused"
        self.stdout.write(f"{verb} extract: {extract}")

        for spec in SEED_ACCOUNTS:
            self._seed_account(emfuleni, extract, spec)

        self.stdout.write(self.style.SUCCESS("Seed complete."))

    def _seed_account(self, tenant, extract, spec):
        account, _ = MunicipalAccount.objects.get_or_create(
            municipality=tenant,
            account_number=spec["account_number"],
            defaults={
                "holder_name": spec["holder_name"],
                "service_address": spec["service_address"],
                "account_class": spec["account_class"],
                "source_extract": extract,
            },
        )

        may_bill, _ = MunicipalBill.objects.get_or_create(
            municipal_account=account,
            period=date(2026, 5, 1),
            defaults={
                "municipality": tenant,
                "opening_balance": Decimal("0.00"),
                "closing_balance": spec["may_closing"],
                "charges": {"water_kl": 14, "rates": 800, "refuse": 220},
                "payments": {"received": 0},
                "source_extract": extract,
            },
        )
        june_bill, _ = MunicipalBill.objects.get_or_create(
            municipal_account=account,
            period=date(2026, 6, 1),
            defaults={
                "municipality": tenant,
                "opening_balance": spec["may_closing"],
                "closing_balance": spec["june_closing"],
                "charges": {"water_kl": 18, "rates": 800, "refuse": 220},
                "payments": {"received": 0},
                "source_extract": extract,
            },
        )

        for bill in (may_bill, june_bill):
            MunicipalLedgerEntry.objects.get_or_create(
                municipality=tenant,
                municipal_bill=bill,
                entry_date=bill.period,
                kind=MunicipalLedgerEntry.KIND_CHARGE,
                description=f"Water consumption {bill.period_label}",
                defaults={
                    "amount": Decimal("420.00"),
                    "raw_row": {"category": "water", "units": "kl"},
                    "source_extract": extract,
                },
            )

        for month, reading_kl in spec["readings"]:
            MeterReading.objects.get_or_create(
                municipal_account=account,
                reading_date=date(2026, month, 1),
                defaults={
                    "municipality": tenant,
                    "reading_kl": reading_kl,
                    "source_extract": extract,
                },
            )

        self.stdout.write(f"  {account.account_number} — {account.holder_name}")
