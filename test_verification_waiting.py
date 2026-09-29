# -*- coding: utf-8 -*-
"""After creating an account, the screen must not invite signing in.

Why this exists, measured rather than assumed. On production between 13 and 28
September, twelve accounts were created. Three of them attempted to sign in
within sixteen seconds of registering, and were refused because the emailed
link had not been opened yet. Nobody had done anything wrong: on success the
plugin called _go_to_signin and pre-filled the address, so the screen left in
front of the user was a password box and a Sign In button. A modal said to
check the inbox, and the screen underneath said to sign in.

Over the same sixteen days the resend endpoint received no requests at all,
while the only route to it was a button inside the dialog that appears after
the refusal has already happened.

These are source-level tests. They pin the routing decision and the absence of
a control the backend cannot support; they do not attempt to drive Qt, which
needs a running QGIS.
"""
import pathlib
import re

import pytest

HERE = pathlib.Path(__file__).resolve().parent
DIALOG = "atlas_geo_plugin_dialog.py"


def src():
    return (HERE / DIALOG).read_text(encoding="utf-8")


def body_of(func):
    s = src()
    assert ("def %s" % func) in s, "%s is missing" % func
    return s.split("def %s" % func, 1)[1].split("\n    def ", 1)[0]


# ── the routing decision ──────────────────────────────────────────────────

def test_signup_success_does_not_land_on_the_signin_form():
    """The specific regression: a new account must not be dropped onto a form
    whose only action fails until the email link is opened."""
    b = body_of("_on_signup_success")
    assert "_go_to_signin()" not in b, (
        "signup success routes to the sign-in form again; that is the flow "
        "that produced premature sign-in refusals")


def test_signup_success_routes_to_the_waiting_screen():
    b = body_of("_on_signup_success")
    assert "_go_to_verification_wait(" in b, (
        "signup success must route to the verification waiting screen")


def test_waiting_screen_exists_and_is_registered_on_the_stack():
    s = src()
    assert "def _build_verification_page" in s
    assert "PAGE_VERIFY_WAIT" in s
    b = body_of("_build_verification_page")
    assert "self.stacked_pages.addWidget(page)" in b, (
        "the page must be added to the stack, and its index taken from the "
        "stack rather than hardcoded")


def test_page_index_is_not_a_hardcoded_literal():
    """A fixed index would collide the day another page is added in Designer."""
    s = src()
    m = re.search(r"PAGE_VERIFY_WAIT\s*=\s*(.+)", s)
    assert m, "PAGE_VERIFY_WAIT is not declared"
    assert m.group(1).strip() == "None", (
        "PAGE_VERIFY_WAIT must start as None and be assigned from addWidget()")


# ── resend must be reachable BEFORE the mistake ───────────────────────────

def test_waiting_screen_offers_resend():
    b = body_of("_build_verification_page")
    assert "btn_verify_resend" in b
    assert "_on_verify_resend_clicked" in b, (
        "the resend button must be wired up on the waiting screen")


def test_resend_reports_its_outcome_on_the_page_that_asked():
    """Writing the result to the sign-in label while the user is looking at the
    waiting page reports success somewhere they cannot see it."""
    b = body_of("_resend_verification_email")
    assert "on_page" in b, "_resend_verification_email must know which screen called it"
    assert "_show_verify_status" in b
    # Exactly one sign-in write is allowed: the one inside the routing helper.
    # More than that means an outcome branch bypasses the router and would
    # report its result on a screen the user is not looking at.
    assert b.count("self._show_signin_error(") <= 1, (
        "%d branches write straight to the sign-in label; route them through "
        "the page-aware helper" % b.count("self._show_signin_error("))
    # and every outcome branch goes through the router
    assert b.count("_say(") >= 5, (
        "expected every resend outcome (sent, already verified, unknown "
        "address, other, exception) to route through _say")


def test_waiting_screen_offers_a_route_back_to_sign_in():
    b = body_of("_build_verification_page")
    assert "_go_to_signin" in b, (
        "the waiting screen must let a user who has verified continue to sign in")


# ── nothing may promise what the service cannot do ────────────────────────

def test_no_change_email_control_is_offered():
    """The service exposes no change-address endpoint. A button for it could
    only apologise, so it must not be built until the backend supports it.

    Checked against the widgets the page actually constructs, not the prose:
    the docstring explaining why the control is absent necessarily names it.
    """
    b = body_of("_build_verification_page")
    widgets = re.findall(r'Qt[A-Za-z]*\.Q(?:PushButton|CommandLinkButton|Label)'
                         r'\(\s*"([^"]*)"', b)
    for label in widgets:
        low = label.lower()
        for phrase in ("change email", "change address", "wrong email",
                       "edit email", "use a different"):
            assert phrase not in low, (
                "the waiting screen builds a control labelled %r, which has no "
                "backend support" % label)


def test_change_email_absence_is_documented_not_accidental():
    """If it is ever added, that should be a decision, not a drift."""
    b = body_of("_build_verification_page")
    assert "no endpoint" in b.lower() or "no \"change email address\"" in b.lower(), (
        "the reason the change-address control is absent must be recorded in "
        "the page that omits it")


def test_reused_link_case_is_explained_on_the_screen():
    """A single-use link that has already been opened is the most common
    confusion; the screen should say so rather than leave a bare error."""
    b = body_of("_build_verification_page")
    assert "only be used once" in b.lower()


@pytest.mark.parametrize("func", ["_go_to_verification_wait",
                                  "_on_verify_resend_clicked",
                                  "_show_verify_status"])
def test_supporting_methods_exist(func):
    assert ("def %s" % func) in src(), "%s is missing" % func
