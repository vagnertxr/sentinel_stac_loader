# -*- coding: utf-8 -*-
import os
from qgis.PyQt import uic, QtWidgets
from qgis.core import (
    QgsRasterLayer, QgsProject, QgsCoordinateTransform, 
    QgsCoordinateReferenceSystem, Qgis
)
from qgis.utils import iface
import processing

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'sentinel_stac_loader_dialog_base.ui'))

class SentinelSTACLoader:
    """Data processing class"""
    
    def __init__(self):
        self.catalog_url = "https://planetarycomputer.microsoft.com/api/stac/v1"
        self.collection = "sentinel-2-l2a"
        self.compositions = {}

    def get_canvas_bbox(self):
        """Returns canvas bounding box in WGS-84."""
        canvas = iface.mapCanvas()
        extent = canvas.extent()
        crs_src = canvas.mapSettings().destinationCrs()
        crs_dest = QgsCoordinateReferenceSystem("EPSG:4326")
        
        xform = QgsCoordinateTransform(crs_src, crs_dest, QgsProject.instance())
        
        p1 = xform.transform(extent.xMinimum(), extent.yMinimum())
        p2 = xform.transform(extent.xMaximum(), extent.yMaximum())
        
        return [p1.x(), p1.y(), p2.x(), p2.y()]

    def search_images(self, bbox, start_date, end_date):
        """Lazy Import."""
        try:
            import pystac_client
            
            catalog = pystac_client.Client.open(self.catalog_url)
            search = catalog.search(
                collections=[self.collection],
                bbox=bbox,
                datetime=f"{start_date}/{end_date}"
            )
            items = list(search.get_all_items())
            return sorted(items, key=lambda x: x.properties.get("eo:cloud_cover", 100))
        except ImportError:
            iface.messageBar().pushMessage("Error", "pystac-client library not found.", Qgis.Critical)
            return []
        except Exception as e:
            iface.messageBar().pushMessage("STAC error", str(e), Qgis.Critical)
            return []

    def load_vrt(self, item, composition_name):
        """Generates VRT and loads on QGIS."""
        try:
            # LAZY IMPORT
            import planetary_computer
            
            bands = self.compositions.get(composition_name)
            band_hrefs = []
            
            for band in bands:
                asset = item.assets.get(band)
                if asset:
                    signed_href = planetary_computer.sign(asset.href)
                    band_hrefs.append(f"/vsicurl/{signed_href}")

            if not band_hrefs: 
                return False

            params = {
                'INPUT': band_hrefs, 
                'SEPARATE': True, 
                'OUTPUT': 'TEMPORARY_OUTPUT'
            }
            
            result = processing.run("gdal:buildvirtualraster", params)
            cloud_pct = item.properties.get("eo:cloud_cover", 0)
            
            prefix = "S2" if "sentinel" in self.collection else "LS"
            layer_name = f"{prefix}_{item.id}_{composition_name} ({cloud_pct:.1f}% Nuvens)"
            
            vrt_layer = QgsRasterLayer(result['OUTPUT'], layer_name)
            if vrt_layer.isValid():
                QgsProject.instance().addMapLayer(vrt_layer)
                return True
        except ImportError:
            iface.messageBar().pushMessage("Error", "planetary-computer library not found.", Qgis.Critical)
        except Exception as e:
            iface.messageBar().pushMessage("VRT Error", str(e), Qgis.Critical)
        return False

class SentinelSTACDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super(SentinelSTACDialog, self).__init__(parent)
        self.setupUi(self)
        self.loader = SentinelSTACLoader()
        self.last_items = []
        self.comboBox_satelite.currentIndexChanged.connect(self.atualizar_parametros_satelite)
        self.tableWidget.cellClicked.connect(self.atualizar_indice_pelo_clique)
        self.atualizar_parametros_satelite()

    def atualizar_parametros_satelite(self):
        """Configures collections and compositions for each satellite type."""
        satelite = self.comboBox_satelite.currentText()
        
        if "Sentinel" in satelite:
                    self.loader.collection = "sentinel-2-l2a"
                    self.loader.compositions = {
                        "True Color (B04, B03, B02)": ['B04', 'B03', 'B02'],
                        "False Color NIR (B08, B04, B03)": ['B08', 'B04', 'B03'],
                        "False Color SWIR (B12, B08, B04)": ['B12', 'B08', 'B04'],
                        "Agriculture (B11, B08, B02)": ['B11', 'B08', 'B02'],
                        "Geology (B12, B11, B02)": ['B12', 'B11', 'B02'],
                        "Urban / Soil (B12, B11, B04)": ['B12', 'B11', 'B04'],
                        "Bathymetric (B04, B03, B01)": ['B04', 'B03', 'B01'],
                        "Atmospheric Penetration (B12, B11, B8A)": ['B12', 'B11', 'B8A'],
                        "Vegetation Index / Biomass (B08, B11, B04)": ['B08', 'B11', 'B04'],
                        "Shortwave IR / Wildfires (B12, B08, B03)": ['B12', 'B08', 'B03']
                    }
        
        elif "Landsat" in satelite:
                    self.loader.collection = "landsat-c2-l2"
                    self.loader.compositions = {
                        "True Color (R, G, B)": ['red', 'green', 'blue'],
                        "False Color NIR (NIR, R, G)": ['nir08', 'red', 'green'],
                        "Agriculture (SWIR1, NIR, B)": ['swir16', 'nir08', 'blue'],
                        "Geology (SWIR2, SWIR1, B)": ['swir22', 'swir16', 'blue'],
                        "Urban / Soil (SWIR2, SWIR1, R)": ['swir22', 'swir16', 'red'],
                        "Bathymetric (G, R, Coastal)": ['green', 'red', 'coastal'],
                        "Shortwave IR / Wildfires (SWIR2, NIR, G)": ['swir22', 'nir08', 'green'],
                        "Atmospheric Penetration (SWIR2, SWIR1, NIR)": ['swir22', 'swir16', 'nir08']
                    }

                    
        self.comboBox_composicao.clear()
        self.comboBox_composicao.addItems(list(self.loader.compositions.keys()))

    def atualizar_indice_pelo_clique(self, row, column):
        """Updates the spinbox value when the user clicks a row in the table."""
        self.spinBox_indice.setValue(row)

    def popular_tabela(self):
        """Searches for STAC items and populates the table widget."""
        data_inicio = self.dateEdit_inicio.date().toString("yyyy-MM-dd")
        data_final = self.dateEdit_final.date().toString("yyyy-MM-dd")
        
        bbox = self.loader.get_canvas_bbox()
        
        iface.mainWindow().statusBar().showMessage("Searching images on Planetary Computer STAC API...")
        
        self.last_items = self.loader.search_images(bbox, data_inicio, data_final)
        
        if not self.last_items:
            iface.messageBar().pushMessage("Quick VRT Imagery Loader", "No image found for selected parameters.", Qgis.Warning)
            self.tableWidget.setRowCount(0)
            return

        self.tableWidget.setRowCount(0)
        for idx, item in enumerate(self.last_items):
            date_str = item.properties.get("datetime", "N/A")[:10]
            clouds = f"{item.properties.get('eo:cloud_cover', 0):.2f}%"
            img_id = item.id
            
            self.tableWidget.insertRow(idx)
            self.tableWidget.setItem(idx, 0, QtWidgets.QTableWidgetItem(str(idx)))
            self.tableWidget.setItem(idx, 1, QtWidgets.QTableWidgetItem(date_str))
            self.tableWidget.setItem(idx, 2, QtWidgets.QTableWidgetItem(clouds))
            self.tableWidget.setItem(idx, 3, QtWidgets.QTableWidgetItem(img_id))
            
        self.tableWidget.resizeColumnsToContents()
        iface.messageBar().pushMessage("Quick VRT Imagery Loader", f"{len(self.last_items)} images listed.", Qgis.Info)

    def process_stac_load(self):
        """Loads image from local cache (last_items)."""
        if not self.last_items:
            iface.messageBar().pushMessage("Error", "Click 'List available images' before trying to load.", Qgis.Warning)
            return

        indice = self.spinBox_indice.value()
        composicao = self.comboBox_composicao.currentText()

        if indice < 0 or indice >= len(self.last_items):
            iface.messageBar().pushMessage("Error", "Invalid index number selected.", Qgis.Critical)
            return

        selected_item = self.last_items[indice]
        iface.messageBar().pushMessage("Quick VRT Imagery Loader", f"Loading {selected_item.id}...", Qgis.Info)
        
        success = self.loader.load_vrt(selected_item, composicao)
        if success:
            iface.messageBar().pushMessage("Quick VRT Imagery Loader", "Image loaded.", Qgis.Success)
