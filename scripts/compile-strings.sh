#!/bin/bash
# Compile translation strings
# This script compiles .ts files to .qm files

PLUGIN_DIR=$(dirname "$0")/..
I18N_DIR=$PLUGIN_DIR/i18n

echo "Compiling translation files..."
for f in $I18N_DIR/*.ts
do
    qmake -project -o $PLUGIN_DIR/atlas_geo_plugin.pro $PLUGIN_DIR
    lrelease $f -qm ${f%.ts}.qm
done

echo "Translation compilation complete!"
