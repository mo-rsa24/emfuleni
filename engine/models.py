"""Models for the engine app.

Owns `Finding` — what the reconciliation engine concluded for a given
(account, period). Per the data-model skill this is in the "Derived
(writable)" group: only `engine` writes findings. The reconciliation
**contract** itself is a projection — see the reconciliation-contract
skill — and is NOT a model.

See .claude/skills/data-model/SKILL.md for the architectural rules.
"""

from __future__ import annotations

from django.db import models

from common.models import TenantTimestamped


class Finding(TenantTimestamped):
    """One archetype-tagged conclusion about a (municipal_account, period).

    `archetype` names the rule that produced this finding
    (`estimate_cap_breach`, `tariff_misapplied`, etc.). `delta_amount` is
    signed — negative when we believe the municipality overcharged the
    ratepayer (so paying our number means a smaller bill).

    `evidence` is the Evidence row this finding cites. Nullable: some
    archetypes (e.g. tariff-only) might be derived from the bill alone
    with no ratepayer-supplied evidence. The contract assembler still
    expects a citation, so leaving evidence null means the citation
    field in the projection is `null` for that finding.
    """

    ARCHETYPE_ESTIMATE_CAP_BREACH = "estimate_cap_breach"
    ARCHETYPE_TARIFF_MISAPPLIED = "tariff_misapplied"
    ARCHETYPE_METER_READING_DISPUTED = "meter_reading_disputed"
    ARCHETYPE_CHOICES = [
        (ARCHETYPE_ESTIMATE_CAP_BREACH, "Estimate cap breach (MSA s95)"),
        (ARCHETYPE_TARIFF_MISAPPLIED, "Tariff misapplied"),
        (ARCHETYPE_METER_READING_DISPUTED, "Meter reading disputed"),
    ]

    municipal_account = models.ForeignKey(
        "ingest.MunicipalAccount",
        on_delete=models.PROTECT,
        related_name="findings",
    )
    period = models.DateField(help_text="First day of the billing month (day=1).")
    archetype = models.CharField(max_length=64, choices=ARCHETYPE_CHOICES)
    delta_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Signed. Negative = municipality appears to have overcharged.",
    )
    statutory_ref = models.CharField(
        max_length=64,
        blank=True,
        help_text="e.g. 'MSA s95' for estimate-cap findings.",
    )
    evidence = models.ForeignKey(
        "portal.Evidence",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="findings",
    )
    explanation = models.TextField(
        blank=True,
        help_text="Plain-English explanation safe to show the ratepayer.",
    )

    class Meta:
        indexes = [
            models.Index(fields=["municipality", "municipal_account", "period"]),
        ]

    def __str__(self):
        return f"Finding({self.archetype}, delta={self.delta_amount})"
