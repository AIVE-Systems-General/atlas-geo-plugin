# -*- coding: utf-8 -*-
"""The public listing must say what the product does, and only what it does.

Two separate jobs here.

BRANDING. The customer-visible name became AIVE ATLAS GEO for the 1.1.5
listing. The technical identity did NOT change: the package is still
atlas_geo_plugin, the repository is unchanged, and the QGIS plugin id is
unchanged, so existing installs receive this as an ordinary update rather than
as a second, competing plugin. Renaming the package would have created a new
listing and abandoned the existing one along with its download history. These
tests pin both halves, because the risk is someone "finishing the rename".

LISTING ACCURACY. The previous description claimed UAV pose estimation, which
the product does not do. A store listing is a public statement about
capability, and the ones removed here were either unsupported or contradicted
by the service's own behaviour. Each is pinned individually so it cannot
reappear by copy-paste from an older draft.

Checks read metadata.txt, never this file: a test that forbids a phrase has to
contain that phrase to look for it.
"""
import pathlib
import re

import pytest

HERE = pathlib.Path(__file__).resolve().parent
META = HERE / "metadata.txt"

VISIBLE_NAME = "AIVE ATLAS GEO"
PACKAGE_ID = "atlas_geo_plugin"


def meta_text():
    return META.read_text(encoding="utf-8")


def meta_field(key):
    """One metadata value, including any indented continuation lines."""
    out, capturing = [], False
    for line in meta_text().split("\n"):
        if line.startswith(key + "="):
            out.append(line[len(key) + 1:])
            capturing = True
        elif capturing:
            if line.startswith("    "):
                out.append(line[4:])
            else:
                break
    return "\n".join(out).strip()


# ── visible name ──────────────────────────────────────────────────────────

def test_metadata_name_is_the_new_visible_name():
    assert meta_field("name") == VISIBLE_NAME


def test_window_title_and_wordmarks_use_the_visible_name():
    ui = (HERE / "atlas_geo_plugin_dialog_base.ui").read_text(encoding="utf-8")
    assert "<string>%s</string>" % VISIBLE_NAME in ui
    assert "<string>ATLAS-GEO</string>" not in ui, (
        "a visible ATLAS-GEO wordmark survived the rename")


def test_menu_entry_uses_the_visible_name():
    src = (HERE / "atlas_geo_plugin.py").read_text(encoding="utf-8")
    assert VISIBLE_NAME in src
    assert "Atlas Georeferencer" not in src, (
        "the old menu label is still registered")


# ── technical identity must NOT change ────────────────────────────────────

def test_package_identity_is_unchanged():
    """The upgrade path depends on this. A new package id is a new listing, and
    the existing one, with its install base and download history, is abandoned.

    Checked against the Makefile, which is where the packaged folder name is
    actually decided, rather than against a display name that may legitimately
    differ from it.
    """
    assert (HERE / "__init__.py").is_file()
    mk = (HERE / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^PLUGINNAME\s*=\s*%s\s*$" % PACKAGE_ID, mk, re.M), (
        "the packaged folder name is no longer %s" % PACKAGE_ID)


def test_packaging_config_carries_no_removed_claim():
    """pb_tool.cfg is not shipped, but it is a packaging config that can
    publish a listing, and it carried the pose-estimation claim too."""
    cfg = (HERE / "pb_tool.cfg").read_text(encoding="utf-8").lower()
    assert "pose estimation" not in cfg


def test_repository_and_tracker_are_unchanged():
    for key in ("tracker", "repository", "homepage"):
        assert "AIVE-Systems-General/atlas-geo-plugin" in meta_field(key), (
            "%s no longer points at the existing repository" % key)


def test_client_version_prefix_is_unchanged():
    """The service identifies the client by this string."""
    src = (HERE / "atlas_geo_plugin_dialog.py").read_text(encoding="utf-8")
    assert "atlas-geo-plugin/%s" in src


@pytest.mark.parametrize("ident", ["AtlasGeoHandlerDemoDialog", PACKAGE_ID])
def test_internal_identifiers_still_present(ident):
    joined = "\n".join(p.read_text(encoding="utf-8")
                       for p in HERE.glob("*.py") if not p.name.startswith("test_"))
    assert ident in joined


# ── listing content: what it must say ─────────────────────────────────────

def test_version_is_rc2():
    assert meta_field("version") == "1.1.5-rc2"


def test_short_description_states_the_batch_limit():
    d = meta_field("description")
    assert "100" in d, "the submission limit must be stated, not implied"
    assert "200" in d and "free" in d.lower()


def test_short_description_avoids_the_full_flight_claim():
    """"Full flight" without the limit promises unbounded batches."""
    d = meta_field("description").lower()
    assert "full flight" not in d


@pytest.mark.parametrize("url", [
    "https://youtu.be/niRbey06EXU",
    "https://aivesystems.com/privacy",
    "https://aivesystems.com/approved-countries",
])
def test_about_carries_the_canonical_urls(url):
    assert url in meta_field("about")


def test_about_states_the_batch_limit_and_the_allowance():
    a = meta_field("about")
    assert "100 images per submission" in a
    assert "200 images each month" in a


def test_about_names_the_product():
    assert VISIBLE_NAME in meta_field("about")


# ── listing content: what it must NOT say ─────────────────────────────────

def test_no_safe_links_url_anywhere_in_metadata():
    """An Outlook-rewritten link leaks the tenant and rots when rewritten."""
    t = meta_text().lower()
    for marker in ("safelinks", "protection.outlook.com", "urldefense"):
        assert marker not in t, "metadata contains a rewritten link: %s" % marker


def test_no_pose_estimation_claim():
    """The product registers images to a reference map. It does not estimate
    camera or vehicle pose, and the previous listing said it did."""
    t = meta_text().lower()
    assert "pose estimation" not in t
    assert "pose-estimation" not in t


def test_no_claim_that_uploaded_data_is_not_accessed():
    """Uploads ARE processed by the hosted service. The defensible statement is
    about training, not about access, and that distinction is the point."""
    t = re.sub(r"\s+", " ", meta_text().lower())
    for bad in ("does not access", "do not access", "never access"):
        assert bad not in t, "metadata claims uploaded data is not accessed"


def test_training_statement_is_present_and_scoped():
    a = re.sub(r"\s+", " ", meta_field("about"))
    assert "not used to train or fine-tune AI models" in a


def test_no_local_or_on_device_processing_claim():
    a = re.sub(r"\s+", " ", meta_field("about")).lower()
    assert "does not run on the user's computer" in a
    for bad in ("runs locally", "on-device", "on your machine without"):
        assert bad not in a


def test_availability_is_described_by_declared_country_not_location():
    """Eligibility follows the country declared on the account. Claiming
    physical-location enforcement would describe a control that does not exist."""
    a = re.sub(r"\s+", " ", meta_field("about"))
    assert "country declared for the account" in a
    for bad in ("physical location", "geolocation", "GPS location of the user"):
        assert bad not in a


# ── encoding ──────────────────────────────────────────────────────────────

def test_metadata_stays_ascii():
    """It was ASCII before this change. QGIS parses metadata.txt itself, and a
    stray smart quote or em dash is the classic way a listing renders wrong."""
    bad = sorted({c for c in meta_text() if ord(c) > 127})
    assert not bad, "non-ASCII characters in metadata.txt: %r" % bad


# ── parsed through the reader QGIS actually uses ──────────────────────────

def _parsed():
    """metadata.txt via configparser, which is how QGIS reads plugin metadata.

    Asserting on the PARSED value rather than the file text is the point: a
    separator that looks fine in the source can still render as a literal
    character in the Plugin Manager, which is exactly what a run of lone "."
    lines did.
    """
    import configparser
    cp = configparser.ConfigParser()
    cp.read(str(META), encoding="utf-8")
    return cp


def test_metadata_parses_with_the_qgis_reader():
    cp = _parsed()
    assert cp.has_section("general")
    assert len(cp.options("general")) >= 17


def test_about_renders_without_artificial_separator_characters():
    """Lone "." lines were used as paragraph breaks and rendered as periods."""
    about = _parsed().get("general", "about")
    lone = [l for l in about.split("\n") if l.strip() in (".", "-", "*", "_")]
    assert not lone, "about renders %d artificial separator lines" % len(lone)


def test_about_is_complete_and_nothing_was_swallowed():
    """A mis-parsed continuation block silently eats the keys after it."""
    cp = _parsed()
    about = cp.get("general", "about")
    assert about.strip().startswith(VISIBLE_NAME)
    assert about.strip().endswith("QGIS.")
    for leaked in ("tracker=", "repository=", "changelog=", "tags=", "homepage="):
        assert leaked not in about, "%s was swallowed into about" % leaked


@pytest.mark.parametrize("key", ["tracker", "repository", "changelog", "tags",
                                 "homepage", "category", "icon", "experimental",
                                 "deprecated"])
def test_fields_after_about_remain_separate_keys(key):
    assert key in _parsed().options("general")


@pytest.mark.parametrize("url", [
    "https://youtu.be/niRbey06EXU",
    "https://aivesystems.com/privacy",
    "https://aivesystems.com/approved-countries",
])
def test_urls_survive_parsing_on_their_own_line(url):
    """On their own line so a renderer can linkify them, and unsplit."""
    about = _parsed().get("general", "about")
    assert any(l.strip() == url for l in about.split("\n")), (
        "%s is not intact on its own line after parsing" % url)


def test_about_parses_identically_under_a_strict_reader():
    """Not every consumer of this file is QGIS's own configparser call. The
    format must not depend on empty_lines_in_values being left at its default,
    because a blank line inside the value makes the two https lines collide as
    duplicate options the moment that value terminates early.
    """
    import configparser
    strict = configparser.ConfigParser(empty_lines_in_values=False)
    strict.read(str(META), encoding="utf-8")       # must not raise
    a = strict.get("general", "about")
    assert a.strip().endswith("QGIS.")
    assert len(strict.options("general")) == len(_parsed().options("general"))


# ── the removed claim must be gone from every customer-facing surface ─────

def packaged_files():
    """Every file the Makefile actually ships, read from the Makefile.

    ⚠️ DERIVED, NOT LISTED. A hand-kept list is why the claim survived in
    __init__.py through a pass that checked six other files: the file simply was
    not on the list. Reading the package definition means a file added to the
    ZIP is covered the day it is added.
    """
    # Join Makefile backslash continuations first, then read each assignment.
    mk = (HERE / "Makefile").read_text(encoding="utf-8").replace("\\\n", " ")
    names = []
    for key in ("PY_FILES", "UI_FILES", "EXTRAS"):
        m = re.search(r"^%s\s*=\s*(.*)$" % key, mk, re.M)
        if m:
            names += m.group(1).split()
    return sorted({n for n in names if (HERE / n).is_file()})


def test_packaged_file_list_is_discovered():
    files = packaged_files()
    assert len(files) >= 12, "only found %d packaged files: %r" % (len(files), files)
    assert "__init__.py" in files and "metadata.txt" in files


@pytest.mark.parametrize("name", packaged_files() + ["pb_tool.cfg"])
def test_no_pose_estimation_claim_in_any_shipped_file(name):
    """The product does not estimate camera or vehicle pose. pb_tool.cfg is
    added because it is not shipped but can publish a listing."""
    f = HERE / name
    if not f.is_file():
        pytest.skip("%s absent" % name)
    try:
        t = f.read_text(encoding="utf-8").lower()
    except UnicodeDecodeError:
        return                      # binary asset, nothing to claim
    assert "pose estimation" not in t and "pose-estimation" not in t, (
        "%s still claims pose estimation" % name)
