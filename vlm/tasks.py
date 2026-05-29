"""Background tasks for the vlm app. Enqueued via RQ.

Define tasks here. Other apps enqueue them by calling
vlm.services.enqueue_<task_name>(...) — never by importing the task
function directly.

The task is a thin wrapper around `extract_meter_reading`. Real logic
stays in services so it can be unit-tested without spinning up Redis.
"""

from __future__ import annotations

import logging

from portal import worker_services as portal_worker


logger = logging.getLogger(__name__)


def run_extraction(evidence_id: int) -> dict:
    """Run VLM extraction on one Evidence row. Runs inside an RQ worker.

    Looks the Evidence up via `portal.worker_services.get_evidence_by_pk`
    (the deliberately tenant-agnostic worker lookup — see that module's
    docstring). The tenant comes back on the Evidence row itself.
    Returns a small dict so the RQ job result is human-readable.
    """
    from . import services  # local import to dodge circular-load at startup

    evidence = portal_worker.get_evidence_by_pk(evidence_id)
    if evidence is None:
        logger.warning("run_extraction: evidence %s not found", evidence_id)
        return {"status": "skipped", "reason": "evidence_not_found"}

    extraction = services.extract_meter_reading(evidence)
    logger.info(
        "run_extraction: evidence=%s status=%s reading=%s confidence=%s",
        evidence_id,
        extraction.status,
        extraction.reading_kl,
        extraction.confidence,
    )
    return {
        "extraction_id": extraction.pk,
        "status": extraction.status,
        "reading_kl": extraction.reading_kl,
        "confidence": extraction.confidence,
    }
