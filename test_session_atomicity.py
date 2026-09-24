# -*- coding: utf-8 -*-
"""The commit window: epoch validation and token mutation must be one step.

Before this, the final epoch check and the token assignment were separate
statements, and the epoch bump took no shared lock. A logout landing
between them would clear the tokens and then have them written straight back
by the refresh that was already past its check.

`_before_commit_hook` is a test seam fired with NO lock held, immediately
before the commit critical section. A test pauses there, performs a real
logout or sign-in on another thread, then releases. If the guard were still
two separate operations these tests would write stale tokens; because
validation and commit share the auth-state lock with every session change,
they cannot.
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
    spec = importlib.util.spec_from_file_location("_dlg_atomic", DIALOG)
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
    # process. This call looked like isolation and was writing to the real
    # ~/.config/AIVE/AtlasGeo.conf -- the tester's own settings. Replacing the
    # settings object the plugin actually constructs is unambiguous: every read
    # and write under test lands in tmp_path.
    _real_qs = QtCore.QSettings
    _ini = str(tmp_path / "AtlasGeo.ini")

    def _scoped_qsettings(*a, **k):
        return _real_qs(_ini, _real_qs.Format.IniFormat)

    _scoped_qsettings.Format = _real_qs.Format
    _scoped_qsettings.Scope = _real_qs.Scope
    monkeypatch.setattr(mod.QtCore, "QSettings", _scoped_qsettings)
    if hasattr(mod, "QgsSettings"):
        monkeypatch.setattr(
            mod, "QgsSettings",
            lambda *a, **k: _real_qs(str(tmp_path / "AtlasGeo-profile.ini"),
                                     _real_qs.Format.IniFormat))

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


class _CommitBarrier:
    """Pauses the refresh at the commit window until the test says go."""

    def __init__(self):
        self.reached = threading.Event()
        self.release = threading.Event()
        self.hits = 0

    def install(self, dlg):
        # Registered so fixture teardown releases it even if this test fails.
        dlg._test_release_events.append(self.release)

        def hook():
            self.hits += 1
            self.reached.set()
            assert self.release.wait(timeout=10), "test never released the commit"
        dlg._before_commit_hook = hook

    def wait_until_at_commit(self):
        assert self.reached.wait(timeout=10), "the refresh never reached the commit"

    def let_it_commit(self):
        self.release.set()


# ══════════════════════════════════════════════════════════════════════════
# logout inside the commit window
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_logout_inside_the_commit_window_wins(mod, dlg, monkeypatch, qapp):
    """The exact interleaving that was previously unsafe."""
    barrier = _CommitBarrier()
    barrier.install(dlg)

    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(
        200, {"access_token": "access-STALE", "refresh_token": "refresh-STALE"}))

    outcome = {}
    worker = threading.Thread(
        target=lambda: outcome.setdefault("r", dlg._attempt_refresh()), daemon=True)
    worker.start()
    barrier.wait_until_at_commit()

    # The refresh is validated and about to write. Log out right now.
    dlg._clear_session(forget_email=True)
    assert dlg.access_token is None

    barrier.let_it_commit()
    worker.join(timeout=10)
    assert not worker.is_alive()

    assert outcome["r"] == dlg.REFRESH_STALE, (
        f"the refresh committed as {outcome['r']}; it must detect the logout")
    assert dlg.access_token is None, "a stale refresh wrote tokens back after logout"
    assert dlg.refresh_token is None


@needs_qgis
def test_new_signin_inside_the_commit_window_is_not_overwritten(
        mod, dlg, monkeypatch, qapp):
    """User B signs in while user A's refresh sits at the commit window."""
    barrier = _CommitBarrier()
    barrier.install(dlg)

    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(
        200, {"access_token": "access-STALE-A", "refresh_token": "refresh-STALE-A"}))

    outcome = {}
    worker = threading.Thread(
        target=lambda: outcome.setdefault("r", dlg._attempt_refresh()), daemon=True)
    worker.start()
    barrier.wait_until_at_commit()

    dlg._install_session("access-USER-B", "refresh-USER-B", 300,
                         email="user-b@example.invalid")

    barrier.let_it_commit()
    worker.join(timeout=10)

    assert outcome["r"] == dlg.REFRESH_STALE
    assert dlg.access_token == "access-USER-B", (
        "user A's refresh overwrote user B's access token in the commit window")
    assert dlg.refresh_token == "refresh-USER-B"
    assert dlg.current_user_email == "user-b@example.invalid"


@needs_qgis
def test_shutdown_inside_the_commit_window_discards(mod, dlg, monkeypatch):
    barrier = _CommitBarrier()
    barrier.install(dlg)
    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(
        200, {"access_token": "access-STALE", "refresh_token": "refresh-STALE"}))

    outcome = {}
    worker = threading.Thread(
        target=lambda: outcome.setdefault("r", dlg._attempt_refresh()), daemon=True)
    worker.start()
    barrier.wait_until_at_commit()

    dlg.shutdown_session()

    barrier.let_it_commit()
    worker.join(timeout=10)

    assert outcome["r"] == dlg.REFRESH_STALE
    assert dlg.access_token == "access-USER-A", "a stale value was committed"


@needs_qgis
def test_commit_window_without_interference_still_succeeds(mod, dlg, monkeypatch):
    """The barrier must not make every refresh stale: the control arm."""
    barrier = _CommitBarrier()
    barrier.install(dlg)
    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(
        200, {"access_token": "access-NEW", "refresh_token": "refresh-NEW"}))

    outcome = {}
    worker = threading.Thread(
        target=lambda: outcome.setdefault("r", dlg._attempt_refresh()), daemon=True)
    worker.start()
    barrier.wait_until_at_commit()
    barrier.let_it_commit()
    worker.join(timeout=10)

    assert outcome["r"] == dlg.REFRESH_OK
    assert dlg.access_token == "access-NEW"
    assert barrier.hits == 1


# ══════════════════════════════════════════════════════════════════════════
# the lock contract
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_auth_state_lock_is_separate_from_the_refresh_lock(mod, dlg):
    assert dlg._auth_state_lock is not dlg._refresh_lock


@needs_qgis
def test_auth_state_lock_is_not_held_during_network_io(mod, dlg, monkeypatch):
    """Holding it across a 15s request would block logout for 15 seconds."""
    observed = {}

    def probing_post(url, **kw):
        # A different thread must be able to take the lock while the request
        # is in flight.
        got = threading.Event()

        def grab():
            if dlg._auth_state_lock.acquire(timeout=2):
                try:
                    got.set()
                finally:
                    dlg._auth_state_lock.release()

        t = threading.Thread(target=grab, daemon=True)
        t.start()
        t.join(timeout=5)
        observed["free"] = got.is_set()
        return _Resp(200, {"access_token": "A2", "refresh_token": "R2"})

    monkeypatch.setattr(mod.requests, "post", probing_post)
    dlg._attempt_refresh()
    assert observed.get("free") is True, (
        "the auth-state lock was held across the HTTP request")


def test_source_commits_under_the_auth_state_lock():
    """Code reference: the check and the write share one critical section."""
    import ast
    tree = ast.parse(DIALOG.read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_attempt_refresh")

    withs = [n for n in ast.walk(fn) if isinstance(n, ast.With)]
    committing = []
    for w in withs:
        dumped = ast.dump(w)
        if "_auth_state_lock" in dumped and "_token_generation" in dumped \
                and "access_token" in dumped:
            committing.append(w)
    assert committing, (
        "no single `with self._auth_state_lock` block contains both the epoch "
        "check and the token assignment")
    body = ast.dump(committing[0])
    assert "_auth_epoch" in body, "the commit block does not validate the epoch"


# ══════════════════════════════════════════════════════════════════════════
# remembered email across logout, expiry and restart
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_explicit_logout_retains_the_remembered_email(mod, dlg, monkeypatch, qapp):
    dlg.current_user_email = "keep@example.invalid"
    dlg._set_remember_email(True)
    dlg._save_remembered_email()

    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(200))
    dlg._logout_thread()
    qapp.processEvents()

    assert dlg.access_token is None, "logout must still clear tokens"
    assert dlg._remembered_email() == "keep@example.invalid", (
        "an ordinary log-out forgot the address")


@needs_qgis
def test_logout_forgets_the_email_when_remember_is_off(mod, dlg, monkeypatch, qapp):
    dlg.current_user_email = "temp@example.invalid"
    dlg._set_remember_email(True)
    dlg._save_remembered_email()
    dlg._set_remember_email(False)          # the user turns the preference off

    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(200))
    dlg._logout_thread()
    qapp.processEvents()
    assert dlg._remembered_email() == ""


@needs_qgis
def test_session_expiry_retains_the_remembered_email(mod, dlg):
    dlg.current_user_email = "expire@example.invalid"
    dlg._set_remember_email(True)
    dlg._save_remembered_email()
    dlg._notify_session_expired()
    dlg._on_session_expired()
    assert dlg._remembered_email() == "expire@example.invalid"
    assert dlg.input_email.text() == "expire@example.invalid"


@needs_qgis
def test_restart_prefills_the_email(mod, dlg, qapp):
    """A fresh dialog, as a QGIS restart produces, must find the address."""
    dlg.current_user_email = "restart@example.invalid"
    dlg._set_remember_email(True)
    dlg._save_remembered_email()

    from qgis.PyQt import QtWidgets

    class _Bar:
        def pushMessage(self, *a, **k):
            pass

    class _Iface:
        def __init__(self):
            self._w = QtWidgets.QMainWindow()

        def mainWindow(self):
            return self._w

        def messageBar(self):
            return _Bar()

    fresh = mod.AtlasGeoHandlerDemoDialog(_Iface())
    assert fresh._remembered_email() == "restart@example.invalid"
    fresh._prefill_remembered_email()
    assert fresh.input_email.text() == "restart@example.invalid"
    fresh.shutdown_session()


@needs_qgis
def test_no_password_or_token_is_ever_persisted(mod, dlg):
    from qgis.PyQt import QtCore
    dlg.refresh_token = "REFRESH-SECRET"
    dlg.access_token = "ACCESS-SECRET"
    dlg.current_user_email = "p@example.invalid"
    dlg._set_remember_email(True)
    dlg._save_remembered_email()

    s = mod.QtCore.QSettings(dlg._SETTINGS_ORG, dlg._SETTINGS_APP)
    blob = " ".join(f"{k}={s.value(k)}" for k in s.allKeys())
    for secret in ("REFRESH-SECRET", "ACCESS-SECRET", "password"):
        assert secret not in blob, f"{secret!r} was persisted: {blob[:200]}"
    assert not s.contains(dlg._SETTINGS_KEY)
