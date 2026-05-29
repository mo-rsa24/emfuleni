"""Models for the common app.

Holds the tenant root (Municipality) and the abstract base class
(TenantTimestamped) that every domain model in the project must inherit.

See .claude/skills/data-model/SKILL.md for the architectural rules.
"""

from django.db import models


class TenantManager(models.Manager):
    """Manager that scopes querysets to a single municipality.

    Every domain model uses this. Bare .objects.filter() on a domain
    model is a tenancy violation — always go through .for_tenant().
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
