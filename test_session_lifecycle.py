# -*- coding: utf-8 -*-
"""Regression tests for the 1.1.3 session lifecycle fix.

The defect these guard against was reported from the field: after a period of
inactivity the plugin showed "Session expired", then stayed in a fully
signed-in-looking state. Tokens were still set, the header still showed the
user, polling kept running, and Force Logout was the only way out. Signing back
in then demanded the email address again, because the logout path wiped it.

Underneath sat a timing problem no amount of reactive refresh could fix. The
Keycloak realm sets accessTokenLifespan=300 and ssoSessionIdleTimeout=1800, and
1.1.2 refreshed only when a request returned 401. Idle for thirty minutes and
the refresh token itself is dead, so the 401 arrives with nothing left to
recover from.

Nothing here touches the network: requests is replaced per-test.
"""
import ast
import io
import pathlib
import threading
import time

import pytest

HERE = pathlib.Path(__file__).resolve().parent
DIALOG = HERE / "atlas_geo_plugin_dialog.py"
ENTRY = HERE / "atlas_geo_plugin.py"

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
    spec = importlib.util.spec_from_file_location("_dlg_session", DIALOG)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Resp:
    """Minimal stand-in for a requests.Response."""
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


@pytest.fixture
def dlg(mod, qapp, monkeypatch, tmp_path):
    """A real dialog, with the modal blockers neutralised and QSettings
    pointed somewhere disposable so a test never touches the real registry."""
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
# 1. refresh succeeds
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_expired_access_token_is_refreshed(mod, dlg, monkeypatch):
    calls = []

    def fake_post(url, **kw):
        calls.append(url)
        return _Resp(200, {"access_token": "access-NEW",
                           "refresh_token": "refresh-NEW"})

    monkeypatch.setattr(mod.requests, "post", fake_post)
    assert dlg._attempt_refresh() == dlg.REFRESH_OK
    assert dlg.access_token == "access-NEW"
    assert dlg.refresh_token == "refresh-NEW", "the rotated refresh token must be kept"
    assert len(calls) == 1


@needs_qgis
def test_refresh_updates_both_tokens_atomically(mod, dlg, monkeypatch):
    """A new access token must never be left beside a stale refresh token."""
    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(
        200, {"access_token": "A2", "refresh_token": "R2"}))
    gen_before = dlg._token_generation
    assert dlg._attempt_refresh() == dlg.REFRESH_OK
    assert (dlg.access_token, dlg.refresh_token) == ("A2", "R2")
    assert dlg._token_generation == gen_before + 1


@needs_qgis
def test_200_without_a_token_is_not_treated_as_success(mod, dlg, monkeypatch):
    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(200, {}))
    # A 200 carrying no token is a broken contract, not proof the credential is
    # dead, so it is transient and the tokens are left alone.
    assert dlg._attempt_refresh() == dlg.REFRESH_TRANSIENT
    assert dlg.access_token == "access-ORIGINAL", "tokens must not be clobbered"


# ══════════════════════════════════════════════════════════════════════════
# 2. one 401 causes exactly one refresh and one retry
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_401_refreshes_once_and_retries_once(mod, dlg, monkeypatch):
    sent = []
    refreshed = []

    def fake_request(method, url, **kw):
        sent.append(kw.get("headers", {}).get("Authorization"))
        return _Resp(401 if len(sent) == 1 else 200)

    def fake_post(url, **kw):
        refreshed.append(url)
        return _Resp(200, {"access_token": "access-NEW",
                           "refresh_token": "refresh-NEW"})

    monkeypatch.setattr(mod.requests, "request", fake_request)
    monkeypatch.setattr(mod.requests, "post", fake_post)

    resp = dlg._authed_request("GET", "https://example.invalid/thing")
    assert resp.status_code == 200
    assert len(refreshed) == 1, "exactly one refresh"
    assert len(sent) == 2, "exactly one retry, so two sends in total"
    assert sent[0] == "Bearer access-ORIGINAL"
    assert sent[1] == "Bearer access-NEW", "the retry must carry the NEW token"


@needs_qgis
def test_no_infinite_retry_when_the_fresh_token_is_also_rejected(mod, dlg, monkeypatch):
    sent = []
    monkeypatch.setattr(mod.requests, "request",
                        lambda m, u, **kw: (sent.append(1), _Resp(401))[1])
    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(
        200, {"access_token": "A2", "refresh_token": "R2"}))

    resp = dlg._authed_request("GET", "https://example.invalid/thing")
    assert resp.status_code == 401
    assert len(sent) == 2, "must stop after one retry, never loop"


# ══════════════════════════════════════════════════════════════════════════
# 3. concurrent requests cause only one refresh
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_concurrent_refreshes_collapse_to_one_network_call(mod, dlg, monkeypatch):
    """Keycloak rotates refresh tokens, so a second POST with the same token
    is not merely wasteful, it presents an already-consumed credential."""
    posts = []
    lock = threading.Lock()

    def slow_post(url, **kw):
        with lock:
            posts.append(kw.get("json", {}).get("refresh_token"))
        time.sleep(0.05)          # widen the window every thread races through
        return _Resp(200, {"access_token": "access-NEW",
                           "refresh_token": "refresh-NEW"})

    monkeypatch.setattr(mod.requests, "post", slow_post)

    results = []
    threads = [threading.Thread(target=lambda: results.append(
        dlg._attempt_refresh() == dlg.REFRESH_OK)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert all(results), "every caller should end up with a usable token"
    assert len(posts) == 1, f"expected 1 refresh call, got {len(posts)}: {posts}"
    assert dlg.access_token == "access-NEW"


# ══════════════════════════════════════════════════════════════════════════
# 4 & 5. failure returns to sign-in, with no Force Logout
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_refresh_failure_returns_to_signin_without_force_logout(mod, dlg, monkeypatch):
    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(401))
    monkeypatch.setattr(mod.requests, "request", lambda m, u, **kw: _Resp(401))

    dlg._authed_request("GET", "https://example.invalid/thing")
    dlg._on_session_expired()          # the queued slot, run directly

    assert dlg.access_token is None, "access token must be cleared"
    assert dlg.refresh_token is None, "refresh token must be cleared"
    assert dlg.stacked_pages.currentIndex() == dlg.PAGE_SIGNIN, \
        "must land on the sign-in page by itself"
    msgs = " ".join(str(a) for a, k in dlg.iface.messageBar().pushed)
    assert "session expired" in msgs.lower()


@needs_qgis
def test_absent_refresh_token_fails_cleanly(mod, dlg, monkeypatch):
    dlg.refresh_token = None
    called = []
    monkeypatch.setattr(mod.requests, "post",
                        lambda url, **kw: called.append(url) or _Resp(200, {}))
    assert dlg._attempt_refresh() == dlg.REFRESH_TERMINAL
    assert not called, "must not call the network with no refresh token"


@needs_qgis
def test_session_expiry_is_idempotent(mod, dlg):
    """Several in-flight requests can each hit 401. The user must not be shown
    the same message once per request."""
    dlg._notify_session_expired()
    dlg._notify_session_expired()
    dlg._notify_session_expired()
    assert dlg._session_expired_handled is True


@needs_qgis
def test_expiry_stops_the_proactive_timer(mod, dlg):
    dlg._start_session_timer()
    assert dlg._session_timer.isActive()
    dlg._notify_session_expired()
    dlg._on_session_expired()
    assert not dlg._session_timer.isActive(), "timer must stop, not keep firing"


# ══════════════════════════════════════════════════════════════════════════
# 6-8. logout, remembered email, no stored password
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_logout_clears_all_token_state(mod, dlg, monkeypatch):
    monkeypatch.setattr(mod.requests, "post", lambda url, **kw: _Resp(200))
    dlg._logout_thread()
    assert dlg.access_token is None
    assert dlg.refresh_token is None
    # The ADDRESS is deliberately kept. Signing out ends the session; it
    # does not mean the plugin should forget who was using it, and making
    # someone retype a known address was half the reported complaint.
    assert dlg.current_user_email == "tester@example.invalid"


@needs_qgis
def test_remembered_email_is_saved_and_restored(mod, dlg):
    dlg.current_user_email = "someone@example.invalid"
    dlg._save_remembered_email()
    assert dlg._remembered_email() == "someone@example.invalid"

    dlg.input_email.clear()
    dlg._prefill_remembered_email()
    assert dlg.input_email.text() == "someone@example.invalid", \
        "the address must come back so the user does not retype it"


@needs_qgis
def test_session_expiry_keeps_the_remembered_email(mod, dlg):
    """The whole point of the second reported complaint."""
    dlg.current_user_email = "keepme@example.invalid"
    dlg._save_remembered_email()
    dlg._notify_session_expired()
    dlg._on_session_expired()
    assert dlg._remembered_email() == "keepme@example.invalid"
    assert dlg.input_email.text() == "keepme@example.invalid"


@needs_qgis
def test_refresh_token_is_never_written_to_settings(mod, dlg):
    from qgis.PyQt import QtCore
    dlg.refresh_token = "SUPER-SECRET-REFRESH"
    dlg.current_user_email = "a@example.invalid"
    dlg._save_remembered_email()

    s = mod.QtCore.QSettings(dlg._SETTINGS_ORG, dlg._SETTINGS_APP)
    for key in s.allKeys():
        assert "SUPER-SECRET-REFRESH" not in str(s.value(key)), \
            f"refresh token leaked into QSettings key {key}"
    assert not s.contains(dlg._SETTINGS_KEY), \
        "the legacy plaintext token key must never be populated"


@needs_qgis
def test_legacy_plaintext_token_is_purged(mod, dlg):
    """Anyone upgrading from 1.1.2 still has one on disk. Removing it is part
    of the fix, not just ceasing to write new ones."""
    from qgis.PyQt import QtCore
    s = mod.QtCore.QSettings(dlg._SETTINGS_ORG, dlg._SETTINGS_APP)
    s.setValue(dlg._SETTINGS_KEY, "left-over-from-1.1.2")
    s.sync()
    assert s.contains(dlg._SETTINGS_KEY)

    dlg._purge_legacy_token()

    s2 = mod.QtCore.QSettings(dlg._SETTINGS_ORG, dlg._SETTINGS_APP)
    assert not s2.contains(dlg._SETTINGS_KEY), "legacy token not removed"


def test_password_is_never_persisted_anywhere_in_source():
    """Static: no code path may write a password into settings or a file."""
    src = DIALOG.read_text(encoding="utf-8")
    tree = ast.parse(src)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else ""
        if name not in ("setValue", "write", "write_text", "dump", "dumps"):
            continue
        blob = ast.dump(node).lower()
        if "password" in blob:
            offenders.append(f"line {node.lineno}: {name}(...) mentions password")
    assert not offenders, "password may be persisted:\n  " + "\n  ".join(offenders)


# ══════════════════════════════════════════════════════════════════════════
# 9. nothing sensitive reaches the log
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_failed_refresh_logs_no_token_material(mod, dlg, monkeypatch):
    import qgis.core as qcore
    captured = []
    monkeypatch.setattr(qcore.QgsMessageLog, "logMessage",
                        staticmethod(lambda message, tag=None, level=None,
                                     notifyUser=True: captured.append(str(message))))
    monkeypatch.setattr(mod, "QgsMessageLog", qcore.QgsMessageLog, raising=False)

    dlg.refresh_token = "refresh-SENSITIVE-VALUE"

    def boom(url, **kw):
        raise mod.requests.RequestException(
            "POST https://auth.example.invalid/refresh?token=refresh-SENSITIVE-VALUE failed")

    monkeypatch.setattr(mod.requests, "post", boom)
    # A RequestException is transient: the network failed, not the credential.
    assert dlg._attempt_refresh() == dlg.REFRESH_TRANSIENT

    blob = " ".join(captured)
    for fragment in ("refresh-SENSITIVE-VALUE", "token=", "Bearer"):
        assert fragment not in blob, f"{fragment!r} reached the QGIS log: {blob[:200]}"


def test_no_source_line_logs_a_raw_token():
    src = DIALOG.read_text(encoding="utf-8")
    for lineno, line in enumerate(src.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "logMessage" in line or "_log_nonfatal" in line:
            assert "access_token" not in line and "refresh_token" not in line, \
                f"line {lineno} logs a token directly: {stripped[:80]}"


# ══════════════════════════════════════════════════════════════════════════
# 10. the six QGIS portal scanner findings must stay absent
# ══════════════════════════════════════════════════════════════════════════

def _code_only(path):
    import tokenize
    out = []
    with tokenize.open(path) as fh:
        for tok in tokenize.generate_tokens(fh.readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    return " ".join(out)


@pytest.mark.parametrize("path,pattern,label", [
    (DIALOG, "Qgis . Info",     "Qt6: unscoped Qgis.Info"),
    (DIALOG, "Qgis . Warning",  "Qt6: unscoped Qgis.Warning"),
    (ENTRY,  "Qgis . Warning",  "Qt6: unscoped Qgis.Warning"),
    (ENTRY,  "Qgis . Critical", "Qt6: unscoped Qgis.Critical"),
])
def test_qgis_portal_qt6_patterns_absent(path, pattern, label):
    assert pattern not in _code_only(path), f"{label} is back in {path.name}"


def test_qgis_portal_flake8_patterns_absent():
    entry = _code_only(ENTRY)
    assert "from . resources import *" not in entry, "F403 wildcard import is back"
    dialog_src = DIALOG.read_text(encoding="utf-8")
    tree = ast.parse(dialog_src)
    bad = [n.lineno for n in ast.walk(tree)
           if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store) and n.id == "l"]
    assert not bad, f"E741 ambiguous name 'l' assigned at lines {bad}"
