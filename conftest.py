# -*- coding: utf-8 -*-
"""Shared test guards.

⚠️ NOT PART OF THE DISTRIBUTED PLUGIN. The release ZIP is built from an explicit
file list; this file is test-only and is never packaged.

The guard below exists because a settings-isolation bug went unnoticed for
several checkpoints. Tests redirected QSettings with QSettings.setPath, which
silently does nothing: QSettings(org, app) resolves through NativeFormat while
setPath is keyed BY FORMAT, and Qt caches the resolved file for the life of the
process. The tests looked isolated, passed, and were writing to the real
~/.config/AIVE/AtlasGeo.conf the whole time.

A claim of isolation is worth nothing unless something fails when it is untrue,
so this fails the run if any test changes a real settings location.
"""
import os
import pathlib

import pytest


def _real_settings_targets():
    """Every place the plugin's settings could really land on this machine.

    Covers the QSettings(org, app) file on each platform. The QGIS
    profile-backed store lives under the profile directory and is redirected
    per-test, so it is not listed here.
    """
    home = pathlib.Path(os.path.expanduser("~"))
    return [
        home / ".config" / "AIVE" / "AtlasGeo.conf",            # Linux
        home / ".config" / "AIVE.conf",
        home / "Library" / "Preferences" / "com.AIVE.AtlasGeo.plist",   # macOS
        home / "Library" / "Preferences" / "com.AIVE.plist",
    ]


def _fingerprint(paths):
    """(exists, size, mtime) per path. Cheap, and enough to catch a write."""
    out = {}
    for p in paths:
        try:
            st = p.stat()
            out[str(p)] = (True, st.st_size, st.st_mtime_ns)
        except OSError:
            out[str(p)] = (False, 0, 0)
    return out


@pytest.fixture(autouse=True)
def _no_real_settings_writes(request):
    """Fail the test if it touched a real user settings file.

    Autouse and unconditional: a test that needs an exception should isolate
    itself properly rather than opt out of the check.
    """
    targets = _real_settings_targets()
    before = _fingerprint(targets)
    yield
    after = _fingerprint(targets)
    changed = [p for p in before if before[p] != after[p]]
    assert not changed, (
        "this test wrote to a REAL settings location instead of a temporary "
        "one:\n  " + "\n  ".join(changed) +
        "\n\nQSettings.setPath does not redirect QSettings(org, app): it is "
        "keyed by format and the resolved file is cached for the process. "
        "Replace the settings object the plugin constructs instead."
    )
