"""Background tasks for the ingest app. Enqueued via RQ.

Define tasks here. Other apps enqueue them by calling
ingest.services.enqueue_<task_name>(...) — never by importing the task
function directly.

These wrappers stay thin on purpose: the real logic lives in
`importers.py` and `inbox.py`, which are plain functions/classes that can
be tested without spinning up a Redis or a worker. The tasks below are
the RQ-callable entry points.
"""

from __future__ import annotations

import logging
from pathlib import Path

from common.models import Municipality

from .inbox import FileSystemInboxAdapter
from .importers import import_extract_from_file


logger = logging.getLogger(__name__)


def poll_inbox(municipality_id: int) -> dict:
    """List the tenant's inbox, enqueue an import job per new file.

    Runs inside a worker. Returns a small dict so RQ has something to log.
    """
    from . import services  # local to avoid a circular import at module load

    municipality = Municipality.objects.get(pk=municipality_id)
    adapter = FileSystemInboxAdapter()
    new_files = adapter.list_new_files(municipality)

    enqueued = 0
    for inbox_file in new_files:
        services.enqueue_import_file(municipality, str(inbox_file.path))
        enqueued += 1

    logger.info(
        "poll_inbox: tenant=%s scanned=%d enqueued=%d",
        municipality.slug, len(new_files), enqueued,
    )
    return {"scanned": len(new_files), "enqueued": enqueued}


def import_file(municipality_id: int, path: str) -> dict:
    """Import one extract file. Runs inside a worker.

    Idempotent — re-importing the same file is a no-op via the
    content_hash check in `import_extract_from_file`.
    """
    municipality = Municipality.objects.get(pk=municipality_id)
    result = import_extract_from_file(municipality, Path(path))

    logger.info(
        "import_file: tenant=%s file=%s skipped=%s accounts=%d bills=%d ledger=%d readings=%d",
        municipality.slug,
        path,
        result.skipped_duplicate,
        result.accounts_touched,
        result.bills_created,
        result.ledger_entries_created,
        result.meter_readings_created,
    )
    return {
        "extract_id": result.extract.pk,
        "skipped_duplicate": result.skipped_duplicate,
        "accounts_touched": result.accounts_touched,
        "bills_created": result.bills_created,
        "ledger_entries_created": result.ledger_entries_created,
        "meter_readings_created": result.meter_readings_created,
    }
