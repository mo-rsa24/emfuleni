"""Service-layer functions for the ingest app.

Other apps MUST call into this module — never import models from
this app directly. See CLAUDE.md hard rules.
"""

from __future__ import annotations

import django_rq

from common.models import Municipality

from . import tasks
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
