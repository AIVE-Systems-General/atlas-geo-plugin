# -*- coding: utf-8 -*-
"""Refresh scheduling, threading, and transient-vs-terminal failure handling.

Three separate risks are covered here, and they pull in different directions:

  * the schedule must follow the SERVER's token lifetime, not a number baked
    into the client that silently rots when the realm is retuned;
  * the refresh must never issue network I/O on the GUI thread, because a
    blocking POST there freezes the QGIS window for up to the request timeout;
  * a flaky network must NOT sign anyone out, while a revoked credential must
    not be retried for ever. Getting this backwards produces either the bug
    just fixed, or a client that hammers an auth server with a dead token.

No network: requests is replaced in every test.
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
    spec = importlib.util.spec_from_file_location("_dlg_sched", DIALOG)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Resp:
    def __init__(self, status_code, payload=None, raises=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self._raises = raises

    def json(self):
        if self._raises:
            raise self._raises
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
    d.access_token = "access-ORIGINAL"
    d.refresh_token = "refresh-ORIGINAL"
    d.current_user_email = "tester@example.invalid"
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


# ══════════════════════════════════════════════════════════════════════════
# 1. schedule derived from expires_in
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_schedule_is_derived_from_expires_in(mod, dlg, monkeypatch):
    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(
        200, {"access_token": "A2", "refresh_token": "R2", "expires_in": 900}))

    assert dlg._attempt_refresh() == dlg.REFRESH_OK
    assert dlg._access_expires_in == 900
    # 80% of 900s = 720s
    assert dlg._refresh_interval_ms() == 720_000
    dlg._apply_refresh_schedule()
    assert dlg._session_timer.interval() == 720_000


@needs_qgis
@pytest.mark.parametrize("expires_in,expected_ms", [
    (300,  240_000),      # the current realm value: 80% of 5 min
    (900,  720_000),
    (60,    48_000),
    (30,    24_000),      # 80%; a flat floor here would overshoot expiry
    (10,     8_000),      # 80%, leaving 2s of headroom before the token dies
])
def test_interval_is_eighty_percent_with_a_floor(mod, dlg, expires_in, expected_ms):
    dlg._access_expires_in = dlg._coerce_expires_in(expires_in)
    assert dlg._refresh_interval_ms() == expected_ms


# ══════════════════════════════════════════════════════════════════════════
# 2. fallback when expires_in is missing or unusable
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_fallback_interval_when_expires_in_absent(mod, dlg, monkeypatch):
    """The live backend does not send expires_in today, so this is the path
    that actually runs in production."""
    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(
        200, {"access_token": "A2", "refresh_token": "R2"}))
    assert dlg._attempt_refresh() == dlg.REFRESH_OK
    assert dlg._access_expires_in is None
    assert dlg._refresh_interval_ms() == dlg.SESSION_REFRESH_FALLBACK_MS == 240_000


@needs_qgis
@pytest.mark.parametrize("bad", [
    None, "", "abc", "NaN", 0, -1, -900, 9, 86_401, 10**9, [], {}, True, False,
])
def test_invalid_expires_in_falls_back(mod, dlg, bad):
    """Junk must never produce a nonsense schedule: a zero or negative value
    would re-arm the timer instantly and spin.

    Asserted EXACTLY. An earlier version accepted "None or any positive
    number", which would have passed even if a bool had been read as a
    one-second lifetime.
    """
    assert dlg._coerce_expires_in(bad) is None, (
        f"{bad!r} must be rejected outright, not coerced to "
        f"{dlg._coerce_expires_in(bad)!r}")
    dlg._access_expires_in = dlg._coerce_expires_in(bad)
    assert dlg._refresh_interval_ms() == dlg.SESSION_REFRESH_FALLBACK_MS


# ══════════════════════════════════════════════════════════════════════════
# 3. no network I/O on the GUI thread
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_timer_refresh_does_no_network_io_on_the_gui_thread(mod, dlg, monkeypatch, qapp):
    """The QTimer tick may START the work; it must not DO it.

    A blocking POST on the GUI thread freezes the QGIS window for up to the
    15s request timeout, which is exactly the kind of hang users report as
    "QGIS locked up".
    """
    gui_thread_id = threading.get_ident()
    post_threads = []
    done = threading.Event()

    def recording_post(url, **kw):
        post_threads.append(threading.get_ident())
        done.set()
        return _Resp(200, {"access_token": "A2", "refresh_token": "R2",
                           "expires_in": 300})

    monkeypatch.setattr(mod.requests, "post", recording_post)

    dlg._proactive_refresh()            # called as the timer would call it
    assert done.wait(timeout=5), "the refresh worker never ran"

    assert len(post_threads) == 1
    assert post_threads[0] != gui_thread_id, (
        "the refresh POST ran on the GUI thread; it must run on a worker")

    # and the UI-affecting part must come back to the GUI thread
    qapp.processEvents()
    assert dlg.access_token == "A2"


@needs_qgis
def test_proactive_refresh_returns_immediately(mod, dlg, monkeypatch):
    """The tick itself must not block on the request."""
    import time as _time
    release = threading.Event()
    dlg._test_release_events.append(release)

    def slow_post(url, **kw):
        release.wait(timeout=5)
        return _Resp(200, {"access_token": "A2", "refresh_token": "R2"})

    monkeypatch.setattr(mod.requests, "post", slow_post)
    started = _time.time()
    dlg._proactive_refresh()
    elapsed = _time.time() - started
    release.set()
    assert elapsed < 0.5, (
        f"_proactive_refresh blocked the GUI thread for {elapsed:.2f}s")


def test_source_shows_the_post_only_inside_the_worker():
    """Code reference, not just behaviour: the refresh POST must live in
    _attempt_refresh, reached only from the worker thread."""
    import ast
    tree = ast.parse(DIALOG.read_text(encoding="utf-8"))
    tick = next(n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == "_proactive_refresh")
    body = ast.dump(tick)
    for forbidden in ("'post'", "'request'", "'get'", "_attempt_refresh"):
        assert forbidden not in body, (
            f"_proactive_refresh references {forbidden}; it must only start a thread")
    assert "Thread" in body, "_proactive_refresh must hand the work to a thread"


# ══════════════════════════════════════════════════════════════════════════
# 4. transient failures keep the session
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
@pytest.mark.parametrize("exc_name", ["Timeout", "ConnectionError"])
def test_network_failure_does_not_log_the_user_out(mod, dlg, monkeypatch, exc_name):
    exc = getattr(mod.requests, exc_name)

    def boom(url, **kw):
        raise exc("network down")

    monkeypatch.setattr(mod.requests, "post", boom)

    assert dlg._attempt_refresh() == dlg.REFRESH_TRANSIENT
    assert dlg.access_token == "access-ORIGINAL", "tokens must survive"
    assert dlg.refresh_token == "refresh-ORIGINAL"
    assert dlg._session_expired_handled is False, "must NOT tear the session down"


@needs_qgis
@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_server_error_does_not_log_the_user_out(mod, dlg, monkeypatch, status):
    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(status))
    assert dlg._attempt_refresh() == dlg.REFRESH_TRANSIENT
    assert dlg.refresh_token == "refresh-ORIGINAL"
    assert dlg._session_expired_handled is False


# NOTE: a test asserting "teardown after N transient failures" used to live
# here. It was removed deliberately, not because it was flaky: a timeout, a DNS
# failure or a 502 is not evidence that the refresh token is invalid, so no
# count of them may end the session. Unbounded tolerance is covered by
# test_session_resilience.test_many_transient_failures_never_clear_tokens.


@needs_qgis
def test_a_success_resets_the_failure_count(mod, dlg, monkeypatch, qapp):
    responses = [_Resp(503), _Resp(503),
                 _Resp(200, {"access_token": "A2", "refresh_token": "R2"})]
    monkeypatch.setattr(mod.requests, "post",
                        lambda url, **kw: responses.pop(0))
    dlg._proactive_refresh_thread()
    dlg._proactive_refresh_thread()
    assert dlg._refresh_failures == 2
    dlg._proactive_refresh_thread()
    assert dlg._refresh_failures == 0, "a success must clear the backoff"
    assert dlg._session_expired_handled is False


# ══════════════════════════════════════════════════════════════════════════
# 5. terminal failures tear down exactly once
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
@pytest.mark.parametrize("status", [400, 401])
def test_invalid_grant_is_terminal(mod, dlg, monkeypatch, status):
    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(
        status, {"error": "invalid_grant",
                 "error_description": "Token is not active"}))
    assert dlg._attempt_refresh() == dlg.REFRESH_TERMINAL


@needs_qgis
def test_invalid_grant_causes_exactly_one_teardown(mod, dlg, monkeypatch, qapp):
    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(
        401, {"error": "invalid_grant"}))

    # Four terminal outcomes in a row, as several in-flight requests would
    # produce. Only the queued teardown counts: the slot must be invoked once,
    # so the user is told once, however many requests failed.
    for _ in range(4):
        dlg._proactive_refresh_thread()
    qapp.processEvents()

    assert dlg._session_expired_handled is True
    msgs = [a for a, k in dlg.iface.messageBar().pushed
            if "session expired" in str(a).lower()]
    assert len(msgs) == 1, (
        f"four terminal refreshes produced {len(msgs)} teardowns, expected 1")
    assert dlg.access_token is None and dlg.refresh_token is None


@needs_qgis
def test_max_server_session_expiry_returns_to_signin_cleanly(mod, dlg, monkeypatch, qapp):
    """ssoSessionMaxLifespan is 10 hours and cannot be extended by refreshing.
    When it is reached the refresh token is rejected outright, and the user
    must land back on sign-in with their address intact."""
    dlg.current_user_email = "longrunner@example.invalid"
    dlg._save_remembered_email()
    dlg._start_session_timer()

    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(
        400, {"error": "invalid_grant", "error_description": "Session not active"}))

    dlg._proactive_refresh_thread()
    qapp.processEvents()
    dlg._on_session_expired()

    assert dlg.access_token is None and dlg.refresh_token is None
    assert not dlg._session_timer.isActive(), "the timer must stop"
    assert dlg.stacked_pages.currentIndex() == dlg.PAGE_SIGNIN
    assert dlg.input_email.text() == "longrunner@example.invalid", \
        "the address must survive so the user does not retype it"
