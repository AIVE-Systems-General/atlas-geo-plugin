# -*- coding: utf-8 -*-
"""Regression tests for the two Qt6 breaks found by live QGIS 4.2.2 testing.

Both bugs shipped in 1.1.2-rc3 and both were invisible to the earlier checks:

  * `pushMessage(..., level=0)` carries no enum NAME, so an audit that greps for
    `Qt.Something` spellings cannot see it. Qt6 refuses the implicit int to enum
    conversion and raises TypeError. It fired on sign-in.

  * `QPrinter.HighResolution` and friends live in QtPrintSupport, which was not
    in the module list the earlier Qt6 audit enumerated. Qt6 removed the
    unscoped names, and separately changed setPageMargins so that NO call form
    works on both Qt5 and Qt6.

The first test is static, because the bad pattern must never reappear anywhere,
including on code paths a test cannot reach. The second is behavioural, because
a name-resolution check is exactly what missed this the first time: it proves a
real PDF comes out, of the right size, with the right margins.
"""
import ast
import io
import pathlib
import re

import pytest

HERE = pathlib.Path(__file__).resolve().parent
DIALOG = HERE / "atlas_geo_plugin_dialog.py"

try:
    import os as _os
    # Set BEFORE qgis.PyQt is imported. Under pytest the import happens at
    # collection time, well before any test body runs, so setting it inside a
    # test is too late for the platform plugin.
    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import qgis.PyQt  # noqa: F401
    HAVE_QGIS = True
except Exception:                                             # noqa: BLE001
    HAVE_QGIS = False


@pytest.fixture(scope="module")
def qapp():
    """One QApplication for the module, created before any widget exists.

    PyQt6 aborts the process rather than raising when a widget is built without
    a live QApplication, so this must not be left to a test body.
    """
    from qgis.PyQt import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# ══════════════════════════════════════════════════════════════════════════
# 1. pushMessage must never be handed a raw message level again
# ══════════════════════════════════════════════════════════════════════════

def _pushmessage_calls(tree):
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "pushMessage"):
            yield node


def _level_argument(call):
    """The node passed as `level`, by keyword or in the third positional slot."""
    for kw in call.keywords:
        if kw.arg == "level":
            return kw.value
    if len(call.args) >= 3:
        return call.args[2]
    return None


def _describe_bad_level(node):
    """Return a reason string if this level argument is not an enum, else None."""
    if node is None:
        return None                                   # omitted, Qt uses its default

    # a bare integer literal: level=0
    if isinstance(node, ast.Constant) and isinstance(node.value, int) \
            and not isinstance(node.value, bool):
        return f"raw integer {node.value!r}"

    # unary minus on a literal, level=-1
    if isinstance(node, ast.UnaryOp) and isinstance(node.operand, ast.Constant) \
            and isinstance(node.operand.value, int):
        return "raw integer (negated literal)"

    # int(...) anywhere in the expression
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) \
                and sub.func.id == "int":
            return "int(...) conversion"
        # .value on an enum, which unwraps it back to an int
        if isinstance(sub, ast.Attribute) and sub.attr == "value":
            return "`.value` unwraps the enum back to an int"

    return None


def test_no_pushmessage_call_passes_a_raw_level():
    """Fails if any pushMessage passes an int, `.value`, or int(...) as level.

    Qt6 accepts only a Qgis.MessageLevel member here. Passing anything that is
    an int at runtime raises TypeError and aborts whatever the user was doing.
    """
    tree = ast.parse(DIALOG.read_text(encoding="utf-8"))
    offenders = []
    total = 0
    for call in _pushmessage_calls(tree):
        total += 1
        reason = _describe_bad_level(_level_argument(call))
        if reason:
            offenders.append(f"line {call.lineno}: {reason}")

    assert total > 0, "no pushMessage calls found, the test is not looking at the right file"
    assert not offenders, (
        "pushMessage must be given a Qgis.MessageLevel member, not an int.\n"
        "Qt6 raises TypeError for an int and the call site aborts.\n  "
        + "\n  ".join(offenders))


def test_every_pushmessage_level_is_a_messagelevel_member():
    """The positive form: whatever is passed must name Qgis.MessageLevel."""
    tree = ast.parse(DIALOG.read_text(encoding="utf-8"))
    wrong = []
    for call in _pushmessage_calls(tree):
        node = _level_argument(call)
        if node is None:
            continue
        text = ast.dump(node)
        if "MessageLevel" not in text:
            wrong.append(f"line {call.lineno}: {ast.unparse(node)}")
    assert not wrong, (
        "these levels do not reference Qgis.MessageLevel:\n  " + "\n  ".join(wrong))


def _code_without_comments_or_strings(path):
    """Source with comments and string literals removed.

    Needed because the fixed code CARRIES the banned spellings in its
    explanatory comments, on purpose. Scanning raw text makes this test fail on
    its own documentation.
    """
    import tokenize
    out = []
    with tokenize.open(path) as fh:
        for tok in tokenize.generate_tokens(fh.readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
    return " ".join(out)


def test_source_has_no_unscoped_qprinter_enums():
    """The Qt5 spellings Qt6 deleted. Static, because export needs a real job."""
    code = _code_without_comments_or_strings(DIALOG)
    banned = ["QPrinter . HighResolution", "QPrinter . PdfFormat",
              "QPrinter . A4", "QPrinter . Millimeter"]
    present = [b.replace(" ", "") for b in banned if b in code]
    assert not present, (
        "Qt6 removed these unscoped names; use the scoped spelling: " + ", ".join(present))


def test_qtextdocument_printing_uses_the_cross_version_spelling():
    """PyQt6 removed the print_() alias; only print() exists on both.

    Static guard for a break that no name-resolution audit can see, because
    `doc` is a local variable rather than a dotted Qt class path.
    """
    code = _code_without_comments_or_strings(DIALOG)
    assert "print_ (" not in code and ". print_" not in code, (
        "doc.print_() does not exist on PyQt6; use doc.print(), which PyQt5 also has")


# ══════════════════════════════════════════════════════════════════════════
# 2. PDF export must actually produce a correct PDF, on this Qt
# ══════════════════════════════════════════════════════════════════════════

pytestmark_qgis = pytest.mark.skipif(not HAVE_QGIS, reason="needs a real QGIS Python")

A4_PT = (595.276, 841.890)      # A4 in PostScript points, what a PDF MediaBox carries
MM_TO_PT = 72.0 / 25.4          # 14 mm -> 39.685 pt


@pytest.mark.skipif(not HAVE_QGIS, reason="needs a real QGIS Python")
def test_pdf_export_produces_a_real_a4_pdf_with_14mm_margins(qapp, tmp_path, monkeypatch):
    """Drive the plugin's own export_report() and inspect what lands on disk.

    Deliberately NOT a check that the names resolve. rc3's names resolved fine
    on Qt5 and the export still produced nothing on Qt6. This asserts on the
    bytes of a real file.
    """
    import importlib.util
    import os

    from qgis.PyQt import QtWidgets

    spec = importlib.util.spec_from_file_location("_dlg_pdf", DIALOG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    out_pdf = tmp_path / "report.pdf"

    # The only things stubbed are the two modal dialogs. Everything between them,
    # including all printer configuration, is the shipped code.
    monkeypatch.setattr(QtWidgets.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out_pdf), "PDF Files (*.pdf)")))

    captured = {}

    # Capture the QPrinter the SHIPPED code builds, so the page size and margins
    # asserted below are the ones that actually produced this PDF, rather than a
    # reconstruction that might drift from the real code.
    import qgis.PyQt.QtPrintSupport as _qps
    _RealPrinter = _qps.QPrinter
    made = []

    class _RecordingPrinter(_RealPrinter):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            made.append(self)

    monkeypatch.setattr(_qps, "QPrinter", _RecordingPrinter)

    class _Bar:
        """Records what the plugin pushes, so a bad level would surface here too."""
        def __init__(self):
            self.pushed = []

        def pushMessage(self, *a, **k):
            self.pushed.append((a, k))

    class _Iface:
        def __init__(self):
            # A real QMainWindow: the dialog parents itself to it during setup,
            # and PyQt6 aborts the process rather than raising if this is None.
            self._w = QtWidgets.QMainWindow()
            self._bar = _Bar()

        def mainWindow(self):
            return self._w

        def messageBar(self):
            return self._bar

    dlg = mod.AtlasGeoHandlerDemoDialog(_Iface())
    monkeypatch.setattr(dlg, "_themed_notice",
                        lambda *a, **k: captured.setdefault("notice", (a, k)))

    # Minimal completed-job state, enough for a report to have a row.
    dlg._job_results = {
        "job-0001": {"file": "DJI_0001.JPG", "status": "success",
                     "inliers": 412, "tier": 1, "angle": 0.8,
                     "lat": 30.2999, "lon": -97.7283, "elapsed": 12.4},
    }
    for attr in ("_failed_jobs", "_local_save_failures", "_submitted_jobs"):
        if not getattr(dlg, attr, None):
            setattr(dlg, attr, {})
    dlg.current_user_email = "tester@example.invalid"

    dlg.export_report()

    # ── the file itself ────────────────────────────────────────────────
    assert out_pdf.exists(), (
        f"no PDF was written. export_report reported: {captured.get('notice')}")
    size = out_pdf.stat().st_size
    assert size > 0, "PDF is zero bytes"
    assert size > 500, f"PDF is implausibly small at {size} bytes"

    data = out_pdf.read_bytes()
    assert data.startswith(b"%PDF-"), "file is not a PDF"

    # ── A4 page size, read out of the PDF's own MediaBox ───────────────
    boxes = re.findall(rb"/MediaBox\s*\[\s*([\d.\-]+)\s+([\d.\-]+)\s+"
                       rb"([\d.\-]+)\s+([\d.\-]+)\s*\]", data)
    assert boxes, "no /MediaBox in the PDF, cannot confirm the page size"
    x0, y0, x1, y1 = (float(v) for v in boxes[0])
    width, height = abs(x1 - x0), abs(y1 - y0)
    assert abs(width - A4_PT[0]) <= 3 and abs(height - A4_PT[1]) <= 3, (
        f"page is {width:.1f}x{height:.1f} pt, expected A4 "
        f"{A4_PT[0]:.1f}x{A4_PT[1]:.1f} pt")

    # ── the export must have reported success, not a caught failure ────
    assert captured.get("notice"), "export_report did not report an outcome"
    title = captured["notice"][0][0] if captured["notice"][0] else ""
    assert "failed" not in str(title).lower(), (
        f"export reported failure: {captured['notice']}")

    # ── A4 and 14 mm margins on the printer that WROTE this file ───────
    from qgis.PyQt.QtGui import QPageSize, QPageLayout
    assert made, "the shipped code never constructed a QPrinter"
    layout = made[0].pageLayout()
    assert layout.pageSize().id() == QPageSize.PageSizeId.A4, (
        f"export used page size {layout.pageSize().name()}, expected A4")

    mm = layout.margins(QPageLayout.Unit.Millimeter)
    for side, value in (("left", mm.left()), ("top", mm.top()),
                        ("right", mm.right()), ("bottom", mm.bottom())):
        assert abs(value - 14.0) < 0.5, (
            f"{side} margin of the exported PDF is {value:.2f} mm, expected ~14 mm")

    # ── CSV companion, which the same function writes ──────────────────
    csv_path = out_pdf.with_suffix(".csv")
    assert csv_path.exists() and csv_path.stat().st_size > 0, (
        "export_report did not write the CSV alongside the PDF")


@pytest.mark.skipif(not HAVE_QGIS, reason="needs a real QGIS Python")
def test_printer_configuration_is_a4_and_14mm_on_this_qt(qapp):
    """The margin API split Qt5 from Qt6, so assert the configured layout.

    Qt5 QPrinter.setPageMargins takes (l, t, r, b, unit); Qt6 takes
    (QMarginsF, unit). No single call satisfies both, which is why the shipped
    code goes through QPageLayout. This confirms that route yields A4 with
    14 mm margins on whichever Qt is running.
    """
    from qgis.PyQt.QtCore import QMarginsF
    from qgis.PyQt.QtGui import QPageSize, QPageLayout
    from qgis.PyQt.QtPrintSupport import QPrinter

    # exactly the sequence in export_report
    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    layout = printer.pageLayout()
    layout.setUnits(QPageLayout.Unit.Millimeter)
    layout.setMargins(QMarginsF(14, 14, 14, 14))
    printer.setPageLayout(layout)

    got = printer.pageLayout()
    assert got.pageSize().id() == QPageSize.PageSizeId.A4, "page size is not A4"

    m = got.margins()
    for name, value in (("left", m.left()), ("top", m.top()),
                        ("right", m.right()), ("bottom", m.bottom())):
        assert abs(value - 14.0) < 0.5, f"{name} margin is {value}, expected ~14 mm"
