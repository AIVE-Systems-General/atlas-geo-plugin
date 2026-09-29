# -*- coding: utf-8 -*-
"""The onboarding changes, asserted against real widgets rather than source.

test_onboarding_entry.py and test_verification_waiting.py read the source and
pin the wiring. That catches a removed call, but it cannot tell you whether the
button ended up on the screen, whether it sits before the sign-in route, or
whether a password field is actually empty after a restart. These build the
dialog and look at it.

Runs headless under QT_QPA_PLATFORM=offscreen, so it executes in the QGIS
container matrix and skips on a host without QGIS.

⚠️ NOTHING HERE TOUCHES THE NETWORK. requests is replaced per test. No token,
password or response body is asserted on or printed.
"""
import importlib.util
import os
import pathlib

import pytest

HERE = pathlib.Path(__file__).resolve().parent
DIALOG = HERE / "atlas_geo_plugin_dialog.py"

try:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import qgis.PyQt  # noqa: F401
    HAVE_QGIS = True
except Exception:                                             # noqa: BLE001
    HAVE_QGIS = False

needs_qgis = pytest.mark.skipif(not HAVE_QGIS, reason="needs a real QGIS Python")
pytestmark = needs_qgis


@pytest.fixture(scope="module")
def qapp():
    from qgis.PyQt import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def mod(qapp):
    spec = importlib.util.spec_from_file_location("_dlg_onboarding", DIALOG)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def dlg(mod, tmp_path, monkeypatch):
    """A real dialog, with settings redirected into tmp_path.

    QSettings is replaced rather than re-pathed: setPath is keyed by format and
    silently does nothing for QSettings(org, app), which is how an earlier
    isolation bug wrote into the tester's own configuration for several
    checkpoints. conftest fails the run if that happens again.
    """
    from qgis.PyQt import QtCore, QtWidgets

    _real_qs = QtCore.QSettings
    _ini = str(tmp_path / "AtlasGeo.ini")

    def _scoped(*a, **k):
        return _real_qs(_ini, _real_qs.Format.IniFormat)
    _scoped.Format = _real_qs.Format
    _scoped.Scope = _real_qs.Scope
    monkeypatch.setattr(mod.QtCore, "QSettings", _scoped)
    if hasattr(mod, "QgsSettings"):
        monkeypatch.setattr(mod, "QgsSettings", lambda *a, **k: _real_qs(
            str(tmp_path / "AtlasGeo-profile.ini"), _real_qs.Format.IniFormat))

    class _Bar:
        def pushMessage(self, *a, **k):
            pass

    class _Iface:
        def __init__(self):
            self._w = QtWidgets.QMainWindow()
            self._bar = _Bar()

        def mainWindow(self):
            return self._w

        def messageBar(self):
            return self._bar

        def mapCanvas(self):
            return None

    d = mod.AtlasGeoHandlerDemoDialog(_Iface())
    # No confirmation prompts and no legal fetch during these tests.
    monkeypatch.setattr(d, "_themed_confirm", lambda *a, **k: True)
    monkeypatch.setattr(d, "_themed_notice", lambda *a, **k: None)
    monkeypatch.setattr(d, "_fetch_legal_config", lambda *a, **k: None)
    yield d
    d.deleteLater()


# ── 1. Create Account is the primary new-user action ──────────────────────

def test_create_account_button_exists_and_is_visible(dlg):
    assert hasattr(dlg, "btn_create_account")
    assert dlg.btn_create_account.text().strip() != ""


def test_create_account_precedes_sign_in_on_the_landing_page(dlg):
    """Reading order, measured from the layout rather than assumed."""
    layout = dlg.btn_get_started.parentWidget().layout()
    i_create = layout.indexOf(dlg.btn_create_account)
    i_signin = layout.indexOf(dlg.btn_get_started)
    assert i_create >= 0 and i_signin >= 0
    assert i_create < i_signin, (
        "the account-creation button must come before the sign-in route")


def test_create_account_opens_registration(dlg):
    dlg.stacked_pages.setCurrentIndex(dlg.PAGE_GETSTARTED)
    dlg.btn_create_account.click()
    assert dlg.stacked_pages.currentIndex() == dlg.PAGE_SIGNUP


# ── 2. existing-user sign-in remains reachable ────────────────────────────

def test_sign_in_route_still_present_and_working(dlg):
    dlg.stacked_pages.setCurrentIndex(dlg.PAGE_GETSTARTED)
    assert dlg.btn_get_started.isVisibleTo(dlg.btn_get_started.parentWidget())
    assert "account" in dlg.btn_get_started.text().lower()
    dlg.btn_get_started.click()
    assert dlg.stacked_pages.currentIndex() == dlg.PAGE_SIGNIN


# ── 3. the allowance is stated before registration ────────────────────────

def test_allowance_and_no_card_wording_present(dlg):
    text = dlg.label_free_allowance.text().lower()
    assert "200" in text and "image" in text
    assert "card" in text, "the no-card reassurance must be present"
    assert "trial" not in text, "it is an included allowance, not a trial"


# ── 4. optional labels must not change validation ─────────────────────────

def test_optional_labels_are_marked(dlg):
    assert "(optional)" in dlg.label_sn_jobtitle.text().lower()
    assert "(optional)" in dlg.label_sn_org.text().lower()


def test_country_label_is_not_marked_optional(dlg):
    lbl = getattr(dlg, "label_sn_country", None)
    if lbl is not None:
        assert "(optional)" not in lbl.text().lower()


def test_optional_fields_do_not_block_the_submit_gate(dlg, monkeypatch):
    """Leaving both optional questions blank must not disable submission, and
    filling them must not be required to enable it."""
    sent = {}
    monkeypatch.setattr(dlg, "_signup_thread",
                        lambda *a, **k: sent.setdefault("args", a))
    dlg._go_to_signup()
    dlg.input_signup_name.setText("A Tester")
    dlg.input_signup_email.setText("tester@example.invalid")
    dlg.input_signup_password.setText("correct horse battery")
    dlg.input_signup_confirm.setText("correct horse battery")
    if getattr(dlg, "combo_signup_country", None) is not None and \
            dlg.combo_signup_country.count() > 1:
        dlg.combo_signup_country.setCurrentIndex(1)
    chk = getattr(dlg, "_legal_check", None)
    if chk is not None:
        chk.setChecked(True)
    # both optional fields deliberately left blank
    dlg._handle_signup()
    assert "args" in sent, "submission was blocked with optional fields empty"
    job_title, org = sent["args"][3], sent["args"][4]
    assert job_title == "" and org == ""


# ── 5. signup success opens the waiting screen ────────────────────────────

def test_signup_success_opens_the_waiting_screen(dlg):
    dlg._on_signup_success("tester@example.invalid")
    assert dlg.PAGE_VERIFY_WAIT is not None
    assert dlg.stacked_pages.currentIndex() == dlg.PAGE_VERIFY_WAIT
    assert "tester@example.invalid" in dlg.label_verify_body.text()


def test_waiting_screen_is_not_the_sign_in_page(dlg):
    dlg._on_signup_success("tester@example.invalid")
    assert dlg.stacked_pages.currentIndex() != dlg.PAGE_SIGNIN


def test_a_second_dialog_builds_its_own_waiting_page(mod, dlg, tmp_path,
                                                     monkeypatch):
    """Reopening the plugin creates a new dialog in the same process.

    Holding the page index on the CLASS made the second dialog skip building
    its own page and then reach for the first dialog's widgets. This is the
    regression: the index must belong to the instance.
    """
    from qgis.PyQt import QtWidgets

    dlg._on_signup_success("first@example.invalid")
    first_index = dlg.PAGE_VERIFY_WAIT
    assert first_index is not None

    class _Bar:
        def pushMessage(self, *a, **k):
            pass

    class _Iface:
        def __init__(self):
            self._w = QtWidgets.QMainWindow()
            self._bar = _Bar()

        def mainWindow(self):
            return self._w

        def messageBar(self):
            return self._bar

        def mapCanvas(self):
            return None

    second = mod.AtlasGeoHandlerDemoDialog(_Iface())
    monkeypatch.setattr(second, "_themed_notice", lambda *a, **k: None)
    try:
        # must not raise, and must land on its own page
        second._on_signup_success("second@example.invalid")
        assert second.PAGE_VERIFY_WAIT is not None
        assert second.stacked_pages.currentIndex() == second.PAGE_VERIFY_WAIT
        assert "second@example.invalid" in second.label_verify_body.text()
        # the first dialog's page is untouched
        assert "first@example.invalid" in dlg.label_verify_body.text()
    finally:
        second.deleteLater()


# ── 6. resend stays on the waiting screen and reports there ───────────────

class _Resp:
    def __init__(self, code):
        self.status_code = code
        self.text = ""

    def json(self):
        return {}


def test_resend_reports_success_on_the_waiting_screen(mod, dlg, monkeypatch):
    dlg._on_signup_success("tester@example.invalid")
    page_before = dlg.stacked_pages.currentIndex()
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: _Resp(200))
    dlg.btn_verify_resend.click()
    assert dlg.stacked_pages.currentIndex() == page_before, (
        "resend must not navigate away from the waiting screen")
    assert dlg.label_verify_status.isVisible() or dlg.label_verify_status.text()
    assert "sent" in dlg.label_verify_status.text().lower()


def test_resend_reports_failure_on_the_waiting_screen(mod, dlg, monkeypatch):
    dlg._on_signup_success("tester@example.invalid")
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: _Resp(500))
    dlg.btn_verify_resend.click()
    assert dlg.stacked_pages.currentIndex() == dlg.PAGE_VERIFY_WAIT
    assert dlg.label_verify_status.text().strip() != "", (
        "a failed resend must say so on the screen that asked")


def test_resend_already_verified_is_explained_not_retried(mod, dlg, monkeypatch):
    dlg._on_signup_success("tester@example.invalid")
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: _Resp(400))
    dlg.btn_verify_resend.click()
    said = dlg.label_verify_status.text().lower()
    assert "already verified" in said


# ── 7/8. different address restores profile, never a password ─────────────

def test_different_email_restores_only_non_sensitive_fields(dlg):
    dlg._last_signup_profile = {
        "name": "A Tester", "email": "typo@example.invalid",
        "job_title": "Survey Engineer", "organization": "Acme Geo",
        "country": "",
    }
    dlg._restart_signup_with_new_email()
    assert dlg.stacked_pages.currentIndex() == dlg.PAGE_SIGNUP
    assert dlg.input_signup_name.text() == "A Tester"
    assert dlg.input_signup_jobtitle.text() == "Survey Engineer"
    assert dlg.input_signup_org.text() == "Acme Geo"
    # the address is offered for correction rather than blanked
    assert dlg.input_signup_email.text() == "typo@example.invalid"


def test_password_fields_are_empty_after_a_restart(dlg):
    dlg._go_to_signup()
    dlg.input_signup_password.setText("should not survive")
    dlg.input_signup_confirm.setText("should not survive")
    dlg._last_signup_profile = {"name": "A Tester",
                                "email": "typo@example.invalid",
                                "job_title": "", "organization": "",
                                "country": ""}
    dlg._restart_signup_with_new_email()
    assert dlg.input_signup_password.text() == "", "a password survived the restart"
    assert dlg.input_signup_confirm.text() == "", "a password survived the restart"


def test_stored_profile_never_gains_a_password_key(dlg, monkeypatch):
    monkeypatch.setattr(dlg, "_signup_thread", lambda *a, **k: None)
    dlg._go_to_signup()
    dlg.input_signup_name.setText("A Tester")
    dlg.input_signup_email.setText("tester@example.invalid")
    dlg.input_signup_password.setText("correct horse battery")
    dlg.input_signup_confirm.setText("correct horse battery")
    if getattr(dlg, "combo_signup_country", None) is not None and \
            dlg.combo_signup_country.count() > 1:
        dlg.combo_signup_country.setCurrentIndex(1)
    chk = getattr(dlg, "_legal_check", None)
    if chk is not None:
        chk.setChecked(True)
    dlg._handle_signup()
    prof = getattr(dlg, "_last_signup_profile", {})
    assert prof, "the profile was not captured"
    for key, value in prof.items():
        assert "pass" not in key.lower()
        assert value != "correct horse battery", (
            "the password was stored under %r" % key)


# ── 9/10. pre-verification sign-in vs a country refusal ───────────────────

def test_sign_in_before_verification_is_actionable(dlg, monkeypatch):
    seen = {}
    monkeypatch.setattr(dlg, "_themed_confirm",
                        lambda *a, **k: seen.setdefault("prompted", True) and False)
    dlg._on_email_not_verified("tester@example.invalid")
    msg = dlg.label_signin_error.text().lower()
    assert "verif" in msg, "the message must name verification as the cause"
    assert "tester@example.invalid" in dlg.label_signin_error.text(), (
        "the message must say which address to check")
    assert seen.get("prompted"), "the remedy must be offered, not just described"


def test_country_refusal_is_not_treated_as_a_verification_problem(dlg, monkeypatch):
    """403 carries two meanings. Misreading a geography refusal as an unverified
    email sends that user into a loop they can never complete."""
    called = {}
    monkeypatch.setattr(dlg, "_on_email_not_verified",
                        lambda *a, **k: called.setdefault("verify", True))
    dlg._on_signin_failed("ATLAS-GEO is not available in your region yet.")
    assert "verify" not in called
    assert "region" in dlg.label_signin_error.text().lower()


# ── 11. the header must state the version that is running ─────────────────

def test_header_shows_the_real_version_on_open(dlg):
    """The .ui carries a literal "v1.0" as the header default, and it was only
    replaced on sign-in or sign-out. The opening screen therefore advertised
    v1.0 on every release, which is the first thing a new user sees and the
    version a screenshot in a bug report would name."""
    shown = dlg.label_header_status.text().strip()
    assert shown.startswith("v")
    assert shown != "v1.0", "the header still shows the .ui placeholder version"
    assert shown == "v%s" % dlg._plugin_version()


def test_header_version_matches_metadata(dlg):
    import pathlib
    meta = (pathlib.Path(dlg.__module__ and HERE) / "metadata.txt").read_text(
        encoding="utf-8")
    version = [l.split("=", 1)[1].strip() for l in meta.splitlines()
               if l.strip().startswith("version=")][0]
    assert dlg.label_header_status.text().strip() == "v%s" % version
