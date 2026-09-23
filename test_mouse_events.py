# -*- coding: utf-8 -*-
"""Qt6 mouse-event coordinate compatibility, with real events.

`QMouseEvent.globalPos()` was REMOVED in Qt6, not deprecated. Dragging any
frameless themed dialog on QGIS 4 therefore raised

    AttributeError: 'QMouseEvent' object has no attribute 'globalPos'

Measured on the three supported versions:

    API                Qt 5.15    Qt 6.9
    globalPos()        QPoint     ABSENT
    globalPosition()   ABSENT     QPointF
    globalX/globalY    int        ABSENT
    localPos/screenPos QPointF    ABSENT
    pos()              QPoint     QPoint     <- unchanged, safe to keep using

⚠️ WHY THE EXISTING AUDITS MISSED IT. `qt_attr_resolve.py` resolves dotted Qt
CLASS paths against the running Qt. `e.globalPos()` is an attribute on a local
variable, so there is no class path to resolve. This is the third bug of that
exact shape, after `doc.print_()` and `pushMessage(level=0)`. The static test at
the end of this file closes the hole for mouse APIs specifically.

These tests dispatch REAL QMouseEvents through QApplication.sendEvent, so the
handler runs the way Qt runs it, not via a direct call that could mask a
dispatch-level problem.
"""
import ast
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


@pytest.fixture(scope="module")
def qapp():
    from qgis.PyQt import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def mod(qapp):
    import importlib.util
    spec = importlib.util.spec_from_file_location("_dlg_mouse", DIALOG)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _mouse_event(kind, local, glob, button="left", buttons="left"):
    """Build a real QMouseEvent that both Qt5 and Qt6 accept.

    The six-argument QPointF constructor is valid on 5.15 and 6.9 alike, so no
    version branch is needed here either.
    """
    from qgis.PyQt import QtCore, QtGui
    from qgis.PyQt.QtCore import QPointF
    B = QtCore.Qt.MouseButton
    btn = {"left": B.LeftButton, "none": B.NoButton}[button]
    btns = {"left": B.LeftButton, "none": B.NoButton}[buttons]
    types = {"press": QtCore.QEvent.Type.MouseButtonPress,
             "move": QtCore.QEvent.Type.MouseMove,
             "release": QtCore.QEvent.Type.MouseButtonRelease}
    return QtGui.QMouseEvent(
        types[kind], QPointF(*local), QPointF(*glob),
        btn, btns, QtCore.Qt.KeyboardModifier.NoModifier)


# ══════════════════════════════════════════════════════════════════════════
# 1. the helper itself, against a real event
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_helper_returns_global_point_on_this_qt(mod, qapp):
    from qgis.PyQt.QtCore import QPoint
    ev = _mouse_event("press", (10, 20), (110, 120))
    pt = mod._mouse_global_point(ev)
    assert isinstance(pt, QPoint), f"expected QPoint, got {type(pt).__name__}"
    assert (pt.x(), pt.y()) == (110, 120)


@needs_qgis
def test_helper_uses_whichever_api_this_qt_has(mod, qapp):
    """Feature detection, not a version check: exactly one of the two APIs is
    present, and the helper must pick the one that exists."""
    ev = _mouse_event("press", (1, 2), (3, 4))
    has_new = callable(getattr(ev, "globalPosition", None))
    has_old = callable(getattr(ev, "globalPos", None))
    assert has_new != has_old, (
        f"expected exactly one of globalPosition/globalPos, got "
        f"new={has_new} old={has_old}")
    assert (mod._mouse_global_point(ev).x(),
            mod._mouse_global_point(ev).y()) == (3, 4)


@needs_qgis
def test_helper_survives_fractional_coordinates(mod, qapp):
    """Qt6 hands back QPointF. toPoint() must not throw on a fractional value."""
    ev = _mouse_event("press", (1.5, 2.5), (110.7, 120.4))
    pt = mod._mouse_global_point(ev)
    assert pt.x() in (110, 111) and pt.y() in (120, 121)


# ══════════════════════════════════════════════════════════════════════════
# 2. dragging a real themed dialog, via real dispatched events
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def themed(mod, qapp):
    """A real ThemedDialog. This is the base class behind every themed dialog
    in the plugin, including Contact Sales, the notices, the confirmations and
    the plan chooser, so all of them share this drag code."""
    from qgis.PyQt import QtWidgets
    parent = QtWidgets.QMainWindow()
    d = mod.ThemedDialog(parent, "Contact Sales", "Tell us about your needs")
    d.show()
    qapp.processEvents()
    yield d
    d.close()
    d.deleteLater()
    qapp.processEvents()


@needs_qgis
def test_contact_sales_press_move_release_raises_nothing(mod, themed, qapp):
    """The reported crash: press on the dialog, on QGIS 4."""
    from qgis.PyQt import QtWidgets
    for kind, local, glob, btn, btns in (
            ("press",   (40, 12), (500, 300), "left", "left"),
            ("move",    (70, 30), (530, 318), "none", "left"),
            ("release", (70, 30), (530, 318), "left", "none")):
        ev = _mouse_event(kind, local, glob, btn, btns)
        QtWidgets.QApplication.sendEvent(themed, ev)
        qapp.processEvents()
    # reaching here without AttributeError is the assertion


@needs_qgis
def test_dragging_actually_moves_the_dialog(mod, themed, qapp):
    """Not just 'no exception': the drag must still work."""
    from qgis.PyQt import QtWidgets
    themed.move(200, 150)
    qapp.processEvents()
    start = themed.frameGeometry().topLeft()

    press_global = (start.x() + 40, start.y() + 12)
    QtWidgets.QApplication.sendEvent(
        themed, _mouse_event("press", (40, 12), press_global))
    qapp.processEvents()
    assert themed._drag_pos is not None, "press did not begin a drag"

    moved_global = (press_global[0] + 60, press_global[1] + 35)
    QtWidgets.QApplication.sendEvent(
        themed, _mouse_event("move", (40, 12), moved_global, "none", "left"))
    qapp.processEvents()

    end = themed.frameGeometry().topLeft()
    assert (end.x() - start.x(), end.y() - start.y()) == (60, 35), (
        f"dialog moved by ({end.x()-start.x()}, {end.y()-start.y()}), "
        f"expected (60, 35)")


@needs_qgis
def test_release_ends_the_drag(mod, themed, qapp):
    from qgis.PyQt import QtWidgets
    QtWidgets.QApplication.sendEvent(
        themed, _mouse_event("press", (40, 12), (500, 300)))
    qapp.processEvents()
    assert themed._drag_pos is not None

    QtWidgets.QApplication.sendEvent(
        themed, _mouse_event("release", (40, 12), (500, 300), "left", "none"))
    qapp.processEvents()
    assert themed._drag_pos is None, "release did not clear the drag state"


@needs_qgis
def test_move_without_press_does_not_move_the_dialog(mod, themed, qapp):
    """A stray move must not teleport the window."""
    from qgis.PyQt import QtWidgets
    themed.move(300, 200)
    qapp.processEvents()
    before = themed.frameGeometry().topLeft()
    QtWidgets.QApplication.sendEvent(
        themed, _mouse_event("move", (10, 10), (900, 900), "none", "none"))
    qapp.processEvents()
    after = themed.frameGeometry().topLeft()
    assert (after.x(), after.y()) == (before.x(), before.y())


@needs_qgis
def test_every_themed_dialog_variant_drags(mod, qapp):
    """The audit found ThemedDialog is the only custom draggable widget, and it
    backs several dialogs. Exercise the shapes the plugin actually builds."""
    from qgis.PyQt import QtWidgets
    parent = QtWidgets.QMainWindow()
    variants = [
        ("Contact Sales", "Tell us about your needs"),
        ("Choose your plan", "1 credit = 1 image"),
        ("Welcome to the team", ""),
        ("Log out?", ""),
    ]
    for title, subtitle in variants:
        d = mod.ThemedDialog(parent, title, subtitle)
        d.show()
        qapp.processEvents()
        d.move(180, 140)
        qapp.processEvents()
        start = d.frameGeometry().topLeft()
        QtWidgets.QApplication.sendEvent(
            d, _mouse_event("press", (30, 10), (start.x() + 30, start.y() + 10)))
        QtWidgets.QApplication.sendEvent(
            d, _mouse_event("move", (30, 10),
                            (start.x() + 55, start.y() + 27), "none", "left"))
        qapp.processEvents()
        end = d.frameGeometry().topLeft()
        assert (end.x() - start.x(), end.y() - start.y()) == (25, 17), \
            f"{title!r} did not drag correctly"
        d.close()
        d.deleteLater()
        qapp.processEvents()


# ══════════════════════════════════════════════════════════════════════════
# 3. the static guard, so this class of break cannot return unnoticed
# ══════════════════════════════════════════════════════════════════════════

# Removed in Qt6. Each maps to what should be used instead.
QT6_REMOVED_EVENT_APIS = {
    "globalPos":   "_mouse_global_point(event)",
    "globalX":     "_mouse_global_point(event).x()",
    "globalY":     "_mouse_global_point(event).y()",
    "localPos":    "event.position() / event.pos()",
    "windowPos":   "event.scenePosition()",
    "screenPos":   "event.globalPosition()",
}


def test_no_qt6_removed_event_api_is_called_directly():
    """Fails if any handler reaches for an API Qt6 deleted.

    The helper's own Qt5 fallback is the single permitted call site: it is
    guarded by a callable() check on the Qt6 API, so it only runs where the
    Qt5 one exists.
    """
    tree = ast.parse(DIALOG.read_text(encoding="utf-8"))
    helper = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                   and n.name == "_mouse_global_point"), None)
    assert helper is not None, "the compatibility helper is missing"
    allowed = {n for n in ast.walk(helper)}

    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        name = node.func.attr
        if name not in QT6_REMOVED_EVENT_APIS:
            continue
        if node in allowed:
            continue                       # the helper's own fallback
        offenders.append(
            f"line {node.lineno}: .{name}() was removed in Qt6, use "
            f"{QT6_REMOVED_EVENT_APIS[name]}")

    assert not offenders, (
        "Qt6-removed event APIs called directly:\n  " + "\n  ".join(offenders))


def test_mouse_handlers_go_through_the_helper():
    """Every mouse handler that needs a global position must use the helper,
    so a future handler cannot quietly reintroduce the direct call."""
    tree = ast.parse(DIALOG.read_text(encoding="utf-8"))
    HANDLERS = {"mousePressEvent", "mouseMoveEvent", "mouseReleaseEvent",
                "mouseDoubleClickEvent", "wheelEvent"}
    checked = 0
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        for fn in [f for f in cls.body
                   if isinstance(f, ast.FunctionDef) and f.name in HANDLERS]:
            checked += 1
            body = ast.dump(fn)
            for removed in QT6_REMOVED_EVENT_APIS:
                assert f"attr='{removed}'" not in body, (
                    f"{cls.name}.{fn.name} (line {fn.lineno}) calls "
                    f".{removed}(), removed in Qt6")
    assert checked >= 3, f"expected to inspect several handlers, saw {checked}"
