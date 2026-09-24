# -*- coding: utf-8 -*-
"""Merge into mosaic must not delete anything.

Reported: selecting "Merge into single mosaic" also deletes layers that had
nothing to do with the merge, and the deletion cannot be undone.

The cause was this, run over the WHOLE project after the warp succeeded:

    for lyr in list(proj.mapLayers().values()):
        if lyr.name().startswith("ATLAS"):
            proj.removeMapLayer(lyr.id())

Three separate problems in four lines:

  * it matches on DISPLAY NAME, so any layer the user happened to name
    "ATLAS ..." is destroyed, including their own survey data;
  * it is scoped to the entire project rather than to this merge's inputs;
  * QgsProject.removeMapLayer is not on the QGIS undo stack, so none of it can
    be undone. Ctrl+Z covers edits inside a vector layer, not project
    membership.

It also ran BEFORE the output was loaded and validated, so a mosaic that failed
to load left the user with neither their layers nor a result.

Nothing here touches the network. Real GeoTIFFs are written to tmp_path so the
warp is genuine rather than mocked.
"""
import os
import pathlib
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
    spec = importlib.util.spec_from_file_location("_dlg_mosaic", DIALOG)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Canvas:
    def refresh(self):
        pass


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
            self._canvas = _Canvas()
            self.active = None

        def mainWindow(self):
            return self._w

        def messageBar(self):
            return self._bar

        def mapCanvas(self):
            return self._canvas

        def setActiveLayer(self, lyr):
            self.active = lyr

        def zoomToActiveLayer(self):
            pass

    d = mod.AtlasGeoHandlerDemoDialog(_Iface())
    # Modal notices are recorded, never shown.
    d._notices = []
    monkeypatch.setattr(d, "_themed_notice",
                        lambda title, msg, **k: d._notices.append((title, msg)))
    yield d
    from qgis.core import QgsProject
    QgsProject.instance().removeAllMapLayers()
    QgsProject.instance().layerTreeRoot().removeAllChildren()


# ══════════════════════════════════════════════════════════════════════════
# a realistic project: sources, unrelated layers, groups, duplicate names
# ══════════════════════════════════════════════════════════════════════════

def _geotiff(path, originx=-97.73, originy=30.30):
    """A real 8x8 RGB GeoTIFF, so gdal.Warp has something genuine to merge."""
    from osgeo import gdal, osr
    ds = gdal.GetDriverByName("GTiff").Create(str(path), 8, 8, 3)
    sr = osr.SpatialReference()
    sr.ImportFromEPSG(4326)
    ds.SetProjection(sr.ExportToWkt())
    ds.SetGeoTransform((originx, 0.001, 0, originy, 0, -0.001))
    for b in range(1, 4):
        ds.GetRasterBand(b).Fill(120 + b * 10)
    ds = None
    return str(path)


@pytest.fixture
def project(mod, dlg, tmp_path):
    """Build a project that looks like a real user's, and snapshot it."""
    from qgis.core import (QgsProject, QgsRasterLayer, QgsVectorLayer)
    proj = QgsProject.instance()
    proj.removeAllMapLayers()
    proj.layerTreeRoot().removeAllChildren()

    st = {}
    # ── the two merge inputs, named the way the plugin names its results ──
    src_a = _geotiff(tmp_path / "a_georef.tif", -97.730, 30.300)
    src_b = _geotiff(tmp_path / "b_georef.tif", -97.724, 30.300)
    la = QgsRasterLayer(src_a, "ATLAS · a")
    lb = QgsRasterLayer(src_b, "ATLAS · b")
    proj.addMapLayer(la)
    proj.addMapLayer(lb)
    st["sources"] = [la.id(), lb.id()]
    st["source_paths"] = [src_a, src_b]

    # ── an unrelated raster the USER named starting with ATLAS ───────────
    #    This is the reported defect in one layer: their own survey data.
    other = _geotiff(tmp_path / "user_survey.tif", -97.700, 30.400)
    lu = QgsRasterLayer(other, "ATLAS survey 2024 - DO NOT DELETE")
    proj.addMapLayer(lu)
    st["user_atlas_named"] = lu.id()

    # ── unrelated vector layers, one of them also ATLAS-named ────────────
    lv1 = QgsVectorLayer("Point?crs=EPSG:4326", "My roads", "memory")
    lv2 = QgsVectorLayer("Polygon?crs=EPSG:4326", "ATLAS parcels", "memory")
    proj.addMapLayer(lv1)
    proj.addMapLayer(lv2)
    st["vectors"] = [lv1.id(), lv2.id()]

    # ── a basemap ────────────────────────────────────────────────────────
    lb2 = QgsVectorLayer("Point?crs=EPSG:4326", "Satellite Basemap", "memory")
    proj.addMapLayer(lb2)
    st["basemap"] = lb2.id()

    # ── two layers sharing a NAME but with different ids ─────────────────
    d1 = QgsVectorLayer("Point?crs=EPSG:4326", "ATLAS duplicate", "memory")
    d2 = QgsVectorLayer("Point?crs=EPSG:4326", "ATLAS duplicate", "memory")
    proj.addMapLayer(d1)
    proj.addMapLayer(d2)
    st["duplicates"] = [d1.id(), d2.id()]

    # ── a nested group holding one more ATLAS-named layer ────────────────
    root = proj.layerTreeRoot()
    outer = root.addGroup("Survey")
    inner = outer.addGroup("2024")
    gl = QgsVectorLayer("Point?crs=EPSG:4326", "ATLAS nested", "memory")
    proj.addMapLayer(gl, False)          # not at root
    inner.addLayer(gl)
    st["nested"] = gl.id()

    # visibility is part of the state that must survive
    root.findLayer(lv1.id()).setItemVisibilityChecked(False)
    st["hidden"] = lv1.id()

    dlg._result_paths = [src_a, src_b]
    st["before_ids"] = set(proj.mapLayers().keys())
    st["before_names"] = {i: l.name() for i, l in proj.mapLayers().items()}
    st["before_order"] = [n.layerId() for n in root.findLayers()]
    st["before_visible"] = {n.layerId(): n.itemVisibilityChecked()
                            for n in root.findLayers()}
    return st


def _tree_state(proj):
    root = proj.layerTreeRoot()
    return {
        "ids": set(proj.mapLayers().keys()),
        "names": {i: l.name() for i, l in proj.mapLayers().items()},
        "order": [n.layerId() for n in root.findLayers()],
        "visible": {n.layerId(): n.itemVisibilityChecked() for n in root.findLayers()},
    }


def _mosaic_ids(proj):
    return [i for i, l in proj.mapLayers().items() if l.name() == "ATLAS Mosaic"]


# ══════════════════════════════════════════════════════════════════════════
# the defect
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_unrelated_layers_survive_a_merge(mod, dlg, project):
    """The reported bug: layers outside the merge are deleted."""
    from qgis.core import QgsProject
    proj = QgsProject.instance()
    dlg._merge_into_mosaic()

    survivors = set(proj.mapLayers().keys())
    for key, label in (("user_atlas_named", "the user's own ATLAS-named survey"),
                       ("basemap", "the basemap"),
                       ("nested", "an ATLAS-named layer inside a nested group")):
        assert project[key] in survivors, f"{label} was deleted by the merge"
    for lid in project["vectors"]:
        assert lid in survivors, "an unrelated vector layer was deleted"
    for lid in project["duplicates"]:
        assert lid in survivors, "a duplicate-named layer was deleted"


@needs_qgis
def test_the_selected_source_layers_survive(mod, dlg, project):
    """Merging is presentation. It must not consume the inputs."""
    from qgis.core import QgsProject
    dlg._merge_into_mosaic()
    survivors = set(QgsProject.instance().mapLayers().keys())
    for lid in project["sources"]:
        assert lid in survivors, "a selected source layer was deleted"


@needs_qgis
def test_layer_identity_order_groups_and_visibility_are_preserved(
        mod, dlg, project):
    from qgis.core import QgsProject
    proj = QgsProject.instance()
    dlg._merge_into_mosaic()
    after = _tree_state(proj)
    new = _mosaic_ids(proj)

    assert project["before_ids"] <= after["ids"], "existing layer IDs changed or vanished"
    for lid, name in project["before_names"].items():
        assert after["names"].get(lid) == name, f"layer {lid} was renamed"
    kept_order = [i for i in after["order"] if i not in new]
    assert kept_order == project["before_order"], "existing layer order changed"
    for lid, vis in project["before_visible"].items():
        assert after["visible"].get(lid) == vis, f"visibility changed for {lid}"

    # the nested layer is still nested, not relocated to the root
    node = proj.layerTreeRoot().findLayer(project["nested"])
    assert node is not None and node.parent().name() == "2024", \
        "a grouped layer was moved out of its group"


@needs_qgis
def test_the_mosaic_is_added_exactly_once(mod, dlg, project):
    from qgis.core import QgsProject
    dlg._merge_into_mosaic()
    assert len(_mosaic_ids(QgsProject.instance())) == 1


@needs_qgis
def test_removing_the_new_layer_restores_the_previous_view(mod, dlg, project):
    """Requirement 8: the visible result is reversible by deleting one layer."""
    from qgis.core import QgsProject
    proj = QgsProject.instance()
    dlg._merge_into_mosaic()
    for mid in _mosaic_ids(proj):
        proj.removeMapLayer(mid)
    assert set(proj.mapLayers().keys()) == project["before_ids"], \
        "removing the mosaic did not return the project to its previous state"


# ══════════════════════════════════════════════════════════════════════════
# failure and cancellation must not mutate the project
# ══════════════════════════════════════════════════════════════════════════

@needs_qgis
def test_a_warp_failure_mutates_nothing(mod, dlg, project, monkeypatch):
    from qgis.core import QgsProject
    from osgeo import gdal
    monkeypatch.setattr(gdal, "Warp",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("warp failed")))
    before = _tree_state(QgsProject.instance())
    dlg._merge_into_mosaic()
    assert _tree_state(QgsProject.instance()) == before, \
        "a failed merge changed the project"


@needs_qgis
def test_a_missing_output_file_mutates_nothing(mod, dlg, project, monkeypatch):
    from qgis.core import QgsProject
    from osgeo import gdal
    monkeypatch.setattr(gdal, "Warp", lambda *a, **k: None)   # writes nothing
    before = _tree_state(QgsProject.instance())
    dlg._merge_into_mosaic()
    assert _tree_state(QgsProject.instance()) == before


@needs_qgis
def test_an_unloadable_output_removes_nothing(mod, dlg, project, monkeypatch):
    """The worst case: the old code deleted first, then failed to load, so the
    user lost their layers AND got no mosaic."""
    from qgis.core import QgsProject
    import qgis.core as qcore

    real = mod.QgsRasterLayer

    def broken(path, name, *a, **k):
        lyr = real(path, name, *a, **k)
        if name == "ATLAS Mosaic":
            return real("/nonexistent/definitely-not-a-raster.tif", name)
        return lyr

    monkeypatch.setattr(mod, "QgsRasterLayer", broken)
    before = _tree_state(QgsProject.instance())
    dlg._merge_into_mosaic()
    after = _tree_state(QgsProject.instance())
    assert before["ids"] <= after["ids"], \
        "layers were removed even though the mosaic could not be loaded"
    assert not _mosaic_ids(QgsProject.instance())


@needs_qgis
def test_too_few_inputs_mutates_nothing(mod, dlg, project):
    """Cancellation-equivalent: the operation declines before doing anything."""
    from qgis.core import QgsProject
    dlg._result_paths = dlg._result_paths[:1]
    before = _tree_state(QgsProject.instance())
    dlg._merge_into_mosaic()
    assert _tree_state(QgsProject.instance()) == before


@needs_qgis
def test_a_layer_removed_during_the_merge_is_handled_safely(
        mod, dlg, project, monkeypatch):
    """A user deleting a layer while the warp runs must not crash the merge."""
    from qgis.core import QgsProject
    from osgeo import gdal
    proj = QgsProject.instance()
    real_warp = gdal.Warp
    victim = project["vectors"][0]

    def warp_then_delete(*a, **k):
        proj.removeMapLayer(victim)          # simulates the user acting mid-run
        return real_warp(*a, **k)

    monkeypatch.setattr(gdal, "Warp", warp_then_delete)
    dlg._merge_into_mosaic()
    assert victim not in proj.mapLayers(), "the user's own removal was undone"
    assert len(_mosaic_ids(proj)) == 1, "the merge did not complete"
    for lid in project["sources"] + [project["user_atlas_named"]]:
        assert lid in proj.mapLayers(), "an unrelated layer was collaterally removed"


@needs_qgis
def test_closing_the_plugin_triggers_no_delayed_cleanup(mod, dlg, project):
    """Nothing may remove layers after the dialog goes away."""
    from qgis.core import QgsProject
    proj = QgsProject.instance()
    dlg._merge_into_mosaic()
    snapshot = set(proj.mapLayers().keys())
    dlg.shutdown_session()
    dlg.close()
    from qgis.PyQt import QtWidgets
    QtWidgets.QApplication.instance().processEvents()
    assert set(proj.mapLayers().keys()) == snapshot, \
        "closing the plugin removed layers"
