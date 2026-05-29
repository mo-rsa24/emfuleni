"""Service-layer functions for the identity app.

Other apps MUST call into this module — never import models from
this app directly. See CLAUDE.md hard rules.
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from django.utils import timezone

from common.models import Municipality
from ingest import services as ingest_services

from .models import OtpCode, Ratepayer, RatepayerAccountLink


logger = logging.getLogger(__name__)


OTP_TTL = timedelta(minutes=5)
OTP_CODE_DIGITS = 6


def bind_account(
    ratepayer: Ratepayer, account_number: str
) -> RatepayerAccountLink | None:
    """Bind a Ratepayer to a MunicipalAccount by account number.

    Tenant comes from the Ratepayer — we look up the account under the
    same municipality the ratepayer belongs to. Returns the link, or None
    if no account with that number exists under this tenant. Idempotent:
    rebinding an already-bound account returns the existing link.
    """
    account = ingest_services.get_account(ratepayer.municipality, account_number)
    if account is None:
        return None

    link, _ = RatepayerAccountLink.objects.get_or_create(
        ratepayer=ratepayer,
        municipal_account=account,
        defaults={"municipality": ratepayer.municipality},
    )
    return link


def find_ratepayer_by_msisdn(
    municipality: Municipality, msisdn: str
) -> Ratepayer | None:
    """Look up a Ratepayer by MSISDN, tenant-scoped.

    The channel adapter (WhatsApp / USSD) knows which tenant it serves
    from its own config; it passes the municipality in. Returns None if
    no ratepayer with this MSISDN exists under this tenant.
    """
    return (
        Ratepayer.objects.for_tenant(municipality)
        .filter(msisdn=msisdn)
        .first()
    )


def get_ratepayer(municipality: Municipality, pk) -> Ratepayer | None:
    """Look up a Ratepayer by pk, tenant-scoped.

    Accepts any pk shape (int, str, None). Non-integer or missing pks
    return None rather than raising — callers (e.g. portal views with
    tampered form posts) get graceful Nones instead of 500s.
    """
    if pk in (None, ""):
        return None
    try:
        pk_int = int(pk)
    except (TypeError, ValueError):
        return None
    return (
        Ratepayer.objects.for_tenant(municipality)
        .filter(pk=pk_int)
        .first()
    )


def primary_ratepayer_for_account(account) -> Ratepayer | None:
    """Return the first ratepayer linked to this account, or None.

    MVP policy: "whoever bound first wins" when an account has multiple
    linked ratepayers (landlord + tenant). Slice 5+ may grow into a
    chooser. `account` is duck-typed (any object with `municipality`
    and pk works) — kept untyped here to avoid importing ingest.models.
    """
    return (
        Ratepayer.objects.for_tenant(account.municipality)
        .filter(account_links__municipal_account=account)
        .order_by("account_links__created_at")
        .first()
    )


def first_linked_account_for(ratepayer: Ratepayer):
    """Return the oldest-linked MunicipalAccount for this ratepayer, or None."""
    link = (
        RatepayerAccountLink.objects.for_tenant(ratepayer.municipality)
        .filter(ratepayer=ratepayer)
        .select_related("municipal_account")
        .order_by("created_at")
        .first()
    )
    return link.municipal_account if link else None


def is_account_linked(ratepayer: Ratepayer, account) -> bool:
    """Is `ratepayer` linked to `account`? Tenant-scoped by the link table."""
    return (
        RatepayerAccountLink.objects.for_tenant(ratepayer.municipality)
        .filter(ratepayer=ratepayer, municipal_account=account)
        .exists()
    )


def issue_otp(ratepayer: Ratepayer) -> OtpCode:
    """Create and persist a fresh OtpCode for this ratepayer.

    Stubbed delivery: the code is written to logs only. Real SMS
    delivery lands in Slices 10/11 alongside USSD. The caller
    (portal.views.lookup) decides what to show the user; the OtpCode
    row carries the actual code value for the test client to read.
    """
    code = "".join(secrets.choice("0123456789") for _ in range(OTP_CODE_DIGITS))
    otp = OtpCode.objects.create(
        municipality=ratepayer.municipality,
        ratepayer=ratepayer,
        code=code,
        expires_at=timezone.now() + OTP_TTL,
    )
    logger.info(
        "OTP issued: ratepayer=%s tenant=%s code=%s (stub delivery)",
        ratepayer.pk, ratepayer.municipality.slug, code,
    )
    return otp


def verify_otp(ratepayer: Ratepayer, code: str) -> bool:
    """Check `code` against this ratepayer's live OTPs. Consume on success.

    Returns True if the code matched a live (unexpired, unconsumed) OTP
    and was just consumed; False otherwise. Tenancy is implicit — only
    OTPs owned by this ratepayer are considered.

    Race-safe: the consume is a single conditional UPDATE. Two
    simultaneous correct submissions both compile the same WHERE
    (`consumed_at IS NULL AND expires_at > now()`); the first to
    commit flips `consumed_at`, the second's UPDATE matches zero rows.
    Postgres MVCC handles the rest.
    """
    code = code.strip()
    rows_updated = (
        OtpCode.objects.for_tenant(ratepayer.municipality)
        .filter(
            ratepayer=ratepayer,
            code=code,
            consumed_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .update(consumed_at=timezone.now())
    )
    return rows_updated > 0
