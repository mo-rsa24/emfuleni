"""Portal views — the web channel surface for the ratepayer.

Implements design note §2.2 (lookup + bill display) and §2.3 (evidence
upload).

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

from . import services as portal_services
from .services import EvidenceValidationError
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
def challenge_panel(request: HttpRequest, account_id: int) -> HttpResponse:
    """HTMX partial: opens the evidence upload form for this account."""
    account, _ = _resolve_account_for_request(request, account_id)
    if account is None:
        return HttpResponse(status=401)
    return render(
        request,
        "portal/partials/upload_form.html",
        _panel_context(account),
    )


@require_POST
def evidence_upload(request: HttpRequest, account_id: int) -> HttpResponse:
    """HTMX partial: accept a file, validate, persist, re-render the panel."""
    account, ratepayer = _resolve_account_for_request(request, account_id)
    if account is None:
        return HttpResponse(status=401)

    kind = request.POST.get("kind", "").strip()
    uploaded = request.FILES.get("file")
    if uploaded is None:
        ctx = _panel_context(account)
        ctx.update(error="Choose a file before uploading.", selected_kind=kind)
        return render(request, "portal/partials/upload_form.html", ctx)

    try:
        portal_services.record_evidence(
            ratepayer=ratepayer,
            account=account,
            kind=kind,
            uploaded_file=uploaded,
        )
    except EvidenceValidationError as exc:
        ctx = _panel_context(account)
        ctx.update(error=str(exc), selected_kind=kind)
        return render(request, "portal/partials/upload_form.html", ctx)

    ctx = _panel_context(account)
    ctx.update(success="Uploaded — saved against this account.")
    return render(request, "portal/partials/upload_form.html", ctx)


def evidence_list_panel(request: HttpRequest, account_id: int) -> HttpResponse:
    """HTMX partial: just the evidence list (used for live polling).

    While any photo extraction is still `pending`, the rendered fragment
    carries `hx-trigger="every 2s"` so HTMX self-polls. Once everything
    has settled (extracted / low_confidence / failed) the fragment is
    re-rendered WITHOUT a trigger and polling stops on the next swap.
    """
    account, _ = _resolve_account_for_request(request, account_id)
    if account is None:
        return HttpResponse(status=401)
    return render(
        request,
        "portal/partials/evidence_list.html",
        _panel_context(account),
    )


def _panel_context(account) -> dict:
    """Build the context the upload-form / evidence-list partials need.

    Centralised so callers (challenge_panel, evidence_upload,
    evidence_list_panel) emit consistent context — including the bill
    recap and the `any_pending` flag that drives HTMX polling.
    """
    from vlm import services as vlm_services

    evidence = portal_services.list_evidence_for_account(account)
    return {
        "account": account,
        "bill": ingest_services.get_latest_bill(account),
        "evidence_list": evidence,
        "any_pending": vlm_services.has_pending_extraction_in(evidence),
    }


def _resolve_account_for_request(request: HttpRequest, account_id: int):
    """Common pre-check for the account-scoped HTMX endpoints.

    Returns (account, ratepayer) on success; (None, None) when the caller
    isn't logged in or isn't linked to the account. Callers convert the
    None case into HttpResponse(status=401).
    """
    tenant = get_current_tenant(request)
    ratepayer = get_logged_in_ratepayer(request, tenant)
    if ratepayer is None:
        return None, None
    account = ingest_services.get_account_by_pk(tenant, account_id)
    if account is None or not identity_services.is_account_linked(ratepayer, account):
        return None, None
    return account, ratepayer
