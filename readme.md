# Quick VRT Imagery Loader
Version 0.5

A QGIS plugin designed for searching and loading satellite imagery via the **Microsoft Planetary Computer STAC API**. This tool optimizes the remote sensing workflow by providing pre-configured band compositions and loading imagery directly as Virtual Rasters (VRT).

Developed with Python and the QGIS Plugin Builder tool.

This plugin is available on the QGIS official plugin repository: https://plugins.qgis.org/plugins/sentinel_stac_loader/

## Installation

Download the `.zip` file from this repository.

You may use the Install from ZIP option on your QGIS plugin manager or Extract the folder into your QGIS plugins directory:
   - **Windows**: 
   
   `%AppData%\Roaming\QGIS\QGIS3\profiles\default\python\plugins`

   - **Linux**: 
   
   `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins`

## Dependencies

The plugin requires the following Python libraries installed within your QGIS environment:
- `pystac-client`
- `planetary-computer`
- `shapely`

A script was implemented to search and install dependencies automatically, so it is no longer necessary to install the dependencies manually. But, if needed, this is how to install them:

#### **Windows (via OSGeo4W Shell)**
Open the **OSGeo4W Shell** as Administrator and run:
```bash
python3 -m pip install pystac-client planetary-computer shapely
```

#### Linux (via Terminal)
Run the following command in your terminal:

```bash
pip install pystac-client planetary-computer shapely
```

## License

This project is licensed under the GNU General Public License v2.
