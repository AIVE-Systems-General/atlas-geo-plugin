# -*- coding: utf-8 -*-
"""Exported reports must not all be called the same thing.

Reported: the exported report always uses the same filename. The dialog was
opened with a hard-coded "atlas_report.pdf" every time, so a second export
proposed exactly the same name as the first and quietly invited the user to
overwrite their previous report.

The CSV was worse. It is derived from the chosen PDF path and written with a
plain open(..., "w"), so the save dialog's overwrite prompt never covered it:
an existing .csv was replaced with no prompt at all.

The clock is injected everywhere. Nothing here sleeps.
"""
import datetime
import os
import pathlib

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

T1 = datetime.datetime(2026, 9, 24, 17, 45, 33)
T2 = datetime.datetime(2026, 9, 24, 17, 45, 34)


@pytest.fixture(scope="module")
def qapp():
    from qgis.PyQt import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def mod(qapp):
    import importlib.util
    spec = importlib.util.spec_from_file_location("_dlg_report", DIALOG)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Bar:
    def __init__(self):
        self.pushed = []

    def pushMessage(self, *a, **k):
        self.pushed.append(str(a))


@pytest.fixture
def dlg(mod, qapp, monkeypatch, tmp_path):
    from qgis.PyQt import QtWidgets, QtCore

    for name in ("warning", "critical", "information", "question", "about"):
        monkeypatch.setattr(QtWidgets.QMessageBox, name,
                            staticmethod(lambda *a, **k:
                                         QtWidgets.QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QtWidgets.QDialog, "exec", lambda self, *a, **k: 0)
    # ⚠️ QSettings.setPath IS NOT ENOUGH and quietly does nothing.
    # QSettings(org, app) resolves through NativeFormat while setPath is keyed
    # BY FORMAT, and Qt caches the resolved file for the process lifetime. These
    # tests were therefore writing to the real ~/.config/AIVE/AtlasGeo.conf --
    # the tester's own profile. Replacing the QSettings the plugin constructs is
    # unambiguous: everything under test goes to a file inside tmp_path.
    _real_qs = QtCore.QSettings
    _ini = str(tmp_path / "AtlasGeo.ini")

    def _scoped(*a, **k):
        return _real_qs(_ini, _real_qs.Format.IniFormat)

    _scoped.Format = _real_qs.Format
    _scoped.Scope = _real_qs.Scope
    monkeypatch.setattr(mod.QtCore, "QSettings", _scoped)

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
    d._notices = []
    monkeypatch.setattr(d, "_themed_notice",
                        lambda title, msg, **k: d._notices.append((title, msg)))
    # One completed job, so export_report has something to write.
    d._job_results = {"job-1": {"file": "a.jpg", "num_inliers": 42,
                                "elapsed_s": 1.5, "lat": 30.3, "lon": -97.7,
                                "tier": 1, "angle_deg": 0.0}}
    d._failed_jobs = {}
    return d


@pytest.fixture
def saves(monkeypatch, mod, tmp_path):
    """Capture what the save dialog PROPOSES, and choose where it lands."""
    from qgis.PyQt import QtWidgets
    st = {"proposed": [], "answer": None, "cancel": False}

    def fake_save(parent, title, proposed, filt):
        st["proposed"].append(proposed)
        if st["cancel"]:
            return "", ""
        target = st["answer"] or str(tmp_path / os.path.basename(proposed))
        return target, filt

    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        staticmethod(fake_save))
    return st


def _freeze(monkeypatch, mod, when):
    """Freeze the plugin's clock without touching real time."""
    class _FrozenDT(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return when
    monkeypatch.setattr(mod.datetime, "datetime", _FrozenDT)


# ══════════════════════════════════════════════════════════════════════════
# the helper
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_timestamp_format_is_filesystem_safe(mod):
    stamp = mod.AtlasGeoHandlerDemoDialog._report_timestamp(T1)
    assert stamp == "20260924_174533"
    for bad in ':/\\*?"<>|':
        assert bad not in stamp, f"{bad!r} is not valid in a Windows filename"


@needs_qgis
def test_exact_pdf_default_filename(mod):
    name = mod.AtlasGeoHandlerDemoDialog._report_basename("pdf", T1)
    assert name == "ATLAS_Geo_Dock_Report_20260924_174533.pdf"


@needs_qgis
def test_exact_csv_default_filename(mod):
    name = mod.AtlasGeoHandlerDemoDialog._report_basename("csv", T1)
    assert name == "ATLAS_Geo_Dock_Report_20260924_174533.csv"


@needs_qgis
def test_pdf_and_csv_share_one_timestamp(mod):
    pdf = mod.AtlasGeoHandlerDemoDialog._report_basename("pdf", T1)
    csv = mod.AtlasGeoHandlerDemoDialog._report_basename("csv", T1)
    assert os.path.splitext(pdf)[0] == os.path.splitext(csv)[0]


@needs_qgis
def test_a_second_timestamp_is_never_appended(mod):
    """Requirement 6: a name that already carries this action's stamp keeps it."""
    already = "ATLAS_Geo_Dock_Report_20260924_174533"
    name = mod.AtlasGeoHandlerDemoDialog._report_basename("pdf", T2, base=already)
    assert name == "ATLAS_Geo_Dock_Report_20260924_174533.pdf"
    assert name.count("2026") == 1, "the name was stamped twice"


@needs_qgis
def test_different_times_give_different_names(mod):
    a = mod.AtlasGeoHandlerDemoDialog._report_basename("pdf", T1)
    b = mod.AtlasGeoHandlerDemoDialog._report_basename("pdf", T2)
    assert a != b


@needs_qgis
def test_non_clobbering_path_adds_a_deterministic_suffix(mod, tmp_path):
    f = mod.AtlasGeoHandlerDemoDialog._non_clobbering_path
    target = tmp_path / "r.csv"
    assert f(str(target)) == str(target)          # free: unchanged
    target.write_text("first")
    assert f(str(target)) == str(tmp_path / "r_2.csv")
    (tmp_path / "r_2.csv").write_text("second")
    assert f(str(target)) == str(tmp_path / "r_3.csv")


# ══════════════════════════════════════════════════════════════════════════
# the export action
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_the_dialog_proposes_a_timestamped_name(mod, dlg, saves, monkeypatch):
    """The reported defect: the proposal was always 'atlas_report.pdf'."""
    _freeze(monkeypatch, mod, T1)
    dlg.export_report()
    assert saves["proposed"], "the save dialog was never opened"
    # The proposal is now an absolute path (see the macOS starting-directory
    # fix); the NAME is what this test is about.
    assert (os.path.basename(saves["proposed"][0])
            == "ATLAS_Geo_Dock_Report_20260924_174533.pdf")


@needs_qgis
def test_two_exports_propose_different_names(mod, dlg, saves, monkeypatch):
    _freeze(monkeypatch, mod, T1)
    dlg.export_report()
    _freeze(monkeypatch, mod, T2)
    dlg.export_report()
    assert saves["proposed"][0] != saves["proposed"][1], \
        "two exports proposed the same filename"


@needs_qgis
def test_both_files_are_written_and_share_the_stem(mod, dlg, saves, monkeypatch,
                                                   tmp_path):
    _freeze(monkeypatch, mod, T1)
    dlg.export_report()
    pdfs = sorted(p.name for p in tmp_path.glob("*.pdf"))
    csvs = sorted(p.name for p in tmp_path.glob("*.csv"))
    assert pdfs == ["ATLAS_Geo_Dock_Report_20260924_174533.pdf"]
    assert csvs == ["ATLAS_Geo_Dock_Report_20260924_174533.csv"]


@needs_qgis
def test_an_existing_csv_is_not_silently_overwritten(mod, dlg, saves,
                                                     monkeypatch, tmp_path):
    """The save dialog only ever confirms the PDF. The CSV is derived, so
    nothing asks the user before replacing it."""
    _freeze(monkeypatch, mod, T1)
    victim = tmp_path / "ATLAS_Geo_Dock_Report_20260924_174533.csv"
    victim.write_text("PRECIOUS DATA")
    dlg.export_report()
    assert victim.read_text() == "PRECIOUS DATA", "an existing CSV was overwritten"
    assert (tmp_path / "ATLAS_Geo_Dock_Report_20260924_174533_2.csv").exists(), \
        "the new CSV should have taken a deterministic suffix"


@needs_qgis
def test_a_user_edited_filename_is_respected(mod, dlg, saves, monkeypatch,
                                             tmp_path):
    _freeze(monkeypatch, mod, T1)
    saves["answer"] = str(tmp_path / "my own name.pdf")
    dlg.export_report()
    assert (tmp_path / "my own name.pdf").exists()
    assert (tmp_path / "my own name.csv").exists()


@needs_qgis
def test_a_cancelled_dialog_writes_nothing(mod, dlg, saves, monkeypatch, tmp_path):
    _freeze(monkeypatch, mod, T1)
    saves["cancel"] = True
    dlg.export_report()
    assert list(tmp_path.glob("*.pdf")) == []
    assert list(tmp_path.glob("*.csv")) == []


@needs_qgis
def test_a_failed_export_leaves_no_partial_final_file(mod, dlg, saves,
                                                      monkeypatch, tmp_path):
    """A crash partway through must not leave a truncated report sitting where
    a complete one belongs."""
    _freeze(monkeypatch, mod, T1)
    import csv as _csv
    real_writer = _csv.writer

    def exploding_writer(fh, *a, **k):
        w = real_writer(fh, *a, **k)
        fh.write("partial,row\n")
        raise RuntimeError("disk gave up")

    monkeypatch.setattr(_csv, "writer", exploding_writer)
    dlg.export_report()
    finals = [p.name for p in tmp_path.glob("*.csv")
              if not p.name.endswith(".part")]
    assert finals == [], f"a partial CSV was left behind: {finals}"
    assert any("failed" in t.lower() for t, _ in dlg._notices), \
        "the user was not told the export failed"


@needs_qgis
def test_nothing_to_export_opens_no_dialog(mod, dlg, saves):
    dlg._job_results = {}
    dlg._failed_jobs = {}
    dlg.export_report()
    assert saves["proposed"] == []
