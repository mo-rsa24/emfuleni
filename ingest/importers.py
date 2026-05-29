"""CSV import — parse one extract file into upstream models.

The CSV schema is one row per (account_number, period). Each row creates
or refreshes a `MunicipalAccount`, the `MunicipalBill` for that period,
one summary `MunicipalLedgerEntry`, and the headline `MeterReading`. A
richer multi-file feed (separate ledger and reading drops) replaces this
when the real Emfuleni feed shape is known — until then this is one
believable shape per the design note §2.1.

Idempotency: every successful import creates one `Extract` row keyed by
`(municipality, content_hash)`. Re-importing the same file is a no-op
because the inbox adapter filters by hash before the file reaches here,
and the importer itself short-circuits if it sees the hash already.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from django.db import transaction
from django.utils import timezone as djtz

from common.models import Municipality

from .inbox import hash_file
from .models import (
    Extract,
    MeterReading,
    MunicipalAccount,
    MunicipalBill,
    MunicipalLedgerEntry,
)


REQUIRED_COLUMNS = {
    "account_number",
    "holder_name",
    "service_address",
    "account_class",
    "period",
    "opening_balance",
    "closing_balance",
    "water_kl",
    "rates",
    "refuse",
    "payments_received",
    "latest_meter_reading_kl",
    "latest_meter_reading_date",
}


class ExtractImportError(Exception):
    """The file could not be imported — schema mismatch or bad row."""


@dataclass
class ImportResult:
    extract: Extract
    accounts_touched: int
    bills_created: int
    ledger_entries_created: int
    meter_readings_created: int
    skipped_duplicate: bool = False


def import_extract_from_file(
    municipality: Municipality, path: Path
) -> ImportResult:
    """Import one CSV file as a new Extract under this tenant.

    Atomic per file — either every row in the file lands or none do.
    If an Extract with the same content_hash already exists under this
    tenant, returns a result with `skipped_duplicate=True` and no new rows.
    """
    content_hash = hash_file(path)

    existing = (
        Extract.objects.for_tenant(municipality)
        .filter(content_hash=content_hash)
        .first()
    )
    if existing is not None:
        return ImportResult(
            extract=existing,
            accounts_touched=0,
            bills_created=0,
            ledger_entries_created=0,
            meter_readings_created=0,
            skipped_duplicate=True,
        )

    rows = _read_csv(path)

    with transaction.atomic():
        # Privileged in-app writer (see TenantManager docstring): bare
        # .create()/.get_or_create() are allowed inside the app that owns
        # the table, as long as municipality= is explicit on every call.
        # Read paths still go through `for_tenant(...)`.
        extract = Extract.objects.create(
            municipality=municipality,
            filename=path.name,
            received_at=djtz.now(),
            content_hash=content_hash,
            row_count=len(rows),
            status=Extract.STATUS_RECEIVED,
        )

        counts = {"accounts": 0, "bills": 0, "ledger": 0, "readings": 0}
        for row in rows:
            _import_row(municipality, extract, row, counts)

        extract.status = Extract.STATUS_IMPORTED
        extract.save(update_fields=["status", "updated_at"])

    return ImportResult(
        extract=extract,
        accounts_touched=counts["accounts"],
        bills_created=counts["bills"],
        ledger_entries_created=counts["ledger"],
        meter_readings_created=counts["readings"],
    )


def _read_csv(path: Path) -> list[dict]:
    with path.open("r", newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = set(reader.fieldnames or [])
        missing = REQUIRED_COLUMNS - header
        if missing:
            raise ExtractImportError(
                f"CSV is missing required columns: {sorted(missing)}"
            )
        return list(reader)


def _import_row(
    tenant: Municipality, extract: Extract, row: dict, counts: dict
) -> None:
    account_number = row["account_number"].strip()
    if not account_number:
        raise ExtractImportError("Row has empty account_number")

    period = _parse_period(row["period"])

    account, account_created = MunicipalAccount.objects.update_or_create(
        municipality=tenant,
        account_number=account_number,
        defaults={
            "holder_name": row["holder_name"].strip(),
            "service_address": row["service_address"].strip(),
            "account_class": row["account_class"].strip(),
            "source_extract": extract,
        },
    )
    counts["accounts"] += 1 if account_created else 0

    bill, bill_created = MunicipalBill.objects.update_or_create(
        municipal_account=account,
        period=period,
        defaults={
            "municipality": tenant,
            "opening_balance": _money(row["opening_balance"]),
            "closing_balance": _money(row["closing_balance"]),
            "charges": {
                "water_kl": _int(row["water_kl"]),
                "rates": _money_str(row["rates"]),
                "refuse": _money_str(row["refuse"]),
            },
            "payments": {"received": _money_str(row["payments_received"])},
            "source_extract": extract,
        },
    )
    counts["bills"] += 1 if bill_created else 0

    _, ledger_created = MunicipalLedgerEntry.objects.get_or_create(
        municipality=tenant,
        municipal_bill=bill,
        entry_date=period,
        kind=MunicipalLedgerEntry.KIND_CHARGE,
        description=f"Period charges {period.strftime('%Y-%m')}",
        defaults={
            "amount": _money(row["closing_balance"]) - _money(row["opening_balance"]),
            "raw_row": dict(row),
            "source_extract": extract,
        },
    )
    counts["ledger"] += 1 if ledger_created else 0

    reading_date = _parse_iso_date(row["latest_meter_reading_date"])
    _, reading_created = MeterReading.objects.get_or_create(
        municipality=tenant,
        municipal_account=account,
        reading_date=reading_date,
        defaults={
            "reading_kl": _int(row["latest_meter_reading_kl"]),
            "source_extract": extract,
        },
    )
    counts["readings"] += 1 if reading_created else 0


def _parse_period(raw: str) -> date:
    """Accept 'YYYY-MM' or 'YYYY-MM-DD'. Normalises to day=1."""
    raw = raw.strip()
    for fmt in ("%Y-%m", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw, fmt).date()
            return parsed.replace(day=1)
        except ValueError:
            continue
    raise ExtractImportError(f"Bad period value: {raw!r}")


def _parse_iso_date(raw: str) -> date:
    raw = raw.strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ExtractImportError(f"Bad date value: {raw!r}") from exc


def _money(raw: str) -> Decimal:
    return Decimal(raw.strip() or "0")


def _money_str(raw: str) -> str:
    """JSONB-safe — store amounts as strings inside the charges/payments JSON
    so we never silently lose precision on JSON round-trips."""
    return str(_money(raw))


def _int(raw: str) -> int:
    return int(raw.strip() or "0")
