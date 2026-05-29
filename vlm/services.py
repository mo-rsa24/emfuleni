"""Service-layer functions for the vlm app.

Other apps MUST call into this module — never import models from
this app directly. See CLAUDE.md hard rules.

The interface (`extract_meter_reading`) is the single swappable function
from design note Q3: replace it (and the provider client) to switch from
Anthropic Sonnet 4.6 to GPT-vision, Gemini vision, or a self-hosted VLM.
The rest of the system does not notice.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import django_rq
from django.conf import settings
from pydantic import BaseModel, Field, ValidationError

from . import tasks
from .models import VlmExtraction


logger = logging.getLogger(__name__)


# --- Provider response shape -----------------------------------------------


class MeterReading(BaseModel):
    """The structured shape we ask the VLM to fill in."""

    reading_kl: int = Field(
        ge=0,
        description="The dial reading in kilolitres, as a whole integer.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Float in [0, 1] — your confidence in the reading.",
    )
    notes: str = Field(
        default="",
        description="Brief plain-English notes if the photo was ambiguous.",
    )


VLM_PROMPT = (
    "You are reading the dial of a residential water meter from a photograph. "
    "Return your answer as JSON with three fields:\n"
    "- reading_kl: the dial reading as a single integer in kilolitres (kℓ). "
    "If the dial shows a decimal portion, round down to the integer kℓ.\n"
    "- confidence: a float in [0, 1] representing how confident you are. "
    "Lower it if the image is blurry, partially obscured, lit poorly, or "
    "shows something that is not clearly a water meter.\n"
    "- notes: short notes — empty string if nothing to flag.\n"
    "If the image is not a water meter, set confidence to 0 and explain in notes."
)


# --- Public service surface ------------------------------------------------


def extract_meter_reading(evidence, *, client=None) -> VlmExtraction:
    """Read a meter photograph and persist the parsed result.

    The single swappable function (design note Q3). `client` is injectable
    so tests can substitute a fake `anthropic.Anthropic`-shaped object
    without monkey-patching the module. Returns the persisted
    `VlmExtraction` row regardless of outcome — a failed call still
    writes a `status=failed` row with the error in `raw_response.error`.

    Idempotency: this function uses `update_or_create` keyed on `evidence`,
    so a retry overwrites the prior attempt rather than stacking rows.
    """
    if client is None:
        client = _default_client()

    try:
        image_b64, media_type = _read_evidence_image(evidence)
    except OSError as exc:
        return _persist_failure(evidence, error=f"could not read file: {exc}")

    if client is None:
        return _persist_failure(
            evidence, error="ANTHROPIC_API_KEY not configured; skipped extraction"
        )

    try:
        response = client.messages.create(
            model=settings.VLM_MODEL,
            max_tokens=settings.VLM_MAX_OUTPUT_TOKENS,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": VLM_PROMPT},
                    ],
                }
            ],
        )
    except Exception as exc:  # noqa: BLE001 — provider may raise anything
        logger.exception("VLM call failed for evidence=%s", evidence.pk)
        return _persist_failure(evidence, error=str(exc))

    raw_text = _extract_text(response)
    clean_text = _strip_code_fences(raw_text)
    try:
        parsed = MeterReading.model_validate_json(clean_text)
    except (ValidationError, ValueError) as exc:
        logger.warning(
            "VLM returned unparseable JSON for evidence=%s: %s", evidence.pk, exc
        )
        return _persist_failure(
            evidence,
            error="VLM response could not be parsed as MeterReading JSON",
            raw_text=raw_text,
        )

    status = (
        VlmExtraction.STATUS_LOW_CONFIDENCE
        if parsed.confidence < settings.VLM_MIN_CONFIDENCE
        else VlmExtraction.STATUS_EXTRACTED
    )

    extraction, _ = VlmExtraction.objects.update_or_create(
        evidence=evidence,
        defaults={
            "municipality": evidence.municipality,
            "status": status,
            "reading_kl": parsed.reading_kl,
            "confidence": parsed.confidence,
            "model_name": settings.VLM_MODEL,
            "raw_response": {
                "model": settings.VLM_MODEL,
                "parsed": parsed.model_dump(),
                "text": raw_text,
            },
            "notes": parsed.notes,
        },
    )
    return extraction


def get_extraction_for_evidence(evidence) -> VlmExtraction | None:
    """Tenant-scoped read for downstream apps.

    Engine (Slice 7) will call this when assembling the reconciliation
    contract — never reaching into vlm.models directly.
    """
    return (
        VlmExtraction.objects.for_tenant(evidence.municipality)
        .filter(evidence=evidence)
        .first()
    )


def has_pending_extraction_in(evidence_iterable) -> bool:
    """True if any evidence in `evidence_iterable` has a pending VLM job.

    Walks the iterable in-process (no extra DB hit per item — caller's
    queryset already eagerly loaded). Used by portal to decide whether
    to keep HTMX-polling the evidence panel.
    """
    for ev in evidence_iterable:
        extr = getattr(ev, "vlm_extraction", None)
        if extr is not None and extr.status == VlmExtraction.STATUS_PENDING:
            return True
    return False


def enqueue_extraction(evidence):
    """Public RQ enqueue surface.

    Portal calls this from `record_evidence` when `kind=photo` so the
    extraction runs in the background. In tests RQ runs sync (settings
    `RQ_QUEUES.default.ASYNC=False`).
    """
    queue = django_rq.get_queue("default")
    return queue.enqueue(tasks.run_extraction, evidence.pk)


# --- Internals -------------------------------------------------------------


def _default_client():
    """Construct the default Anthropic client, or None if no key is set."""
    if not settings.ANTHROPIC_API_KEY:
        return None
    import anthropic

    return anthropic.Anthropic(
        api_key=settings.ANTHROPIC_API_KEY,
        timeout=settings.VLM_TIMEOUT_SECONDS,
    )


def _read_evidence_image(evidence) -> tuple[str, str]:
    """Return (base64_str, media_type) for the Evidence's stored file.

    Anthropic accepts image/jpeg, image/png, image/gif, image/webp. Anything
    else (heic, octet-stream from a misbehaving client) is uploaded as
    jpeg and we let the provider decide whether it can decode.
    """
    path = Path(evidence.file.path)
    with path.open("rb") as fh:
        data = fh.read()
    b64 = base64.standard_b64encode(data).decode("ascii")
    media_type = evidence.content_type or "image/jpeg"
    if media_type not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
        media_type = "image/jpeg"
    return b64, media_type


def _extract_text(response) -> str:
    """Pull the first text block out of an Anthropic Messages response."""
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "text":
            return getattr(block, "text", "") or ""
    return ""


def _strip_code_fences(text: str) -> str:
    """Strip markdown ```code fences``` if the model wrapped its JSON.

    Sonnet sometimes returns its JSON inside a ```json ... ``` block even
    when prompted for raw JSON — known model behavior. Long-term we should
    switch to `client.messages.parse(output_format=MeterReading)`, which is
    schema-enforced and side-steps fences entirely; until then, this strips
    them so the row lands as `extracted` instead of `failed`.
    """
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    # Drop the opening fence (e.g. "```json")
    lines = lines[1:]
    # Drop any trailing fence lines
    while lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


_ERROR_MAX_CHARS = 500


def _persist_failure(evidence, *, error: str, raw_text: str = "") -> VlmExtraction:
    # Cap the error string before persisting — provider exceptions
    # sometimes carry the full response body, which can include rate-limit
    # detail or org identifiers. POPIA §6 Q6 wants us cautious about
    # what we keep on disk.
    capped_error = error[:_ERROR_MAX_CHARS]
    extraction, _ = VlmExtraction.objects.update_or_create(
        evidence=evidence,
        defaults={
            "municipality": evidence.municipality,
            "status": VlmExtraction.STATUS_FAILED,
            "reading_kl": None,
            "confidence": None,
            "model_name": settings.VLM_MODEL,
            "raw_response": {
                "model": settings.VLM_MODEL,
                "error": capped_error,
                "text": raw_text[:_ERROR_MAX_CHARS],
            },
            "notes": "",
        },
    )
    return extraction
