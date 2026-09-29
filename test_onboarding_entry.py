# -*- coding: utf-8 -*-
"""The opening screen must offer new users a way in.

Measured context. Between 13 and 28 September the signup screen was opened 26
times against roughly 145 recorded package downloads. The landing page shipped
with exactly one button, labelled "Get Started", and it opened the sign-in
form: a password box for an account the visitor did not have. Registration was
reachable only by noticing a secondary control on the page after that one.

Also pinned here: the free allowance was stated nowhere in the shipped package,
so the offer was invisible until after registration; and the two optional
profile questions were not marked, so the form read as longer than it is.

Source-level tests. They check the wiring and the copy, not Qt behaviour,
which needs a running QGIS.
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


# ── a route to registration from the opening screen ───────────────────────

def test_create_account_route_is_installed_on_startup():
    b = body_of("_wire_signals")
    assert "_install_create_account_route()" in b, (
        "the account-creation route must be installed when signals are wired")


def test_create_account_button_goes_to_registration():
    b = body_of("_install_create_account_route")
    assert "btn_create_account" in b
    assert "self._go_to_signup" in b, (
        "the account-creation button must open registration, not sign-in")


def test_create_account_is_placed_before_the_sign_in_button():
    """Reading order is the whole point: a new user should meet registration
    first, not a password form."""
    b = body_of("_install_create_account_route")
    assert "insertWidget(idx, self.btn_create_account)" in b, (
        "the button must be inserted at the existing button's index so it "
        "reads first")


def test_existing_button_is_relabelled_not_removed():
    """Returning users still need sign-in, and it is the only route to it."""
    b = body_of("_install_create_account_route")
    assert "btn.setText(" in b
    assert "already have an account" in b.lower()
    assert "removeWidget" not in b and ".hide()" not in b, (
        "the sign-in route must not be removed from the opening screen")


def test_missing_ui_widget_does_not_crash_startup():
    """A stale .ui on someone's machine must not turn 'open the plugin' into an
    AttributeError, which is how a cosmetic change becomes a support incident."""
    b = body_of("_install_create_account_route")
    assert 'getattr(self, "btn_get_started", None)' in b
    assert b.count("return") >= 3, (
        "each missing-widget branch must return rather than continue")


# ── the free allowance, stated before registration ────────────────────────

def test_free_allowance_is_stated_on_the_opening_screen():
    b = body_of("_install_create_account_route")
    assert "200 images" in b, (
        "the included allowance must be stated before registration")


def test_allowance_copy_does_not_promise_a_trial_or_a_card():
    """It is an included allowance, not a trial, and there is no payment step
    at registration. Saying either would be a claim support has to unwind."""
    b = body_of("_install_create_account_route")
    low = b.lower()
    assert "free trial" not in low
    assert "credit card" not in low or "no card" in low


# ── optional fields ───────────────────────────────────────────────────────

def test_optional_fields_are_marked():
    b = body_of("_mark_optional_signup_fields")
    assert "label_sn_jobtitle" in b and "label_sn_org" in b
    assert b.lower().count("(optional)") >= 2, (
        "both optional questions must be marked")


def test_country_is_not_marked_optional():
    """The server rules on eligibility using country, so it is required.
    Marking it optional produces a refusal after submission."""
    b = body_of("_mark_optional_signup_fields")
    # No country widget is relabelled, and no label text produced here mentions
    # country. Checked against the (widget, text) pairs the method sets, so the
    # comment explaining why country is excluded does not trip the test.
    assert "combo_signup_country" not in b
    assert "label_sn_country" not in b
    pairs = re.findall(r'\("(label_sn_[a-z]+)",\s*"([^"]+)"\)', b)
    assert pairs, "the method no longer sets any label text"
    for widget, text in pairs:
        assert "country" not in widget.lower(), "country must not be relabelled"
        assert "country" not in text.lower()


def test_marking_is_applied_at_startup():
    b = body_of("_wire_signals")
    assert "_mark_optional_signup_fields()" in b


def test_marking_does_not_touch_validation():
    """Copy only. If this method ever starts changing submit behaviour, the
    label and the rule can disagree."""
    b = body_of("_mark_optional_signup_fields")
    for forbidden in ("setEnabled", "setRequired", "clicked.connect",
                      "_handle_signup", "setValidator"):
        assert forbidden not in b, (
            "%s changes behaviour; this method may only change labels" % forbidden)


# ── signing in before verifying ───────────────────────────────────────────

def test_premature_signin_is_still_handled_distinctly():
    """403 carries two meanings on this endpoint. Collapsing them would send a
    user refused on geography into a verification loop they cannot finish."""
    s = src()
    assert "COUNTRY_NOT_SUPPORTED" in s
    assert "_on_email_not_verified" in s


def test_premature_signin_message_is_actionable():
    b = body_of("_on_email_not_verified")
    low = b.lower()
    assert "verification" in low or "verify" in low
    assert "resend" in low, (
        "the refusal must offer the remedy, not just report the problem")


def test_already_verified_resend_response_is_preserved():
    """Telling someone to keep retrying something that already succeeded is
    worse than saying nothing; this wording was deliberate and must survive."""
    b = body_of("_resend_verification_email")
    assert "already verified" in b.lower()
    assert "forgot password" in b.lower(), (
        "the already-verified path must point at password recovery")


@pytest.mark.parametrize("name", ["btn_create_account", "label_free_allowance",
                                  "btn_verify_resend", "btn_verify_change_email"])
def test_new_widgets_are_attributes_not_locals(name):
    """Widgets held only in a local go out of scope and can be collected."""
    assert re.search(r"self\.%s\s*=" % name, src()), (
        "%s must be stored on the dialog" % name)
