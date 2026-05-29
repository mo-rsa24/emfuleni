"""Portal views — the web channel surface for the ratepayer.

Implements design note §2.2: lookup account → verify OTP → see current bill.

All cross-app data access goes through `identity.services` or
`ingest.services`. The portal app does NOT import models from any other
app — the services boundary is the only way out.
"""

from __future__ import annotations

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from common.services import get_current_tenant
from identity import services as identity_services
from ingest import services as ingest_services

from .session import (
    clear_logged_in_ratepayer,
    get_logged_in_ratepayer,
    set_logged_in_ratepayer,
)


def _context(request: HttpRequest, **extra) -> dict:
    """Base template context — tenant + logged-in ratepayer (if any)."""
    tenant = get_current_tenant(request)
    return {
        "tenant": tenant,
        "ratepayer": get_logged_in_ratepayer(request, tenant),
        **extra,
    }


def home(request: HttpRequest) -> HttpResponse:
    return render(request, "portal/home.html", _context(request))


def lookup(request: HttpRequest) -> HttpResponse:
    """GET shows the form; POST issues an OTP and renders the verify page."""
    tenant = get_current_tenant(request)

    if request.method == "GET":
        return render(request, "portal/lookup.html", _context(request))

    account_number = request.POST.get("account_number", "").strip()
    if not account_number:
        return render(
            request,
            "portal/lookup.html",
            _context(request, error="Please enter your account number."),
        )

    account = ingest_services.get_account(tenant, account_number)
    if account is None:
        return render(
            request,
            "portal/lookup.html",
            _context(
                request,
                submitted_account_number=account_number,
                error="No matching account found. Check the number and try again.",
            ),
        )

    ratepayer = identity_services.primary_ratepayer_for_account(account)
    if ratepayer is None:
        return render(
            request,
            "portal/lookup.html",
            _context(
                request,
                submitted_account_number=account_number,
                error="That account is not yet bound to a ratepayer. Contact the municipality to register.",
            ),
        )

    identity_services.issue_otp(ratepayer)
    return render(
        request,
        "portal/verify.html",
        _context(request, ratepayer=ratepayer, account_number=account_number),
    )


@require_POST
def verify(request: HttpRequest) -> HttpResponse:
    """Consume an OTP. On success, set the session and go to the account page."""
    tenant = get_current_tenant(request)
    ratepayer_id = request.POST.get("ratepayer_id")
    code = request.POST.get("code", "").strip()
    account_number = request.POST.get("account_number", "").strip()

    ratepayer = identity_services.get_ratepayer(tenant, ratepayer_id)
    if ratepayer is None:
        # Session was tampered with or the ratepayer was deleted mid-flow.
        return redirect("portal:lookup")

    if not identity_services.verify_otp(ratepayer, code):
        return render(
            request,
            "portal/verify.html",
            _context(
                request,
                ratepayer=ratepayer,
                account_number=account_number,
                error="That code is incorrect or expired. Try again or restart the lookup.",
            ),
        )

    set_logged_in_ratepayer(request, ratepayer)
    account = ingest_services.get_account(tenant, account_number) if account_number else None
    if account is None:
        # Pick the ratepayer's first linked account as a fallback.
        account = identity_services.first_linked_account_for(ratepayer)
        if account is None:
            messages.info(request, "Verified — but no accounts are linked yet.")
            return redirect("portal:home")
    return redirect("portal:account_detail", account_id=account.pk)


def logout(request: HttpRequest) -> HttpResponse:
    clear_logged_in_ratepayer(request)
    return redirect("portal:home")


def account_detail(request: HttpRequest, account_id: int) -> HttpResponse:
    tenant = get_current_tenant(request)
    ratepayer = get_logged_in_ratepayer(request, tenant)
    if ratepayer is None:
        return redirect("portal:lookup")

    account = ingest_services.get_account_by_pk(tenant, account_id)
    if account is None or not identity_services.is_account_linked(ratepayer, account):
        # Same response shape for "no such account" and "exists but not yours" —
        # closes the 302-vs-404 distinction a logged-in attacker could probe.
        return redirect("portal:home")

    bill = ingest_services.get_latest_bill(account)
    return render(
        request,
        "portal/account_detail.html",
        _context(request, account=account, bill=bill),
    )


@require_POST
def challenge_stub(request: HttpRequest, account_id: int) -> HttpResponse:
    """HTMX partial response — Slice 5 replaces this with the evidence flow."""
    tenant = get_current_tenant(request)
    ratepayer = get_logged_in_ratepayer(request, tenant)
    if ratepayer is None:
        return HttpResponse(status=401)
    return render(request, "portal/partials/challenge_acknowledged.html")
