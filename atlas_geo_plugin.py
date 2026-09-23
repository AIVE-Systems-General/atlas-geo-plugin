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
from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.core import Qgis, QgsMessageLog
# QgsRasterLayer / QgsProject were imported only for the on-open basemap loader
# removed below. Left in, they read as "this file still touches map layers",
# which is exactly the impression that let a second basemap implementation sit
# here unnoticed.

# Explicit, not `from .resources import *`.
#
# The wildcard was flagged by the QGIS repository scan as F403, and it was
# genuinely opaque: resources.py is a generated stub whose qt_resource_data is
# empty, so the wildcard bound a handful of names that nothing in this plugin
# referenced. Every icon is loaded from disk by path (see initGui), and no ':/'
# resource URL appears anywhere in the codebase.
#
# qInitResources() is still called, because it is the conventional registration
# hook: if resources.py is ever recompiled with real data, the resources become
# available without anyone having to remember to add this line back.
from .resources import qInitResources
from .atlas_geo_plugin_dialog import AtlasGeoHandlerDemoDialog
import os

class AtlasGeoHandlerDemo:
    """QGIS Plugin entry point for the ATLAS Georeferencing system."""

    def __init__(self, iface):
        self.iface       = iface
        self.plugin_dir  = os.path.dirname(__file__)
        self.actions     = []
        self.menu        = self.tr(u'&Atlas Geo Georeferencer')
        self.first_start = True
        # Declared up front so a failed construction leaves a defined
        # attribute rather than a missing one. See run() for why that matters.
        self.dlg         = None

    def tr(self, message):
        return QCoreApplication.translate('AtlasGeoHandlerDemo', message)

    def add_action(self, icon_path, text, callback,
                   enabled_flag=True, add_to_menu=True,
                   add_to_toolbar=True, parent=None):
        icon = QIcon(icon_path)
        action = QAction(icon, text, parent)
        action.triggered.connect(callback)
        action.setEnabled(enabled_flag)
        if add_to_toolbar:
            self.iface.addToolBarIcon(action)
        if add_to_menu:
            self.iface.addPluginToMenu(self.menu, action)
        self.actions.append(action)
        return action

    def initGui(self):
        # Register the compiled Qt resources. Currently a no-op, because the
        # generated stub carries no data, but it is the hook that makes ':/'
        # paths work if resources.py is ever recompiled for real.
        qInitResources()
        # Load directly from disk rather than the compiled Qt resource (:/...) path —
        # icon.png didn't exist when resources.py was last compiled, so the resource
        # path silently resolved to a blank icon. This works regardless of whether
        # resources.py is ever recompiled.
        self.add_action(
            os.path.join(self.plugin_dir, 'icon.png'),
            text=self.tr(u'Atlas Georeferencer'),
            callback=self.run,
            parent=self.iface.mainWindow(),
        )
        self.first_start = True

    def unload(self):
        """Remove what initGui added, and release the dialog.

        self.actions is CLEARED afterwards. Left populated, a disable/enable
        cycle appends a second action to the same list while the first is
        already gone from the toolbar, so a later unload iterates over stale
        objects and the toolbar can accumulate duplicates.
        """
        for action in self.actions:
            self.iface.removePluginMenu(self.tr(u'&Atlas Geo Georeferencer'), action)
            self.iface.removeToolBarIcon(action)
        self.actions = []
        # Release the dialog too, so reloading the plugin builds a fresh one
        # against the reloaded module rather than keeping the old instance.
        if self.dlg is not None:
            try:
                self.dlg.close()
                self.dlg.deleteLater()
            except Exception as exc:                          # noqa: BLE001
                QgsMessageLog.logMessage(
                    f"dialog not released on unload: {type(exc).__name__}",
                    "ATLAS Geo-Dock", level=Qgis.MessageLevel.Warning)
            self.dlg = None
        self.first_start = True

    # No basemap is loaded when the plugin opens.
    #
    # The dialog's _load_basemap (atlas_geo_plugin_dialog.py) is the single
    # place that creates one, on View on Map. It runs after sign-in, which is
    # the first point at which a tile session can exist, and it keeps the
    # imagery source configurable server-side rather than fixed in a
    # published artifact.

    def run(self):
        """Open the dialog, constructing it on first use.

        ⚠️ THE ORDER OF THE TWO STATEMENTS BELOW IS THE BUG THIS METHOD USED
        TO HAVE. `first_start` was cleared BEFORE the dialog was constructed,
        so if the constructor raised, the flag stayed False and this method
        could never try again. The first click surfaced the real error; every
        click after it raised

            AttributeError: 'AtlasGeoHandlerDemo' object has no attribute 'dlg'

        which is the error users actually reported, and which says nothing
        about the real cause. The latch is now cleared only AFTER the dialog
        exists, so a transient failure can be retried and a permanent one
        reports itself every time instead of mutating into a different error.
        """
        if self.dlg is None:
            try:
                dlg = AtlasGeoHandlerDemoDialog(self.iface)
                dlg.processing_complete.connect(self._on_processing_complete)
                dlg.stacked_pages.setCurrentIndex(0)
            except Exception as exc:                          # noqa: BLE001
                # ⚠️ NEVER LOG str(exc). An exception message is attacker- and
                # environment-controlled text: OSError carries absolute paths
                # including the account name, and a network error carries the
                # URL with its query string, which is where a token would be.
                # An earlier version of this handler interpolated {exc} under a
                # comment claiming it was safe; it was not, and a token in a
                # ?token= parameter went straight into the QGIS log.
                #
                # What is recorded is enough to route a bug report and nothing
                # more: the stage that failed, the exception CLASS name, and a
                # stable code the user can quote.
                # crc32, NOT hash(). Python randomises str hashing per process
                # (PYTHONHASHSEED), so hash() would give a different code on
                # every launch and be useless for matching two reports.
                import zlib
                code = ("ATLAS-INIT-"
                        f"{zlib.crc32(type(exc).__name__.encode()) % 10000:04d}")
                QgsMessageLog.logMessage(
                    f"startup failed at stage=dialog_construct "
                    f"error_class={type(exc).__name__} code={code}",
                    "ATLAS Geo-Dock", level=Qgis.MessageLevel.Critical)
                QMessageBox.critical(
                    self.iface.mainWindow(),
                    self.tr("ATLAS Geo-Dock could not open"),
                    self.tr(
                        "The plugin could not start.\n\n"
                        "Reference code: {code}\n\n"
                        "Please report this code along with your QGIS version "
                        "and operating system. Further detail is in the QGIS "
                        "message log under 'ATLAS Geo-Dock'.").format(code=code))
                return
            # Only now is the dialog real. Assign last.
            self.dlg = dlg
            self.first_start = False

        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()

    def _on_processing_complete(self):
        pass  
    