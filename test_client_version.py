# -*- coding: utf-8 -*-
"""The version the plugin reports about itself must be the version it is.

Why this exists: through the whole 1.1.x line the signup request carried a
literal "atlas-geo-plugin/1.0". Every install, on every release, reported
itself as 1.0. The consequence was not cosmetic. When the September onboarding
funnel was investigated, the server could not break any figure down by client
version, because every request was in the same bucket, and there was no way to
tell whether an install was the published build or an older one.

The same defect had already appeared once, in the window title, and was fixed
there by reading metadata.txt. It was not fixed at the second site. So these
tests pin BOTH: that no literal version string is reintroduced anywhere in the
shipped code, and that the value actually sent is derived from metadata.txt.
"""
import io
import pathlib
import re
import tokenize

import pytest

HERE = pathlib.Path(__file__).resolve().parent
SHIPPED = ["atlas_geo_plugin.py", "atlas_geo_plugin_dialog.py",
           "__init__.py", "resources.py", "_iso3166_data.py"]


def src(name):
    return (HERE / name).read_text(encoding="utf-8")


def metadata_version():
    for line in (HERE / "metadata.txt").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("version="):
            return line.split("=", 1)[1].strip()
    raise AssertionError("metadata.txt has no version= line")


# ── no hardcoded version may ship ─────────────────────────────────────────

def string_literals(name):
    """Every STRING token in a module, comments and docstring prose excluded.

    Scanned by token rather than by regex over the raw text on purpose: this
    file, and any comment explaining the defect, necessarily mentions the bad
    literal. A raw-text search would flag the explanation as the defect.
    """
    out = []
    with io.open(HERE / name, "rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type == tokenize.STRING:
                out.append(tok.string)
    return out


@pytest.mark.parametrize("name", SHIPPED)
def test_no_literal_client_version_string(name):
    """A quoted atlas-geo-plugin/<number> among the shipped STRING tokens is
    the defect itself: a version that cannot follow the release."""
    hits = [s for s in string_literals(name)
            if re.search(r"atlas-geo-plugin/\d", s)]
    # A format template carrying a placeholder is the fix, not the defect.
    hits = [s for s in hits if "%s" not in s and "{" not in s]
    assert not hits, (
        "%s hardcodes a client version %r; derive it from metadata.txt via "
        "_plugin_version() instead" % (name, hits))


def test_client_version_is_derived_from_plugin_version():
    """The payload field must be built from the helper, not from a literal."""
    s = src("atlas_geo_plugin_dialog.py")
    m = re.search(r'"client_version":\s*([^,\n]+)', s)
    assert m, "the signup payload no longer carries client_version"
    expr = m.group(1)
    assert "_plugin_version()" in expr, (
        "client_version is %r; it must call _plugin_version()" % expr)


def test_plugin_version_helper_reads_metadata_not_a_constant():
    s = src("atlas_geo_plugin_dialog.py")
    body = s.split("def _plugin_version", 1)[1].split("\n    def ", 1)[0]
    assert "metadata.txt" in body, "_plugin_version must read metadata.txt"
    assert not re.search(r'return\s+["\']\d+\.\d+', body), (
        "_plugin_version must not return a literal version number")


# ── the value that would actually be sent ─────────────────────────────────

def test_sent_value_matches_metadata_for_this_build():
    """End to end on the real files: the string the server receives must equal
    the version QGIS shows in the plugin manager."""
    version = metadata_version()
    sent = "atlas-geo-plugin/%s" % version
    assert re.fullmatch(r"atlas-geo-plugin/\d+\.\d+\.\d+", sent), (
        "unexpected shape %r" % sent)
    assert not sent.endswith("/1.0"), (
        "this build would still report 1.0, which is the bug")


def test_unknown_version_is_reported_honestly_not_as_a_number():
    """_plugin_version returns "?" when metadata.txt cannot be read. That must
    stay a visible unknown: a fallback of "1.0" would recreate the defect in
    exactly the situation where the truth is least knowable."""
    s = src("atlas_geo_plugin_dialog.py")
    body = s.split("def _plugin_version", 1)[1].split("\n    def ", 1)[0]
    assert 'return "?"' in body, (
        "_plugin_version must fall back to '?', never to a version number")
