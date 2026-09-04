#!/bin/bash
# Run ATLAS Geo-Dock plugin in QGIS development environment

# Setup environment
export QGIS_PLUGIN_PATH=~/.local/share/QGIS/QGIS3/profiles/default/python/plugins

# Get plugin directory
PLUGIN_DIR=$(dirname "$0")/..

# Create symlink if not exists
if [ ! -L "$QGIS_PLUGIN_PATH/atlas_geo_plugin" ]; then
    mkdir -p "$QGIS_PLUGIN_PATH"
    ln -s "$PLUGIN_DIR" "$QGIS_PLUGIN_PATH/atlas_geo_plugin"
fi

echo "Plugin linked to: $QGIS_PLUGIN_PATH/atlas_geo_plugin"
echo "Start QGIS and enable the ATLAS Geo-Dock plugin in the plugin manager"
