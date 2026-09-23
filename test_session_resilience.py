# -*- coding: utf-8 -*-
"""Timer discipline, unbounded transient tolerance, and safe scheduling.

Three corrections are locked in here:

  * The refresh QTimer is SINGLE-SHOT. A repeating timer plus a 15s HTTP
    timeout is a worker factory: at a 5s retry interval it fires at 5s, 10s and
    15s while the first request is still in flight, and each stacked worker
    spends a rotated refresh token the others then find invalid.

  * A transient failure NEVER ends the session. Timeout, DNS failure and 5xx
    say something about the network, not about the credential. Retrying is
    capped in interval but not in count: the session ends only on a confirmed
    400/401 rejection, an explicit logout, or shutdown.

  * The refresh is always scheduled BEFORE the token expires. A flat floor
    applied to a short lifetime would schedule the renewal after the thing it
    was meant to renew had already died.
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
    spec = importlib.util.spec_from_file_location("_dlg_res", DIALOG)
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
    QtCore.QSettings.setPath(QtCore.QSettings.Format.IniFormat,
                             QtCore.QSettings.Scope.UserScope, str(tmp_path))

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
# 1. the timer is single-shot and re-armed only after completion
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_session_timer_is_single_shot(mod, dlg):
    assert dlg._session_timer.isSingleShot() is True, (
        "a repeating timer stacks workers behind a slow request")


@needs_qgis
def test_timer_is_rearmed_only_after_an_attempt_completes(mod, dlg, monkeypatch, qapp):
    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(
        200, {"access_token": "A2", "refresh_token": "R2"}))
    dlg._start_session_timer()
    assert dlg._session_timer.isActive()

    dlg._session_timer.stop()                  # simulate the tick having fired
    assert not dlg._session_timer.isActive()

    dlg._proactive_refresh_thread()            # worker runs to completion
    qapp.processEvents()
    assert dlg._session_timer.isActive(), "the timer must be re-armed on success"


@needs_qgis
def test_slow_request_cannot_stack_waiting_workers(mod, dlg, monkeypatch):
    """The scenario named in review: a 5s retry interval against a 15s timeout.

    The tick is invoked repeatedly while the first request is still blocked.
    Exactly one worker may exist.
    """
    in_post = threading.Event()
    release = threading.Event()
    concurrent = []
    live = {"n": 0}
    guard = threading.Lock()

    # No token filtering: fixture teardown now joins every worker a test
    # starts, so nothing from an earlier test can still be parked here.
    dlg._test_release_events.append(release)

    def slow_post(url, **kw):
        with guard:
            live["n"] += 1
            concurrent.append(live["n"])
        in_post.set()
        release.wait(timeout=10)               # stands in for a 15s timeout
        with guard:
            live["n"] -= 1
        return _Resp(200, {"access_token": "A2", "refresh_token": "R2"})

    monkeypatch.setattr(mod.requests, "post", slow_post)

    dlg._proactive_refresh()                   # tick 1: starts the worker
    assert in_post.wait(timeout=5)

    for _ in range(5):                         # ticks at 5s, 10s, 15s...
        dlg._proactive_refresh()
        time.sleep(0.02)

    release.set()
    time.sleep(0.3)

    assert max(concurrent) == 1, (
        f"up to {max(concurrent)} refresh requests were in flight at once")
    assert len(concurrent) == 1, (
        f"{len(concurrent)} workers started; the in-flight guard failed")


@needs_qgis
def test_in_flight_flag_is_released_even_when_the_request_raises(mod, dlg, monkeypatch):
    """A crashed worker must not wedge the session permanently."""
    def boom(url, **kw):
        raise mod.requests.ConnectionError("down")

    monkeypatch.setattr(mod.requests, "post", boom)
    dlg._proactive_refresh_thread()
    assert dlg._refresh_in_flight is False, "a failed attempt left the guard set"


# ══════════════════════════════════════════════════════════════════════════
# 2. transient failures never end the session
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
@pytest.mark.parametrize("mode", ["timeout", "connection", "500", "503"])
def test_many_transient_failures_never_clear_tokens(mod, dlg, monkeypatch, qapp, mode):
    """Far more failures than any previous bound, with no teardown."""
    def responder(url, **kw):
        if mode == "timeout":
            raise mod.requests.Timeout("slow")
        if mode == "connection":
            raise mod.requests.ConnectionError("no route")
        return _Resp(int(mode))

    monkeypatch.setattr(mod.requests, "post", responder)

    for _ in range(50):
        dlg._proactive_refresh_thread()
        qapp.processEvents()

    assert dlg._session_expired_handled is False, "a network fault ended the session"
    assert dlg.access_token == "access-ORIGINAL", "tokens were cleared"
    assert dlg.refresh_token == "refresh-ORIGINAL"
    assert dlg.stacked_pages.currentIndex() != dlg.PAGE_SIGNIN, \
        "the user was returned to sign-in by a network fault"
    assert dlg._refresh_failures == 50


@needs_qgis
def test_backoff_is_capped_and_keeps_retrying(mod, dlg, monkeypatch, qapp):
    """Observed through the real effect: the interval the timer is re-armed to.

    The worker marshals by slot NAME, so the queued call is what actually sets
    the interval; letting Qt deliver it is both simpler and a truer test than
    intercepting the marshalling.
    """
    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(503))

    intervals = []
    for _ in range(12):
        dlg._proactive_refresh_thread()
        qapp.processEvents()
        intervals.append(dlg._session_timer.interval())

    expected_ramp = [s * 1000 for s in dlg.REFRESH_BACKOFF_S]
    assert intervals[:len(expected_ramp)] == expected_ramp, (
        f"backoff ramp was {intervals[:len(expected_ramp)]}, "
        f"expected {expected_ramp}")

    ceiling = dlg.REFRESH_BACKOFF_S[-1] * 1000
    assert set(intervals[len(expected_ramp):]) == {ceiling}, (
        "the interval must cap at the last backoff value and stay there")

    assert dlg._session_expired_handled is False,         "twelve transient failures ended the session"
    assert dlg.refresh_token == "refresh-ORIGINAL", "tokens were cleared"


@needs_qgis
def test_recovery_after_several_transient_failures(mod, dlg, monkeypatch, qapp):
    """The whole point of retrying: the session resumes when the network does."""
    script = [_Resp(503), _Resp(503), _Resp(502)]
    script += [_Resp(200, {"access_token": "A-RECOVERED",
                           "refresh_token": "R-RECOVERED"})]

    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: script.pop(0))

    for _ in range(3):
        dlg._proactive_refresh_thread()
        qapp.processEvents()
    assert dlg._refresh_failures == 3
    assert dlg.access_token == "access-ORIGINAL", "tokens kept while failing"

    dlg._proactive_refresh_thread()
    qapp.processEvents()

    assert dlg.access_token == "A-RECOVERED"
    assert dlg.refresh_token == "R-RECOVERED"
    assert dlg._refresh_failures == 0, "the backoff must reset on recovery"
    assert dlg._session_expired_handled is False
    assert dlg._session_timer.isActive(), "normal schedule must resume"


# ══════════════════════════════════════════════════════════════════════════
# 3. every call site preserves the distinction
# ══════════════════════════════════════════════════════════════════════════

def test_no_boolean_refresh_wrapper_exists():
    """The collapsing façade must not come back: it is what let the upload
    path treat a timeout as a dead session."""
    import ast
    tree = ast.parse(DIALOG.read_text(encoding="utf-8"))
    names = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert "_refresh_access_token" not in names, (
        "the boolean wrapper is back; it collapses TRANSIENT and TERMINAL")


def test_every_refresh_call_site_handles_the_tristate():
    """Each caller must compare against a REFRESH_* constant, not truthiness."""
    import ast
    tree = ast.parse(DIALOG.read_text(encoding="utf-8"))

    def calls_attempt_refresh(fn):
        return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == "_attempt_refresh" for n in ast.walk(fn))

    callers = [fn for fn in ast.walk(tree)
               if isinstance(fn, ast.FunctionDef)
               and fn.name != "_attempt_refresh"
               and calls_attempt_refresh(fn)]
    assert len(callers) >= 3, (
        f"expected at least 3 calling functions, found "
        f"{[f.name for f in callers]}")

    for fn in callers:
        body = ast.dump(fn)
        assert "REFRESH_OK" in body, (
            f"{fn.name}() calls _attempt_refresh but never compares the result "
            f"against REFRESH_OK; it is treating the outcome as a boolean")
        assert ("REFRESH_TERMINAL" in body or "REFRESH_TRANSIENT" in body), (
            f"{fn.name}() does not distinguish TERMINAL from TRANSIENT, which "
            f"is how a network blip becomes a forced sign-out")


@needs_qgis
@pytest.mark.parametrize("failure", ["timeout", "500"])
def test_upload_path_does_not_log_out_on_a_transient_refresh_failure(
        mod, dlg, monkeypatch, failure):
    """The specific regression called out in review.

    A 401 on upload followed by a refresh that times out must NOT end the
    session: the auth server was unreachable, which says nothing about whether
    the credential is still good.
    """
    def responder(url, **kw):
        if failure == "timeout":
            raise mod.requests.Timeout("slow")
        return _Resp(500)

    monkeypatch.setattr(mod.requests, "post", responder)

    outcome = dlg._attempt_refresh()
    assert outcome == dlg.REFRESH_TRANSIENT
    # the upload path branches on exactly this value
    assert dlg._session_expired_handled is False
    assert dlg.refresh_token == "refresh-ORIGINAL"


def test_upload_path_source_branches_on_transient():
    """Code reference: the upload's 401 handler must consult the outcome."""
    src = DIALOG.read_text(encoding="utf-8")
    assert "upload_refresh_outcome = self._attempt_refresh()" in src, \
        "the upload path no longer captures the refresh outcome"
    assert "upload_refresh_outcome in (self.REFRESH_TRANSIENT," in src, \
        "the upload path does not special-case a transient refresh failure"
    assert "self.REFRESH_STALE)" in src, \
        "the upload path does not treat a stale refresh as non-terminal"


# ══════════════════════════════════════════════════════════════════════════
# 4. the interval is always before expiry
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
@pytest.mark.parametrize("expires_in", [10, 30, 60, 300, 900])
def test_interval_is_always_before_expiry(mod, dlg, expires_in):
    dlg._access_expires_in = dlg._coerce_expires_in(expires_in)
    assert dlg._access_expires_in == float(expires_in), \
        f"{expires_in}s should be accepted as a plausible lifetime"
    interval_ms = dlg._refresh_interval_ms()
    lifetime_ms = expires_in * 1000
    assert interval_ms < lifetime_ms, (
        f"expires_in={expires_in}s schedules the refresh at {interval_ms}ms, "
        f"which is at or after the {lifetime_ms}ms expiry")
    assert interval_ms > 0


@needs_qgis
@pytest.mark.parametrize("expires_in,expected_ms", [
    (10,    8_000),      # 80%, still 2s of headroom
    (30,   24_000),
    (60,   48_000),
    (300, 240_000),      # the current realm value
    (900, 720_000),
])
def test_interval_is_exactly_eighty_percent(mod, dlg, expires_in, expected_ms):
    dlg._access_expires_in = dlg._coerce_expires_in(expires_in)
    assert dlg._refresh_interval_ms() == expected_ms


# ══════════════════════════════════════════════════════════════════════════
# 5. coercion: exact outcomes, no "either is fine"
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
@pytest.mark.parametrize("value,expected", [
    # accepted
    (300,       300.0),
    (300.0,     300.0),
    ("300",     300.0),
    ("300.5",   300.5),
    (10,         10.0),        # exactly the minimum
    (86_400, 86_400.0),        # exactly the maximum
    # rejected
    (None,       None),
    ("",         None),
    ("abc",      None),
    ("NaN",      None),
    ("inf",      None),
    (0,          None),
    (-1,         None),
    (-900,       None),
    (9,          None),        # below the plausible minimum
    (86_401,     None),        # above the plausible maximum
    (10 ** 9,    None),
    ([],         None),
    ({},         None),
    (True,       None),        # bool is NOT 1 second
    (False,      None),
])
def test_coerce_expires_in_exact_outcomes(mod, dlg, value, expected):
    assert dlg._coerce_expires_in(value) == expected, \
        f"_coerce_expires_in({value!r}) should be exactly {expected!r}"


@needs_qgis
def test_bool_is_rejected_rather_than_read_as_one_second(mod, dlg):
    """float(True) == 1.0, so a JSON `true` would otherwise mean a 1s token."""
    assert dlg._coerce_expires_in(True) is None
    dlg._access_expires_in = dlg._coerce_expires_in(True)
    assert dlg._refresh_interval_ms() == dlg.SESSION_REFRESH_FALLBACK_MS


# ══════════════════════════════════════════════════════════════════════════
# 6. shutdown during an in-flight refresh
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_shutdown_during_inflight_refresh_does_not_touch_the_dialog(
        mod, dlg, monkeypatch, qapp):
    """A worker can outlive the dialog when the user unloads the plugin while a
    request is still waiting. It must notice and do nothing."""
    entered = threading.Event()
    release = threading.Event()
    dlg._test_release_events.append(release)

    def slow_post(url, **kw):
        entered.set()
        release.wait(timeout=10)
        return _Resp(200, {"access_token": "A-LATE", "refresh_token": "R-LATE"})

    monkeypatch.setattr(mod.requests, "post", slow_post)

    worker = threading.Thread(target=dlg._proactive_refresh_thread, daemon=True)
    worker.start()
    assert entered.wait(timeout=5)

    dlg.shutdown_session()             # the plugin is unloaded mid-request
    release.set()
    worker.join(timeout=10)
    qapp.processEvents()

    assert dlg._shutting_down is True
    assert not dlg._session_timer.isActive(), \
        "a worker re-armed the timer after shutdown"


@needs_qgis
def test_logout_during_inflight_refresh_does_not_resurrect_the_session(
        mod, dlg, monkeypatch, qapp):
    entered = threading.Event()
    release = threading.Event()
    dlg._test_release_events.append(release)

    def slow_post(url, **kw):
        entered.set()
        release.wait(timeout=10)
        return _Resp(200, {"access_token": "A-LATE", "refresh_token": "R-LATE"})

    monkeypatch.setattr(mod.requests, "post", slow_post)
    worker = threading.Thread(target=dlg._proactive_refresh_thread, daemon=True)
    worker.start()
    assert entered.wait(timeout=5)

    dlg.shutdown_session()
    dlg.access_token = None
    dlg.refresh_token = None
    release.set()
    worker.join(timeout=10)
    qapp.processEvents()

    assert not dlg._session_timer.isActive(), \
        "a logged-out session had its refresh timer restarted by a late worker"


@needs_qgis
def test_invoke_on_gui_reports_failure_rather_than_raising(mod, dlg):
    dlg._shutting_down = True
    assert dlg._invoke_on_gui("_apply_refresh_schedule") is False


# ══════════════════════════════════════════════════════════════════════════
# 7. the balance worker's RuntimeError handling is NARROW
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_destroyed_dialog_during_marshalling_is_swallowed(mod, dlg, monkeypatch):
    """(a) A deleted-object RuntimeError during the callback is a lifecycle
    race, not a bug, and must not escape as an unhandled thread exception."""
    from qgis.PyQt import QtCore

    def deleted(*a, **k):
        raise RuntimeError("wrapped C/C++ object of type QWidget has been deleted")

    monkeypatch.setattr(QtCore.QMetaObject, "invokeMethod", staticmethod(deleted))
    dlg._shutting_down = False
    assert dlg._invoke_on_gui("_apply_refresh_schedule") is False
    assert dlg._shutting_down is True


@needs_qgis
def test_unrelated_runtime_error_is_not_swallowed(mod, dlg, monkeypatch):
    """(b) Any OTHER RuntimeError is a real defect and must propagate.

    Catching RuntimeError around the whole worker would have hidden this, which
    is why the guard is around the marshalling call alone.
    """
    from qgis.PyQt import QtCore

    def unrelated(*a, **k):
        raise RuntimeError("dictionary changed size during iteration")

    monkeypatch.setattr(QtCore.QMetaObject, "invokeMethod", staticmethod(unrelated))
    dlg._shutting_down = False
    with pytest.raises(RuntimeError, match="dictionary changed size"):
        dlg._invoke_on_gui("_apply_refresh_schedule")


@needs_qgis
def test_deleted_object_check_works_on_this_qt(mod):
    """sip.isdeleted must be usable on every supported QGIS, or the guard
    silently degrades to never detecting a destroyed dialog."""
    from qgis.PyQt import QtWidgets, sip
    w = QtWidgets.QWidget()
    assert mod._object_is_deleted(w) is False
    sip.delete(w)
    assert mod._object_is_deleted(w) is True


def test_balance_worker_has_no_blanket_runtime_catch():
    """Code reference: the balance worker must not wrap everything."""
    import ast
    tree = ast.parse(DIALOG.read_text(encoding="utf-8"))
    workers = [n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "_work"
               and any(isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                       and c.func.attr == "_invoke_on_gui" for c in ast.walk(n))]
    assert workers, "the guarded balance worker was not found"
    for fn in workers:
        handlers = [h for h in ast.walk(fn) if isinstance(h, ast.ExceptHandler)]
        assert not handlers, (
            f"_work at line {fn.lineno} still wraps its body in a handler; the "
            f"guard belongs around the marshalling call only")
        raw = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
               and isinstance(c.func, ast.Attribute) and c.func.attr == "invokeMethod"]
        assert not raw, f"_work at line {fn.lineno} still marshals unguarded"
