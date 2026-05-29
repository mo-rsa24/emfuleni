"""Service-layer functions for the ingest app.

Other apps MUST call into this module — never import models from
this app directly. See CLAUDE.md hard rules.
"""

from common.models import Municipality

from .models import MunicipalAccount


def get_account(municipality: Municipality, account_number: str) -> MunicipalAccount | None:
    """Return the `MunicipalAccount` for (tenant, account_number), or None.

    Tenant-scoped: an account_number that exists only under a different
    municipality will return None, not leak across tenants.
    """
    return (
        MunicipalAccount.objects.for_tenant(municipality)
        .filter(account_number=account_number)
        .first()
    )
