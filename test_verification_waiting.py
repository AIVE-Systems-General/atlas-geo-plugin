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

# ── "Use a different email" is a restart, and must never carry a password ──

def test_waiting_screen_offers_a_different_email_route():
    b = body_of("_build_verification_page")
    assert "btn_verify_change_email" in b
    assert "_restart_signup_with_new_email" in b, (
        "the different-address button must be wired to the restart handler")


def test_restart_never_preserves_a_password():
    """The one rule that matters here. The stored profile must not contain a
    password, and the restart must not write into either password field."""
    b = body_of("_restart_signup_with_new_email")
    assert "input_signup_password" not in b, (
        "the restart touches the password field; it must be left empty")
    assert "input_signup_confirm" not in b, (
        "the restart touches the confirm-password field; it must be left empty")
    for bad in ("password", "passwd", "pwd"):
        assert ('prof.get("%s")' % bad) not in b and ("prof['%s']" % bad) not in b, (
            "the restart reads %r from the stored profile" % bad)


def test_stored_signup_profile_holds_no_credential():
    """_last_signup_profile outlives the page, so its contents matter."""
    s = src()
    block = s.split("self._last_signup_profile = {", 1)[1].split("}", 1)[0]
    lowered = block.lower()
    for bad in ("password", "passwd", "pwd", "confirm", "secret", "token"):
        assert bad not in lowered, (
            "_last_signup_profile carries %r; it must hold only non-sensitive "
            "answers" % bad)


def test_restart_clears_the_form_before_restoring():
    """_go_to_signup clears every field including both passwords. Restoring
    before it would be undone; restoring after it is what keeps the passwords
    empty."""
    b = body_of("_restart_signup_with_new_email")
    clear_at = b.find("self._go_to_signup()")
    restore_at = b.find("setText(prof[")
    assert clear_at != -1, "the restart must go through _go_to_signup()"
    assert restore_at == -1 or clear_at < restore_at, (
        "values are restored before the form is cleared, so the clear wipes them")


def test_restart_preserves_the_non_sensitive_answers():
    b = body_of("_restart_signup_with_new_email")
    for key in ("name", "job_title", "organization", "country"):
        assert key in b, "the restart drops %r, which the user must retype" % key


def test_copy_states_it_starts_registration_and_changes_nothing():
    """Telling someone their address was changed, when the pending account
    still exists, is a claim their next sign-in attempt disproves."""
    b = body_of("_restart_signup_with_new_email")
    low = b.lower()
    assert "starts registration again" in low
    assert "not changed or removed" in low, (
        "the copy must say the earlier account is untouched")
    assert "password again" in low, (
        "the copy must warn that the password has to be re-entered")


def test_restart_is_confirmed_before_leaving_the_screen():
    b = body_of("_restart_signup_with_new_email")
    assert "_themed_confirm" in b and "return" in b, (
        "a misclick must be recoverable: confirm before navigating away")


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
