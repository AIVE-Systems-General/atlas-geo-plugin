#!/bin/bash
# Update translation strings
# This script extracts translatable strings from Python and UI files

PLUGIN_DIR=$(dirname "$0")/..
I18N_DIR=$PLUGIN_DIR/i18n

echo "Extracting translatable strings..."
pylupdate5 -noobsolete -pro $PLUGIN_DIR/atlas_geo_plugin.pro -ts $I18N_DIR/*.ts

echo "Translation update complete!"
