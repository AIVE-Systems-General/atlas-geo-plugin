# -*- coding: utf-8 -*-
"""Regression tests for the 100-image selection limit and chunked submission.

Stakeholder feedback raised the practical selection limit from 10 images to 100.
The upload was already chunked, but at a cap of 10 there was only ever ONE
chunk, so two things were latent and untested:

  * the plugin's chunk size (20) was LARGER than the server's code default
    MAX_BATCH (10). A 100-image selection would have been refused on its first
    request with 400 "Batch too large";
  * any mid-upload failure cancelled the whole batch, discarding jobs the server
    had already accepted. With one chunk that was harmless; with ten it would
    routinely throw away up to 90 accepted images.

Both are covered here. Nothing touches the network: requests is replaced
per-test, and no token, password or response body is asserted on.
"""
import io
import os
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
    spec = importlib.util.spec_from_file_location("_dlg_upload", DIALOG)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Resp:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return dict(self._payload)


class _Bar:
    def __init__(self):
        self.pushed = []

    def pushMessage(self, *a, **k):
        self.pushed.append(f"{a[0] if a else ''}: {a[1] if len(a) > 1 else ''}")


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
    d._install_session("access-TEST", "refresh-TEST", 300,
                       email="tester@example.invalid")
    # The poll loop sleeps 5s per cycle. Tests assert on SUBMISSION, so the wait
    # is removed rather than slept through.
    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)
    d._threads_at_start = {t.ident for t in threading.enumerate()}
    yield d
    d.shutdown_session()
    deadline = time.time() + 10
    leaked = [t.name for t in threading.enumerate()
              if t.ident not in d._threads_at_start
              and t is not threading.current_thread()
              and (t.join(timeout=max(0.0, deadline - time.time())) or t.is_alive())]
    assert not leaked, f"test leaked worker threads: {leaked}"


# ══════════════════════════════════════════════════════════════════════════
# harness: a fake /register that records the size of every chunk
# ══════════════════════════════════════════════════════════════════════════

def _make_images(tmp_path, n):
    """n tiny real files. Real paths, so file handles are really opened."""
    out = []
    for i in range(n):
        p = tmp_path / f"img_{i:04d}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 64)     # minimal JPEG header
        out.append(str(p))
    return out


@pytest.fixture
def world(mod, dlg, monkeypatch):
    """Records each /register call. `register` may be replaced per test."""
    st = {"chunks": [], "batch_ids": [], "balance": 10_000,
          "register": None, "job_seq": 0, "opened": 0, "closed": 0}

    # Metadata extraction is not under test here.
    #
    # ⚠️ THE COORDINATES MUST MOVE. The plugin has a frozen-GPS gate: three or
    # more frames within 10 m of each other are treated as a stationary track
    # and the user is asked to confirm. Handing every image the same position
    # makes that dialog fire and the selection is dropped, which looks like a
    # limit bug and is not one.
    def _moving_gps(path):
        i = int(os.path.basename(path).split("_")[1].split(".")[0])
        return (30.30 + i * 0.001, -97.70 + i * 0.001, 0.0)   # ~111 m per frame

    monkeypatch.setattr(dlg, "_extract_gps_from_image", _moving_gps)
    monkeypatch.setattr(dlg, "_extract_altitude_with_source", lambda p: (100.0, "AGL"))
    monkeypatch.setattr(dlg, "_extract_focal35_from_raw_xmp", lambda p: 24.0)
    monkeypatch.setattr(dlg, "_tilt_from_nadir", lambda p: 0.0)
    # Upload compression is not under test; keep real paths so handles open.
    monkeypatch.setattr(dlg, "_prepare_upload_files",
                        lambda paths: ([(os.path.basename(p), p) for p in paths], []))
    monkeypatch.setattr(dlg, "_fetch_balance",
                        lambda *a, **k: {"tier": "free",
                                         "available_tokens": st["balance"]})
    # Jobs resolve immediately so the run terminates; result handling is covered
    # by the existing suites, not this one.
    monkeypatch.setattr(dlg, "_fetch_job_statuses",
                        lambda ids: {j: {"status": "failed", "reason": "test"}
                                     for j in ids})

    def default_register(n_images, call_index):
        st["job_seq"] += n_images
        return _Resp(200, {"job_ids": [f"job-{st['job_seq'] - n_images + i}"
                                       for i in range(n_images)],
                           "batch_id": "batch-ONE"})

    def fake_post(url, **kw):
        if "register" not in str(url):
            return _Resp(200, {})
        files = kw.get("files") or []
        st["chunks"].append(len(files))
        data = kw.get("data") or {}
        st["batch_ids"].append(data.get("batch_id"))
        fn = st["register"] or default_register
        return fn(len(files), len(st["chunks"]) - 1)

    monkeypatch.setattr(mod.requests, "post", fake_post)
    monkeypatch.setattr(mod.requests, "request",
                        lambda m, u, **kw: fake_post(u, **kw))
    monkeypatch.setattr(mod.requests, "get", lambda u, **kw: fake_post(u, **kw))
    return st


def _run(dlg):
    """Run the worker body synchronously and capture how it ended.

    ⚠️ THE WORKER DOES NOT RAISE. It swallows every exception and reports it on
    the processing_failed signal, so a test that waits for an exception waits
    for something that never comes and passes while asserting nothing.
    """
    out = {"failed": [], "cancelled": []}
    c1 = dlg.processing_failed.connect(lambda m: out["failed"].append(m))
    c2 = dlg.processing_cancelled.connect(lambda: out["cancelled"].append(1))
    try:
        dlg._run_backend_request()
    finally:
        try:
            dlg.processing_failed.disconnect(c1)
            dlg.processing_cancelled.disconnect(c2)
        except TypeError:
            pass
    return out


# ══════════════════════════════════════════════════════════════════════════
# 1. the selection limit itself
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_selection_limit_is_100_and_chunk_is_capped_at_10(mod):
    assert mod.MAX_IMAGES_PER_SUBMISSION == 100
    assert "100" in mod.MAX_IMAGES_MESSAGE
    assert mod.UPLOAD_CHUNK_SIZE <= mod.UPLOAD_CHUNK_LIMIT == 10, (
        "the chunk must never exceed the server's code-default MAX_BATCH of 10")


@needs_qgis
def test_chunk_size_cannot_be_raised_above_the_server_default(mod, monkeypatch):
    """The env var may lower the chunk; it must not be able to raise it."""
    src = io.open(DIALOG, encoding="utf-8").read()
    assert 'min(int(os.getenv("ATLAS_UPLOAD_CHUNK"' in src.replace("\n", "").replace(" ", "") \
        or "UPLOAD_CHUNK_LIMIT))" in src, "the chunk size must be clamped, not merely defaulted"
    monkeypatch.setenv("ATLAS_UPLOAD_CHUNK", "500")
    clamped = max(1, min(int(os.getenv("ATLAS_UPLOAD_CHUNK", "10")),
                         mod.UPLOAD_CHUNK_LIMIT))
    assert clamped == 10


@needs_qgis
@pytest.mark.parametrize("n", [1, 10, 11, 99, 100])
def test_selection_sizes_are_accepted(mod, dlg, qapp, world, tmp_path, n):
    files = _make_images(tmp_path, n)
    dlg._load_image_list(files)
    assert len(dlg.selected_files) == n, f"{n} images should be accepted"


@needs_qgis
def test_101_images_are_rejected_before_any_request(mod, dlg, qapp, world, tmp_path):
    files = _make_images(tmp_path, 101)
    dlg._load_image_list(files)
    assert not dlg.selected_files, "101 images must not be loaded"
    assert world["chunks"] == [], "nothing may be uploaded when the selection is refused"


@needs_qgis
def test_go_to_processing_also_refuses_over_the_limit(mod, dlg, qapp, world, tmp_path):
    """Second gate: the check is repeated at submission, not only at selection."""
    dlg.selected_files = _make_images(tmp_path, 101)
    dlg._go_to_processing()
    assert world["chunks"] == []


# ══════════════════════════════════════════════════════════════════════════
# 2. chunk boundaries and one logical batch
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
@pytest.mark.parametrize("n,expected", [
    (1,   [1]),
    (10,  [10]),
    (11,  [10, 1]),
    (99,  [10] * 9 + [9]),
    (100, [10] * 10),
])
def test_chunk_boundaries(mod, dlg, qapp, world, tmp_path, n, expected):
    dlg.selected_files = _make_images(tmp_path, n)
    _run(dlg)
    assert world["chunks"] == expected
    assert max(world["chunks"]) <= 10, "no request may exceed the server default"


@needs_qgis
def test_one_shared_logical_batch(mod, dlg, qapp, world, tmp_path):
    dlg.selected_files = _make_images(tmp_path, 100)
    _run(dlg)
    assert world["batch_ids"][0] in (None, ""), "first chunk creates the batch"
    rest = [b for b in world["batch_ids"][1:]]
    assert len(rest) == 9, f"expected 9 follow-up chunks, got {len(rest)}"
    assert all(b == "batch-ONE" for b in rest), \
        f"every later chunk must reuse the first batch_id, got {set(rest)}"


@needs_qgis
def test_small_batches_still_behave_exactly_as_before(mod, dlg, qapp, world, tmp_path):
    dlg.selected_files = _make_images(tmp_path, 5)
    _run(dlg)
    assert world["chunks"] == [5]
    assert len(dlg._submitted_jobs) == 5


# ══════════════════════════════════════════════════════════════════════════
# 3. credits
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_exactly_sufficient_balance_is_allowed(mod, dlg, qapp, world, tmp_path):
    world["balance"] = 100
    dlg.selected_files = _make_images(tmp_path, 100)
    out = _run(dlg)
    assert not out["failed"] or "credits" not in " ".join(out["failed"]).lower(),         f"exact balance must not be refused: {out['failed']}"
    assert sum(world["chunks"]) == 100


@needs_qgis
def test_insufficient_balance_blocks_before_any_upload(mod, dlg, qapp, world, tmp_path):
    world["balance"] = 40
    dlg.selected_files = _make_images(tmp_path, 100)
    out = _run(dlg)
    assert out["failed"], "an insufficient balance must be reported"
    msg = " ".join(out["failed"])
    assert "100" in msg and "40" in msg, f"must show required and available: {msg}"
    assert world["chunks"] == [], "no image may be uploaded when the pre-check fails"


@needs_qgis
def test_unreadable_balance_does_not_block(mod, dlg, qapp, world, tmp_path, monkeypatch):
    """A billing blip must not strand a user who does have credits."""
    monkeypatch.setattr(dlg, "_fetch_balance", lambda *a, **k: None)
    dlg.selected_files = _make_images(tmp_path, 10)
    out = _run(dlg)
    assert not [m for m in out["failed"] if "credits" in m.lower()], out["failed"]
    assert world["chunks"] == [10]


@needs_qgis
def test_server_refuses_a_later_chunk_and_earlier_jobs_are_kept(
        mod, dlg, qapp, world, tmp_path):
    """The authoritative 402 arrives on chunk 5. Chunks 1-4 must survive."""
    def register(n_images, call_index):
        if call_index >= 4:
            return _Resp(402, {"detail": {"needed": 10, "available": 0}})
        world["job_seq"] += n_images
        return _Resp(200, {"job_ids": [f"job-{world['job_seq'] - n_images + i}"
                                       for i in range(n_images)],
                           "batch_id": "batch-ONE"})
    world["register"] = register
    dlg.selected_files = _make_images(tmp_path, 100)
    _run(dlg)

    assert len(world["chunks"]) == 5, "submission must STOP at the refusal"
    assert dlg._submission["submitted"] == 40
    assert dlg._submission["not_submitted"] == 60
    assert dlg._submission["stopped_reason"] == "refused"
    assert dlg._submission["unknown"] == 0, \
        "an authoritative 402 leaves nothing in doubt"
    assert len(dlg._submitted_jobs) == 40, "accepted jobs must be preserved"


@needs_qgis
def test_partial_acceptance_is_reported_to_the_user(
        mod, dlg, qapp, world, tmp_path):
    def register(n_images, call_index):
        if call_index >= 2:
            return _Resp(402, {"detail": {"needed": 10, "available": 0}})
        world["job_seq"] += n_images
        return _Resp(200, {"job_ids": [f"job-{world['job_seq'] - n_images + i}"
                                       for i in range(n_images)],
                           "batch_id": "batch-ONE"})
    world["register"] = register
    dlg.selected_files = _make_images(tmp_path, 50)
    _run(dlg)
    for _ in range(40):
        qapp.processEvents()
    said = " ".join(dlg.iface.messageBar().pushed)
    assert "20 of 50" in said, f"the split must be stated plainly: {said}"


@needs_qgis
def test_first_chunk_refused_raises_and_submits_nothing(
        mod, dlg, qapp, world, tmp_path):
    """Nothing accepted means there is no partial result to preserve."""
    world["register"] = lambda n, i: _Resp(402, {"detail": {"needed": 10,
                                                            "available": 0}})
    dlg.selected_files = _make_images(tmp_path, 30)
    out = _run(dlg)
    assert out["failed"], "a refused first chunk must be reported"
    assert len(world["chunks"]) == 1
    assert not getattr(dlg, "_submitted_jobs", None)


@needs_qgis
def test_no_batch_cancel_on_a_rejected_chunk(mod, dlg, qapp, world, tmp_path,
                                             monkeypatch):
    """Accepted jobs are not ours to withdraw. This was the old behaviour."""
    cancelled = []
    monkeypatch.setattr(dlg, "_cancel_batch_backend",
                        lambda *a, **k: cancelled.append(1))

    def register(n_images, call_index):
        if call_index >= 3:
            return _Resp(500, {}, text="server error")
        world["job_seq"] += n_images
        return _Resp(200, {"job_ids": [f"job-{world['job_seq'] - n_images + i}"
                                       for i in range(n_images)],
                           "batch_id": "batch-ONE"})
    world["register"] = register
    dlg.selected_files = _make_images(tmp_path, 100)
    _run(dlg)
    assert not cancelled, "a rejected chunk must NOT refund already-accepted jobs"
    assert len(dlg._submitted_jobs) == 30


# ══════════════════════════════════════════════════════════════════════════
# 4. cancellation, session, connectivity
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_cancellation_between_chunks_stops_and_uses_existing_cancel(
        mod, dlg, qapp, world, tmp_path, monkeypatch):
    cancelled = []
    monkeypatch.setattr(dlg, "_cancel_batch_backend",
                        lambda *a, **k: cancelled.append(1))

    def register(n_images, call_index):
        if call_index == 2:
            dlg._cancel_flag.set()          # user presses Cancel mid-run
        world["job_seq"] += n_images
        return _Resp(200, {"job_ids": [f"job-{world['job_seq'] - n_images + i}"
                                       for i in range(n_images)],
                           "batch_id": "batch-ONE"})
    world["register"] = register
    dlg.selected_files = _make_images(tmp_path, 100)
    _run(dlg)
    assert len(world["chunks"]) == 3, "no further chunk may be submitted"
    assert dlg._submission["stopped_reason"] == "cancelled"
    assert cancelled, "cancellation keeps the established refund behaviour"


@needs_qgis
def test_session_expiry_during_submission_stops_the_run(
        mod, dlg, qapp, world, tmp_path):
    def register(n_images, call_index):
        if call_index >= 2:
            return _Resp(401, {})
        world["job_seq"] += n_images
        return _Resp(200, {"job_ids": [f"job-{world['job_seq'] - n_images + i}"
                                       for i in range(n_images)],
                           "batch_id": "batch-ONE"})
    world["register"] = register
    dlg.selected_files = _make_images(tmp_path, 100)
    _run(dlg)
    assert len(world["chunks"]) == 3, "submission stops when the session ends"
    assert len(dlg._submitted_jobs) == 20, "accepted jobs survive"


@needs_qgis
def test_transient_connectivity_failure_leaves_that_chunk_unknown(
        mod, dlg, qapp, world, tmp_path):
    """A lost connection is NOT proof the server refused the chunk."""
    def register(n_images, call_index):
        if call_index >= 3:
            raise mod.requests.ConnectionError("network down")
        world["job_seq"] += n_images
        return _Resp(200, {"job_ids": [f"job-{world['job_seq'] - n_images + i}"
                                       for i in range(n_images)],
                           "batch_id": "batch-ONE"})
    world["register"] = register
    dlg.selected_files = _make_images(tmp_path, 100)
    _run(dlg)
    s = dlg._submission
    assert len(dlg._submitted_jobs) == 30, "confirmed jobs are kept"
    assert s["submitted"] == 30
    assert s["unknown"] == 10, "the in-flight chunk is in doubt, not refused"
    assert s["not_submitted"] == 60, "only the chunks never sent are certain"
    assert s["stopped_reason"] == "unknown"


@needs_qgis
def test_ambiguous_response_is_not_resubmitted(mod, dlg, qapp, world, tmp_path):
    """/register has no idempotency key, so a retry could double-charge."""
    def register(n_images, call_index):
        if call_index == 2:
            raise mod.requests.Timeout("no answer")
        world["job_seq"] += n_images
        return _Resp(200, {"job_ids": [f"job-{world['job_seq'] - n_images + i}"
                                       for i in range(n_images)],
                           "batch_id": "batch-ONE"})
    world["register"] = register
    dlg.selected_files = _make_images(tmp_path, 100)
    _run(dlg)
    assert len(world["chunks"]) == 3, \
        "the timed-out chunk must NOT be sent again; that could charge twice"


# ══════════════════════════════════════════════════════════════════════════
# 5. progress, handles, threads, responsiveness
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_aggregate_progress_totals(mod, dlg, qapp, world, tmp_path):
    dlg.selected_files = _make_images(tmp_path, 100)
    _run(dlg)
    s = dlg._submission
    assert s["total"] == 100
    assert s["submitted"] == 100
    assert s["not_submitted"] == 0
    assert s["chunks_total"] == 10 and s["chunks_done"] == 10
    assert s["accepted"] == 100


@needs_qgis
def test_progress_totals_after_a_partial_run(mod, dlg, qapp, world, tmp_path):
    def register(n_images, call_index):
        if call_index >= 6:
            return _Resp(500, {}, text="boom")
        world["job_seq"] += n_images
        return _Resp(200, {"job_ids": [f"job-{world['job_seq'] - n_images + i}"
                                       for i in range(n_images)],
                           "batch_id": "batch-ONE"})
    world["register"] = register
    dlg.selected_files = _make_images(tmp_path, 100)
    _run(dlg)
    s = dlg._submission
    # A 5xx is ambiguous, so that chunk is unknown rather than not-submitted.
    assert s["submitted"] + s["not_submitted"] + s["unknown"] == s["total"] == 100
    assert s["submitted"] == 60
    assert s["unknown"] == 10
    assert s["not_submitted"] == 30


@needs_qgis
def test_every_file_handle_is_closed(mod, dlg, qapp, world, tmp_path, monkeypatch):
    """A 100-image run opens handles in ten batches; none may be left open."""
    import builtins
    real_open = builtins.open
    live = set()

    def tracking_open(path, mode="r", *a, **k):
        fh = real_open(path, mode, *a, **k)
        p = str(path)
        if "b" in mode and p.endswith(".jpg"):
            live.add(fh)
            real_close = fh.close

            def closing():
                live.discard(fh)
                return real_close()
            fh.close = closing
        return fh

    monkeypatch.setattr(builtins, "open", tracking_open)
    dlg.selected_files = _make_images(tmp_path, 100)
    _run(dlg)
    monkeypatch.setattr(builtins, "open", real_open)
    assert not live, f"{len(live)} image file handles were left open"


@needs_qgis
def test_images_are_not_all_held_in_memory(mod, dlg, qapp, world, tmp_path):
    """Only the chunk in flight is materialised, never the whole selection."""
    peak = {"n": 0}
    real_post = mod.requests.post

    def counting_post(url, **kw):
        if "register" in str(url):
            peak["n"] = max(peak["n"], len(kw.get("files") or []))
        return real_post(url, **kw)

    mod.requests.post = counting_post
    try:
        dlg.selected_files = _make_images(tmp_path, 100)
        _run(dlg)
    finally:
        mod.requests.post = real_post
    assert peak["n"] <= 10, f"{peak['n']} images were in one request"


@needs_qgis
def test_ui_thread_is_not_blocked_during_submission(mod, dlg, qapp, world, tmp_path):
    """The worker body must be runnable off the GUI thread and leave it free."""
    dlg.selected_files = _make_images(tmp_path, 100)
    ticks = []
    from qgis.PyQt import QtCore
    t = QtCore.QTimer()
    t.timeout.connect(lambda: ticks.append(1))
    t.start(5)

    worker = threading.Thread(target=dlg._run_backend_request, daemon=True)
    worker.start()
    deadline = time.time() + 20
    while worker.is_alive() and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    worker.join(10)
    t.stop()
    assert not worker.is_alive(), "submission did not finish"
    assert ticks, "the GUI event loop never ran: the UI thread was blocked"
    assert sum(world["chunks"]) == 100


@needs_qgis
def test_worker_threads_are_released(mod, dlg, qapp, world, tmp_path):
    before = {t.ident for t in threading.enumerate()}
    dlg.selected_files = _make_images(tmp_path, 50)
    worker = threading.Thread(target=dlg._run_backend_request, daemon=True)
    worker.start()
    worker.join(30)
    assert not worker.is_alive()
    leaked = [t.name for t in threading.enumerate()
              if t.ident not in before and t.is_alive() and t is not worker]
    assert not leaked, f"threads left running: {leaked}"


# ══════════════════════════════════════════════════════════════════════════
# 6. definite refusal versus ambiguous outcome
# ══════════════════════════════════════════════════════════════════════════

def _stop_after(world, call_index, failure):
    """Succeed until `call_index`, then raise/return `failure`."""
    def register(n_images, idx):
        if idx >= call_index:
            if isinstance(failure, BaseException):
                raise failure
            return failure
        world["job_seq"] += n_images
        return _Resp(200, {"job_ids": [f"job-{world['job_seq'] - n_images + i}"
                                       for i in range(n_images)],
                           "batch_id": "batch-ONE"})
    world["register"] = register


@needs_qgis
@pytest.mark.parametrize("failure,label", [
    (_Resp(402, {"detail": {"needed": 10, "available": 0}}), "insufficient credits"),
    (_Resp(400, {}, text="Batch too large. Max 10 images."), "batch too large"),
    (_Resp(401, {}), "authenticated rejection"),
    (_Resp(413, {"detail": "file too large"}), "unsupported input"),
    (_Resp(429, {"detail": "busy"}), "rate/queue rejection"),
    (_Resp(507, {"detail": "disk"}), "disk rejection"),
])
def test_authoritative_refusals_are_counted_as_not_submitted(
        mod, dlg, qapp, world, tmp_path, failure, label):
    """The server answered before creating work, so nothing is in doubt."""
    _stop_after(world, 3, failure)
    dlg.selected_files = _make_images(tmp_path, 100)
    _run(dlg)
    s = dlg._submission
    assert s["unknown"] == 0, f"{label} must not be reported as ambiguous"
    assert s["submitted"] == 30
    assert s["not_submitted"] == 70
    assert s["stopped_reason"] == "refused"


@needs_qgis
@pytest.mark.parametrize("failure,label", [
    (None, "timeout before the server received the request"),
    ("after", "timeout after the server accepted the chunk"),
    ("reset", "connection reset after acceptance"),
    (_Resp(500, {}, text="boom"), "5xx before commit"),
    (_Resp(502, {}, text="gateway"), "5xx after commit"),
    ("unreadable", "accepted but the reply could not be read"),
    ("nojobs", "200 with no job ids"),
])
def test_ambiguous_outcomes_are_counted_as_unknown(
        mod, dlg, qapp, world, tmp_path, failure, label):
    """None of these prove the chunk was refused, so none may be called
    'not submitted'."""
    if failure is None:
        f = mod.requests.Timeout("no answer")
    elif failure == "after":
        f = mod.requests.Timeout("read timed out after upload")
    elif failure == "reset":
        f = mod.requests.ConnectionError("connection reset by peer")
    elif failure == "unreadable":
        class _Bad(_Resp):
            def json(self):
                raise ValueError("not json")
        f = _Bad(200, {})
    elif failure == "nojobs":
        f = _Resp(200, {"job_ids": [], "batch_id": "batch-ONE"})
    else:
        f = failure
    _stop_after(world, 4, f)
    dlg.selected_files = _make_images(tmp_path, 100)
    _run(dlg)
    s = dlg._submission
    assert s["unknown"] == 10, f"{label} must be reported as unknown"
    assert s["submitted"] == 40
    assert s["not_submitted"] == 50
    assert s["stopped_reason"] == "unknown"
    assert s["submitted"] + s["not_submitted"] + s["unknown"] == 100


@needs_qgis
def test_an_ambiguous_chunk_is_never_resubmitted(mod, dlg, qapp, world, tmp_path):
    _stop_after(world, 3, mod.requests.Timeout("no answer"))
    dlg.selected_files = _make_images(tmp_path, 100)
    _run(dlg)
    assert len(world["chunks"]) == 4, \
        "the ambiguous chunk must be sent once and never repeated"


@needs_qgis
def test_the_user_is_told_not_to_retry_unknown_images(
        mod, dlg, qapp, world, tmp_path):
    _stop_after(world, 2, mod.requests.ConnectionError("reset"))
    dlg.selected_files = _make_images(tmp_path, 50)
    _run(dlg)
    for _ in range(40):
        qapp.processEvents()
    said = " ".join(dlg.iface.messageBar().pushed).lower()
    assert "20 of 50" in said
    assert "could not be confirmed" in said
    assert "before sending those images again" in said
    # 20 images genuinely never left the client, so naming them is correct.
    # What must never happen is folding the 10 UNKNOWN ones into that figure.
    assert "20 were not submitted" in said, "the certain shortfall is reported"
    assert "30 were not submitted" not in said, \
        "unknown images must never be counted as definitely not submitted"


@needs_qgis
def test_the_balance_is_refreshed_after_an_ambiguous_outcome(
        mod, dlg, qapp, world, tmp_path, monkeypatch):
    """An ambiguous chunk may have consumed credits, so the local figure is
    no longer trustworthy."""
    calls = []
    real = dlg._fetch_balance
    monkeypatch.setattr(dlg, "_fetch_balance",
                        lambda *a, **k: (calls.append(1), real())[1])
    _stop_after(world, 2, mod.requests.Timeout("x"))
    dlg.selected_files = _make_images(tmp_path, 50)
    _run(dlg)
    assert len(calls) >= 2, "pre-check plus a refresh after the ambiguity"


@needs_qgis
def test_confirmed_jobs_are_not_cancelled_by_a_later_ambiguity(
        mod, dlg, qapp, world, tmp_path, monkeypatch):
    cancelled = []
    monkeypatch.setattr(dlg, "_cancel_batch_backend",
                        lambda *a, **k: cancelled.append(1))
    _stop_after(world, 5, mod.requests.Timeout("x"))
    dlg.selected_files = _make_images(tmp_path, 100)
    _run(dlg)
    assert not cancelled, "an ambiguous chunk must not cancel confirmed jobs"
    assert len(dlg._submitted_jobs) == 50


@needs_qgis
def test_recovery_through_a_batch_lookup_when_one_exists(
        mod, dlg, qapp, world, tmp_path):
    """If a batch->jobs lookup is ever added, an ambiguous chunk resolves and
    the run continues with the recovered ids merged in."""
    dlg._batch_jobs_lookup = lambda batch_id: [f"recovered-{i}" for i in range(40)]
    _stop_after(world, 3, mod.requests.Timeout("x"))
    dlg.selected_files = _make_images(tmp_path, 100)
    _run(dlg)
    s = dlg._submission
    assert s["unknown"] == 0, "a resolved chunk is no longer in doubt"
    assert s["stopped_reason"] == "recovered"
    assert s["submitted"] == 40


@needs_qgis
def test_unresolved_recovery_leaves_the_chunk_unknown(
        mod, dlg, qapp, world, tmp_path):
    """The real case today: no batch lookup exists, so it stays unknown."""
    assert getattr(dlg, "_batch_jobs_lookup", None) is None, \
        "the service exposes no batch->jobs endpoint; see _recover_unknown_chunk"
    _stop_after(world, 3, mod.requests.Timeout("x"))
    dlg.selected_files = _make_images(tmp_path, 100)
    _run(dlg)
    assert dlg._submission["unknown"] == 10
    assert dlg._submission["stopped_reason"] == "unknown"


@needs_qgis
def test_a_failing_recovery_hook_does_not_become_a_second_error(
        mod, dlg, qapp, world, tmp_path):
    def boom(batch_id):
        raise RuntimeError("lookup exploded")
    dlg._batch_jobs_lookup = boom
    _stop_after(world, 3, mod.requests.Timeout("x"))
    dlg.selected_files = _make_images(tmp_path, 100)
    _run(dlg)
    assert dlg._submission["unknown"] == 10, "it must fall back to unknown"
