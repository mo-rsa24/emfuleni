"""Seed the dev DB with Emfuleni ratepayers and their account links.

Depends on `seed_emfuleni` having run first (it provides the
`MunicipalAccount` rows that bind_account looks up). Idempotent — uses
get_or_create on (municipality, full_name) for the Ratepayer rows and
delegates link creation to the idempotent `identity.services.bind_account`.

Demonstrates the M2M shape from design note §6 Q5:
- three ratepayers who each own one account (typical homeowner case)
- one landlord ratepayer who owns two accounts (multi-property case)
- one web-only ratepayer with no MSISDN on file (id_last4 fallback)

Usage:
    python manage.py seed_emfuleni       # must run first
    python manage.py seed_ratepayers
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from common.models import Municipality
from identity.models import Ratepayer
from identity.services import bind_account


SEED_RATEPAYERS = [
    {
        "full_name": "Mrs N Dlamini",
        "msisdn": "+27820000001",
        "id_last4": None,
        "accounts": ["88231104"],
    },
    {
        "full_name": "Mr T Khumalo",
        "msisdn": "+27820000002",
        "id_last4": None,
        "accounts": ["88231205"],
    },
    {
        "full_name": "Ms B Modise",
        "msisdn": None,
        "id_last4": "4321",
        "accounts": ["88231306"],
    },
    {
        "full_name": "Mr S Mokoena",
        "msisdn": "+27820000004",
        "id_last4": None,
        "accounts": ["88231205", "88231306"],
    },
]


class Command(BaseCommand):
    help = "Seed the dev DB with Emfuleni ratepayers and account links."

    @transaction.atomic
    def handle(self, *args, **opts):
        try:
            emfuleni = Municipality.objects.get(slug="emfuleni")
        except Municipality.DoesNotExist as exc:
            raise CommandError(
                "Emfuleni municipality not found. Run `python manage.py "
                "seed_emfuleni` first."
            ) from exc

        for spec in SEED_RATEPAYERS:
            ratepayer, created = Ratepayer.objects.get_or_create(
                municipality=emfuleni,
                full_name=spec["full_name"],
                defaults={
                    "msisdn": spec["msisdn"],
                    "id_last4": spec["id_last4"],
                },
            )
            verb = "Created" if created else "Reused"
            self.stdout.write(f"{verb} ratepayer: {ratepayer}")

            for account_number in spec["accounts"]:
                link = bind_account(ratepayer, account_number)
                if link is None:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  Skipped: account {account_number} not found "
                            "under Emfuleni (did seed_emfuleni run?)"
                        )
                    )
                else:
                    self.stdout.write(f"  ↳ bound to {account_number}")

        self.stdout.write(self.style.SUCCESS("Ratepayer seed complete."))
