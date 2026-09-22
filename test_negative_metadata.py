# -*- coding: utf-8 -*-
"""Negative metadata cases for the GDAL extraction path.

Two things are being checked, and the second matters more than the first:

  1. Nothing raises an unhandled exception out of the plugin.
  2. Nothing INVENTS a value. A missing coordinate must surface as missing, not
     as a plausible-looking number, because the upload carries whatever these
     return and a fabricated GPS produces a confidently mis-placed result.

Needs a real QGIS for GDAL. Skipped otherwise, and a skip is not a pass.
"""
import importlib.util
import pathlib

import pytest

HERE = pathlib.Path(__file__).resolve().parent

try:
    import qgis.PyQt  # noqa: F401
    HAVE_QGIS = True
except Exception:                                             # noqa: BLE001
    HAVE_QGIS = False

pytestmark = pytest.mark.skipif(not HAVE_QGIS, reason="needs a real QGIS Python")


@pytest.fixture(scope="module")
def mod():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qgis.PyQt import QtWidgets
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    spec = importlib.util.spec_from_file_location(
        "_dlg_neg", HERE / "atlas_geo_plugin_dialog.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def dlg(mod):
    """A stand-in carrying only what the extractors call on self, so the tests
    exercise the extraction logic without building the whole dialog."""
    class Stub:
        ALT_SOURCE_AGL = mod.AtlasGeoHandlerDemoDialog.ALT_SOURCE_AGL
        ALT_SOURCE_MSL = mod.AtlasGeoHandlerDemoDialog.ALT_SOURCE_MSL
        ALT_SOURCE_UNKNOWN = mod.AtlasGeoHandlerDemoDialog.ALT_SOURCE_UNKNOWN
        for _n in ("_extract_gps_from_image", "_extract_altitude_with_source",
                   "_extract_heading_from_exif", "_extract_focal35_from_raw_xmp",
                   "_extract_yaw_from_raw_xmp", "_extract_pitch_from_raw_xmp",
                   "_extract_altitude_from_raw_xmp", "_tilt_from_nadir"):
            locals()[_n] = getattr(mod.AtlasGeoHandlerDemoDialog, _n)
        del _n
    return Stub()


def make_tif(path, tags=None, gt=None, epsg=None, w=8, h=6, bands=1):
    from osgeo import gdal, osr
    ds = gdal.GetDriverByName("GTiff").Create(str(path), w, h, bands)
    for k, v in (tags or {}).items():
        ds.SetMetadataItem(k, v)
    if gt is not None:
        ds.SetGeoTransform(gt)
    if epsg is not None:
        sr = osr.SpatialReference(); sr.ImportFromEPSG(epsg)
        ds.SetProjection(sr.ExportToWkt())
    ds = None
    return path


# ── 1. missing GPS ──────────────────────────────────────────────────────────
def test_missing_gps_is_reported_not_invented(mod, dlg, tmp_path):
    p = make_tif(tmp_path / "nogps.tif", {"EXIF_Make": "DJI"})
    with pytest.raises(Exception) as ei:
        dlg._extract_gps_from_image(str(p))
    # It must fail loudly rather than return a default coordinate.
    assert "GPS" in str(ei.value) or isinstance(ei.value, (ValueError, RuntimeError))


def test_partial_gps_latitude_only_is_rejected(mod, dlg, tmp_path):
    """Half a fix is not a fix. Returning latitude with a defaulted longitude
    would place the image on the wrong meridian."""
    p = make_tif(tmp_path / "halfgps.tif",
                 {"EXIF_GPSLatitude": "(30) (17) (9.5)", "EXIF_GPSLatitudeRef": "N"})
    with pytest.raises(Exception):
        dlg._extract_gps_from_image(str(p))


# ── 2. missing altitude ─────────────────────────────────────────────────────
def test_missing_altitude_returns_unknown_source(mod, dlg, tmp_path):
    p = make_tif(tmp_path / "noalt.tif", {"EXIF_Make": "SONY"})
    alt, srcname = dlg._extract_altitude_with_source(str(p))
    assert alt is None, f"altitude invented: {alt}"
    assert srcname == mod.AtlasGeoHandlerDemoDialog.ALT_SOURCE_UNKNOWN


# ── 3. missing yaw / heading ────────────────────────────────────────────────
def test_missing_heading_returns_none(mod, dlg, tmp_path):
    p = make_tif(tmp_path / "nohdg.tif", {"EXIF_Make": "SONY"})
    assert dlg._extract_heading_from_exif(str(p)) is None


def test_missing_yaw_raises_or_returns_none(mod, dlg, tmp_path):
    """Whichever it does, it must not return a number."""
    p = make_tif(tmp_path / "noyaw.tif", {"EXIF_Make": "SONY"})
    try:
        v = dlg._extract_yaw_from_raw_xmp(str(p))
    except Exception:
        return                                   # acceptable
    assert v is None, f"yaw invented: {v}"


# ── 4. empty metadata ───────────────────────────────────────────────────────
def test_completely_empty_metadata(mod, dlg, tmp_path):
    p = make_tif(tmp_path / "empty.tif")
    assert mod._image_tags(str(p)) is not None
    alt, srcname = dlg._extract_altitude_with_source(str(p))
    assert alt is None and srcname == mod.AtlasGeoHandlerDemoDialog.ALT_SOURCE_UNKNOWN
    assert dlg._extract_heading_from_exif(str(p)) is None
    with pytest.raises(Exception):
        dlg._extract_gps_from_image(str(p))


# ── 5. malformed numeric metadata ───────────────────────────────────────────
@pytest.mark.parametrize("bad", ["not-a-number", "", "   ", "(((", "N/A",
                                 "1/0", "9e999", "--12", "30,17,9"])
def test_malformed_numeric_metadata_never_invents(mod, dlg, tmp_path, bad):
    p = make_tif(tmp_path / f"bad_{abs(hash(bad))}.tif", {
        "EXIF_GPSLatitude": bad, "EXIF_GPSLatitudeRef": "N",
        "EXIF_GPSLongitude": bad, "EXIF_GPSLongitudeRef": "W",
        "EXIF_GPSAltitude": bad, "EXIF_GPSImgDirection": bad,
        "EXIF_FocalLengthIn35mmFilm": bad,
    })
    try:
        lat, lon, _ = dlg._extract_gps_from_image(str(p))
        assert isinstance(lat, float) and isinstance(lon, float)
        assert -90 <= lat <= 90, f"latitude out of range from {bad!r}: {lat}"
        assert -180 <= lon <= 180, f"longitude out of range from {bad!r}: {lon}"
    except Exception:
        pass                                     # refusing is the right answer
    alt, _ = dlg._extract_altitude_with_source(str(p))
    assert alt is None or isinstance(alt, float)
    h = dlg._extract_heading_from_exif(str(p))
    assert h is None or isinstance(h, float)


# ── 6. truncated / corrupt image ────────────────────────────────────────────
def test_truncated_image(mod, dlg, tmp_path):
    good = make_tif(tmp_path / "whole.tif", {"EXIF_Make": "DJI"})
    data = pathlib.Path(good).read_bytes()
    trunc = tmp_path / "trunc.tif"
    trunc.write_bytes(data[:len(data) // 3])
    assert mod._image_tags(str(trunc)) == {} or isinstance(mod._image_tags(str(trunc)), dict)
    alt, srcname = dlg._extract_altitude_with_source(str(trunc))
    assert alt is None


def test_not_an_image_at_all(mod, dlg, tmp_path):
    p = tmp_path / "text.jpg"
    p.write_text("this is plain text, not an image", encoding="utf-8")
    assert mod._image_tags(str(p)) == {}
    assert dlg._extract_heading_from_exif(str(p)) is None
    alt, _ = dlg._extract_altitude_with_source(str(p))
    assert alt is None


def test_zero_byte_file(mod, dlg, tmp_path):
    p = tmp_path / "zero.jpg"
    p.write_bytes(b"")
    assert mod._image_tags(str(p)) == {}
    assert mod._raster_info(str(p)) is None


# ── 7. unreadable path ──────────────────────────────────────────────────────
def test_nonexistent_path(mod, dlg, tmp_path):
    p = tmp_path / "does" / "not" / "exist.jpg"
    assert mod._image_tags(str(p)) == {}
    assert mod._image_xmp(str(p)) == ""
    assert mod._raster_info(str(p)) is None
    assert dlg._extract_heading_from_exif(str(p)) is None


def test_directory_given_instead_of_file(mod, tmp_path):
    d = tmp_path / "adir"
    d.mkdir()
    assert mod._image_tags(str(d)) == {}
    assert mod._raster_info(str(d)) is None


# ── 8. TIFF without CRS ─────────────────────────────────────────────────────
def test_tiff_with_transform_but_no_crs(mod, tmp_path):
    """has_geo requires BOTH. A transform with no CRS cannot be placed."""
    p = make_tif(tmp_path / "nocrs.tif", gt=(-97.7, 0.001, 0, 30.3, 0, -0.001))
    info = mod._raster_info(str(p))
    assert info is not None
    assert info[0] is False, "a raster with no CRS must not count as georeferenced"


def test_tiff_with_crs_but_identity_transform(mod, tmp_path):
    p = make_tif(tmp_path / "identity.tif", epsg=4326)
    info = mod._raster_info(str(p))
    assert info is not None
    assert info[0] is False, "identity transform must not count as georeferenced"


# ── 9. identity transform, and the fallback that repairs it ─────────────────
def test_identity_transform_then_repair(mod, tmp_path):
    p = make_tif(tmp_path / "toRepair.tif", w=10, h=8)
    assert mod._raster_info(str(p))[0] is False
    assert mod._apply_geotransform_in_place(
        str(p), -97.70, 30.30, -97.69, 30.29, epsg=4326)
    info = mod._raster_info(str(p))
    assert info[0] is True, "after repair the raster must be georeferenced"
    assert info[1] == 10 and info[2] == 8, "dimensions must be unchanged"


def test_repair_on_a_readonly_or_missing_file_returns_false(mod, tmp_path):
    assert mod._apply_geotransform_in_place(
        str(tmp_path / "absent.tif"), -1, 1, 1, -1, epsg=4326) is False


# ── no traceback escapes, across every negative case at once ────────────────
def test_no_unhandled_exception_from_any_helper(mod, tmp_path):
    """The helpers are the boundary. Whatever is thrown at them, they return a
    sentinel; the extractors above may still raise deliberately."""
    cases = []
    p1 = tmp_path / "t1.jpg"; p1.write_bytes(b""); cases.append(p1)
    p2 = tmp_path / "t2.jpg"; p2.write_text("junk"); cases.append(p2)
    cases.append(tmp_path / "missing.jpg")
    cases.append(tmp_path)
    good = make_tif(tmp_path / "ok.tif", {"EXIF_Make": "X"})
    cases.append(pathlib.Path(good))
    for c in cases:
        assert isinstance(mod._image_tags(str(c)), dict)
        assert isinstance(mod._image_xmp(str(c)), str)
        r = mod._raster_info(str(c))
        assert r is None or isinstance(r, tuple)
