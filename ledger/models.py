"""Models for the ledger app.

Owns the writable source-of-truth for Primeserve's view of each
ratepayer's balance — `LedgerEntry` (append-only line items) and
`ReconciledPosition` (rolled-up per-period). `PaymentSnapshot` (the
immutable audit blob captured at payment time) lands in Slice 9.

Per the data-model skill this is the "Owned (writable)" group: `ledger`
is the ONLY app that writes here. Other apps read via `ledger.services`.

See .claude/skills/data-model/SKILL.md.
"""

from __future__ import annotations

from django.db import models

from common.models import TenantTimestamped


class LedgerEntry(TenantTimestamped):
    """Authoritative line item Primeserve holds for a (account, period).

    **Append-only.** A correction to a prior entry is a NEW row with a
    signed `amount` and an explanatory `description`; the prior row is
    never edited. This makes the entire balance history reconstructable
    by a `SUM(amount)` against the same (account, period).

    `kind` separates the few entry shapes the engine produces. Slice 9
    will add `payment` rows when the Ozow webhook fires.
    """

    KIND_OPENING = "opening"
    KIND_CHARGE_ACCEPTED = "charge_accepted"
    KIND_CHARGE_CORRECTED = "charge_corrected"
    KIND_PAYMENT_RECEIVED = "payment_received"
    KIND_CHOICES = [
        (KIND_OPENING, "Opening balance brought forward"),
        (KIND_CHARGE_ACCEPTED, "Charge accepted as billed"),
        (KIND_CHARGE_CORRECTED, "Charge corrected (engine adjustment)"),
        (KIND_PAYMENT_RECEIVED, "Payment received"),
    ]

    municipal_account = models.ForeignKey(
        "ingest.MunicipalAccount",
        on_delete=models.PROTECT,
        related_name="ledger_entries",
    )
    period = models.DateField(help_text="First day of the billing month.")
    kind = models.CharField(max_length=24, choices=KIND_CHOICES)
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Signed. Positive = ratepayer owes more; negative = correction or payment.",
    )
    description = models.CharField(max_length=255)

    class Meta:
        indexes = [
            models.Index(fields=["municipality", "municipal_account", "period"]),
        ]

    def __str__(self):
        return f"LedgerEntry({self.kind}, {self.amount})"


class ReconciledPosition(TenantTimestamped):
    """Rolled-up "what we say is owed right now" for one (account, period).

    Recomputed when underlying inputs change. The reconciliation contract
    is the projection assembled from this (plus findings + evidence) —
    `ReconciledPosition` itself is NOT the contract. It is just the
    cached top-line our_balance + delta so the portal can render without
    re-walking the ledger on every request.
    """

    municipal_account = models.ForeignKey(
        "ingest.MunicipalAccount",
        on_delete=models.PROTECT,
        related_name="reconciled_positions",
    )
    period = models.DateField()
    municipality_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="The closing balance from MunicipalBill at recompute time.",
    )
    our_balance = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="What we say the ratepayer owes after applying findings.",
    )
    delta = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="our_balance - municipality_balance. Signed.",
    )
    last_computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["municipal_account", "period"],
                name="ledger_reconciledposition_unique_per_period",
            ),
        ]
        indexes = [
            models.Index(fields=["municipality", "period"]),
        ]

    def __str__(self):
        return f"ReconciledPosition({self.municipal_account.account_number}, {self.period}, delta={self.delta})"
