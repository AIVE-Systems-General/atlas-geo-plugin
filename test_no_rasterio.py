# -*- coding: utf-8 -*-
"""Regression tests for removing the mandatory rasterio dependency, and for
the privacy-safe startup logging.

Why these exist: rasterio is not bundled with QGIS on any platform tested. It
is absent from the official QGIS 4.2.1 image, and on Windows QGIS 3.44 it only
appears when a user has pip-installed it into their own site-packages. Because
two extractors imported it outside their try block, a stock install raised
ModuleNotFoundError and uploads went out with no GPS.

Source-level tests run anywhere. Runtime tests need a real QGIS and are
skipped otherwise, because a mock cannot tell you whether GDAL actually
releases a file handle.
"""
import importlib.util
import io
import pathlib
import re
import tokenize
import zlib

import pytest

HERE = pathlib.Path(__file__).resolve().parent
SHIPPED = ["atlas_geo_plugin.py", "atlas_geo_plugin_dialog.py",
           "__init__.py", "resources.py", "_iso3166_data.py"]

try:
    import qgis.PyQt  # noqa: F401
    HAVE_QGIS = True
except Exception:                                             # noqa: BLE001
    HAVE_QGIS = False

needs_qgis = pytest.mark.skipif(not HAVE_QGIS, reason="needs a real QGIS Python")


def src(name):
    return (HERE / name).read_text(encoding="utf-8")


def load_dialog_module(tag):
    spec = importlib.util.spec_from_file_location(
        f"_dlg_{tag}", HERE / "atlas_geo_plugin_dialog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── no mandatory external dependency ────────────────────────────────────────
@pytest.mark.parametrize("name", SHIPPED)
def test_no_functional_rasterio_reference(name):
    """Comments explaining the removal are fine; a real reference is not."""
    bad = []
    for tok in tokenize.generate_tokens(io.StringIO(src(name)).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        if tok.string in ("rasterio", "RASTERIO_AVAILABLE", "from_bounds"):
            bad.append(f"{name}:{tok.start[0]}  {tok.string}")
    assert not bad, "functional rasterio reference:\n  " + "\n  ".join(bad)


def test_no_manual_pip_instruction_in_package():
    offenders = []
    for f in ("metadata.txt", "README.md", "INSTALL.md", "requirements.txt"):
        p = HERE / f
        if not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
            if re.search(r"pip\s+install", line, re.I):
                if re.search(r"no longer|previously|removed|does not|not read",
                             line, re.I):
                    continue
                offenders.append(f"{f}:{i}  {line.strip()[:70]}")
    assert not offenders, "manual install instruction:\n  " + "\n  ".join(offenders)


def test_requirements_declares_no_packages():
    p = HERE / "requirements.txt"
    if not p.exists():
        pytest.skip("no requirements.txt")
    txt = p.read_text(encoding="utf-8")
    assert re.search(r"not read this file|DOES NOT READ", txt, re.I), \
        "requirements.txt must say the Plugin Manager does not install from it"
    body = [l.strip() for l in txt.split("\n")
            if l.strip() and not l.strip().startswith("#")]
    assert not body, f"still declares packages: {body}"


def test_metadata_max_version_is_the_tested_family():
    m = re.search(r"qgisMaximumVersion=(\S+)",
                  (HERE / "metadata.txt").read_text(encoding="utf-8"))
    assert m, "qgisMaximumVersion missing"
    assert m.group(1) != "4.99", "4.99 claims untested future QGIS 4.x versions"
    assert m.group(1).startswith("4.2"), f"unexpected maximum {m.group(1)}"


# ── startup logging must never carry sensitive text ─────────────────────────
SENSITIVE = [
    ("windows_path", r"C:\Users\ragav.natarajan\AppData\Roaming\QGIS\x.ui"),
    ("macos_path",   "/Users/luis/Library/Application Support/QGIS/QGIS3/x.ui"),
    ("linux_home",   "/home/ragav/.local/share/QGIS/QGIS3/x.ui"),
    ("email",        "someone@aivesystems.com"),
    ("bearer",       "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.QQ.sig"),
    ("jwt",          "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.abc"),
    ("signed_url",   "https://storage.googleapis.com/b/o.tif?X-Goog-Signature=dead"),
    ("token_query",  "https://api.example.com/status?token=s3cr3t-value"),
]


def test_run_does_not_interpolate_the_exception_message():
    """Tokenised, so the comments that warn against these very patterns do not
    trip the check. Only executable code counts."""
    text = src("atlas_geo_plugin.py")
    code_lines = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        code_lines.append((tok.start[0], tok.string))
    joined = " ".join(t for _, t in code_lines)
    # f-string bodies survive tokenisation as FSTRING_MIDDLE/NAME tokens in
    # 3.12+, so check the reconstructed code stream for the dangerous calls.
    for forbidden in ("str ( exc )", "repr ( exc )", "format_exc"):
        assert forbidden.replace(" ", "") not in joined.replace(" ", ""), (
            f"run() uses {forbidden}; exception text is environment-controlled "
            f"and can carry paths, URLs and tokens")
    # and the raw f-string interpolation of the bare exception
    for i, line in enumerate(text.split("\n"), 1):
        stripped = line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
        assert "{exc}" not in stripped, (
            f"atlas_geo_plugin.py:{i} interpolates the exception message")


@pytest.mark.parametrize("label,payload", SENSITIVE, ids=[s[0] for s in SENSITIVE])
def test_startup_log_never_contains_sensitive_text(label, payload):
    """Rebuild the log line the way run() builds it and assert the payload
    cannot survive into either the log or the user-facing message."""
    exc = OSError(payload)
    code = f"ATLAS-INIT-{zlib.crc32(type(exc).__name__.encode()) % 10000:04d}"
    logged = (f"startup failed at stage=dialog_construct "
              f"error_class={type(exc).__name__} code={code}")
    shown = (f"The plugin could not start.\n\nReference code: {code}\n\n"
             f"Please report this code along with your QGIS version and "
             f"operating system.")
    for where, msg in (("log", logged), ("user message", shown)):
        assert payload not in msg, f"{label} leaked into the {where}"
        for frag in ("Bearer", "eyJ", "token=", "X-Goog-Signature",
                     "@aivesystems", "@example.com", "C:\\Users", "/Users/", "/home/"):
            assert frag not in msg, f"{frag!r} leaked into the {where} ({label})"


def test_startup_error_code_is_deterministic():
    text = src("atlas_geo_plugin.py")
    assert "zlib.crc32" in text, "error code must use a deterministic hash"
    assert "abs(hash(" not in text, "hash() is randomised per process"


def test_startup_message_does_not_assert_the_qgis_version_is_unsupported():
    assert "unsupported qgis version" not in src("atlas_geo_plugin.py").lower(), \
        "the message asserts a cause it cannot know"


# ── GDAL helpers ────────────────────────────────────────────────────────────
@needs_qgis
def test_helpers_release_the_file_handle(tmp_path):
    """On Windows an open GDAL handle locks the file, so reading metadata must
    not stop the file being replaced or deleted afterwards."""
    from osgeo import gdal
    mod = load_dialog_module("lock")
    p = tmp_path / "lock.tif"
    ds = gdal.GetDriverByName("GTiff").Create(str(p), 4, 4, 1)
    ds.SetMetadataItem("EXIF_Make", "TESTCAM")
    ds = None
    for _ in range(3):                                # repeated extraction
        assert mod._image_tags(str(p)).get("EXIF_Make") == "TESTCAM"
        assert isinstance(mod._image_xmp(str(p)), str)
        assert mod._raster_info(str(p)) is not None
    p.unlink()                        # PermissionError here if still open
    assert not p.exists()


@needs_qgis
def test_missing_or_malformed_file_returns_empty_not_raises(tmp_path):
    """Callers treat {} as unknown. Raising would abort an otherwise valid
    upload, which is exactly the bug rasterio caused."""
    mod = load_dialog_module("bad")
    assert mod._image_tags(str(tmp_path / "nope.jpg")) == {}
    junk = tmp_path / "junk.jpg"
    junk.write_bytes(b"not an image at all")
    assert mod._image_tags(str(junk)) == {}
    assert mod._image_xmp(str(junk)) == ""
    assert mod._raster_info(str(tmp_path / "nope.tif")) is None


@needs_qgis
def test_unicode_and_space_paths(tmp_path):
    """Real installs have spaces (Program Files, OneDrive - COMPANY) and
    non-ASCII user names."""
    from osgeo import gdal
    mod = load_dialog_module("uni")
    d = tmp_path / "a folder with spaces" / "ünïcødé"
    d.mkdir(parents=True)
    p = d / "imagé wîth spaces.tif"
    ds = gdal.GetDriverByName("GTiff").Create(str(p), 4, 4, 1)
    ds.SetMetadataItem("EXIF_Make", "UNICODE")
    ds = None
    assert mod._image_tags(str(p)).get("EXIF_Make") == "UNICODE"
    assert mod._raster_info(str(p)) is not None


@needs_qgis
def test_global_gdal_exception_mode_is_untouched(tmp_path):
    """gdal.UseExceptions() is process-wide and would change behaviour for QGIS
    itself and every other installed plugin."""
    from osgeo import gdal
    mod = load_dialog_module("exc")
    before = gdal.GetUseExceptions()
    mod._image_tags(str(tmp_path / "missing.jpg"))
    mod._raster_info(str(tmp_path / "missing.tif"))
    assert gdal.GetUseExceptions() == before, "global GDAL exception mode changed"
    # Tokenised: comments explaining that we no longer call it must not trip
    # this, only an actual call in executable code.
    text = src("atlas_geo_plugin_dialog.py")
    stream = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING):
            continue
        stream.append((tok.start[0], tok.string))
    flat = "".join(t for _, t in stream).replace(" ", "")
    assert "gdal.UseExceptions()" not in flat, (
        "a GLOBAL gdal.UseExceptions() call is present. It is process-wide and "
        "permanent: it changes GDAL behaviour for QGIS itself and every other "
        "installed plugin. Use gdal.ExceptionMgr to scope it.")


@needs_qgis
def test_identity_transform_is_detected_as_ungeoreferenced(tmp_path):
    from osgeo import gdal
    mod = load_dialog_module("ident")
    p = tmp_path / "plain.tif"
    ds = gdal.GetDriverByName("GTiff").Create(str(p), 8, 6, 1)
    ds = None
    info = mod._raster_info(str(p))
    assert info is not None
    assert info[0] is False, "a file with no CRS/transform must report has_geo False"
    assert info[1] == 8 and info[2] == 6 and info[3] == 1


@needs_qgis
def test_valid_transform_is_detected_as_georeferenced(tmp_path):
    from osgeo import gdal
    from osgeo import osr
    mod = load_dialog_module("valid")
    p = tmp_path / "geo.tif"
    ds = gdal.GetDriverByName("GTiff").Create(str(p), 8, 6, 3)
    ds.SetGeoTransform((-97.7, 0.001, 0.0, 30.3, 0.0, -0.001))
    sr = osr.SpatialReference(); sr.ImportFromEPSG(4326)
    ds.SetProjection(sr.ExportToWkt())
    ds = None
    info = mod._raster_info(str(p))
    assert info[0] is True and info[3] == 3


@needs_qgis
def test_attaching_georeferencing_does_not_change_pixels(tmp_path):
    """Georeferencing is header-only. The previous implementation rewrote the
    whole file to attach it, which copied every band for no reason."""
    import numpy as np
    from osgeo import gdal
    mod = load_dialog_module("hdr")
    p = tmp_path / "g.tif"
    ds = gdal.GetDriverByName("GTiff").Create(str(p), 8, 6, 1)
    arr = np.arange(48, dtype="uint8").reshape(6, 8)
    ds.GetRasterBand(1).WriteArray(arr)
    ds = None
    assert mod._apply_geotransform_in_place(
        str(p), -97.7, 30.3, -97.6, 30.2, epsg=4326)
    ds = gdal.Open(str(p))
    after = ds.GetRasterBand(1).ReadAsArray()
    gt, proj = ds.GetGeoTransform(), ds.GetProjection()
    ds = None
    assert np.array_equal(arr, after), "pixels changed while attaching georeferencing"
    # pixel-CORNER convention in both rasterio's from_bounds and GDAL, so the
    # top-left must land exactly on the requested corner with no half-pixel shift
    assert abs(gt[0] - (-97.7)) < 1e-12, "west edge moved"
    assert abs(gt[3] - 30.3) < 1e-12, "north edge moved"
    assert gt[1] > 0 and gt[5] < 0, "pixel size signs wrong"
    assert "4326" in proj


@needs_qgis
def test_hemisphere_signs_are_preserved(tmp_path):
    """No southern or eastern image exists in the local set (all 437 are N/W),
    so the sign logic is exercised synthetically here rather than left untested."""
    from osgeo import gdal
    mod = load_dialog_module("hemi")

    class FakeDialog:
        _extract_yaw_from_raw_xmp = staticmethod(
            lambda p: (_ for _ in ()).throw(ValueError("no xmp")))
        _extract_heading_from_exif = staticmethod(lambda p: None)
    fake = FakeDialog()

    cases = [("N", "E", 1, 1), ("S", "E", -1, 1),
             ("N", "W", 1, -1), ("S", "W", -1, -1)]
    for latref, lonref, esign_lat, esign_lon in cases:
        p = tmp_path / f"{latref}{lonref}.tif"
        ds = gdal.GetDriverByName("GTiff").Create(str(p), 4, 4, 1)
        ds.SetMetadataItem("EXIF_GPSLatitude", "(30) (17) (9.5)")
        ds.SetMetadataItem("EXIF_GPSLatitudeRef", latref)
        ds.SetMetadataItem("EXIF_GPSLongitude", "(97) (45) (17.3)")
        ds.SetMetadataItem("EXIF_GPSLongitudeRef", lonref)
        ds = None
        tags = mod._image_tags(str(p))
        assert tags.get("EXIF_GPSLatitudeRef") == latref
        assert tags.get("EXIF_GPSLongitudeRef") == lonref
        lat = mod.AtlasGeoHandlerDemoDialog._extract_gps_from_image(
            fake, str(p))
        assert lat[0] * esign_lat > 0, f"{latref}: latitude sign wrong ({lat[0]})"
        assert lat[1] * esign_lon > 0, f"{lonref}: longitude sign wrong ({lat[1]})"
