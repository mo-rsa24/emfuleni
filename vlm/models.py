"""Models for the vlm app.

Owns `VlmExtraction` — the parsed output of a vision-language-model call
against an uploaded meter photograph. Per the data-model skill this is in
the "Derived (writable)" group: vlm is the only writer.

See .claude/skills/data-model/SKILL.md for the architectural rules.
"""

from __future__ import annotations

from django.db import models

from common.models import TenantTimestamped


class VlmExtraction(TenantTimestamped):
    """The result of a VLM call against one Evidence row.

    `raw_response` keeps the complete provider response (Anthropic JSON,
    or our stub envelope on failure) for audit and re-parsing. The
    parsed fields up top — `reading_kl`, `confidence` — are what
    downstream apps (engine in Slice 7) read.

    A failed extraction (network error, key missing, malformed response)
    is still persisted as a row with `status=failed` and the error
    captured in `raw_response.error`. Idempotency: one row per Evidence
    (OneToOneField) — re-running the extraction overwrites.
    """

    STATUS_PENDING = "pending"
    STATUS_EXTRACTED = "extracted"
    STATUS_LOW_CONFIDENCE = "low_confidence"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_EXTRACTED, "Extracted"),
        (STATUS_LOW_CONFIDENCE, "Low confidence (review)"),
        (STATUS_FAILED, "Failed"),
    ]

    evidence = models.OneToOneField(
        "portal.Evidence",
        on_delete=models.CASCADE,
        related_name="vlm_extraction",
    )
    status = models.CharField(
        max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING
    )
    reading_kl = models.PositiveIntegerField(null=True, blank=True)
    confidence = models.FloatField(null=True, blank=True)
    model_name = models.CharField(
        max_length=64,
        blank=True,
        help_text="Provider model identifier — e.g. claude-sonnet-4-6.",
    )
    raw_response = models.JSONField(default=dict)
    notes = models.TextField(blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["municipality", "status", "-created_at"]),
        ]

    def __str__(self):
        return f"VlmExtraction({self.status}, reading={self.reading_kl})"
