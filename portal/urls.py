"""URL routes for the portal app — the web-channel surface."""

from django.urls import path

from . import views


app_name = "portal"

urlpatterns = [
    path("", views.home, name="home"),
    path("lookup/", views.lookup, name="lookup"),
    path("verify/", views.verify, name="verify"),
    path("logout/", views.logout, name="logout"),
    path("account/<int:account_id>/", views.account_detail, name="account_detail"),
    path(
        "account/<int:account_id>/challenge/",
        views.challenge_panel,
        name="challenge_panel",
    ),
    path(
        "account/<int:account_id>/evidence/upload/",
        views.evidence_upload,
        name="evidence_upload",
    ),
    path(
        "account/<int:account_id>/evidence/list/",
        views.evidence_list_panel,
        name="evidence_list_panel",
    ),
]
