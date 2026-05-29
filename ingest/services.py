"""Service-layer functions for the ingest app.

Other apps MUST call into this module — never import models from
this app directly. See CLAUDE.md hard rules.
"""

from __future__ import annotations

import django_rq

from common.models import Municipality

from . import tasks
from .models import MunicipalAccount, MunicipalBill


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


def get_account_by_pk(municipality: Municipality, pk) -> MunicipalAccount | None:
    """Return the `MunicipalAccount` for (tenant, pk), or None.

    Used by views that have a numeric pk from a URL pattern. Non-integer
    pks return None instead of raising.
    """
    if pk in (None, ""):
        return None
    try:
        pk_int = int(pk)
    except (TypeError, ValueError):
        return None
    return (
        MunicipalAccount.objects.for_tenant(municipality)
        .filter(pk=pk_int)
        .first()
    )


def get_bill(account: MunicipalAccount, period) -> MunicipalBill | None:
    """Return the `MunicipalBill` for (account, period), or None.

    Period is a date with day=1 convention. Tenancy is enforced by the
    account FK — bills are reached only through their account, which is
    already tenant-scoped.
    """
    return (
        MunicipalBill.objects.for_tenant(account.municipality)
        .filter(municipal_account=account, period=period)
        .first()
    )


def get_latest_bill(account: MunicipalAccount) -> MunicipalBill | None:
    """Return the most recent `MunicipalBill` for this account, or None."""
    return (
        MunicipalBill.objects.for_tenant(account.municipality)
        .filter(municipal_account=account)
        .order_by("-period")
        .first()
    )


def enqueue_poll(municipality: Municipality):
    """Enqueue a poll of the tenant's SFTP inbox.

    The actual work runs in an RQ worker. In tests RQ runs sync (see
    settings.RQ_QUEUES default ASYNC flag).
    """
    queue = django_rq.get_queue("default")
    return queue.enqueue(tasks.poll_inbox, municipality.pk)


def enqueue_import_file(municipality: Municipality, path: str):
    """Enqueue an import of one extract file. Called by the poller per
    new file it finds, but also callable directly if you want to import
    a specific file out-of-band."""
    queue = django_rq.get_queue("default")
    return queue.enqueue(tasks.import_file, municipality.pk, path)
