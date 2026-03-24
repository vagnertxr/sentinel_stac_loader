# <img src="https://raw.githubusercontent.com/vagnertxr/sentinel_stac_loader/main/icon.png" width="32" valign="middle"/> Quick VRT Imagery Loader

A QGIS plugin designed for searching and loading satellite imagery via the **Microsoft Planetary Computer STAC API**. This tool optimizes the remote sensing workflow by providing pre-configured band compositions and loading imagery directly as Virtual Rasters (VRT).

Supported satellites: **Sentinel-2 L2A** and **Landsat Collection 2 L2**.

Developed with Python and the QGIS Plugin Builder tool.

## Installation

### Via QGIS Plugin Manager *(recommended)*

1. Open QGIS and go to **Plugins > Manage and Install Plugins**
2. Click "All" then Search for `Quick VRT Imagery Loader`
3. Click **Install Plugin**

### Via ZIP file

Download the `.zip` file from this repository and use the **Install from ZIP** option in the QGIS Plugin Manager, or extract the folder manually into your QGIS plugins directory:

- **Windows**: `%AppData%\Roaming\QGIS\QGIS3\profiles\default\python\plugins`
- **Linux**: `~/.local/share/QGIS/QGIS3/profiles\default\python\plugins`

---

## Dependencies

The plugin requires the following Python libraries:

- `pystac-client`
- `planetary-computer`
- `shapely`

On first use, the plugin will attempt to install any missing dependencies automatically. Manual installation should not be necessary, but if needed:

#### Windows (via OSGeo4W Shell)

Open the **OSGeo4W Shell** as Administrator and run:

```bash
python3 -m pip install pystac-client planetary-computer shapely
```

#### Linux (via Terminal)

```bash
pip install pystac-client planetary-computer shapely
```

---

## Plugin page

This plugin is available on the QGIS official plugin repository:
https://plugins.qgis.org/plugins/sentinel_stac_loader/

---

## License

This project is licensed under the [GNU General Public License v2]
