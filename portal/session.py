"""Portal session helpers — custom (non-Django-auth) ratepayer sessions.

The portal does NOT use Django's auth.User. After a successful OTP
verify, `set_logged_in_ratepayer` stashes the Ratepayer pk on
`request.session`; `get_logged_in_ratepayer` reads it back through
`identity.services.get_ratepayer` — never touching identity's models
directly. Logout clears the key.

Why custom: the design note (§6 Q5) authenticates by account number +
OTP, not by username/password. Django's auth machinery (passwords,
permissions, admin overlap) is unused weight here. ~30 lines beats
fighting `User`'s assumptions.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpRequest

from common.models import Municipality
from identity import services as identity_services


def set_logged_in_ratepayer(request: HttpRequest, ratepayer) -> None:
    request.session[settings.PORTAL_SESSION_KEY] = ratepayer.pk


def clear_logged_in_ratepayer(request: HttpRequest) -> None:
    request.session.pop(settings.PORTAL_SESSION_KEY, None)


def get_logged_in_ratepayer(request: HttpRequest, tenant: Municipality):
    """Return the Ratepayer on session, or None.

    Tenant-scoped: a session that points at a ratepayer under another
    tenant returns None (defensive; should never happen in MVP since
    the tenant is fixed, but the check costs nothing).
    """
    pk = request.session.get(settings.PORTAL_SESSION_KEY)
    return identity_services.get_ratepayer(tenant, pk)
