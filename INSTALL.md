# Installing ATLAS Geo-Dock

## 1. Install the plugin

**From the QGIS Plugin Repository (recommended)**

1. In QGIS, open Plugins > Manage and Install Plugins.
2. Search for "ATLAS Geo-Dock".
3. Click Install Plugin.

**From a ZIP file**

1. In QGIS, open Plugins > Manage and Install Plugins.
2. Choose "Install from ZIP".
3. Select `atlas_geo_plugin.zip` and click Install Plugin.

QGIS 3.44 or newer is required.

## 2. Install the Python dependencies

The plugin needs two Python packages that are not part of a standard QGIS
installation:

- `rasterio`
- `requests`

`numpy` and GDAL already ship with QGIS.

These must be installed into **the Python that QGIS itself uses**, which is
usually not the same Python you get from a normal terminal. The reliable way to
target the correct interpreter is to install from inside QGIS.

Open Plugins > Python Console in QGIS and run:

```python
import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install", "rasterio", "requests"])
```

Then restart QGIS.

Versions are deliberately left unpinned so that `rasterio` binds against the
GDAL version your QGIS was built with.

### If that does not work

**Windows.** Use the OSGeo4W Shell (search for it in the Start menu) rather than
a normal Command Prompt, then run:

```
python -m pip install rasterio requests
```

**macOS.** If QGIS was installed from the official package, use its bundled
Python:

```
/Applications/QGIS.app/Contents/MacOS/bin/python3 -m pip install rasterio requests
```

**Linux.** If QGIS came from your distribution's package manager, prefer the
distribution packages, for example `python3-rasterio` and `python3-requests`,
so they match the system GDAL.

If your environment blocks installation, ask whoever administers the machine to
install the two packages into the QGIS Python.

## 3. Verify

Open the plugin from the toolbar or the Plugins menu. If a dependency is
missing, the plugin reports it when it opens and names the package. If it opens
to the sign-in screen, the dependencies are in place.

## 4. Create an ATLAS account

Georeferencing runs on AIVE AI Systems' hosted ATLAS service, not on your machine.
You need an account and an internet connection. You can create an account from
the plugin's sign-up screen.

Access to the service is limited to approved jurisdictions. The plugin is
downloadable worldwide, but an account may not be usable outside them.

## Uninstalling

Plugins > Manage and Install Plugins > Installed, select ATLAS Geo-Dock, then
Uninstall Plugin. This removes the plugin only. It does not remove `rasterio`
or `requests`, which other software may be using, and it does not delete data
held by the ATLAS service. To remove your account data, use the account options
in the plugin or contact AIVE AI Systems.
