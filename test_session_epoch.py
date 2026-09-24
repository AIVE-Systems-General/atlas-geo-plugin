# -*- coding: utf-8 -*-
"""The in-flight-refresh race: a result may only be applied to its own session.

A refresh request can be in flight for up to 15 seconds. In that window the
user can log out, unload the plugin, or log out and sign in as somebody else.
When the old response finally lands it must be discarded completely: it must
not restore tokens, re-arm the timer, reset failure counters, show a
session-expired message, or touch a destroyed dialog.

⚠️ CHECKING `access_token is not None` IS NOT SUFFICIENT, and that is the whole
reason an epoch exists. After logout-then-new-sign-in the tokens are not None;
they belong to a different person. A None-check would happily overwrite the new
user's credentials with the previous user's.

Every test here is deterministic: the refresh response is held on an Event, the
session-changing action happens while it is held, and only then is it released.
No sleeps decide the outcome.
"""
import pathlib
import threading
import time

import pytest

HERE = pathlib.Path(__file__).resolve().parent
DIALOG = HERE / "atlas_geo_plugin_dialog.py"

try:
    import os as _os
    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import qgis.PyQt  # noqa: F401
    HAVE_QGIS = True
except Exception:                                             # noqa: BLE001
    HAVE_QGIS = False

needs_qgis = pytest.mark.skipif(not HAVE_QGIS, reason="needs a real QGIS Python")


@pytest.fixture(scope="module")
def qapp():
    from qgis.PyQt import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def mod(qapp):
    import importlib.util
    spec = importlib.util.spec_from_file_location("_dlg_epoch", DIALOG)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


@pytest.fixture
def dlg(mod, qapp, monkeypatch, tmp_path):
    from qgis.PyQt import QtWidgets, QtCore
    for name in ("warning", "critical", "information", "question", "about"):
        monkeypatch.setattr(QtWidgets.QMessageBox, name,
                            staticmethod(lambda *a, **k:
                                         QtWidgets.QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QtWidgets.QDialog, "exec", lambda self, *a, **k: 0)
    # ⚠️ QSettings.setPath DOES NOT REDIRECT QSettings(org, app).
    #
    # It is keyed BY FORMAT, and QSettings(org, app) resolves through
    # NativeFormat; Qt then caches the resolved file for the life of the
    # process. This call looked like isolation while writing to the real
    # ~/.config/AIVE/AtlasGeo.conf. Replacing the settings objects the plugin
    # constructs is unambiguous. QgsSettings is covered too: the report
    # directory is stored there, and it is backed by the QGIS profile.
    _real_qs = QtCore.QSettings
    _app_ini = str(tmp_path / "AtlasGeo-app.ini")
    _prof_ini = str(tmp_path / "AtlasGeo-profile.ini")

    def _scoped_qsettings(*a, **k):
        return _real_qs(_app_ini, _real_qs.Format.IniFormat)

    _scoped_qsettings.Format = _real_qs.Format
    _scoped_qsettings.Scope = _real_qs.Scope
    monkeypatch.setattr(mod.QtCore, "QSettings", _scoped_qsettings)
    if hasattr(mod, "QgsSettings"):
        monkeypatch.setattr(
            mod, "QgsSettings",
            lambda *a, **k: _real_qs(_prof_ini, _real_qs.Format.IniFormat))

    class _Bar:
        def __init__(self):
            self.pushed = []

        def pushMessage(self, *a, **k):
            self.pushed.append((a, k))

    class _Iface:
        def __init__(self):
            self._w = QtWidgets.QMainWindow()
            self._bar = _Bar()

        def mainWindow(self):
            return self._w

        def messageBar(self):
            return self._bar

    d = mod.AtlasGeoHandlerDemoDialog(_Iface())
    d.access_token = "access-USER-A"
    d.refresh_token = "refresh-USER-A"
    d.current_user_email = "user-a@example.invalid"
    # Every blocking Event a test parks a worker on is registered here, so
    # teardown can release it even if the test fails before doing so itself.
    d._test_release_events = []
    d._threads_at_start = {t.ident for t in threading.enumerate()}
    yield d

    # ── teardown: no worker may outlive the test that started it ─────────
    #
    # Leaked daemon threads do not just waste time: when released they call
    # whatever requests.post currently points at, which is the NEXT test's
    # stub. That produced a Qt5-only failure that looked like a concurrency
    # bug in the plugin and was not.
    for _ev in d._test_release_events:
        _ev.set()
    d.shutdown_session()

    _deadline = time.time() + 10.0
    _leaked = []
    for _t in threading.enumerate():
        if _t.ident in d._threads_at_start or _t is threading.current_thread():
            continue
        _t.join(timeout=max(0.0, _deadline - time.time()))
        if _t.is_alive():
            _leaked.append(_t.name)
    assert not _leaked, (
        f"test leaked live worker threads: {_leaked}. Register the blocking "
        f"Event on dlg._test_release_events so teardown can release it.")


class _HeldRefresh:
    """A refresh response frozen until the test releases it."""

    def __init__(self, status=200, payload=None):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.status = status
        self.payload = payload if payload is not None else {
            "access_token": "access-STALE-A", "refresh_token": "refresh-STALE-A"}

    def post(self, url, **kw):
        self.entered.set()
        assert self.release.wait(timeout=10), "test never released the response"
        return _Resp(self.status, self.payload)

    def start(self, dlg):
        # Registered so fixture teardown releases it even if this test fails.
        dlg._test_release_events.append(self.release)
        worker = threading.Thread(target=dlg._proactive_refresh_thread, daemon=True)
        worker.start()
        assert self.entered.wait(timeout=5), "the refresh worker never issued a request"
        return worker

    def finish(self, worker, qapp):
        self.release.set()
        worker.join(timeout=10)
        assert not worker.is_alive(), "the refresh worker did not finish"
        qapp.processEvents()


def _session_messages(dlg):
    return [a for a, k in dlg.iface.messageBar().pushed
            if "session expired" in str(a).lower()]


# ══════════════════════════════════════════════════════════════════════════
# 1. explicit logout while a refresh is in flight
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_logout_during_inflight_refresh_discards_the_result(mod, dlg, monkeypatch, qapp):
    held = _HeldRefresh()
    monkeypatch.setattr(mod.requests, "post", held.post)

    worker = held.start(dlg)

    # the user logs out while the request is still open
    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(200))
    dlg._logout_thread()
    qapp.processEvents()
    assert dlg.access_token is None

    monkeypatch.setattr(mod.requests, "post", held.post)
    held.finish(worker, qapp)

    assert dlg.access_token is None, "a stale refresh restored tokens after logout"
    assert dlg.refresh_token is None
    assert not dlg._session_timer.isActive(), "the timer was re-armed after logout"
    assert _session_messages(dlg) == [], "a stale session-expired message was shown"


@needs_qgis
def test_logout_bumps_the_epoch_before_clearing_tokens(mod, dlg, monkeypatch, qapp):
    """Ordering matters: if the epoch were bumped after the tokens were
    cleared, a refresh landing in between would write them back."""
    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(200))
    before = dlg._auth_epoch
    dlg._logout_thread()
    qapp.processEvents()
    assert dlg._auth_epoch > before, "logout did not invalidate in-flight refreshes"


# ══════════════════════════════════════════════════════════════════════════
# 2. unload / dialog shutdown while a refresh is in flight
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_unload_during_inflight_refresh_discards_the_result(mod, dlg, monkeypatch, qapp):
    held = _HeldRefresh()
    monkeypatch.setattr(mod.requests, "post", held.post)
    worker = held.start(dlg)

    dlg.shutdown_session()             # the plugin is unloaded mid-request
    held.finish(worker, qapp)

    assert dlg.access_token == "access-USER-A", \
        "shutdown does not clear tokens, but the stale result must not change them"
    assert not dlg._session_timer.isActive(), "the timer was re-armed after unload"
    assert _session_messages(dlg) == []


@needs_qgis
def test_no_callback_touches_a_destroyed_dialog(mod, dlg, monkeypatch, qapp):
    """After shutdown, the worker must not marshal anything back."""
    held = _HeldRefresh()
    monkeypatch.setattr(mod.requests, "post", held.post)
    worker = held.start(dlg)

    invoked = []
    original = dlg._invoke_on_gui
    monkeypatch.setattr(dlg, "_invoke_on_gui",
                        lambda slot, *a: (invoked.append(slot), original(slot, *a))[1])

    dlg.shutdown_session()
    held.finish(worker, qapp)

    assert invoked == [], f"the worker marshalled {invoked} after shutdown"


@needs_qgis
def test_invoke_on_gui_survives_a_destroyed_object(mod, dlg):
    """The guard must report failure, not raise, if the C++ side is gone."""
    dlg._shutting_down = False

    def boom(*a, **k):
        raise RuntimeError("wrapped C/C++ object has been deleted")

    from qgis.PyQt import QtCore
    original = QtCore.QMetaObject.invokeMethod
    try:
        QtCore.QMetaObject.invokeMethod = staticmethod(boom)
        assert dlg._invoke_on_gui("_apply_refresh_schedule") is False
        assert dlg._shutting_down is True, "the guard should latch after a failure"
    finally:
        QtCore.QMetaObject.invokeMethod = original


# ══════════════════════════════════════════════════════════════════════════
# 3. a NEW sign-in started while the old refresh was in flight
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_new_signin_tokens_are_never_overwritten_by_a_stale_refresh(
        mod, dlg, monkeypatch, qapp):
    """The case a None-check cannot catch.

    User A's refresh is in flight. A logs out, B signs in. When A's response
    lands, the tokens are NOT None, so a None-check would pass and B would
    silently be given A's credentials.
    """
    held = _HeldRefresh(payload={"access_token": "access-STALE-A",
                                 "refresh_token": "refresh-STALE-A"})
    monkeypatch.setattr(mod.requests, "post", held.post)
    worker = held.start(dlg)

    # user A logs out, user B signs in, all while A's refresh is open
    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(200))
    dlg._logout_thread()
    qapp.processEvents()

    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(
        200, {"access_token": "access-USER-B", "refresh_token": "refresh-USER-B",
              "email_verified": True}))
    dlg._signin_thread("user-b@example.invalid", "irrelevant")
    qapp.processEvents()
    assert dlg.access_token == "access-USER-B", "user B did not sign in"

    monkeypatch.setattr(mod.requests, "post", held.post)
    held.finish(worker, qapp)

    assert dlg.access_token == "access-USER-B", (
        "user A's in-flight refresh overwrote user B's access token")
    assert dlg.refresh_token == "refresh-USER-B", (
        "user A's in-flight refresh overwrote user B's refresh token")
    assert dlg.access_token != "access-STALE-A"
    assert _session_messages(dlg) == [], \
        "user B was told their session expired because user A's refresh failed"


@needs_qgis
def test_stale_terminal_refresh_does_not_expire_the_new_session(
        mod, dlg, monkeypatch, qapp):
    """User A's refresh comes back 401. User B is now signed in. B must not be
    signed out because A's credential was revoked."""
    held = _HeldRefresh(status=401, payload={"error": "invalid_grant"})
    monkeypatch.setattr(mod.requests, "post", held.post)
    worker = held.start(dlg)

    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(
        200, {"access_token": "access-USER-B", "refresh_token": "refresh-USER-B",
              "email_verified": True}))
    dlg._signin_thread("user-b@example.invalid", "irrelevant")
    qapp.processEvents()

    monkeypatch.setattr(mod.requests, "post", held.post)
    held.finish(worker, qapp)

    assert dlg._session_expired_handled is False, \
        "a stale 401 tore down the new user's session"
    assert dlg.access_token == "access-USER-B"
    assert _session_messages(dlg) == []


@needs_qgis
def test_signin_bumps_the_epoch(mod, dlg, monkeypatch, qapp):
    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(
        200, {"access_token": "A", "refresh_token": "R", "email_verified": True}))
    before = dlg._auth_epoch
    dlg._signin_thread("someone@example.invalid", "irrelevant")
    qapp.processEvents()
    assert dlg._auth_epoch > before


# ══════════════════════════════════════════════════════════════════════════
# 4. the mechanism itself
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_attempt_refresh_reports_stale_when_the_epoch_moves(mod, dlg, monkeypatch):
    """Directly: bump the epoch from inside the request."""
    def bumping_post(url, **kw):
        # A REAL production transition, not a synthetic epoch bump: this is a
        # logout landing while the request is open.
        dlg._clear_session(forget_email=False)
        return _Resp(200, {"access_token": "access-STALE",
                           "refresh_token": "refresh-STALE"})

    monkeypatch.setattr(mod.requests, "post", bumping_post)
    assert dlg._attempt_refresh() == dlg.REFRESH_STALE
    # The logout cleared the tokens. The point is that the stale response did
    # NOT write its values back over that.
    assert dlg.access_token is None, "a stale result was applied after logout"
    assert dlg.refresh_token is None


@needs_qgis
def test_expiry_teardown_bumps_the_epoch(mod, dlg):
    before = dlg._auth_epoch
    dlg._notify_session_expired()
    dlg._on_session_expired()
    assert dlg._auth_epoch > before


@needs_qgis
def test_notify_with_a_stale_epoch_is_ignored(mod, dlg):
    stale_epoch = dlg._auth_epoch
    # A new user signs in; the old session's epoch is now stale.
    dlg._install_session("access-B", "refresh-B", 300, email="b@example.invalid")
    dlg._notify_session_expired(epoch=stale_epoch)
    assert dlg._session_expired_handled is False, \
        "a teardown fired for a session that had already been replaced"


@needs_qgis
def test_stale_result_does_not_reset_the_failure_counter(mod, dlg, monkeypatch, qapp):
    """A stale success must not reset the backoff of whatever replaced it.

    A real transition legitimately clears the counter: a new session starts
    with a clean slate. What must not happen is user A's late worker zeroing
    the counter that now belongs to user B.
    """
    held = _HeldRefresh()
    monkeypatch.setattr(mod.requests, "post", held.post)
    worker = held.start(dlg)

    # User B signs in while user A's refresh is still open.
    dlg._install_session("access-B", "refresh-B", 300, email="b@example.invalid")
    assert dlg._refresh_failures == 0, "a new session starts with a clean backoff"

    # B's session then accumulates transient failures of its own.
    dlg._refresh_failures = 7

    held.finish(worker, qapp)      # user A's stale success finally lands

    assert dlg._refresh_failures == 7, \
        "user A's stale success reset user B's backoff state"
    assert dlg.access_token == "access-B", "the stale result was applied"


def test_source_does_not_rely_on_a_none_check_alone():
    """Code reference: the guards must compare epochs, not just tokens."""
    src = DIALOG.read_text(encoding="utf-8")
    assert "self._auth_epoch != epoch_on_entry" in src, \
        "the refresh path no longer verifies the authentication epoch"
    assert src.count("self._auth_epoch != epoch_on_entry") >= 2, \
        "the epoch must be checked on entry AND immediately before mutating"
    # Every production session change goes through the atomic helpers, which
    # bump the epoch and mutate the tokens in one critical section. A bare
    # bump-then-clear would reopen the window this guards.
    assert "_install_session(" in src, \
        "sign-in no longer installs the session atomically"
    assert "_clear_session(" in src, \
        "logout/expiry no longer clear the session atomically"
    assert "_auth_state_lock" in src, \
        "the dedicated auth-state lock is gone"
