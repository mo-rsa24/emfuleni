"""Models for the identity app.

The writable Primeserve view of the human ratepayer (one row per person)
and their many-to-many link to municipal accounts. See design note §6 Q5.

These are owned writable models — only `identity` writes to them. Other
apps read through `identity.services`.
"""

from django.db import models

from common.models import TenantTimestamped


class Ratepayer(TenantTimestamped):
    """A person who ratepays in one municipality.

    A landlord with five erven is ONE Ratepayer with five
    RatepayerAccountLink rows — not five Ratepayers.

    MSISDN is nullable: web-first contact may not surrender it until they
    bind a WhatsApp or USSD channel. id_last4 is the fallback identity per
    Q5 when no MSISDN is on file. Full OTP / session state is added in
    Slice 4 when the portal auth views land.
    """

    full_name = models.CharField(max_length=200)
    msisdn = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        help_text="E.164 phone number, e.g. +27821234567. Nullable until learned.",
    )
    id_last4 = models.CharField(
        max_length=4,
        null=True,
        blank=True,
        help_text="Last 4 digits of ID. Fallback identifier for ratepayers with no mobile on file.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["municipality", "msisdn"],
                condition=models.Q(msisdn__isnull=False),
                name="identity_ratepayer_unique_msisdn_per_tenant",
            ),
        ]
        indexes = [
            models.Index(fields=["municipality", "msisdn"]),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.msisdn or 'no-msisdn'})"


class RatepayerAccountLink(TenantTimestamped):
    """Binds one Ratepayer to one MunicipalAccount.

    Cross-app FK to ingest.MunicipalAccount: this is a real schema
    relationship, not a query-time cross-app import — Django resolves
    `"ingest.MunicipalAccount"` lazily so identity does not import
    ingest's Python module.
    """

    ratepayer = models.ForeignKey(
        Ratepayer, on_delete=models.CASCADE, related_name="account_links"
    )
    municipal_account = models.ForeignKey(
        "ingest.MunicipalAccount",
        on_delete=models.PROTECT,
        related_name="ratepayer_links",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ratepayer", "municipal_account"],
                name="identity_link_unique_per_ratepayer_account",
            ),
        ]
        indexes = [
            models.Index(fields=["municipality", "municipal_account"]),
        ]

    def __str__(self):
        return f"{self.ratepayer.full_name} → {self.municipal_account.account_number}"
