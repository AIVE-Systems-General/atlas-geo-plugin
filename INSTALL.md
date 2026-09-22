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

## Dependencies

None. The plugin uses only what a standard QGIS
installation already provides: the GDAL library and the QGIS

### If that does not work

**Windows.** Use the OSGeo4W Shell (search for it in the Start menu) rather than
a normal Command Prompt, then run:

```

```

**macOS.** If QGIS was installed from the official package, use its bundled
Python:

```

```

**Linux.** If QGIS came from your distribution's package manager, prefer the

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

or `requests`, which other software may be using, and it does not delete data
held by the ATLAS service. To remove your account data, use the account options
in the plugin or contact AIVE AI Systems.
