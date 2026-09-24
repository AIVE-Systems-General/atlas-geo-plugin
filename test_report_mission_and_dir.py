# -*- coding: utf-8 -*-
"""Report filenames carry the mission name, and the save dialog remembers where.

Two pieces of stakeholder feedback, extending the timestamp work:

  * the filename could identify the mission;
  * on macOS the save dialog reopened at the user's home folder every time
    instead of where the last report went.

The second is a path problem, not a macOS quirk to work around: the dialog was
given a bare filename, which is interpreted relative to the process working
directory. Windows usually lands somewhere plausible; the native macOS panel
ignores it entirely. An absolute path fixes both.

QSettings is redirected into tmp_path for every test, so nothing here can touch
the tester's real QGIS profile.
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
T2 = datetime.datetime(2026, 9, 24, 18, 1, 2)


@pytest.fixture(scope="module")
def qapp():
    from qgis.PyQt import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def mod(qapp):
    import importlib.util
    spec = importlib.util.spec_from_file_location("_dlg_mission", DIALOG)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Bar:
    def __init__(self):
        self.pushed = []

    def pushMessage(self, *a, **k):
        self.pushed.append(str(a))


@pytest.fixture
def profile(mod, qapp, tmp_path, monkeypatch):
    """Point the PLUGIN's settings at a file inside tmp_path.

    ⚠️ QSettings.setPath IS NOT ENOUGH, and quietly does nothing here.
    QSettings(org, app) resolves through NativeFormat, setPath is keyed by
    format, and Qt caches the resolved file for the life of the process. In
    practice these tests were writing to the real ~/.config/AIVE/AtlasGeo.conf
    -- the tester's own profile, which they must never touch.

    Replacing the QSettings the plugin constructs is unambiguous: every read
    and write under test goes to a file in tmp_path and nowhere else. Real
    profile scoping is QGIS's job, not the plugin's; what the plugin owes is to
    use the profile-scoped org/app namespace, which the tests below check.
    """
    from qgis.PyQt import QtCore
    ini = tmp_path / "profile" / "AtlasGeo.ini"
    ini.parent.mkdir(parents=True, exist_ok=True)
    # Captured BEFORE patching: mod.QtCore is the same module object every
    # caller sees, so replacing the name would also break QSettings.Format
    # lookups inside this very factory.
    real = QtCore.QSettings

    def _scoped(*a, **k):
        return real(str(ini), real.Format.IniFormat)

    _scoped.Format = real.Format
    _scoped.Scope = real.Scope
    _scoped.setPath = real.setPath
    _scoped.setDefaultFormat = real.setDefaultFormat
    monkeypatch.setattr(mod.QtCore, "QSettings", _scoped)
    return ini


@pytest.fixture
def dlg(mod, qapp, monkeypatch, tmp_path, profile):
    from qgis.PyQt import QtWidgets

    for name in ("warning", "critical", "information", "question", "about"):
        monkeypatch.setattr(QtWidgets.QMessageBox, name,
                            staticmethod(lambda *a, **k:
                                         QtWidgets.QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(QtWidgets.QDialog, "exec", lambda self, *a, **k: 0)

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
    d._job_results = {"job-1": {"file": "a.jpg", "num_inliers": 42,
                                "elapsed_s": 1.5, "lat": 30.3, "lon": -97.7,
                                "tier": 1, "angle_deg": 0.0}}
    d._failed_jobs = {}
    return d


@pytest.fixture
def saves(monkeypatch, tmp_path):
    from qgis.PyQt import QtWidgets
    st = {"proposed": [], "answer": None, "cancel": False}

    def fake_save(parent, title, proposed, filt):
        st["proposed"].append(proposed)
        if st["cancel"]:
            return "", ""
        target = st["answer"] or str(tmp_path / "out" / os.path.basename(proposed))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        return target, filt

    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        staticmethod(fake_save))
    return st


def _freeze(monkeypatch, mod, when):
    class _FrozenDT(datetime.datetime):
        @classmethod
        def now(cls, tz=None):
            return when
    monkeypatch.setattr(mod.datetime, "datetime", _FrozenDT)


def _set_mission(dlg, name):
    from qgis.PyQt import QtWidgets
    w = getattr(dlg, "input_mission_name", None)
    if w is None:
        w = QtWidgets.QLineEdit()
        dlg.input_mission_name = w
    w.setText(name)


# ══════════════════════════════════════════════════════════════════════════
# sanitisation
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
@pytest.mark.parametrize("raw,expected", [
    ("Austin Flight 01", "Austin_Flight_01"),
    ("Austin_Flight_01", "Austin_Flight_01"),
    ("north/field:2024", "north_field_2024"),
    ("a\\b|c?d*e", "a_b_c_d_e"),
    ('quote"name<>', "quote_name"),
    ("Austin   Flight  --  01", "Austin_Flight_01"),
    ("trailing dots...", "trailing_dots"),
    ("trailing spaces   ", "trailing_spaces"),
    ("  leading", "leading"),
    ("with\x00control\x1fchars", "with_control_chars"),
])
def test_sanitize_produces_safe_components(mod, raw, expected):
    assert mod.AtlasGeoHandlerDemoDialog._sanitize_mission_name(raw) == expected


@needs_qgis
@pytest.mark.parametrize("raw", ["", "   ", "///", "...", "___", None, ":::"])
def test_entirely_invalid_names_fall_back(mod, raw):
    assert mod.AtlasGeoHandlerDemoDialog._sanitize_mission_name(raw) == ""


@needs_qgis
def test_a_long_mission_name_is_bounded(mod):
    safe = mod.AtlasGeoHandlerDemoDialog._sanitize_mission_name("A" * 400)
    assert 0 < len(safe) <= mod.AtlasGeoHandlerDemoDialog._MISSION_NAME_MAX
    name = mod.AtlasGeoHandlerDemoDialog._report_basename(
        "pdf", T1, mission="A" * 400)
    assert name.endswith("_ATLAS_Report_20260924_174533.pdf"), \
        "the timestamp and extension must survive truncation"


@needs_qgis
@pytest.mark.parametrize("reserved", ["CON", "con", "PRN", "NUL", "COM1", "LPT9"])
def test_windows_reserved_names_are_avoided(mod, reserved):
    safe = mod.AtlasGeoHandlerDemoDialog._sanitize_mission_name(reserved)
    assert safe.upper() not in mod.AtlasGeoHandlerDemoDialog._WINDOWS_RESERVED
    assert safe, "a reserved name should be adjusted, not discarded"


@needs_qgis
def test_no_path_or_hidden_metadata_leaks_into_the_name(mod):
    safe = mod.AtlasGeoHandlerDemoDialog._sanitize_mission_name(
        "/Users/someone/secret/Mission")
    assert "/" not in safe and "\\" not in safe
    assert not os.path.isabs(safe)


# ══════════════════════════════════════════════════════════════════════════
# filenames
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_mission_name_and_timestamp(mod):
    name = mod.AtlasGeoHandlerDemoDialog._report_basename(
        "pdf", T1, mission="Austin Flight 01")
    assert name == "Austin_Flight_01_ATLAS_Report_20260924_174533.pdf"


@needs_qgis
def test_pdf_and_csv_share_the_mission_stem(mod):
    pdf = mod.AtlasGeoHandlerDemoDialog._report_basename("pdf", T1, mission="Site B")
    csv = mod.AtlasGeoHandlerDemoDialog._report_basename("csv", T1, mission="Site B")
    assert os.path.splitext(pdf)[0] == os.path.splitext(csv)[0]
    assert pdf.endswith(".pdf") and csv.endswith(".csv")


@needs_qgis
def test_timestamp_only_fallback_without_a_mission(mod):
    for missing in (None, "", "   ", "///"):
        assert mod.AtlasGeoHandlerDemoDialog._report_basename(
            "pdf", T1, mission=missing) == \
            "ATLAS_Geo_Dock_Report_20260924_174533.pdf"


@needs_qgis
def test_neither_mission_nor_timestamp_is_applied_twice(mod):
    already = "Austin_Flight_01_ATLAS_Report_20260924_174533"
    name = mod.AtlasGeoHandlerDemoDialog._report_basename(
        "pdf", T2, base=already, mission="Austin Flight 01")
    assert name == already + ".pdf"
    assert name.count("ATLAS_Report") == 1
    assert name.count("2026") == 1


@needs_qgis
def test_the_dialog_proposes_the_mission_name(mod, dlg, saves, monkeypatch):
    _freeze(monkeypatch, mod, T1)
    _set_mission(dlg, "Austin Flight 01")
    dlg.export_report()
    assert os.path.basename(saves["proposed"][0]) == \
        "Austin_Flight_01_ATLAS_Report_20260924_174533.pdf"


@needs_qgis
def test_both_files_use_the_mission_stem(mod, dlg, saves, monkeypatch, tmp_path):
    _freeze(monkeypatch, mod, T1)
    _set_mission(dlg, "Austin Flight 01")
    dlg.export_report()
    out = tmp_path / "out"
    assert (out / "Austin_Flight_01_ATLAS_Report_20260924_174533.pdf").exists()
    assert (out / "Austin_Flight_01_ATLAS_Report_20260924_174533.csv").exists()


# ══════════════════════════════════════════════════════════════════════════
# the remembered directory
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_the_proposed_path_is_absolute(mod, dlg, saves, monkeypatch):
    """The macOS defect: a bare filename made the panel reopen at home."""
    _freeze(monkeypatch, mod, T1)
    dlg.export_report()
    assert os.path.isabs(saves["proposed"][0]), \
        "a relative name is what makes the macOS panel ignore the location"


@needs_qgis
def test_the_first_export_uses_the_standard_fallback(mod, dlg, saves, monkeypatch):
    _freeze(monkeypatch, mod, T1)
    dlg.export_report()
    first = os.path.dirname(saves["proposed"][0])
    assert first == mod.AtlasGeoHandlerDemoDialog._default_report_dir()


@needs_qgis
def test_a_successful_export_remembers_its_directory(mod, dlg, saves,
                                                     monkeypatch, tmp_path):
    from qgis.PyQt import QtCore
    _freeze(monkeypatch, mod, T1)
    dlg.export_report()
    stored = mod.AtlasGeoHandlerDemoDialog._remembered_report_dir()
    assert stored == str(tmp_path / "out")


@needs_qgis
def test_the_next_export_opens_in_the_remembered_directory(
        mod, dlg, saves, monkeypatch, tmp_path):
    _freeze(monkeypatch, mod, T1)
    dlg.export_report()
    _freeze(monkeypatch, mod, T2)
    dlg.export_report()
    assert os.path.dirname(saves["proposed"][1]) == str(tmp_path / "out")
    assert os.path.basename(saves["proposed"][1]) == \
        "ATLAS_Geo_Dock_Report_20260924_180102.pdf", \
        "the remembered directory must come with a NEW filename"


@needs_qgis
def test_only_the_directory_is_stored(mod, dlg, saves, monkeypatch, tmp_path):
    """No filename, account, or report content may end up in settings."""
    from qgis.PyQt import QtCore
    _freeze(monkeypatch, mod, T1)
    dlg.current_user_email = "tester@example.invalid"
    dlg.export_report()
    stored = str(mod.AtlasGeoHandlerDemoDialog._remembered_report_dir())
    assert stored == str(tmp_path / "out")
    assert ".pdf" not in stored and ".csv" not in stored
    assert "example.invalid" not in stored


@needs_qgis
def test_a_cancelled_export_does_not_change_the_remembered_directory(
        mod, dlg, saves, monkeypatch, tmp_path):
    from qgis.PyQt import QtCore
    _freeze(monkeypatch, mod, T1)
    dlg.export_report()                      # establishes tmp_path/out
    saves["cancel"] = True
    dlg.export_report()
    s = QtCore.QSettings(mod.AtlasGeoHandlerDemoDialog._SETTINGS_ORG,
                         mod.AtlasGeoHandlerDemoDialog._SETTINGS_APP)
    assert s.value(mod.AtlasGeoHandlerDemoDialog._SETTINGS_REPORT_DIR) == \
        str(tmp_path / "out")


@needs_qgis
def test_a_failed_export_does_not_change_the_remembered_directory(
        mod, dlg, saves, monkeypatch, tmp_path):
    from qgis.PyQt import QtCore
    _freeze(monkeypatch, mod, T1)
    dlg.export_report()                      # good export -> tmp_path/out
    other = tmp_path / "elsewhere"
    other.mkdir()
    saves["answer"] = str(other / "x.pdf")
    import csv as _csv
    monkeypatch.setattr(_csv, "writer",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")))
    dlg.export_report()
    s = QtCore.QSettings(mod.AtlasGeoHandlerDemoDialog._SETTINGS_ORG,
                         mod.AtlasGeoHandlerDemoDialog._SETTINGS_APP)
    assert s.value(mod.AtlasGeoHandlerDemoDialog._SETTINGS_REPORT_DIR) == \
        str(tmp_path / "out"), "a failed export moved the remembered directory"


@needs_qgis
def test_a_deleted_remembered_directory_falls_back_safely(
        mod, dlg, saves, monkeypatch, tmp_path):
    import shutil
    _freeze(monkeypatch, mod, T1)
    dlg.export_report()
    shutil.rmtree(tmp_path / "out")          # the user deleted it
    _freeze(monkeypatch, mod, T2)
    dlg.export_report()
    assert os.path.dirname(saves["proposed"][1]) == \
        mod.AtlasGeoHandlerDemoDialog._default_report_dir()


@needs_qgis
def test_a_missing_remembered_directory_is_not_recreated(
        mod, dlg, saves, monkeypatch, tmp_path):
    import shutil
    _freeze(monkeypatch, mod, T1)
    dlg.export_report()
    gone = tmp_path / "out"
    shutil.rmtree(gone)
    mod.AtlasGeoHandlerDemoDialog._remembered_report_dir()
    assert not gone.exists(), "the plugin recreated a directory the user removed"


@needs_qgis
def test_the_setting_is_profile_scoped_and_not_shared(mod, qapp, tmp_path,
                                                      monkeypatch):
    """Two profiles keep independent values.

    A QGIS profile is a separate settings FILE (and, in use, a separate
    process). What the plugin controls is that it stores through the
    profile-scoped org/app namespace rather than a global file of its own, so
    switching profile switches the value. Both halves are asserted here.
    """
    from qgis.PyQt import QtCore
    cls = mod.AtlasGeoHandlerDemoDialog

    # 1. the plugin uses the same namespace as its other settings, which is
    #    what QGIS scopes per profile.
    assert cls._SETTINGS_ORG == "AIVE" and cls._SETTINGS_APP == "AtlasGeo"
    assert cls._SETTINGS_REPORT_DIR.startswith("report/")
    src = pathlib.Path(DIALOG).read_text(encoding="utf-8")
    assert "QSettings(cls._SETTINGS_ORG, cls._SETTINGS_APP)" in src,         "the report directory must go through the profile-scoped namespace"

    # 2. two profiles -> two files -> independent values.
    ini_a = tmp_path / "A" / "AtlasGeo.ini"
    ini_b = tmp_path / "B" / "AtlasGeo.ini"
    for i in (ini_a, ini_b):
        i.parent.mkdir(parents=True, exist_ok=True)

    real = getattr(QtCore.QSettings, "Format", None) and QtCore.QSettings
    if real is None:                      # already patched by the fixture
        real = mod.QtCore.QSettings.Format and QtCore.QSettings

    def use(ini):
        def _f(*a, **k):
            return real(str(ini), real.Format.IniFormat)
        _f.Format = real.Format
        _f.Scope = real.Scope
        monkeypatch.setattr(mod.QtCore, "QSettings", _f)

    chosen = tmp_path / "chosen"
    chosen.mkdir()          # must exist, or _remembered_report_dir falls back
    use(ini_a)
    cls._remember_report_dir(str(chosen))
    assert cls._remembered_report_dir() == str(chosen)

    use(ini_b)
    assert cls._remembered_report_dir() == cls._default_report_dir(),         "a different profile saw the first profile's directory"
    assert ini_a.exists() and "last_export_dir" in ini_a.read_text()
    assert not ini_b.exists() or "last_export_dir" not in ini_b.read_text()


@needs_qgis
def test_a_user_edited_filename_is_still_respected(mod, dlg, saves,
                                                   monkeypatch, tmp_path):
    _freeze(monkeypatch, mod, T1)
    _set_mission(dlg, "Austin Flight 01")
    chosen = tmp_path / "picked"
    chosen.mkdir()
    saves["answer"] = str(chosen / "my report.pdf")
    dlg.export_report()
    assert (chosen / "my report.pdf").exists()
    assert (chosen / "my report.csv").exists()


@needs_qgis
def test_non_overwrite_still_holds_with_a_mission_name(mod, dlg, saves,
                                                       monkeypatch, tmp_path):
    _freeze(monkeypatch, mod, T1)
    _set_mission(dlg, "Site B")
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    victim = out / "Site_B_ATLAS_Report_20260924_174533.csv"
    victim.write_text("PRECIOUS")
    dlg.export_report()
    assert victim.read_text() == "PRECIOUS"
    assert (out / "Site_B_ATLAS_Report_20260924_174533_2.csv").exists()
