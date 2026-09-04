# ATLAS Geo-Dock

A QGIS plugin that georeferences UAV imagery against reference maps using
AI-powered pose estimation.

Select drone images in QGIS, submit them, and receive georeferenced outputs you
can load straight onto the canvas. The plugin handles batch submission, a
5-step rotation sweep, auto-scaling, and an interactive preview of where each
image was placed.

## Important: this plugin uses a hosted service

Georeferencing does **not** run on your machine. The images you select and the
results produced are uploaded to and processed by AIVE AI Systems' hosted ATLAS
service.

Using the plugin therefore requires:

- an ATLAS account (you can create one from inside the plugin), and
- a working internet connection.

Access to the ATLAS service is limited to approved jurisdictions. The plugin
itself is downloadable worldwide, but an account may not be usable outside
those jurisdictions.

Before submitting imagery, please review AIVE AI Systems' Terms of Service and
Privacy Policy, which the plugin links to at sign-up.

## Requirements

- QGIS 3.44 or newer
- Python packages `rasterio` and `requests`

`numpy` and GDAL ship with QGIS. `rasterio` and `requests` may not be present
in a stock install. The plugin tells you when they are missing.

See [INSTALL.md](INSTALL.md) for installation instructions.

## Installation

Install from the QGIS Plugin Repository (Plugins > Manage and Install Plugins),
or install the ZIP directly. Full instructions, including the dependencies, are
in [INSTALL.md](INSTALL.md).

## Usage

1. Open the plugin from the toolbar or the Plugins menu.
2. Sign in, or create an account.
3. Choose your drone images and submit them.
4. Watch progress in the plugin, then load the georeferenced results onto the
   map when they are ready.

## Reporting problems

Please open an issue on the tracker linked from `metadata.txt`. Include your
QGIS version, your operating system, and the message the plugin displayed.

## Licence

GNU General Public License, version 2 or (at your option) any later version.
See [LICENSE](LICENSE).

This plugin was generated from the
[QGIS Plugin Builder](http://g-sherman.github.io/Qgis-Plugin-Builder/) templates
by Gary Sherman, which are also distributed under the GPL.
