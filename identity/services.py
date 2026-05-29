"""Service-layer functions for the identity app.

Other apps MUST call into this module — never import models from
this app directly. See CLAUDE.md hard rules.
"""

from common.models import Municipality
from ingest import services as ingest_services

from .models import Ratepayer, RatepayerAccountLink


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
