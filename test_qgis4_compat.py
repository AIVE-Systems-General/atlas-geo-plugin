# -*- coding: utf-8 -*-
"""Regression tests for the QGIS 4 / Qt6 compatibility work.

Two kinds of test live here and they are NOT equivalent:

  * Source-level tests parse the shipped modules and assert on what they
    contain. They run anywhere, including CI without QGIS.
  * Runtime tests construct the real dialog. They are SKIPPED unless a real
    qgis.PyQt is importable, because a mock cannot tell you whether Qt
    accepts an enum spelling. Passing the source tests alone does NOT mean
    the plugin runs.

The bug these guard against: an exception inside the dialog constructor left
`self.dlg` unset, and because `first_start` was cleared BEFORE construction
the plugin could never retry. Every subsequent click raised
`AttributeError: 'AtlasGeoHandlerDemo' object has no attribute 'dlg'`, which
hid the real cause.
"""
import ast
import pathlib
import re

import pytest

HERE = pathlib.Path(__file__).resolve().parent
ENTRY = HERE / "atlas_geo_plugin.py"
DIALOG = HERE / "atlas_geo_plugin_dialog.py"
SHIPPED = ["atlas_geo_plugin.py", "atlas_geo_plugin_dialog.py",
           "__init__.py", "resources.py", "_iso3166_data.py"]

# Symbols that moved under Qt6. Written as the UNSCOPED spelling, which must
# not appear in shipped code.
FORBIDDEN_UNSCOPED = [
    "SmoothTransformation", "FastTransformation",
    "ScrollBarAlwaysOff", "ScrollBarAlwaysOn", "ScrollBarAsNeeded",
    "KeepAspectRatio", "IgnoreAspectRatio",
    "RichText", "PlainText",
    "QueuedConnection", "DirectConnection",
    "transparent", "white", "black",
    "PointingHandCursor", "WaitCursor", "ArrowCursor",
    "LeftButton", "RightButton",
    "NoPen", "SolidLine",
    "Checked", "Unchecked",
    "AlignTop", "AlignCenter", "AlignLeft", "AlignRight",
    "AlignVCenter", "AlignHCenter", "AlignBottom",
    # Found only by running under real PyQt6. A curated list missed all of
    # these; they are pinned here so they cannot come back.
    "ElideRight", "CaseInsensitive", "MatchContains",
    "RightArrow", "DownArrow", "NoDropShadowWindowHint",
    "ToolButtonTextBesideIcon", "WA_Hover", "WA_StyledBackground",
]

# Class.Symbol pairs the runtime oracle proved broken under Qt6, for classes
# that may be imported bare (QPainter is imported inside a function, which is
# why import-parsing missed it).
FORBIDDEN_BARE = {
    "QEvent": {"ActivationChange"},
    "QPainter": {"Antialiasing", "TextAntialiasing", "SmoothPixmapTransform"},
    "QFont": {"AbsoluteSpacing", "PercentageSpacing", "Monospace", "SansSerif",
              "Serif", "TypeWriter"},
}


@pytest.mark.parametrize("name", SHIPPED)
def test_no_unscoped_bare_class_enum(name):
    """Catches Class.Symbol regardless of how Class was imported."""
    text = src(name)
    bad = []
    for i, line in enumerate(text.split("\n"), 1):
        code = line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
        for cls, syms in FORBIDDEN_BARE.items():
            for m in re.finditer(r"(?<![\w.])" + cls + r"\.([A-Za-z_][A-Za-z0-9_]*)", code):
                if m.group(1) in syms:
                    bad.append(f"{name}:{i}  {cls}.{m.group(1)}")
    assert not bad, "unscoped bare-class enum:\n  " + "\n  ".join(bad)


def src(name):
    return (HERE / name).read_text(encoding="utf-8")


# ── source-level: no unscoped Qt enum survives ──────────────────────────────
@pytest.mark.parametrize("name", SHIPPED)
def test_no_unscoped_qt_enum_access(name):
    """Qt.<symbol> must not be used for any enum that Qt6 scoped.

    The scoped spelling was verified to work under PyQt5 5.15.11 as well, so
    there is one spelling for both generations and no reason for the
    unscoped form to reappear.
    """
    text = src(name)
    bad = []
    for i, line in enumerate(text.split("\n"), 1):
        code = line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
        for m in re.finditer(r"\b(?:QtCore\.)?Qt\.([A-Za-z_][A-Za-z0-9_]*)", code):
            sym = m.group(1)
            if sym in FORBIDDEN_UNSCOPED:
                bad.append(f"{name}:{i}  Qt.{sym}")
    assert not bad, "unscoped Qt enum access found:\n  " + "\n  ".join(bad)


@pytest.mark.parametrize("name", SHIPPED)
def test_no_qt5_only_api(name):
    text = src(name)
    bad = []
    for i, line in enumerate(text.split("\n"), 1):
        code = line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
        if ".exec_(" in code:
            bad.append(f"{name}:{i}  exec_() removed in Qt6")
        if re.search(r"\bQRegExp\b", code):
            bad.append(f"{name}:{i}  QRegExp removed in Qt6")
        if re.search(r"\bQDesktopWidget\b", code):
            bad.append(f"{name}:{i}  QDesktopWidget removed in Qt6")
        if re.search(r"\.fontMetrics\(\)\.width\(", code):
            bad.append(f"{name}:{i}  QFontMetrics.width() removed in Qt6")
    assert not bad, "Qt5-only API found:\n  " + "\n  ".join(bad)


@pytest.mark.parametrize("name", SHIPPED)
def test_no_direct_pyqt_import(name):
    """Qt must be reached through qgis.PyQt so QGIS decides the binding.
    A direct PyQt5 or PyQt6 import pins the plugin to one generation."""
    text = src(name)
    bad = [f"{name}:{i}" for i, line in enumerate(text.split("\n"), 1)
           if re.match(r"\s*(from|import)\s+PyQt[56]\b", line)]
    assert not bad, f"direct PyQt import at {bad}"


@pytest.mark.parametrize("name", SHIPPED)
def test_widget_class_enums_are_scoped(name):
    """QSizePolicy, QFrame, QMessageBox and friends also scoped their enums."""
    text = src(name)
    pairs = {
        "QSizePolicy": {"Expanding", "Fixed", "Minimum", "Maximum", "Preferred"},
        "QFrame": {"NoFrame", "HLine", "VLine", "Box", "Panel", "Sunken", "Raised"},
        "QMessageBox": {"Yes", "No", "Ok", "Cancel", "Warning", "Critical",
                        "Information", "Question"},
        "QLineEdit": {"Password", "Normal", "NoEcho", "TrailingPosition"},
        "QDialog": {"Accepted", "Rejected"},
        "QToolButton": {"InstantPopup"},
        "QComboBox": {"NoInsert"},
        "QCompleter": {"PopupCompletion"},
    }
    bad = []
    for i, line in enumerate(text.split("\n"), 1):
        code = line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
        for m in re.finditer(r"\bQtWidgets\.(Q[A-Za-z]+)\.([A-Za-z_][A-Za-z0-9_]*)", code):
            cls, sym = m.group(1), m.group(2)
            if cls in pairs and sym in pairs[cls]:
                bad.append(f"{name}:{i}  QtWidgets.{cls}.{sym}")
    assert not bad, "unscoped widget-class enum:\n  " + "\n  ".join(bad)


# ── source-level: the latch ordering that caused the reported bug ───────────
def test_dlg_is_initialised_in_constructor():
    """self.dlg must exist after __init__, so a failed construction yields a
    clear message rather than AttributeError on the next click."""
    tree = ast.parse(src("atlas_geo_plugin.py"))
    init = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AtlasGeoHandlerDemo":
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "__init__":
                    init = sub
    assert init is not None, "AtlasGeoHandlerDemo.__init__ not found"
    assigned = set()
    for n in ast.walk(init):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                        and t.value.id == "self"):
                    assigned.add(t.attr)
    assert "dlg" in assigned, "self.dlg is not assigned in __init__"


def test_first_start_is_not_cleared_before_dialog_is_built():
    """THE REGRESSION. In 1.1.1, run() set first_start = False before
    constructing the dialog, so a constructor failure was permanent. The
    assignment must come after the construction."""
    text = src("atlas_geo_plugin.py")
    tree = ast.parse(text)
    run = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AtlasGeoHandlerDemo":
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == "run":
                    run = sub
    assert run is not None, "run() not found"

    latch_lines, ctor_lines = [], []
    for n in ast.walk(run):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                        and t.value.id == "self" and t.attr == "first_start"):
                    latch_lines.append(n.lineno)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id == "AtlasGeoHandlerDemoDialog":
            ctor_lines.append(n.lineno)
    assert ctor_lines, "dialog is never constructed in run()"
    for latch in latch_lines:
        assert latch > max(ctor_lines), (
            f"first_start assigned at line {latch}, before the dialog is "
            f"constructed at {max(ctor_lines)}. A constructor failure would "
            f"then be permanent.")


def test_run_handles_a_failing_constructor():
    """run() must catch construction failure, log it and return, rather than
    letting an AttributeError appear on the next invocation."""
    text = src("atlas_geo_plugin.py")
    tree = ast.parse(text)
    run = next(sub for node in ast.walk(tree)
               if isinstance(node, ast.ClassDef) and node.name == "AtlasGeoHandlerDemo"
               for sub in node.body
               if isinstance(sub, ast.FunctionDef) and sub.name == "run")
    assert any(isinstance(n, ast.Try) for n in ast.walk(run)), \
        "run() does not guard the dialog construction"
    assert "QgsMessageLog" in text, "construction failure is not logged"


def test_unload_clears_actions_and_releases_dialog():
    """Otherwise a disable/enable cycle accumulates toolbar actions."""
    text = src("atlas_geo_plugin.py")
    tree = ast.parse(text)
    unload = next(sub for node in ast.walk(tree)
                  if isinstance(node, ast.ClassDef) and node.name == "AtlasGeoHandlerDemo"
                  for sub in node.body
                  if isinstance(sub, ast.FunctionDef) and sub.name == "unload")
    body = ast.unparse(unload)
    assert "self.actions = []" in body, "unload() does not clear self.actions"
    assert "self.dlg = None" in body, "unload() does not release the dialog"


def test_metadata_declares_a_qgis_version_range():
    meta = (HERE / "metadata.txt").read_text(encoding="utf-8")
    mn = re.search(r"qgisMinimumVersion=(\S+)", meta)
    mx = re.search(r"qgisMaximumVersion=(\S+)", meta)
    assert mn, "qgisMinimumVersion missing"
    assert mx, "qgisMaximumVersion missing"
    assert mn.group(1).startswith("3."), f"unexpected minimum {mn.group(1)}"


# ── runtime: only meaningful inside a real QGIS ─────────────────────────────
# Scoped to these tests ONLY. A module-level importorskip would skip the
# source-level tests above as well, which must run everywhere including CI
# without QGIS, and a green "1 skipped" would then look like a pass.
try:
    import qgis.PyQt  # noqa: F401
    HAVE_QGIS = True
except Exception:                                             # noqa: BLE001
    HAVE_QGIS = False

needs_qgis = pytest.mark.skipif(
    not HAVE_QGIS, reason="needs a real QGIS Python (qgis.PyQt unavailable)")


@pytest.fixture(scope="module")
def qapp():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qgis.PyQt import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@needs_qgis
def test_every_scoped_enum_resolves_in_this_qt(qapp):
    """Evaluate the scoped spellings against the Qt actually present. This is
    what distinguishes a real check from a mocked one: it fails on Qt5 if a
    spelling is Qt6-only, and on Qt6 if one is Qt5-only."""
    from qgis.PyQt import QtWidgets
    from qgis.PyQt.QtCore import Qt
    ns = {"Qt": Qt, "QtWidgets": QtWidgets}
    exprs = [
        "Qt.TransformationMode.SmoothTransformation",
        "Qt.ScrollBarPolicy.ScrollBarAlwaysOff",
        "Qt.ScrollBarPolicy.ScrollBarAsNeeded",
        "Qt.AlignmentFlag.AlignTop", "Qt.AlignmentFlag.AlignCenter",
        "Qt.AlignmentFlag.AlignRight", "Qt.AlignmentFlag.AlignVCenter",
        "Qt.CursorShape.PointingHandCursor", "Qt.CursorShape.WaitCursor",
        "Qt.TextFormat.RichText",
        "Qt.ConnectionType.QueuedConnection",
        "Qt.GlobalColor.transparent",
        "Qt.AspectRatioMode.KeepAspectRatio",
        "Qt.PenStyle.NoPen",
        "QtWidgets.QSizePolicy.Policy.Expanding",
        "QtWidgets.QSizePolicy.Policy.Fixed",
        "QtWidgets.QFrame.Shape.NoFrame",
        "QtWidgets.QFrame.Shape.HLine",
        "QtWidgets.QDialog.DialogCode.Accepted",
        "QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup",
        "QtWidgets.QLineEdit.ActionPosition.TrailingPosition",
        "QtWidgets.QCompleter.CompletionMode.PopupCompletion",
        "QtWidgets.QComboBox.InsertPolicy.NoInsert",
    ]
    failed = []
    for e in exprs:
        try:
            eval(e, ns)
        except Exception as exc:
            failed.append(f"{e}  -> {type(exc).__name__}")
    assert not failed, "scoped spelling not available in this Qt:\n  " + "\n  ".join(failed)


@needs_qgis
def test_exec_and_horizontal_advance_exist(qapp):
    from qgis.PyQt import QtWidgets, QtGui
    assert hasattr(QtWidgets.QDialog(), "exec"), "QDialog.exec missing"
    fm = QtGui.QFontMetrics(QtGui.QFont())
    assert hasattr(fm, "horizontalAdvance"), "QFontMetrics.horizontalAdvance missing"
