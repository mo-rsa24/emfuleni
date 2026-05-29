"""Worker-only service surface for the portal app.

Separate from `portal/services.py` for one specific reason: this module
holds the few intentionally **tenant-agnostic** lookups that background
workers need. Keeping them out of `services.py` does two things:

1. It breaks an import cycle. The VLM worker imports portal to resolve
   an Evidence pk; portal.services imports vlm.services to enqueue the
   extraction. Hoisting the worker lookup here lets `portal.services`
   import `vlm.services` at module level (no inline imports).

2. It signals at the call site. `from portal.worker_services import …`
   means "I'm a background job that already has a trusted pk, no tenant
   in scope". A view code reviewer who sees this import in HTTP-path
   code can flag it immediately.

DO NOT import from this module in any code reachable from an HTTP
request. Use `portal.services.get_evidence(municipality, pk)` instead.
"""

from __future__ import annotations

from .models import Evidence


def get_evidence_by_pk(pk) -> Evidence | None:
    """Tenant-agnostic Evidence lookup by pk.

    Used by `vlm.tasks.run_extraction` and any other in-process worker
    that already has a trusted pk and no caller-supplied tenant. The
    returned Evidence carries `municipality` itself, so downstream
    queries can scope from there. Returns None on bad pk shape.
    """
    if pk in (None, ""):
        return None
    try:
        pk_int = int(pk)
    except (TypeError, ValueError):
        return None
    return Evidence.objects.filter(pk=pk_int).first()
