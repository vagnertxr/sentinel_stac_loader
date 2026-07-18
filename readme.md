# <img src="https://raw.githubusercontent.com/vagnertxr/quickvrt/main/icon.png" width="32" valign="middle"/> Quick VRT Imagery Loader

A QGIS plugin designed for searching and loading satellite imagery via multiple **STAC APIs**. This tool optimizes the remote sensing workflow by providing pre-configured band compositions and loading imagery directly as Virtual Rasters (VRT).

Supported STAC providers and collections:

- **Microsoft Planetary Computer**: Sentinel-2 L2A, Landsat Collection 2 Level-2
- **Element84 Earth Search (AWS)**: Sentinel-2 L2A — a free, unsigned alternative source, handy when Planetary Computer is unavailable
- **INPE / Brazil Data Cube**: CBERS-4/4A MUX, CBERS-4/4A WFI, CBERS-4A WPM (multispectral + panchromatic), CBERS-4A WPM Pansharpened True Color, CBERS-4 PAN10M, CBERS-4 PAN5M panchromatic

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
- **Linux**: `~/.local/share/QGIS/QGIS4/profiles/default/python/plugins`

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

This project is licensed under the GNU General Public License v2