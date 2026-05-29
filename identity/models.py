"""Models for the identity app.

The writable Primeserve view of the human ratepayer (one row per person)
and their many-to-many link to municipal accounts. See design note §6 Q5.

These are owned writable models — only `identity` writes to them. Other
apps read through `identity.services`.
"""

from django.db import models
from django.utils import timezone

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


class OtpCode(TenantTimestamped):
    """A short-lived one-time code issued for ratepayer web auth (§6 Q5).

    Stored as a row (not in Redis) for the audit trail — POPIA wants us
    able to reconstruct who attempted login when. `consumed_at` is set on
    successful verify; codes are single-use. Expiry is enforced in service
    code, not DB — keeps the row available for later auditing even after
    the code is no longer usable.

    Production swap-out: the actual SMS delivery is stubbed by Slice 4
    (the code is written to logs); real SMS lands in Slices 10/11
    alongside the WhatsApp/USSD channels.
    """

    ratepayer = models.ForeignKey(
        Ratepayer, on_delete=models.CASCADE, related_name="otp_codes"
    )
    code = models.CharField(max_length=8)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["municipality", "ratepayer", "-created_at"]),
        ]

    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    def is_usable(self) -> bool:
        return not self.is_consumed() and not self.is_expired()

    def __str__(self):
        state = "consumed" if self.is_consumed() else ("expired" if self.is_expired() else "live")
        return f"OTP for {self.ratepayer.full_name} ({state})"
