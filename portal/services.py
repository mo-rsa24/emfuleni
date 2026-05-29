"""Service-layer functions for the portal app.

Other apps MUST call into this module — never import models from
this app directly. See CLAUDE.md hard rules.

Slice 5 adds the first portal-owned write surface: `record_evidence`.
The portal app was read-only until now; from this slice on it owns
Evidence and is the only writer.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile

from common.models import Municipality

from .models import Evidence


class EvidenceValidationError(ValueError):
    """Raised when an upload fails size / MIME / extension checks.

    Callers (views) should catch this and surface the message — it's
    safe to show to the ratepayer; nothing in it leaks server state.
    """


def record_evidence(
    *,
    ratepayer,
    account,
    kind: str,
    uploaded_file: UploadedFile,
) -> Evidence:
    """Persist an uploaded file as an Evidence row.

    Validates kind, size, and MIME/extension against
    `settings.EVIDENCE_KIND_RULES` before touching the filesystem.
    The Evidence model's `upload_to` callable strips the user-supplied
    filename — only the cleaned UUID-named file lands on disk — so
    path-traversal payloads die at the storage boundary. The original
    filename is kept on the row for audit.

    Tenant is derived from the ratepayer; the caller has already
    enforced the ratepayer-is-linked-to-account check at the view layer.
    """
    if kind not in settings.EVIDENCE_KIND_RULES:
        raise EvidenceValidationError(f"Unknown evidence kind: {kind!r}.")

    rules = settings.EVIDENCE_KIND_RULES[kind]
    _validate_size(uploaded_file)
    _validate_extension(uploaded_file.name, rules["allowed_exts"])
    _validate_mime(uploaded_file.content_type, rules["allowed_mimes"])

    evidence = Evidence.objects.create(
        municipality=ratepayer.municipality,
        ratepayer=ratepayer,
        municipal_account=account,
        kind=kind,
        file=uploaded_file,
        original_filename=uploaded_file.name[:255],
        content_type=(uploaded_file.content_type or "")[:80],
        size_bytes=uploaded_file.size,
    )

    # Slice 6 auto-trigger: every photo enqueues a VLM extraction. CSV and
    # PDF kinds skip this — Slice 7 will parse those when the engine needs them.
    if kind == Evidence.KIND_PHOTO:
        from vlm import services as vlm_services

        vlm_services.enqueue_extraction(evidence)

    return evidence


def get_evidence_by_pk(pk) -> Evidence | None:
    """Tenant-agnostic Evidence lookup by pk for in-process workers.

    **WORKER-ONLY.** This is the only deliberately tenant-agnostic read
    service in the project. Use it ONLY from background jobs that already
    have a trusted pk and no caller-supplied tenant (e.g.
    `vlm.tasks.run_extraction`). The returned Evidence carries
    `municipality` itself, so downstream queries can scope from there.

    DO NOT call this from view code or any code reachable from an HTTP
    request — use `get_evidence(municipality, pk)` (tenant-scoped) instead.
    Returns None on bad pk shape.
    """
    if pk in (None, ""):
        return None
    try:
        pk_int = int(pk)
    except (TypeError, ValueError):
        return None
    return Evidence.objects.filter(pk=pk_int).first()


def get_evidence(municipality: Municipality, pk) -> Evidence | None:
    """Look up an Evidence row by pk, tenant-scoped.

    Used by downstream apps (vlm, engine) to read a ratepayer's uploads
    without crossing into portal's model graph directly.
    """
    if pk in (None, ""):
        return None
    try:
        pk_int = int(pk)
    except (TypeError, ValueError):
        return None
    return (
        Evidence.objects.for_tenant(municipality)
        .filter(pk=pk_int)
        .first()
    )


def list_evidence_for_account(account) -> list[Evidence]:
    """All evidence rows for one account, newest first."""
    return list(
        Evidence.objects.for_tenant(account.municipality)
        .filter(municipal_account=account)
        .order_by("-created_at")
    )


def _validate_size(uploaded_file: UploadedFile) -> None:
    if uploaded_file.size > settings.EVIDENCE_MAX_BYTES:
        mb = settings.EVIDENCE_MAX_BYTES // (1024 * 1024)
        raise EvidenceValidationError(
            f"File is too large — please keep uploads under {mb} MB."
        )
    if uploaded_file.size == 0:
        raise EvidenceValidationError("The file is empty.")


def _validate_extension(filename: str, allowed_exts: tuple[str, ...]) -> None:
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in allowed_exts:
        nice = ", ".join("." + e for e in allowed_exts)
        raise EvidenceValidationError(
            f"That file type isn't accepted for this kind. Allowed: {nice}."
        )


def _validate_mime(content_type: str | None, allowed_mimes: tuple[str, ...]) -> None:
    # Browsers vary; some send "image/jpg" or empty. Be lenient on absence,
    # strict on a wrong-but-present type that doesn't match any allowed.
    if not content_type:
        return
    # Strip any "; charset=..." suffix.
    base = content_type.split(";", 1)[0].strip().lower()
    if base not in {m.lower() for m in allowed_mimes}:
        raise EvidenceValidationError(
            "The file's reported content type doesn't match the chosen kind."
        )
