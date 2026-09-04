#############################################
# Makefile for ATLAS Geo-Dock Plugin
#############################################

PLUGINNAME = atlas_geo_plugin
PLUGINS_DIR = ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins
PLUGIN_DIR = $(PLUGINS_DIR)/$(PLUGINNAME)

# _iso3166_data.py is GENERATED (infra/gen-iso3166.py) and imported by the dialog
# for the signup country dropdown. Omit it and the plugin still loads, because the
# import falls back to an empty list, but the country field silently offers nothing.
PY_FILES = atlas_geo_plugin.py atlas_geo_plugin_dialog.py __init__.py resources.py \
           _iso3166_data.py
UI_FILES = atlas_geo_plugin_dialog_base.ui
RESOURCE_FILES = resources.qrc
# LICENSE is REQUIRED in the package: the QGIS plugin repository expects one and
# infra/verify-plugin-artifact.py refuses to release without a real licence file.
EXTRAS = metadata.txt icon.png aive_logo.png LICENSE README.md INSTALL.md requirements.txt
COMPILED_RESOURCE_FILES = resources.py
# `test` is deliberately NOT shipped: it is a development harness, adds nothing
# for a user, and the release gate rejects a package containing it.
EXTRA_DIRS = help i18n scripts

.PHONY: all deploy dclean clean compile

all: compile

compile: $(COMPILED_RESOURCE_FILES)

$(COMPILED_RESOURCE_FILES): $(RESOURCE_FILES)
	pyrcc5 -o $@ $<

deploy: compile
	@echo
	@echo "Deploying plugin to: $(PLUGIN_DIR)"
	@mkdir -p $(PLUGIN_DIR)
	@cp -vf $(PY_FILES) $(PLUGIN_DIR)
	@cp -vf $(UI_FILES) $(PLUGIN_DIR)
	@cp -vf $(RESOURCE_FILES) $(PLUGIN_DIR)
	@cp -vf $(EXTRAS) $(PLUGIN_DIR)
	@cp -rv $(EXTRA_DIRS) $(PLUGIN_DIR)
	@echo "Plugin deployed successfully"

dclean:
	@echo
	@echo "Removing deployed plugin from: $(PLUGIN_DIR)"
	@rm -Rf $(PLUGIN_DIR)
	@echo "Plugin removed"

clean:
	@echo "Cleaning compiled files"
	@find . -name "*.pyc" -delete
	@find . -name "__pycache__" -type d -exec rm -rf {} +
	@rm -f resources.py
	@echo "Clean complete"

help:
	@echo "ATLAS Geo-Dock Plugin Makefile"
	@echo ""
	@echo "Available targets:"
	@echo "  all         - Compile resources"
	@echo "  compile     - Compile Qt resources"
	@echo "  deploy      - Deploy plugin to QGIS"
	@echo "  dclean      - Remove deployed plugin"
	@echo "  clean       - Clean compiled files"
	@echo "  help        - Show this help message"
