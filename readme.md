# <img src="https://raw.githubusercontent.com/vagnertxr/quickvrt/main/icon.png" width="32" valign="middle"/> Quick VRT Imagery Loader (quickvrt)

A QGIS plugin designed for searching and loading satellite imagery via multiple **STAC APIs**: the **Microsoft Planetary Computer** and **INPE's Brazil Data Cube**. This tool optimizes the remote sensing workflow by providing pre-configured band compositions and loading imagery directly as Virtual Rasters (VRT).

Supported STAC providers and collections:

- **Microsoft Planetary Computer**: Sentinel-2 L2A, Landsat Collection 2 Level-2
- **INPE / Brazil Data Cube (CBERS)**: CBERS-4/4A MUX, CBERS-4/4A WFI, CBERS-4A WPM (multispectral + panchromatic), CBERS-4A WPM Pansharpened True Color, CBERS-4 PAN10M, CBERS-4 PAN5M panchromatic

Each collection ships with its own set of pre-configured compositions (True Color, False Color Infrared, Agriculture, Geology, Urban, Bathymetric, Panchromatic, NDVI/NDWI/NDMI/EVI, and more where the sensor's bands support them).

Developed with Python and the QGIS Plugin Builder tool.

## Installation

### Via QGIS Plugin Manager *(recommended)*

1. Open QGIS and go to **Plugins > Manage and Install Plugins**
2. Click "All", then search for `Quick VRT Imagery Loader`
3. Click **Install Plugin**

### Via ZIP file

Download the `.zip` file from this repository (under "releases") and use the **Install from ZIP** option in the QGIS Plugin Manager, or extract the folder manually into your QGIS plugins directory:

- **Windows**: `%AppData%\Roaming\QGIS\QGIS4\profiles\default\python\plugins`
- **Linux**: `~/.local/share/QGIS/QGIS4/profiles\default\python\plugins`

---

## Dependencies

The plugin requires the following Python libraries:

- `pystac-client`
- `planetary-computer`
- `shapely`

`planetary-computer` is only used when querying Microsoft's Planetary Computer STAC; INPE's Brazil Data Cube STAC serves public, unsigned assets.

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

*(the listing above was published under the plugin's previous technical name, `sentinel_stac_loader`; it will move to `quickvrt` once the QGIS plugin repository listing is updated separately)*

---

## License

This project is licensed under the [GNU General Public License v2]
