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
from qgis.PyQt.QtWidgets import QAction
# QgsRasterLayer / QgsProject were imported only for the on-open basemap loader
# removed below. Left in, they read as "this file still touches map layers",
# which is exactly the impression that let a second basemap implementation sit
# here unnoticed.

from .resources import *
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

    def tr(self, message):
        return QCoreApplication.translate('AtlasGeoHandlerDemo', message)

    def add_action(self, icon_path, text, callback,
                   enabled_flag=True, add_to_menu=True,
                   add_to_toolbar=True, parent=None):
        icon   = QIcon(icon_path)
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
        for action in self.actions:
            self.iface.removePluginMenu(self.tr(u'&Atlas Geo Georeferencer'), action)
            self.iface.removeToolBarIcon(action)

    # No basemap is loaded when the plugin opens.
    #
    # The dialog's _load_basemap (atlas_geo_plugin_dialog.py) is the single
    # place that creates one, on View on Map. It runs after sign-in, which is
    # the first point at which a tile session can exist, and it keeps the
    # imagery source configurable server-side rather than fixed in a
    # published artifact.

    def run(self):
        if self.first_start:
            self.first_start = False
            self.dlg = AtlasGeoHandlerDemoDialog(self.iface) 
            self.dlg.processing_complete.connect(self._on_processing_complete)
            self.dlg.stacked_pages.setCurrentIndex(0)

        self.dlg.show()

    def _on_processing_complete(self):
        pass  
    