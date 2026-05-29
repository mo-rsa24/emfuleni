"""Models for the ingest app.

Upstream mirror of what we receive from the municipality via SFTP.
READ-ONLY in business logic — only this app writes to these tables, and
only when importing a new extract. See .claude/skills/data-model/SKILL.md.
"""

from django.db import models

from common.models import TenantTimestamped


class Extract(TenantTimestamped):
    """One SFTP drop. Every other upstream row FKs back to here.

    content_hash is the SHA-256 of the file body; used by the importer to
    skip re-importing an extract we've already seen (idempotency).
    """

    STATUS_RECEIVED = "received"
    STATUS_IMPORTED = "imported"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_RECEIVED, "received"),
        (STATUS_IMPORTED, "imported"),
        (STATUS_FAILED, "failed"),
    ]

    filename = models.CharField(max_length=255)
    received_at = models.DateTimeField()
    content_hash = models.CharField(max_length=64)
    row_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_RECEIVED)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["municipality", "content_hash"],
                name="ingest_extract_unique_content_per_tenant",
            ),
        ]
        indexes = [
            models.Index(fields=["municipality", "received_at"]),
        ]

    def __str__(self):
        return f"{self.filename} ({self.status})"


class MunicipalAccount(TenantTimestamped):
    """One row per municipal account number (a property / erf).

    `holder_name` is the name the municipality has on file — a plain string,
    NOT a FK to Ratepayer. The link to a Primeserve `Ratepayer` lives in
    `identity.RatepayerAccountLink` (see Slice 2).
    """

    account_number = models.CharField(max_length=32)
    holder_name = models.CharField(max_length=200)
    service_address = models.TextField()
    account_class = models.CharField(max_length=64)
    source_extract = models.ForeignKey(
        Extract, on_delete=models.PROTECT, related_name="accounts"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["municipality", "account_number"],
                name="ingest_municipalaccount_unique_account_per_tenant",
            ),
        ]
        indexes = [
            models.Index(fields=["municipality", "account_number"]),
        ]

    def __str__(self):
        return f"{self.account_number} — {self.holder_name}"


class MunicipalBill(TenantTimestamped):
    """One row per (account, period). Period uses day=1 convention.

    `charges` and `payments` are JSONB blocks holding the raw structured
    breakdown the municipality sent. The reconciled position lives in
    `ledger.ReconciledPosition`, not here.
    """

    municipal_account = models.ForeignKey(
        MunicipalAccount, on_delete=models.PROTECT, related_name="bills"
    )
    period = models.DateField(help_text="First day of the billing month (day=1).")
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2)
    closing_balance = models.DecimalField(max_digits=12, decimal_places=2)
    charges = models.JSONField(default=dict)
    payments = models.JSONField(default=dict)
    source_extract = models.ForeignKey(
        Extract, on_delete=models.PROTECT, related_name="bills"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["municipal_account", "period"],
                name="ingest_municipalbill_unique_period_per_account",
            ),
        ]
        indexes = [
            models.Index(fields=["municipality", "period"]),
        ]

    @property
    def period_label(self) -> str:
        return self.period.strftime("%Y-%m")

    def __str__(self):
        return f"{self.municipal_account.account_number} {self.period_label}"


class MunicipalLedgerEntry(TenantTimestamped):
    """A line item the municipality recorded against an account.

    `raw_row` keeps the original CSV row as JSONB so we can always reconstruct
    what arrived, even if we add/rename parsed fields later.
    """

    KIND_CHARGE = "charge"
    KIND_PAYMENT = "payment"
    KIND_JOURNAL = "journal"
    KIND_CHOICES = [
        (KIND_CHARGE, "charge"),
        (KIND_PAYMENT, "payment"),
        (KIND_JOURNAL, "journal"),
    ]

    municipal_bill = models.ForeignKey(
        MunicipalBill, on_delete=models.PROTECT, related_name="entries"
    )
    entry_date = models.DateField()
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    description = models.CharField(max_length=255)
    raw_row = models.JSONField(default=dict)
    source_extract = models.ForeignKey(
        Extract, on_delete=models.PROTECT, related_name="ledger_entries"
    )

    class Meta:
        indexes = [
            models.Index(fields=["municipality", "entry_date"]),
        ]

    def __str__(self):
        return f"{self.entry_date} {self.kind} {self.amount}"


class MeterReading(TenantTimestamped):
    """A reading the municipality has on file. Read-only mirror.

    Ratepayer-submitted readings live elsewhere — this model is the
    municipality's view only.
    """

    municipal_account = models.ForeignKey(
        MunicipalAccount, on_delete=models.PROTECT, related_name="meter_readings"
    )
    reading_date = models.DateField()
    reading_kl = models.PositiveIntegerField()
    source_extract = models.ForeignKey(
        Extract, on_delete=models.PROTECT, related_name="meter_readings"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["municipal_account", "reading_date"],
                name="ingest_meterreading_unique_date_per_account",
            ),
        ]
        indexes = [
            models.Index(fields=["municipality", "reading_date"]),
        ]

    def __str__(self):
        return f"{self.municipal_account.account_number} @ {self.reading_date}: {self.reading_kl} kℓ"
