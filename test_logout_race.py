# -*- coding: utf-8 -*-
"""Regression tests for the logout / session-expiry race found in RC4.

Reported from manual testing:

    sign in -> stay signed in a minute or two -> click Log out
    -> "Logged out successfully."
    -> shortly afterwards -> "Your session expired. Please sign in again."

An explicit logout must NEVER be followed by a session-expiry message.

What actually happened: the plugin refreshes the balance whenever its window
regains focus, and clicking "Log out" in the confirmation dialog closes that
dialog and hands focus straight back. So the click started an authenticated
request and the logout at the same instant. The request came back 401 after the
session was gone, found no refresh token, read that as a terminal expiry, and
announced it. Three calls to _notify_session_expired passed no epoch at all, so
the guard built for exactly this never ran, and an explicit logout never closed
the expiry latch.

Every test here fails against RC4 unless marked otherwise. Nothing touches the
network: requests is replaced per-test. No token, password or response body is
asserted on or printed.
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
    spec = importlib.util.spec_from_file_location("_dlg_logout_race", DIALOG)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Resp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = ""

    def json(self):
        return dict(self._payload)


class _Bar:
    def __init__(self):
        self.pushed = []

    def pushMessage(self, *a, **k):
        title = a[0] if a else ""
        text = a[1] if len(a) > 1 else ""
        self.pushed.append(f"{title}: {text}")

    # ---- the two assertions every test in this file makes ----------------
    def logout_messages(self):
        return [m for m in self.pushed if "logged out" in m.lower()]

    def expiry_messages(self):
        return [m for m in self.pushed if "session expired" in m.lower()]


@pytest.fixture
def dlg(mod, qapp, monkeypatch, tmp_path):
    from qgis.PyQt import QtWidgets, QtCore

    for name in ("warning", "critical", "information", "question", "about"):
        monkeypatch.setattr(QtWidgets.QMessageBox, name,
                            staticmethod(lambda *a, **k:
                                         QtWidgets.QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QtWidgets.QDialog, "exec", lambda self, *a, **k: 0)
    QtCore.QSettings.setPath(QtCore.QSettings.Format.IniFormat,
                             QtCore.QSettings.Scope.UserScope, str(tmp_path))

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
    d._install_session("access-ORIGINAL", "refresh-ORIGINAL", 300,
                       email="tester@example.invalid")
    d._set_remember_email(True)
    d._save_remembered_email()
    d._test_release_events = []
    d._threads_at_start = {t.ident for t in threading.enumerate()}
    yield d

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
    assert not _leaked, f"test leaked live worker threads: {_leaked}"


# ══════════════════════════════════════════════════════════════════════════
# helpers
# ══════════════════════════════════════════════════════════════════════════

def _pump(qapp, seconds=0.4):
    """Deliver queued slots. The tear-down is queued, so a test that did not
    pump could pass simply by never letting the banner be delivered."""
    end = time.time() + seconds
    while time.time() < end:
        qapp.processEvents()
        time.sleep(0.01)


def assert_clean_logout(dlg, qapp, expect_email="tester@example.invalid"):
    """Every assertion the brief asks for, in one place.

    Applied identically to all ten scenarios so no case can quietly check less
    than another.
    """
    _pump(qapp)
    bar = dlg.iface.messageBar()
    assert len(bar.logout_messages()) == 1, \
        f"expected exactly one successful-logout message, got {bar.pushed}"
    assert bar.expiry_messages() == [], \
        f"an explicit logout must never report a session expiry: {bar.pushed}"
    # tokens gone
    assert dlg.access_token is None
    assert dlg.refresh_token is None
    assert dlg._access_expires_in is None
    # sign-in page, email kept, password empty
    assert dlg.stacked_pages.currentIndex() == dlg.PAGE_SIGNIN
    assert dlg.input_email.text() == expect_email
    assert dlg.input_password.text() == ""
    assert dlg._remembered_email() == expect_email
    # timer stopped, session marked over
    assert not dlg._session_timer.isActive(), "proactive refresh timer must stop"
    assert dlg._session_cancelled.is_set()
    assert dlg._session_expired_handled is True


def _authed_paths(calls):
    """Request paths that carried an Authorization header."""
    return [p for p, authed in calls if authed]


@pytest.fixture
def net(mod, monkeypatch):
    """Records every request as (path, was_authenticated). Never a body."""
    calls = []
    routes = {}

    def _path(url):
        return "/" + str(url).split("://", 1)[-1].split("/", 1)[-1]

    def _handle(method, url, **kw):
        p = _path(url)
        calls.append((p, bool((kw.get("headers") or {}).get("Authorization"))))
        for frag, fn in routes.items():
            if frag in p:
                return fn(**kw)
        return _Resp(200, {})

    monkeypatch.setattr(mod.requests, "request",
                        lambda m, u, **kw: _handle(m, u, **kw))
    monkeypatch.setattr(mod.requests, "post",
                        lambda u, **kw: _handle("POST", u, **kw))
    monkeypatch.setattr(mod.requests, "get",
                        lambda u, **kw: _handle("GET", u, **kw))
    return {"calls": calls, "routes": routes}


# ══════════════════════════════════════════════════════════════════════════
# 1. balance request begins, logout completes, balance returns 401
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_balance_401_after_logout_is_not_an_expiry(mod, dlg, qapp, net):
    """The exact reported reproduction."""
    hold = threading.Event()
    started = threading.Event()
    dlg._test_release_events.append(hold)

    def _balance(**kw):
        started.set()
        hold.wait(10)
        return _Resp(401)

    net["routes"]["/balance"] = _balance

    dlg._refresh_balance()
    assert started.wait(5), "balance request never started"

    dlg._logout_thread()            # the user clicks Log out
    _pump(qapp)
    hold.set()                      # the in-flight request now returns 401
    assert_clean_logout(dlg, qapp)


# ══════════════════════════════════════════════════════════════════════════
# 2. status poll returns 401 after logout
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_status_poll_401_after_logout_is_not_an_expiry(mod, dlg, qapp, net):
    net["routes"]["/status"] = lambda **kw: _Resp(401)
    dlg._logout_thread()
    _pump(qapp)
    dlg._fetch_job_statuses(["job-1"])       # a poll still in the pipe
    assert_clean_logout(dlg, qapp)


# ══════════════════════════════════════════════════════════════════════════
# 3. upload returns 401 after logout
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_upload_401_after_logout_is_not_an_expiry(mod, dlg, qapp, net):
    """The upload posts its own multipart body, so it carries its own epoch."""
    upload_epoch = dlg._auth_epoch          # as the upload path captures it
    dlg._logout_thread()
    _pump(qapp)
    # What the upload path does on a 401 it cannot recover from.
    assert dlg._attempt_refresh() == dlg.REFRESH_TERMINAL
    dlg._notify_session_expired(epoch=upload_epoch)
    assert_clean_logout(dlg, qapp)


# ══════════════════════════════════════════════════════════════════════════
# 4. proactive refresh completes after logout
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_proactive_refresh_landing_after_logout_is_not_an_expiry(
        mod, dlg, qapp, net):
    hold = threading.Event()
    started = threading.Event()
    dlg._test_release_events.append(hold)

    def _refresh(**kw):
        started.set()
        hold.wait(10)
        return _Resp(401, {"error": "invalid_grant"})

    net["routes"]["/refresh"] = _refresh

    worker = threading.Thread(target=dlg._proactive_refresh_attempt, daemon=True)
    worker.start()
    assert started.wait(5), "refresh never started"

    dlg._logout_thread()
    _pump(qapp)
    hold.set()
    worker.join(10)
    assert_clean_logout(dlg, qapp)


# ══════════════════════════════════════════════════════════════════════════
# 5. expiry queued, then logout before the UI callback runs
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_queued_expiry_is_dropped_when_logout_lands_first(mod, dlg, qapp, net):
    """Stage 2 of the guard. The decision to expire is already made and the
    slot is already queued; the logout happens before Qt delivers it."""
    dlg._notify_session_expired()                 # queued, not yet delivered
    assert getattr(dlg, "_session_expired_epoch", None) is not None, (
        "stage 1 must record the epoch the queued tear-down belongs to")
    dlg._logout_thread()                          # lands first
    assert_clean_logout(dlg, qapp)
    # And calling the slot directly, as Qt would, still refuses.
    dlg._on_session_expired()
    assert dlg.iface.messageBar().expiry_messages() == []


# ══════════════════════════════════════════════════════════════════════════
# 6-7. the logout endpoint itself misbehaves
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
@pytest.mark.parametrize("code", [400, 401])
def test_logout_endpoint_error_is_still_a_clean_logout(mod, dlg, qapp, net, code):
    net["routes"]["/logout"] = lambda **kw: _Resp(code)
    dlg._logout_thread()
    assert_clean_logout(dlg, qapp)


@needs_qgis
def test_logout_endpoint_network_failure_is_still_a_clean_logout(
        mod, dlg, qapp, net):
    def _boom(**kw):
        raise mod.requests.ConnectionError("unreachable")

    net["routes"]["/logout"] = _boom
    dlg._logout_thread()
    assert_clean_logout(dlg, qapp)


# ══════════════════════════════════════════════════════════════════════════
# 8. logout immediately followed by a new sign-in
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_old_session_cannot_disturb_a_new_sign_in(mod, dlg, qapp, net):
    hold = threading.Event()
    started = threading.Event()
    dlg._test_release_events.append(hold)

    def _balance(**kw):
        started.set()
        hold.wait(10)
        return _Resp(401)

    net["routes"]["/balance"] = _balance

    old_epoch = dlg._auth_epoch
    dlg._refresh_balance()
    assert started.wait(5)

    dlg._logout_thread()
    _pump(qapp)
    # A different person signs in straight away.
    dlg._install_session("access-SECOND", "refresh-SECOND", 300,
                         email="second@example.invalid")
    assert dlg._session_expired_handled is False, \
        "a new sign-in must reset the expiry latch for its own session"
    assert not dlg._session_cancelled.is_set()

    hold.set()                       # the first session's 401 lands now
    _pump(qapp)

    bar = dlg.iface.messageBar()
    assert bar.expiry_messages() == [], \
        f"the old session tore down the new one: {bar.pushed}"
    assert dlg.access_token == "access-SECOND"
    assert dlg.current_user_email == "second@example.invalid"
    assert dlg._auth_epoch != old_epoch


# ══════════════════════════════════════════════════════════════════════════
# 9. no authenticated follow-up calls after logout
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_balance_worker_stops_after_logout(mod, dlg, qapp, net):
    """/balance returns after the logout; /storage/usage and
    /trial-request/status must never be issued."""
    hold = threading.Event()
    started = threading.Event()
    dlg._test_release_events.append(hold)

    def _balance(**kw):
        started.set()
        hold.wait(10)
        return _Resp(200, {"tier": "payg", "available_tokens": 5})

    net["routes"]["/balance"] = _balance

    dlg._refresh_balance()
    assert started.wait(5)
    dlg._logout_thread()
    _pump(qapp)
    hold.set()
    _pump(qapp, 0.6)

    paths = [p for p, _ in net["calls"]]
    assert not [p for p in paths if "storage" in p], \
        f"storage was queried after logout: {paths}"
    assert not [p for p in paths if "trial" in p], \
        f"trial status was queried after logout: {paths}"
    # Nothing authenticated after the logout call itself.
    after = paths[paths.index("/logout") + 1:] if "/logout" in paths else []
    assert not [p for p in after if "balance" in p or "storage" in p], after
    assert_clean_logout(dlg, qapp)


# ══════════════════════════════════════════════════════════════════════════
# 10. focus returning from the confirmation dialog
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_focus_return_during_logout_starts_no_refresh(mod, dlg, qapp, net,
                                                      monkeypatch):
    """Closing the "Log out?" dialog hands focus back to this window. That
    must not start an authenticated refresh."""
    from qgis.PyQt import QtCore

    started = []
    real_refresh = dlg._refresh_balance
    monkeypatch.setattr(dlg, "_refresh_balance",
                        lambda: started.append(1) or real_refresh())
    dlg._billing_strip_built = True
    monkeypatch.setattr(dlg, "isActiveWindow", lambda: True)

    # While signed in, a focus change SHOULD refresh: the guard must not have
    # simply disabled the feature.
    dlg.changeEvent(QtCore.QEvent(QtCore.QEvent.Type.ActivationChange))
    _pump(qapp)
    assert started, "focus refresh must still work for a live session"

    started.clear()
    dlg._logout_thread()
    _pump(qapp)
    dlg.changeEvent(QtCore.QEvent(QtCore.QEvent.Type.ActivationChange))
    _pump(qapp)
    assert not started, "a focus change during/after logout started a refresh"
    assert_clean_logout(dlg, qapp)


# ══════════════════════════════════════════════════════════════════════════
# the behaviour that must NOT regress: a genuine expiry still signs you out
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_genuine_expiry_in_the_current_epoch_still_signs_out(
        mod, dlg, qapp, net):
    net["routes"]["/refresh"] = lambda **kw: _Resp(401, {"error": "invalid_grant"})
    net["routes"]["/balance"] = lambda **kw: _Resp(401)

    dlg._fetch_balance()
    _pump(qapp)

    bar = dlg.iface.messageBar()
    assert len(bar.expiry_messages()) == 1, \
        f"a real expiry must be reported exactly once: {bar.pushed}"
    assert bar.logout_messages() == []
    assert dlg.access_token is None
    assert dlg.refresh_token is None
    assert dlg.stacked_pages.currentIndex() == dlg.PAGE_SIGNIN
    assert dlg.input_email.text() == "tester@example.invalid"
    assert dlg.input_password.text() == ""
    assert not dlg._session_timer.isActive()


@needs_qgis
def test_genuine_expiry_is_reported_once_for_many_requests(mod, dlg, qapp, net):
    net["routes"]["/refresh"] = lambda **kw: _Resp(401, {"error": "invalid_grant"})
    net["routes"]["/balance"] = lambda **kw: _Resp(401)

    for _ in range(4):
        dlg._fetch_balance()
    _pump(qapp)
    assert len(dlg.iface.messageBar().expiry_messages()) == 1
