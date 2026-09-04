# -*- coding: utf-8 -*-
"""
 ATLAS Geo-Dock
 A QGIS plugin for UAV pose estimation and UAV-to-map registration.

 Copyright © 2026 AIVE AI Systems

 Georeferencing runs on AIVE AI Systems' hosted ATLAS service, not locally.
 An ATLAS account and an internet connection are required.

 This program is free software; you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation; either version 2 of the License, or
 (at your option) any later version.

 This program is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
 GNU General Public License for more details. A copy is distributed
 with this plugin in the file LICENSE.
"""
import os
import threading
import time
import datetime
import math
import re
import requests
import json
from pathlib import Path
from qgis.core import QgsRectangle
try:
    import rasterio
    from rasterio.transform import from_bounds
    from rasterio.crs import CRS
    RASTERIO_AVAILABLE = True
except ImportError:
    # rasterio powers georeferencing + report output but is NOT bundled with a stock
    # QGIS install. Importing defensively lets the plugin still load and show a clear
    # "install rasterio" message (see INSTALL.md) instead of failing to load entirely.
    rasterio = None
    from_bounds = None
    CRS = None
    RASTERIO_AVAILABLE = False

from qgis.PyQt import uic, QtWidgets, QtCore
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices, QPixmap, QColor
from qgis.core import QgsProject, QgsRasterLayer

FEEDBACK_EMAIL = "sales@aivesystems.com"


class FeedbackDialog(QtWidgets.QDialog):
    """Modal dialog that collects user feedback and opens the default mail client."""

    SUBJECTS = [
        "General Feedback",
        "Bug Report",
        "Feature Request",
        "Performance Issue",
        "Other",
    ]

    def __init__(self, user_email: str = "", parent=None):
        super().__init__(parent)
        self._user_email = user_email
        self.setWindowTitle("Send Feedback")
        self.setMinimumWidth(420)
        self.setModal(True)
        self._build_ui()

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(14)
        root.setContentsMargins(20, 20, 20, 20)

        # Header
        title = QtWidgets.QLabel("We'd love to hear from you")
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: #1a1612;")
        sub = QtWidgets.QLabel("Your feedback helps us improve ATLAS.")
        sub.setStyleSheet("font-size: 11px; color: #78716c; margin-bottom: 4px;")
        root.addWidget(title)
        root.addWidget(sub)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setStyleSheet("background-color: #e8e2d8; max-height: 1px;")
        root.addWidget(sep)

        # Subject
        lbl_subj = QtWidgets.QLabel("Subject")
        lbl_subj.setStyleSheet("font-size: 11px; font-weight: 600; color: #44403c;")
        self.combo_subject = QtWidgets.QComboBox()
        self.combo_subject.addItems(self.SUBJECTS)
        self.combo_subject.setStyleSheet(
            "font-size: 12px; padding: 5px 8px; border: 1.5px solid #e8e2d8;"
            "border-radius: 6px; background: #ffffff;"
        )
        root.addWidget(lbl_subj)
        root.addWidget(self.combo_subject)

        # Rating
        lbl_rating = QtWidgets.QLabel("Overall rating  (optional)")
        lbl_rating.setStyleSheet("font-size: 11px; font-weight: 600; color: #44403c;")
        root.addWidget(lbl_rating)

        star_row = QtWidgets.QHBoxLayout()
        star_row.setSpacing(4)
        self._stars = []
        for i in range(1, 6):
            btn = QtWidgets.QPushButton("☆")
            btn.setFixedSize(32, 32)
            btn.setStyleSheet(
                "font-size: 18px; border: none; background: transparent; color: #d6d3d1;"
            )
            btn.clicked.connect(lambda _, n=i: self._set_rating(n))
            star_row.addWidget(btn)
            self._stars.append(btn)
        star_row.addStretch()
        self._rating = 0
        root.addLayout(star_row)

        # Message
        lbl_msg = QtWidgets.QLabel("Message")
        lbl_msg.setStyleSheet("font-size: 11px; font-weight: 600; color: #44403c;")
        self.text_message = QtWidgets.QPlainTextEdit()
        self.text_message.setPlaceholderText(
            "Describe your experience, report a bug, or suggest a feature..."
        )
        self.text_message.setMinimumHeight(110)
        self.text_message.setStyleSheet(
            "font-size: 12px; padding: 6px; border: 1.5px solid #e8e2d8;"
            "border-radius: 6px; background: #ffffff;"
        )
        root.addWidget(lbl_msg)
        root.addWidget(self.text_message)

        # Info note
        note = QtWidgets.QLabel(
            "Clicking <b>Send</b> will open your email client with this feedback pre-filled."
        )
        note.setStyleSheet("font-size: 10px; color: #a8a29e;")
        note.setWordWrap(True)
        root.addWidget(note)

        # Inline validation message (replaces a native popup for the empty case).
        self.label_feedback_error = QtWidgets.QLabel("")
        self.label_feedback_error.setStyleSheet(
            "color:#b91c1c; font-weight:600; font-size:11px; padding:8px 11px;"
            " background-color:#fef2f2; border:1px solid #fecaca; border-radius:6px;")
        self.label_feedback_error.setWordWrap(True)
        self.label_feedback_error.hide()
        root.addWidget(self.label_feedback_error)

        # Buttons
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(10)

        btn_cancel = QtWidgets.QPushButton("Cancel")
        btn_cancel.setMinimumHeight(36)
        btn_cancel.setStyleSheet(
            "font-size: 12px; color: #78716c; background: transparent;"
            "border: 1.5px solid #e8e2d8; border-radius: 6px; padding: 6px 16px;"
        )
        btn_cancel.clicked.connect(self.reject)

        self.btn_send = QtWidgets.QPushButton("Send Feedback")
        self.btn_send.setMinimumHeight(36)
        self.btn_send.setStyleSheet(
            "QPushButton { font-size: 12px; font-weight: 700; color: #ffffff;"
            " background-color: #ea580c; border: none; border-radius: 6px; padding: 8px 20px; }"
            "QPushButton:hover { background-color: #c2410c; }"
        )
        self.btn_send.clicked.connect(self._send)

        btn_row.addWidget(btn_cancel)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_send)
        root.addLayout(btn_row)

    def _set_rating(self, n: int):
        self._rating = n
        for i, btn in enumerate(self._stars):
            if i < n:
                btn.setText("★")
                btn.setStyleSheet(
                    "font-size: 18px; border: none; background: transparent; color: #f59e0b;"
                )
            else:
                btn.setText("☆")
                btn.setStyleSheet(
                    "font-size: 18px; border: none; background: transparent; color: #d6d3d1;"
                )

    def _send(self):
        message = self.text_message.toPlainText().strip()
        if not message:
            self.label_feedback_error.setText("Please write a message before sending.")
            self.label_feedback_error.show()
            self.text_message.setFocus()
            return
        self.label_feedback_error.hide()

        subject = self.combo_subject.currentText()
        # ASCII-only in the email body: mailto: bodies have no reliable charset, and
        # Windows mail clients decode them as Windows-1252 — Unicode stars (★/☆) would
        # arrive as mojibake ("â˜…"). The dialog UI still shows the real stars.
        rating_line = (f"Rating: {self._rating}/5  [{'*' * self._rating}{'.' * (5 - self._rating)}]\n"
                       if self._rating else "")
        user_line = f"User: {self._user_email}\n" if self._user_email else ""

        body = (
            f"{rating_line}"
            f"{user_line}"
            f"\n{message}\n\n"
            f"---\nSent from ATLAS UAV Georeferencing Plugin"
        )

        from urllib.parse import quote
        mailto = (
            f"mailto:{FEEDBACK_EMAIL}"
            f"?subject={quote(f'[ATLAS Feedback] {subject}')}"
            f"&body={quote(body)}"
        )

        QDesktopServices.openUrl(QUrl(mailto))
        self.accept()
        par = self.parent()
        if par is not None and hasattr(par, "_themed_notice"):
            par._themed_notice(
                "Feedback ready",
                "We've opened your email client with the feedback pre-filled. "
                "Hit Send there to submit it.",
                accent="green", button="Done")
        else:
            QtWidgets.QMessageBox.information(
                self, "Feedback ready",
                "Your email client has been opened with the feedback pre-filled. "
                "Hit Send in your email app to submit it.")

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'atlas_geo_plugin_dialog_base.ui'))

_CLR_PENDING  = "color: #334155; padding: 3px 0px;"
_CLR_RUNNING  = "color: #f59e0b; font-weight: bold; padding: 3px 0px;"
_CLR_DONE     = "color: #10b981; padding: 3px 0px;"
_CLR_ERROR    = "color: #ef4444; font-weight: bold; padding: 3px 0px;"

_GLYPH_PENDING = "  "
_GLYPH_RUNNING = "⟳ "
_GLYPH_DONE    = "✓ "
_GLYPH_ERROR   = "✗ "

_STEP_LABELS = [
    "Uploading image data",
    "Analyzing metadata",
    "Running image matcher",
    "Preparing output layer",
]

# ─────────────────────────────────────────────────────────────
# BACKEND CONFIG
# ─────────────────────────────────────────────────────────────
# Service defaults. These are what a user gets, because a user will never set an
# environment variable.
#
# HTTPS only: the gateway serves TLS on these names and redirects port 80, and
# the tokens and imagery crossing this link must never travel in the clear.
#
# The environment overrides exist so a deployment can be pointed at a different
# ATLAS instance without editing an installed plugin.
AUTH_BASE_URL  = os.getenv("ATLAS_AUTH_URL",  "https://auth.aivesystems.com")
ATLAS_BASE_URL = os.getenv("ATLAS_API_URL",   "https://api.aivesystems.com")

SIGNUP_URL         = f"{AUTH_BASE_URL}/signup"
# Which Terms and Privacy Policy are CURRENT is decided by the server, never
# here. This plugin is publicly distributed, so old builds stay installed
# indefinitely; a hard-coded version would let one of them collect agreement to
# a document that was superseded long ago, and the record would look valid.
# There is deliberately no fallback constant to fall back TO.
LEGAL_CONFIG_URL   = f"{AUTH_BASE_URL}/legal/config"
SIGNIN_URL         = f"{AUTH_BASE_URL}/signin"
REFRESH_URL        = f"{AUTH_BASE_URL}/refresh"
REGISTER_URL       = f"{ATLAS_BASE_URL}/register"
CANCEL_URL         = f"{ATLAS_BASE_URL}/cancel"
RESET_PASSWORD_URL = f"{AUTH_BASE_URL}/reset-password"
LOGOUT_URL         = f"{AUTH_BASE_URL}/logout"

# Billing shares the API host: the gateway routes the billing prefixes
# (/balance, /checkout, /subscription, /portal, /storage, /trial-request,
# /billing) are served on that host alongside the API.
#
# NOT .../billing as a prefix: /balance is served at the root, so adding one
# would request /billing/balance and get a 404.
BILLING_BASE_URL   = os.getenv("ATLAS_BILLING_URL", "https://api.aivesystems.com")
BALANCE_URL        = f"{BILLING_BASE_URL}/balance"
TRIAL_REQUEST_URL  = f"{BILLING_BASE_URL}/trial-request"
TRIAL_STATUS_URL   = f"{BILLING_BASE_URL}/trial-request/status"
CHECKOUT_TOKENS_URL= f"{BILLING_BASE_URL}/checkout/tokens"
CHECKOUT_SUB_URL   = f"{BILLING_BASE_URL}/checkout/subscription"
SWITCH_URL         = f"{BILLING_BASE_URL}/subscription/switch"
SUB_CANCEL_URL     = f"{BILLING_BASE_URL}/subscription/cancel"
SUB_CANCEL_SWITCH_URL = f"{BILLING_BASE_URL}/subscription/cancel-switch"
SUB_REACTIVATE_URL = f"{BILLING_BASE_URL}/subscription/reactivate"
PORTAL_URL         = f"{BILLING_BASE_URL}/portal"
STORAGE_USAGE_URL  = f"{BILLING_BASE_URL}/storage/usage"          # Phase-1B: tiered storage
STORAGE_DELETE_URL = f"{BILLING_BASE_URL}/storage/delete-oldest"
STORAGE_MIGRATE_URL = f"{BILLING_BASE_URL}/storage/migrate"       # Phase-1B: org file migration choice

# Phase-2 scheduling perks on the Plans cards (priority + concurrency). Kept FALSE
# until the corresponding server-side enforcement is enabled,
# so we never advertise a perk the backend isn't enforcing yet. Flip to True at the
# same time you enable scheduling (and reship the plugin).
SHOW_SCHEDULING_PERKS = False

# Subscription tiers are not part of the current service model. The capability
# is retained rather than deleted so it can be restored by configuration alone.
SHOW_SUBSCRIPTION_TIERS = os.getenv("ATLAS_SHOW_SUBSCRIPTION_TIERS", "").lower() == "true"

# Pay As You Go purchase entry points. Off by default for the current model.
SHOW_PAYG = os.getenv("ATLAS_SHOW_PAYG", "").lower() == "true"

# Evaluation programme request form. Off by default for the current model.
SHOW_TRIAL_REQUEST = os.getenv("ATLAS_SHOW_TRIAL_REQUEST", "").lower() == "true"

# Basemap tiles through our proxy, so the licensed vendor token stays
# server-side and never ships inside a downloadable plugin.
#
# No durable credential is embedded in this artifact. GET /tiles/session
# exchanges the signed-in user's ATLAS token for a short-lived, per-user,
# rate-limited session that grants display tiles only and then expires.
#
# Same host as the API: the gateway routes /tiles to the tile proxy and
# everything else to the main API.
TILES_BASE_URL     = os.getenv("ATLAS_TILES_URL", "https://api.aivesystems.com")
TILE_SESSION_URL   = f"{TILES_BASE_URL.rstrip('/')}/tiles/session"

# Chunked upload: images sent per /register call. Every chunk of one selection shares
# ONE batch_id (the first chunk creates it, the rest reuse it) so the consecutive-fail
# guard AND cancel span the whole batch. Must be <= the server's batch limit.
# Images per upload request. 50 put ~419 MB of DJI frames in a single POST and
# left the whole chunk to fail together on a slow link; 20 is ~168 MB, completes
# in a third of the time, gives the user visible progress sooner, and limits how
# much has to be retried when a request does fail.
UPLOAD_CHUNK_SIZE  = int(os.getenv("ATLAS_UPLOAD_CHUNK", "20"))

# Client-side upload optimisation: downscale each image's long edge to this many
# pixels and re-encode JPEG before upload. GPS/yaw/altitude are extracted from
# the ORIGINAL beforehand and the matcher works at 800px, so this is effectively
# lossless for georeferencing while cutting upload size ~5-10x. Safety: if
# compression fails for an image it falls back to the original, and the copy is
# only used when it's actually smaller. Set ATLAS_UPLOAD_MAX_EDGE=0 to disable
# (upload originals) without a code change.
UPLOAD_MAX_EDGE     = int(os.getenv("ATLAS_UPLOAD_MAX_EDGE", "2048"))
UPLOAD_JPEG_QUALITY = int(os.getenv("ATLAS_UPLOAD_JPEG_QUALITY", "85"))

# ISO 3166-1 countries for the signup dropdown.
#
# GENERATED, not hand-written: see infra/gen-iso3166.py. The first version of
# this list was written by hand and had 227 entries against ISO's 249, silently
# missing inhabited places such as Sao Tome and Principe and the Faroe Islands.
#
# Imported from a sibling module so the plugin ships the SAME list the server
# validates against. Falls back to an empty list rather than failing to load:
# the country field is optional, and a packaging mistake must not stop someone
# signing in.
try:
    from ._iso3166_data import COUNTRIES
except ImportError:  # pragma: no cover - direct execution outside the package
    try:
        from _iso3166_data import COUNTRIES
    except ImportError:
        COUNTRIES = []


# An email that this WILL accept and a real mail server will not is possible;
# the point is only to catch the obvious mistakes before we ask the server, so
# the user gets "Enter a valid email address" instead of a validation dump.
# Deliberately rejects whitespace anywhere, which the previous check
# (`"@" in email and "." in email`) allowed: "red @gmail.com" passed it, went to
# the server, and came back as a raw pydantic error the user could not act on.
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")


def server_message(resp, fallback="Something went wrong. Please try again."):
    """Turn any FastAPI error body into one sentence a person can act on.

    FastAPI returns `detail` in three shapes and only one of them is a string:

      422  detail is a LIST of {loc, msg, type} dicts from pydantic
      4xx  detail is a STRING we wrote
      auth detail is a DICT, sometimes carrying Keycloak's error_description

    Formatting the list straight into the UI is what produced
    "[{'type': 'value_error', 'loc': ['body', 'email'], ...}]" on the reset
    screen. Anything not understood falls back rather than being dumped: a raw
    internal payload in front of a customer is worse than a generic sentence.
    """
    try:
        body = resp.json()
    except Exception:
        return fallback

    detail = body.get("detail") if isinstance(body, dict) else None

    if isinstance(detail, str) and detail.strip():
        return detail.strip()

    if isinstance(detail, dict):
        for k in ("error_description", "message", "error"):
            v = detail.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return fallback

    if isinstance(detail, list) and detail:
        parts = []
        for item in detail:
            if not isinstance(item, dict):
                continue
            # loc is like ["body", "email"]; the last element names the field.
            loc = [str(x) for x in (item.get("loc") or []) if x != "body"]
            field = loc[-1] if loc else ""
            msg = str(item.get("msg") or "").strip()
            # Pydantic's phrasing is written for developers. Rewrite the cases a
            # signup form actually hits; pass anything else through trimmed.
            low = msg.lower()
            if "valid email" in low:
                msg = "Enter a valid email address"
            elif low.startswith("field required") or "missing" in low:
                msg = "This field is required"
            elif "at least" in low and "characters" in low:
                msg = msg[0].upper() + msg[1:]
            else:
                msg = msg[0].upper() + msg[1:] if msg else "Invalid value"
            label = field.replace("_", " ") if field else ""
            parts.append(f"{label.capitalize()}: {msg}" if label else msg)
        if parts:
            # One line. A stack of field errors in a small dialog is unreadable.
            return parts[0] if len(parts) == 1 else "; ".join(parts[:3])

    return fallback



class TrialRequestDialog(QtWidgets.QDialog):
    """Request an evaluation allowance.

    Submits to the backend rather than opening a mail client the way
    FeedbackDialog does: a trial request needs to land in a reviewable queue,
    not an inbox. Nothing is granted here — an administrator approves it at
    /admin/requests, which keeps the "no self-serve grant" decision intact
    while giving the user a way to ask.

    Sign-in is required by the endpoint, so the email is taken from the verified
    token server-side and is shown here read-only.
    """

    TEAM_SIZES = ["Just me", "2-10", "11-50", "51-200", "200+"]
    VOLUMES    = ["Under 100", "100-1,000", "1,000-5,000", "5,000-20,000", "20,000+"]

    # access_token defaults to None, not "". An empty-string default on a
    # credential-shaped parameter is what Bandit reports as B107
    # (hardcoded_password_default), and the QGIS plugin repository classifies
    # B107 as CRITICAL regardless of Bandit's own Low rating, which blocks
    # approval. There was never a credential here, but "no token yet" is
    # honestly None rather than an empty string, so the fix is a better
    # signature rather than a scanner suppression.
    def __init__(self, user_email: str = "", access_token: str | None = None,
                 resubmit: bool = False, previous: dict = None, parent=None):
        super().__init__(parent)
        self._user_email = user_email
        # Normalised so every consumer keeps seeing a plain string; the guard at
        # the request site is falsy for both None and "".
        self._access_token = access_token or ""
        # Two modes, one dialog. A first-time applicant must NOT be told to
        # "resubmit" or to give MORE detail before they have given any.
        self._resubmit = bool(resubmit)
        self._previous = previous or {}
        self.setWindowTitle("Resubmit Trial Request" if self._resubmit
                            else "Request a Free Trial")
        self.setMinimumWidth(460)
        self.setModal(True)
        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────
    def _lbl(self, text):
        w = QtWidgets.QLabel(text)
        w.setStyleSheet("font-size: 11px; font-weight: 600; color: #44403c;")
        return w

    def _field(self, placeholder=""):
        w = QtWidgets.QLineEdit()
        w.setPlaceholderText(placeholder)
        w.setStyleSheet(
            "font-size: 12px; padding: 6px 8px; border: 1.5px solid #e8e2d8;"
            "border-radius: 6px; background: #ffffff;")
        return w

    def _combo(self, items):
        w = QtWidgets.QComboBox()
        w.addItems(items)
        w.setStyleSheet(
            "font-size: 12px; padding: 5px 8px; border: 1.5px solid #e8e2d8;"
            "border-radius: 6px; background: #ffffff;")
        return w

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setSpacing(11)
        root.setContentsMargins(20, 20, 20, 20)

        title = QtWidgets.QLabel("Resubmit Trial Request" if self._resubmit
                                 else "Request a free trial")
        title.setStyleSheet("font-size: 15px; font-weight: 700; color: #1a1612;")
        sub = QtWidgets.QLabel(
            "Please provide a bit more detail about your project so our team "
            "can re-evaluate your request." if self._resubmit else
            "Tell us a little about your work and we'll get back to you.")
        sub.setStyleSheet("font-size: 11px; color: #78716c; margin-bottom: 4px;")
        root.addWidget(title)
        root.addWidget(sub)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.HLine)
        sep.setStyleSheet("background-color: #e8e2d8; max-height: 1px;")
        root.addWidget(sep)

        # Account shown read-only: the server takes the email from the token,
        # so letting it be edited here would be misleading.
        if self._user_email:
            acct = QtWidgets.QLabel(f"Signed in as <b>{self._user_email}</b>")
            acct.setStyleSheet("font-size: 11px; color: #57534e;")
            root.addWidget(acct)

        self.in_name = self._field("Jane Smith")
        root.addWidget(self._lbl("Your name"))
        root.addWidget(self.in_name)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)
        cbox = QtWidgets.QVBoxLayout()
        self.in_company = self._field("Acme Surveying")
        cbox.addWidget(self._lbl("Company or organisation"))
        cbox.addWidget(self.in_company)
        rbox = QtWidgets.QVBoxLayout()
        self.in_role = self._field("GIS Analyst")
        rbox.addWidget(self._lbl("Your role"))
        rbox.addWidget(self.in_role)
        row.addLayout(cbox)
        row.addLayout(rbox)
        root.addLayout(row)

        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(10)
        tbox = QtWidgets.QVBoxLayout()
        self.cmb_team = self._combo(self.TEAM_SIZES)
        tbox.addWidget(self._lbl("Team size"))
        tbox.addWidget(self.cmb_team)
        vbox = QtWidgets.QVBoxLayout()
        self.cmb_volume = self._combo(self.VOLUMES)
        vbox.addWidget(self._lbl("Images per month"))
        vbox.addWidget(self.cmb_volume)
        row2.addLayout(tbox)
        row2.addLayout(vbox)
        root.addLayout(row2)

        root.addWidget(self._lbl("How do you plan to use ATLAS?"))
        self.txt_use_case = QtWidgets.QPlainTextEdit()
        self.txt_use_case.setPlaceholderText(
            "e.g. Georeferencing drone survey imagery of coastal erosion sites "
            "for a local council, roughly monthly flights.")
        self.txt_use_case.setMinimumHeight(84)
        self.txt_use_case.setStyleSheet(
            "font-size: 12px; padding: 6px; border: 1.5px solid #e8e2d8;"
            "border-radius: 6px; background: #ffffff;")
        root.addWidget(self.txt_use_case)

        if self._resubmit:
            tip = QtWidgets.QLabel(
                "<b>Tip:</b> Be as specific as possible about your drone platform, "
                "hardware integration (e.g. LiDAR, IMU, camera specs), and "
                "geolocation needs to speed up approval.")
            tip.setTextFormat(QtCore.Qt.RichText)
            tip.setWordWrap(True)
            tip.setStyleSheet(
                "font-size: 10px; color: #78716c; background: #f2ede4;"
                " border-left: 3px solid #d8cdbb; border-radius: 0 6px 6px 0;"
                " padding: 7px 10px;")
            root.addWidget(tip)

        self.label_error = QtWidgets.QLabel("")
        self.label_error.setStyleSheet(
            "color:#b91c1c; font-weight:600; font-size:11px; padding:8px 11px;"
            " background-color:#fef2f2; border:1px solid #fecaca; border-radius:6px;")
        self.label_error.setWordWrap(True)
        self.label_error.hide()
        root.addWidget(self.label_error)

        # Says where the answer will appear, NOT that we'll email -- nothing in the
        # the service sends mail, and promising one would leave a declined
        # applicant waiting indefinitely for a message that never comes.
        note = QtWidgets.QLabel(
            "Your previous answers are filled in below. Edit them, then submit. "
            "Check back in the account menu to see the decision."
            if self._resubmit else
            "Trial requests are reviewed by our team. Check back here in the "
            "account menu to see the decision.")
        note.setStyleSheet("font-size: 10px; color: #a8a29e;")
        note.setWordWrap(True)
        root.addWidget(note)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.setSpacing(10)
        btn_cancel = QtWidgets.QPushButton("Cancel")
        btn_cancel.setMinimumHeight(36)
        btn_cancel.setStyleSheet(
            "font-size: 12px; color: #78716c; background: transparent;"
            "border: 1.5px solid #e8e2d8; border-radius: 6px; padding: 6px 16px;")
        btn_cancel.clicked.connect(self.reject)

        self.btn_submit = QtWidgets.QPushButton(
            "Submit New Request" if self._resubmit else "Send Request")
        self.btn_submit.setMinimumHeight(36)
        self.btn_submit.setStyleSheet(
            "QPushButton { font-size: 12px; font-weight: 700; color: #ffffff;"
            " background-color: #ea580c; border: none; border-radius: 6px; padding: 8px 20px; }"
            "QPushButton:hover { background-color: #c2410c; }"
            "QPushButton:disabled { background-color: #d6d3d1; }")
        self.btn_submit.clicked.connect(self._submit)

        btn_row.addWidget(btn_cancel)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_submit)
        root.addLayout(btn_row)

        self._apply_previous()

    def _apply_previous(self):
        """Pre-fill from the user's last request (returned by /trial-request/status).

        NOT from the access token: that carries only email and name, so company,
        role and volume could not be restored from it. Carrying the previous
        answers forward also fits what is being asked -- the request was declined
        for wanting more detail, so the user should be editing what they wrote,
        not retyping it from a blank form and losing the parts that were fine.
        """
        prev = self._previous or {}
        if not prev:
            return
        for key, widget in (("full_name", self.in_name),
                            ("company",   self.in_company),
                            ("role",      self.in_role)):
            val = prev.get(key)
            if val:
                widget.setText(str(val))
        for key, combo in (("team_size",       self.cmb_team),
                           ("expected_images", self.cmb_volume)):
            val = prev.get(key)
            if val:
                # findText, not setCurrentText: an unrecognised value would
                # otherwise be silently added as a new option and submitted.
                i = combo.findText(str(val))
                if i >= 0:
                    combo.setCurrentIndex(i)
        if prev.get("use_case"):
            self.txt_use_case.setPlainText(str(prev["use_case"]))

    # ── Submit ────────────────────────────────────────────────────────────
    def _fail(self, msg, focus=None):
        self.label_error.setText(msg)
        self.label_error.show()
        self.btn_submit.setEnabled(True)
        self.btn_submit.setText("Submit New Request" if self._resubmit
                                else "Send Request")
        if focus:
            focus.setFocus()

    def _submit(self):
        use_case = self.txt_use_case.toPlainText().strip()
        # Mirrors the server's own check so the user gets the message inline
        # rather than as an HTTP error.
        if len(use_case) < 10:
            self._fail("Please tell us a little about how you plan to use ATLAS.",
                       self.txt_use_case)
            return
        self.label_error.hide()
        self.btn_submit.setEnabled(False)
        self.btn_submit.setText("Sending…")

        payload = {
            "full_name":       self.in_name.text().strip() or None,
            "company":         self.in_company.text().strip() or None,
            "role":            self.in_role.text().strip() or None,
            "team_size":       self.cmb_team.currentText(),
            "expected_images": self.cmb_volume.currentText(),
            "use_case":        use_case,
            "source":          "plugin",
        }
        headers = {}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"

        try:
            resp = requests.post(TRIAL_REQUEST_URL, json=payload,
                                 headers=headers, timeout=20)
        except Exception as e:
            self._fail(f"Could not reach the server. Please try again. ({e})")
            return

        if resp.status_code in (200, 201):
            self.accept()
            return
        if resp.status_code in (401, 403):
            self._fail("Your session has expired. Please sign in again.")
            return
        # 409 covers 'already on a plan' and 'request already pending' — both are
        # useful messages the server writes, so surface them rather than a generic.
        try:
            detail = resp.json().get("detail") or resp.text
        except Exception:
            detail = resp.text or f"Request failed ({resp.status_code})."
        self._fail(str(detail))


class ThemedDialog(QtWidgets.QDialog):
    """Frameless, theme-styled modal with a draggable header.

    Callers add their content widgets/layouts to ``self.body``.
    """

    def __init__(self, parent=None, title="ATLAS-GEO", subtitle=""):
        super().__init__(parent)
        self.setWindowFlags(QtCore.Qt.Dialog | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self._drag_pos = None

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        self.card = QtWidgets.QFrame()
        self.card.setObjectName("themed_card")
        self.card.setStyleSheet(
            "QFrame#themed_card {"
            "  background-color: #f7f3ec;"
            "  border: 1px solid #e4dccf;"
            "  border-radius: 16px;"
            "}"
        )
        outer.addWidget(self.card)

        cl = QtWidgets.QVBoxLayout(self.card)
        cl.setContentsMargins(22, 18, 22, 20)
        cl.setSpacing(0)

        # ── Header: title block + round close button ──
        hdr = QtWidgets.QHBoxLayout()
        hdr.setSpacing(8)
        tbox = QtWidgets.QVBoxLayout()
        tbox.setSpacing(2)
        self._title = QtWidgets.QLabel(title)
        self._title.setStyleSheet(
            "font-size: 18px; font-weight: 800; color: #1a1612;"
            " background: transparent; border: none;")
        tbox.addWidget(self._title)
        if subtitle:
            sub = QtWidgets.QLabel(subtitle)
            sub.setStyleSheet(
                "font-size: 12px; color: #78716c;"
                " background: transparent; border: none;")
            tbox.addWidget(sub)
        hdr.addLayout(tbox)
        hdr.addStretch(1)
        close_btn = QtWidgets.QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setCursor(QtCore.Qt.PointingHandCursor)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #a8a29e; border: none;"
            "  border-radius: 14px; font-size: 14px; font-weight: bold; }"
            "QPushButton:hover { background: #ece6dd; color: #1a1612; }")
        close_btn.clicked.connect(self.reject)
        hdr.addWidget(close_btn, 0, QtCore.Qt.AlignTop)
        cl.addLayout(hdr)

        # ── Divider ──
        cl.addSpacing(14)
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet("background-color: #e7ded2; max-height: 1px; border: none;")
        cl.addWidget(line)
        cl.addSpacing(16)

        # ── Body (callers populate this) ──
        self.body = QtWidgets.QVBoxLayout()
        self.body.setSpacing(12)
        cl.addLayout(self.body)

    def showEvent(self, e):
        """Center over the parent window (frameless dialogs don't auto-center)."""
        super().showEvent(e)
        par = self.parent()
        if par is not None:
            geo = self.frameGeometry()
            geo.moveCenter(par.frameGeometry().center())
            self.move(geo.topLeft())

    # Drag-to-move (frameless window has no native title bar)
    def mousePressEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton:
            self._drag_pos = e.globalPos() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and (e.buttons() & QtCore.Qt.LeftButton):
            self.move(e.globalPos() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag_pos = None


class MenuRow(QtWidgets.QWidget):
    """A clickable menu row that can be coloured, unlike a native QMenu item.

    QMenu offers no per-item text colour, so a status that needs to read as
    "needs your attention" cannot be expressed with a plain QAction — the earlier
    build had to use a coloured dot icon instead, which also forced Qt to indent
    every other item in the menu. Wrapping this widget in a QWidgetAction gives
    full control of colour, weight and a trailing chevron, at the cost of having
    to emit the click and close the menu ourselves.

    The same pattern is already used for the Storage caption; this one is
    interactive rather than a caption.
    """

    clicked = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        h = QtWidgets.QHBoxLayout(self)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(8)
        self._label = QtWidgets.QLabel()
        self._chev = QtWidgets.QLabel("›")
        h.addWidget(self._label)
        h.addStretch(1)
        h.addWidget(self._chev)
        self._colour = "#44403c"
        self._bold = False
        self._restyle(hover=False)

    def set_row(self, text, colour="#44403c", bold=False, chevron=True):
        self._label.setText(text)
        self._colour = colour
        self._bold = bold
        self._chev.setVisible(chevron)
        self._restyle(hover=False)

    def _restyle(self, hover):
        weight = "700" if self._bold else "400"
        self.setStyleSheet(
            f"MenuRow {{ background:{'#f3f4f6' if hover else 'transparent'};"
            "  border-radius:2px; }")
        css = (f"background:transparent; border:none; font-size:12px;"
               f" color:{self._colour}; font-weight:{weight};")
        self._label.setStyleSheet(css)
        self._chev.setStyleSheet(css)

    # QWidgetAction rows get no hover highlight for free — paint it ourselves so
    # the row still behaves like the native items around it.
    def enterEvent(self, e):
        self._restyle(hover=True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._restyle(hover=False)
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == QtCore.Qt.LeftButton and self.rect().contains(e.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class ToggleSwitch(QtWidgets.QCheckBox):
    """A sleek, drawn on/off switch that behaves exactly like a QCheckBox
    (isChecked()/setChecked()/toggled all work) — used for 'Auto-load to canvas'."""
    def sizeHint(self):
        fm = self.fontMetrics()
        try:
            tw = fm.horizontalAdvance(self.text())
        except Exception:
            tw = fm.width(self.text())
        return QtCore.QSize(56 + tw, 28)

    def hitButton(self, pos):
        return self.rect().contains(pos)   # whole widget toggles

    def paintEvent(self, e):
        from qgis.PyQt.QtGui import QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = 46, 24
        y = (self.height() - h) // 2
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QColor("#ea580c") if self.isChecked() else QColor("#d6cfca"))
        p.drawRoundedRect(QtCore.QRectF(0, y, w, h), h / 2.0, h / 2.0)
        d = h - 6
        kx = (w - d - 3) if self.isChecked() else 3
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QtCore.QRectF(kx, y + 3, d, d))
        p.setPen(QColor("#1a1612"))
        p.drawText(QtCore.QRectF(w + 10, 0, self.width() - w - 10, self.height()),
                   QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, self.text())
        p.end()


class AtlasGeoHandlerDemoDialog(QtWidgets.QDialog, FORM_CLASS):
    """Multi-screen mission dialog."""

    progress_updated    = QtCore.pyqtSignal(int, int)
    processing_complete = QtCore.pyqtSignal()
    processing_failed   = QtCore.pyqtSignal(str)
    layer_ready_signal  = QtCore.pyqtSignal(str, float, float)  # FIX: pass coords too
    processing_cancelled = QtCore.pyqtSignal()   # user cancelled the batch mid-run

    PAGE_GETSTARTED     = 0
    PAGE_SIGNIN         = 1
    PAGE_SIGNUP         = 2
    PAGE_RESET_PASSWORD = 3
    PAGE_SETUP          = 4
    PAGE_PROCESSING     = 5
    PAGE_RESULTS        = 6

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.setupUi(self)
        self._load_header_logo()
        self._wrap_content_scrollable()   # let the dialog fit small/scaled screens

        if not RASTERIO_AVAILABLE:
            QtWidgets.QMessageBox.warning(
                self, "Missing dependency: rasterio",
                "The 'rasterio' Python package is required for georeferencing and "
                "report output, but it isn't installed in QGIS's Python.\n\n"
                "Install it (see INSTALL.md), then restart QGIS. From "
                "Plugins → Python Console:\n\n"
                "    import subprocess, sys\n"
                "    subprocess.check_call([sys.executable, '-m', 'pip', "
                "'install', 'rasterio'])",
            )

        self.iface = iface
        self.selected_file: str | None = None
        self.selected_files: list[str] = []
        self._processing_thread: threading.Thread | None = None
        self._cancel_flag = threading.Event()
        # Recent-Uploads panel scales to large batches: a name->row index for O(1)
        # status updates, and a debounce timer so thousands of updates coalesce into
        # ≤1 rebuild per ~300ms instead of rebuilding the whole panel per update.
        self._upload_row_by_name: dict = {}
        self._uploads_refresh_timer = QtCore.QTimer(self)
        self._uploads_refresh_timer.setSingleShot(True)
        self._uploads_refresh_timer.timeout.connect(self._rebuild_recent_uploads)
        self._processing_active = False   # True while a batch is processing
        self._was_cancelled = False       # True if the current batch was user-cancelled
        self._batch_id = None             # set from the /register response (for /cancel)
        self._batch_total = 0             # original batch size (for the cancel summary)

        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.current_user_email: str | None = None

        # Store the GPS coords used for the job so we can georeference the result
        self._job_lat: float = 0.0
        self._job_lon: float = 0.0
        self._current_job_id: str | None = None
        self._processing_start_time: float | None = None
        self._result_paths: list[str] = []

        # Real per-job results captured from the backend poll response.
        # Keyed by job_id → {angle, num_inliers, elapsed_s, lat, lon, status}
        self._job_results: dict = {}
        # Per-job failures (low match quality / could not georeference).
        # Keyed by job_id → {file, reason}. A failed image NO LONGER aborts the
        # whole batch — the good results still complete; failures are summarised.
        self._failed_jobs: dict = {}
        # Per-file upload state for the Recent Uploads panel (filename, size, status)
        self._upload_rows: list = []
        # Jobs that SUCCEEDED on the backend but whose result could not be written
        # to this machine. Kept apart from _failed_jobs on purpose: the work was
        # delivered and charged, so it is not a processing failure and must not be
        # reported as one. Keyed job_id -> {file, path, error}.
        self._local_save_failures: dict = {}


        self._step_labels = [
            self.label_status_upload,
            self.label_status_analyze,
            self.label_status_match,
            self.label_status_prepare,
        ]

        self._processing_start_time = None
        self._reset_step_labels()
        self._rebuild_recent_uploads()   # clear the static demo rows on load
        self._restyle_getstarted_page()
        self._restyle_signin_page()
        self._restyle_signup_page()
        self._restyle_reset_page()
        self._wire_signals()
        # Auto sign-in if the user previously chose Remember Me
        if self._try_auto_signin():
            self._on_signin_success(self.current_user_email or "", True)

    # ─────────────────────────────────────────────────────────
    # HEADER LOGO
    # ─────────────────────────────────────────────────────────
    def _load_header_logo(self):
        logo_path = os.path.join(os.path.dirname(__file__), "aive_logo.png")
        if not os.path.exists(logo_path):
            return
        pixmap = QPixmap(logo_path)
        if not pixmap.isNull():
            scaled = pixmap.scaledToHeight(24, QtCore.Qt.SmoothTransformation)
            self.label_aive_logo.setPixmap(scaled)
            self.label_aive_logo.setFixedSize(scaled.width(), 24)

    def _plugin_version(self):
        """Version from metadata.txt, which is the one QGIS installs and shows.

        Hardcoded here it read "v1.0" while metadata.txt said 1.1.0, so the
        window disagreed with the plugin manager about which build was running.
        A version string in two places is a version string that goes stale.
        """
        try:
            path = os.path.join(os.path.dirname(__file__), "metadata.txt")
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip().startswith("version="):
                        return line.split("=", 1)[1].strip()
        except Exception:
            pass
        return "?"

    def _update_header_status(self):
        """Right-side header chip. Always shows the version tag — signed in AND
        out. The signed-in identity lives in the profile pill (avatar + name), so we
        deliberately DON'T put the email here (it was redundant and forced the window
        wider); but the version tag is standard for a pro tool, so it stays in both
        states."""
        lbl = getattr(self, "label_header_status", None)
        if lbl is None:
            return
        lbl.setVisible(True)
        lbl.setText(f"v{self._plugin_version()}")
        lbl.setToolTip("")
        # Strip the .ui's pill box (border + fill read as a clickable button); a
        # version tag is purely informational, so mute it to plain low-contrast text
        # aligned with the ATLAS-GEO wordmark on the left.
        lbl.setStyleSheet(
            "background: transparent; border: none; color: #9a8f80;"
            " font-size: 10px; font-weight: 600; padding: 2px 4px;")

    # ─────────────────────────────────────────────────────────
    # RESPONSIVE SIZING (fit small / high-DPI screens)
    # ─────────────────────────────────────────────────────────
    def _wrap_content_scrollable(self):
        """Put the page stack inside a scroll area so the dialog can shrink to
        fit small or display-scaled screens without clipping controls. The top
        brand/header bar stays fixed; only the page content scrolls."""
        try:
            lay = self.verticalLayout_main
            idx = lay.indexOf(self.stacked_pages)
            if idx < 0:
                return
            scroll = QtWidgets.QScrollArea()
            scroll.setObjectName("content_scroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            # AlwaysOff prevents Windows from reserving a ~17px gutter on the right
            # even when the scrollbar isn't visible — that gutter was pushing all
            # page content left of center. Pages that need scroll (setup with many
            # fields) still scroll; the bar just overlays instead of shifting content.
            scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            scroll.setStyleSheet(
                "QScrollArea#content_scroll { border: none; background: transparent; }")
            # Collapse the (always-off) vertical scrollbar to 0px so Windows reserves
            # NO gutter — that gutter was widening the right margin and pushing content
            # off-center. Set on the scrollbar object DIRECTLY (not via a cascading
            # stylesheet) so nested scroll areas (Recent Uploads) keep their own bars.
            vbar = scroll.verticalScrollBar()
            vbar.setStyleSheet("QScrollBar:vertical { width: 0px; }")
            lay.removeWidget(self.stacked_pages)
            # Force the stacked widget to expand horizontally to fill the full
            # viewport width — without this, Windows may leave it at its .ui
            # minimumWidth, causing content to appear shifted to the right.
            self.stacked_pages.setSizePolicy(
                QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
            scroll.setWidget(self.stacked_pages)   # reparents; self.<name> access unchanged
            lay.insertWidget(idx, scroll)
        except Exception as e:
            print(f"content scroll wrap failed: {e}")

    def showEvent(self, e):
        """Clamp the dialog to the available screen on first show, so it never
        opens taller/wider than the monitor (fixes overflow on laptop screens
        with Windows display scaling)."""
        super().showEvent(e)
        if getattr(self, "_fitted", False):
            return
        self._fitted = True
        try:
            scr = self.screen() or QtWidgets.QApplication.primaryScreen()
            avail = scr.availableGeometry()
            # Form-centric plugin: a narrower window suits the auth/setup pages and
            # kills the big side margins. The wide 4-card Plans view is a separate
            # self-sizing dialog, so the main window doesn't need to fit it. Target
            # size settled at 685x810 (fits the Get Started trust bar + CTA without
            # clipping, without extra headroom); still clamped to the screen so it
            # never opens larger than the available display.
            target_w = min(685, int(avail.width()  * 0.95))
            target_h = min(810, int(avail.height() * 0.92))
            self.resize(max(620, target_w), max(460, target_h))
            self.setMaximumSize(avail.width(), avail.height())
            fg = self.frameGeometry()
            fg.moveCenter(avail.center())
            self.move(fg.topLeft())
        except Exception as ex:
            print(f"fit-to-screen failed: {ex}")

    # ─────────────────────────────────────────────────────────
    # SIGNAL WIRING
    # ─────────────────────────────────────────────────────────
    def _wire_signals(self):
        self.btn_get_started.clicked.connect(self._go_to_signin)

        self.btn_signin.clicked.connect(self._handle_signin)
        self.btn_signin_back.clicked.connect(self._go_to_getstarted)
        self.btn_signup.clicked.connect(self._go_to_signup)
        self.btn_forgot_password.clicked.connect(self._go_to_reset_password)

        self.btn_do_signup.clicked.connect(self._handle_signup)
        self.btn_signup_back.clicked.connect(self._go_to_signin)

        self.btn_do_reset.clicked.connect(self._handle_reset_password)
        self.btn_reset_back.clicked.connect(self._go_to_signin)
        self.btn_send_reset_code.clicked.connect(self._handle_send_reset_code)

        self.btn_browse_setup.clicked.connect(self.browse_file)
        # The whole dashed zone is clickable (label + frame); button sits inside it.
        self.label_drop_zone.mousePressEvent = lambda _e: self.browse_file()
        if hasattr(self, "frame_drop_zone"):
            self.frame_drop_zone.mousePressEvent = lambda _e: self.browse_file()
            self.frame_drop_zone.setAttribute(QtCore.Qt.WA_Hover, True)
        self.btn_next_setup.clicked.connect(self._go_to_processing)
        self.btn_next_setup.setEnabled(False)   # disabled until a file is selected
        self.btn_logout_setup.clicked.connect(self._handle_logout)
        self.btn_feedback.clicked.connect(self._open_feedback_dialog)

        self.btn_cancel_processing.clicked.connect(self._cancel_processing)
        self.progress_updated.connect(self._on_progress_updated)
        self.processing_complete.connect(self._on_processing_complete)
        self.processing_failed.connect(self._on_processing_failed)
        self.processing_cancelled.connect(self._on_processing_cancelled)
        # FIX: slot signature updated to receive lat/lon for georeferencing
        self.layer_ready_signal.connect(self._add_warped_layer_to_qgis)

        self.btn_view_map.clicked.connect(self.view_on_map)
        self.btn_export_report.clicked.connect(self.export_report)
        self.btn_new_mission.clicked.connect(self._restart_mission)
        self.btn_logout_results.clicked.connect(self._handle_logout)
        self.btn_feedback_results.clicked.connect(self._open_feedback_dialog)

    # ─────────────────────────────────────────────────────────
    # NAVIGATION HELPERS
    # ─────────────────────────────────────────────────────────
    def _go_to_getstarted(self):
        self.stacked_pages.setCurrentIndex(self.PAGE_GETSTARTED)

    def _go_to_signin(self):
        self.stacked_pages.setCurrentIndex(self.PAGE_SIGNIN)
        self.input_email.clear()
        self.input_password.clear()
        self.input_email.setFocus()
        if hasattr(self, 'label_signin_error'):
            self.label_signin_error.setText("")
            self.label_signin_error.hide()
        if hasattr(self, 'label_signin_info'):
            self.label_signin_info.setText("")
            self.label_signin_info.hide()

    def _go_to_signup(self):
        self.stacked_pages.setCurrentIndex(self.PAGE_SIGNUP)
        self.input_signup_name.clear()
        self.input_signup_email.clear()
        self.input_signup_password.clear()
        self.input_signup_confirm.clear()
        # getattr rather than direct access: these two arrived after the .ui was
        # first shipped, and a stale .ui on someone's machine should not turn
        # "open the signup page" into an AttributeError.
        for _n in ("input_signup_jobtitle", "input_signup_org"):
            _w = getattr(self, _n, None)
            if _w is not None:
                _w.clear()
        # A combo is reset to its blank first entry, not cleared: clear() would
        # empty the country list itself and leave the next visitor nothing to pick.
        _c = getattr(self, "combo_signup_country", None)
        if _c is not None and _c.count():
            _c.setCurrentIndex(0)
        self.label_signup_error.setText("")
        self.label_signup_error.hide()
        # Fetched per visit rather than once per session: a document revised
        # while the plugin is open must not be missed, and a tick left over
        # from a previous visit must not be reused.
        chk = getattr(self, "_legal_check", None)
        if chk is not None:
            chk.setChecked(False)
        self._fetch_legal_config()

    def _go_to_reset_password(self):
        self.stacked_pages.setCurrentIndex(self.PAGE_RESET_PASSWORD)
        self.input_reset_email.clear()
        self.input_reset_code.clear()
        self.input_reset_new_password.clear()
        self.input_reset_confirm.clear()
        self.label_reset_error.setText("")
        self.label_reset_error.hide()
        # Reset any running resend countdown and rewind to Step 1.
        timer = getattr(self, "_resend_timer", None)
        if timer is not None:
            timer.stop()
        rb = getattr(self, "_reset_resend_btn", None)
        if rb is not None:
            rb.setEnabled(True)
            rb.setText("Resend code")
        if getattr(self, "_reset_styled", False):
            self._show_reset_step1()
        self.input_reset_email.setFocus()

    def _go_to_setup(self):
        self.stacked_pages.setCurrentIndex(self.PAGE_SETUP)
        if hasattr(self, "btn_next_setup"):
            has_files = bool(getattr(self, "selected_files", None))
            self.btn_next_setup.setEnabled(has_files)

    # ─────────────────────────────────────────────────────────
    # SIGN IN
    # ─────────────────────────────────────────────────────────
    def _handle_signin(self):
        email    = self.input_email.text().strip()
        password = self.input_password.text().strip()

        self.label_signin_error.setText("")
        self.label_signin_error.hide()

        if not email:
            self._show_signin_error("Enter your email address", "error")
            self.input_email.setFocus()
            return
        if not password:
            self._show_signin_error("Enter your password", "error")
            self.input_password.setFocus()
            return
        if not self._validate_email(email):
            self._show_signin_error("Enter a valid email address", "error")
            self.input_email.setFocus()
            self.input_email.selectAll()
            return
        if len(password) < 6:
            self._show_signin_error("Password must be at least 6 characters", "error")
            self.input_password.setFocus()
            self.input_password.selectAll()
            return

        self.btn_signin.setEnabled(False)
        self.btn_signin.setText("Signing in…")
        self._show_signin_error("Authenticating…", "info")
        threading.Thread(target=self._signin_thread, args=(email, password), daemon=True).start()

    def _signin_thread(self, email: str, password: str):
        try:
            response = requests.post(SIGNIN_URL, json={"email": email, "password": password}, timeout=15)
            if response.status_code == 200:
                data = response.json()
                self.access_token   = data.get("access_token")
                self.refresh_token  = data.get("refresh_token")
                self.current_user_email = email
                email_verified = data.get("email_verified", False)
                cb = getattr(self, "checkbox_remember", None)
                if cb is not None and cb.isChecked():
                    self._save_remembered_token()
                else:
                    self._clear_remembered_token()
                QtCore.QMetaObject.invokeMethod(self, "_on_signin_success", QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, email), QtCore.Q_ARG(bool, email_verified))
            elif response.status_code == 401:
                QtCore.QMetaObject.invokeMethod(self, "_on_signin_failed", QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, "Incorrect email or password."))
            elif response.status_code == 403:
                # 403 carries TWO different meanings on this endpoint. Assuming
                # "email not verified" would send a user refused on geography
                # into a verification loop they can never complete, because
                # there is nothing wrong with their email.
                _code = ""
                try:
                    _d = response.json().get("detail")
                    _code = _d.get("code", "") if isinstance(_d, dict) else ""
                except Exception:
                    _code = ""
                if _code == "COUNTRY_NOT_SUPPORTED":
                    QtCore.QMetaObject.invokeMethod(self, "_on_signin_failed",
                        QtCore.Qt.QueuedConnection,
                        QtCore.Q_ARG(str, server_message(
                            response, "ATLAS-GEO is not available in your region yet.")))
                else:
                    QtCore.QMetaObject.invokeMethod(self, "_on_email_not_verified",
                        QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, email))
            else:
                try:
                    detail = server_message(response, "Sign-in failed. Check your details and try again.")
                except Exception:
                    detail = response.text
                QtCore.QMetaObject.invokeMethod(self, "_on_signin_failed", QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, f"Sign-in failed: {detail}"))
        except requests.ConnectionError:
            QtCore.QMetaObject.invokeMethod(self, "_on_signin_failed", QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, "Can't connect. Check your internet and try again."))
        except requests.Timeout:
            QtCore.QMetaObject.invokeMethod(self, "_on_signin_failed", QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, "Request timed out. Try again."))

    @QtCore.pyqtSlot(str, bool)
    def _on_signin_success(self, email: str, email_verified: bool):
        self.btn_signin.setEnabled(True)
        self.btn_signin.setText("Sign In")
        # 200 means the user is authenticated; 403 handles unverified email separately.
        self.label_signin_error.setText("")
        self.label_signin_error.hide()
        self.iface.messageBar().pushMessage(
            "ATLAS", f"Welcome, {email.split('@')[0]}!", level=0, duration=3
        )
        self._clear_session_state()   # ensure a clean slate for THIS user (no carry-over)
        self._build_profile_menu()    # one-time: profile dropdown (declutters header)
        self._refresh_profile_identity()   # show THIS user's name/avatar (not the previous login's)
        self._apply_setup_layout()    # one-time: reorder config above Recent Uploads
        self._refresh_balance()       # fetch + display current token balance
        # Warm the tile session now, in the background, so View on Map does not
        # block the UI thread on a round trip before it can draw anything. Does
        # NOT create a layer: _load_basemap stays the only thing that does.
        self._prefetch_tile_session()
        self._go_to_setup()

    # ─────────────────────────────────────────────────────────
    # BILLING  (token balance + Stripe checkout)
    # ─────────────────────────────────────────────────────────
    def _build_billing_strip(self):
        """Add a compact balance + Buy/Plans strip to the top of the SETUP page (once)."""
        if getattr(self, "_billing_strip_built", False):
            return
        try:
            page = self.stacked_pages.widget(self.PAGE_SETUP)
            lay = page.layout()
            if lay is None:
                return
            strip = QtWidgets.QWidget()
            strip.setObjectName("billing_strip")
            strip.setStyleSheet(
                "QWidget#billing_strip {"
                "  background: qlineargradient(x1:0, y1:0, x2:1, y2:0,"
                "      stop:0 #ffffff, stop:1 #fffaf4);"
                "  border: 1px solid #ece4d8;"
                "  border-bottom: 2px solid #e3d6c4;"
                "  border-radius: 12px;"
                "}"
            )
            h = QtWidgets.QHBoxLayout(strip)
            h.setContentsMargins(14, 9, 12, 9)
            h.setSpacing(8)

            self.label_token_balance = QtWidgets.QLabel("🪙  …")
            self.label_token_balance.setStyleSheet(
                "QLabel {"
                "  background-color: #fff4ed;"
                "  color: #9a3412;"
                "  border: 1px solid #fed7aa;"
                "  border-radius: 13px;"
                "  padding: 6px 14px;"
                "  font-size: 12px;"
                "  font-weight: 700;"
                "  font-family: 'Segoe UI', sans-serif;"
                "}"
            )

            self.btn_view_plans = QtWidgets.QPushButton("View Plans")
            self.btn_view_plans.setMinimumHeight(34)
            self.btn_view_plans.setCursor(QtCore.Qt.PointingHandCursor)
            self.btn_view_plans.setStyleSheet(
                "QPushButton {"
                "  background-color: #ffffff;"
                "  color: #57534e;"
                "  border: 1px solid #e2ddd8;"
                "  border-radius: 6px;"
                "  padding: 8px 18px;"
                "  font-size: 13px;"
                "  font-weight: 600;"
                "}"
                "QPushButton:hover { background-color: #faf7f2; color: #1a1612; border-color: #ccbda8; }"
                "QPushButton:pressed { background-color: #f3ece3; }"
            )

            self.btn_buy_tokens = QtWidgets.QPushButton("＋  Buy Credits")
            self.btn_buy_tokens.setMinimumHeight(34)
            self.btn_buy_tokens.setCursor(QtCore.Qt.PointingHandCursor)
            self.btn_buy_tokens.setStyleSheet(
                "QPushButton {"
                "  background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                "      stop:0 #f4691c, stop:1 #ea580c);"
                "  color: #ffffff;"
                "  border: none;"
                "  border-bottom: 2px solid #c2410c;"
                "  border-radius: 6px;"
                "  padding: 8px 18px;"
                "  font-size: 13px;"
                "  font-weight: 700;"
                "}"
                "QPushButton:hover {"
                "  background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
                "      stop:0 #ea580c, stop:1 #d2480a);"
                "  border-bottom-color: #9a3412;"
                "}"
                "QPushButton:pressed { background: #c2410c; border-bottom: none; padding-top: 10px; }"
            )

            h.addWidget(self.label_token_balance)
            h.addStretch(1)
            h.addWidget(self.btn_view_plans)
            # This method currently has NO call site (_build_profile_menu replaced
            # the strip), but a dead widget that offers a purchase is exactly the
            # kind of thing that comes back to life in a later refactor.
            self.btn_buy_tokens.setVisible(SHOW_PAYG)
            h.addWidget(self.btn_buy_tokens)
            if hasattr(lay, "insertWidget"):
                lay.insertWidget(0, strip)
            else:
                lay.addWidget(strip)
            self.btn_buy_tokens.clicked.connect(self._buy_tokens)
            self.btn_view_plans.clicked.connect(self._show_plans)
            self._billing_strip_built = True
        except Exception as e:
            print(f"billing strip build failed: {e}")

    def _find_header_hbox(self):
        """Return the setup-page header QHBoxLayout (the row holding the step pill)."""
        return self._find_layout_containing(getattr(self, "label_step_indicator", None))

    def _find_layout_containing(self, widget, root=None):
        """Return the top-level sub-layout (within `root`, default the setup page)
        that holds `widget`, or None."""
        lay = root if root is not None else getattr(self, "verticalLayout_setup", None)
        if lay is None or widget is None:
            return None
        for i in range(lay.count()):
            sub = lay.itemAt(i).layout()
            if sub is None:
                continue
            for j in range(sub.count()):
                if sub.itemAt(j).widget() is widget:
                    return sub
        return None

    def _add_trial_menu_row(self, menu):
        """Insert the trial entry as a colourable row and return (action, row).

        Sits directly under View Plans / Buy Credits: it belongs with the other
        ways of getting capacity, not down among storage and account actions.
        """
        row = MenuRow()
        row.set_row("Request a free trial", chevron=False)
        act = QtWidgets.QWidgetAction(menu)
        act.setDefaultWidget(row)
        menu.addAction(act)
        # A QWidgetAction does not dismiss its menu on click the way a QAction
        # does, so close it explicitly before opening the dialog.
        row.clicked.connect(lambda: (menu.close(), self._open_trial_request_dialog()))
        # Hidden under the current service model. The dialog, the endpoints and
        # the backend plan are all left intact, so restoring it is a flag
        # flip rather than a rebuild.
        #
        # The tier test alone is not enough: a new account is now 'free', so the
        # row would hide by coincidence while every legacy 'payg' account still
        # saw it.
        visible = (SHOW_TRIAL_REQUEST
                   and (getattr(self, "_current_tier", None) or "payg").lower() == "payg")
        act.setVisible(visible)
        # HIDE THE WIDGET TOO, not just the action. A QWidgetAction that is not
        # visible reserves no space in the menu, but its default widget can still
        # paint — so the hidden row rendered ON TOP of the Storage caption below
        # it. Hiding only the action is what produced that overlap.
        row.setVisible(visible)
        return act, row

    def _make_profile_controls(self):
        """Create a balance chip + profile dropdown (View Plans / Buy Credits /
        Manage billing / Feedback / Log out). Returns (chip, profile_button); the
        caller adds them to a header layout. Used by the Results header (the Setup
        header builds its own equivalent in _build_profile_menu)."""
        menu = QtWidgets.QMenu(self)
        try:
            menu.setWindowFlag(QtCore.Qt.NoDropShadowWindowHint, True)
        except Exception:
            pass
        menu.setStyleSheet(
            "QMenu { background:#ffffff; border:1px solid #d1d5db; border-radius:2px; padding:4px; }"
            "QMenu::item { padding:8px 22px 8px 12px; border-radius:2px; font-size:12px; color:#44403c; }"
            "QMenu::item:selected { background:#f3f4f6; color:#1a1612; }"
            "QMenu::item:disabled { color:#b8b2ab; }"          # muted, intentional-looking
            "QMenu::item:selected:disabled { background:transparent; }"  # no hover on disabled
            "QMenu::separator { height:1px; background:#e5e7eb; margin:4px 6px; }")
        menu.addAction("View Plans").triggered.connect(self._show_plans)
        # Free-only launch: no purchase entry point anywhere.
        if SHOW_PAYG:
            menu.addAction("Buy Credits").triggered.connect(self._buy_tokens)
        # Only offered to users who could actually receive a trial: anyone
        # already on a trial or a paid plan would just be told "you're on X".
        self._trial_request_action, self._trial_request_row =             self._add_trial_menu_row(menu)
        menu.addSeparator()
        # Storage status caption (heading) + the greyable action to free space.
        self._results_storage_caption = self._make_storage_caption(menu)
        self._results_freeup_action = menu.addAction("Free up storage")
        self._results_freeup_action.triggered.connect(self._free_up_storage)
        self._results_freeup_action.setEnabled(False)   # enabled only when over quota
        self._results_freeup_action.setToolTip("Only available when you're over your storage limit")
        self._results_migrate_action = menu.addAction("Move my files to the team")
        self._results_migrate_action.triggered.connect(self._migrate_files_on_demand)
        self._results_migrate_action.setVisible(False)   # shown only for org members with personal files
        menu.addSeparator()
        # Hidden with PAYG. The Stripe portal only exists for someone who has
        # bought something; on a Free-only launch nobody has, so this item would
        # be a guaranteed dead end ("No billing account yet. Buy credits or
        # subscribe first.") pointing at a purchase we no longer offer.
        if SHOW_PAYG or SHOW_SUBSCRIPTION_TIERS:
            menu.addAction("Manage billing && saved card").triggered.connect(lambda: self._open_portal())
        menu.addAction("Imagery requirements").triggered.connect(
            self._show_imagery_requirements)
        menu.addAction("Feedback").triggered.connect(self._open_feedback_dialog)
        menu.addSeparator()
        menu.addAction("Log out").triggered.connect(self._handle_logout)

        chip = QtWidgets.QPushButton("🪙  …")
        chip.setCursor(QtCore.Qt.PointingHandCursor)
        chip.setToolTip("View plans & buy credits" if SHOW_PAYG else "View your plan")
        chip.setStyleSheet(
            "QPushButton { background:#fff4ed; color:#9a3412; border:1px solid #fed7aa;"
            "  border-radius:14px; padding:6px 14px; font-size:12px; font-weight:700; }"
            "QPushButton:hover { background:#ffe9db; border-color:#fdba74; }")
        chip.clicked.connect(self._show_plans)

        from qgis.PyQt.QtGui import QIcon
        who = (self.current_user_email or "Account").split("@")[0]
        prof = QtWidgets.QToolButton()
        prof.setIcon(QIcon(self._avatar_pixmap((self.current_user_email or "U")[0])))
        prof.setIconSize(QtCore.QSize(22, 22))
        prof.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        prof.setText(f"{who}  ▾")
        prof.setCursor(QtCore.Qt.PointingHandCursor)
        prof.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        prof.setMenu(menu)
        prof.setStyleSheet(
            "QToolButton { background:#ffffff; border:1px solid #e2ddd8; border-radius:16px;"
            "  padding:5px 12px 5px 5px; font-size:12px; font-weight:600; color:#57534e; }"
            "QToolButton:hover { border-color:#ccbda8; color:#1a1612; }"
            "QToolButton::menu-indicator { image:none; width:0px; }")
        return chip, prof

    def _svg_icon(self, name, size=26, color="#ea580c"):
        """Crisp single-family line icon rendered from inline SVG (Lucide-style) via
        QtSvg — one stroke weight, one color, sharp at any size, no external files.
        Raises if QtSvg is unavailable so callers can fall back to text/emoji."""
        from qgis.PyQt.QtSvg import QSvgRenderer
        from qgis.PyQt.QtGui import QPixmap, QPainter
        paths = {
            # crosshair / locate — "georeferencing = precise positioning"
            "target": '<path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>'
                      '<circle cx="12" cy="12" r="7"/>'
                      '<circle cx="12" cy="12" r="1.7" fill="{c}" stroke="none"/>',
            # stacked layers — "QGIS map layers"
            "layers": '<path d="M12 2 2 7l10 5 10-5-10-5Z"/>'
                      '<path d="m2 12 10 5 10-5"/><path d="m2 17 10 5 10-5"/>',
            # cloud — "cloud sync"
            "cloud":  '<path d="M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z"/>',
            # folder — "Select Folder"
            "folder": '<path d="M3 7a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.6.8L11.8 7H19a2 2 0 '
                      '0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/>',
            # image — "Select Files" (picking images)
            "image":  '<rect x="3" y="3" width="18" height="18" rx="2"/>'
                      '<circle cx="9" cy="9" r="2"/><path d="m21 15-4.5-4.5L6 21"/>',
        }
        inner = paths[name].format(c=color)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
               f'fill="none" stroke="{color}" stroke-width="2" '
               f'stroke-linecap="round" stroke-linejoin="round">{inner}</svg>')
        renderer = QSvgRenderer(QtCore.QByteArray(svg.encode("utf-8")))
        if not renderer.isValid():
            raise ValueError(f"SVG failed to load for icon '{name}'")
        pm = QPixmap(size, size)
        pm.fill(QtCore.Qt.transparent)
        p = QPainter(pm)
        renderer.render(p)
        p.end()
        return pm

    def _avatar_pixmap(self, initial, size=22):
        """A small circular orange avatar with a white initial (profile button icon)."""
        from qgis.PyQt.QtGui import QPixmap, QPainter, QFont
        pm = QPixmap(size, size)
        pm.fill(QtCore.Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QColor("#ea580c"))
        p.drawEllipse(0, 0, size, size)
        p.setPen(QColor("#ffffff"))
        f = QFont(); f.setBold(True); f.setPointSize(max(7, int(size * 0.42)))
        p.setFont(f)
        p.drawText(pm.rect(), QtCore.Qt.AlignCenter, (initial or "U")[:1].upper())
        p.end()
        return pm

    def _refresh_profile_identity(self):
        """Update the name + avatar on the (one-time-built) profile buttons to the
        CURRENT user. Fixes the stale name after logout → login as a different user,
        since _build_profile_menu / _style_results_page only run once."""
        from qgis.PyQt.QtGui import QIcon
        who = (self.current_user_email or "Account").split("@")[0]
        initial = (self.current_user_email or "U")[0]
        icon = QIcon(self._avatar_pixmap(initial))
        for attr in ("_profile_btn", "_results_profile_btn"):
            btn = getattr(self, attr, None)
            if btn is not None:
                btn.setText(f"{who}  ▾")
                btn.setIcon(icon)
        self._update_header_status()   # header chip -> signed-in email

    def _style_results_page(self):
        """One-time Results-page polish to match the rest: de-boxed sections + borderless
        grey headers, a profile dropdown in the header (Feedback/Logout move there), and
        standard-sized sharp buttons instead of full-width slabs."""
        if getattr(self, "_results_styled", False):
            return
        from qgis.PyQt.QtGui import QFont
        vlr = getattr(self, "verticalLayout_results", None)

        # De-box NEXT ACTIONS + borderless grey header.
        gr = getattr(self, "groupbox_results", None)
        if gr is not None:
            gr.setTitle("NEXT ACTIONS")
            f = gr.font(); f.setBold(True); f.setPointSize(8)
            try:
                f.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
            except Exception:
                pass
            gr.setFont(f)
            gr.setStyleSheet(
                "QGroupBox { border:none; background:transparent; margin-top:12px;"
                "  font-size:9px; color:#9ca3af; }"
                "QGroupBox::title { subcontrol-origin:margin; subcontrol-position:top left;"
                "  left:0px; top:0px; padding:0 0 10px 0; background:transparent; color:#9ca3af; }")

        # Header: de-box the step pill, hide the Geo-Located badge, add the profile dropdown.
        si = getattr(self, "label_step_indicator_3", None)
        if si is not None:
            si.setStyleSheet("font-size:11px; font-weight:800; letter-spacing:1px;"
                             " color:#9ca3af; background:transparent; border:none;")
        badge = getattr(self, "label_geo_located_badge", None)
        if badge is not None:
            badge.hide()
        header = self._find_layout_containing(si, root=vlr) if si is not None else None
        if header is not None:
            chip, prof = self._make_profile_controls()
            header.addWidget(chip); header.addWidget(prof)
            self._results_balance_chip = chip   # kept fresh by _set_balance_ui
            self._results_profile_btn = prof    # name/avatar refreshed on each login

        # Feedback + Logout now live in the dropdown -> hide the full-width slabs.
        for b in ("btn_feedback_results", "btn_logout_results"):
            w = getattr(self, b, None)
            if w is not None:
                w.hide()

        # Buttons -> standard, sharp (no full-width slabs).
        vm = getattr(self, "btn_view_map", None)
        if vm is not None:
            vm.setMinimumHeight(42)
            vm.setStyleSheet(
                "QPushButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                "    stop:0 #14c98e, stop:1 #10b981); color:#ffffff; border:none;"
                "  border-bottom:3px solid #059669; border-radius:6px; padding:11px;"
                "  font-size:13px; font-weight:800; }"
                "QPushButton:hover { background:#10b981; }"
                "QPushButton:pressed { background:#059669; border-bottom:none; padding-top:13px; }")
        for b in ("btn_export_report", "btn_new_mission"):
            w = getattr(self, b, None)
            if w is not None:
                w.setMinimumHeight(38)
                w.setStyleSheet(
                    "QPushButton { background:#ffffff; color:#57534e; border:1px solid #e2ddd8;"
                    "  border-radius:6px; padding:9px 16px; font-size:12px; font-weight:600; }"
                    "QPushButton:hover { background:#faf7f2; color:#1a1612; border-color:#ccbda8; }")

        self._results_styled = True

    def _find_item_layout(self, target, root):
        """Find the (layout, index) that holds `target` anywhere under `root`'s
        layout tree (depth-first). Returns (None, -1) if not found."""
        if root is None or target is None:
            return None, -1
        stack = []
        if root.layout() is not None:
            stack.append(root.layout())
        while stack:
            lay = stack.pop()
            for i in range(lay.count()):
                it = lay.itemAt(i)
                if it.widget() is target:
                    return lay, i
                sub = it.layout()
                if sub is not None:
                    stack.append(sub)
        return None, -1

    def _restyle_getstarted_page(self):
        """One-time landing-page polish: the hero ate ~half the screen (huge title +
        three oversized spacers + a two-paragraph blurb), pushing the value below the
        fold. Tighten the hero, collapse the spacers, swap the blurb for a one-line
        value prop, give the feature tiles a real hierarchy (title → benefit → tech
        tag), and right-size the CTA. Pure runtime restyle — preserves widgets."""
        if getattr(self, "_getstarted_styled", False):
            return
        lay = getattr(self, "verticalLayout_getstarted", None)
        if lay is not None:
            lay.setContentsMargins(32, 12, 16, 12)
            lay.setSpacing(18)   # breathing room between hero elements
            # The .ui's three spacers default to EXPANDING (no sizeType) — that's
            # what ate ~half the page. Remove them entirely, then center the now-
            # compact hero with one stretch top + bottom, so the leftover space is
            # balanced margins (intentional) rather than a single big void.
            for i in range(lay.count() - 1, -1, -1):
                if lay.itemAt(i).spacerItem() is not None:
                    lay.takeAt(i)
            lay.insertStretch(0, 1)
            lay.addStretch(1)
            lay.invalidate()

        t = getattr(self, "label_getstarted_title", None)
        if t is not None:   # 38px -> 28px: prominent but no longer dominating
            t.setStyleSheet("font-size:28px; font-weight:800; color:#1a1612;"
                            " letter-spacing:1px; background:transparent;")

        gs = getattr(self, "label_gs_brand", None)
        if gs is not None:
            gs.setText("UAV  GEOREFERENCING")

        d = getattr(self, "label_getstarted_description", None)
        if d is not None:   # value-first, one line (was two dense paragraphs)
            d.setText("Transform UAV imagery into accurately georeferenced map "
                      "layers in seconds.")
            d.setStyleSheet("color:#57534e; font-size:12px; background:transparent;")

        # Trust bar: credibility chips inserted between the description and the tiles.
        if lay is not None and not getattr(self, "_trust_bar_built", False):
            # ⚠️ WIDTH IS A CONSTRAINT HERE, not a detail. These four chips sit
            # on ONE row inside a 685px window, and the row sets a minimum width
            # on the whole page: make them longer and the layout does not wrap,
            # it overflows, clipping the last chip AND pushing the third feature
            # tile off the right edge. That is what happened when the shorter
            # encryption claim was replaced with a longer, more accurate one.
            # Keep the total under 92 characters; a test enforces it.
            chips = [
                ("⚡", "Automated in seconds"),
                ("🛰", "Satellite reference matching"),
                ("🔒", "Encrypted in transit"),
                ("⚙", "No ground control"),
            ]
            trust_row = QtWidgets.QHBoxLayout()
            trust_row.setSpacing(8)
            trust_row.addStretch(1)
            for icon, label in chips:
                chip = QtWidgets.QLabel(f"{icon}  {label}")
                chip.setStyleSheet(
                    "QLabel { background:#ffffff; color:#57534e;"
                    "  border:1px solid #e8e2d8; border-radius:12px;"
                    "  padding:4px 10px; font-size:10px; font-weight:600; }")
                trust_row.addWidget(chip)
            trust_row.addStretch(1)
            # Insert after the description widget
            d_idx = lay.indexOf(d) if d is not None else -1
            lay.insertLayout(d_idx + 1 if d_idx >= 0 else lay.count(), trust_row)
            self._trust_bar_built = True

        # Feature tiles: icon → bold title → one-line benefit → faint tech tag.
        # Deliberately does not name the model. Accurate either way, does not go
        # stale the next time the matcher changes, and does not publish the
        # server-side stack to everyone who downloads the plugin.
        tech_tags = {
            "tile_feat1": "Dense neural image matching",
            "tile_feat2": "Direct canvas layer load",
            "tile_feat3": "Stored in your account",
        }
        benefits = {
            "tile_feat1": "Position UAV imagery automatically in seconds.",
            "tile_feat2": "Load results straight onto your map canvas.",
            "tile_feat3": "Results saved to your account, ready to download.",
        }
        for name, benefit in benefits.items():
            tile = getattr(self, name, None)
            if tile is None:
                continue
            tlay = tile.layout()
            if tlay is None or tlay.count() < 3:
                continue
            # Keep a real card, but lighter: drop the heavy 2px bottom-border that
            # made it read as a form field — a single 1px border + radius = clean card.
            tile.setStyleSheet(
                "QFrame { background-color: #ffffff; border: 1px solid #ece4d8;"
                "  border-radius: 12px; }"
                " QLabel { background: transparent; border: none; }")
            # Swap the mismatched color emoji (⚡ 🗺️ ☁️) for one drawn icon family
            # — same orange, same size, same weight. Falls back to the emoji on any
            # draw error so the tile is never left blank.
            icon_lbl = tlay.itemAt(0).widget()
            kind = {"tile_feat1": "target", "tile_feat2": "layers",
                    "tile_feat3": "cloud"}.get(name)
            if icon_lbl is not None and kind:
                try:
                    icon_lbl.setText("")
                    icon_lbl.setPixmap(self._svg_icon(kind, size=26))
                except Exception:
                    pass   # QtSvg unavailable → keep the original emoji
            title_lbl = tlay.itemAt(1).widget()   # bold title
            tag_lbl   = tlay.itemAt(2).widget()   # tech tag, text set below
            # ⚠️ SET THE TAG TEXT HERE, do not inherit it from the .ui.
            #
            # It used to come from the .ui alone, and the .ui named a matcher
            # production had already moved off. Worse, a plugin directory where
            # the .py updated and the .ui did not showed the corrected chips
            # beside the stale claim, so the screen advertised the wrong engine
            # while looking freshly built. One source, beside the benefit copy.
            if tag_lbl is not None:
                tag_lbl.setText(tech_tags.get(name, ""))
            if title_lbl is not None:
                title_lbl.setStyleSheet("font-size:12px; font-weight:700; color:#1a1612;"
                                        " background:transparent;")
            if tag_lbl is not None:
                tag_lbl.setStyleSheet("font-size:9px; color:#a8a29e; background:transparent;")
            ben = QtWidgets.QLabel(benefit)
            ben.setWordWrap(True)
            ben.setStyleSheet("font-size:11px; color:#57534e; background:transparent;")
            tlay.insertWidget(2, ben)   # between the title and the tech tag

        # Equalize the three tiles: with no explicit stretch/height they size to
        # their own content, and the benefit lines differ in length, so the tiles
        # came out visibly mismatched widths/heights (client feedback: "resize
        # boxes for UI consistency"). Force equal width via stretch, equal height
        # via a shared minimum matched to the tallest tile's natural content.
        tiles = [t for t in (getattr(self, n, None) for n in benefits) if t is not None]
        if tiles:
            row = self._find_layout_containing(tiles[0], root=lay)
            if row is not None:
                for i in range(row.count()):
                    row.setStretch(i, 1)
            for t in tiles:
                t.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            tallest = max(t.sizeHint().height() for t in tiles)
            for t in tiles:
                t.setMinimumHeight(tallest)

        b = getattr(self, "btn_get_started", None)
        if b is not None:
            b.setMinimumHeight(44)
            b.setStyleSheet(
                "QPushButton { background:#ea580c; color:#ffffff; border:none;"
                "  border-bottom:3px solid #c2410c; border-radius:6px; padding:13px;"
                "  font-size:14px; font-weight:800; }"
                "QPushButton:hover { background:#d2480a; }"
                "QPushButton:pressed { background:#c2410c; border-bottom:none; padding-top:15px; }")

        self._getstarted_styled = True

    def _restyle_signin_page(self):
        """One-time sign-in redesign (approved mockup): drop the big branding hero,
        left-align/tighten the title, de-box the form, clean inputs, Remember-me as a
        toggle, sharp Sign In, secondary Create Account, small text-link Back. Pure
        runtime restyle — preserves every wired widget; reversible."""
        if getattr(self, "_signin_styled", False):
            return
        hero = getattr(self, "frame_signin_hero", None)
        if hero is not None:
            hero.hide()   # dark header bar already brands it
        t = getattr(self, "label_signin_title", None)
        if t is not None:
            t.setStyleSheet("font-size:22px; font-weight:800; color:#1a1612; background:transparent;")
        st = getattr(self, "label_signin_subtitle", None)
        if st is not None:
            st.setText("Sign in to your ATLAS account.")
            st.setStyleSheet("font-size:12px; color:#78716c; background:transparent;")
        form = getattr(self, "frame_signin_form", None)
        if form is not None:
            form.setStyleSheet("QFrame#frame_signin_form { background:transparent; border:none; }"
                               " QLabel { background:transparent; border:none; }")
        for n in ("input_email", "input_password"):
            w = getattr(self, n, None)
            if w is not None:
                w.setMinimumHeight(44)
                w.setStyleSheet("QLineEdit { border:1px solid #e2ddd8; border-radius:6px;"
                                "  padding:11px 13px; font-size:13px; color:#1a1612; background:#ffffff; }"
                                "QLineEdit:focus { border-color:#ea580c; }")
        # Remember me -> toggle (matches Setup's Auto-load control).
        cb = getattr(self, "checkbox_remember", None)
        if cb is not None:
            lay, idx = self._find_item_layout(cb, form or self)
            if lay is not None:
                tog = ToggleSwitch()
                tog.setText(cb.text())
                tog.setChecked(cb.isChecked())
                lay.removeWidget(cb); cb.hide(); cb.setParent(None)
                lay.insertWidget(idx, tog)
                self.checkbox_remember = tog
        bs = getattr(self, "btn_signin", None)
        if bs is not None:
            bs.setStyleSheet(
                "QPushButton { background:#ea580c; color:#ffffff; border:none;"
                "  border-bottom:3px solid #c2410c; border-radius:6px; padding:13px;"
                "  font-size:14px; font-weight:800; }"
                "QPushButton:hover { background:#d2480a; }"
                "QPushButton:pressed { background:#c2410c; border-bottom:none; padding-top:15px; }"
                "QPushButton:disabled { background:#f4a877; }")
        bu = getattr(self, "btn_signup", None)
        if bu is not None:
            # Inline text link (sits next to the prompt, centered under Sign In) —
            # not a second competing button.
            bu.setCursor(QtCore.Qt.PointingHandCursor)
            bu.setStyleSheet(
                "QPushButton { background:transparent; color:#ea580c; border:none;"
                "  font-size:11px; font-weight:700; padding:2px; }"
                "QPushButton:hover { color:#c2410c; text-decoration:underline; }")
        bb = getattr(self, "btn_signin_back", None)
        if bb is not None:
            bb.setText("← Back")
            bb.setMinimumHeight(28)
            bb.setStyleSheet(
                "QPushButton { background:transparent; color:#78716c; border:none;"
                "  font-size:12px; font-weight:600; padding:6px; }"
                "QPushButton:hover { color:#1a1612; }")

        # ── Layout polish (wrapped: this runs at construction; never break init) ──
        try:
            # Hide the faint "──  or  ──" divider: with only two actions it's noise.
            dl = getattr(self, "frame_divider_l", None)
            page = getattr(self, "page_signin", None)
            if dl is not None and page is not None:
                dlay, _ = self._find_item_layout(dl, page)
                if dlay is not None:
                    for j in range(dlay.count()):
                        w = dlay.itemAt(j).widget()
                        if w is not None:
                            w.hide()

            # Create-account: pull the prompt + link together and center them under
            # Sign In (was label hard-left + button hard-right — eye travels across).
            aa = getattr(self, "vl_account_actions", None)
            if aa is not None:
                for j in range(aa.count() - 1, -1, -1):
                    if aa.itemAt(j).spacerItem() is not None:
                        aa.takeAt(j)
                aa.setSpacing(6)
                aa.insertStretch(0, 1)
                aa.addStretch(1)

            # Center the form vertically: drop the expanding spacer that pushed the
            # back link to the page bottom, then balance with a stretch top + bottom.
            vlay = getattr(self, "verticalLayout_signin", None)
            if vlay is not None:
                vlay.setContentsMargins(32, 12, 16, 12)
                for i in range(vlay.count() - 1, -1, -1):
                    sp = vlay.itemAt(i).spacerItem()
                    if sp is not None and (sp.expandingDirections() & QtCore.Qt.Vertical):
                        vlay.takeAt(i)
                vlay.insertStretch(0, 1)
                vlay.addStretch(1)
                vlay.invalidate()
        except Exception as e:
            print(f"signin layout polish failed: {e}")

        self._signin_styled = True

    def _restyle_signup_page(self):
        """One-time Create-Account redesign (ATLAS industrial style — matches Sign In):
        de-boxed, left-aligned, full-width (NO floating card), tight field grouping,
        clean white inputs with an orange focus, a live password-strength meter, a
        disabled-until-valid primary, and a text-link Back. Pure runtime restyle."""
        if getattr(self, "_signup_styled", False):
            return
        t = getattr(self, "label_signup_title", None)
        if t is not None:
            t.setStyleSheet("font-size:22px; font-weight:800; color:#1a1612; background:transparent;")
        st = getattr(self, "label_signup_subtitle", None)
        if st is not None:
            st.setText("Register for ATLAS access.")
            st.setStyleSheet("font-size:12px; color:#78716c; background:transparent;")
        gb = getattr(self, "groupbox_signup", None)
        if gb is not None:
            gb.setTitle("")   # drop the "ACCOUNT DETAILS" tab label
            gb.setContentsMargins(0, 0, 0, 0)
            gb.setStyleSheet("QGroupBox { border:none; background:transparent; margin-top:0px;"
                             "  padding:0px; }"
                             " QLabel { background:transparent; border:none; }")
        # Tight grouping: each label hugs its input (6px), and fields are separated by
        # ~14px via a top margin on every label after the first.
        form = getattr(self, "vl_signup_form", None)
        if form is not None:
            form.setSpacing(6)
            form.setContentsMargins(0, 0, 0, 0)
        fn = getattr(self, "label_sn_name", None)
        if fn is not None:
            fn.setStyleSheet("font-size:12px; font-weight:600; color:#44403c; background:transparent;")
        for ln in ("label_sn_email", "label_sn_password", "label_sn_confirm"):
            lw = getattr(self, ln, None)
            if lw is not None:
                lw.setStyleSheet("font-size:12px; font-weight:600; color:#44403c;"
                                 " background:transparent; margin-top:8px;")
        # Clean white inputs, orange focus, warm-grey hover (identical to Sign In so
        # the auth flow reads as one tool, not a generic web form).
        # Styling only. input_signup_jobtitle / _org are included so the two new
        # demographic fields match the rest of the form; they are deliberately
        # absent from the validation list below, because they are optional.
        for n in ("input_signup_name", "input_signup_email",
                  "input_signup_jobtitle", "input_signup_org",
                  "input_signup_password", "input_signup_confirm"):
            w = getattr(self, n, None)
            if w is not None:
                w.setMinimumHeight(40)
                w.setStyleSheet("QLineEdit { border:1px solid #e2ddd8; border-radius:6px;"
                                "  padding:10px 13px; font-size:13px; color:#1a1612; background:#ffffff; }"
                                "QLineEdit:hover { border-color:#ccbda8; }"
                                "QLineEdit:focus { border-color:#ea580c; }")

        # ── Optional country, populated once and styled to match the text inputs ──
        # Demographic only. This is NOT an eligibility check: the approved-country
        # list lives on the server and is never shipped to the plugin, so the full
        # ISO set is offered here and the server decides what it means.
        cc = getattr(self, "combo_signup_country", None)
        if cc is not None and cc.count() == 0:
            cc.addItem("Select your country", "")           # index 0 == not answered
            for _code, _name in COUNTRIES:
                cc.addItem(_name, _code)                    # label shown, code sent
            cc.setCurrentIndex(0)
            cc.setMinimumHeight(40)
            cc.setEditable(False)

            # ⚠️ Without these three the popup is 250 rows tall and Qt draws it
            # as a full-height panel across the QGIS window rather than a menu
            # under the field. setMaxVisibleItems alone is ignored on styled
            # combos: it only takes effect with a non-native popup, which is what
            # setItemView + the AA_ hint below give us.
            cc.setMaxVisibleItems(12)
            cc.setView(QtWidgets.QListView())
            cc.view().setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
            cc.view().setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
            # Uniform row heights let the view size itself from maxVisibleItems
            # instead of measuring all 250 rows.
            cc.view().setUniformItemSizes(True)
            cc.view().setTextElideMode(QtCore.Qt.ElideRight)

            # Type-to-find over 250 entries. Without a completer the only way to
            # reach Zimbabwe is to scroll the whole list.
            cc.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
            _comp = QtWidgets.QCompleter(
                [cc.itemText(i) for i in range(cc.count())], cc)
            _comp.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
            _comp.setFilterMode(QtCore.Qt.MatchContains)
            _comp.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
            cc.setCompleter(_comp)
            cc.currentIndexChanged.connect(self._validate_signup_form)

            cc.setStyleSheet(
                # ⚠️ `combobox-popup: 0` is the load-bearing line, not the colours.
                #
                # Qt honours setMaxVisibleItems ONLY when the style does not set
                # SH_ComboBox_Popup. QGIS's style does set it, so the combo opens
                # as a POPUP MENU which ignores maxVisibleItems entirely and grows
                # to fit all 250 rows: a full-height panel across the QGIS window,
                # which is exactly what the screenshot showed. Setting this to 0
                # forces the list-view form, where maxVisibleItems is respected and
                # the popup scrolls instead of expanding.
                "QComboBox { combobox-popup: 0;"
                "  border:1px solid #e2ddd8; border-radius:6px;"
                "  padding:10px 13px; font-size:13px; color:#1a1612; background:#ffffff; }"
                "QComboBox:hover { border-color:#ccbda8; }"
                "QComboBox:focus { border-color:#ea580c; }"
                "QComboBox::drop-down { border:none; width:26px; }"
                # The popup needs its own row padding and a bounded height, or it
                # renders as an unstyled full-window list.
                "QComboBox QAbstractItemView { border:1px solid #e2ddd8;"
                "  background:#ffffff; color:#1a1612; selection-background-color:#ea580c;"
                "  selection-color:#ffffff; outline:none; padding:4px; }"
                "QComboBox QAbstractItemView::item { min-height:26px; padding:2px 8px; }")

        # ── Real-time email validity: a ✓/✕ icon inside the email field's trailing edge ──
        em = getattr(self, "input_signup_email", None)
        if em is not None and getattr(self, "_email_status_action", None) is None:
            act = em.addAction(self._mk_status_icon(True), QtWidgets.QLineEdit.TrailingPosition)
            act.setVisible(False)   # hidden until the user types something
            self._email_status_action = act

        # ── Live password-strength meter (inserted right under the password field) ──
        pw = getattr(self, "input_signup_password", None)
        if form is not None and pw is not None and getattr(self, "_pw_strength_bar", None) is None:
            meter = QtWidgets.QWidget()
            mh = QtWidgets.QHBoxLayout(meter)
            mh.setContentsMargins(0, 2, 0, 0); mh.setSpacing(8)
            bar = QtWidgets.QProgressBar()
            bar.setRange(0, 100); bar.setValue(0); bar.setTextVisible(False)
            bar.setFixedHeight(5)
            lab = QtWidgets.QLabel("")
            lab.setMinimumWidth(46)
            lab.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            mh.addWidget(bar, 1); mh.addWidget(lab)
            self._pw_strength_bar = bar
            self._pw_strength_label = lab
            self._pw_strength_meter = meter
            meter.hide()   # only appears once the user starts typing a password
            idx = form.indexOf(pw)
            form.insertWidget(idx + 1 if idx >= 0 else form.count(), meter)

        # ── Legal acceptance row (Terms + Privacy) ──────────────────────────
        # Built once, populated when /legal/config answers. The checkbox starts
        # unticked and DISABLED: until the server has told us which documents
        # are current, there is nothing for the user to agree to, and a tickable
        # box at that moment would be agreeing to nothing.
        if form is not None and getattr(self, "_legal_check", None) is None:
            row = QtWidgets.QWidget()
            rv = QtWidgets.QVBoxLayout(row)
            rv.setContentsMargins(0, 12, 0, 0)
            rv.setSpacing(4)

            chk = QtWidgets.QCheckBox()
            chk.setChecked(False)
            chk.setEnabled(False)
            # "agree to the Terms" but "acknowledge the Privacy Policy": they are
            # different instruments. A privacy policy is a notice about how data
            # is processed, and that processing may not rest on consent at all,
            # so "I agree to the Privacy Policy" is imprecise. This is the shape the
                # wording should take.
            lbl = QtWidgets.QLabel(
                'I agree to the <a href="#terms">Terms of Service</a> and '
                'acknowledge the <a href="#privacy">Privacy Policy</a>.')
            lbl.setTextFormat(QtCore.Qt.RichText)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                "font-size:11px; color:#57534e; background:transparent;"
                " border:none;")
            lbl.linkActivated.connect(self._open_legal_link)

            top = QtWidgets.QHBoxLayout()
            top.setContentsMargins(0, 0, 0, 0)
            top.setSpacing(8)
            top.addWidget(chk, 0, QtCore.Qt.AlignTop)
            top.addWidget(lbl, 1)
            rv.addLayout(top)

            status = QtWidgets.QLabel("Loading Terms and Privacy Policy…")
            status.setWordWrap(True)
            status.setStyleSheet(
                "font-size:10px; color:#a8a29e; background:transparent;"
                " border:none; margin-left:26px;")
            rv.addWidget(status)

            retry = QtWidgets.QPushButton("Retry")
            retry.setCursor(QtCore.Qt.PointingHandCursor)
            retry.setStyleSheet(
                "QPushButton { background:transparent; color:#ea580c;"
                "  border:none; font-size:11px; font-weight:700;"
                "  text-align:left; padding:2px 0 2px 26px; }"
                "QPushButton:hover { color:#c2410c; }")
            retry.clicked.connect(self._fetch_legal_config)
            retry.hide()
            rv.addWidget(retry)

            self._legal_check  = chk
            self._legal_label  = lbl
            self._legal_status = status
            self._legal_retry  = retry
            chk.toggled.connect(self._validate_signup_form)
            form.addWidget(row)

        bd = getattr(self, "btn_do_signup", None)
        if bd is not None:
            bd.setText("Create Account  →")   # single label, arrow inside the button
            bd.setStyleSheet(
                "QPushButton { background:#ea580c; color:#ffffff; border:none;"
                "  border-bottom:3px solid #c2410c; border-radius:6px; padding:13px;"
                "  font-size:14px; font-weight:800; }"
                "QPushButton:hover { background:#d2480a; }"
                "QPushButton:pressed { background:#c2410c; border-bottom:none; padding-top:15px; }"
                "QPushButton:disabled { background:#efe9e0; color:#bcb3a7;"
                "  border-bottom:3px solid #e2ddd8; }")
        bb = getattr(self, "btn_signup_back", None)
        if bb is not None:
            bb.setText("← Back to Sign In")
            bb.setMinimumHeight(28)
            bb.setStyleSheet(
                "QPushButton { background:transparent; color:#78716c; border:none;"
                "  font-size:12px; font-weight:600; padding:6px; }"
                "QPushButton:hover { color:#1a1612; }")

        # De-boxed, left-aligned, full-width — same as Sign In (NOT a floating card).
        # Drop the .ui's expanding spacer (the big void) and top-align with one stretch.
        vl = getattr(self, "verticalLayout_signup", None)
        if vl is not None:
            vl.setContentsMargins(32, 12, 16, 12)
            for i in range(vl.count()):
                it = vl.itemAt(i)
                if it is not None and it.spacerItem() is not None:
                    vl.takeAt(i)
                    break
            vl.setSpacing(12)
            vl.addStretch(1)

        # ── Wire live validation: strength meter + email check + disable-until-valid ──
        for nm in ("input_signup_name", "input_signup_email", "input_signup_confirm"):
            w = getattr(self, nm, None)
            if w is not None:
                w.textChanged.connect(self._validate_signup_form)
        if em is not None:
            em.textChanged.connect(self._update_email_status)
        if pw is not None:
            pw.textChanged.connect(self._update_signup_strength)
            pw.textChanged.connect(self._validate_signup_form)
        if bd is not None:
            bd.setEnabled(False)
        self._update_signup_strength()
        self._update_email_status()
        self._signup_styled = True

    def _mk_status_icon(self, ok, size=16):
        """Small ✓ (green) / ✕ (red) glyph icon for the inline email-validity marker."""
        from qgis.PyQt.QtGui import QPixmap, QPainter, QFont, QIcon
        pm = QPixmap(size, size)
        pm.fill(QtCore.Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(QColor("#16a34a") if ok else QColor("#dc2626"))
        f = QFont(); f.setBold(True); f.setPointSize(int(size * 0.62))
        p.setFont(f)
        p.drawText(pm.rect(), QtCore.Qt.AlignCenter, "✓" if ok else "✕")
        p.end()
        return QIcon(pm)

    def _update_email_status(self, *_):
        """Real-time email validity marker: green ✓ when valid, red ✕ when not;
        hidden while the field is empty."""
        act = getattr(self, "_email_status_action", None)
        if act is None:
            return
        txt = self.input_signup_email.text().strip()
        if not txt:
            act.setVisible(False)
            return
        ok = self._validate_email(txt)
        act.setIcon(self._mk_status_icon(ok))
        act.setToolTip("Looks good" if ok else "Enter a valid email address")
        act.setVisible(True)

    def _update_signup_strength(self, *_):
        """Drive the live password-strength meter (length-based: <8 Weak, <12 Good,
        else Strong). Empty -> blank track, no label."""
        bar = getattr(self, "_pw_strength_bar", None)
        lab = getattr(self, "_pw_strength_label", None)
        if bar is None or lab is None:
            return
        meter = getattr(self, "_pw_strength_meter", None)
        pw = self.input_signup_password.text()
        if not pw:
            if meter is not None:
                meter.hide()
            bar.setValue(0); lab.setText("")
            return
        if meter is not None:
            meter.show()
        if len(pw) < 8:
            val, color, word = 33, "#dc2626", "Weak"
        elif len(pw) < 12:
            val, color, word = 66, "#d97706", "Good"
        else:
            val, color, word = 100, "#10b981", "Strong"
        bar.setValue(val)
        bar.setStyleSheet("QProgressBar { border:none; background:#ece6dd; border-radius:3px; }"
                          f"QProgressBar::chunk {{ background:{color}; border-radius:3px; }}")
        lab.setText(word)
        lab.setStyleSheet(f"font-size:10px; font-weight:700; color:{color}; background:transparent;")

    # ── Legal acceptance ────────────────────────────────────────────────
    def _fetch_legal_config(self):
        """Ask the server which Terms and Privacy Policy are current.

        Runs every time the signup page opens, not once per session, so a
        document revised while the plugin is open is picked up rather than
        cached past its own replacement.
        """
        self._legal_cfg = None
        st = getattr(self, "_legal_status", None)
        if st is not None:
            st.setText("Loading Terms and Privacy Policy…")
            st.setStyleSheet("font-size:10px; color:#a8a29e;"
                             " background:transparent; border:none;"
                             " margin-left:26px;")
        for w in (getattr(self, "_legal_check", None),
                  getattr(self, "_legal_retry", None)):
            if w is not None:
                w.setEnabled(False) if w is self._legal_check else w.hide()
        self._validate_signup_form()

        def _work():
            try:
                r = requests.get(LEGAL_CONFIG_URL, timeout=15)
                r.raise_for_status()
                d = r.json()
                cfg = json.dumps({
                    "terms_version":   d["terms"]["version"],
                    "terms_url":       d["terms"]["url"],
                    "privacy_version": d["privacy"]["version"],
                    "privacy_url":     d["privacy"]["url"],
                })
            except Exception:
                cfg = ""
            QtCore.QMetaObject.invokeMethod(
                self, "_apply_legal_config", QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, cfg))
        threading.Thread(target=_work, daemon=True).start()

    @QtCore.pyqtSlot(str)
    def _apply_legal_config(self, payload: str):
        """Enable the acceptance row, or fail closed with a way to retry.

        FAILS CLOSED. If the current versions cannot be fetched there is no
        fallback to a remembered value: agreeing to a document we cannot
        confirm is current is worth nothing, and a legal control that quietly
        degrades to "proceed anyway" is not a control.
        """
        chk = getattr(self, "_legal_check", None)
        st  = getattr(self, "_legal_status", None)
        rt  = getattr(self, "_legal_retry", None)
        self._legal_cfg = json.loads(payload) if payload else None

        if self._legal_cfg:
            if chk is not None:
                chk.setEnabled(True)
            if st is not None:
                st.hide()
            if rt is not None:
                rt.hide()
        else:
            if chk is not None:
                chk.setChecked(False)
                chk.setEnabled(False)
            if st is not None:
                st.show()
                st.setText("Unable to load the Terms of Service and Privacy "
                           "Policy. Check your connection and try again.")
                st.setStyleSheet("font-size:10px; color:#b91c1c;"
                                 " background:transparent; border:none;"
                                 " margin-left:26px;")
            if rt is not None:
                rt.show()
        self._validate_signup_form()

    def _open_legal_link(self, href: str):
        """Open the hosted document in the user's browser.

        Opening it is never required before ticking the box. The link has to be
        available; forcing a click produces no better evidence than the
        acceptance record already does, and irritates people.
        """
        cfg = getattr(self, "_legal_cfg", None) or {}
        url = cfg.get("terms_url") if href == "#terms" else cfg.get("privacy_url")
        if url:
            QDesktopServices.openUrl(QUrl(url))

    @QtCore.pyqtSlot(dict)
    def _legal_versions_stale(self, detail: dict):
        """The server refused because the documents changed under us.

        Re-fetch, and CLEAR THE TICK. Carrying the old acceptance forward would
        record agreement to a document the user never saw, which is the exact
        failure this whole mechanism exists to prevent.
        """
        chk = getattr(self, "_legal_check", None)
        if chk is not None:
            chk.setChecked(False)
        self._fetch_legal_config()
        self._show_signup_error(
            (detail or {}).get("message")
            or "The Terms of Service or Privacy Policy have been updated. "
               "Please review and acknowledge the latest version.",
            "warning")

    def _validate_signup_form(self, *_):
        """Enable Create Account only when name + valid email + 8-char password +
        matching confirm are all present."""
        bd = getattr(self, "btn_do_signup", None)
        if bd is None:
            return
        name_ok    = bool(self.input_signup_name.text().strip())
        em         = self.input_signup_email.text().strip()
        email_ok   = self._validate_email(em)
        pwv        = self.input_signup_password.text()
        pwd_ok     = len(pwv) >= 8
        confirm_ok = bool(self.input_signup_confirm.text()) and pwv == self.input_signup_confirm.text()
        # Both halves matter. The tick is the user's act; having a config is
        # what makes the tick mean something, since without it we do not know
        # WHICH documents were agreed to and the server would reject the signup
        # anyway. Gating on both fails closed at the earliest point.
        chk = getattr(self, "_legal_check", None)
        legal_ok = bool(getattr(self, "_legal_cfg", None)) and (
            chk is None or chk.isChecked())
        # A country is REQUIRED: service availability is decided from it, and the
        # server refuses a signup without one. Enforcing it here means the user
        # finds out while looking at the field, not after submitting the form.
        # Index 0 is the "Select your country" placeholder, not an answer.
        cc = getattr(self, "combo_signup_country", None)
        country_ok = cc is None or cc.currentIndex() > 0
        bd.setEnabled(name_ok and email_ok and pwd_ok and confirm_ok
                      and legal_ok and country_ok)

    def _restyle_reset_page(self):
        """One-time Reset-Password redesign: a clean two-step flow (Step 1 email →
        Step 2 code + new password) with a step indicator, disable-until-valid
        buttons, a live password-strength meter, and a 60s resend countdown. Same
        ATLAS de-boxed orange/cream language as Sign In / Create Account (NO card,
        NO blue/slate). Pure runtime restyle — preserves every wired widget."""
        if getattr(self, "_reset_styled", False):
            return
        t = getattr(self, "label_reset_title", None)
        if t is not None:
            t.setStyleSheet("font-size:22px; font-weight:800; color:#1a1612; background:transparent;")
        sub = getattr(self, "label_reset_subtitle", None)
        if sub is not None:
            sub.setText("Recover your account access.")
            sub.setStyleSheet("font-size:12px; color:#78716c; background:transparent;")

        # De-box the form groupbox; tight field grouping (label hugs input).
        gb = getattr(self, "groupbox_reset", None)
        if gb is not None:
            gb.setTitle("")
            gb.setContentsMargins(0, 0, 0, 0)
            gb.setStyleSheet("QGroupBox { border:none; background:transparent; margin-top:0px;"
                             "  padding:0px; } QLabel { background:transparent; border:none; }")
        form = getattr(self, "vl_reset_form", None)
        if form is not None:
            form.setSpacing(6)
            form.setContentsMargins(0, 0, 0, 0)
        for ln in ("label_reset_code", "label_reset_new_password", "label_reset_confirm"):
            lw = getattr(self, ln, None)
            if lw is not None:
                lw.setStyleSheet("font-size:12px; font-weight:600; color:#44403c;"
                                 " background:transparent; margin-top:8px;")
        le = getattr(self, "label_reset_email", None)   # first label in its step: no top gap
        if le is not None:
            le.setStyleSheet("font-size:12px; font-weight:600; color:#44403c; background:transparent;")

        # Clean white inputs (identical to Sign In / Create Account).
        for n in ("input_reset_email", "input_reset_code",
                  "input_reset_new_password", "input_reset_confirm"):
            w = getattr(self, n, None)
            if w is not None:
                w.setMinimumHeight(40)
                w.setStyleSheet("QLineEdit { border:1px solid #e2ddd8; border-radius:6px;"
                                "  padding:10px 13px; font-size:13px; color:#1a1612; background:#ffffff; }"
                                "QLineEdit:hover { border-color:#ccbda8; }"
                                "QLineEdit:focus { border-color:#ea580c; }")

        # Sharp orange primaries with a grey disabled state.
        primary = ("QPushButton { background:#ea580c; color:#ffffff; border:none;"
                   "  border-bottom:3px solid #c2410c; border-radius:6px; padding:13px;"
                   "  font-size:14px; font-weight:800; }"
                   "QPushButton:hover { background:#d2480a; }"
                   "QPushButton:pressed { background:#c2410c; border-bottom:none; padding-top:15px; }"
                   "QPushButton:disabled { background:#efe9e0; color:#bcb3a7;"
                   "  border-bottom:3px solid #e2ddd8; }")
        for bn in ("btn_send_reset_code", "btn_do_reset"):
            b = getattr(self, bn, None)
            if b is not None:
                b.setMinimumHeight(44)
                b.setStyleSheet(primary)
        if getattr(self, "btn_send_reset_code", None) is not None:
            self.btn_send_reset_code.setText("Send Reset Code  →")
        if getattr(self, "btn_do_reset", None) is not None:
            self.btn_do_reset.setText("Reset Password  →")
        bb = getattr(self, "btn_reset_back", None)
        if bb is not None:
            bb.setText("← Back to Sign In")
            bb.setMinimumHeight(28)
            bb.setStyleSheet("QPushButton { background:transparent; color:#78716c; border:none;"
                             "  font-size:12px; font-weight:600; padding:6px; }"
                             "QPushButton:hover { color:#1a1612; }")

        # Step-1 subtle hint (repurpose the old long instructional label).
        hint = getattr(self, "label_reset_hint", None)
        if hint is not None:
            hint.setText("We'll email you a reset code.")
            hint.setStyleSheet("font-size:11px; color:#a8a29e; background:transparent; border:none;")

        # Resend link (Step 2) right under the code field.
        code_in = getattr(self, "input_reset_code", None)
        if form is not None and code_in is not None and getattr(self, "_reset_resend_btn", None) is None:
            resend = QtWidgets.QPushButton("Resend code")
            resend.setCursor(QtCore.Qt.PointingHandCursor)
            resend.setStyleSheet("QPushButton { background:transparent; color:#ea580c; border:none;"
                                 "  font-size:11px; font-weight:600; text-align:left; padding:2px 0; }"
                                 "QPushButton:hover { color:#c2410c; }"
                                 "QPushButton:disabled { color:#bcb3a7; }")
            resend.clicked.connect(self._resend_reset_code)
            self._reset_resend_btn = resend
            idx = form.indexOf(code_in)
            form.insertWidget(idx + 1 if idx >= 0 else form.count(), resend)

        # Password-strength meter (Step 2) under the new-password field.
        pw = getattr(self, "input_reset_new_password", None)
        if form is not None and pw is not None and getattr(self, "_reset_strength_bar", None) is None:
            meter = QtWidgets.QWidget()
            mh = QtWidgets.QHBoxLayout(meter); mh.setContentsMargins(0, 2, 0, 0); mh.setSpacing(8)
            bar = QtWidgets.QProgressBar(); bar.setRange(0, 100); bar.setValue(0); bar.setTextVisible(False)
            bar.setFixedHeight(5)
            lab = QtWidgets.QLabel(""); lab.setMinimumWidth(46)
            lab.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            mh.addWidget(bar, 1); mh.addWidget(lab)
            self._reset_strength_bar = bar
            self._reset_strength_label = lab
            self._reset_strength_meter = meter
            meter.hide()
            idx = form.indexOf(pw)
            form.insertWidget(idx + 1 if idx >= 0 else form.count(), meter)

        # Drop the .ui's expanding spacer (the big void); top-align everything.
        vl = getattr(self, "verticalLayout_reset", None)
        if vl is not None:
            vl.setContentsMargins(32, 12, 16, 12)
            for i in range(vl.count()):
                it = vl.itemAt(i)
                if it is not None and it.spacerItem() is not None:
                    vl.takeAt(i)
                    break
            vl.setSpacing(12)
            vl.addStretch(1)

        # Move the status/alert label to the TOP of the form (between subtitle and
        # first field) so "Code sent — check your email" guides eyes into the first
        # input rather than appearing below all the fields.
        err = getattr(self, "label_reset_error", None)
        if form is not None and err is not None:
            # Remove from wherever the .ui placed it (outer vl or form).
            if vl is not None:
                idx_vl = vl.indexOf(err)
                if idx_vl >= 0:
                    vl.removeWidget(err)
            idx_f = form.indexOf(err)
            if idx_f >= 0:
                form.removeWidget(err)
            # Re-insert at position 0 in the form (above every field).
            form.insertWidget(0, err)
            err.hide()

        # Wire live validation + strength.
        if getattr(self, "input_reset_email", None) is not None:
            self.input_reset_email.textChanged.connect(self._validate_reset_email_field)
        for nm in ("input_reset_code", "input_reset_new_password", "input_reset_confirm"):
            w = getattr(self, nm, None)
            if w is not None:
                w.textChanged.connect(self._validate_reset_form)
        if pw is not None:
            pw.textChanged.connect(self._update_reset_strength)

        self._reset_styled = True
        self._show_reset_step1()

    def _set_reset_step(self, n):
        """Render the 'STEP 1 — STEP 2' indicator with the reached steps in orange."""
        lbl = getattr(self, "label_reset_step", None)
        if lbl is None:
            return
        on  = "color:#ea580c; font-weight:800;"
        off = "color:#cbd5e1; font-weight:800;"
        s1 = on                      # Step 1 is always reached
        s2 = on if n >= 2 else off
        lbl.setText(f"<span style='{s1}'>STEP 1</span>"
                    f"<span style='color:#d6cfca'>&nbsp;&nbsp;—&nbsp;&nbsp;</span>"
                    f"<span style='{s2}'>STEP 2</span>")
        lbl.setStyleSheet("font-size:11px; letter-spacing:1px;"
                          " font-family:'Consolas','Roboto Mono',monospace; background:transparent;")

    _RESET_STEP1 = ("label_reset_email", "input_reset_email",
                    "btn_send_reset_code", "label_reset_hint")
    _RESET_STEP2 = ("label_reset_code", "input_reset_code", "_reset_resend_btn",
                    "label_reset_new_password", "input_reset_new_password",
                    "label_reset_confirm", "input_reset_confirm", "btn_do_reset")

    def _show_reset_step1(self):
        """Step 1: only email + Send Reset Code (everything else hidden)."""
        self._set_reset_step(1)
        for n in self._RESET_STEP1:
            w = getattr(self, n, None)
            if w is not None:
                w.show()
        for n in self._RESET_STEP2 + ("_reset_strength_meter",):
            w = getattr(self, n, None)
            if w is not None:
                w.hide()
        self._validate_reset_email_field()

    def _show_reset_step2(self):
        """Step 2: code + new password + confirm + Reset Password."""
        self._set_reset_step(2)
        for n in self._RESET_STEP1:
            w = getattr(self, n, None)
            if w is not None:
                w.hide()
        for n in self._RESET_STEP2:
            w = getattr(self, n, None)
            if w is not None:
                w.show()
        self._update_reset_strength()   # stays hidden until a password is typed
        self._validate_reset_form()
        if getattr(self, "input_reset_code", None) is not None:
            self.input_reset_code.setFocus()

    def _validate_reset_email_field(self, *_):
        """Enable Send Reset Code only for a valid-looking email (Step 1)."""
        b = getattr(self, "btn_send_reset_code", None)
        if b is None:
            return
        b.setEnabled(self._validate_email(self.input_reset_email.text().strip()))

    def _validate_reset_form(self, *_):
        """Enable Reset Password only when code + 8-char password + matching confirm."""
        b = getattr(self, "btn_do_reset", None)
        if b is None:
            return
        code_ok    = bool(self.input_reset_code.text().strip())
        pwv        = self.input_reset_new_password.text()
        pwd_ok     = len(pwv) >= 8
        confirm_ok = bool(self.input_reset_confirm.text()) and pwv == self.input_reset_confirm.text()
        b.setEnabled(code_ok and pwd_ok and confirm_ok)

    def _update_reset_strength(self, *_):
        """Live password-strength meter for the new password (same scale as Create
        Account: <8 Weak, <12 Good, else Strong). Hidden while empty."""
        bar = getattr(self, "_reset_strength_bar", None)
        lab = getattr(self, "_reset_strength_label", None)
        if bar is None or lab is None:
            return
        meter = getattr(self, "_reset_strength_meter", None)
        pw = self.input_reset_new_password.text()
        if not pw:
            if meter is not None:
                meter.hide()
            bar.setValue(0); lab.setText("")
            return
        if meter is not None:
            meter.show()
        if len(pw) < 8:
            val, color, word = 33, "#dc2626", "Weak"
        elif len(pw) < 12:
            val, color, word = 66, "#d97706", "Good"
        else:
            val, color, word = 100, "#10b981", "Strong"
        bar.setValue(val)
        bar.setStyleSheet("QProgressBar { border:none; background:#ece6dd; border-radius:3px; }"
                          f"QProgressBar::chunk {{ background:{color}; border-radius:3px; }}")
        lab.setText(word)
        lab.setStyleSheet(f"font-size:10px; font-weight:700; color:{color}; background:transparent;")

    def _resend_reset_code(self):
        """Step-2 'Resend code' link: re-send to the same email, restart the timer."""
        btn = getattr(self, "_reset_resend_btn", None)
        if btn is not None:
            btn.setEnabled(False)
            btn.setText("Sending…")
        self._handle_send_reset_code()

    def _start_resend_timer(self, seconds=60):
        """Disable the resend link for `seconds` with a live countdown."""
        btn = getattr(self, "_reset_resend_btn", None)
        if btn is None:
            return
        self._resend_left = int(seconds)
        btn.setEnabled(False)
        btn.setText(f"Resend code in {self._resend_left}s")
        timer = getattr(self, "_resend_timer", None)
        if timer is None:
            timer = QtCore.QTimer(self)
            timer.setInterval(1000)
            timer.timeout.connect(self._tick_resend_timer)
            self._resend_timer = timer
        timer.start()

    def _tick_resend_timer(self):
        btn = getattr(self, "_reset_resend_btn", None)
        self._resend_left -= 1
        if self._resend_left <= 0:
            if getattr(self, "_resend_timer", None) is not None:
                self._resend_timer.stop()
            if btn is not None:
                btn.setEnabled(True)
                btn.setText("Resend code")
            return
        if btn is not None:
            btn.setText(f"Resend code in {self._resend_left}s")

    def _build_profile_menu(self):
        """One-time header declutter: replace the full-width billing strip with a
        compact PROFILE dropdown (top-right). The menu holds balance, View Plans,
        Buy Credits, Manage billing, Feedback and Log out — so the header itself
        carries only the step indicator. Native QMenu actions (robust) + a guarded
        fallback so Feedback/Logout are never lost if placement fails."""
        if getattr(self, "_profile_built", False):
            return
        try:
            menu = QtWidgets.QMenu(self)
            # Industrial: sharp 2px corners, thin #d1d5db border, and drop the fluffy
            # OS shadow for a tighter look.
            try:
                menu.setWindowFlag(QtCore.Qt.NoDropShadowWindowHint, True)
            except Exception:
                pass
            menu.setStyleSheet(
                "QMenu { background:#ffffff; border:1px solid #d1d5db; border-radius:2px; padding:4px; }"
                "QMenu::item { padding:8px 22px 8px 12px; border-radius:2px; font-size:12px; color:#44403c; }"
                "QMenu::item:selected { background:#f3f4f6; color:#1a1612; }"
                "QMenu::item:disabled { color:#b8b2ab; }"          # muted, intentional-looking
                "QMenu::item:selected:disabled { background:transparent; }"  # no hover on disabled
                "QMenu::separator { height:1px; background:#e5e7eb; margin:4px 6px; }")
            menu.addAction("View Plans").triggered.connect(self._show_plans)
            # Free-only launch: no purchase entry point anywhere.
            if SHOW_PAYG:
                menu.addAction("Buy Credits").triggered.connect(self._buy_tokens)
            # Mirrors the Results-header menu in _make_profile_controls. Both menus
            # need it: this is the Setup screen, where a user without a plan
            # actually is when they realise they want a trial.
            self._trial_request_action_setup, self._trial_request_row_setup =                 self._add_trial_menu_row(menu)
            menu.addSeparator()
            # Storage lives here (not on the main chip) so the header stays clean; the
            # main chip only surfaces storage when the user is near/over the limit.
            # Status = a styled caption (heading), action = the greyable item below.
            self._storage_caption = self._make_storage_caption(menu)
            self._freeup_action = menu.addAction("Free up storage")
            self._freeup_action.triggered.connect(self._free_up_storage)
            # Nothing to free until the user is over quota -> disabled by default
            # (removes the dead-end "nothing needs to be removed" popup).
            self._freeup_action.setEnabled(False)
            self._freeup_action.setToolTip("Only available when you're over your storage limit")
            # On-demand file migration: an org member who dismissed the join prompt (or
            # chose 'keep') can move their existing files to the team pool any time.
            # Hidden until _set_migration_ui sees they still have personal files.
            self._migrate_action = menu.addAction("Move my files to the team")
            self._migrate_action.triggered.connect(self._migrate_files_on_demand)
            self._migrate_action.setVisible(False)
            menu.addSeparator()
            # '&' is a Qt menu mnemonic -> use '&&' to render a literal ampersand.
            # Hidden with PAYG, for the reason given in _make_profile_controls:
            # with nothing purchasable there is no Stripe customer to manage.
            if SHOW_PAYG or SHOW_SUBSCRIPTION_TIERS:
                menu.addAction("Manage billing && saved card").triggered.connect(lambda: self._open_portal())
            menu.addAction("Imagery requirements").triggered.connect(
                self._show_imagery_requirements)
            menu.addAction("Feedback").triggered.connect(self._open_feedback_dialog)
            menu.addSeparator()
            menu.addAction("Log out").triggered.connect(self._handle_logout)

            # Glanceable balance chip in the header (discoverability): always visible,
            # click -> View Plans. Updated by _set_balance_ui.
            self.label_token_balance = QtWidgets.QPushButton("🪙  …")
            self.label_token_balance.setCursor(QtCore.Qt.PointingHandCursor)
            self.label_token_balance.setToolTip(
                "View plans & buy credits" if SHOW_PAYG else "View your plan")
            self.label_token_balance.setStyleSheet(
                "QPushButton { background:#fff4ed; color:#9a3412; border:1px solid #fed7aa;"
                "  border-radius:14px; padding:6px 14px; font-size:12px; font-weight:700; }"
                "QPushButton:hover { background:#ffe9db; border-color:#fdba74; }")
            self.label_token_balance.clicked.connect(self._show_plans)

            from qgis.PyQt.QtGui import QIcon
            who = (self.current_user_email or "Account").split("@")[0]
            prof = QtWidgets.QToolButton()
            prof.setIcon(QIcon(self._avatar_pixmap((self.current_user_email or "U")[0])))
            prof.setIconSize(QtCore.QSize(22, 22))
            prof.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            prof.setText(f"{who}  ▾")
            prof.setCursor(QtCore.Qt.PointingHandCursor)
            prof.setPopupMode(QtWidgets.QToolButton.InstantPopup)
            prof.setMenu(menu)
            prof.setStyleSheet(
                "QToolButton { background:#ffffff; border:1px solid #e2ddd8; border-radius:16px;"
                "  padding:5px 12px 5px 5px; font-size:12px; font-weight:600; color:#57534e; }"
                "QToolButton:hover { border-color:#ccbda8; color:#1a1612; }"
                "QToolButton::menu-indicator { image:none; width:0px; }")
            self._profile_btn = prof
            self._profile_menu = menu

            header = self._find_header_hbox()
            if header is not None:
                header.addWidget(self.label_token_balance)   # glanceable balance, click -> plans
                header.addWidget(prof)
                # Feedback/Logout now live in the menu -> hide the header copies.
                for b in ("btn_feedback", "btn_logout_setup"):
                    w = getattr(self, b, None)
                    if w is not None:
                        w.hide()
            else:
                # Fallback: couldn't locate the header row — drop the chip + profile
                # button at the top and KEEP the original feedback/logout buttons.
                page_lay = getattr(self, "verticalLayout_setup", None)
                if page_lay is not None:
                    wrap = QtWidgets.QHBoxLayout(); wrap.addStretch(1)
                    wrap.addWidget(self.label_token_balance); wrap.addWidget(prof)
                    page_lay.insertLayout(0, wrap)

            self._billing_strip_built = True   # keep changeEvent's balance refresh working
            self._profile_built = True
        except Exception as e:
            print(f"profile menu build failed: {e}")

    def _apply_setup_layout(self):
        """One-time Setup-page workflow reorder (pure runtime — no .ui change):
        move Recent Uploads to sit BELOW the Mission + Base-map row, so the flow is
        upload -> configure (mission/basemap) -> recent uploads -> Begin Processing.
        The .ui ships it as upload -> recent uploads -> config, which broke the flow."""
        if getattr(self, "_setup_laid_out", False):
            return
        lay = getattr(self, "verticalLayout_setup", None)
        gb  = getattr(self, "groupbox_recent_uploads", None)
        btn = getattr(self, "btn_next_setup", None)
        if lay is None or gb is None or btn is None:
            return
        try:
            lay.removeWidget(gb)                 # detach Recent Uploads from its spot
            btn_idx = lay.indexOf(btn)           # CTA is last; spacer sits just above it
            lay.insertWidget(max(0, btn_idx - 1), gb)   # re-insert just above that spacer

            # Collapsible accordion: a clickable header toggles the list; starts
            # collapsed so it never interrupts the upload -> configure -> process flow.
            tgl = QtWidgets.QToolButton()
            tgl.setCheckable(True)
            tgl.setChecked(False)
            tgl.setCursor(QtCore.Qt.PointingHandCursor)
            tgl.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
            tgl.setArrowType(QtCore.Qt.RightArrow)
            tgl.setText("Recent uploads")
            tgl.setStyleSheet(
                "QToolButton { border:none; background:transparent; padding:6px 0;"
                "  font-size:12px; font-weight:700; color:#78716c; }"
                "QToolButton:hover { color:#1a1612; }")

            def _toggle_recent(checked):
                gb.setVisible(checked)
                tgl.setArrowType(QtCore.Qt.DownArrow if checked else QtCore.Qt.RightArrow)
            tgl.toggled.connect(_toggle_recent)

            gb.setTitle("")          # drop the tab-style title; the toggle is the header
            gb.setVisible(False)     # start collapsed
            lay.insertWidget(lay.indexOf(gb), tgl)   # header sits just above the list
            self._recent_toggle = tgl
            self._sync_recent_toggle()

            from qgis.PyQt.QtGui import QFont

            def _eng_font(widget, pt=8):
                f = widget.font(); f.setBold(True); f.setPointSize(pt)
                try:
                    f.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
                except Exception:
                    pass
                widget.setFont(f)

            for gbname in ("groupbox_mission_name", "groupbox_base_map"):
                g = getattr(self, gbname, None)
                if g is not None:
                    g.setTitle("")
                    g.setStyleSheet("QGroupBox { border:none; background:transparent; margin-top:0px; }")
            gmn = getattr(self, "groupbox_mission_name", None)
            mb_hbox = self._find_layout_containing(gmn) if gmn is not None else None
            vls = getattr(self, "verticalLayout_setup", None)
            if mb_hbox is not None and vls is not None:
                mb_idx = next((i for i in range(vls.count())
                               if vls.itemAt(i).layout() is mb_hbox), -1)
                if mb_idx >= 0:
                    msh = QtWidgets.QLabel("Mission Setup")
                    msh.setStyleSheet("font-size:13px; font-weight:800; color:#1a1612;"
                                      " background:transparent; margin-top:14px;")
                    vls.insertWidget(mb_idx, msh)
                    # Mission name + Base map stay SIDE-BY-SIDE (kept compact so an
                    # expanded Recent Uploads doesn't push Begin Processing off-screen).

            qh = getattr(self, "label_query_header", None)
            if qh is not None:
                qh.setText("Query Image")
                qh.setStyleSheet("font-size:13px; font-weight:800; color:#1a1612;"
                                 " background:transparent; margin-top:10px;")
                # Imagery requirements as an inline accordion (same pattern as
                # Recent Uploads below), NOT a modal: this is reference material
                # the user wants open WHILE picking files and checking drone
                # settings, so it must not cover the screen it describes.
                # Collapsed by default so it never interrupts the normal flow.
                if vls is not None:
                    qi = vls.indexOf(qh)
                    if qi >= 0:
                        vls.removeWidget(qh)
                        hdr_row = QtWidgets.QHBoxLayout()
                        hdr_row.setContentsMargins(0, 0, 0, 0)
                        hdr_row.setSpacing(8)
                        hdr_row.addWidget(qh)
                        hdr_row.addStretch(1)
                        req_tgl = QtWidgets.QToolButton()
                        req_tgl.setCheckable(True)
                        req_tgl.setChecked(False)
                        req_tgl.setCursor(QtCore.Qt.PointingHandCursor)
                        req_tgl.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
                        req_tgl.setArrowType(QtCore.Qt.RightArrow)
                        req_tgl.setText("Imagery requirements")
                        req_tgl.setToolTip(
                            "What your images need for accurate georeferencing")
                        req_tgl.setStyleSheet(
                            "QToolButton { border:none; background:transparent;"
                            "  padding:2px 0; margin-top:10px; font-size:11px;"
                            "  font-weight:600; color:#ea580c; }"
                            "QToolButton:hover { color:#c2410c; }")
                        hdr_row.addWidget(req_tgl)
                        vls.insertLayout(qi, hdr_row)

                        req_panel = QtWidgets.QFrame()
                        req_panel.setStyleSheet(
                            "QFrame { background:#fffaf5; border:1px solid #f0e2d2;"
                            "  border-radius:8px; }"
                            " QLabel { background:transparent; border:none; }")
                        rp = QtWidgets.QVBoxLayout(req_panel)
                        rp.setContentsMargins(14, 12, 14, 12)
                        req_body = QtWidgets.QLabel(self._imagery_requirements_html())
                        req_body.setTextFormat(QtCore.Qt.RichText)
                        req_body.setWordWrap(True)
                        req_body.setStyleSheet(
                            "font-size:11px; color:#57534e; background:transparent;")
                        rp.addWidget(req_body)
                        req_panel.setVisible(False)
                        vls.insertWidget(qi + 1, req_panel)

                        def _toggle_req(checked, _p=req_panel, _t=req_tgl):
                            _p.setVisible(checked)
                            _t.setArrowType(QtCore.Qt.DownArrow if checked
                                            else QtCore.Qt.RightArrow)
                        req_tgl.toggled.connect(_toggle_req)
                        self._req_toggle = req_tgl

            old_cb = getattr(self, "checkbox_auto_load", None)
            vlb = getattr(self, "vl_basemap", None)
            if old_cb is not None and vlb is not None:
                tog = ToggleSwitch()
                tog.setText(old_cb.text())
                tog.setChecked(old_cb.isChecked())
                i = vlb.indexOf(old_cb)
                vlb.removeWidget(old_cb); old_cb.hide(); old_cb.setParent(None)
                vlb.insertWidget(i if i >= 0 else vlb.count(), tog)
                self.checkbox_auto_load = tog   # subclass of QCheckBox -> isChecked() still works

            # Symmetry: give the Base map column a field label above its dropdown so the
            # "Satellite" box shares a baseline with the "Mission name" field on the left.
            if vlb is not None:
                ms = QtWidgets.QLabel("Map source")
                ms.setStyleSheet("font-size: 11px; font-weight: 600; color: #57534e;")
                vlb.insertWidget(0, ms)

            # Clean, styled basemap dropdown. The native Windows combo popup ignores
            # most item-view QSS, so we force an explicit QListView and style THAT
            # directly — that's what makes the padding / orange selection actually show.
            combo = getattr(self, "combo_basemap_setup", None)
            if combo is not None:
                combo.setMinimumHeight(38)
                combo.setCursor(QtCore.Qt.PointingHandCursor)
                lview = QtWidgets.QListView()
                lview.setSpacing(2)
                combo.setView(lview)
                combo.setStyleSheet(
                    "QComboBox { border:1px solid #e2ddd8; border-radius:6px; padding:7px 12px;"
                    "  background:#ffffff; font-size:13px; color:#1a1612; }"
                    "QComboBox:hover { border-color:#ccbda8; }"
                    "QComboBox:focus { border-color:#ea580c; }"
                    "QComboBox:on { border-color:#ea580c; }"          # while popup is open
                    "QComboBox::drop-down { border:none; width:28px; }"
                    "QComboBox::down-arrow { image:none; border-left:5px solid transparent;"
                    "  border-right:5px solid transparent; border-top:6px solid #9a948c;"
                    "  margin-right:10px; width:0; height:0; }")
                # Style the popup list view itself (reliable across platforms).
                combo.view().setStyleSheet(
                    "QListView { border:1px solid #e2ddd8; border-radius:8px; background:#ffffff;"
                    "  outline:none; padding:5px; font-size:13px; }"
                    "QListView::item { min-height:30px; padding:5px 10px; border-radius:6px;"
                    "  color:#44403c; margin:1px 0; }"
                    "QListView::item:hover { background:#faf3ec; color:#1a1612; }"
                    "QListView::item:selected { background:#fff4ed; color:#9a3412; }")

            bp = getattr(self, "btn_next_setup", None)
            if bp is not None:
                bp.setText("Begin Processing  →")
                _eng_font(bp, pt=11)
                bp.setStyleSheet(
                    "QPushButton { background: qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                    "    stop:0 #f4691c, stop:1 #ea580c); color:#ffffff; border:none;"
                    "  border-bottom:3px solid #c2410c; border-radius:6px; padding:14px;"
                    "  font-size:14px; font-weight:800; }"
                    "QPushButton:hover { background:#ea580c; }"
                    "QPushButton:pressed { background:#c2410c; border-bottom:none; padding-top:16px; }"
                    # A custom QSS background suppresses Qt's automatic disabled-graying,
                    # so without this the button stayed full orange (looked clickable)
                    # even while setEnabled(False) — this was misleading before any
                    # valid image was selected.
                    "QPushButton:disabled { background:#efe9e0; color:#bcb3a7;"
                    "  border-bottom:3px solid #e2ddd8; }")

            # Remove the duplicate "N images selected" grey-italic line — the drop-zone
            # label already shows the selection state, so this keeps the green box clean.
            fi = getattr(self, "label_file_info", None)
            if fi is not None:
                fi.hide()

            # Match the step pill's height to the credit chip so their tops line up.
            si = getattr(self, "label_step_indicator", None)
            if si is not None:
                si.setStyleSheet(
                    "background:#fff4ed; color:#9a3412; border:1px solid #fed7aa;"
                    " border-radius:14px; padding:6px 14px; font-size:11px; font-weight:800;")

            # Align Mission + Base map columns: equal width, top-aligned (parallel).
            mb = self._find_layout_containing(getattr(self, "groupbox_mission_name", None))
            if mb is not None:
                if mb.count() >= 2:
                    mb.setStretch(0, 1)
                    mb.setStretch(1, 1)
                for gbn in ("groupbox_mission_name", "groupbox_base_map"):
                    g = getattr(self, gbn, None)
                    if g is not None:
                        mb.setAlignment(g, QtCore.Qt.AlignTop)

            self._setup_laid_out = True
            self._pin_setup_cta()
        except Exception as e:
            print(f"setup layout reorder failed: {e}")

    def _pin_setup_cta(self):
        """Move 'Begin Processing' out of the scrollable Setup-page content into a
        fixed footer on the dialog itself, so it's always visible without scrolling
        even on short/scaled screens (client feedback: the button could scroll out
        of view). Shown only while the Setup page is the active page."""
        try:
            lay = getattr(self, "verticalLayout_setup", None)
            btn = getattr(self, "btn_next_setup", None)
            main = getattr(self, "verticalLayout_main", None)
            if lay is None or btn is None or main is None:
                return
            idx = lay.indexOf(btn)
            if idx < 0:
                return
            if idx > 0:
                prev = lay.itemAt(idx - 1)
                if prev is not None and prev.spacerItem() is not None:
                    lay.takeAt(idx - 1)
            lay.removeWidget(btn)

            footer = QtWidgets.QWidget()
            footer.setObjectName("setup_cta_footer")
            footer.setStyleSheet(
                "QWidget#setup_cta_footer { background:#faf7f2; border-top:1px solid #e7ded2; }")
            flay = QtWidgets.QVBoxLayout(footer)
            flay.setContentsMargins(16, 12, 16, 14)
            flay.addWidget(btn)
            footer.setVisible(self.stacked_pages.currentIndex() == self.PAGE_SETUP)
            main.addWidget(footer)
            self._setup_cta_footer = footer

            def _sync_footer(index):
                footer.setVisible(index == self.PAGE_SETUP)
            self.stacked_pages.currentChanged.connect(_sync_footer)
        except Exception as e:
            print(f"setup CTA pin failed: {e}")

    def _sync_recent_toggle(self):
        """Keep the accordion header label in sync with the upload count."""
        tgl = getattr(self, "_recent_toggle", None)
        if tgl is not None:
            n = len(getattr(self, "_upload_rows", []) or [])
            tgl.setText(f"Recent uploads ({n})" if n else "Recent uploads")

    # Pretty tier names for the strip. Internal tier key stays 'hobbyist'
    # (catalog PK + checkout), but the customer-facing name is "Entry".
    _TIER_LABELS = {
        "free": "Free", "payg": "Pay As You Go", "hobbyist": "Entry",
        "professional": "Professional", "enterprise": "Enterprise",
    }
    # Tier ordering — drives the upgrade (immediate, prorated) vs downgrade
    # (scheduled at renewal) wording in the switch confirmation.
    _TIER_RANK = {"free": 0, "payg": 0, "trial": 0, "hobbyist": 1,
                  "professional": 2, "enterprise": 3}
    # Monthly image allowance per subscription tier (UI copy only; the
    # authoritative allowance lives in the billing.plans catalog).
    _TIER_ALLOWANCE = {"hobbyist": "30,000", "professional": "60,000"}
    # Phase-2 scheduling perk copy per tier (priority band + concurrency cap). UI
    # copy only — the authoritative values live in billing.plans (priority /
    # concurrency_limit). Shown on the Plans cards only when SHOW_SCHEDULING_PERKS.
    _TIER_SCHED_PERK = {
        "free":         "1 job at a time",
        "payg":         "Standard priority · 1 job at a time",
        "hobbyist":     "Standard priority · 1 job at a time",
        "professional": "Priority processing · 2 concurrent jobs",
        "enterprise":   "Highest priority · 4 concurrent jobs",
    }

    @staticmethod
    def _fmt_date(iso):
        """Format a server ISO timestamp as 'Jul 12', or None if unparseable."""
        if not iso:
            return None
        try:
            s = str(iso).replace("Z", "+00:00")
            return datetime.datetime.fromisoformat(s).strftime("%b %d")
        except Exception:
            return None

    def _fetch_balance(self):
        """Return the full balance dict from the billing API, or None on failure.
        Also caches the tier on self._current_tier for the Plans dialog.

        Shape: tier, subscription_tokens, purchased_tokens,
        available_tokens (sum, back-compat), monthly_reset_date,
        subscription_active, grace_until."""
        try:
            resp = self._authed_request("GET", BALANCE_URL, timeout=10)
            if resp.status_code == 200:
                d = resp.json()
                self._current_tier = str(d.get("tier", "") or "payg").lower()
                self._last_balance = d
                return d
        except Exception:
            pass
        return None

    def _format_balance(self, d):
        """Build the (label, tooltip) pair from a balance dict, surfacing the two
        buckets honestly: expiring monthly credits vs. never-expiring purchased."""
        if not d:
            return "🪙  —", ""
        tier = (d.get("tier") or "payg").lower()
        tier_disp = self._TIER_LABELS.get(tier, tier.title())
        sub = int(d.get("subscription_tokens") or 0)
        pur = int(d.get("purchased_tokens") or 0)
        paused = d.get("dunning_state") == "paused"   # failed renewal -> processing blocked
        cr_seg = f" · {pur:,} credits" if pur else ""

        # Billing-UX decision: show expiring monthly IMAGES and never-expiring CREDITS
        # separately on the chip (not one merged total). Backend burns images first.
        lines = []
        if tier == "trial":
            exp = self._fmt_date(d.get("trial_expires_at"))
            # Inline expiry on the chip — the end date drives conversion.
            label = f"🪙  {sub:,} trial" + (f" · ends {exp}" if exp else "") + cr_seg
            lines.append(f"{sub:,} free trial images" + (f", expires {exp}" if exp else ""))
            if pur:
                lines.append(f"{pur:,} credits, never expire")
            lines.append("Subscribe anytime to keep going after your trial.")
            return label, "\n".join(lines)

        # Enterprise seat: the allowance is the TEAM's shared pool, not this person's.
        # Say so plainly — otherwise "60,000 images" reads as a personal balance and a
        # user can't tell why it drops when a colleague uploads. Fields come from
        # billing /balance (org_name/org_seats/is_org_member); older backends omit
        # them, so this branch simply never triggers there.
        if d.get("is_org_member"):
            oname = d.get("org_name") or "your team"
            seats = int(d.get("org_seats") or 0)
            reset = self._fmt_date(d.get("monthly_reset_date"))
            seat_txt = f" · {seats} seats" if seats else ""
            label = f"👥  {sub:,} images" + cr_seg + seat_txt
            lines.append(f"{oname} — {tier_disp} team plan"
                         + (f" ({seats} seats)" if seats else ""))
            reset_txt = f", resets {reset}" if reset else " (resets monthly)"
            lines.append(f"{sub:,} images across the team{reset_txt}")
            if pur:
                lines.append(f"{pur:,} credits, never expire")
            return label, "\n".join(lines)

        # FREE + PAYG on one account. The two pools must stay visibly separate:
        # the included allowance is spent first and renews, purchased credits
        # never expire. A single merged total would hide which half is about to
        # reset, so a user could not tell whether "1,050" was mostly theirs to
        # keep or mostly about to disappear.
        #
        # Branches on the TIER, not on subscription_active. Free sets that flag
        # so the existing reset sweep treats it uniformly, but hanging the whole
        # display off it meant any path that cleared the flag silently collapsed
        # a Free account to "N credits" and told them to subscribe.
        if tier == "free":
            reset = self._fmt_date(d.get("monthly_reset_date"))
            label = f"🪙  {sub:,} images" + cr_seg
            lines.append("Free plan")
            # Deliberately not "this month" / "resets monthly": whether the cycle
            # is 30 days from signup or the same date each month is configurable, and
                # the chip must not assert an answer it does not have.
            lines.append(f"{sub:,} included images left"
                         + (f", renews {reset}" if reset else ""))
            if pur:
                # A legacy balance from before the Free-only launch. Still shown
                # and still spent, because the credits were paid for; we simply
                # no longer sell more.
                lines.append(f"{pur:,} purchased credits, never expire")
                lines.append("Included images are used first.")
            elif SHOW_PAYG:
                lines.append("Buy credits for more. Purchased credits never expire.")
            else:
                # No purchase exists in this build, so pointing at one would send
                # the user hunting for a button that is not there.
                lines.append("Your included images renew each cycle.")
            return label, "\n".join(lines)

        if d.get("subscription_active"):
            cancel  = self._fmt_date(d.get("cancel_at"))
            pending = d.get("pending_tier") or ""
            reset   = self._fmt_date(d.get("monthly_reset_date"))
            label = f"🪙  {sub:,} images" + cr_seg
            if cancel:
                lines.append(f"Plan cancels {cancel}. Reactivate from View Plans.")
            elif pending:
                pdisp = self._TIER_LABELS.get(pending, pending.title())
                lines.append(f"Switching to {pdisp}" + (f" on {reset}." if reset else " at next renewal."))
            elif paused:
                lines.append("Subscription paused. Update your payment method to resume.")
            lines.append(f"{tier_disp} plan")
            reset_txt = f", resets {reset}" if reset else " (resets monthly)"
            lines.append(f"{sub:,} images this month{reset_txt}")
            lines.append(f"{pur:,} credits, never expire")
            lines.append("Monthly images are used first.")
        else:
            label = f"🪙  {pur:,} credits"
            lines.append(f"{pur:,} credits, never expire")
            # Only advertise a subscription while one is actually for sale. The
            # 8 September launch sells Free + PAYG, so telling a user to
            # "subscribe for a monthly allowance" points at a product they
            # cannot buy from any screen in the plugin.
            lines.append("Subscribe for a monthly image allowance."
                         if SHOW_SUBSCRIPTION_TIERS else
                         "Credits are used one per image.")
        return label, "\n".join(lines)

    def _refresh_balance(self):
        """Fetch the token balance in a background thread and update the strip."""
        def _work():
            bal = self._fetch_balance()
            label, tip = self._format_balance(bal)
            # Phase-1B: storage lives in the profile dropdown (calm state) to keep the
            # header chip clean. It only escalates onto the main chip when the user is
            # NEAR the limit (>=90%) or over it — that's the only time it needs to be in
            # their face. Fail-soft: older deploys without the endpoint just show
            # images/credits exactly as before, and the dropdown line stays "…".
            usage = self._get_storage_usage()
            storage_line = ""      # dropdown text
            storage_over = False   # drives the ⚠ escalation
            if usage:
                used_gb  = usage.get("used_gb", 0)
                quota_gb = usage.get("quota_gb", 0)
                over     = usage.get("over_quota")
                pct      = (used_gb / quota_gb * 100.0) if quota_gb else 0.0
                storage_over = bool(over)
                used_disp = f"{used_gb*1024:.0f} MB" if used_gb < 1.0 else f"{used_gb:.2f} GB"
                # Org seat: this is the TEAM's shared storage, not just this user's —
                # label it so a member isn't puzzled by usage they didn't create.
                pool_lbl = "Team storage" if usage.get("is_org_pool") else "Storage"
                # A tiny fraction (e.g. 60 MB of 1000 GB) rounds to "(0.0%)" — that
                # conveys nothing and reads as a broken counter. Only show the percent
                # once it's actually meaningful; "of X GB" already says "barely anything".
                pct_txt = f" ({pct:.1f}%)" if pct >= 0.1 else ""
                storage_line = f"{pool_lbl}: {used_disp} of {quota_gb:.0f} GB{pct_txt}"
                # Escalate to the header chip only when it matters (>=90% or over).
                if over or pct >= 90.0:
                    label = f"{label} · {used_disp}/{quota_gb:.0f} GB" + (" ⚠" if over else "")
            QtCore.QMetaObject.invokeMethod(self, "_set_balance_ui",
                QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, label), QtCore.Q_ARG(str, tip))
            QtCore.QMetaObject.invokeMethod(self, "_set_storage_ui",
                QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, storage_line),
                QtCore.Q_ARG(bool, storage_over))
            # Show/hide the on-demand "Move my files to the team" menu item — visible
            # whenever an org member still has un-migrated personal files.
            QtCore.QMetaObject.invokeMethod(self, "_set_migration_ui",
                QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(bool, bool(bal and bal.get("org_has_personal_files"))))
            # Trial-request state, but only for PAYG users — anyone on a plan can't
            # receive a trial, so the call would be wasted on every refresh.
            tier = (bal or {}).get("tier") or "payg"
            trial_state = (self._get_trial_status()
                           if str(tier).lower() == "payg" else "none")
            QtCore.QMetaObject.invokeMethod(self, "_set_trial_request_ui",
                QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, trial_state))
            # Newly-seated org member with pre-existing files → offer the one-time
            # migrate-or-keep choice (backend flag clears once they decide). Older
            # backends omit the field, so this simply never fires there.
            if bal and bal.get("org_migration_pending"):
                QtCore.QMetaObject.invokeMethod(self, "_prompt_file_migration",
                    QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, bal.get("org_name") or "your team"))
        threading.Thread(target=_work, daemon=True).start()

    # ── Org file migration: one-time migrate-or-keep prompt (Phase-1B) ──────────
    @QtCore.pyqtSlot(str)
    def _prompt_file_migration(self, org_name: str):
        """When a user is newly seated into an org and still has pre-existing personal
        files, ask once whether to move them into the shared team pool or keep them
        personal. Shown at most once per session; the backend clears the pending flag
        once a real decision is submitted, so a decided user is never asked again.
        Dismissing (window close) records nothing — they'll be asked next session."""
        if getattr(self, "_migration_prompt_seen", False):
            return
        self._migration_prompt_seen = True

        dlg = ThemedDialog(self, "Welcome to the team",
                           f"You've joined {org_name}.")
        msg = QtWidgets.QLabel(
            "What should happen to the files you uploaded <b>before</b> joining?<br><br>"
            "• <b>Move to team storage</b>: they join the shared team pool and are kept "
            "for 1 year (Enterprise retention).<br>"
            "• <b>Keep them personal</b>: they stay yours and keep your current retention.<br><br>"
            "Either way, anything you upload <b>from now on</b> is shared with the team.")
        msg.setTextFormat(QtCore.Qt.RichText)
        msg.setWordWrap(True)
        msg.setMinimumWidth(340)
        msg.setStyleSheet("font-size: 13px; color: #57534e; background: transparent; border: none;")
        dlg.body.addWidget(msg)

        result = {"decision": None}
        foot = QtWidgets.QHBoxLayout()
        foot.addStretch(1)
        keep = self._styled_button("Keep them personal", primary=False, min_height=34)
        move = self._styled_button("Move to team storage", primary=True, accent="orange", min_height=34)
        keep.clicked.connect(lambda: (result.__setitem__("decision", "keep"), dlg.accept()))
        move.clicked.connect(lambda: (result.__setitem__("decision", "migrate"), dlg.accept()))
        foot.addWidget(keep)
        foot.addWidget(move)
        dlg.body.addSpacing(6)
        dlg.body.addLayout(foot)

        dlg.adjustSize()
        dlg.exec_()
        if result["decision"]:
            self._submit_migration_decision(result["decision"])

    def _submit_migration_decision(self, decision: str):
        """POST the user's migrate-or-keep choice; refresh the balance on success so
        the pending flag clears and the prompt won't reappear."""
        def _work():
            try:
                resp = self._authed_request("POST", STORAGE_MIGRATE_URL,
                                            json={"decision": decision}, timeout=30)
                if resp.status_code == 200:
                    n = int(resp.json().get("migrated_files") or 0)
                    QtCore.QMetaObject.invokeMethod(self, "_migration_done",
                        QtCore.Qt.QueuedConnection,
                        QtCore.Q_ARG(str, decision), QtCore.Q_ARG(int, n))
                    return
                msg = f"Could not save your choice (HTTP {resp.status_code})."
            except Exception as e:
                msg = f"Couldn't save your choice: {e}"
            QtCore.QMetaObject.invokeMethod(self, "_checkout_failed",
                QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, msg))
        threading.Thread(target=_work, daemon=True).start()

    @QtCore.pyqtSlot(str, int)
    def _migration_done(self, decision: str, migrated_files: int):
        self._refresh_balance()   # clears org_migration_pending on the backend's next read
        if decision == "migrate":
            self._themed_notice(
                "Files moved to the team",
                f"{migrated_files:,} file{'s' if migrated_files != 1 else ''} now "
                "belong to your team's shared storage and are kept for 1 year.",
                accent="green", button="Done", primary_button=True)
        else:
            self._themed_notice(
                "Kept personal",
                "Your existing files stay yours and keep their current retention. "
                "Anything you upload from now on is shared with the team.",
                accent="green", button="Done")

    def _migrate_files_on_demand(self):
        """Account-menu action for an org member who dismissed the join prompt (or chose
        'keep'): move their existing personal files into the team pool at any time. Only
        offers 'migrate' — the on-demand intent is explicitly to share (migrating is
        one-way; already-shared files stay shared)."""
        bal = getattr(self, "_last_balance", None) or {}
        org = bal.get("org_name") or "your team"
        if self._themed_confirm(
                "Move your files to the team?",
                f"Your existing personal files will move into {org}'s shared storage and be "
                "kept for 1 year (Enterprise retention). Files you've already shared stay "
                "shared. This can't be undone.",
                confirm_text="Move to team storage", cancel_text="Not now", accent="orange"):
            self._submit_migration_decision("migrate")

    def _start_balance_watch(self, timeout=180, interval=4):
        """After a purchase, poll /balance until it changes (or times out), so the
        strip updates the instant the Stripe webhook credits the account. Only one
        watcher runs at a time."""
        if getattr(self, "_balance_watching", False):
            return
        self._balance_watching = True

        def _total(d):
            if not d:
                return None
            return int(d.get("available_tokens")
                       if d.get("available_tokens") is not None
                       else (d.get("subscription_tokens") or 0) + (d.get("purchased_tokens") or 0))

        def _work():
            try:
                base_total = _total(self._fetch_balance())
                deadline = time.time() + timeout
                while time.time() < deadline:
                    time.sleep(interval)
                    cur = self._fetch_balance()
                    if not cur:
                        continue
                    label, tip = self._format_balance(cur)
                    QtCore.QMetaObject.invokeMethod(self, "_set_balance_ui",
                        QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, label), QtCore.Q_ARG(str, tip))
                    if base_total is not None and _total(cur) != base_total:
                        break
            finally:
                self._balance_watching = False
        threading.Thread(target=_work, daemon=True).start()

    def changeEvent(self, e):
        """Refresh the balance whenever the plugin window regains focus — e.g. the
        user returns from the Stripe Checkout browser tab after paying."""
        super().changeEvent(e)
        if e.type() == QtCore.QEvent.ActivationChange and self.isActiveWindow():
            if getattr(self, "_billing_strip_built", False):
                self._refresh_balance()

    @QtCore.pyqtSlot(str, str)
    def _set_balance_ui(self, text: str, tooltip: str):
        # Balance now lives in the profile dropdown (a disabled QAction); keep the
        # old strip label updated too if it still exists (back-compat / fallback).
        act = getattr(self, "_balance_action", None)
        if act is not None:
            act.setText(text)
            act.setToolTip(tooltip)
        if hasattr(self, "label_token_balance"):
            self.label_token_balance.setText(text)
            self.label_token_balance.setToolTip(tooltip)
        rc = getattr(self, "_results_balance_chip", None)   # the Results-header chip
        if rc is not None:
            rc.setText(text)
            rc.setToolTip(tooltip)

    def _make_storage_caption(self, menu):
        """A non-interactive Storage status caption embedded in the profile menu.

        Uses a styled QLabel inside a QWidgetAction (not a disabled QAction) so it
        reads as a small status HEADING — distinct from the actionable, greyable
        'Free up storage' item below it. Returns the QLabel (updated by _set_storage_ui)."""
        lbl = QtWidgets.QLabel("Storage: …")
        # Give the caption a real min-width so the QMenu grows to fit the full line
        # (e.g. "⚠ Storage: 23.90 GB of 25 GB (95.6%)") — without it the menu sizes
        # to the shorter items and clips the percentage.
        lbl.setMinimumWidth(238)
        lbl.setStyleSheet(
            "QLabel { color:#78716c; font-size:11px; font-weight:600;"
            "  padding:4px 12px 2px 12px; background:transparent; }")
        wa = QtWidgets.QWidgetAction(menu)
        wa.setDefaultWidget(lbl)
        menu.addAction(wa)
        return lbl

    @QtCore.pyqtSlot(str, bool)
    def _set_storage_ui(self, line: str, over: bool):
        """Update the read-only Storage caption in the profile dropdown(s). Empty line =
        endpoint unavailable -> leave the placeholder. When over-quota, colour the
        caption red and ENABLE 'Free up storage'; otherwise the action stays greyed
        out so the user can't hit a dead-end 'nothing to remove' popup."""
        text = line or "Storage: —"
        if line and over:
            text = "⚠ " + line
        for attr in ("_storage_caption", "_results_storage_caption"):
            lbl = getattr(self, attr, None)
            if lbl is not None:
                lbl.setText(text)
                # Red + bold when over quota; calm grey otherwise.
                color = "#b91c1c" if (line and over) else "#78716c"
                weight = "700" if (line and over) else "600"
                lbl.setStyleSheet(
                    f"QLabel {{ color:{color}; font-size:11px; font-weight:{weight};"
                    "  padding:4px 12px 2px 12px; background:transparent; }")
        # 'Free up storage' is only meaningful when the user is over quota.
        for attr in ("_freeup_action", "_results_freeup_action"):
            act = getattr(self, attr, None)
            if act is not None:
                act.setEnabled(bool(over))
                act.setToolTip("" if over else "Only available when you're over your storage limit")

    @QtCore.pyqtSlot(bool)
    def _set_migration_ui(self, has_personal_files: bool):
        """Show 'Move my files to the team' in the profile menu(s) only for an org
        member who still has un-migrated personal files — so the migrate choice is
        reachable on demand, not just via the one-time join prompt."""
        for attr in ("_migrate_action", "_results_migrate_action"):
            act = getattr(self, attr, None)
            if act is not None:
                act.setVisible(bool(has_personal_files))

    def _is_org_member(self):
        """True if the signed-in user is a seat on an Enterprise org (their billing is
        the shared org pool, not a personal plan)."""
        return bool((getattr(self, "_last_balance", None) or {}).get("is_org_member"))

    def _team_plan_notice(self):
        """Org members bill through their organisation's shared pool. A personal
        subscribe or credit top-up would create a charge on THEIR card that's invisible
        in-product (the balance shows the team pool, not a personal plan) — exactly the
        trap we block at join time. So the plans / buy flows show this instead."""
        bal = getattr(self, "_last_balance", None) or {}
        org = bal.get("org_name") or "your team"
        self._themed_notice(
            "You're on a team plan",
            f"Your account is part of {org}'s Enterprise team. Images and storage are "
            "shared across the team and billed to your organisation. There is no personal "
            "plan or credits to manage here.\n\n"
            "Need more capacity? Ask your team's administrator.",
            icon="", accent="orange", button="Got it")

    def _buy_tokens(self):
        # Belt and braces. Every entry point is hidden when SHOW_PAYG is off, so
        # reaching here would mean one was missed; refuse rather than open a
        # checkout for a product this build does not sell.
        if not SHOW_PAYG:
            return
        if self._is_org_member():
            self._team_plan_notice()
            return
        self._open_checkout(CHECKOUT_TOKENS_URL, None)

    def _switch_plan(self, tier, plan):
        """Switch the active subscription to another tier via our backend
        (upgrade = immediate, downgrade = at next renewal)."""
        disp = self._TIER_LABELS.get(tier, tier.title())
        billing = "Annual" if plan == "annual" else "Monthly"

        # ── Confirm with consequences ──────────────────────────────────────
        # A switch moves real money / changes state, and upgrade vs downgrade
        # behave differently — so we confirm with tailored copy, not a bare
        # "Are you sure?". (Credit top-ups skip this: Stripe Checkout is its
        # own confirmation surface.)
        bal  = getattr(self, "_last_balance", None) or {}
        cur  = (getattr(self, "_current_tier", None) or bal.get("tier") or "payg").lower()
        reactivating = bool(self._fmt_date(bal.get("cancel_at")))
        react_line = " This also reactivates your subscription." if reactivating else ""
        allow = self._TIER_ALLOWANCE.get(tier)

        if self._TIER_RANK.get(tier, 0) >= self._TIER_RANK.get(cur, 0):
            # Upgrade — effective immediately, prorated charge today.
            allow_line = (f" Your new allowance of {allow} images/month is available "
                          "immediately." if allow else "")
            ok = self._themed_confirm(
                f"Upgrade to {disp}?",
                f"You'll switch to {disp} ({billing} billing) now. Your card on file "
                "will be charged a prorated amount today for the rest of this billing "
                "period." + allow_line + react_line,
                confirm_text="Confirm upgrade", cancel_text="Keep current plan",
                accent="green")
        else:
            # Downgrade — scheduled at cycle end, no charge today, undoable.
            reset = self._fmt_date(bal.get("monthly_reset_date"))
            when  = f"on {reset}" if reset else "at your next renewal"
            undo_when = reset or "your next renewal"
            ok = self._themed_confirm(
                f"Switch to {disp}?",
                f"You'll move to {disp} ({billing} billing) {when}. No charge today — "
                "you keep your current plan and images until then. You can undo this "
                f"anytime before {undo_when}." + react_line,
                confirm_text="Schedule switch", cancel_text="Keep current plan",
                accent="orange")
        if not ok:
            return

        def _work():
            try:
                resp = self._authed_request("POST", SWITCH_URL,
                                            json={"tier": tier, "plan": plan}, timeout=25)
                if resp.status_code == 200:
                    eff = resp.json().get("effective", "")
                    QtCore.QMetaObject.invokeMethod(self, "_switch_done",
                        QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, disp),
                        QtCore.Q_ARG(str, eff), QtCore.Q_ARG(str, billing))
                    return
                try:
                    detail = resp.json().get("detail")
                except Exception:
                    detail = None
                msg = (detail if isinstance(detail, str) else None) or \
                      f"Could not switch plan (HTTP {resp.status_code})."
            except Exception as e:
                msg = f"Switch error: {e}"
            QtCore.QMetaObject.invokeMethod(self, "_checkout_failed",
                QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, msg))
        threading.Thread(target=_work, daemon=True).start()

    @QtCore.pyqtSlot(str, str, str)
    def _switch_done(self, disp, eff, billing):
        self._refresh_balance()
        self._themed_notice(
            "Plan switched",
            f"You've switched to {disp} ({billing} billing). This is effective {eff}.",
            accent="green", button="Done", primary_button=True)

    def _cancel_subscription(self):
        """Cancel the subscription at the end of the paid term (our backend; keeps
        access until period end, no refund). Confirm first."""
        bal = getattr(self, "_last_balance", None) or {}
        pending = bal.get("pending_tier") or ""
        extra = ""
        if pending:
            pdisp = self._TIER_LABELS.get(pending, pending.title())
            extra = f"\n\nThis also cancels your scheduled switch to {pdisp}."
        ok = self._themed_confirm(
            "Cancel subscription?",
            "Your plan stays active until the end of your current paid term, then "
            "drops to Pay As You Go. No refund for the current term." + extra +
            "\n\nYou can reactivate any time before it ends.",
            confirm_text="Cancel subscription", cancel_text="Keep plan", accent="red")
        if not ok:
            return
        def _work():
            try:
                resp = self._authed_request("POST", SUB_CANCEL_URL, json={}, timeout=25)
                if resp.status_code == 200:
                    when = resp.json().get("cancel_at") or ""
                    QtCore.QMetaObject.invokeMethod(self, "_cancel_done",
                        QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, when))
                    return
                try:
                    detail = resp.json().get("detail")
                except Exception:
                    detail = None
                msg = (detail if isinstance(detail, str) else None) or \
                      f"Could not cancel (HTTP {resp.status_code})."
            except Exception as e:
                msg = f"Cancel error: {e}"
            QtCore.QMetaObject.invokeMethod(self, "_checkout_failed",
                QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, msg))
        threading.Thread(target=_work, daemon=True).start()

    @QtCore.pyqtSlot(str)
    def _cancel_done(self, when):
        self._refresh_balance()
        tail = f" on {when}" if when else " at the end of your term"
        self._themed_notice(
            "Subscription cancelled",
            f"Your plan will end{tail} and switch to Pay As You Go. "
            "You keep full access until then. Reactivate any time before it ends.",
            icon="", accent="orange", button="Close")

    def _reactivate_subscription(self):
        """Undo a pending cancellation (keep the current plan) via our backend."""
        def _work():
            try:
                resp = self._authed_request("POST", SUB_REACTIVATE_URL, json={}, timeout=25)
                if resp.status_code == 200:
                    QtCore.QMetaObject.invokeMethod(self, "_reactivate_done",
                        QtCore.Qt.QueuedConnection)
                    return
                try:
                    detail = resp.json().get("detail")
                except Exception:
                    detail = None
                msg = (detail if isinstance(detail, str) else None) or \
                      f"Could not reactivate (HTTP {resp.status_code})."
            except Exception as e:
                msg = f"Reactivate error: {e}"
            QtCore.QMetaObject.invokeMethod(self, "_checkout_failed",
                QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, msg))
        threading.Thread(target=_work, daemon=True).start()

    @QtCore.pyqtSlot()
    def _reactivate_done(self):
        self._refresh_balance()
        self._themed_notice(
            "Subscription reactivated",
            "Your plan will continue as normal. The scheduled cancellation has been "
            "removed.",
            accent="green", button="Done", primary_button=True)

    def _cancel_pending_switch(self, keep_disp):
        """Undo a scheduled plan switch and stay on the current plan (our backend)."""
        def _work():
            try:
                resp = self._authed_request("POST", SUB_CANCEL_SWITCH_URL, json={}, timeout=25)
                if resp.status_code == 200:
                    QtCore.QMetaObject.invokeMethod(self, "_cancel_switch_done",
                        QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, keep_disp))
                    return
                try:
                    detail = resp.json().get("detail")
                except Exception:
                    detail = None
                msg = (detail if isinstance(detail, str) else None) or \
                      f"Could not cancel the switch (HTTP {resp.status_code})."
            except Exception as e:
                msg = f"Cancel-switch error: {e}"
            QtCore.QMetaObject.invokeMethod(self, "_checkout_failed",
                QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, msg))
        threading.Thread(target=_work, daemon=True).start()

    @QtCore.pyqtSlot(str)
    def _cancel_switch_done(self, keep_disp):
        self._refresh_balance()
        self._themed_notice(
            "Switch cancelled",
            f"The scheduled plan switch has been cancelled. You'll stay on "
            f"{keep_disp}.",
            accent="green", button="Done", primary_button=True)

    def _open_checkout(self, url: str, body):
        """Create a Stripe Checkout session and open it in the browser."""
        def _work():
            try:
                resp = self._authed_request("POST", url, json=(body or {}), timeout=15)
                if resp.status_code == 200:
                    checkout_url = resp.json().get("checkout_url")
                    if checkout_url:
                        QtCore.QMetaObject.invokeMethod(self, "_open_browser",
                            QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, checkout_url))
                        return
                # Surface the server's explanation (e.g. 409 "already subscribed").
                try:
                    detail = resp.json().get("detail")
                except Exception:
                    detail = None
                msg = detail or f"Could not start checkout (HTTP {resp.status_code})."
            except Exception as e:
                msg = f"Checkout error: {e}"
            QtCore.QMetaObject.invokeMethod(self, "_checkout_failed",
                QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, msg))
        threading.Thread(target=_work, daemon=True).start()

    def _themed_notice(self, title, message, icon=None, accent="orange", button="OK",
                       primary_button=False):
        """Show a themed, frameless notice dialog (replaces native QMessageBox).
        icon: None -> auto monochrome mark from accent (green ✓ / red ✗); "" -> no badge.
        primary_button: False (default) -> subtle grey Close/OK, since dismissing an
        alert isn't a primary action; True -> solid accent button, for notices that
        cap off a real action the user just took (purchase, mosaic, report)."""
        badge = {
            "orange": ("#fff4ed", "#fed7aa"),
            "green":  ("#ecfdf5", "#a7f3d0"),
            "red":    ("#fff1f2", "#fecdd3"),
        }.get(accent, ("#fff4ed", "#fed7aa"))
        if icon is None:
            icon = {"green": "✓", "red": "✗"}.get(accent, "")
        mark = {"green": "#047857", "red": "#dc2626"}.get(accent, "#b45309")

        dlg = ThemedDialog(self, title)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(14)

        if icon:
            ic = QtWidgets.QLabel(icon)
            ic.setFixedSize(48, 48)
            ic.setAlignment(QtCore.Qt.AlignCenter)
            ic.setStyleSheet(
                f"font-size: 22px; font-weight: 700; color: {mark};"
                f" background-color: {badge[0]};"
                f" border: 1px solid {badge[1]}; border-radius: 24px;")
            row.addWidget(ic, 0, QtCore.Qt.AlignTop)

        msg = QtWidgets.QLabel(message)
        msg.setWordWrap(True)
        msg.setMinimumWidth(300)
        msg.setStyleSheet("font-size: 13px; color: #57534e; background: transparent; border: none;")
        row.addWidget(msg, 1)
        dlg.body.addLayout(row)

        foot = QtWidgets.QHBoxLayout()
        foot.addStretch(1)
        ok = self._styled_button(button, primary=primary_button, accent=accent, min_height=34)
        ok.clicked.connect(dlg.accept)
        foot.addWidget(ok)
        dlg.body.addSpacing(6)
        dlg.body.addLayout(foot)

        dlg.adjustSize()
        dlg.exec_()

    @QtCore.pyqtSlot(str)
    def _open_browser(self, url: str):
        QDesktopServices.openUrl(QUrl(url))
        # Watch for the webhook credit so the strip updates without a manual refresh.
        if getattr(self, "_billing_strip_built", False):
            self._start_balance_watch()
        self._themed_notice(
            "Complete your purchase",
            "We've opened Stripe Checkout in your browser.\n\n"
            "After paying, return here and your credit balance will update automatically "
            "within a few seconds.",
            icon="", accent="orange")

    @QtCore.pyqtSlot(str)
    def _checkout_failed(self, msg: str):
        self._themed_notice("Something went wrong", msg,
                            accent="red", button="Close")

    def _open_portal(self, flow=None):
        """Open the Stripe Customer Portal (manage card / cancel / switch).
        flow='switch' deep-links straight to the plan-change screen."""
        body = {"flow": flow} if flow else {}
        def _work():
            try:
                resp = self._authed_request("POST", PORTAL_URL, json=body, timeout=15)
                if resp.status_code == 200:
                    portal_url = resp.json().get("portal_url")
                    if portal_url:
                        QtCore.QMetaObject.invokeMethod(self, "_open_browser",
                            QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, portal_url))
                        return
                elif resp.status_code == 404:
                    msg = "No billing account yet. Buy credits or subscribe first."
                    QtCore.QMetaObject.invokeMethod(self, "_checkout_failed",
                        QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, msg))
                    return
                msg = f"Could not open billing portal (HTTP {resp.status_code})."
            except Exception as e:
                msg = f"Portal error: {e}"
            QtCore.QMetaObject.invokeMethod(self, "_checkout_failed",
                QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, msg))
        threading.Thread(target=_work, daemon=True).start()

    # ── Themed popup helpers ────────────────────────────────────────────
    def _styled_button(self, text, primary=True, accent="orange", min_height=36):
        """Return a QPushButton styled to match the plugin's design language."""
        btn = QtWidgets.QPushButton(text)
        btn.setMinimumHeight(min_height)
        btn.setCursor(QtCore.Qt.PointingHandCursor)
        if primary:
            top, bot, edge, press = {
                "orange": ("#f4691c", "#ea580c", "#c2410c", "#c2410c"),
                "green":  ("#14c98e", "#10b981", "#059669", "#047857"),
                "red":    ("#ef4444", "#dc2626", "#b91c1c", "#991b1b"),
            }.get(accent, ("#f4691c", "#ea580c", "#c2410c", "#c2410c"))
            btn.setStyleSheet(
                "QPushButton {"
                f"  background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {top}, stop:1 {bot});"
                "  color: #ffffff; border: none;"
                f"  border-bottom: 2px solid {edge};"
                "  border-radius: 6px; padding: 8px 16px;"
                "  font-size: 13px; font-weight: 700; }"
                "QPushButton:hover {"
                f"  background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 {bot}, stop:1 {edge}); }}"
                f"QPushButton:pressed {{ background: {press}; border-bottom: none; padding-top: 10px; }}"
            )
        else:
            btn.setStyleSheet(
                "QPushButton {"
                "  background-color: #ffffff; color: #57534e;"
                "  border: 1px solid #e2ddd8; border-radius: 6px;"
                "  padding: 8px 16px; font-size: 13px; font-weight: 600; }"
                "QPushButton:hover { background-color: #faf7f2; color: #1a1612; border-color: #ccbda8; }"
                "QPushButton:pressed { background-color: #f3ece3; }"
            )
        return btn

    def _plan_card(self, name, tagline, price, price_suffix, features, accent=False, badge=None):
        """Build a pricing tier card. Returns (frame, button_box_layout)."""
        frame = QtWidgets.QFrame()
        frame.setAttribute(QtCore.Qt.WA_Hover, True)   # ensure :hover repaints reliably
        if accent:
            # Featured / current plan: keep the orange outline as its meaning-bearing
            # marker; hover only deepens the tint slightly (never changes the border).
            frame.setStyleSheet(
                "QFrame { background-color: #fffaf4; border: 2px solid #ea580c;"
                "  border-radius: 14px; }"
                "QFrame:hover { background-color: #fff4ea; }"
                "QLabel { background: transparent; border: none; }")
        else:
            # Neutral hover (NOT orange — orange stays reserved for the current/featured
            # card): warm-grey border + faint lift so the card feels interactive.
            frame.setStyleSheet(
                "QFrame { background-color: #ffffff; border: 1px solid #ece4d8;"
                "  border-bottom: 2px solid #e3d6c4; border-radius: 14px; }"
                "QFrame:hover { border-color: #ccbda8; background-color: #fffdfa; }"
                "QLabel { background: transparent; border: none; }")
        frame.setMinimumWidth(210)
        v = QtWidgets.QVBoxLayout(frame)
        v.setContentsMargins(16, 12, 16, 16)
        v.setSpacing(4)

        # badge row (always present so titles align across cards; empty = invisible)
        brow = QtWidgets.QHBoxLayout()
        brow.addStretch(1)
        blbl = QtWidgets.QLabel(badge or "")
        if badge:
            blbl.setStyleSheet(
                "font-size: 9px; font-weight: 800; letter-spacing: 1px; color: #ffffff;"
                " background-color: #ea580c; padding: 2px 8px; border-radius: 8px;")
        else:
            blbl.setStyleSheet("font-size: 9px; padding: 2px 8px;")  # reserves equal height
        brow.addWidget(blbl)
        v.addLayout(brow)

        # title on its OWN full-width row -> never truncated, whatever the length
        nlbl = QtWidgets.QLabel(name)
        nlbl.setStyleSheet("font-size: 12px; font-weight: 800; letter-spacing: 1px; color: #ea580c;")
        v.addWidget(nlbl)

        tag = QtWidgets.QLabel(tagline)
        tag.setStyleSheet("font-size: 11px; color: #a8a29e;")
        v.addWidget(tag)

        # price row
        prow = QtWidgets.QHBoxLayout()
        prow.setSpacing(2)
        prow.setAlignment(QtCore.Qt.AlignLeft)
        plbl = QtWidgets.QLabel(price)
        plbl.setStyleSheet("font-size: 26px; font-weight: 800; color: #1a1612;")
        prow.addWidget(plbl)
        if price_suffix:
            slbl = QtWidgets.QLabel(price_suffix)
            slbl.setStyleSheet("font-size: 12px; color: #78716c; padding-bottom: 4px;")
            prow.addWidget(slbl, 0, QtCore.Qt.AlignBottom)
        v.addSpacing(6)
        v.addLayout(prow)
        v.addSpacing(8)

        for f in features:
            fl = QtWidgets.QLabel(f"✓  {f}")
            fl.setStyleSheet("font-size: 12px; color: #57534e;")
            fl.setWordWrap(True)
            v.addWidget(fl)

        v.addSpacing(12)
        v.addStretch(1)
        btn_box = QtWidgets.QVBoxLayout()
        btn_box.setSpacing(6)
        v.addLayout(btn_box)
        return frame, btn_box

    def _add_sub_card_buttons(self, dlg, box, card_tier, current_tier, is_subscriber):
        """Wire the action buttons on a subscription card (Hobbyist / Professional).

        - current plan         -> "Manage / Switch plan" (portal)
        - already on a sub      -> "Switch via Manage billing" (portal; a fresh
                                   checkout would 409 / double-charge)
        - not subscribed (payg/trial) -> Monthly + Annual checkout with the tier
        """
        if current_tier == card_tier:
            bal = getattr(self, "_last_balance", None) or {}
            cancel = self._fmt_date(bal.get("cancel_at"))
            pending = bal.get("pending_tier") or ""
            cur_disp = self._TIER_LABELS.get(current_tier, current_tier.title())
            switching = bool(pending) and pending != card_tier and not cancel
            if cancel:
                lbl = QtWidgets.QLabel(f"Cancels {cancel}")
                lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #b45309;")
            elif switching:
                pdisp = self._TIER_LABELS.get(pending, pending.title())
                lbl = QtWidgets.QLabel(f"Switching to {pdisp}")
                lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #b45309;")
            else:
                lbl = QtWidgets.QLabel("✓  Your active plan")
                lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #047857;")
            lbl.setAlignment(QtCore.Qt.AlignCenter)
            box.addWidget(lbl)
            # We own the lifecycle here (the Stripe portal can't cancel a schedule-
            # managed sub). Switching to OTHER tiers is on their cards; card + invoices
            # live under "Manage billing & saved card".
            if cancel:
                react = self._styled_button("Reactivate plan", primary=True)
                react.clicked.connect(lambda: (dlg.accept(), self._reactivate_subscription()))
                box.addWidget(react)
                hint_txt = "Keeps your plan. Switch from another card; card & invoices in Manage billing."
            elif switching:
                # A downgrade is scheduled (auto-applies at renewal). Two valid intents:
                #   • changed my mind  -> Keep current plan (undo the switch)  [primary]
                #   • quit entirely    -> Cancel subscription -> Pay As You Go [link]
                # Cancelling is reachable in one click but labelled by its OUTCOME so it
                # isn't mistaken for "cancel the switch".
                keep = self._styled_button(f"Keep {cur_disp}", primary=True)
                keep.clicked.connect(lambda _c=False, d=cur_disp: (dlg.accept(), self._cancel_pending_switch(d)))
                box.addWidget(keep)
                canc = QtWidgets.QPushButton("Cancel subscription → Pay As You Go")
                canc.setCursor(QtCore.Qt.PointingHandCursor)
                canc.setStyleSheet(
                    "QPushButton { background: transparent; color: #a8a29e; border: none;"
                    "  font-size: 11px; text-decoration: underline; padding: 2px; }"
                    "QPushButton:hover { color: #b45309; }")
                canc.clicked.connect(lambda: (dlg.accept(), self._cancel_subscription()))
                box.addWidget(canc)
                hint_txt = (f"Switches to {pdisp} at renewal. “Keep {cur_disp}” undoes it; "
                            f"“Cancel” ends the subscription entirely.")
            else:
                # A faint gray text link, not a button, so it's hard to hit by
                # accident but still reachable. Reveals on hover.
                canc = QtWidgets.QPushButton("Cancel plan")
                canc.setCursor(QtCore.Qt.PointingHandCursor)
                canc.setStyleSheet(
                    "QPushButton { background: transparent; color: #bcb3a7; border: none;"
                    "  font-size: 11px; padding: 2px; }"
                    "QPushButton:hover { color: #b45309; text-decoration: underline; }")
                canc.clicked.connect(lambda: (dlg.accept(), self._cancel_subscription()))
                box.addWidget(canc)
                hint_txt = "Switch from another card; card & invoices in Manage billing."
            hint = QtWidgets.QLabel(hint_txt)
            hint.setStyleSheet("font-size: 10px; color: #a8a29e;")
            hint.setAlignment(QtCore.Qt.AlignCenter)
            hint.setWordWrap(True)
            box.addWidget(hint)
        elif is_subscriber:
            bal = getattr(self, "_last_balance", None) or {}
            cancel = self._fmt_date(bal.get("cancel_at"))
            pending = bal.get("pending_tier") or ""
            if pending == card_tier and not cancel:
                # A switch to THIS tier is already scheduled — don't offer it again
                # (re-clicking is harmless but looks like nothing's queued).
                # NOTE: a pending CANCELLATION voids any queued switch (the sub ends,
                # it never renews), so when `cancel` is set we fall through and re-show
                # the buttons as a reactivate-by-switch path.
                lbl = QtWidgets.QLabel("Switch scheduled for next renewal")
                lbl.setStyleSheet("font-size: 12px; font-weight: 700; color: #b45309;")
                lbl.setAlignment(QtCore.Qt.AlignCenter)
                lbl.setWordWrap(True)
                box.addWidget(lbl)
                hint = QtWidgets.QLabel("Manage it from your current plan card")
                hint.setStyleSheet("font-size: 10px; color: #a8a29e;")
                hint.setAlignment(QtCore.Qt.AlignCenter)
                hint.setWordWrap(True)
                box.addWidget(hint)
            else:
                # Switch to THIS tier via our backend (upgrade now / downgrade at renewal).
                mo = self._styled_button("Switch to Monthly", primary=True)
                mo.clicked.connect(lambda _c=False, t=card_tier: (dlg.accept(), self._switch_plan(t, "monthly")))
                yr = self._styled_button("Annual", primary=False)
                yr.clicked.connect(lambda _c=False, t=card_tier: (dlg.accept(), self._switch_plan(t, "annual")))
                box.addWidget(mo)
                box.addWidget(yr)
                if cancel:
                    # Plan is set to cancel — switching here un-cancels and moves you.
                    rhint = QtWidgets.QLabel("Switching also reactivates your plan")
                    rhint.setStyleSheet("font-size: 10px; color: #a8a29e;")
                    rhint.setAlignment(QtCore.Qt.AlignCenter)
                    rhint.setWordWrap(True)
                    box.addWidget(rhint)
        else:
            mo = self._styled_button("Subscribe Monthly", primary=True)
            # NOTE: QPushButton.clicked emits a `checked` bool; the leading
            # `_checked` param absorbs it so PyQt can't overwrite `t` (else the
            # body sends tier=False -> billing 422).
            mo.clicked.connect(lambda _checked=False, t=card_tier: (dlg.accept(),
                self._open_checkout(CHECKOUT_SUB_URL, {"tier": t, "plan": "monthly"})))
            yr = self._styled_button("Annual", primary=False)
            yr.clicked.connect(lambda _checked=False, t=card_tier: (dlg.accept(),
                self._open_checkout(CHECKOUT_SUB_URL, {"tier": t, "plan": "annual"})))
            box.addWidget(mo)
            box.addWidget(yr)

    def _show_plans(self):
        # Org members bill through the shared team pool — never show them personal
        # subscribe / buy options (that would create an invisible personal charge).
        if self._is_org_member():
            self._team_plan_notice()
            return
        # NOTE: the prices/allowances below are PLACEHOLDERS pending client
        # confirmation. The authoritative charge is the Stripe price wired into the
        # billing.plans catalog — update these labels at the pricing cutover only.
        tier = getattr(self, "_current_tier", "payg")
        is_subscriber = tier in ("hobbyist", "professional")
        dlg = ThemedDialog(self, "Choose your plan", "1 credit = 1 image")

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(12)

        def badge_for(t, default=None):
            return "CURRENT" if tier == t else default

        # Per-tier feature bullets. The scheduling perk (priority + concurrency) is
        # appended only when SHOW_SCHEDULING_PERKS, so we never advertise a perk the
        # backend isn't enforcing yet. "Priority processing" is NOT a standalone base
        # bullet — it's carried by the gated perk line to avoid a duplicate.
        def feats(tier_key, base):
            return base + [self._TIER_SCHED_PERK[tier_key]] if SHOW_SCHEDULING_PERKS else base

        # FREE — what every new account starts on. Deliberately vague about the
        # cycle length: 30 days from signup and a monthly anniversary differ over
        # a year, so the card states the entitlement and the account panel
        # shows the real reset date reported by the backend.
        free_frame, free_box = self._plan_card(
            "FREE", "Included with every account", "$0", "",
            feats("free", ["200 included images", "1 GB storage", "Renews each cycle"]),
            badge=badge_for("free"))
        payg_frame = payg_box = None
        if SHOW_PAYG:
            payg_frame, payg_box = self._plan_card(
                "PAY AS YOU GO", "No commitment", "$0.025", "/image",
                feats("payg", ["Buy credits in packs", "Credits never expire", "Best for occasional use"]),
                badge=badge_for("payg"))
        ent_frame, ent_box = self._plan_card(
            "ENTERPRISE", "Custom scale", "Let's talk", "",
            feats("enterprise", ["Multi-seat & shared pool", "SLA & priority support", "Volume pricing"]),
            badge=badge_for("enterprise"))

        cards = [free_frame]
        if payg_frame is not None:
            cards.append(payg_frame)
        if SHOW_SUBSCRIPTION_TIERS:
            hob_frame, hob_box = self._plan_card(
                "ENTRY", "For solo mappers", "$79", "/mo",
                feats("hobbyist", ["30,000 images / month", "Resets monthly", "Buy extra credits anytime"]),
                badge=badge_for("hobbyist"))
            pro_frame, pro_box = self._plan_card(
                "PROFESSIONAL", "Best for teams", "$139", "/mo",
                feats("professional", ["60,000 images / month", "Resets monthly", "Top-up anytime"]),
                accent=True, badge=badge_for("professional", "POPULAR"))
            cards += [hob_frame, pro_frame]
        cards.append(ent_frame)

        for fr in cards:
            row.addWidget(fr)
        dlg.body.addLayout(row)

        # PAYG — buy non-expiring credit packs. Buying does NOT move the account
        # off Free: the two balances coexist, Free is spent first, and purchased
        # credits never expire. Absent entirely on the Free-only launch.
        if payg_box is not None:
            buy_btn = self._styled_button("Buy Credits", primary=not is_subscriber)
            buy_btn.clicked.connect(lambda: (dlg.accept(), self._open_checkout(CHECKOUT_TOKENS_URL, None)))
            payg_box.addWidget(buy_btn)

        # Subscription tiers — only when the subscription model is being sold.
        if SHOW_SUBSCRIPTION_TIERS:
            self._add_sub_card_buttons(dlg, hob_box, "hobbyist", tier, is_subscriber)
            self._add_sub_card_buttons(dlg, pro_box, "professional", tier, is_subscriber)

        # Enterprise — sales-led / manual invoicing
        ent_btn = self._styled_button("Contact Sales", primary=False)
        ent_btn.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl("mailto:sales@aivesystems.com?subject=ATLAS-GEO%20Enterprise%20enquiry")))
        ent_box.addWidget(ent_btn)

        # Footer: manage saved card / cancel subscription via the Stripe portal.
        # Same reasoning as the profile menus: with nothing purchasable there is
        # no Stripe customer, so this link can only ever 404.
        if SHOW_PAYG or SHOW_SUBSCRIPTION_TIERS:
            foot = QtWidgets.QHBoxLayout()
            manage = QtWidgets.QPushButton("Manage billing && saved card")
            manage.setCursor(QtCore.Qt.PointingHandCursor)
            manage.setStyleSheet(
                "QPushButton { background: transparent; color: #ea580c; border: none;"
                "  font-size: 12px; font-weight: 600; text-decoration: underline; padding: 4px; }"
                "QPushButton:hover { color: #c2410c; }")
            manage.clicked.connect(lambda: (dlg.accept(), self._open_portal()))
            foot.addStretch(1)
            foot.addWidget(manage)
            foot.addStretch(1)
            dlg.body.addSpacing(2)
            dlg.body.addLayout(foot)

        dlg.adjustSize()
        dlg.exec_()

    @QtCore.pyqtSlot(str)
    def _on_email_not_verified(self, email: str):
        self.btn_signin.setEnabled(True)
        self.btn_signin.setText("Sign In")
        self._show_signin_error(f"Verification required. A secure link has been sent to {email}. Please verify your address to continue.", "warning")
        if self._themed_confirm(
                "Verify your email",
                f"We've sent a verification link to {email}.\n\n"
                "Check your inbox (and spam folder), click the link, then sign in.\n\n"
                "Didn't receive it? Resend the verification email now.",
                confirm_text="Resend email", cancel_text="Close", accent="orange"):
            self._resend_verification_email(email)

    def _resend_verification_email(self, email: str):
        try:
            response = requests.post(f"{AUTH_BASE_URL}/resend-verification", json={"email": email}, timeout=10)
            if response.status_code == 200:
                self._show_signin_error(f"Verification link sent to {email}. Check your inbox.", "success")
            elif response.status_code == 400:
                # "Email already verified". Reporting that as "Could not resend,
                # try again later" tells the user to keep retrying something
                # that has already succeeded, and hides the fact that the real
                # problem is elsewhere. Say what is actually true.
                self._show_signin_error(
                    "This address is already verified. Sign in with your password, "
                    "or use Forgot password if you cannot remember it.", "info")
            elif response.status_code == 404:
                self._show_signin_error(
                    "No account found for that address. Check the spelling, or "
                    "create an account.", "error")
            else:
                self._show_signin_error(
                    server_message(response, "Could not resend right now. Please try again."),
                    "error")
        except Exception as e:
            self._show_signin_error(f"Error: {str(e)}", "error")

    @QtCore.pyqtSlot(str)
    def _on_signin_failed(self, message: str):
        self.btn_signin.setEnabled(True)
        self.btn_signin.setText("Sign In")
        self._show_signin_error(message, "error")

    def _show_signin_error(self, message: str, msg_type: str = "error"):
        self.label_signin_error.setText(message)
        self.label_signin_error.setStyleSheet(self._alert_style(msg_type))
        self.label_signin_error.show()

    # ─────────────────────────────────────────────────────────
    # SIGN UP
    # ─────────────────────────────────────────────────────────
    def _handle_signup(self):
        name    = self.input_signup_name.text().strip()
        email   = self.input_signup_email.text().strip()
        password = self.input_signup_password.text().strip()
        confirm  = self.input_signup_confirm.text().strip()

        self.label_signup_error.setText("")
        self.label_signup_error.hide()

        if not name:
            self._show_signup_error("Enter your full name", "error"); self.input_signup_name.setFocus(); return
        if not email:
            self._show_signup_error("Enter your email address", "error"); self.input_signup_email.setFocus(); return
        if not password:
            self._show_signup_error("Enter a password", "error"); self.input_signup_password.setFocus(); return
        if not confirm:
            self._show_signup_error("Confirm your password", "error"); self.input_signup_confirm.setFocus(); return
        if not self._validate_email(email):
            self._show_signup_error("Enter a valid email address", "error"); self.input_signup_email.setFocus(); self.input_signup_email.selectAll(); return
        if len(password) < 8:
            self._show_signup_error("Password must be at least 8 characters", "error"); self.input_signup_password.setFocus(); self.input_signup_password.selectAll(); return
        if password != confirm:
            self._show_signup_error("Passwords don't match", "error"); self.input_signup_confirm.setFocus(); self.input_signup_confirm.selectAll(); return

        # Job title and organisation are collected but NOT validated: they are
        # signup demographics only. Optional is the
        # reversible choice -- a field can be made mandatory later without
        # stranding accounts, whereas blocking signup on an unanswered question
        # costs conversions at exactly the moment the launch is trying to win them.
        job_title = getattr(self, "input_signup_jobtitle", None)
        org       = getattr(self, "input_signup_org", None)
        job_title = job_title.text().strip() if job_title is not None else ""
        org       = org.text().strip() if org is not None else ""

        # Index 0 is the "Select your country" placeholder, not an answer.
        #
        # The plugin does NOT hold the approved list and never decides
        # eligibility: it sends what the user picked and the server rules on it.
        # Shipping the list would publish it to everyone who installs the plugin
        # and still be bypassable, since a modified client can send anything.
        combo   = getattr(self, "combo_signup_country", None)
        country = ""
        if combo is not None and combo.currentIndex() > 0:
            country = combo.itemData(combo.currentIndex()) or ""

        self.btn_do_signup.setEnabled(False)
        self.btn_do_signup.setText("Creating account…")
        self._show_signup_error("Setting up your account…", "info")
        threading.Thread(target=self._signup_thread,
                         args=(name, email, password, job_title, org, country),
                         daemon=True).start()

    def _signup_thread(self, name: str, email: str, password: str,
                       job_title: str = "", organization: str = "",
                       country: str = ""):
        try:
            cfg = getattr(self, "_legal_cfg", None) or {}
            response = requests.post(SIGNUP_URL, json={
                "name": name, "email": email, "password": password,
                # Omitted entirely when blank rather than sent as "": the server
                # treats absent and empty the same, but sending nothing keeps
                # "not answered" distinguishable from "answered with nothing" if
                # these ever become mandatory.
                **({"job_title": job_title} if job_title else {}),
                **({"organization": organization} if organization else {}),
                **({"country": country} if country else {}),
                # What THIS client displayed. The server decides whether it is
                # still current; it is never asserted as correct from here.
                "terms_version":   cfg.get("terms_version"),
                "privacy_version": cfg.get("privacy_version"),
                "client_version":  "atlas-geo-plugin/1.0",
            }, timeout=15)
            if response.status_code == 200:
                QtCore.QMetaObject.invokeMethod(self, "_on_signup_success", QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, email))
                return
            # BRANCH ON THE CODE, NOT THE STATUS. 409 already meant "this email
            # is already registered" here long before the legal check existed,
            # and the server now also returns 409 for a stale document version.
            # Reading the status alone would tell a user whose Terms had simply
            # been updated that their email was taken -- confidently, and
            # wrongly. The same trap as 403, which this file still uses to mean
            # "email not verified".
            detail = {}
            try:
                d = response.json().get("detail")
                # Only a dict carries our structured codes. A 422 makes this a
                # LIST, and str() on it dumps pydantic internals into the dialog.
                detail = d if isinstance(d, dict) else {
                    "message": server_message(response, "Could not create your account.")}
            except Exception:
                detail = {"message": "Could not create your account. Please try again."}
            code = detail.get("code")

            if code == "COUNTRY_NOT_SUPPORTED":
                # Its own branch so the message arrives WHOLE. The generic path
                # below prefixes "Sign-up failed:", which turns a clear statement
                # about regional availability into what looks like an error the
                # user did something to cause.
                QtCore.QMetaObject.invokeMethod(self, "_on_country_not_supported",
                    QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, detail.get("message") or
                                 "ATLAS-GEO is not available in your region yet."))
            elif code == "LEGAL_VERSION_STALE":
                QtCore.QMetaObject.invokeMethod(self, "_legal_versions_stale",
                    QtCore.Qt.QueuedConnection, QtCore.Q_ARG(dict, detail))
            elif code == "PLUGIN_LEGAL_UNSUPPORTED":
                QtCore.QMetaObject.invokeMethod(self, "_on_signup_failed", QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, detail.get("message")
                                 or "Please update ATLAS-GEO to continue."))
            elif response.status_code == 409:
                QtCore.QMetaObject.invokeMethod(self, "_on_signup_failed", QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, "This email is already registered. Sign in instead."))
            else:
                QtCore.QMetaObject.invokeMethod(self, "_on_signup_failed", QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, f"Sign-up failed: {detail.get('message', '')}"))
        except requests.ConnectionError:
            QtCore.QMetaObject.invokeMethod(self, "_on_signup_failed", QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, "Can't connect. Check your internet and try again."))
        except requests.Timeout:
            QtCore.QMetaObject.invokeMethod(self, "_on_signup_failed", QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, "Request timed out. Try again."))

    @QtCore.pyqtSlot(str)
    def _on_signup_success(self, email: str):
        self.btn_do_signup.setEnabled(True)
        self.btn_do_signup.setText("Create Account  →")
        self._show_signup_error(f"Account created. Check {email} for a verification link.", "success")
        self._themed_notice(
            "Account created",
            f"Account created for {email}.\n\n"
            "We've sent a verification link to your inbox. Click it to activate your "
            "account, then sign in with your credentials.",
            accent="green", button="Got it", primary_button=True)
        self._go_to_signin()
        self.input_email.setText(email)

    @QtCore.pyqtSlot(str)
    def _on_country_not_supported(self, message: str):
        """Regional availability, explained rather than reported as a failure.

        Deliberately NOT phrased as an error: the user has done nothing wrong,
        and the copy from the server already tells them who to contact. The
        country field is re-focused because the commonest cause by far is
        picking the wrong entry from a 249-item list.
        """
        self.btn_do_signup.setEnabled(True)
        self.btn_do_signup.setText("Create Account  →")
        # ONE surface, not two. The modal and the inline banner carried the same
        # sentence word for word, so the user read it twice and the form kept a
        # yellow warning sitting under a dialog that already said it.
        #
        # The modal is the one that stays: this is a decision about the account,
        # not a field-level validation error, and it needs an acknowledgement.
        # The inline banner is cleared rather than left behind.
        self.label_signup_error.setText("")
        self.label_signup_error.hide()
        self._themed_notice("Not available in your region", message,
                            icon="", accent="orange", button="Got it")
        cc = getattr(self, "combo_signup_country", None)
        if cc is not None:
            cc.setFocus()

    @QtCore.pyqtSlot(str)
    def _on_signup_failed(self, message: str):
        self.btn_do_signup.setEnabled(True)
        self.btn_do_signup.setText("Create Account  →")
        self._show_signup_error(message, "error")

    def _show_signup_error(self, message: str, msg_type: str = "error"):
        self.label_signup_error.setText(message)
        self.label_signup_error.setStyleSheet(self._alert_style(msg_type))
        self.label_signup_error.show()

    @staticmethod
    def _alert_style(msg_type: str = "error") -> str:
        """Clean tinted inline-alert style (fg, soft bg, matching border) shared by
        the sign-in / sign-up / reset error labels — no more stark white slab."""
        palette = {
            "error":   ("#b91c1c", "#fef2f2", "#fecaca"),
            "warning": ("#b45309", "#fffbeb", "#fde68a"),
            "success": ("#15803d", "#f0fdf4", "#bbf7d0"),
            "info":    ("#1d4ed8", "#eff6ff", "#bfdbfe"),
        }
        fg, bg, bd = palette.get(msg_type, palette["error"])
        return (f"color:{fg}; font-weight:600; font-size:11px; padding:8px 11px;"
                f" background-color:{bg}; border:1px solid {bd}; border-radius:6px;")

    # ─────────────────────────────────────────────────────────
    # RESET PASSWORD
    # ─────────────────────────────────────────────────────────
    def _handle_send_reset_code(self):
        email = self.input_reset_email.text().strip()
        if not email:
            self._show_reset_error("Enter your email address first", "error"); self.input_reset_email.setFocus(); return
        if not self._validate_email(email):
            self._show_reset_error("Enter a valid email address", "error"); self.input_reset_email.setFocus(); self.input_reset_email.selectAll(); return
        self.btn_send_reset_code.setEnabled(False)
        self.btn_send_reset_code.setText("Sending…")
        self._show_reset_error("Sending reset code to your email…", "info")
        threading.Thread(target=self._send_reset_code_thread, args=(email,), daemon=True).start()

    def _send_reset_code_thread(self, email: str):
        try:
            response = requests.post(f"{AUTH_BASE_URL}/reset-password-request", json={"email": email}, timeout=10)
            if response.status_code == 200:
                msg, msg_type = "Code sent. Check your email, then enter it below.", "success"
            else:
                msg, msg_type = server_message(
                    response, "Could not send the reset code. Please try again."), "error"
        except requests.ConnectionError:
            msg, msg_type = "Can't connect. Check your internet and try again.", "error"
        except requests.Timeout:
            msg, msg_type = "Request timed out. Try again.", "error"
        except Exception as e:
            msg, msg_type = f"Error: {str(e)}", "error"
        QtCore.QMetaObject.invokeMethod(self, "_on_send_reset_code_done", QtCore.Qt.QueuedConnection,
            QtCore.Q_ARG(str, msg), QtCore.Q_ARG(str, msg_type))

    @QtCore.pyqtSlot(str, str)
    def _on_send_reset_code_done(self, message: str, msg_type: str):
        self.btn_send_reset_code.setText("Send Reset Code  →")
        self._show_reset_error(message, msg_type)
        if msg_type == "success":
            # Code's on its way — advance to Step 2 and start the 60s resend lock.
            self._show_reset_step2()
            self._start_resend_timer(60)
        else:
            # Stay on Step 1 (or Step 2 for a failed resend) and re-enable the controls.
            self._validate_reset_email_field()
            rb = getattr(self, "_reset_resend_btn", None)
            timer = getattr(self, "_resend_timer", None)
            if rb is not None and (timer is None or not timer.isActive()):
                rb.setEnabled(True)
                rb.setText("Resend code")

    def _handle_reset_password(self):
        email    = self.input_reset_email.text().strip()
        code     = self.input_reset_code.text().strip()
        password = self.input_reset_new_password.text().strip()
        confirm  = self.input_reset_confirm.text().strip()

        self.label_reset_error.setText("")
        self.label_reset_error.hide()

        if not email:
            self._show_reset_error("Enter your email address", "error"); self.input_reset_email.setFocus(); return
        if not self._validate_email(email):
            self._show_reset_error("Enter a valid email address", "error"); self.input_reset_email.setFocus(); self.input_reset_email.selectAll(); return
        if not code:
            self._show_reset_error("Enter the verification code from your email", "error"); self.input_reset_code.setFocus(); return
        if not password:
            self._show_reset_error("Enter a new password", "error"); self.input_reset_new_password.setFocus(); return
        if len(password) < 8:
            self._show_reset_error("Password must be at least 8 characters", "error"); self.input_reset_new_password.setFocus(); self.input_reset_new_password.selectAll(); return
        if not confirm:
            self._show_reset_error("Confirm your new password", "error"); self.input_reset_confirm.setFocus(); return
        if password != confirm:
            self._show_reset_error("Passwords don't match", "error"); self.input_reset_confirm.setFocus(); self.input_reset_confirm.selectAll(); return

        self.btn_do_reset.setEnabled(False)
        self.btn_do_reset.setText("Resetting…")
        self._show_reset_error("Resetting password…", "info")
        threading.Thread(target=self._reset_password_thread, args=(email, code, password), daemon=True).start()

    def _reset_password_thread(self, email: str, code: str, password: str):
        try:
            response = requests.post(RESET_PASSWORD_URL, json={"email": email, "code": code, "new_password": password}, timeout=15)
            if response.status_code == 200:
                QtCore.QMetaObject.invokeMethod(self, "_on_reset_password_success", QtCore.Qt.QueuedConnection, QtCore.Q_ARG(str, email))
            elif response.status_code == 400:
                QtCore.QMetaObject.invokeMethod(self, "_on_reset_password_failed", QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, "Invalid or expired code. Request a new one."))
            elif response.status_code == 404:
                QtCore.QMetaObject.invokeMethod(self, "_on_reset_password_failed", QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, "Email not found. Check your email address."))
            else:
                try:
                    detail = server_message(
                        response, "Could not reset your password. Check the code and try again.")
                except Exception:
                    detail = response.text
                QtCore.QMetaObject.invokeMethod(self, "_on_reset_password_failed", QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, f"Reset failed: {detail}"))
        except requests.ConnectionError:
            QtCore.QMetaObject.invokeMethod(self, "_on_reset_password_failed", QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, "Can't connect. Check your internet and try again."))
        except requests.Timeout:
            QtCore.QMetaObject.invokeMethod(self, "_on_reset_password_failed", QtCore.Qt.QueuedConnection,
                QtCore.Q_ARG(str, "Request timed out. Try again."))

    @QtCore.pyqtSlot(str)
    def _on_reset_password_success(self, email: str):
        self.btn_do_reset.setEnabled(True)
        self.btn_do_reset.setText("Reset Password  →")
        self._show_reset_error("Password reset. You can sign in now.", "success")
        self._themed_notice(
            "Password reset",
            "Your password has been reset successfully.\n\nYou can now sign in with your new password.",
            accent="green", button="Sign in", primary_button=True)
        self._go_to_signin()
        self.input_email.setText(email)

    @QtCore.pyqtSlot(str)
    def _on_reset_password_failed(self, message: str):
        self.btn_do_reset.setEnabled(True)
        self.btn_do_reset.setText("Reset Password  →")
        self._show_reset_error(message, "error")

    def _show_reset_error(self, message: str, msg_type: str = "error"):
        self.label_reset_error.setText(message)
        self.label_reset_error.setStyleSheet(self._alert_style(msg_type))
        self.label_reset_error.show()

    # ─────────────────────────────────────────────────────────
    # LOGOUT
    # ─────────────────────────────────────────────────────────
    def _themed_confirm(self, title, message, icon=None, confirm_text="Yes",
                        cancel_text="No", accent="orange"):
        """Themed Yes/No confirmation. Returns True if the user confirms.
        icon: None -> auto mark from accent; "" -> no badge."""
        badge = {
            "orange": ("#fff4ed", "#fed7aa"),
            "green":  ("#ecfdf5", "#a7f3d0"),
            "red":    ("#fff1f2", "#fecdd3"),
        }.get(accent, ("#fff4ed", "#fed7aa"))
        if icon is None:
            icon = {"green": "✓", "red": "✗"}.get(accent, "")
        mark = {"green": "#047857", "red": "#dc2626"}.get(accent, "#b45309")

        dlg = ThemedDialog(self, title)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(14)
        if icon:
            ic = QtWidgets.QLabel(icon)
            ic.setFixedSize(48, 48)
            ic.setAlignment(QtCore.Qt.AlignCenter)
            ic.setStyleSheet(
                f"font-size: 22px; font-weight: 700; color: {mark};"
                f" background-color: {badge[0]};"
                f" border: 1px solid {badge[1]}; border-radius: 24px;")
            row.addWidget(ic, 0, QtCore.Qt.AlignTop)
        msg = QtWidgets.QLabel(message)
        msg.setWordWrap(True)
        msg.setMinimumWidth(280)
        msg.setStyleSheet("font-size: 13px; color: #57534e; background: transparent; border: none;")
        row.addWidget(msg, 1)
        dlg.body.addLayout(row)

        foot = QtWidgets.QHBoxLayout()
        foot.addStretch(1)
        cancel = self._styled_button(cancel_text, primary=False, min_height=34)
        confirm = self._styled_button(confirm_text, primary=True, accent=accent, min_height=34)
        cancel.clicked.connect(dlg.reject)
        confirm.clicked.connect(dlg.accept)
        foot.addWidget(cancel)
        foot.addWidget(confirm)
        dlg.body.addSpacing(6)
        dlg.body.addLayout(foot)

        dlg.adjustSize()
        return dlg.exec_() == QtWidgets.QDialog.Accepted

    def _clear_session_state(self):
        """Wipe ALL per-user, in-memory state so one account's data never leaks into
        another within the same QGIS session. Called on logout AND on a fresh login
        (belt-and-suspenders). No data is persisted to disk, so this is sufficient for
        full isolation between users."""
        self.selected_file = None
        self.selected_files = []
        self._result_paths = []
        self._job_results = {}
        self._failed_jobs = {}
        self._upload_rows = []
        self._local_save_failures = {}
        self._job_lat = 0.0
        self._job_lon = 0.0
        self._current_job_id = None
        self._processing_start_time = None
        self._mission_id = None
        # Billing snapshot belongs to the previous user — drop it (re-fetched on login).
        self._last_balance = None
        self._current_tier = None
        # ⚠️ THE TILE SESSION IS A CREDENTIAL, AND IT IS USER A'S.
        #
        # It is issued against the signed-in user's token, counts against THEIR
        # rate limit, and is logged server-side as theirs. Leaving it cached
        # across a logout would let the next person on this machine draw tiles
        # on the previous user's session, which is both wrong attribution and a
        # credential outliving the session it belongs to. It costs one HTTP call
        # to mint a fresh one.
        self._tile_session_cache = None
        # Same reasoning: user A's "not approved" must not greet user B on the next
        # login. Refreshed from the server as soon as B's balance loads.
        self._trial_request_state = "none"
        self._trial_request_previous = {}
        # Let the one-time file-migration prompt fire for the NEXT user who logs in on
        # this same QGIS session (otherwise user A's dismissal would suppress user B's).
        self._migration_prompt_seen = False
        try:
            self._rebuild_recent_uploads()   # clears the Recent Uploads panel
        except Exception:
            pass
        # Wipe any visible remnants on the setup screen (filename, mission name, drop zone).
        try:
            if hasattr(self, "input_mission_name"):
                self.input_mission_name.clear()
            if hasattr(self, "label_file_info"):
                self.label_file_info.setText("No file selected")
                self.label_file_info.setStyleSheet(
                    "color: #a8a29e; font-size: 11px; font-style: italic;")
            if hasattr(self, "label_drop_zone"):
                self.label_drop_zone.setText("Drop image here")
                self.label_drop_zone.setStyleSheet("")
            if hasattr(self, "frame_drop_zone"):
                self.frame_drop_zone.setStyleSheet("")   # revert dashed box (was green after file-select)
            if hasattr(self, "btn_next_setup"):
                self.btn_next_setup.setEnabled(False)
        except Exception:
            pass

    def _handle_logout(self):
        if self._themed_confirm(
                "Log out?",
                "You'll need to sign in again to upload imagery or manage billing.",
                confirm_text="Log out", cancel_text="Stay", accent="orange"):
            threading.Thread(target=self._logout_thread, daemon=True).start()

    def _logout_thread(self):
        try:
            headers = {}
            if self.access_token:
                headers["Authorization"] = f"Bearer {self.access_token}"
            requests.post(LOGOUT_URL, json={}, headers=headers, timeout=10)
        except Exception:
            pass
        finally:
            self.access_token = None
            self.refresh_token = None
            self.current_user_email = None
            self._clear_remembered_token()
            QtCore.QMetaObject.invokeMethod(self, "_on_logout_complete", QtCore.Qt.QueuedConnection)

    @QtCore.pyqtSlot()
    def _on_logout_complete(self):
        self._clear_session_state()   # drop the previous user's uploads/jobs/results
        self._update_header_status()  # header chip -> back to version tag
        self.iface.messageBar().pushMessage("ATLAS", "Logged out successfully.", level=0, duration=3)
        self._go_to_signin()

    # ─────────────────────────────────────────────────────────
    # VALIDATION
    # ─────────────────────────────────────────────────────────
    def _validate_email(self, email: str) -> bool:
        """Catch obvious mistakes locally so the server never has to say no.

        The old check was `"@" in email and "." in email`, which accepted
        "red @gmail.com". That reached the server, failed pydantic's EmailStr,
        and the 422 body was rendered raw in the dialog.
        """
        return bool(EMAIL_RE.match((email or "").strip()))

    # ─────────────────────────────────────────────────────────
    # FILE BROWSE
    # ─────────────────────────────────────────────────────────
    def _choice_card(self, icon, title, desc):
        """Build a large clickable option card. Returns (frame, button)."""
        frame = QtWidgets.QFrame()
        frame.setStyleSheet(
            "QFrame { background-color: #ffffff; border: 1px solid #ece4d8;"
            "  border-bottom: 2px solid #e3d6c4; border-radius: 14px; }"
            "QLabel { background: transparent; border: none; }")
        frame.setMinimumWidth(200)
        v = QtWidgets.QVBoxLayout(frame)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(6)
        if icon:
            from qgis.PyQt.QtGui import QPixmap
            ic = QtWidgets.QLabel()
            if isinstance(icon, QPixmap):
                ic.setPixmap(icon)
            else:
                ic.setText(icon)
                ic.setStyleSheet("font-size: 30px;")
            v.addWidget(ic)
        t = QtWidgets.QLabel(title)
        t.setStyleSheet("font-size: 15px; font-weight: 700; color: #1a1612;")
        v.addWidget(t)
        d = QtWidgets.QLabel(desc)
        d.setStyleSheet("font-size: 11px; color: #a8a29e;")
        d.setWordWrap(True)
        v.addWidget(d)
        v.addSpacing(8)
        btn = self._styled_button("Choose", primary=True)
        v.addWidget(btn)
        return frame, btn

    def browse_file(self):
        """Allow selecting either individual files or an entire folder of images."""
        dlg = ThemedDialog(self, "Add imagery", "Choose how to load your UAV images")

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(12)
        try:
            files_icon  = self._svg_icon("image", size=30)
            folder_icon = self._svg_icon("folder", size=30)
        except Exception:
            files_icon, folder_icon = "🗂", "📁"   # emoji fallback if QtSvg missing
        files_frame, files_btn   = self._choice_card(files_icon, "Select Files", "Pick up to 10 individual images")
        folder_frame, folder_btn = self._choice_card(folder_icon, "Select Folder", "Load every image in a folder")
        row.addWidget(files_frame)
        row.addWidget(folder_frame)
        dlg.body.addLayout(row)

        foot = QtWidgets.QHBoxLayout()
        foot.addStretch(1)
        cancel = self._styled_button("Cancel", primary=False)
        cancel.clicked.connect(dlg.reject)
        foot.addWidget(cancel)
        dlg.body.addSpacing(2)
        dlg.body.addLayout(foot)

        result = {"choice": None}
        files_btn.clicked.connect(lambda: (result.__setitem__("choice", "files"), dlg.accept()))
        folder_btn.clicked.connect(lambda: (result.__setitem__("choice", "folder"), dlg.accept()))

        dlg.adjustSize()
        dlg.exec_()

        if result["choice"] == "files":
            self._browse_files()
        elif result["choice"] == "folder":
            self._browse_folder()

    def _browse_files(self):
        filenames, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Select UAV Images (Max 10)", "",
            "Drone images with GPS (*.jpg *.jpeg *.tif *.tiff *.geotiff);;All files (*.*)"
        )
        if not filenames:
            return
        self._load_image_list(filenames)

    def _browse_folder(self):
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select Folder Containing UAV Images", ""
        )
        if not folder:
            return

        SUPPORTED = {".jpg", ".jpeg", ".tif", ".tiff", ".geotiff"}
        found = [
            str(p) for p in sorted(Path(folder).iterdir())
            if p.is_file() and p.suffix.lower() in SUPPORTED
        ]

        if not found:
            self._themed_notice(
                "No images found",
                f"No supported image files found in:\n{folder}\n\n"
                "Supported formats: JPG, JPEG, TIF, TIFF, GeoTIFF (with embedded GPS)",
                accent="red", button="Close")
            return

        self._load_image_list(found, source_folder=folder)

    def _load_image_list(self, filenames: list, source_folder: str = None):
        # Advertised per-batch cap; the upload chunks these into UPLOAD_CHUNK_SIZE
        # requests under one batch_id, so large selections are fine.
        MAX_BATCH_IMAGES = int(os.getenv("ATLAS_MAX_BATCH_IMAGES", "5000"))
        if len(filenames) > MAX_BATCH_IMAGES:
            if not self._themed_confirm(
                    "Too many images",
                    f"Found {len(filenames)} images. Only the first {MAX_BATCH_IMAGES} will be processed.\n\nContinue?",
                    confirm_text="Continue", cancel_text="Cancel", accent="orange"):
                return
            filenames = filenames[:MAX_BATCH_IMAGES]

        # Pre-upload metadata gate: georeferencing is seeded by embedded GPS, so an
        # image without it is a guaranteed null-island failure. Flag/exclude those
        # up front rather than waste a processing run (and confuse the user).
        with_gps, no_gps = [], []
        gps_points = []
        for p in filenames:
            try:
                _lat, _lon, _ = self._extract_gps_from_image(p)
                with_gps.append(p)
                gps_points.append((_lat, _lon))
            except Exception:
                no_gps.append(p)

        if no_gps:
            names = "\n".join(f"  • {os.path.basename(p)}" for p in no_gps[:8])
            more  = f"\n  …and {len(no_gps) - 8} more" if len(no_gps) > 8 else ""
            if not with_gps:
                self._themed_notice(
                    "No GPS metadata",
                    "None of the selected images contain GPS metadata, so they can't be "
                    "georeferenced:\n\n" + names + more +
                    "\n\nUse the original drone photos (JPG/TIFF with embedded GPS). "
                    "PNGs and edited/exported copies usually strip this data.",
                    accent="red", button="Close")
                return
            if not self._themed_confirm(
                    "Some images have no GPS",
                    f"{len(no_gps)} of {len(filenames)} image(s) have no GPS metadata and "
                    "would fail georeferencing:\n\n" + names + more +
                    f"\n\nContinue with the {len(with_gps)} valid image(s) and skip these?",
                    confirm_text=f"Continue with {len(with_gps)}",
                    cancel_text="Cancel", accent="orange"):
                return
            filenames = with_gps

        # Second gate: a GPS track that never moves. Georeferencing fetches the
        # reference imagery around each frame's recorded position, so a batch
        # that reports one position for every frame searches the same patch of
        # ground every time. If the aircraft was actually moving, every frame
        # after the first is compared against ground it was never over, and the
        # whole batch fails.
        #
        # A batch reporting one identical coordinate for every frame therefore
        # fails throughout, and a generic "difficult terrain" explanation sends
        # the operator to inspect imagery when the position data was the problem.
        # It is detectable here, before a single byte is uploaded.
        #
        # WARNS, NEVER BLOCKS. An aircraft that genuinely held position produces
        # exactly this metadata, and those frames really are all over that point,
        # so they would match correctly. Only the user knows which case this is.
        # Remembered so the results and error screens can name the REAL cause. The
        # user was warned before uploading; if the batch then fails, repeating the
        # generic terrain advice would send them to look at the wrong thing twice.
        self._frozen_gps_batch = False
        if len(with_gps) >= 3 and gps_points:
            _lats = [q[0] for q in gps_points]
            _lons = [q[1] for q in gps_points]
            _mid = math.radians(sum(_lats) / len(_lats))
            _spread_m = max((max(_lats) - min(_lats)) * 111320.0,
                            (max(_lons) - min(_lons)) * 111320.0 * math.cos(_mid))
            # A real survey moves far further than this between the first and
            # last frame; 10 m is below even a single frame's ground footprint.
            if _spread_m < 10.0:
                _n_distinct = len(set(gps_points))
                # The buttons carry the question, not the paragraph. "Continue"
                # and "Cancel" made this a nuisance to dismiss, when the whole
                # point is that only the operator knows which case they are in:
                # a moving flight with faulty position data, or a deliberate
                # hover. Naming the assumption in the label makes continuing an
                # assertion about the flight rather than a shrug.
                #
                # The wording says "very little or no movement" because the check
                # measures SPREAD IN METRES, not distinct coordinates. Saying
                # "every image reports the same position" was wrong whenever the
                # gate fired on a GPS jittering a few metres, which is exactly the
                # case a distinct-count check would have missed.
                if not self._themed_confirm(
                        "These images show almost no GPS movement",
                        f"The {len(with_gps)} selected images show very little or "
                        "no GPS movement: every position falls within "
                        f"{_spread_m:.0f} m of the others"
                        + (f", and all {len(with_gps)} frames report one identical "
                           "coordinate" if _n_distinct == 1 else
                           f" ({_n_distinct} distinct coordinates across the "
                           "batch)") + ".\n\n"
                        "If this was a moving survey flight, the recorded position "
                        "data is faulty. Check the image metadata before "
                        "continuing: every frame would be matched against the same "
                        "patch of ground, so the batch cannot be georeferenced.\n\n"
                        "If the aircraft genuinely held one position, this is "
                        "expected and processing will work normally.",
                        confirm_text="Aircraft was stationary, continue",
                        cancel_text="Cancel and check the metadata",
                        accent="orange"):
                    return
                self._frozen_gps_batch = True

        # Third gate: strongly tilted frames. These are processed normally, they
        # are simply much less likely to place accurately, so the user is told
        # once rather than left to wonder why those results look worse. Never
        # blocks: frames with no pitch recorded are treated as fine.
        tilted = []
        for p in filenames:
            tilt = self._tilt_from_nadir(p)
            if tilt is not None and tilt >= self.OBLIQUE_WARN_TILT_DEG:
                tilted.append((p, tilt))

        # Remembered so the results screen can explain failures accurately rather
        # than guessing. Tilted images are attempted rather than gated, and the
        # angle is named as a likely cause if georeferencing does not succeed.
        self._tilted_files = {os.path.basename(p) for p, _ in tilted}

        if tilted:
            tilted.sort(key=lambda t: -t[1])
            names = "\n".join(f"  • {os.path.basename(p)}  ({t:.0f}° from straight down)"
                              for p, t in tilted[:8])
            more  = f"\n  …and {len(tilted) - 8} more" if len(tilted) > 8 else ""
            self._themed_notice(
                "Steeply angled images",
                f"{len(tilted)} of {len(filenames)} image(s) were taken at a steep "
                "angle rather than pointing straight down:\n\n" + names + more +
                "\n\nThese will be processed as normal. They are less likely to be "
                "placed accurately, because the reference map is a straight-down view "
                "and tall features are seen from the side, so treat their results with "
                "more caution.",
                accent="orange", button="Got it")

        self.selected_files = filenames
        self.selected_file  = filenames[0]
        count = len(filenames)

        if source_folder:
            folder_name = Path(source_folder).name
            display_text = f"✓  {count} images from  '{folder_name}'"
            info_text    = f"{count} images from folder"
        else:
            display_text = f"✓  {count} image{'s' if count > 1 else ''} selected"
            info_text    = f"{count} image{'s' if count > 1 else ''} selected"

        self.label_file_info.setText(info_text)
        self.label_drop_zone.setText(display_text)
        if hasattr(self, "btn_next_setup"):
            self.btn_next_setup.setEnabled(True)
        # Files chosen -> turn the whole dashed FRAME green (border + tint) and the
        # prompt text green. (The dashed box is now the frame, not the label.)
        self.label_drop_zone.setStyleSheet(
            "color: #10b981; font-size: 14px; font-weight: 600; background: transparent; border: none;")
        if hasattr(self, "frame_drop_zone"):
            self.frame_drop_zone.setStyleSheet(
                "QFrame#frame_drop_zone { border: 1px solid #10b981; border-radius: 10px;"
                " background-color: #f0fdf4; }")

        # Populate the Recent Uploads panel with the real selected files.
        self._upload_rows = [
            {
                "name":   os.path.basename(p),
                "size":   self._human_size(p),
                "status": "Ready",
            }
            for p in self.selected_files
        ]
        self._upload_row_by_name = {r["name"]: r for r in self._upload_rows}
        self._rebuild_recent_uploads()

    # ─────────────────────────────────────────────────────────
    # RECENT UPLOADS PANEL (real, driven by selected files + job status)
    # ─────────────────────────────────────────────────────────
    @staticmethod
    def _human_size(path: str) -> str:
        try:
            b = os.path.getsize(path)
        except OSError:
            return "—"
        for unit in ("B", "KB", "MB", "GB"):
            if b < 1024.0:
                return f"{b:.1f} {unit}" if unit != "B" else f"{int(b)} B"
            b /= 1024.0
        return f"{b:.1f} TB"

    _UPLOAD_STATUS_COLORS = {
        "Ready":      "#64748b",
        "Processing": "#f59e0b",
        "Processed":  "#10b981",
        "Low confidence": "#f59e0b",
        "Failed":     "#ef4444",
    }

    def _rebuild_recent_uploads(self):
        """Clear the static demo rows and rebuild from self._upload_rows.

        Rows live inside a QScrollArea so any number of files (1-10) displays
        cleanly within the fixed-height panel instead of overflowing.
        """
        layout = getattr(self, "vl_recent_uploads", None)
        if layout is None:
            return
        self._clear_layout(layout)
        self._sync_recent_toggle()   # keep the accordion "(N)" count current

        if not self._upload_rows:
            empty = QtWidgets.QLabel("No images selected")
            empty.setStyleSheet("font-size: 10px; color: #a8a29e; font-style: italic; padding: 6px;")
            layout.addWidget(empty)
            return

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        inner = QtWidgets.QWidget()
        ivl = QtWidgets.QVBoxLayout(inner)
        ivl.setContentsMargins(0, 0, 0, 0)
        ivl.setSpacing(3)

        for row in self._upload_rows[:40]:   # cap rendered widgets; large batches show a summary below
            frame = QtWidgets.QFrame()
            frame.setStyleSheet(
                "QFrame { background-color: #faf8f4; border: 1px solid #e8e2d8; border-radius: 4px; }"
            )
            frame.setMinimumHeight(32)
            frame.setMaximumHeight(32)
            hl = QtWidgets.QHBoxLayout(frame)
            hl.setContentsMargins(8, 4, 8, 4)
            hl.setSpacing(8)

            name = QtWidgets.QLabel(row["name"])
            name.setStyleSheet("font-size: 10px; font-weight: 500; color: #1a1612;")
            size = QtWidgets.QLabel(row["size"])
            size.setStyleSheet("font-size: 9px; color: #a8a29e;")
            status = QtWidgets.QLabel(row["status"])
            color = self._UPLOAD_STATUS_COLORS.get(row["status"], "#64748b")
            status.setStyleSheet(
                f"font-size: 9px; font-weight: bold; color: #ffffff; "
                f"background-color: {color}; padding: 2px 6px; border-radius: 3px;"
            )

            hl.addWidget(name)
            hl.addStretch()
            hl.addWidget(size)
            hl.addWidget(status)
            ivl.addWidget(frame)

        # Large batches: don't render thousands of row widgets — summarize the rest.
        if len(self._upload_rows) > 40:
            counts = {}
            for r in self._upload_rows:
                counts[r["status"]] = counts.get(r["status"], 0) + 1
            summary = QtWidgets.QLabel(
                f"… and {len(self._upload_rows) - 40} more   ·   "
                + "   ·   ".join(f"{v} {k}" for k, v in counts.items()))
            summary.setStyleSheet("font-size: 9px; color: #78716c; padding: 4px 8px;")
            ivl.addWidget(summary)

        ivl.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll)

    @QtCore.pyqtSlot(str, str)
    def _set_upload_status(self, filename: str, status: str):
        """Update one file's status badge and refresh the panel (debounced)."""
        row = self._upload_row_by_name.get(filename)
        if row is None:                      # fallback if the index is missing/stale
            for r in self._upload_rows:
                if r["name"] == filename:
                    row = r
                    break
        if row is not None:
            row["status"] = status
        self._schedule_recent_uploads_refresh()

    def _schedule_recent_uploads_refresh(self):
        """Coalesce rapid status updates into ≤1 panel rebuild per ~300ms. Large
        batches fire thousands of updates; rebuilding per-update would freeze the UI."""
        t = getattr(self, "_uploads_refresh_timer", None)
        if t is None:
            self._rebuild_recent_uploads()   # timer not ready yet -> rebuild immediately
        elif not t.isActive():
            t.start(300)

    # ─────────────────────────────────────────────────────────
    # PROCESSING
    # ─────────────────────────────────────────────────────────
    def _go_to_processing(self):
        if not self.selected_files:
            self._themed_notice("No files selected", "Please select UAV images first.",
                                accent="red", button="OK")
            return
        self._style_processing_page()
        self.stacked_pages.setCurrentIndex(self.PAGE_PROCESSING)
        self._start_processing()

    def _start_processing(self):
        self._cancel_flag.clear()
        self._processing_active = True
        self._was_cancelled = False
        self._batch_id = None
        self._batch_total = len(self.selected_files)
        self._processing_start_time = time.time()
        self._job_results = {}
        self._failed_jobs = {}
        self._submitted_jobs = {}
        # ⚠️ _local_save_failures is deliberately NOT cleared here. An unsaved
        # result is unfinished business: the work was done and charged for, and
        # the file is still retrievable. Clearing it at the start of the next
        # batch would silently discard the only record of what is missing and
        # the URL needed to fetch it, so the user loses a paid-for result by
        # doing nothing worse than running another job. Entries are removed only
        # when the result is saved, confirmed gone from the server, or dismissed.
        self.progressbar_processing.setValue(0)
        self.label_progress_percent.setText("0%")
        self._reset_step_labels()

        # Replace the static demo rows in the stats cards with a live "starting" state.
        self._rebuild_processing_stats(0, len(self.selected_files))
        if hasattr(self, "label_processing_file") and self.selected_files:
            self.label_processing_file.setText(os.path.basename(self.selected_files[0]))
        if hasattr(self, "label_frame_counter"):
            self.label_frame_counter.setText(f"Frame 0/{len(self.selected_files)}")

        # Set initial customer-friendly metrics
        file_count = len(self.selected_files)
        file_text = "file" if file_count == 1 else "files"
        self.label_insight_quality.setText(f"File status: your {file_count} {file_text} {'is' if file_count == 1 else 'are'} ready and validated")
        self.label_insight_speed.setText("Current step: starting analysis…")
        # A hard-coded "about 3-5 minutes" is invented: one constant cannot be
        # right for both a handful of frames and several hundred. Say we do not
        # know yet, then say the real number once frames have been timed.
        self.label_insight_eta.setText("Time remaining: estimating")
        
        self._processing_thread = threading.Thread(target=self._run_backend_request, daemon=True)
        self._processing_thread.start()

    # ── Remember Me persistence (QSettings — survives QGIS restarts) ──────────
    _SETTINGS_ORG  = "AIVE"
    _SETTINGS_APP  = "AtlasGeo"
    _SETTINGS_KEY  = "auth/refresh_token"
    _SETTINGS_EMAIL = "auth/email"

    def _save_remembered_token(self):
        s = QtCore.QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
        s.setValue(self._SETTINGS_KEY,  self.refresh_token or "")
        s.setValue(self._SETTINGS_EMAIL, self.current_user_email or "")

    def _clear_remembered_token(self):
        s = QtCore.QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
        s.remove(self._SETTINGS_KEY)
        s.remove(self._SETTINGS_EMAIL)

    def _try_auto_signin(self):
        """On startup: if a saved refresh token exists, silently exchange it for
        a new access token and go straight to the setup page. Shows sign-in page
        on any failure so the user can log in normally."""
        s = QtCore.QSettings(self._SETTINGS_ORG, self._SETTINGS_APP)
        saved_token = s.value(self._SETTINGS_KEY, "")
        saved_email = s.value(self._SETTINGS_EMAIL, "")
        if not saved_token:
            return False
        self.refresh_token = saved_token
        self.current_user_email = saved_email
        ok = self._refresh_access_token()
        if ok:
            self._save_remembered_token()   # persist the rotated refresh token
            return True
        # Token expired / revoked — clear and fall through to sign-in
        self._clear_remembered_token()
        self.refresh_token = None
        self.current_user_email = None
        return False

    def _refresh_access_token(self) -> bool:
        """Use the stored refresh token to get a new access token.
        Returns True on success (self.access_token updated), False if the
        refresh token is also expired/invalid (user must sign in again)."""
        if not self.refresh_token:
            return False
        try:
            resp = requests.post(
                REFRESH_URL, json={"refresh_token": self.refresh_token}, timeout=15
            )
            if resp.status_code == 200:
                data = resp.json()
                self.access_token = data.get("access_token")
                # Keycloak rotates refresh tokens — keep the new one.
                if data.get("refresh_token"):
                    self.refresh_token = data.get("refresh_token")
                return True
        except Exception:
            pass
        return False

    def _authed_request(self, method: str, url: str, **kwargs):
        """Issue an authenticated request. On 401, transparently refresh the
        access token once and retry, so a mid-session expiry is invisible to
        the user. Bodies here are token-free (json/params/no files), so the
        retry is safe to repeat. (File uploads handle their own retry.)"""
        def _send():
            headers = dict(kwargs.get("headers") or {})
            if self.access_token:
                headers["Authorization"] = f"Bearer {self.access_token}"
            kw = dict(kwargs)
            kw["headers"] = headers
            return requests.request(method, url, **kw)

        resp = _send()
        if resp.status_code == 401 and self._refresh_access_token():
            resp = _send()
        return resp

    # ── Storage (Phase-1B: tiered storage quota) ──────────────────────────
    def _get_storage_usage(self):
        """Fetch the user's storage usage vs. tier quota. Returns the billing payload
        dict, or None on any error (the feature may be disabled server-side)."""
        try:
            resp = self._authed_request("GET", STORAGE_USAGE_URL, timeout=10)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        return None

    def _free_up_storage(self):
        """Account-menu action: show current storage usage and, on the user's explicit
        consent, delete their OLDEST data (server-side) to get back under quota. Runs
        on the UI thread, so prompting here is safe (unlike the upload worker)."""
        usage = self._get_storage_usage()
        if usage is None:
            self._themed_notice(
                "Storage",
                "Storage usage isn't available right now. Please try again later.",
                icon="", accent="orange", button="OK")
            return
        used_gb  = usage.get("used_gb", 0)
        quota_gb = usage.get("quota_gb", 0)
        count    = usage.get("object_count", 0)
        if not usage.get("over_quota"):
            self._themed_notice(
                "Storage",
                (f"Your team is using {used_gb:.2f} GB of its shared {quota_gb:.0f} GB "
                 f"({count} files), which is within the limit. Nothing needs to be removed."
                 if usage.get("is_org_pool") else
                 f"You're using {used_gb:.2f} GB of your {quota_gb:.0f} GB "
                 f"({count} files), which is within your limit. Nothing needs to be removed."),
                icon="", accent="green", button="OK")
            return
        # An org seat shares ONE storage pool, but deletion is scoped to the caller's
        # OWN files only — never a teammate's. A member can free just
        # their own contribution; if teammates' data keeps the pool over, they can't
        # fix it alone. Consent copy says exactly that.
        if usage.get("is_org_pool"):
            ok = self._themed_confirm(
                "Free up your storage",
                f"Your team is using {used_gb:.2f} GB of its {quota_gb:.0f} GB shared "
                "storage.\n\n"
                "This deletes only your own oldest files, not your teammates'. If the "
                "pool is still over the limit afterward, someone else on the team will "
                "need to clear space too.\n\n"
                "This can't be undone.",
                icon="", confirm_text="Delete my oldest",
                cancel_text="Cancel", accent="red")
        else:
            ok = self._themed_confirm(
                "Free up storage",
                f"You're using {used_gb:.2f} GB of your {quota_gb:.0f} GB storage "
                f"({count} files) and are over your limit.\n\n"
                "Delete your OLDEST data to free space? The earliest uploaded images and "
                "results are removed first, until you're back under quota.\n\n"
                "This cannot be undone.",
                icon="", confirm_text="Delete oldest", cancel_text="Cancel", accent="red")
        if not ok:
            return
        try:
            # free_bytes=0 → server frees just enough to get back under quota.
            resp = self._authed_request("POST", STORAGE_DELETE_URL,
                                        json={"free_bytes": 0}, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                freed_gb = data.get("freed_bytes", 0) / (1024 ** 3)
                n_del = data.get("deleted_count", 0)
                pool_over = data.get("pool_still_over")
                if n_del == 0 and pool_over:
                    # Nothing of the caller's own left to remove, but the shared pool
                    # is still over — it's teammates' data holding it over.
                    self._themed_notice(
                        "Nothing of yours to remove",
                        "You don't have any older files to delete. The pool is over its "
                        "limit because of files your teammates own, so they'll need to "
                        "clear some space themselves.",
                        icon="", accent="orange", button="OK")
                else:
                    tail = (" The pool is still over the limit, so a teammate may need "
                            "to clear some space too."
                            if pool_over else " You can upload again now.")
                    self._themed_notice(
                        "Storage freed",
                        f"Removed {n_del} file(s), freeing {freed_gb:.2f} GB." + tail,
                        icon="", accent="green", button="Done", primary_button=True)
            else:
                self._themed_notice(
                    "Storage",
                    f"Could not free storage (HTTP {resp.status_code}). Please try again.",
                    icon="⚠", accent="red", button="Close")
        except Exception as e:
            self._themed_notice(
                "Storage", f"Could not free storage: {e}",
                icon="⚠", accent="red", button="Close")

    @staticmethod
    def _atomic_write(out_path, content):
        """Write `content` to `out_path` atomically. Returns (ok, error_str).

        WHY ATOMIC. A plain write_bytes on a full disk leaves a TRUNCATED .tif at
        the final name, and if a valid result from an earlier run was already
        there it has just been destroyed by a failure. Both outcomes are worse
        than not writing at all: QGIS will happily open a partial GeoTIFF and
        show a corrupt raster, and the user has no way to tell it apart from a
        genuine result.

        So the bytes go to a unique temporary file in the SAME directory, and the
        final name is only created by os.replace once the write has completed.
        Same directory matters: os.replace is atomic only within one filesystem,
        and a temp file in /tmp could land on a different one.

        The temp file is removed on every failure path, including a failed
        replace, so a full disk does not accumulate debris.
        """
        import os as _os
        import tempfile as _tempfile
        tmp = None
        try:
            fd, tmp_name = _tempfile.mkstemp(
                dir=str(out_path.parent),
                prefix=f".{out_path.stem}.", suffix=".part")
            tmp = tmp_name
            with _os.fdopen(fd, "wb") as fh:
                fh.write(content)
                fh.flush()
                # Durability before the rename: without this the rename can be
                # visible while the data is still only in the page cache.
                _os.fsync(fh.fileno())
            _os.replace(tmp, str(out_path))
            tmp = None
            return True, None
        except OSError as e:
            return False, str(e)
        finally:
            if tmp is not None:
                try:
                    _os.unlink(tmp)
                except OSError:
                    pass

    def _retry_local_saves(self, target_dir=None):
        """Re-download results that processed correctly but could not be saved.

        This exists so the failure message can offer something ACTIONABLE. The
        first version told the user to "run these images again", which would
        reprocess and consume another image from their allowance for work they
        had already paid for. The result is still on the server, so the honest
        remedy is to fetch it again, not to redo it.

        Uses the same atomic write as the original attempt, so a retry that
        fails on a still-full disk cannot leave a partial file either.
        """
        pend = dict(getattr(self, "_local_save_failures", {}) or {})
        if not pend:
            return
        # `gone` holds results the server no longer has. They are dropped rather
        # than retried forever, but counted separately so the user is told the
        # file is unrecoverable instead of being invited to try again.
        saved, still_failing, gone, first_err = 0, {}, {}, None
        for jid, info in pend.items():
            url = info.get("download_url")
            if not url:
                still_failing[jid] = dict(info, kind="no_url")
                continue
            try:
                resp = self._authed_request("GET", url, timeout=60)
            except Exception as e:
                info = dict(info, error=str(e), kind="network")
                still_failing[jid] = info
                first_err = first_err or str(e)
                continue
            if resp.status_code != 200:
                # ⚠️ CLASSIFY, don't lump. An earlier version reported every
                # non-200 as "the stored result is no longer available", which
                # would tell a user their file was deleted when their session
                # had merely expired, or when the server was briefly down.
                # Each of these needs a different action from the user.
                code = resp.status_code
                if code == 404:
                    # Genuinely gone: retention swept it, or it was cleaned up.
                    # This one is terminal, so the entry is dropped rather than
                    # left to be retried forever.
                    gone[jid] = dict(info, error="the stored result is no longer "
                                                 "available on the server",
                                     kind="gone")
                    self._set_upload_status(info.get("file", ""), "Not saved")
                    first_err = first_err or gone[jid]["error"]
                    continue
                if code == 401:
                    # _authed_request already refreshed and retried once, so a
                    # persistent 401 means the session really is finished.
                    msg = "your session has expired, please sign in again"
                    kind = "auth"
                elif code == 403:
                    # ⚠️ NOT the same as 401, and must not be reported as one.
                    # 403 is 'authenticated but not permitted', which on this
                    # deployment can mean the result belongs to another account,
                    # or a server-side access policy refused the request. Telling
                    # that user to sign in again sends them round a loop that
                    # cannot resolve it, and hides the real cause.
                    msg = ("access to this result was refused (HTTP 403). If you "
                           "are signed in to a different account than the one "
                           "that ran the job, sign in as that account; otherwise "
                           "contact support")
                    kind = "forbidden"
                elif 500 <= code < 600:
                    msg = f"the server is temporarily unavailable (HTTP {code})"
                    kind = "server"
                else:
                    msg = f"the download failed (HTTP {code})"
                    kind = "http"
                still_failing[jid] = dict(info, error=msg, kind=kind)
                first_err = first_err or msg
                continue
            # When the user has chosen a different folder, keep the FILENAME the
            # original path implies. Retrying into the same unwritable directory
            # while telling them to "pick a writable folder" would be advice the
            # code does not act on.
            out_path = (Path(target_dir) / Path(info["path"]).name
                        if target_dir else Path(info["path"]))
            ok, err = self._atomic_write(out_path, resp.content)
            if ok:
                saved += 1
                self._result_paths.append(str(out_path))
                # The result is now on disk exactly as if it had saved first
                # time, so it belongs in _job_results. Leaving it out would make
                # a recovered job vanish from every bucket and be reported as
                # unaccounted for.
                res = info.get("result")
                if res:
                    self._job_results[jid] = res
                self._set_upload_status(info.get("file", ""), "Processed")
            else:
                info = dict(info, error=err, kind="write")
                still_failing[jid] = info
                first_err = first_err or err
        # Entries the server no longer has are dropped: retrying them forever
        # would never succeed and would keep the button offering false hope.
        self._local_save_failures = still_failing
        if saved and not still_failing:
            self._themed_notice(
                "Results saved",
                f"{saved} result{'s' if saved != 1 else ''} downloaded and saved. "
                "Your images were not charged again.",
                accent="green", button="Done", primary_button=True)
        elif saved:
            self._themed_notice(
                "Some results saved",
                f"{saved} saved, {len(still_failing)} still could not be written. "
                f"Reason: {first_err}",
                icon="", accent="orange", button="Close")
        elif gone and not still_failing:
            self._themed_notice(
                "Results no longer available",
                f"{len(gone)} result{'s' if len(gone) != 1 else ''} could not be "
                "retrieved because the stored copy is no longer on the server. "
                "These cannot be recovered by retrying. To get them again the "
                "images would have to be processed afresh, which does use your "
                "allowance.",
                icon="", accent="orange", button="Close")
        else:
            # Only offer the folder chooser when the failure is actually a WRITE
            # problem. Offering it after a session expiry, a 403 or a server
            # error would send the user to fix the wrong thing.
            #
            # ⚠️ Decided on an explicit `kind`, NOT by matching words in the
            # message. The first version tested for "expired" and "temporarily
            # unavailable" in the error text, which silently mis-classifies the
            # moment any message is reworded, and treated every unrecognised
            # failure as a disk problem. The classifier above is the single place
            # that decides what a failure IS; the message is only how it is said.
            writable_problem = bool(still_failing) and not target_dir and any(
                i.get("kind") == "write" for i in still_failing.values())
            if writable_problem and self._themed_confirm(
                    "Could not save the results",
                    f"The results could not be written. Reason: {first_err}\n\n"
                    "Your images have not been charged again. Would you like to "
                    "save them to a different folder?",
                    confirm_text="Choose a folder", cancel_text="Not now",
                    accent="orange"):
                chosen = QtWidgets.QFileDialog.getExistingDirectory(
                    self, "Choose a folder to save the results into", "")
                if chosen:
                    # One retry into the chosen folder. target_dir is passed so
                    # this cannot loop back into the chooser indefinitely.
                    self._retry_local_saves(target_dir=chosen)
                    return
            else:
                self._themed_notice(
                    "Still could not save",
                    f"The results could not be written. Reason: {first_err}\n\n"
                    "Your images have not been charged again.",
                    accent="red", button="Close")
        self._ensure_retry_save_button()
        # ⚠️ THE CARD MUST BE REBUILT, NOT JUST THE BUTTON.
        #
        # A successful retry moves the job out of _local_save_failures and into
        # _job_results, so "Saved to this computer" and "Not saved to this
        # computer" both change. Refreshing only the button left the card showing
        # the PRE-retry counts: seen live, a green "1 result downloaded and saved"
        # dialog sitting directly above a table still reading "Saved to this
        # computer: 0". The user is told two different things about the same file.
        #
        # Cheap and safe to call unconditionally: it rebuilds from the current
        # dictionaries, so it is correct after a full success, a partial one, or
        # a failure that changed nothing.
        self._populate_results_metrics()

    def _ensure_retry_save_button(self):
        """Show 'Save results again' on the Results page only while something is
        actually unsaved. A button that does nothing is worse than no button."""
        layout = getattr(self, "verticalLayout_results", None)
        if layout is None:
            return
        btn = getattr(self, "_btn_retry_save", None)
        if btn is None:
            btn = self._styled_button("Save results again", primary=True,
                                      accent="orange")
            btn.clicked.connect(self._retry_local_saves)
            self._btn_retry_save = btn
            anchor = getattr(self, "groupbox_results", None)
            idx = layout.indexOf(anchor) if anchor is not None else -1
            layout.insertWidget(idx if idx >= 0 else layout.count(), btn)
        n = len(getattr(self, "_local_save_failures", {}) or {})
        btn.setVisible(bool(n))
        if n:
            btn.setText(f"Save {n} result{'s' if n != 1 else ''} again")

    def _create_mission_row(self, mission_name: str, first_lat: float, first_lon: float) -> str | None:
        """Create a mission row via atlas-api and return its UUID, or None on failure."""
        try:
            resp = self._authed_request(
                "POST",
                f"{ATLAS_BASE_URL}/missions",
                json={"name": mission_name, "lat": first_lat, "lon": first_lon},
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json().get("mission_id")
        except Exception:
            pass
        return None

    def _fetch_job_statuses(self, ids):
        """Fetch statuses for many jobs in ONE request via POST /status/batch
        (the server resolves them in one call) instead of N per-job GETs per cycle.
        Falls back to per-job GET if the backend lacks the batch endpoint.
        Returns {job_id: data}, or {} on a transient error (caller retries)."""
        try:
            resp = self._authed_request(
                "POST", f"{ATLAS_BASE_URL}/status/batch",
                json={"job_ids": ids}, timeout=15,
            )
            if resp.status_code == 200:
                return resp.json().get("results", {}) or {}
            if resp.status_code in (404, 405):
                return self._fetch_job_statuses_fallback(ids)   # older backend
        except Exception:
            pass
        return {}

    def _fetch_job_statuses_fallback(self, ids):
        """Legacy per-job GET /status/{id} when /status/batch isn't available."""
        out = {}
        for jid in ids:
            try:
                resp = self._authed_request("GET", f"{ATLAS_BASE_URL}/status/{jid}", timeout=10)
                if resp.status_code == 200:
                    out[jid] = resp.json()
                elif resp.status_code == 500:
                    # legacy API raises 500 with detail={status:failed,...} on failure
                    try:
                        out[jid] = resp.json().get("detail", {"status": "failed", "reason": "unknown"})
                    except Exception:
                        out[jid] = {"status": "failed", "reason": "unknown"}
            except Exception:
                continue
        return out

    def _prepare_upload_files(self, paths):
        """Downscale + recompress images client-side for a much smaller upload.

        Returns (upload_specs, temp_paths) where upload_specs is a list of
        (original_basename, path_to_send) in the SAME order as `paths`, and
        temp_paths are the temporary files to delete after upload.

        Fail-safe by design: ANY problem with an image (unreadable, encode
        failure, or the copy isn't smaller) → that image uploads as the
        untouched original. Setting UPLOAD_MAX_EDGE<=0 uploads everything as-is.
        """
        import tempfile
        from qgis.PyQt.QtGui import QImage

        specs, temps = [], []
        for p in paths:
            orig_name = os.path.basename(p)
            send_path = p   # default: original
            if UPLOAD_MAX_EDGE and UPLOAD_MAX_EDGE > 0:
                try:
                    img = QImage(p)
                    if not img.isNull() and max(img.width(), img.height()) > UPLOAD_MAX_EDGE:
                        scaled = img.scaled(
                            UPLOAD_MAX_EDGE, UPLOAD_MAX_EDGE,
                            QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
                        fd, tmp = tempfile.mkstemp(suffix=".jpg", prefix="atlas_up_")
                        os.close(fd)
                        ok = scaled.save(tmp, "JPEG", UPLOAD_JPEG_QUALITY)
                        try:
                            smaller = ok and os.path.getsize(tmp) < os.path.getsize(p)
                        except OSError:
                            smaller = False
                        if smaller:
                            send_path = tmp
                            temps.append(tmp)
                        else:
                            try:
                                os.remove(tmp)   # encode failed or not smaller
                            except OSError:
                                pass
                except Exception as e:
                    print(f"upload compress fallback for {orig_name}: {e}")
            specs.append((orig_name, send_path))
        return specs, temps

    def _run_backend_request(self):
        try:
            # Snapshot the file list up front. Cancelling clears
            # self.selected_files on the UI thread (_restart_mission), so this
            # worker must keep using its own copy — otherwise it indexes an
            # emptied list mid-run and crashes with "list index out of range".
            selected_files = list(self.selected_files)
            if not selected_files:
                return

            self.progress_updated.emit(10, 0)

            # ── Step 1: Extract GPS metadata PER IMAGE ──
            per_image_meta = []
            for path in selected_files:
                try:
                    lat, lon, heading = self._extract_gps_from_image(path)
                    altitude, altitude_source = self._extract_altitude_with_source(path)
                    focal35  = self._extract_focal35_from_raw_xmp(path)
                    # Normalised ONCE here, at the boundary. Downstream code must
                    # never see raw gimbal pitch: DJI writes -90 for straight down,
                    # so feeding the raw value into tan() overstates the geometry
                    # roughly threefold. tilt_from_nadir_deg is degrees away from
                    # vertical, 0 = straight down. None when the camera did not
                    # record a pitch, which must not block the upload.
                    tilt = self._tilt_from_nadir(path)
                    if tilt is not None and not (0.0 <= tilt <= 90.0):
                        tilt = None          # implausible, treat as unknown
                    per_image_meta.append({
                        "lat":      lat,
                        "lon":      lon,
                        "heading":  heading,
                        # Send unknown altitude as null, never as 0.0. The
                        # extractor above already returns None when it finds
                        # nothing, and the worker is built for that: it retries
                        # with its own XMP parser when altitude is None
                        # (server-side job processing). 0.0 defeats that twice over:
                        # it is falsy, so it silently disables the tilt-aware
                        # positional guard, and it is not None, so it also stops
                        # the fallback parser from ever running.
                        "altitude": altitude,
                        # WHERE that number came from, which decides whether it
                        # may be used as height above ground. relative_agl is
                        # DJI's takeoff-relative height and is AGL-like;
                        # gps_msl is EXIF GPSAltitude and is above SEA LEVEL,
                        # so the worker must not feed it to AGL-only geometry.
                        "altitude_source": altitude_source,
                        # Focal length follows the same rule as altitude: unknown
                        # travels as null. 24 mm is a reasonable number to compute
                        # WITH, and every consumer still applies it locally, but
                        # substituting it here erases the fact that nobody measured
                        # it. The footprint scale check needs that distinction --
                        # it derives a physical ground width from this value and
                        # refuses the matcher for disagreeing, which is only sound
                        # when the value is real. See worker.save_result.
                        "focal35":  focal35,
                        "tilt_from_nadir_deg": tilt,
                        "gimbal_pitch_deg": self._extract_pitch_from_raw_xmp(path),
                    })
                except ValueError as e:
                    raise Exception(
                        f"Metadata extraction failed for {os.path.basename(path)}: {e}\n\n"
                        "Make sure your UAV images have valid GPS and altitude metadata."
                    )

            # Use first image coords for map centering later
            self._job_lat = per_image_meta[0]["lat"]
            self._job_lon = per_image_meta[0]["lon"]

            # ── Step 1b: Create mission row in PostgREST ──
            mission_name = (
                getattr(self, "input_mission_name", None) and
                self.input_mission_name.text().strip()
            ) or "Untitled"
            self._mission_id = self._create_mission_row(
                mission_name, self._job_lat, self._job_lon
            )

            # ── Step 2: Upload images in CHUNKS, all under ONE batch_id ──
            # Each chunk is a small, reliable request; the SHARED batch_id makes the
            # consecutive-fail guard AND /cancel span the whole logical batch (a doomed
            # large batch then stops early instead of burning GPU on every image).
            def _register_chunk(chunk_files, chunk_meta):
                """Upload ONE chunk under self._batch_id (set by the first chunk).
                Returns this chunk's job_ids; raises on any backend error."""
                data = {
                    "per_image_meta": json.dumps(chunk_meta),
                    "mission_id":     self._mission_id or "",
                    # The NAME as well as the id: mission_id points into the
                    # PostgREST database, which billing cannot read, so the label
                    # has to travel with the upload for storage rows to carry it.
                    "mission_name":   mission_name,
                }
                if self._batch_id:
                    data["batch_id"] = self._batch_id      # join the existing batch

                # Downscale + recompress client-side so we send ~5-10x less data
                # (fail-safe: each image falls back to its original on any problem).
                upload_specs, temp_paths = self._prepare_upload_files(chunk_files)

                # Scale the upload timeout to the ACTUAL bytes we send, assuming a
                # conservative ~1 Mbps floor uplink (~125 KB/s), plus a fixed
                # allowance for the server re-uploading those bytes to object
                # storage before it replies (bridge_api /register does that
                # synchronously, so the client is still waiting through it).
                #
                # The old ceiling was 900 s, which SILENTLY DISCARDED the number
                # this formula computes: a default 50-image chunk of DJI frames is
                # about 419 MB, for which the calculation asks 3354 s and got 900.
                # A full chunk therefore needed a 3.7 Mbps uplink to survive, and
                # anything slower failed partway. Observed in the field: uploads died
                # around 23 images, which is exactly where 900 s runs out at
                # roughly 1.7 Mbps.
                try:
                    total_bytes = sum(os.path.getsize(sp) for _, sp in upload_specs)
                except OSError:
                    total_bytes = 0
                read_timeout = min(3600, max(180, int(total_bytes / 125_000) + 120))

                def _post_register():
                    """Send the multipart upload. File handles are re-opened on each
                    call so a token-refresh retry doesn't send already-consumed files.
                    The ORIGINAL filename is sent even when the bytes are a temp,
                    downscaled copy (keeps job→file naming correct)."""
                    fhs = []
                    files = []
                    for orig_name, send_path in upload_specs:
                        fh = open(send_path, "rb")
                        fhs.append(fh)
                        files.append(("images", (orig_name, fh)))
                    try:
                        hdr = {}
                        if self.access_token:
                            hdr["Authorization"] = f"Bearer {self.access_token}"
                        return requests.post(
                            REGISTER_URL, files=files, data=data, headers=hdr,
                            timeout=(15, read_timeout),   # (connect, read)
                        )
                    finally:
                        for fh in fhs:
                            fh.close()

                try:
                    response = _post_register()
                    # If the token expired mid-session, refresh once and retry.
                    if response.status_code == 401 and self._refresh_access_token():
                        response = _post_register()
                finally:
                    # Temp downscaled copies are no longer needed after the upload.
                    for tp in temp_paths:
                        try:
                            os.remove(tp)
                        except OSError:
                            pass

                if response.status_code == 401:
                    raise Exception("Session expired. Please sign in again.")
                if response.status_code == 402:
                    # the server sends a structured detail so we can guide the user
                    # correctly: out-of-credits vs. a paused (failed-payment) subscription.
                    info = {}
                    try:
                        d = response.json().get("detail")
                        if isinstance(d, dict):
                            info = d
                    except Exception:
                        pass
                    if info.get("code") == "SUBSCRIPTION_PAUSED":
                        raise Exception(info.get("message") or
                            "Your subscription is paused due to a failed payment. Open "
                            "'View Plans → Manage billing' to update your card, then try again.")
                    need, have = info.get("needed"), info.get("available")
                    # ⚠️ On a Free-only build the SERVER's message is deliberately
                    # NOT used. the server's 402 copy was written for a world with
                    # a purchase path and says to buy credits; rendering it here
                    # would send the user looking for a button this build removed.
                    # The server stays authoritative about the NUMBERS, which is
                    # what `needed` and `available` carry.
                    if not SHOW_PAYG:
                        renew = ("Your included images renew at the start of your "
                                 "next cycle. If you need more before then, contact "
                                 "sales@aivesystems.com.")
                        if need is not None and have is not None:
                            raise Exception(
                                f"This batch needs {need} images and you have {have} "
                                f"left. {renew}")
                        raise Exception(
                            f"You have used all of your included images. {renew}")
                    if need is not None and have is not None:
                        raise Exception(f"Not enough credits: this batch needs {need}, you have {have}. "
                                        "Tap 'Buy Credits' or 'View Plans' to top up, then try again.")
                    raise Exception(info.get("message") or
                        "You're out of credits. Tap 'Buy Credits' on the previous "
                        "screen to top up, then try again.")
                if response.status_code == 413:
                    # 413 has two causes: our storage-quota gate (structured detail
                    # dict) OR a single oversized file (plain string detail). The
                    # quota gate rejects BEFORE reserving credits — nothing charged.
                    detail = None
                    try:
                        detail = response.json().get("detail")
                    except Exception:
                        pass
                    if isinstance(detail, dict) and detail.get("code") == "STORAGE_QUOTA_EXCEEDED":
                        raise Exception(detail.get("message") or
                            "This upload would exceed your storage quota. Open the account "
                            "menu → 'Free up storage' to remove your oldest data, then try again.")
                    raise Exception(detail if isinstance(detail, str) and detail else
                        "A file is too large to upload. Please reduce file sizes and try again.")
                if response.status_code == 429:
                    # Server shed load (queue backpressure or per-user rate limit).
                    try:
                        detail = response.json().get("detail")
                    except Exception:
                        detail = None
                    raise Exception(detail or
                        "The processing service is busy right now. Please wait a moment and try again.")
                if response.status_code == 507:
                    # Shared storage nearly full — disk guard.
                    try:
                        detail = response.json().get("detail")
                    except Exception:
                        detail = None
                    raise Exception(detail or
                        "Server storage is temporarily full. Please try again a little later.")
                if response.status_code not in (200, 202):
                    raise Exception(f"Backend error {response.status_code}: {response.text}")

                result = response.json()
                chunk_ids = result.get("job_ids", [])
                if not chunk_ids:
                    raise Exception("No job IDs returned from backend")
                if not self._batch_id:
                    self._batch_id = result.get("batch_id")   # first chunk defines the batch
                return chunk_ids

            # Upload every chunk under one batch_id; on any mid-upload failure abort the
            # partial batch so the chunks already queued are skipped + their credits refunded.
            self._batch_id = None
            job_ids = []
            try:
                for _start in range(0, len(selected_files), UPLOAD_CHUNK_SIZE):
                    if self._cancel_flag.is_set():
                        self.processing_cancelled.emit()
                        return
                    job_ids.extend(_register_chunk(
                        selected_files[_start:_start + UPLOAD_CHUNK_SIZE],
                        per_image_meta[_start:_start + UPLOAD_CHUNK_SIZE],
                    ))
            except Exception:
                if self._batch_id and job_ids:
                    self._cancel_batch_backend()   # refund the chunks already queued
                raise

            self.progress_updated.emit(30, 1)

            # ── Step 3: Poll ALL jobs ──
            self.progress_updated.emit(50, 2)

            # Map each job to its source filename and mark all as Processing.
            job_files = {jid: os.path.basename(selected_files[i])
                         for i, jid in enumerate(job_ids)}
            idx_by_id = {jid: i for i, jid in enumerate(job_ids)}   # O(1) job->index (avoids O(n^2) .index)
            # ⚠️ THE REPORT RECONCILES AGAINST THIS, not against the
            # result dictionaries. Summing _job_results and _failed_jobs
            # undercounts, because a job that succeeds but cannot be written
            # to disk belongs to NEITHER: it is a storage failure, not a
            # processing one. Summing dictionaries can only report what they
            # happen to contain; counting from what was submitted makes a job
            # that falls out of every bucket visible instead of absent.
            self._submitted_jobs = dict(job_files)
            for fname in job_files.values():
                QtCore.QMetaObject.invokeMethod(
                    self, "_set_upload_status", QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, fname), QtCore.Q_ARG(str, "Processing"))

            total_jobs = len(job_ids)
            pending    = set(job_ids)
            # Scale the poll budget with batch size so large (chunked) batches aren't
            # abandoned mid-run; ~5s/poll, floor 180 (~15 min) for small batches.
            max_polls  = max(180, total_jobs * 6)
            poll_count = 0

            while pending and poll_count < max_polls:
                if self._cancel_flag.is_set():
                    self.processing_cancelled.emit()
                    return
                # Interruptible wait so Cancel takes effect within ~0.5s, not 5s.
                for _ in range(10):
                    if self._cancel_flag.is_set():
                        break
                    time.sleep(0.5)
                if self._cancel_flag.is_set():
                    self.processing_cancelled.emit()
                    return
                poll_count += 1

                # ONE batch call per cycle (resolved server-side in one call) instead of
                # N per-job GETs. Falls back to per-job if backend lacks /batch.
                statuses = self._fetch_job_statuses(list(pending))
                if not statuses:
                    continue   # transient fetch error — retry next cycle

                for jid in list(pending):
                    poll_data = statuses.get(jid)
                    if not poll_data:
                        continue
                    status = poll_data.get("status")

                    if status == "done":
                        dl_url   = poll_data.get("download_url")
                        tif_resp = self._authed_request("GET", dl_url, timeout=60)
                        if tif_resp.status_code != 200:
                            # Georef succeeded but the file download glitched —
                            # leave this job pending and retry on the next cycle
                            # rather than killing the whole batch.
                            continue
                        # Name the output after the ORIGINAL input file for traceability:
                        # "<inputname>_georef.tif", so each result maps 1:1 to its source
                        # image (was an opaque "atlas_result_<jobid>.tif").
                        #
                        # ⚠️ BOTH the directory and the stem come from THIS job's own
                        # source file. The directory used to come from selected_files[0],
                        # which is correct only while every file in a batch shares a
                        # parent. That holds today (getOpenFileNames selects within one
                        # directory, and the folder picker iterates one folder), so this
                        # is not a live bug -- but it made the output location depend on
                        # an invariant nothing enforces, and any future drag-and-drop or
                        # multi-folder selection would silently scatter results into the
                        # first image's directory.
                        job_index = idx_by_id[jid]
                        src_path = Path(selected_files[job_index])
                        out_path = src_path.parent / f"{src_path.stem}_georef.tif"

                        # ⚠️ THE WRITE IS GUARDED, AND A FAILURE HERE IS NOT A
                        # PROCESSING FAILURE. The backend produced a correct result, the
                        # image allowance was correctly consumed and the cloud copy
                        # exists; only local delivery failed. Unguarded, an OSError
                        # (disk full, read-only folder, permissions) propagated to the
                        # outer handler and aborted the WHOLE batch, reporting
                        # "processing failed" for images that had actually succeeded and
                        # been charged for. Billing is deliberately untouched by this
                        # path: the work was delivered.
                        ok, save_err = self._atomic_write(out_path, tif_resp.content)
                        if not ok:
                            e = save_err
                            self._local_save_failures[jid] = {
                                "file": job_files.get(jid, ""),
                                "path": str(out_path),
                                # Kept so the result can be re-downloaded WITHOUT
                                # reprocessing, which would consume another image
                                # from the allowance for work already paid for.
                                "download_url": dl_url,
                                "error": str(e),
                                # The download succeeded and the WRITE failed, so
                                # this one really is a disk/permissions problem.
                                # _retry_local_saves re-classifies on every
                                # attempt; this is the honest starting value.
                                "kind": "write",
                                # ⚠️ THE METRICS TRAVEL WITH THE FAILURE.
                                #
                                # A successful retry removes this entry. Without
                                # somewhere to put the job afterwards it lands in
                                # NO bucket, and a report driven by the submitted
                                # set then calls it "Unaccounted": a red banner
                                # claiming work was lost, immediately after that
                                # work was recovered. Keeping the result here
                                # means the retry can promote it to Saved.
                                "result": {
                                    "file":        job_files.get(jid, ""),
                                    "angle":       poll_data.get("angle"),
                                    "num_inliers": poll_data.get("num_inliers"),
                                    "tier":        poll_data.get("tier"),
                                    "quality":     poll_data.get("quality"),
                                    "elapsed_s":   poll_data.get("elapsed_s"),
                                    "lat":         per_image_meta[job_index]["lat"],
                                    "lon":         per_image_meta[job_index]["lon"],
                                },
                            }
                            # Printed as well as stored. When this happened
                            # live, the only evidence was a missing row in a
                            # PDF and the cause had to be inferred. Job id,
                            # source name, destination and the OS error.
                            print(
                                "ATLAS local save FAILED "
                                "job=%s file=%r dest=%s error=%r"
                                % (jid, job_files.get(jid, ""),
                                   out_path, save_err))
                            QtCore.QMetaObject.invokeMethod(
                                self, "_set_upload_status", QtCore.Qt.QueuedConnection,
                                QtCore.Q_ARG(str, job_files.get(jid, "")),
                                QtCore.Q_ARG(str, "Not saved"))
                            # Resolved, not pending: the job is finished server-side.
                            # Retrying the poll would not make the disk writable.
                            pending.discard(jid)
                            continue
                        self._result_paths.append(str(out_path))

                        # Get the lat/lon for THIS specific job from meta
                        job_lat = per_image_meta[job_index]["lat"]
                        job_lon = per_image_meta[job_index]["lon"]

                        # ── Capture the REAL result metrics from the backend ──
                        quality = poll_data.get("quality")   # "high" | "low" | None
                        self._job_results[jid] = {
                            "file":        job_files.get(jid, ""),
                            "angle":       poll_data.get("angle"),
                            "num_inliers": poll_data.get("num_inliers"),
                            "tier":        poll_data.get("tier"),
                            "quality":     quality,
                            "elapsed_s":   poll_data.get("elapsed_s"),
                            "lat":         job_lat,
                            "lon":         job_lon,
                        }
                        # Honest badge: a Tier-2 rigid fallback (low inliers) is
                        # georeferenced but less accurate — don't show it as a
                        # perfect lock.
                        badge = "Low confidence" if quality == "low" else "Processed"
                        QtCore.QMetaObject.invokeMethod(
                            self, "_set_upload_status", QtCore.Qt.QueuedConnection,
                            QtCore.Q_ARG(str, job_files.get(jid, "")),
                            QtCore.Q_ARG(str, badge))

                        self.layer_ready_signal.emit(str(out_path), job_lat, job_lon)

                        pending.discard(jid)

                    elif status == "failed":
                        # NON-FATAL: one image that couldn't be georeferenced
                        # (feature starvation / poor inliers) must NOT discard the
                        # whole batch. Record it, badge it, and keep going — the
                        # good results still complete.
                        #
                        # `billing` is what the server ACTUALLY did with the
                        # reservation. The old comment here asserted the token was
                        # "already refunded server-side", and that assertion is
                        # what the user-facing copy was built on. It is not true
                        # for a Free user whose imagery was rejected: the current
                        # policy consumes the allowance. Never infer
                        # this again; carry what the server said.
                        self._failed_jobs[jid] = {
                            "file":    job_files.get(jid, ""),
                            "reason":  poll_data.get("reason", "Low match quality"),
                            "billing": poll_data.get("billing"),
                        }
                        QtCore.QMetaObject.invokeMethod(
                            self, "_set_upload_status", QtCore.Qt.QueuedConnection,
                            QtCore.Q_ARG(str, job_files.get(jid, "")),
                            QtCore.Q_ARG(str, "Failed"))
                        pending.discard(jid)

                n_done = total_jobs - len(pending)
                pct    = int(50 + 30 * n_done / total_jobs)
                self.progress_updated.emit(pct, 2)

                # Live processing header: which file + how many done.
                current_file = next(iter(job_files[j] for j in pending), "—") if pending \
                    else (list(job_files.values())[-1] if job_files else "—")
                QtCore.QMetaObject.invokeMethod(
                    self, "_update_processing_header", QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, current_file),
                    QtCore.Q_ARG(int, n_done), QtCore.Q_ARG(int, total_jobs))

            # Any jobs still pending after max_polls timed out — treat each as a
            # per-job failure (non-fatal) rather than sinking the whole batch.
            for jid in list(pending):
                self._failed_jobs[jid] = {
                    "file":   job_files.get(jid, ""),
                    "reason": "Timed out",
                }
                QtCore.QMetaObject.invokeMethod(
                    self, "_set_upload_status", QtCore.Qt.QueuedConnection,
                    QtCore.Q_ARG(str, job_files.get(jid, "")),
                    QtCore.Q_ARG(str, "Failed"))
                pending.discard(jid)

            # Only surface the full error screen if NOTHING succeeded; otherwise
            # go to Results with a partial-success summary.
            #
            # ⚠️ _local_save_failures COUNTS AS SUCCESS HERE. Those jobs were
            # georeferenced and charged; only the write to this computer failed,
            # and the result is still on the server.
            #
            # Testing _job_results alone declared "None of the N image(s) could
            # be georeferenced" for a batch that had georeferenced every one of
            # them, then offered advice about difficult terrain. It fires exactly
            # when a mission is re-run over a folder whose previous outputs are
            # still open as QGIS layers, which is the ordinary case, not an edge
            # one.
            #
            # It also stranded the work: this path raises, which routes through
            # _on_processing_failed to _restart_mission, so the Results page
            # never renders and "Save results again" is never shown. The customer
            # was charged for a recoverable result with no way to reach it.
            if not self._job_results and not self._local_save_failures:
                # Group by cause with a count, rather than concatenating every
                # frame's full sentence. Four frames each contributing "Could not
                # georeference: ..." produced a wall of near-identical text in
                # which the one actionable line was buried at the very end.
                tally = {}
                for f in self._failed_jobs.values():
                    key = self._short_reason(f.get("reason") or "")
                    tally[key] = tally.get(key, 0) + 1
                bullets = "\n".join(
                    f"  •  {n} × {k}" for k, n in
                    sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])))
                raise Exception(
                    f"None of the {total_jobs} image(s) could be georeferenced.\n\n"
                    f"{bullets}\n\n"
                    f"{self._failure_causes_text()}\n\n"
                    f"{self._billing_outcome_text(total_jobs)}"
                )

            self.progress_updated.emit(100, -1)
            self.processing_complete.emit()

        except Exception as exc:
            self.processing_failed.emit(str(exc))
    

    # Altitude provenance values. The NUMBER alone is not enough: the same
    # field carries two different physical meanings depending on the camera,
    # and the worker's geometry is only valid for one of them.
    ALT_SOURCE_AGL = "relative_agl"   # DJI RelativeAltitude, height above takeoff
    ALT_SOURCE_MSL = "gps_msl"        # EXIF GPSAltitude, height above SEA LEVEL
    ALT_SOURCE_UNKNOWN = "unknown"    # nothing usable found

    def _extract_altitude_from_raw_xmp(self, image_path: str) -> float:
        """Backwards-compatible wrapper: the altitude value only.

        Kept so existing callers and tests keep their contract. New code should
        use _extract_altitude_with_source, because the source is what decides
        whether the value may be used as AGL.
        """
        return self._extract_altitude_with_source(image_path)[0]

    def _extract_altitude_with_source(self, image_path: str):
        """Return (altitude_m, altitude_source).

        Prefers DJI's RelativeAltitude (AGL, relative to takeoff) from XMP.
        Falls back to EXIF GPSAltitude for non-DJI sources, which is Above Sea
        Level, NOT above ground.

        Reporting WHICH source produced the number is the whole point. On a DJI
        flight the value is AGL-like and the worker's footprint geometry holds.
        On a Sony ILX-LR1 there is no DJI XMP, so the fallback returns roughly
        367 m of sea-level altitude for a flight a few hundred metres over
        Austin, and any calculation that treats that as height above ground is
        working from a number that does not mean what it assumes.

        The reference benchmark set is entirely DJI, so it only ever exercised
        the first branch. That is why this stayed invisible.
        """
        import re
        with open(image_path, 'rb') as f:
            raw = f.read(1024 * 512)
        text = raw.decode('utf-8', errors='ignore')
        patterns = [
            r'drone-dji:RelativeAltitude="([+-]?\d+\.?\d*)"',
            r'<drone-dji:RelativeAltitude>([+-]?\d+\.?\d*)</drone-dji:RelativeAltitude>',
            r'RelativeAltitude["\s=>:]+([+-]?\d+\.?\d*)',
        ]
        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                return float(m.group(1)), self.ALT_SOURCE_AGL
        # fallback: try reading more bytes
        with open(image_path, 'rb') as f:
            raw = f.read(1024 * 1024 * 2)
        text = raw.decode('utf-8', errors='ignore')
        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                return float(m.group(1)), self.ALT_SOURCE_AGL
        # No DJI tag anywhere — try standard EXIF GPSAltitude (non-DJI cameras).
        # GPSAltitudeRef 0 means above SEA LEVEL, 1 means below it. Neither is
        # above ground, which is why this branch reports gps_msl.
        try:
            import rasterio
            with rasterio.open(image_path) as src:
                tags = src.tags()
                alt_str = tags.get('EXIF_GPSAltitude', '')
                if alt_str:
                    m = re.search(r'(\d+(?:\.\d+)?)', str(alt_str))
                    if m:
                        altitude = float(m.group(1))
                        ref = str(tags.get('EXIF_GPSAltitudeRef', '0'))
                        if '1' in ref:
                            altitude = -altitude
                        return altitude, self.ALT_SOURCE_MSL
        except Exception:
            pass
        return None, self.ALT_SOURCE_UNKNOWN

    def _extract_focal35_from_raw_xmp(self, image_path: str) -> float:
        """Extract FocalLengthIn35mmFilm from EXIF or XMP."""
        import re
        # First try standard EXIF via rasterio
        import rasterio
        try:
            with rasterio.open(image_path) as src:
                tags = src.tags()
                focal_str = tags.get('EXIF_FocalLengthIn35mmFilm', '')
                if focal_str:
                    m = re.search(r'(\d+(?:\.\d+)?)', str(focal_str))
                    if m:
                        return float(m.group(1))
        except Exception:
            pass  # EXIF read failed — fall through to raw-XMP parsing below
        # Then try raw XMP
        with open(image_path, 'rb') as f:
            raw = f.read(1024 * 512)
        text = raw.decode('utf-8', errors='ignore')
        patterns = [
            r'exif:FocalLengthIn35mmFilm="([+-]?\d+\.?\d*)"',
            r'<exif:FocalLengthIn35mmFilm>([+-]?\d+\.?\d*)</exif:FocalLengthIn35mmFilm>',
            r'FocalLengthIn35mmFilm["\s=>:]+([+-]?\d+\.?\d*)',
        ]
        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                return float(m.group(1))
        # Nothing found. Return None, not a guess.
        #
        # This used to return 24.0 ("common for DJI Mini 4 Pro"), which made the
        # value indistinguishable from a measured 24 mm lens. That mattered
        # because worker.save_result derives an expected ground footprint from
        # this number and REFUSES the matcher when the placement disagrees --
        # sound against a real lens, meaningless against a default.
        #
        # Measured against frames that carry no FocalLengthIn35mmFilm: an
        # assumed 24 mm put the derived footprint tens of percent away from the
        # true one, so correct placements with high inlier counts were refused.
        # The implied focal lengths sat well above 24 mm in every case.
        #
        # Every consumer still applies its own default where a number is needed
        # (compute_tile_grid_radius uses `focal35 or 24`), so returning None
        # costs nothing and lets the scale check tell measured from assumed.
        return None

    def _extract_gps_from_image(self, image_path: str) -> tuple:
        import re
        import rasterio

        def parse_dms(raw: str) -> float:
            nums = re.findall(r'[\d.]+', raw)
            if len(nums) >= 3:
                return float(nums[0]) + float(nums[1])/60.0 + float(nums[2])/3600.0
            if len(nums) == 1:
                return float(nums[0])
            raise ValueError(f"Cannot parse DMS: '{raw}'")

        try:
            with rasterio.open(image_path) as src:
                tags = src.tags()

                # GPS from flat GDAL tags
                lat_raw = tags.get("EXIF_GPSLatitude", "").strip()
                lat_ref = tags.get("EXIF_GPSLatitudeRef", "N").strip().upper()
                lon_raw = tags.get("EXIF_GPSLongitude", "").strip()
                lon_ref = tags.get("EXIF_GPSLongitudeRef", "E").strip().upper()

                if not lat_raw or not lon_raw:
                    raise ValueError("GPS tags not found in image.")

                lat = parse_dms(lat_raw)
                lon = parse_dms(lon_raw)
                if lat_ref == "S": lat = -lat
                if lon_ref == "W": lon = -lon

                # ── Step 2: Get yaw by reading raw XMP bytes from the TIFF ──
                # rasterio 1.5.0 on Windows doesn't expose ApplicationNotes,
                # but we can read the raw file bytes and find the XMP block directly.
                # None, not 0.0, when nothing is known. 0.0 is a REAL heading
                # (due north), so defaulting to it told the server "this image
                # faces north" and had the rotation search start there. None
                # says "unknown", which is what the search actually handles.
                heading = None
                try:
                    heading = self._extract_yaw_from_raw_xmp(image_path)
                except Exception:
                    heading = self._extract_heading_from_exif(image_path)

                return lat, lon, heading

        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Failed to read image: {e}")


    def _extract_heading_from_exif(self, image_path: str):
        """Camera heading from the standard EXIF GPSImgDirection tag, or None.

        Fallback for non-DJI cameras, which write no drone-dji XMP block. Mirrors
        what _extract_altitude_from_raw_xmp already does for altitude.

        Heading is NOT cosmetic. The server orders its 12 coarse rotation
        candidates by proximity to the heading and stops at the first angle
        clearing skip_fine_threshold, so with no prior the search starts at 0 deg
        and settles on the first ACCEPTABLE angle rather than the best one. A
        correct heading makes it try the right rotation first.

        GPSImgDirectionRef distinguishes true from magnetic north. We do not
        correct for magnetic declination -- up to ~15 deg in some regions -- so a
        magnetic reading is returned as-is and simply makes the ordering slightly
        less accurate, which is still far better than no prior at all.
        """
        import re
        try:
            import rasterio
            with rasterio.open(image_path) as src:
                tags = src.tags()
            raw = tags.get("EXIF_GPSImgDirection", "")
            if not raw:
                return None
            m = re.search(r"(-?\d+(?:\.\d+)?)", str(raw))
            if not m:
                return None
            # Some writers store it as a rational "1234/10".
            if "/" in str(raw):
                num, _, den = str(raw).partition("/")
                try:
                    val = float(re.sub(r"[^0-9.\-]", "", num)) / float(
                        re.sub(r"[^0-9.\-]", "", den) or 1)
                except (ValueError, ZeroDivisionError):
                    val = float(m.group(1))
            else:
                val = float(m.group(1))
            return val % 360.0
        except Exception:
            return None

    def _extract_yaw_from_raw_xmp(self, image_path: str) -> float:
        """
        Read the raw TIFF bytes to find the DJI XMP block and extract GimbalYawDegree.
        Works even when rasterio can't expose ApplicationNotes.
        
        Your DEMO-01.tif confirmed: GimbalYawDegree = 116.3
        """
        import re

        # Read enough bytes to find the XMP block
        # XMP in TIFFs starts with <?xpacket and contains the drone-dji namespace
        with open(image_path, 'rb') as f:
            # XMP is usually in the first few MB of the file
            raw = f.read(1024 * 512)  # read first 512KB

        # Decode ignoring errors — XMP is UTF-8 text embedded in binary
        text = raw.decode('utf-8', errors='ignore')

        # Look for GimbalYawDegree in drone-dji XMP namespace
        # Format in XMP: drone-dji:GimbalYawDegree="116.3"
        #            or: <drone-dji:GimbalYawDegree>116.3</drone-dji:GimbalYawDegree>
        patterns = [
            r'drone-dji:GimbalYawDegree="([+-]?\d+\.?\d*)"',
            r'<drone-dji:GimbalYawDegree>([+-]?\d+\.?\d*)</drone-dji:GimbalYawDegree>',
            r'GimbalYawDegree["\s=>:]+([+-]?\d+\.?\d*)',
        ]

        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                return float(m.group(1))

        # XMP might be beyond 512KB — try reading more if not found
        with open(image_path, 'rb') as f:
            raw = f.read(1024 * 1024 * 2)  # 2MB
        text = raw.decode('utf-8', errors='ignore')
        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                return float(m.group(1))

        raise ValueError("GimbalYawDegree not found in XMP")

    # Tilt away from straight down, in degrees, past which we warn the user.
    # Measured against a labelled reference set: accuracy falls off gently
    # until roughly 40 degrees and steeply after it, so that is where the
    # warning sits. Below this the imagery is worth processing.
    OBLIQUE_WARN_TILT_DEG = 40.0

    def _billing_outcome_text(self, total_jobs: int) -> str:
        """What actually happened to the user's allowance, from the SERVER.

        This used to be a fixed sentence claiming "You have not been charged.
        The credits reserved for all N image(s) are being returned to your
        balance." That was written when every failure refunded. It is false for
        the most common case under the current policy: a Free user whose
        imagery could not be registered has the allowance CONSUMED.

        A balance that visibly disagrees with the message is worse than saying
        nothing, so this reports only what the server confirmed.
        """
        # Only these two outcomes may become a statement about the user's money.
        # Anything else -- absent, unrecognised, or an older server that does not
        # send the field -- is UNKNOWN and produces no claim. Never infer
        # "consumed" from the absence of "refunded".
        REFUNDED, CONSUMED = "credit_refunded", "included_image_used"
        outcomes = [
            (f.get("billing") or {}).get("outcome")
            for f in self._failed_jobs.values()
        ]
        known = [o for o in outcomes if o in (REFUNDED, CONSUMED)]

        if not known:
            return ("Check your balance to see how this run affected your "
                    "remaining images.")

        refunded = sum(1 for o in known if o == REFUNDED)
        consumed = sum(1 for o in known if o == CONSUMED)

        if consumed and not refunded:
            noun = "image has" if consumed == 1 else "images have"
            # Deliberately narrow. "Images that cannot be matched still use an
            # image" was too broad: it implies EVERY matching failure costs the
            # user, when only input-rejection does. A platform fault returns the
            # reservation, and saying otherwise would misdescribe our own
            # outages as the customer's problem.
            return (f"{consumed} included {noun} been used. Images rejected "
                    f"because the input could not be processed still use an "
                    f"image from your Free allowance.")
        if refunded and not consumed:
            noun = "credit has" if refunded == 1 else "credits have"
            return (f"{refunded} {noun} been returned to your balance.")
        # Mixed: some returned, some not. Report both rather than rounding to
        # whichever is more flattering.
        return (f"{consumed} included image(s) used, and {refunded} credit(s) "
                f"returned to your balance.")

    def _failure_causes_text(self) -> str:
        """Why georeferencing tends to fail, for the results screen.

        Names the likely causes rather than leaving a bare failure count. Leads with the steep-angle case when the
        batch actually contained tilted frames, since that is specific rather
        than generic advice. Percentages come from our measurements against
        a labelled reference dataset.
        """
        parts = []
        # Leads, and stands alone. When every frame reported the same position we
        # already know why the batch failed, and the generic terrain advice below
        # would be actively misleading: it sends the operator to inspect imagery
        # that was never the problem: a frozen GPS track produces exactly this,
        # and terrain advice explains none of it.
        if getattr(self, "_frozen_gps_batch", False):
            return (
                "Every image in this batch reported the same GPS position, which "
                "you were warned about before uploading. Each frame was therefore "
                "matched against the same patch of ground, so only images actually "
                "taken over that point could succeed. If the aircraft was moving, "
                "the position data recorded by the camera is faulty and these "
                "images cannot be georeferenced until that is corrected.")

        tilted = getattr(self, "_tilted_files", None)
        if tilted:
            parts.append(
                f"{len(tilted)} image(s) in this batch were taken at a steep angle. "
                "Angled imagery is harder to place because the reference map is a "
                "straight-down view")
        parts.append(
            "Georeferencing relies on matching ground detail against satellite "
            "imagery, so it is most likely to fail where there is little "
            "distinctive detail to match: open, arid or uniform terrain, dense "
            "canopy, and water")
        parts.append(
            "Very low flights can also struggle, because less ground is visible "
            "in each frame")
        return ". ".join(parts) + "."

    def _extract_pitch_from_raw_xmp(self, image_path: str):
        """Gimbal pitch from the drone-dji XMP block, or None if absent.

        DJI writes -90 for straight down and 0 for level with the horizon, so
        tilt away from nadir is (pitch + 90). Returns None rather than raising:
        an unknown pitch must never block an upload, it only means we cannot
        offer advice about that frame.
        """
        patterns = [
            r'drone-dji:GimbalPitchDegree="([+-]?\d+\.?\d*)"',
            r'<drone-dji:GimbalPitchDegree>([+-]?\d+\.?\d*)</drone-dji:GimbalPitchDegree>',
            r'GimbalPitchDegree["\s=>:]+([+-]?\d+\.?\d*)',
        ]
        try:
            for size in (1024 * 512, 1024 * 1024 * 2):
                with open(image_path, 'rb') as f:
                    text = f.read(size).decode('utf-8', errors='ignore')
                for pattern in patterns:
                    m = re.search(pattern, text)
                    if m:
                        return float(m.group(1))
        except Exception:
            pass
        return None

    def _tilt_from_nadir(self, image_path: str):
        """Degrees away from straight down, or None when the pitch is unknown."""
        pitch = self._extract_pitch_from_raw_xmp(image_path)
        if pitch is None:
            return None
        return abs(pitch + 90.0)

    # ─────────────────────────────────────────────────────────
    # ADD LAYER TO QGIS
    # ─────────────────────────────────────────────────────────
    def _auto_load_enabled(self) -> bool:
        """Whether the 'Auto-load to canvas' toggle is on (default True if missing)."""
        cb = getattr(self, "checkbox_auto_load", None)
        return cb.isChecked() if cb is not None else True

    def _drop_layers_for_path(self, path: str):
        """Remove any layer already reading this file, before it is re-added.

        Result filenames are deterministic (<source>_georef.tif), so processing the
        same image twice OVERWRITES the file underneath a layer that is still on the
        canvas. QGIS keeps the old raster dimensions cached and then asks GDAL for
        rows that no longer exist:

            Access window out of range in RasterIO().
            Requested (0,0) of size 1142x1659 on raster of 1703x580

        The file is fine -- it is the stale layer that is wrong. Dropping it first
        forces QGIS to re-read the header instead of trusting what it cached.
        """
        try:
            target = os.path.normcase(os.path.abspath(path))
            proj = QgsProject.instance()
            for lyr in list(proj.mapLayers().values()):
                try:
                    src = lyr.source().split("|")[0]      # strip any provider suffix
                    if os.path.normcase(os.path.abspath(src)) == target:
                        proj.removeMapLayer(lyr.id())
                except Exception:
                    continue
        except Exception as e:
            print(f"stale-layer cleanup skipped: {e}")

    def _add_result_layer(self, path: str, name: str):
        """Load a result GeoTIFF onto the canvas with standard ATLAS styling
        (black warp borders transparent, 0.75 opacity). Returns the layer or None."""
        # A layer still pointing at this path holds the PREVIOUS run's dimensions.
        self._drop_layers_for_path(path)
        layer = QgsRasterLayer(path, name)
        if not layer.isValid():
            return None
        QgsProject.instance().addMapLayer(layer)
        try:
            rt = layer.renderer().rasterTransparency()
            px = rt.TransparentThreeValuePixel()
            px.red = 0; px.green = 0; px.blue = 0
            rt.setTransparentThreeValuePixelList([px])
        except Exception:
            pass
        layer.setOpacity(0.75)
        return layer

    @QtCore.pyqtSlot(str, float, float)
    def _add_warped_layer_to_qgis(self, path: str, lat: float, lon: float):
        """
        Loads the downloaded .tif onto the QGIS canvas.

        FIX: The warped TIF from the worker has no geotransform (as seen in
        the rasterio warnings in the logs). We embed a rough affine transform
        centred on the job's GPS coordinates so QGIS places it correctly on
        the map instead of dumping it at (0, 0).
        """
        if not os.path.exists(path):
            self.iface.messageBar().pushMessage("ATLAS", f"Result file not found: {path}", level=2)
            return
        # Drop any layer still reading this path BEFORE we touch the file. Result
        # names are deterministic, so reprocessing the same image overwrites it
        # underneath an open layer -- and on Windows that open handle can block the
        # rewrite outright, not merely leave QGIS with stale dimensions.
        self._drop_layers_for_path(path)
        try:
            with rasterio.open(path) as src:
                has_geo = (
                    src.crs is not None and
                    src.transform != rasterio.transform.IDENTITY
                )
                width  = src.width
                height = src.height
                count  = src.count
                dtype  = src.dtypes[0]

            if not has_geo:
                import math
                meters_per_px = 0.3
                deg_per_px_lat = meters_per_px / 111320.0
                deg_per_px_lon = meters_per_px / (111320.0 * math.cos(math.radians(lat)))

                west  = lon - (width  / 2) * deg_per_px_lon
                north = lat + (height / 2) * deg_per_px_lat

                transform = from_bounds(
                    west, north - height * deg_per_px_lat,
                    west + width * deg_per_px_lon, north,
                    width, height,
                )
                # The transform above is built in WGS84 degrees (deg_per_px,
                # west/north are lon/lat), so the CRS MUST be EPSG:4326. Tagging
                # it 3857 made QGIS read the degree coords as Web-Mercator metres
                # and dumped the layer at ~(0,0) null island. QGIS reprojects
                # 4326 -> the canvas CRS automatically.
                crs = CRS.from_epsg(4326)

                # Re-write the TIF in place with the new geotransform
                with rasterio.open(path) as src:
                    data = src.read()

                profile = {
                    "driver":    "GTiff",
                    "height":    height,
                    "width":     width,
                    "count":     count,
                    "dtype":     dtype,
                    "crs":       crs,
                    "transform": transform,
                    "compress":  "lzw",
                }
                with rasterio.open(path, "w", **profile) as dst:
                    dst.write(data)

        except Exception as geo_err:
            self.iface.messageBar().pushMessage(
                "ATLAS", f"Georeference step failed: {geo_err}", level=1, duration=5
            )
        # ── Load into QGIS ───────────────────────────────────
        # Distinct, identifiable name per image so a batch's layers are
        # tell-apart-able (and still start with "ATLAS" for View-on-Map lookup).
        # The saved file is now named after the input ("<inputname>_georef.tif"), so the
        # layer name comes straight from the filename — traceable to the source image.
        stem = Path(path).stem
        srcname = stem[:-7] if stem.endswith("_georef") else stem
        layer_name = f"ATLAS · {srcname}"

        # Auto-load to canvas (Setup-page toggle): when OFF, the result is still
        # downloaded + georeferenced on disk (tracked in _result_paths) and gets
        # loaded on demand by "View on Map" — we just don't add it to the canvas now.
        if not self._auto_load_enabled():
            return
        lyr = self._add_result_layer(path, layer_name)
        if lyr is not None:
            self.iface.setActiveLayer(lyr)
            self.iface.zoomToActiveLayer()
            self.iface.mapCanvas().refresh()
        else:
            self.iface.messageBar().pushMessage("ATLAS", "Invalid raster layer.", level=2)

    # ─────────────────────────────────────────────────────────
    # PROGRESS / STATUS
    # ─────────────────────────────────────────────────────────
    @staticmethod
    def _clear_layout(layout):
        """Remove every child — handles both widgets AND nested layouts.

        setParent(None) BEFORE deleteLater(), and it matters visually.

        deleteLater() only SCHEDULES destruction for the next event-loop pass.
        takeAt() drops the widget from the layout, so it stops being positioned,
        but it stays parented to the panel and keeps painting at its last
        geometry until the deferred delete runs. This panel is rebuilt on every
        poll, so the stale labels sat underneath the new ones and the text
        overlapped: "estimating" from one pass and "~0s" from the next rendered
        on the same pixels as "estimating0s".

        setParent(None) detaches it from the widget tree immediately, so it
        stops painting now rather than eventually. deleteLater() still frees it.
        """
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
                continue
            child = item.layout()
            if child is not None:
                AtlasGeoHandlerDemoDialog._clear_layout(child)
                child.deleteLater()

    @staticmethod
    def _section_header(text: str) -> QtWidgets.QLabel:
        # Industrial section header: UPPERCASE + real letter-spacing (QFont; QSS
        # `letter-spacing` is silently ignored by Qt) + a sharp orange accent bar.
        from qgis.PyQt.QtGui import QFont
        lbl = QtWidgets.QLabel(text.upper())
        f = lbl.font()
        f.setBold(True)
        try:
            f.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
        except Exception:
            pass
        lbl.setFont(f)
        lbl.setStyleSheet(
            "font-size: 10px; color: #9a948c; border-left: 3px solid #EA580C;"
            " padding-left: 7px; margin-bottom: 2px;"
        )
        return lbl

    @staticmethod
    def _stat_row(left: str, right: str, right_color: str = "#ea580c") -> QtWidgets.QWidget:
        from qgis.PyQt.QtGui import QFont
        row = QtWidgets.QWidget()
        hl = QtWidgets.QHBoxLayout(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(4)
        l = QtWidgets.QLabel(left)
        l.setStyleSheet("font-size: 10px; color: #78716c;")
        r = QtWidgets.QLabel(right)
        # Monospace value, right-aligned -> tabular telemetry readout (QSS can't do
        # tabular-nums; a monospace QFont gives fixed-width digits).
        mono = QFont("Roboto Mono"); mono.setStyleHint(QFont.Monospace); mono.setBold(True)
        r.setFont(mono)
        r.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
        r.setStyleSheet(f"font-size: 11px; color: {right_color}; font-weight: bold;")
        hl.addWidget(l)
        hl.addStretch()
        hl.addWidget(r)
        return row

    def _style_processing_page(self):
        """One-time redesign of PAGE_PROCESSING (approved mockup): de-boxed sections on
        the beige bg, one overall bar + a single vertical 7-stage stepper, a dark
        terminal for Mission Insights, a tidied header (meta under the title), and a
        standard bottom-right Cancel. Pure runtime restyle/reflow — reversible."""
        if getattr(self, "_proc_styled", False):
            return
        from qgis.PyQt.QtGui import QFont
        vlp = getattr(self, "verticalLayout_processing", None)

        for n in ("frame_progress_card", "groupbox_pipeline_viz", "frame_stats_card",
                  "groupbox_completed_stages", "groupbox_timing", "groupbox_process_status"):
            w = getattr(self, n, None)
            if w is not None:
                w.setStyleSheet("QFrame#%s, QGroupBox#%s { background:transparent; border:none; }" % (n, n))

        # Overall progress bar: ultra-thin solid orange (kept at top).
        pb = getattr(self, "progressbar_processing", None)
        if pb is not None:
            pb.setMinimumHeight(5); pb.setMaximumHeight(5); pb.setTextVisible(False)
            pb.setStyleSheet("QProgressBar { border:none; background:#e7e2d9; border-radius:2px; }"
                             "QProgressBar::chunk { background:#ea580c; border-radius:2px; }")

        si = getattr(self, "label_step_indicator_2", None)
        if si is not None:
            si.setStyleSheet("font-size:11px; font-weight:800; letter-spacing:1px;"
                             " color:#9ca3af; background:transparent; border:none;")
        badge = getattr(self, "label_frame_badge", None)
        if badge is not None:
            badge.hide()
        title = getattr(self, "label_screen2_title", None)
        pf = getattr(self, "label_processing_file", None)
        fc = getattr(self, "label_frame_counter", None)
        if vlp is not None and title is not None and pf is not None and fc is not None:
            ti = vlp.indexOf(title)
            meta_css = ("font-size:11px; color:#78716c; background:transparent;"
                        " font-family:'Consolas','Roboto Mono',monospace;")
            pf.setParent(None); pf.setStyleSheet(meta_css)
            fc.setParent(None); fc.setStyleSheet(meta_css)
            sep = QtWidgets.QLabel("·"); sep.setStyleSheet("color:#cbd5e1; background:transparent;")
            sub = QtWidgets.QHBoxLayout(); sub.setContentsMargins(0, 0, 0, 0); sub.setSpacing(8)
            sub.addWidget(pf); sub.addWidget(sep); sub.addWidget(fc); sub.addStretch(1)
            vlp.insertLayout(ti + 1 if ti >= 0 else vlp.count(), sub)

        self._build_pipeline_stepper()
        gpv = getattr(self, "groupbox_pipeline_viz", None)
        if gpv is not None:
            gpv.setMaximumHeight(16777215)   # was capped at 145 -> would clip 7 rows
        ps = getattr(self, "groupbox_process_status", None)
        if ps is not None:
            ps.hide()                         # old 4-step list now redundant

        # Two-column balance: the pipeline stepper (01-07) and the stats card
        # (Completed Frames + Timing) were stacked vertically, leaving a big void to
        # the right of the progress bar. Put the stepper LEFT and the stats card RIGHT.
        #
        # But the stats card ships as Completed | Timing SIDE-BY-SIDE, so a naive
        # 2-col split renders as 3 columns and overflows the window width. So we FIRST
        # re-stack the stats card's two blocks VERTICALLY (Completed on top of Timing),
        # then the right column is narrow + tall — a true balanced 2-column grid that
        # fits the width. Guarded so a lookup miss falls back to stacked (never breaks).
        try:
            stats = getattr(self, "frame_stats_card", None)
            comp  = getattr(self, "groupbox_completed_stages", None)
            tim   = getattr(self, "groupbox_timing", None)
            hl    = getattr(self, "hl_stages_timing", None)   # the card's inner HBox

            # Step 1: re-stack Completed + Timing vertically inside the stats card.
            if stats is not None and comp is not None and tim is not None and hl is not None:
                # Hide the old vertical divider (it separated the two side-by-side cols).
                for i in range(hl.count()):
                    w = hl.itemAt(i).widget()
                    if w is not None and w not in (comp, tim):
                        w.hide()   # the VLine divider
                hl.removeWidget(comp)
                hl.removeWidget(tim)
                v_stack = QtWidgets.QVBoxLayout()
                v_stack.setContentsMargins(0, 0, 0, 0)
                v_stack.setSpacing(6)
                v_stack.addWidget(comp)   # COMPLETED FRAMES on top
                v_stack.addWidget(tim)    # TIMING underneath
                hl.addLayout(v_stack)

            # Step 2: stepper (left) + stats card (right), side-by-side.
            if vlp is not None and gpv is not None and stats is not None:
                gpv_idx = vlp.indexOf(gpv)
                if gpv_idx >= 0:
                    vlp.removeWidget(gpv)
                    vlp.removeWidget(stats)      # no-op if it lives elsewhere
                    two_col = QtWidgets.QHBoxLayout()
                    two_col.setContentsMargins(0, 0, 0, 0)
                    two_col.setSpacing(18)
                    two_col.addWidget(gpv, 1)    # pipeline stepper (01-07) — left
                    two_col.addWidget(stats, 1)  # Completed / Timing (stacked) — right
                    two_col.setAlignment(gpv, QtCore.Qt.AlignTop)
                    two_col.setAlignment(stats, QtCore.Qt.AlignTop)
                    vlp.insertLayout(gpv_idx, two_col)
        except Exception as e:
            print(f"processing two-column layout failed: {e}")

        ins = getattr(self, "groupbox_insights", None)
        if ins is not None:
            ins.setStyleSheet("QFrame#groupbox_insights, QGroupBox#groupbox_insights {"
                              " background:#1b1b1f; border:1px solid #2a2a2f; border-radius:6px; }")
        mono = QFont("Consolas"); mono.setStyleHint(QFont.Monospace)
        for n, col in (("label_insight_quality", "#34d399"),   # status   green
                       ("label_insight_speed",   "#e5e7eb"),    # step     standard
                       ("label_insight_eta",     "#60a5fa")):   # eta      blue
            w = getattr(self, n, None)
            if w is not None:
                w.setFont(mono)
                w.setStyleSheet(f"background:transparent; border:none; color:{col}; font-size:11px;")

        cb = getattr(self, "btn_cancel_processing", None)
        if cb is not None:
            cb.setText("Cancel mission")
            cb.setMinimumHeight(36); cb.setMinimumWidth(140); cb.setMaximumWidth(190)
            # Neutral grey border (red text only) so it doesn't read as an
            # error/disabled state; border warms to red on hover to signal the
            # destructive action.
            cb.setStyleSheet(
                "QPushButton { background:#ffffff; color:#b91c1c; border:1px solid #e2ddd8;"
                "  border-radius:6px; padding:8px 18px; font-size:12px; font-weight:700; }"
                "QPushButton:hover { background:#fef2f2; border-color:#f87171; }")
            ci = vlp.indexOf(cb) if vlp is not None else -1
            if ci >= 0:
                cb.setParent(None)
                hb = QtWidgets.QHBoxLayout(); hb.addStretch(1); hb.addWidget(cb)
                vlp.insertLayout(ci, hb)

        self._proc_styled = True

    def _build_pipeline_stepper(self):
        """Build the single vertical 7-stage stepper inside the (de-boxed) pipeline
        card; coloured by _set_pipeline_stage()."""
        vl = getattr(self, "vl_pipeline_viz", None)
        if vl is None:
            return
        self._clear_layout(vl)
        hdr = QtWidgets.QLabel("PIPELINE")
        hdr.setStyleSheet("font-size:9px; font-weight:700; letter-spacing:1.5px;"
                          " color:#9ca3af; background:transparent;")
        vl.addWidget(hdr)
        self._stage_rows = []
        for i, name in enumerate(["Received", "Tiles", "Coarse", "Fine", "Match", "Warp", "Save"]):
            row = QtWidgets.QWidget()
            rl = QtWidgets.QHBoxLayout(row); rl.setContentsMargins(0, 3, 0, 3); rl.setSpacing(10)
            dot = QtWidgets.QLabel("○"); dot.setFixedWidth(14)
            dot.setStyleSheet("font-size:13px; color:#cbd5e1; background:transparent;")
            no = QtWidgets.QLabel(f"{i+1:02d}"); no.setFixedWidth(22)
            no.setStyleSheet("font-size:11px; color:#9ca3af; background:transparent;"
                             " font-family:'Consolas',monospace;")
            nm = QtWidgets.QLabel(name)
            nm.setStyleSheet("font-size:13px; color:#9ca3af; background:transparent;")
            rl.addWidget(dot); rl.addWidget(no); rl.addWidget(nm); rl.addStretch(1)
            vl.addWidget(row)
            self._stage_rows.append((dot, no, nm))

    def _set_pipeline_stage(self, percent):
        """Light the vertical stepper from the overall %: done=green, active=orange,
        upcoming=gray. (Backend emits coarse progress; 7 stages mapped by % band.)"""
        rows = getattr(self, "_stage_rows", None)
        if not rows:
            return
        n = len(rows)
        active = n if percent >= 100 else min(n - 1, int(percent * n / 100.0))
        for i, (dot, no, nm) in enumerate(rows):
            if i < active:          # done -> green
                dot.setText("●"); dot.setStyleSheet("font-size:13px; color:#10b981; background:transparent;")
                no.setStyleSheet("font-size:11px; color:#10b981; background:transparent; font-family:'Consolas',monospace;")
                nm.setStyleSheet("font-size:13px; color:#57534e; background:transparent;")
            elif i == active:       # active -> orange, bold
                dot.setText("●"); dot.setStyleSheet("font-size:13px; color:#ea580c; background:transparent;")
                no.setStyleSheet("font-size:11px; color:#ea580c; background:transparent; font-family:'Consolas',monospace;")
                nm.setStyleSheet("font-size:13px; font-weight:700; color:#1a1612; background:transparent;")
            else:                   # upcoming -> gray
                dot.setText("○"); dot.setStyleSheet("font-size:13px; color:#cbd5e1; background:transparent;")
                no.setStyleSheet("font-size:11px; color:#9ca3af; background:transparent; font-family:'Consolas',monospace;")
                nm.setStyleSheet("font-size:13px; color:#9ca3af; background:transparent;")

    @staticmethod
    def _clean_server_text(text):
        """Strip em dashes out of anything the server wrote before showing it.

        Our own product copy never uses them (flagged repeatedly as an "AI
        generated" tell), but the worker's failure reasons did, and those are
        rendered verbatim in the error dialog and the exported report. Fixed at
        source as well; this keeps messages already queued server-side, and any
        worker not yet redeployed, rendering cleanly in the meantime.
        """
        s = str(text or "")
        # The first one separates a clause from its explanation, which reads as a
        # colon. Any further ones are mid-sentence asides, which read as commas.
        s = re.sub(r"\s*[—–]\s*", ": ", s, count=1)
        s = re.sub(r"\s*[—–]\s*", ", ", s)
        return re.sub(r":\s*:", ":", s)

    @staticmethod
    def _short_reason(reason):
        """A few words naming the cause, for the narrow value column.

        The full sentence used to go here middle-elided, which rendered as
        "Could not ...is matcher)." — every failing frame looked identical and
        none of them said anything. The whole sentence is still on the row's
        tooltip and in the exported report; this is the at-a-glance label.

        WHY NOT ONE GENERIC PHRASE. Flattening every failure to "could not place
        this image" reads more kindly and destroys the only diagnostic the
        operator gets. A "wrong scale" on one frame sitting beside "too few
        matches" on the others points at faulty position data rather than
        difficult terrain. A surveyor who can see that one frame
        failed differently can act on it; one told four times that placement
        failed cannot. So the causes stay distinct, and are worded for a GIS user
        instead of for us. The full sentence is on the tooltip either way.
        """
        s = str(reason or "").lower()
        if "too few" in s:
            return "not enough detail"
        if "covers" in s and "expected" in s:
            return "size vs altitude"
        if ("corners crossed" in s or "geometrically impossible" in s
                or "not a valid shape" in s):
            return "distorted placement"
        if "outside the reference map" in s:
            return "outside map area"
        if "too small to be a real footprint" in s:
            return "placement too small"
        m = re.search(r"placed\s+(\d+)\s*m\s+from", s)
        if m:
            return f"{m.group(1)} m from GPS"
        if "timed out" in s:
            return "timed out"
        if "cancelled" in s:
            return "cancelled"
        if "consecutive failures" in s:
            return "batch stopped"
        return "could not place"

    @staticmethod
    def _elide_middle(s, head=8, tail=13):
        """Middle-elide a filename so the DISTINGUISHING tail (frame index +
        extension) stays visible. Head-truncation hid it — every DJI export shares
        the same long prefix, so keeping only the head made all rows identical."""
        s = str(s)
        if len(s) <= head + tail + 1:
            return s
        return s[:head] + "…" + s[-tail:]

    def _rebuild_processing_stats(self, done: int, total: int):
        """Drive the COMPLETED STAGES + TIMING cards from REAL job data."""
        elapsed = (time.time() - self._processing_start_time) if self._processing_start_time else 0

        # LEFT card: completed frames with real inliers.
        vlc = getattr(self, "vl_completed", None)
        if vlc is not None:
            self._clear_layout(vlc)
            # FAILURES BELONG HERE TOO. This used to list only self._job_results,
            # so a batch whose frames were all failing showed "Waiting for first
            # frame" while TIMING beside it read "Frames 2/4" -- two counters
            # disagreeing on screen because one counted successes and the other
            # counted everything resolved. A frame that failed HAS finished; the
            # user needs to see it happening, not an empty panel implying a stall.
            ok_rows   = [(r, True) for r in self._job_results.values()]
            fail_rows = [(f, False) for f in self._failed_jobs.values()]
            resolved = ok_rows + fail_rows
            n_res = len(resolved)
            # Counts BOTH outcomes, so this header and TIMING's "Frames" agree.
            # "FRAMES (2/4)" did not say what the 2 counted: processed, failed,
            # or listed below. Name the quantity, then give the outcome split as
            # its own rows, so the batch's state is readable without adding up
            # the list underneath.
            head = f"PROCESSED {n_res} OF {total}" if total else "PROCESSED"
            vlc.addWidget(self._section_header(head))
            if not resolved:
                vlc.addWidget(self._stat_row("Waiting for first frame", "—", "#a8a29e"))
            else:
                vlc.addWidget(self._stat_row("Placed", str(len(ok_rows)), "#10b981"))
                if fail_rows:
                    vlc.addWidget(self._stat_row("Failed", str(len(fail_rows)), "#ef4444"))
                if total and total > n_res:
                    vlc.addWidget(self._stat_row("Remaining", str(total - n_res), "#78716c"))
            SHOWN = 4
            for r, ok in resolved[-SHOWN:]:
                full = r.get("file") or "frame"
                # Middle-elide (not head-truncate): DJI names share a long prefix, so
                # [:14] made every row read "unwarped_DJI_2". Keeping the tail shows the
                # frame index + extension that actually distinguishes them.
                name = self._elide_middle(full, head=8, tail=13)
                if ok:
                    roww = self._stat_row(f"✓ {name}",
                                          f"{r.get('num_inliers')} inliers", "#10b981")
                    roww.setToolTip(full)
                else:
                    # The reason is the useful part while a batch is going wrong, so
                    # it goes in the value column rather than being hidden on hover.
                    # A SHORT label, not the whole sentence middle-elided: the
                    # sentences all begin the same way, so eliding them produced
                    # four identical rows reading "Could not ...is matcher)." The
                    # full text is one hover away, and is in the exported report.
                    reason = self._clean_server_text(r.get("reason") or "failed")
                    roww = self._stat_row(f"✗ {name}",
                                          self._short_reason(reason), "#ef4444")
                    roww.setToolTip(f"{full}\n{reason}")
                vlc.addWidget(roww)
            if n_res > SHOWN:
                cap = QtWidgets.QLabel(f"latest {SHOWN} of {n_res} shown")
                cap.setStyleSheet("font-size:9px;color:#a8a29e;padding:2px 0 0 2px;")
                vlc.addWidget(cap)

        # RIGHT card: real timing.
        vlt = getattr(self, "vl_timing", None)
        if vlt is not None:
            self._clear_layout(vlt)
            vlt.addWidget(self._section_header("TIMING"))
            vlt.addWidget(self._stat_row("Elapsed", f"{elapsed:.1f}s"))
            # THE single estimate. Frames actually completed, not the progress
            # percentage, because the percentage is a coarse band that carries no
            # information about how long a frame takes. Whatever this says is also
            # what the terminal below is given, so the screen cannot contradict
            # itself the way it did with two independent models.
            if done > 0:
                per = elapsed / done
                remaining = per * (total - done)
                eta_txt = f"~{remaining:.0f}s"
                eta_line = (f"Time remaining: about {int(remaining // 60)} min"
                            if remaining >= 90 else
                            f"Time remaining: about {max(1, int(remaining))} seconds")
            else:
                eta_txt, eta_line = "estimating…", "Time remaining: estimating"
            vlt.addWidget(self._stat_row("Est. remaining", eta_txt, "#f59e0b"))
            e = getattr(self, "label_insight_eta", None)
            if e is not None:
                e.setText(eta_line)
            # "Completed", not "Frames": the header above reads "Frame 3/4" for the
            # frame being worked on, so two counters both labelled "Frame(s)" with
            # different numbers looked like one of them was wrong.
            vlt.addWidget(self._stat_row("Completed", f"{done}/{total}", "#57534e"))

        # Keep the terminal's top line honest. It was set once at the start to
        # "File status: your N files are ready and validated" in green and never
        # touched again, so a batch in which every frame was failing still showed
        # a green all-clear sitting directly beside the red failure rows.
        q = getattr(self, "label_insight_quality", None)
        unsaved = len(getattr(self, "_local_save_failures", {}) or {})
        if q is not None and (self._job_results or self._failed_jobs or unsaved):
            # ⚠️ COUNT THE UNSAVED AS GEOREFERENCED, because they were. They
            # succeeded on the server and the image was charged; only the write
            # to this machine failed. Counting them nowhere understates the
            # work the customer paid for.
            n_ok = len(self._job_results) + unsaved
            n_bad = len(self._failed_jobs)
            tail = f", {unsaved} not saved to disk" if unsaved else ""
            if n_bad:
                q.setText(f"Frame status: {n_ok} georeferenced, "
                          f"{n_bad} failed{tail}")
                colour = "#f87171"
            elif unsaved:
                q.setText(f"Frame status: {n_ok} georeferenced{tail}")
                colour = "#60a5fa"
            else:
                q.setText(f"Frame status: {n_ok} georeferenced")
                colour = "#34d399"
            q.setStyleSheet(f"background:transparent; border:none; color:{colour};"
                            " font-size:11px;")

    @QtCore.pyqtSlot(str, int, int)
    def _update_processing_header(self, filename: str, done: int, total: int):
        """Update the real filename + frame counter on the processing screen."""
        if hasattr(self, "label_processing_file"):
            self.label_processing_file.setText(filename)
        if hasattr(self, "label_frame_counter"):
            self.label_frame_counter.setText(f"Frame {min(done + 1, total)}/{total}")
        self._rebuild_processing_stats(done, total)

    @QtCore.pyqtSlot(int, int)
    def _on_progress_updated(self, percent: int, active_step: int):
        self.progressbar_processing.setValue(percent)
        self.label_progress_percent.setText(f"{percent}%")
        self._set_pipeline_stage(percent)   # drive the consolidated 7-stage stepper

        step_messages = [
            "Uploading your image...",
            "Analyzing geographic features...",
            "Matching against satellite data...",
            "Preparing your georeferenced layer..."
        ]
        
        if active_step >= 0 and active_step < len(step_messages):
            self.label_insight_speed.setText(f"Current step: {step_messages[active_step]}")
        
        # NO SECOND ETA HERE. This used to derive its own estimate from the
        # overall percentage (elapsed / (percent/100) - elapsed) while the TIMING
        # card derived one from completed frames (elapsed/done * remaining). Two
        # different models on screen at once disagreed openly: one screenshot had
        # "Est. remaining ~40s" beside "Time remaining: about 14 seconds". The
        # percentage one was the wrong one to trust anyway, because `percent` is
        # a coarse band (50 + 30*done/total) that says nothing about frame cost.
        # _rebuild_processing_stats now owns the single estimate and writes both.

        for i, lbl in enumerate(self._step_labels):
            text = _STEP_LABELS[i]
            if active_step == -1 or i < active_step:
                lbl.setText(_GLYPH_DONE + text)
                lbl.setStyleSheet(_CLR_DONE)
            elif i == active_step:
                lbl.setText(_GLYPH_RUNNING + text)
                lbl.setStyleSheet(_CLR_RUNNING)
            else:
                lbl.setText(_GLYPH_PENDING + text)
                lbl.setStyleSheet(_CLR_PENDING)

    @QtCore.pyqtSlot(str, str, str)
    def _update_metrics(self, quality_text: str, speed_text: str, eta_text: str):
        """Update the metrics display labels."""
        self.label_insight_quality.setText(quality_text)
        self.label_insight_speed.setText(speed_text)
        self.label_insight_eta.setText(eta_text)

    @QtCore.pyqtSlot()
    def _on_processing_complete(self):
        self._processing_active = False
        self._style_results_page()
        self._refresh_balance()   # tokens were burned — update the strip (incl. results chip)
        self._populate_results_metrics()
        self._ensure_mosaic_button()   # show "Merge into mosaic" for multi-image runs
        self._ensure_retry_save_button()   # only visible when a save failed

        n = len(self._job_results)
        n_fail = len(self._failed_jobs)
        cancelled = getattr(self, "_was_cancelled", False)
        total = getattr(self, "_batch_total", None) or (n + n_fail)
        refunded = max(0, total - n - n_fail)   # cancelled-and-refunded images
        if hasattr(self, "label_complete_title"):
            # ⚠️ EVERY BRANCH COUNTS PROCESSED, NOT SAVED.
            #
            # `n` above is len(self._job_results), which EXCLUDES anything that
            # georeferenced fine but could not be written to this computer. Used
            # in a headline it announces fewer images than were processed and
            # charged, directly above a table that correctly reports the full
            # number.
            #
            # "{n} of {n + n_fail}" has the same problem twice over, understating
            # both the numerator and the denominator.
            #
            # The table already counts from the submitted set. These titles must
            # read from _report_counts too, so there is one accounting rather
            # than two that can drift apart.
            cnt = self._report_counts()
            n_proc = cnt["n_processed"]
            n_unsaved = cnt["n_unsaved"]
            n_skipped = cnt["n_fail"]
            n_total = cnt["total"]
            plural = 's' if n_proc != 1 else ''
            if cancelled:
                self.label_complete_title.setText(
                    f"Mission cancelled: {n_proc} of {n_total} layer"
                    f"{'s' if n_total != 1 else ''} georeferenced"
                )
            elif n_skipped:
                self.label_complete_title.setText(
                    f"{n_proc} of {n_total} UAV layers georeferenced"
                )
            elif n_unsaved:
                self.label_complete_title.setText(
                    f"{n_proc} UAV layer{plural} georeferenced, "
                    f"{n_unsaved} not saved to this computer"
                )
            else:
                self.label_complete_title.setText(
                    f"{n_proc} UAV layer{plural} georeferenced successfully"
                )
        if hasattr(self, "label_complete_info"):
            word = "layers have" if n != 1 else "layer has"
            info = (
                f"The registered {word} been added to your QGIS canvas and synced to the cloud."
            )
            if n_fail:
                skipped = ", ".join(
                    (f.get("file") or "image") for f in list(self._failed_jobs.values())[:5]
                )
                more = f" (+{n_fail - 5} more)" if n_fail > 5 else ""
                info += (
                    f"  {n_fail} image{'s' if n_fail != 1 else ''} could not be "
                    f"georeferenced and {'were' if n_fail != 1 else 'was'} skipped: "
                    f"{skipped}{more}. No credits were charged for skipped images."
                    f"  {self._failure_causes_text()}"
                )
            # A local-save failure is NOT a processing failure and must not be
            # worded like one. The georeferencing succeeded, the image allowance
            # was correctly used, and the result exists in the cloud; only the
            # write to this computer failed. Telling the user their image failed
            # would be wrong twice over: it misstates what happened, and it
            # implies they were charged for nothing.
            n_unsaved = len(getattr(self, "_local_save_failures", {}) or {})
            if n_unsaved:
                first = list(self._local_save_failures.values())[0]
                names = ", ".join(
                    (f.get("file") or "image")
                    for f in list(self._local_save_failures.values())[:5])
                more_u = f" (+{n_unsaved - 5} more)" if n_unsaved > 5 else ""
                # ⚠️ NAME THE ACTUAL CAUSE. The advice used to be "free some disk
                # space or choose a writable folder" for EVERY failure. On Windows
                # the common cause is neither: re-running a mission over a folder
                # whose previous GeoTIFFs are still loaded as QGIS layers fails
                # with WinError 5, because an open file cannot be replaced. The
                # outputs that already exist fail while new ones succeed, on a
                # writable disk with space to spare.
                #
                # Sending that customer to check their disk is a wrong answer
                # delivered confidently, and "Save results again" fails the same
                # way until the layers are closed.
                err = str(first.get("error", "unknown"))
                locked = ("WinError 5" in err or "Access is denied" in err
                          or "being used by another process" in err)
                if locked:
                    remedy = (
                        "This usually means the previous version of these files is "
                        "still open as a layer in QGIS, which stops them being "
                        "replaced. Remove those layers from your project, or choose "
                        "a different output folder, then use \"Save results again\" "
                        "below."
                    )
                else:
                    remedy = (
                        "Free some disk space or choose a writable folder, then use "
                        "\"Save results again\" below."
                    )
                info += (
                    f"  {n_unsaved} result{'s' if n_unsaved != 1 else ''} "
                    f"could not be saved to this computer: {names}{more_u}. "
                    f"Reason: {err}. "
                    "The georeferencing itself succeeded and your images were "
                    f"used as normal. {remedy} Your images "
                    "will not be charged a second time."
                )
            if cancelled and refunded:
                info += (
                    f"  The mission was cancelled, so {refunded} remaining "
                    f"image{'s' if refunded != 1 else ''} {'were' if refunded != 1 else 'was'} "
                    f"not processed and {'their' if refunded != 1 else 'its'} credits refunded."
                )
            self.label_complete_info.setText(info)

        # "Success pause": settle into the 100% success state and hold ~2s so the user
        # sees completion before the Results screen appears (an instant swap reads as a
        # crash). The Results page is already built above — we just defer the transition.
        try:
            self.progressbar_processing.setValue(100)
            if hasattr(self, "label_progress_percent"):
                self.label_progress_percent.setText("100%")
            self._set_pipeline_stage(100)   # every stage -> green
            if hasattr(self, "label_insight_quality"):
                self.label_insight_quality.setText(
                    f"Pipeline complete: {n} of {n + n_fail} georeferenced. Generating report…"
                    if n_fail else
                    "Pipeline complete. Generating report…")
        except Exception:
            pass
        QtCore.QTimer.singleShot(
            1900, lambda: self.stacked_pages.setCurrentIndex(self.PAGE_RESULTS))

    # ─────────────────────────────────────────────────────────
    # RESULTS METRICS CARD
    # ─────────────────────────────────────────────────────────
    def _populate_results_metrics(self):
        """Build/refresh a card on the results page from the real job results."""
        # ⚠️ AN UNSAVED RESULT IS STILL A RESULT, and its metrics are real.
        #
        # Reading _job_results alone meant a batch where every result
        # georeferenced but could not be written to disk found this list empty
        # and returned EARLY, before the old card is replaced below. The
        # previous run's card therefore stayed on screen, so a headline reading
        # "1 not saved to this computer" sat directly above a table claiming
        # "Saved to this computer: 1" from a different batch entirely.
        #
        # The metrics for those jobs travel inside the failure record for
        # exactly this reason, so they are merged rather than recovered.
        results = list(self._job_results.values())
        results += [f["result"] for f in (self._local_save_failures or {}).values()
                    if f.get("result")]
        if not results:
            return

        # Aggregate across the batch.
        inliers = [r["num_inliers"] for r in results if r.get("num_inliers") is not None]
        elapsed = [r["elapsed_s"]   for r in results if r.get("elapsed_s")   is not None]
        best    = max(results, key=lambda r: r.get("num_inliers") or 0)

        avg_inliers = sum(inliers) / len(inliers) if inliers else 0
        total_time  = sum(elapsed) if elapsed else 0
        confidence  = min(100.0, (avg_inliers / 100.0) * 100.0)

        n_low  = sum(1 for r in results if r.get("quality") == "low")
        n_fail = len(self._failed_jobs)

        counts = self._report_counts()
        rows = [
            ("Submitted",            f"{counts['total']}"),
            ("Successfully processed", f"{counts['n_processed']}"),
            ("Saved to this computer", f"{counts['n_saved']}"),
            ("Match confidence",     f"{confidence:.0f}%"),
            ("Avg. inliers / frame", f"{avg_inliers:.0f}"),
            ("Best lock (inliers)",  f"{best.get('num_inliers', 0)}  @  {best.get('angle', 0):.1f}°"),
            ("Total compute time",   f"{total_time:.1f}s"),
            ("Mission centre",       f"{best.get('lat', 0):.5f}, {best.get('lon', 0):.5f}"),
        ]
        if counts["n_unsaved"]:
            rows.append(("Not saved to this computer", f"{counts['n_unsaved']}"))
        if n_low:
            rows.append(("Low-confidence frames", f"{n_low}"))
        if counts["n_fail"]:
            rows.append(("Processing failures", f"{counts['n_fail']}"))
        if counts["n_unknown"]:
            rows.append(("Unaccounted", f"{counts['n_unknown']}"))

        old = getattr(self, "_metrics_card", None)
        if old is not None:
            old.setParent(None)
            old.deleteLater()

        card = QtWidgets.QGroupBox("REGISTRATION METRICS")
        from qgis.PyQt.QtGui import QFont as _QFont
        _cf = card.font(); _cf.setBold(True); _cf.setPointSize(8)
        try:
            _cf.setLetterSpacing(_QFont.AbsoluteSpacing, 1.0)
        except Exception:
            pass
        card.setFont(_cf)
        card.setStyleSheet(
            "QGroupBox { border:none; background:transparent; margin-top:12px;"
            "  font-size:9px; color:#9ca3af; }"
            "QGroupBox::title { subcontrol-origin:margin; subcontrol-position:top left;"
            "  left:0px; top:0px; padding:0 0 10px 0; background:transparent; color:#9ca3af; }")
        grid = QtWidgets.QGridLayout(card)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        grid.setContentsMargins(12, 10, 12, 10)
        for i, (label, value) in enumerate(rows):
            lbl = QtWidgets.QLabel(label)
            lbl.setStyleSheet("font-size: 10px; color: #78716c;")
            val = QtWidgets.QLabel(value)
            val.setStyleSheet("font-size: 11px; font-weight: 700; color: #1a1612;")
            val.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
            grid.addWidget(lbl, i, 0)
            grid.addWidget(val, i, 1)

        self._metrics_card = card

        layout = getattr(self, "verticalLayout_results", None)
        anchor = getattr(self, "groupbox_results", None)
        if layout is not None and anchor is not None:
            idx = layout.indexOf(anchor)
            layout.insertWidget(idx if idx >= 0 else layout.count(), card)
        elif layout is not None:
            layout.addWidget(card)

    # ─────────────────────────────────────────────────────────
    # MERGE INTO SINGLE MOSAIC (cosmetic — one clean layer)
    # ─────────────────────────────────────────────────────────
    def _ensure_mosaic_button(self):
        """Add a 'Merge into single mosaic' button to the Results page (once).
        Only shown when a run produced more than one georeferenced layer."""
        layout = getattr(self, "verticalLayout_results", None)
        if layout is None:
            return
        if getattr(self, "_btn_merge_mosaic", None) is None:
            btn = self._styled_button("Merge into single mosaic", primary=False)
            btn.clicked.connect(self._merge_into_mosaic)
            self._btn_merge_mosaic = btn
            anchor = getattr(self, "groupbox_results", None)
            if anchor is not None:
                idx = layout.indexOf(anchor)
                layout.insertWidget(idx if idx >= 0 else layout.count(), btn)
            else:
                layout.addWidget(btn)
        # Reset to a fresh, clickable state each run (a prior run may have disabled it
        # after merging). A mosaic only makes sense for 2+ results.
        self._btn_merge_mosaic.setEnabled(True)
        self._btn_merge_mosaic.setText("Merge into single mosaic")
        self._btn_merge_mosaic.setVisible(
            len([p for p in self._result_paths if os.path.exists(p)]) > 1)

    def _merge_into_mosaic(self):
        """Merge all per-image result GeoTIFFs into ONE mosaic GeoTIFF and
        replace the individual layers with that single clean layer.

        Black (0,0,0) warp borders are treated as nodata so they don't paint
        over neighbouring imagery. Geometry is unchanged — this is presentation
        only (per-image alignment is what it is; see orthomosaic for seamless)."""
        paths = [p for p in self._result_paths if os.path.exists(p)]
        if len(paths) < 2:
            self._themed_notice(
                "Nothing to merge",
                "Need at least two georeferenced layers to build a mosaic.",
                icon="⚠", accent="orange", button="OK")
            return

        out = str(Path(paths[0]).parent / f"atlas_mosaic_{int(time.time())}.tif")
        self.setCursor(QtCore.Qt.WaitCursor)
        try:
            from osgeo import gdal
            gdal.UseExceptions()
            opts = gdal.WarpOptions(
                format="GTiff",
                srcNodata="0 0 0", dstNodata="0 0 0",
                creationOptions=["COMPRESS=LZW", "TILED=YES"],
                multithread=True,
            )
            ds = gdal.Warp(out, paths, options=opts)
            ds = None   # flush + close
        except Exception as e:
            self.unsetCursor()
            self._themed_notice("Merge failed", f"Could not build the mosaic:\n{e}",
                                accent="red", button="Close")
            return
        self.unsetCursor()

        if not os.path.exists(out):
            self._themed_notice("Merge failed", "The mosaic file was not created.",
                                accent="red", button="Close")
            return

        # Replace the individual result layers with the single mosaic layer.
        proj = QgsProject.instance()
        for lyr in list(proj.mapLayers().values()):
            if lyr.name().startswith("ATLAS"):
                proj.removeMapLayer(lyr.id())

        layer = QgsRasterLayer(out, "ATLAS Mosaic")
        if not layer.isValid():
            self._themed_notice(
                "Mosaic saved", f"Merged file saved but could not be loaded:\n{out}",
                icon="⚠", accent="orange", button="OK")
            return

        proj.addMapLayer(layer)
        try:   # black borders -> transparent (same as the per-image layers)
            rt = layer.renderer().rasterTransparency()
            px = rt.TransparentThreeValuePixel()
            px.red = 0; px.green = 0; px.blue = 0
            rt.setTransparentThreeValuePixelList([px])
        except Exception:
            pass
        layer.setOpacity(1.0)
        self.iface.setActiveLayer(layer)
        self.iface.zoomToActiveLayer()
        self.iface.mapCanvas().refresh()

        self._mosaic_path = out
        self._result_layers_cache = [layer]   # View-on-Map now frames the mosaic
        # One-shot: prevent re-merging (which would rebuild duplicate mosaics).
        b = getattr(self, "_btn_merge_mosaic", None)
        if b is not None:
            b.setEnabled(False)
            b.setText("✓  Merged into mosaic")
        self._themed_notice(
            "Mosaic created",
            f"Merged {len(paths)} layers into one mosaic and added it to the canvas.\n\n"
            f"Saved to:\n{os.path.basename(out)}",
            accent="green", button="Done", primary_button=True)

    @QtCore.pyqtSlot(str)
    def _on_processing_failed(self, message: str):
        self._processing_active = False
        # A batch that got as far as PROCESSING had a credit reserved per image,
        # which the worker refunds as each one permanently fails. That refund is
        # asynchronous, so the chip kept showing the post-reservation number and
        # the balance only corrected itself the next time the window happened to
        # regain focus. Watch for it instead: _start_balance_watch polls until the
        # figure actually moves, then stops. Only on this path -- an upload that
        # failed before any job existed has nothing to wait for.
        if self._failed_jobs and getattr(self, "_billing_strip_built", False):
            self._refresh_balance()
            self._start_balance_watch(timeout=90, interval=4)
        self._themed_notice("Processing error", self._clean_server_text(message),
                            accent="red", button="Close")
        self._restart_mission()

    @QtCore.pyqtSlot()
    def _on_processing_cancelled(self):
        """The worker stopped because the user cancelled. Keep whatever already
        finished (those images are charged) and show the refund for the rest.
        If nothing finished, skip the results page and just notify."""
        self._processing_active = False
        if self._job_results:
            # Some images completed — show them on the results page (partial run).
            # _on_processing_complete refreshes the balance + renders the cancel
            # summary (it reads self._was_cancelled).
            self._on_processing_complete()
        else:
            # Nothing processed — everything was refunded. No results to show.
            self._refresh_balance()
            self._themed_notice(
                "Mission cancelled",
                "No images were processed. All reserved credits have been refunded "
                "to your account.",
                accent="orange", button="Close")
            self._restart_mission()

    def _cancel_batch_backend(self):
        """Best-effort: tell the backend to abort the batch so jobs that haven't
        started are skipped and their reserved credits refunded. Safe to fail —
        the client has already stopped polling either way."""
        bid = getattr(self, "_batch_id", None)
        if not bid:
            return
        try:
            self._authed_request("POST", CANCEL_URL, json={"batch_id": bid}, timeout=10)
        except Exception as e:
            print(f"cancel batch failed: {e}")

    def _cancel_processing(self):
        # Nothing running (e.g. processing already finished) → ignore.
        if not getattr(self, "_processing_active", False):
            return
        done      = len(self._job_results)
        total     = getattr(self, "_batch_total", None) or len(self.selected_files) or done
        remaining = max(0, total - done)
        if done > 0:
            msg = (f"{done} of {total} images have already been processed. These will be "
                   f"kept and added to your map. The remaining {remaining} will be "
                   f"cancelled and their credits refunded.")
        else:
            msg = (f"No images have finished yet. All {total} images will be cancelled "
                   f"and their reserved credits refunded.")
        if not self._themed_confirm(
                "Cancel mission?", msg,
                confirm_text="Cancel mission", cancel_text="Keep processing", accent="red"):
            return
        # Processing may have finished while the confirmation dialog was open.
        if not getattr(self, "_processing_active", False):
            return
        self._was_cancelled = True
        self._cancel_flag.set()
        # Abort on the backend (refund pending jobs) without blocking the UI.
        threading.Thread(target=self._cancel_batch_backend, daemon=True).start()
        # The worker emits processing_cancelled once it stops → routing happens there.

    # ─────────────────────────────────────────────────────────────
    # BASEMAP LOADER
    # ─────────────────────────────────────────────────────────────
    def _prefetch_tile_session(self):
        """Mint the display-tile session in the background, right after sign-in.

        WHY HERE. _tile_session is a blocking HTTP call, and _load_basemap runs
        it on the UI THREAD, so View on Map froze QGIS for a round trip before
        drawing anything. Sign-in is the earliest moment this is possible at
        all: it is the first point at which an ATLAS token exists. (The old
        plugin-open basemap loader could never have used a session for exactly
        that reason.)

        ⚠️ THIS DOES NOT BUILD A LAYER. It warms a cache and nothing else.
        _load_basemap remains the single place that creates a basemap; adding a
        second one is a defect this codebase has produced before.

        Failure is silent by design. A cold cache simply means _load_basemap
        mints on demand exactly as before, so this can only make things faster,
        never break them.
        """
        if not self.access_token:
            return

        def _work():
            try:
                self._tile_session()      # populates the cache as a side effect
            except Exception as e:
                print(f"tile session prefetch skipped: {e}")
        threading.Thread(target=_work, daemon=True).start()

    def _tile_session(self):
        """Exchange the signed-in ATLAS token for a short-lived display-tile
        session. Returns (url_template, attribution) or (None, "").

        CACHED, with the server's own expiry. The proxy returns `expires_in`
        (8h by default), and a token is reused only while it
        has comfortable life left. The margin matters: a token valid for another
        four seconds is not worth handing to a layer that will outlive it, and
        the failure it produces is a silently blank basemap rather than an
        error.

        On expiry this simply mints a new one, so the 8-hour boundary self-heals
        without the user doing anything.

        The session is what makes a bundled basemap defensible: it is per-user,
        it expires, it is rate-limited server-side, and it grants DISPLAY tiles
        only. The vendor token stays in the proxy and never reaches this
        machine.

        Returns None rather than raising on ANY failure. A basemap is a
        convenience; being unable to draw one must never stop a user
        georeferencing their imagery, so the caller falls through to Esri.
        """
        if not self.access_token:
            return None, ""

        # Serve from cache while it has real life left. 60 s of margin, because
        # a token about to expire is worse than no token: the layer outlives it
        # and goes silently blank instead of falling back to Esri.
        cached = getattr(self, "_tile_session_cache", None)
        if cached and time.time() < cached.get("expires_at", 0) - 60:
            return cached["session"], cached["attribution"]

        try:
            resp = self._authed_request("GET", TILE_SESSION_URL, timeout=15)
        except Exception as e:
            print(f"tile session request failed: {e}")
            return None, ""
        if resp.status_code != 200:
            # 503 is the proxy saying its session store or auth is unavailable;
            # 401 means our own token is not good enough. Neither is worth a
            # dialog: the user did not ask for a basemap, they asked to see
            # their result.
            print(f"tile session unavailable (HTTP {resp.status_code})")
            return None, ""
        try:
            body = resp.json()
        except Exception:
            return None, ""
        token = body.get("session")
        if not token:
            return None, ""
        attribution = str(body.get("attribution") or "")
        # expires_in is the SERVER's number, not an assumption of ours. Absent
        # or unparseable falls back to a deliberately short 5 minutes: a cache
        # entry whose lifetime we cannot establish should be re-minted soon
        # rather than trusted for eight hours.
        try:
            ttl = float(body.get("expires_in") or 0) or 300.0
        except (TypeError, ValueError):
            ttl = 300.0
        self._tile_session_cache = {
            "session":     token,
            "attribution": attribution,
            "expires_at":  time.time() + ttl,
        }
        return token, attribution

    def _load_basemap(self):
        """Load the basemap the user picked in the SETUP dropdown.

        - 'Satellite' → our tile proxy, using a short-lived per-user session.
          Which vendor serves those tiles is the proxy's DISPLAY_PROVIDERS
          setting, NOT a decision baked in here, so it can change server-side
          without another plugin release.
        - 'OpenStreetMap' → free OSM street tiles.

        Falls back to Esri fetched directly if the proxy or the session is
        unavailable, so the workflow never depends on the basemap.

        Note: names deliberately avoid the word 'ATLAS' so view_on_map's result-
        layer lookup doesn't mistake the basemap for a registered result."""
        choice = "Satellite"
        combo = getattr(self, "combo_basemap_setup", None)
        if combo is not None and combo.currentText():
            choice = combo.currentText()

        attribution = ""
        if choice == "OpenStreetMap":
            reuse_names = ("OpenStreetMap",)
            candidates = [(
                "OpenStreetMap",
                "type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&zmax=19&zmin=0",
            )]
            attribution = "© OpenStreetMap contributors"
        else:
            reuse_names = ("Satellite Basemap", "Esri Imagery", "Google Satellite")
            candidates = []
            session, attribution = self._tile_session()
            if TILES_BASE_URL and session:
                base = TILES_BASE_URL.rstrip("/")
                # ⚠️ THE SESSION TRAVELS AS A HEADER, NOT IN THE URL.
                #
                # It was `?s=<token>` on the belief that a QGIS XYZ layer could
                # carry nothing else. That was wrong. QGIS has supported custom
                # request headers through QgsHttpHeaders and the `http-header:`
                # URI parameter since 3.24, and metadata.txt requires 3.44, so
                # every QGIS able to install this plugin can send it.
                #
                # It matters because a token in the URL lands in the tile
                # proxy's access log AND in the upstream load balancer's,
                # which sits in front of our process and cannot be filtered
                # from inside it. It also survives in screenshots and shell history.
                # This one is short-lived and per-user, but that is a reason to
                # keep it out of logs cheaply, not a reason to shrug.
                candidates.append((
                    "Satellite Basemap",
                    f"type=xyz&url={base}/tiles/{{z}}/{{x}}/{{y}}.jpg"
                    f"&http-header:X-Tile-Session={session}"
                    f"&zmax=22&zmin=0",
                ))
            # Direct fallbacks (used if the proxy isn't set or fails to load):
            candidates.append((
                "Esri Imagery",
                "type=xyz&url=https://server.arcgisonline.com/ArcGIS/rest/services/"
                "World_Imagery/MapServer/tile/{z}/{y}/{x}&zmax=19&zmin=0",
            ))
            # Esri Imagery is the only direct fallback. It provides satellite
            # coverage on a documented, publicly usable tile service, which is
            # what a publicly distributed plugin needs when the proxy is not
            # reachable.
            #
            # ATTRIBUTION travels with the layer, below. The proxy returns the
            # correct string for whichever vendor it is configured to serve, so
            # the credit follows the imagery instead of being guessed here; the
            # direct Esri fallback carries its own.
            if not attribution:
                attribution = ("Esri, Maxar, Earthstar Geographics, "
                               "and the GIS User Community")

        # ⚠️ A PROXY-BACKED BASEMAP CARRIES A CREDENTIAL, SO IT CANNOT BE
        # REUSED INDEFINITELY.
        #
        # The reuse rule below predates the tile session and was harmless while
        # the basemap was Esri-direct: a URL with no credential stays valid
        # forever. The session changed that. Its token expires (8h by default,
        # configurable on the proxy), and it is baked into the layer's
        # URI as an http-header. So "a layer called Satellite Basemap already
        # exists, reuse it" would hand back a layer whose header is dead, and
        # keep doing so for the life of the QGIS project. Every subsequent tile
        # would 401 and the basemap would be permanently blank, with no error
        # and no path to recovery short of the user deleting the layer by hand.
        #
        # It bites hardest in the ordinary case: save the project, reopen it
        # tomorrow, and the layer is restored from the .qgz with yesterday's
        # token still in it.
        #
        # So: whenever we have just minted a FRESH session, drop any existing
        # proxy-backed basemap and rebuild it around the new token. Cheap, and
        # the only version that self-heals. Layers we did not credential
        # (Esri, OSM) are reused as before.
        _proj = QgsProject.instance()
        if session:
            for lyr in list(_proj.mapLayers().values()):
                if lyr.name() == "Satellite Basemap":
                    _proj.removeMapLayer(lyr.id())
        else:
            for lyr in _proj.mapLayers().values():
                if lyr.name() in reuse_names:
                    return lyr

        for name, uri in candidates:
            layer = QgsRasterLayer(uri, name, "wms")
            if layer.isValid():
                # Vendor terms require the credit to be visible. Wrapped because
                # the metadata API differs across QGIS 3.x point releases and a
                # missing credit line must not stop the map drawing.
                if attribution:
                    try:
                        md = layer.metadata()
                        md.setRights([attribution])
                        layer.setMetadata(md)
                    except Exception as e:
                        print(f"basemap attribution not set: {e}")
                # ⚠️ THE BASEMAP IS ADDED DIFFERENTLY FROM EVERY OTHER LAYER,
                # and that is where the bug was.
                #
                # Result layers use plain addMapLayer(layer), which registers,
                # adds to the legend, and repaints. The basemap uses
                # addMapLayer(layer, False) + insertLayer so it can be placed at
                # the BOTTOM, under the results. That two-step form leaves two
                # things undone that the one-step form does for you: the tree
                # node's checked state, and a canvas repaint.
                #
                # Symptom: the layer appears in the panel but draws nothing
                # until the user ticks it by hand, which both sets the node
                # checked AND forces a repaint -- so it looked like the basemap
                # had failed to load when it had actually loaded fine.
                #
                # Both are set explicitly here. I could not tell from the code
                # alone which of the two was responsible, so this closes both
                # rather than guessing at one; neither is expensive.
                #
                # addToLegend=False registers the layer WITHOUT placing it in
                # the tree, so insertLayer below controls where it lands. Drop
                # this call and the layer is never registered with the project
                # at all, and insertLayer has nothing valid to wrap.
                QgsProject.instance().addMapLayer(layer, False)
                node = QgsProject.instance().layerTreeRoot().insertLayer(-1, layer)
                try:
                    if node is not None:
                        node.setItemVisibilityChecked(True)
                except Exception as e:
                    print(f"basemap visibility not set: {e}")
                try:
                    self.iface.mapCanvas().refresh()
                except Exception as e:
                    print(f"basemap canvas refresh skipped: {e}")
                return layer
        return None


    # ─────────────────────────────────────────────────────────────
    # VIEW ON MAP 
    # ─────────────────────────────────────────────────────────────
    def view_on_map(self):
        lat = self._job_lat if self._job_lat != 0.0 else None
        lon = self._job_lon if self._job_lon != 0.0 else None

        if lat is None or lon is None:
            self.iface.messageBar().pushMessage(
                "ATLAS", "No GPS coordinates available for this mission.", level=1, duration=4
            )
            return

        canvas = self.iface.mapCanvas()
        self._load_basemap()

        # A batch produces SEVERAL result layers (one per image). Collect them
        # all and frame their COMBINED extent, so View on Map shows every
        # georeferenced image instead of just one.
        result_layers = [lyr for lyr in QgsProject.instance().mapLayers().values()
                         if lyr.name().startswith("ATLAS")]
        if not result_layers:
            # Auto-load was off (or layers were removed) — load the results on demand now.
            for p in self._result_paths:
                if os.path.exists(p):
                    self._add_result_layer(
                        p, f"ATLAS · {Path(p).stem.replace('_georef', '')}")
            result_layers = [lyr for lyr in QgsProject.instance().mapLayers().values()
                             if lyr.name().startswith("ATLAS")]
        self._result_layers_cache = result_layers
        union = self._combined_extent(
            result_layers, canvas.mapSettings().destinationCrs())

        self.hide()

        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY
        canvas_crs = canvas.mapSettings().destinationCrs()
        wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
        
        if canvas_crs.authid() != "EPSG:4326":
            # Map is in meters (Web Mercator)
            xform = QgsCoordinateTransform(wgs84, canvas_crs, QgsProject.instance())
            pt = xform.transform(QgsPointXY(lon, lat))
            target_x, target_y = pt.x(), pt.y()
            
            buf_wide = 200000.0  # 200 km
            buf_city = 5000.0    # 5 km
            buf_street = 800.0   # 800 m
        else:
            # Map is in degrees
            target_x, target_y = lon, lat
            buf_wide = 2.0
            buf_city = 0.05
            buf_street = 0.008

        canvas.setExtent(QgsRectangle(
            target_x - buf_wide, target_y - buf_wide,
            target_x + buf_wide, target_y + buf_wide,
        ))
        canvas.refresh()

        QtCore.QTimer.singleShot(400, lambda: self._zoom_step(
            canvas, target_x, target_y, buf_city, 500,
            lambda: self._zoom_step(
                canvas, target_x, target_y, buf_street, 450,
                lambda: self._zoom_final(canvas, target_x, target_y, union)
            )
        ))

    def _zoom_step(self, canvas, target_x, target_y, buffer, next_delay, next_fn):
        canvas.setExtent(QgsRectangle(
            target_x - buffer, target_y - buffer,
            target_x + buffer, target_y + buffer,
        ))
        canvas.refresh()
        QtCore.QTimer.singleShot(next_delay, next_fn)

    def _zoom_final(self, canvas, target_x, target_y, fit_extent):
        """Final zoom: fit the combined extent of all result layers (already in
        canvas CRS) if available, else a tight box around the point."""
        if fit_extent is not None and not fit_extent.isEmpty():
            pad = fit_extent.width() * 0.15
            canvas.setExtent(fit_extent.buffered(pad if pad > 0 else 50.0))
        else:
            # Fallback zoom (CRS-aware units).
            canvas_crs = canvas.mapSettings().destinationCrs()
            tight = 150.0 if canvas_crs.authid() != "EPSG:4326" else 0.0015
            canvas.setExtent(QgsRectangle(
                target_x - tight, target_y - tight,
                target_x + tight, target_y + tight,
            ))

        canvas.refresh()

        # Flash EVERY result layer together so the whole batch stands out.
        layers = getattr(self, "_result_layers_cache", None)
        if layers:
            self._flash_layers(layers, canvas, flashes=3, interval=300)

        n = len(layers) if layers else 0
        msg = (f"Mission area: {n} georeferenced layer{'s' if n != 1 else ''}"
               if n else
               f"Mission area: {self._job_lat:.5f}°, {self._job_lon:.5f}°")
        self.iface.messageBar().pushMessage("ATLAS", msg, level=0, duration=6)

    def _combined_extent(self, layers, canvas_crs):
        """Union of every layer's extent, reprojected into the canvas CRS.
        Returns a QgsRectangle, or None if there are no valid layers."""
        from qgis.core import QgsCoordinateTransform, QgsRectangle
        union = None
        for lyr in layers:
            if not lyr or not lyr.isValid():
                continue
            ext = lyr.extent()
            if lyr.crs() != canvas_crs:
                try:
                    ext = QgsCoordinateTransform(
                        lyr.crs(), canvas_crs, QgsProject.instance()
                    ).transformBoundingBox(ext)
                except Exception:
                    continue
            if ext.isEmpty():
                continue
            if union is None:
                union = QgsRectangle(ext)
            else:
                union.combineExtentWith(ext)
        return union

    def _flash_layers(self, layers, canvas, flashes=3, interval=300):
        """Blink several layers together (batch highlight) using one timer."""
        root = QgsProject.instance().layerTreeRoot()
        tree = [t for t in (root.findLayer(l.id()) for l in layers) if t is not None]
        if not tree:
            return
        total = flashes * 2
        state = {"count": 0}
        timer = QtCore.QTimer()
        timer.setSingleShot(False)
        timer.setInterval(interval)

        def _toggle():
            if state["count"] >= total:
                timer.stop()
                for t in tree:
                    t.setItemVisibilityChecked(True)
                canvas.refresh()
                return
            vis = state["count"] % 2 != 0
            for t in tree:
                t.setItemVisibilityChecked(vis)
            canvas.refresh()
            state["count"] += 1

        timer.timeout.connect(_toggle)
        self._flash_timer = timer
        timer.start()


    def _flash_layer(self, layer, canvas, flashes, interval):
        """
        Toggle layer visibility on/off `flashes` times to draw the user's eye.
        Uses a QTimer with a mutable state dict to avoid closure scoping issues.
        """
        root = QgsProject.instance().layerTreeRoot()
        tree_layer = root.findLayer(layer.id())
        if not tree_layer:
            return

        total_toggles = flashes * 2  # on + off per flash
        state = {"count": 0}         # dict survives the closure; list did not

        timer = QtCore.QTimer()
        timer.setSingleShot(False)
        timer.setInterval(interval)

        def _toggle():
            if state["count"] >= total_toggles:
                timer.stop()
                tree_layer.setItemVisibilityChecked(True)  # always end visible
                canvas.refresh()
                return
            # even count → hide, odd count → show
            tree_layer.setItemVisibilityChecked(state["count"] % 2 != 0)
            canvas.refresh()
            state["count"] += 1

        timer.timeout.connect(_toggle)
        # Keep a reference on self so the timer isn't garbage-collected mid-flash
        self._flash_timer = timer
        timer.start()
   
    def _collect_report_rows(self):
        """One row per SUBMITTED job, classified exactly once.

        ⚠️ DRIVEN BY self._submitted_jobs, not by the result dictionaries.

        This used to iterate _job_results and _failed_jobs, and add their lengths
        for the total. A job that processed correctly, was charged, and then
        could not be written to this machine lives in _local_save_failures and is
        in NEITHER of those, so it vanished from the report completely. A real
        batch of 13 completed, was charged 13 times, and printed "12 of 12,
        skipped 0": short by one, with nothing indicating anything was missing.

        Counting from what was submitted means a job that falls out of every
        bucket is REPORTED as unaccounted for rather than silently dropped. A
        deliverable a customer forwards on must never under-report work they were
        billed for.
        """
        def _blank(file_, status, reason=""):
            return {"file": file_, "status": status, "inliers": None,
                    "tier": None, "angle": None, "lat": None, "lon": None,
                    "elapsed": None, "reason": reason}

        submitted = getattr(self, "_submitted_jobs", None) or {}
        if not submitted:
            # Older state, or a run that never got as far as registering jobs.
            # Fall back to the union of what we do have, so a report is still
            # produced rather than coming out empty.
            submitted = {}
            for d in (self._job_results, self._local_save_failures,
                      self._failed_jobs):
                for jid, v in (d or {}).items():
                    submitted.setdefault(jid, (v or {}).get("file", ""))

        rows = []
        for jid, fname in submitted.items():
            name = fname or jid[:8]
            if jid in self._job_results:
                r = self._job_results[jid]
                rows.append({
                    "file":    r.get("file") or name,
                    "status":  "Low confidence" if r.get("quality") == "low"
                               else "Processed",
                    "inliers": r.get("num_inliers"),
                    "tier":    r.get("tier"),
                    "angle":   r.get("angle"),
                    "lat":     r.get("lat"),
                    "lon":     r.get("lon"),
                    "elapsed": r.get("elapsed_s"),
                    "reason":  "",
                })
            elif jid in (self._local_save_failures or {}):
                f = self._local_save_failures[jid]
                # Georeferencing SUCCEEDED and the image was correctly used.
                # Only the write to this computer failed, and the result is still
                # retrievable, so this must not read as a processing failure.
                rows.append(_blank(
                    f.get("file") or name, "Not saved locally",
                    self._clean_server_text(
                        "Processed successfully. Could not be written to this "
                        "computer: %s. Use 'Save results again' to retrieve it."
                        % f.get("error", "unknown error"))))
            elif jid in self._failed_jobs:
                f = self._failed_jobs[jid]
                rows.append(_blank(
                    f.get("file") or name, "Skipped",
                    # The report is a deliverable a customer forwards on, so the
                    # worker's em dashes get normalised here too.
                    self._clean_server_text(
                        f.get("reason") or "Low match quality")))
            else:
                # Submitted, charged, and in none of the three buckets. That is a
                # state-tracking defect, and the report says so rather than
                # quietly dropping the row.
                rows.append(_blank(
                    name, "Unaccounted",
                    "Submitted but no result was recorded by the plugin. "
                    "Check your balance and contact support."))

        rows.sort(key=lambda x: x["file"])
        return rows

    def _report_counts(self):
        """Totals derived from the SAME rows the table shows.

        Never re-derived by adding dictionary lengths: that is exactly how the
        total and the table came to disagree.
        """
        rows = self._collect_report_rows()
        def c(*statuses):
            return sum(1 for r in rows if r["status"] in statuses)
        # ⚠️ PROCESSED IS NOT THE SAME AS SAVED, and the difference is money.
        #
        # A result that could not be written to this computer was still
        # georeferenced and still charged. Counting it only under "saved" makes
        # the report understate the work the customer paid for.
        n_saved = c("Processed", "Low confidence")
        n_unsaved = c("Not saved locally")
        return {
            "total":       len(rows),          # submitted
            "n_processed": n_saved + n_unsaved,  # succeeded server-side, charged
            "n_saved":     n_saved,            # written to this computer
            "n_unsaved":   n_unsaved,          # succeeded, not written
            "n_low":       c("Low confidence"),
            "n_fail":      c("Skipped"),
            "n_unknown":   c("Unaccounted"),
            # Kept so nothing downstream breaks on the old key. It means
            # "saved locally", which is why it must not be labelled "processed".
            "n_ok":        n_saved,
        }

    def _logo_data_uri(self):
        """Base64 data URI of the AIVE logo bundled with the plugin (for the PDF)."""
        import base64
        p = os.path.join(os.path.dirname(__file__), "aive_logo.png")
        if not os.path.exists(p):
            return ""
        try:
            with open(p, "rb") as fh:
                b64 = base64.b64encode(fh.read()).decode("ascii")
            return f"data:image/png;base64,{b64}"
        except Exception:
            return ""

    def _report_html(self, rows, meta, logo_uri="", logo_h=34):
        """Branded HTML for the PDF deliverable (rendered via QTextDocument).

        All sizes are in POINTS (pt) — QTextDocument prints onto a high-DPI page,
        and pt scales physically while px would render tiny. Palette matches the
        plugin: cream #faf7f2 / orange #ea580c / dark #1a1612.
        """
        def cell(v, fmt="{}"):
            return "—" if v is None or v == "" else fmt.format(v)

        body = []
        for i, r in enumerate(rows):
            color = {
                "Processed":         "#10b981",   # green, delivered
                "Low confidence":    "#d97706",   # amber, delivered, less certain
                "Not saved locally": "#1d6fd0",   # blue, delivered but not written
                "Skipped":           "#dc2626",   # red, not delivered
                "Unaccounted":       "#dc2626",   # red, and a defect on our side
            }.get(r["status"], "#dc2626")
            latlon = "—"
            if r["lat"] is not None and r["lon"] is not None:
                latlon = f"{r['lat']:.5f}, {r['lon']:.5f}"
            note = r["reason"] if r["status"] == "Skipped" else ""
            zebra = "#ffffff" if i % 2 == 0 else "#fbf9f5"
            body.append(
                f"<tr bgcolor='{zebra}'>"
                f"<td>{cell(r['file'])}</td>"
                f"<td><font color='{color}'><b>{r['status']}</b></font></td>"
                f"<td align='center'>{cell(r['inliers'])}</td>"
                f"<td align='center'>{cell(r['tier'])}</td>"
                f"<td align='center'>{cell(r['angle'], '{:.1f}°')}</td>"
                f"<td>{latlon}</td>"
                f"<td align='center'>{cell(r['elapsed'], '{:.1f}s')}</td>"
                f"<td><font color='#78716c'>{note}</font></td>"
                "</tr>"
            )

        logo_img = (f"<img src='{logo_uri}' height='{logo_h}'>&nbsp;&nbsp;"
                    if logo_uri else "")

        # Shown ONLY when non-zero, so an ordinary clean run is not cluttered
        # with two zeroes. When it does appear it is the line that explains why
        # the frame count and the delivered count differ.
        # Shown ONLY when non-zero. An unaccounted job means the plugin lost
        # track of work that was submitted and charged, so it needs to be
        # impossible to miss, and absent entirely on an ordinary run.
        extra = ""
        if meta.get("n_unknown"):
            extra = ("<tr><td colspan='3' bgcolor='#fef2f2' "
                     "style='color:#b91c1c;'><b>Unaccounted: "
                     f"{meta['n_unknown']}</b>. Submitted and charged, but the "
                     "plugin recorded no result. Please contact support.</td></tr>")

        def section(title):
            return (f"<table width='100%' cellspacing='0' cellpadding='0'><tr>"
                    f"<td style='font-size:11pt;font-weight:bold;color:#ea580c;'>{title}</td>"
                    f"</tr></table>")

        return f"""
        <html><body style="font-family:'Segoe UI',Arial,sans-serif;color:#1a1612;font-size:9pt;">

          <!-- HEADER BAND -->
          <table width="100%" cellspacing="0" cellpadding="8" bgcolor="#faf7f2">
            <tr>
              <td valign="middle" width="120">{logo_img}</td>
              <td valign="middle">
                <span style="font-size:16pt;font-weight:bold;color:#ea580c;">ATLAS-GEO</span>
              </td>
              <td valign="middle" align="right" width="230">
                <span style="font-size:9pt;font-weight:bold;color:#57534e;">REGISTRATION&nbsp;REPORT</span><br/>
                <span style="font-size:7.5pt;color:#a8a29e;">AIVE&nbsp;AI&nbsp;Systems&nbsp;&middot;&nbsp;UAV&nbsp;Georeferencing</span>
              </td>
            </tr>
          </table>
          <table width="100%" cellspacing="0" cellpadding="0"><tr>
            <td bgcolor="#ea580c" style="font-size:2pt;line-height:2pt;">&nbsp;</td>
          </tr></table>

          <br/>
          <!-- META -->
          <table width="100%" cellspacing="0" cellpadding="3" style="font-size:9pt;color:#57534e;">
            <tr><td width="130"><b>Mission</b></td><td>{meta['mission']}</td>
                <td width="110"><b>Generated</b></td><td>{meta['generated']}</td></tr>
            <tr><td><b>User</b></td><td>{meta['user']}</td>
                <td><b>Basemap</b></td><td>{meta['basemap']}</td></tr>
            <tr><td><b>Coordinate system</b></td><td colspan="3">EPSG:4326 (WGS 84)</td></tr>
          </table>

          <br/>
          {section('SUMMARY')}
          <table width="100%" cellspacing="0" cellpadding="5" bgcolor="#faf7f2" style="font-size:9pt;">
            <tr>
              <td width="33%">Submitted<br/><b style="font-size:12pt;color:#1a1612;">{meta['total']}</b></td>
              <td width="33%">Successfully processed<br/><b style="font-size:12pt;color:#ea580c;">{meta['n_processed']}</b></td>
              <td width="33%">Processing failures<br/><b style="font-size:12pt;color:#dc2626;">{meta['n_fail']}</b></td>
            </tr>
            <tr>
              <td>Saved to this computer<br/><b style="font-size:12pt;color:#10b981;">{meta['n_saved']}</b></td>
              <td>Not saved to this computer<br/><b style="font-size:12pt;color:#1d6fd0;">{meta['n_unsaved']}</b></td>
              <td>Low-confidence frames<br/><b style="font-size:12pt;color:#d97706;">{meta['n_low']}</b></td>
            </tr>
            <tr>
              <td>Avg. match confidence<br/><b style="font-size:12pt;color:#ea580c;">{meta['confidence']:.0f}%</b></td>
              <td>Avg. inliers / frame<br/><b style="font-size:12pt;color:#ea580c;">{meta['avg_inliers']:.0f}</b></td>
              <td>Total compute time<br/><b style="font-size:12pt;color:#1a1612;">{meta['total_time']:.1f}s</b></td>
            </tr>
            {extra}
            <tr><td colspan="3" style="color:#57534e;">Mission centre&nbsp;&nbsp;<b>{meta['centre']}</b></td></tr>
          </table>

          <br/>
          {section('PER-FRAME DETAIL')}
          <table width="100%" cellspacing="0" cellpadding="5" border="1" bordercolor="#e8e2d8"
                 style="font-size:8pt;">
            <tr bgcolor="#f1ebe2">
              <td><b>File</b></td><td><b>Status</b></td><td align="center"><b>Inliers</b></td>
              <td align="center"><b>Tier</b></td><td align="center"><b>Angle</b></td>
              <td><b>Lat / Lon</b></td><td align="center"><b>Time</b></td><td><b>Note</b></td>
            </tr>
            {''.join(body)}
          </table>

          <br/><br/>
          <table width="100%" cellspacing="0" cellpadding="0"><tr>
            <td style="font-size:7.5pt;color:#a8a29e;">
              <b>Tier 1</b> = full perspective lock (high confidence).
              <b>Tier 2</b> = rigid-transform fallback (reduced accuracy).
              <b>Skipped</b> = could not be georeferenced.
              <b>Not saved locally</b> = processed successfully and charged, but the
              file could not be written to this computer; it is still retrievable.
              Check your balance in the plugin for how a run affected your allowance.<br/>
              Generated by ATLAS&middot;GEO, AIVE AI Systems.
            </td>
          </tr></table>
        </body></html>
        """

    def export_report(self):
        if not self._job_results and not self._failed_jobs:
            self._themed_notice("Nothing to export",
                                "Run a registration first, then export its report.",
                                accent="red", button="OK")
            return

        save_path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Report", "atlas_report.pdf", "PDF Files (*.pdf)")
        if not save_path:
            return

        import csv
        from qgis.PyQt.QtGui import QTextDocument, QFont
        from qgis.PyQt.QtPrintSupport import QPrinter

        rows = self._collect_report_rows()

        # ── Aggregates (mirror the on-screen REGISTRATION METRICS card) ──
        results     = list(self._job_results.values())
        inliers     = [r["num_inliers"] for r in results if r.get("num_inliers") is not None]
        elapsed     = [r["elapsed_s"]   for r in results if r.get("elapsed_s")   is not None]
        avg_inliers = sum(inliers) / len(inliers) if inliers else 0
        confidence  = min(100.0, avg_inliers)        # inliers≈% (avg/100*100)
        total_time  = sum(elapsed) if elapsed else 0
        best        = max(results, key=lambda r: r.get("num_inliers") or 0) if results else {}
        centre = "—"
        if best.get("lat") is not None and best.get("lon") is not None:
            centre = f"{best['lat']:.5f}, {best['lon']:.5f}"

        mission = "—"
        if getattr(self, "input_mission_name", None) and self.input_mission_name.text().strip():
            mission = self.input_mission_name.text().strip()
        basemap = "Satellite"
        combo = getattr(self, "combo_basemap_setup", None)
        if combo is not None and combo.currentText():
            basemap = combo.currentText()

        meta = {
            "mission":     mission,
            "generated":   time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            "user":        self.current_user_email or "—",
            "basemap":     basemap,
            **self._report_counts(),
            "confidence":  confidence,
            "avg_inliers": avg_inliers,
            "total_time":  total_time,
            "centre":      centre,
        }

        try:
            # ── PDF ──
            pdf_path = save_path if save_path.lower().endswith(".pdf") else save_path + ".pdf"
            printer = QPrinter(QPrinter.HighResolution)
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(pdf_path)
            printer.setPageSize(QPrinter.A4)
            try:
                printer.setPageMargins(14, 14, 14, 14, QPrinter.Millimeter)
            except Exception:
                pass  # older/newer signature — default margins are fine

            doc = QTextDocument()
            doc.setDefaultFont(QFont("Segoe UI", 9))
            # With printer.setPageSize(A4) + no doc.setPageSize(), the document
            # lays out at ~96 DPI logical: pt fonts render at physical size AND
            # HTML px are logical px. So the logo height is a small fixed px
            # value (~42) — do NOT scale it by the printer DPI or it balloons to
            # half the page and squashes the header text into vertical columns.
            doc.setHtml(self._report_html(
                rows, meta, logo_uri=self._logo_data_uri(), logo_h=42))
            # NOTE: do NOT call doc.setPageSize() here. print_() binds the
            # document layout to the printer's DPI and page (A4 set above), so
            # pt-based fonts scale to physical size. Forcing a device-pixel page
            # size makes text microscopic on a giant page.
            doc.print_(printer)

            # ── CSV (same per-frame rows, machine-readable) ──
            csv_path = pdf_path[:-4] + ".csv"
            with open(csv_path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["file", "status", "inliers", "tier",
                            "angle_deg", "lat", "lon", "elapsed_s", "reason"])
                for r in rows:
                    w.writerow([
                        r["file"],
                        r["status"].lower().replace(" ", "_"),
                        r["inliers"] if r["inliers"] is not None else "",
                        r["tier"]    if r["tier"]    is not None else "",
                        r["angle"]   if r["angle"]   is not None else "",
                        r["lat"]     if r["lat"]     is not None else "",
                        r["lon"]     if r["lon"]     is not None else "",
                        r["elapsed"] if r["elapsed"] is not None else "",
                        r["reason"],
                    ])
        except Exception as e:
            self._themed_notice("Export failed", f"Could not write report:\n{e}",
                                accent="red", button="Close")
            return

        self._themed_notice(
            "Report saved",
            f"Saved PDF + CSV:\n{os.path.basename(pdf_path)}\n{os.path.basename(csv_path)}",
            accent="green", button="Done", primary_button=True)

    # ─────────────────────────────────────────────────────────
    # FEEDBACK
    # ─────────────────────────────────────────────────────────
    @staticmethod
    def _imagery_requirements_html():
        """Operator guidance on what ATLAS needs from an image, and why.

        Single source of truth: rendered inline by the Setup-page accordion and
        by the account-menu dialog (the Results page has no accordion), so the
        two can't drift apart.

        The altitude section matters more than it looks: flight altitude decides
        how wide an area of satellite reference map gets fetched for matching. If
        it is missing the smallest reference area is used, which is usually far
        too small for a real flight height and produces weak or failed matches
        with no cause the operator can see.
        """
        return (
            "<p style='margin:0 0 9px 0;'><b>GPS coordinates (required)</b><br>"
            "Every image must have GPS latitude and longitude embedded. Images "
            "without GPS cannot be georeferenced and are skipped before upload.</p>"

            "<p style='margin:0 0 4px 0;'><b>Flight altitude (strongly recommended)</b><br>"
            "Altitude decides how much satellite reference map is fetched to match "
            "your photo against. Without it the smallest reference area is used, "
            "which is usually too small for a real flight and gives weak or failed "
            "matches.</p>"

            "<p style='margin:0 0 9px 0; padding:7px 9px; background:#fff3e8;"
            " border-left:3px solid #ea580c; border-radius:4px;'>"
            "<b>Use height above ground (AGL), not height above sea level (ASL).</b><br>"
            "A drone 100 m above a 500 m plateau is 100 m AGL but 600 m ASL. ATLAS "
            "needs AGL, because that is what determines how much ground your camera "
            "actually sees.</p>"

            "<p style='margin:0 0 9px 0;'><b>Where the altitude comes from</b><br>"
            "DJI aircraft write a relative-altitude field that is already AGL, and "
            "ATLAS uses it automatically. Other cameras usually record only the "
            "standard GPS altitude, which is ASL. ATLAS falls back to that when no "
            "AGL value is present. On flat terrain the difference is small; over "
            "hills, valleys or coastline it can be significant, so check what your "
            "survey software writes.</p>"

            "<p style='margin:0 0 4px 0;'><b>Camera angle</b><br>"
            "Point the camera straight down wherever accuracy matters. ATLAS "
            "matches your photo against a satellite map, which is itself a "
            "straight-down view, so the more the camera is tilted the less the "
            "two have in common. Tall features are seen from the side in an "
            "angled photo and from above on the map.</p>"

            "<p style='margin:0 0 9px 0; padding:7px 9px; background:#fff3e8;"
            " border-left:3px solid #ea580c; border-radius:4px;'>"
            "Angled images are still processed, and often succeed. They are "
            "simply less reliable, and increasingly so past about 40 degrees "
            "from vertical. ATLAS reads the camera angle from each photo and "
            "will tell you when a batch contains steeply angled frames.</p>"

            "<p style='margin:0 0 9px 0;'><b>What makes a scene hard to match</b><br>"
            "Georeferencing works by finding the same ground detail in your photo "
            "and in the satellite map, so it depends on there being distinctive "
            "detail to find. Expect lower success over open, arid or uniform "
            "terrain, dense tree canopy, and water. Built-up areas, parkland and "
            "anything with roads, buildings or field boundaries work best. Very "
            "low flights can also struggle, because each frame covers less "
            "ground and therefore contains fewer landmarks.</p>"

            "<p style='margin:0 0 9px 0;'><b>Before a survey, check that</b><br>"
            "&bull; GPS is enabled and has a fix before takeoff<br>"
            "&bull; Your drone or flight software writes altitude into each photo, "
            "not only into a separate flight log<br>"
            "&bull; You upload the original files. Edited or exported copies, and "
            "most PNG conversions, silently strip this metadata</p>"

            "<p style='margin:0; color:#8b8378;'>"
            "Supported formats: JPG, JPEG, TIF, TIFF, GeoTIFF.</p>"
        )

    def _show_imagery_requirements(self):
        """Modal version of the guidance, for the account menu (reachable from
        the Results page, which has no inline accordion). The Setup page uses
        the inline accordion instead so the reference stays visible while the
        user is actually choosing files."""
        dlg = ThemedDialog(self, "Imagery requirements",
                           "What your images need for accurate georeferencing")

        body = QtWidgets.QLabel(self._imagery_requirements_html())
        body.setTextFormat(QtCore.Qt.RichText)
        body.setWordWrap(True)
        body.setMinimumWidth(440)
        body.setStyleSheet(
            "font-size:12px; color:#44403c; background:transparent; border:none;")

        # The guidance is long, and ThemedDialog ends with adjustSize(), which sizes
        # to whatever the content asks for. That made this modal taller than the
        # plugin window and it spilled off the bottom of the screen, taking the
        # Got it button with it. Scroll the body and cap it against the ACTUAL
        # available screen height, leaving room for this dialog's own header,
        # divider and footer, so it fits on a small or display-scaled monitor too.
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea { background:transparent; border:none; }"
            "QScrollBar:vertical { width:8px; background:transparent; margin:2px; }"
            "QScrollBar::handle:vertical { background:#d8cec1; border-radius:4px;"
            "  min-height:28px; }"
            "QScrollBar::handle:vertical:hover { background:#c3b6a4; }"
            "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }"
            "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {"
            "  background:transparent; }")
        holder = QtWidgets.QWidget()
        holder.setStyleSheet("background:transparent;")
        hv = QtWidgets.QVBoxLayout(holder)
        hv.setContentsMargins(0, 0, 6, 0)   # gutter so text never sits under the bar
        hv.addWidget(body)
        scroll.setWidget(holder)
        try:
            scr = self.screen() or QtWidgets.QApplication.primaryScreen()
            avail_h = scr.availableGeometry().height()
        except Exception:
            avail_h = 900
        # ~150px covers the dialog's title block, divider, footer and outer margins.
        scroll.setMaximumHeight(max(260, int(avail_h * 0.72) - 150))
        dlg.body.addWidget(scroll)

        foot = QtWidgets.QHBoxLayout()
        foot.addStretch(1)
        ok = self._styled_button("Got it", primary=False, min_height=34)
        ok.clicked.connect(dlg.accept)
        foot.addWidget(ok)
        dlg.body.addSpacing(6)
        dlg.body.addLayout(foot)

        dlg.adjustSize()
        dlg.exec_()

    def _open_feedback_dialog(self):
        dlg = FeedbackDialog(user_email=self.current_user_email or "", parent=self)
        dlg.exec_()

    def _get_trial_status(self):
        """Latest trial-request state ('none'|'pending'|'approved'|'rejected').

        Network call — callers must run this off the UI thread. Returns 'none' on
        any error so an older backend without the endpoint simply behaves as before.
        """
        try:
            resp = self._authed_request("GET", TRIAL_STATUS_URL, timeout=10)
            if resp.status_code == 200:
                d = resp.json()
                # Stash the previous answers for a resubmission to pre-fill. A plain
                # dict, so assigning it from this worker thread is safe -- unlike the
                # menu widgets, which go through a queued slot.
                self._trial_request_previous = d.get("previous") or {}
                return str(d.get("state") or "none").lower()
        except Exception:
            pass
        return "none"

    @QtCore.pyqtSlot(str)
    def _set_trial_request_ui(self, state: str):
        """Colour and label the trial entry according to where the request stands.

        The entry carries the state because it is the ONLY way a declined user
        finds out: this service sends no email, so a rejection would otherwise be
        silent and they would wait indefinitely. Menu text is the one surface they
        are guaranteed to see.

        Amber, not red: a declined trial is a decision, not an error the user
        caused. The label names the ACTION rather than restating the bad news --
        "Resubmit Trial Request" says what the click does, and the modal behind it
        still opens with the denial, so nothing is hidden.

        A pyqtSlot invoked via QueuedConnection because the balance refresh runs on
        a worker thread and Qt widgets may only be touched on the UI thread.
        """
        self._trial_request_state = state
        # SHOW_TRIAL_REQUEST is checked HERE as well as at build time. This slot
        # runs after every balance refresh and re-applies visibility, so without
        # the flag it would silently re-show the row that _add_trial_menu_row had
        # just hidden — the launch gate would hold only until the first refresh.
        #
        # Only PAYG users could receive a trial; anyone already on a plan would
        # just be told they have one. An approved request means they are no longer
        # PAYG, so that case disappears on its own.
        show = (SHOW_TRIAL_REQUEST
                and (getattr(self, "_current_tier", None) or "payg").lower() == "payg")
        # No em dashes anywhere here: flagged repeatedly as an "AI generated" tell.
        label, colour, bold, chevron = {
            "pending":  ("Trial request: awaiting review", "#b45309", True,  False),
            "rejected": ("Resubmit Trial Request",         "#b45309", True,  True),
        }.get(state, ("Request a free trial", "#44403c", False, False))

        for act_attr, row_attr in (("_trial_request_action", "_trial_request_row"),
                                   ("_trial_request_action_setup",
                                    "_trial_request_row_setup")):
            act = getattr(self, act_attr, None)
            row = getattr(self, row_attr, None)
            try:
                if act is not None:
                    act.setVisible(show)
                if row is not None:
                    # The widget must follow the action. A hidden QWidgetAction
                    # still paints its default widget, which overlapped the
                    # Storage caption underneath it.
                    row.setVisible(show)
                    row.set_row(label, colour=colour, bold=bold, chevron=chevron)
            except RuntimeError:
                pass   # underlying Qt object already destroyed

    def _show_trial_denied_dialog(self):
        """The denial modal. Returns "resubmit", "buy" or "close".

        Its own dialog rather than _themed_confirm because this needs THREE routes,
        not two: a declined user may want to try again, or may just want capacity
        now. Buy Credits sits at the far left of the footer, away from Close, so it
        reads as a real third option -- a link buried in the paragraph gets skimmed
        past, and by then the user has already decided what to do.
        """
        dlg = ThemedDialog(self, "Trial request not approved")
        msg = QtWidgets.QLabel(
            "Your free-trial request wasn't approved this time.\n\n"
            "If your circumstances have changed, or you think there's been a "
            "mistake, you're welcome to submit a new request with more detail."
            + ("\n\nNeed access right away? You can buy credits without a trial."
               if SHOW_PAYG else ""))
        msg.setWordWrap(True)
        msg.setMinimumWidth(320)
        msg.setStyleSheet(
            "font-size: 13px; color: #57534e; background: transparent; border: none;")
        dlg.body.addWidget(msg)

        result = {"choice": "close"}
        # All three actions cluster bottom-right, in ascending commitment:
        # Buy Credits (link) -> Close (secondary) -> Submit (primary). Anchoring
        # the link far left instead split the footer into two unrelated zones.
        foot = QtWidgets.QHBoxLayout()
        foot.addStretch(1)
        buy = QtWidgets.QPushButton("Buy Credits")
        buy.setCursor(QtCore.Qt.PointingHandCursor)
        # Same link styling as the Plans dialog footer, so it is a familiar
        # affordance rather than a new one. Extra right padding keeps it from
        # crowding Close now that they sit side by side.
        buy.setStyleSheet(
            "QPushButton { background: transparent; color: #ea580c; border: none;"
            "  font-size: 12px; font-weight: 600; text-decoration: underline;"
            "  padding: 4px 10px 4px 4px; }"
            "QPushButton:hover { color: #c2410c; }")
        buy.clicked.connect(lambda: (result.__setitem__("choice", "buy"), dlg.accept()))
        buy.setVisible(SHOW_PAYG)
        close = self._styled_button("Close", primary=False, min_height=34)
        close.clicked.connect(dlg.reject)
        again = self._styled_button("Submit a new request", primary=True,
                                    accent="orange", min_height=34)
        again.clicked.connect(lambda: (result.__setitem__("choice", "resubmit"),
                                       dlg.accept()))
        foot.addWidget(buy)
        foot.addWidget(close)
        foot.addWidget(again)
        dlg.body.addSpacing(6)
        dlg.body.addLayout(foot)

        dlg.adjustSize()
        dlg.exec_()
        return result["choice"]

    def _open_trial_request_dialog(self):
        """Free-trial request: show where an existing request stands, or open the form.

        Branching here is what actually closes the notification gap. Nothing in the
        the service sends email, so if this always opened a blank form a declined
        user would have no way of learning the outcome, and would just re-submit
        into silence.
        """
        if not self.access_token:
            self._themed_notice(
                "Sign in required",
                "Please sign in before requesting a trial, so we know who to "
                "get back to.", accent="orange")
            return

        state = getattr(self, "_trial_request_state", "none")

        if state == "pending":
            self._themed_notice(
                "Request awaiting review",
                "Your trial request is with our team. We'll update this menu once "
                "it has been reviewed. There's nothing else you need to do.",
                icon="", accent="orange", button="Close")
            return

        resubmit = False
        if state == "rejected":
            # The admin's note is deliberately not shown: it is an internal reviewer
            # comment, not copy written for the applicant. Re-applying is allowed
            # (the backend only blocks a second PENDING request), so offer it.
            choice = self._show_trial_denied_dialog()
            if choice == "buy":
                self._buy_tokens()
                return
            if choice != "resubmit":
                return
            resubmit = True

        # Resubmit copy applies to THIS path only. Telling a first-time applicant to
        # "resubmit" and give MORE detail before they have submitted anything would
        # be wrong, so the dialog carries two modes rather than being reworded outright.
        dlg = TrialRequestDialog(user_email=self.current_user_email or "",
                                 access_token=self.access_token,
                                 resubmit=resubmit,
                                 previous=getattr(self, "_trial_request_previous", None),
                                 parent=self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            # Reflect the new state immediately rather than waiting for the next
            # balance refresh, so the menu doesn't still read "Resubmit Trial Request".
            self._set_trial_request_ui("pending")
            self._themed_notice(
                "Request sent",
                "Thanks, your trial request is with our team. Check back in this "
                "menu to see the decision.",
                accent="green", primary_button=True)

    def _restart_mission(self):
        self._processing_active = False
        self._was_cancelled = False
        self._batch_id = None
        self.selected_file = None
        self.selected_files = []
        self._result_paths = []
        self._job_results = {}
        self._failed_jobs = {}
        self._upload_rows = []
        # ⚠️ _local_save_failures IS NOT CLEARED HERE, for the same reason it is
        # not cleared in _start_processing: an unsaved result is work that was
        # done and charged for, and this dictionary holds the only record of
        # what is missing plus the URL needed to fetch it without paying again.
        #
        # Clearing it here defeated that protection entirely. "New Mission" is
        # the ONLY route from the Results page back to Setup, so every user who
        # finished a batch with unsaved results and started another one lost the
        # ability to recover them, silently, with the button vanishing. The
        # careful preservation in _start_processing could never be reached.
        #
        # Cleared on logout and on user switch (_clear_session_state), which is
        # correct: that state belongs to the previous account.
        self._rebuild_recent_uploads()
        self._job_lat = 0.0
        self._job_lon = 0.0
        self.label_file_info.setText("No file selected")
        self.label_file_info.setStyleSheet("color: #a8a29e; font-size: 11px; font-style: italic;")
        self.label_drop_zone.setText("Drop image here")
        self.label_drop_zone.setStyleSheet("")
        if hasattr(self, "btn_next_setup"):
            self.btn_next_setup.setEnabled(False)
        if hasattr(self, "frame_drop_zone"):
            self.frame_drop_zone.setStyleSheet("")   # revert dashed box (grey dashed + hover)
        if hasattr(self, 'input_mission_name'):
            self.input_mission_name.clear()
        self.stacked_pages.setCurrentIndex(self.PAGE_SETUP)

    def _reset_step_labels(self):
        for i, lbl in enumerate(self._step_labels):
            lbl.setText(_GLYPH_PENDING + _STEP_LABELS[i])
            lbl.setStyleSheet(_CLR_PENDING)