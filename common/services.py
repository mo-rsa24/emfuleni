"""Service-layer functions for the common app.

Other apps MUST call into this module — never import models from
this app directly. See CLAUDE.md hard rules.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest

from .models import Municipality


def get_current_tenant(request: HttpRequest | None = None) -> Municipality:
    """Resolve the active tenant for a request.

    MVP: returns the single tenant named by `settings.DEFAULT_TENANT_SLUG`
    (Emfuleni). Slice 13 or a per-tenant URL scheme (Q7) will swap this
    for subdomain / path-prefix resolution. The signature already takes
    a request so callers don't have to change when that happens.
    """
    return Municipality.objects.get(slug=settings.DEFAULT_TENANT_SLUG)
