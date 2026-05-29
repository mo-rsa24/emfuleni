"""Models for the common app.

Holds the tenant root (Municipality) and the abstract base class
(TenantTimestamped) that every domain model in the project must inherit.

See .claude/skills/data-model/SKILL.md for the architectural rules.
"""

from django.db import models


class TenantManager(models.Manager):
    """Manager that scopes querysets to a single municipality.

    Every domain model uses this. The rule splits by direction:

    - **Reads** — ALWAYS go through `.for_tenant(municipality)`. Bare
      `.objects.filter(...)` / `.objects.get(...)` / `.objects.all()` on
      a domain model is a tenancy violation.
    - **Writes** — `.create()`, `.update_or_create()`, and `.get_or_create()`
      may be called on `.objects` directly, AS LONG AS `municipality=` is
      explicit in the call kwargs (or in `defaults` for the *_or_create
      variants). This carve-out exists because Django's `QuerySet.create()`
      does NOT propagate filter conditions from `for_tenant()` into the
      new row — so `for_tenant(t).create(...)` would silently produce a
      `municipality_id=NULL` IntegrityError. Writes are confined to the
      app that owns the table (per the source-of-truth rule in CLAUDE.md),
      which means the privileged writer always knows its tenant.

    Tests follow the read rule for assertions and the write rule for
    fixture construction.
    """

    def for_tenant(self, municipality):
        return self.get_queryset().filter(municipality_id=municipality)


class Municipality(models.Model):
    """The tenant root. One row per municipality we onboard.

    Exempt from TenantTimestamped because it IS the tenant — every other
    domain model points back to a row here via municipality_id.
    """

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "municipalities"

    def __str__(self):
        return self.name


class TenantTimestamped(models.Model):
    """Abstract base for every domain model.

    Provides the municipality_id tenant column, created_at / updated_at
    timestamps, and the tenant-scoped manager. Inheriting models get
    the column auto-named `municipality_id` by Django's FK convention.
    """

    municipality = models.ForeignKey(
        Municipality, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TenantManager()

    class Meta:
        abstract = True
        indexes = [models.Index(fields=["municipality"])]
