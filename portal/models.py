"""Models for the portal app.

Owns the `Evidence` model — files uploaded by ratepayers to back a
challenge. Per the data-model skill, this is in the "Derived (writable)"
group: portal is the only writer, other apps read via portal.services.

See .claude/skills/data-model/SKILL.md for the architectural rules.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from django.db import models

from common.models import TenantTimestamped


def evidence_upload_path(instance: "Evidence", filename: str) -> str:
    """Compute the relative path inside MEDIA_ROOT for an Evidence file.

    Layout: `<tenant_slug>/evidence/<uuid>.<ext>`.

    The tenant subdir keeps two tenants' evidence isolated at the
    filesystem layer (mirrors the inbox convention in Slice 3). The
    UUID prevents collisions and strips the user-supplied filename
    from disk, defusing path-traversal payloads ("../etc/passwd",
    "evil.png\\x00.exe") at the storage boundary. The original
    filename is preserved on the row as `original_filename` for the
    audit trail.
    """
    tenant_slug = instance.municipality.slug
    ext = Path(filename).suffix.lower().lstrip(".") or "bin"
    fresh = f"{uuid.uuid4().hex}.{ext}"
    return f"{tenant_slug}/evidence/{fresh}"


class Evidence(TenantTimestamped):
    """A file the ratepayer uploaded to back a bill challenge.

    Three kinds today (photo, csv, statement_pdf); the parsing of CSV
    and PDF is post-MVP, but the file is captured now so we never have
    to retrofit the storage. The VLM extraction job (Slice 6) reads
    Evidence rows where `kind == photo`.
    """

    KIND_PHOTO = "photo"
    KIND_CSV = "csv"
    KIND_STATEMENT_PDF = "statement_pdf"
    KIND_CHOICES = [
        (KIND_PHOTO, "Photograph (meter or property)"),
        (KIND_CSV, "Spreadsheet of readings (CSV)"),
        (KIND_STATEMENT_PDF, "Municipal statement (PDF)"),
    ]

    ratepayer = models.ForeignKey(
        "identity.Ratepayer",
        on_delete=models.CASCADE,
        related_name="evidence_uploaded",
    )
    municipal_account = models.ForeignKey(
        "ingest.MunicipalAccount",
        on_delete=models.PROTECT,
        related_name="evidence",
    )
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    file = models.FileField(upload_to=evidence_upload_path)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=80)
    size_bytes = models.PositiveIntegerField()

    class Meta:
        indexes = [
            models.Index(fields=["municipality", "ratepayer", "-created_at"]),
            models.Index(fields=["municipality", "municipal_account", "-created_at"]),
        ]

    def __str__(self):
        return f"Evidence({self.kind}, account={self.municipal_account.account_number})"
