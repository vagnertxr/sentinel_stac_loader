# -*- coding: utf-8 -*-
import os
import pystac_client
import planetary_computer
from qgis.PyQt import uic, QtWidgets
from qgis.core import (
    QgsRasterLayer, QgsProject, QgsCoordinateTransform, 
    QgsCoordinateReferenceSystem, Qgis
)
from qgis.utils import iface
import processing

# Carrega o arquivo UI
FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'sentinel_stac_loader_dialog_base.ui'))

# ... (manter imports e FORM_CLASS iguais)

class SentinelSTACLoader:
    """Classe responsável apenas pela lógica de busca e processamento de dados."""
    def __init__(self):
        self.catalog_url = "https://planetarycomputer.microsoft.com/api/stac/v1"
        self.collection = "sentinel-2-l2a"
        self.compositions = {}

    def get_canvas_bbox(self):
        """Obtém a extensão atual do canvas em WGS84."""
        canvas = iface.mapCanvas()
        extent = canvas.extent()
        crs_src = canvas.mapSettings().destinationCrs()
        crs_dest = QgsCoordinateReferenceSystem("EPSG:4326")
        
        xform = QgsCoordinateTransform(crs_src, crs_dest, QgsProject.instance())
        
        p1 = xform.transform(extent.xMinimum(), extent.yMinimum())
        p2 = xform.transform(extent.xMaximum(), extent.yMaximum())
        
        return [p1.x(), p1.y(), p2.x(), p2.y()]

    def search_images(self, bbox, start_date, end_date):
        # ... continua o resto do código
        try:
            catalog = pystac_client.Client.open(self.catalog_url)
            search = catalog.search(
                collections=[self.collection],
                bbox=bbox,
                datetime=f"{start_date}/{end_date}"
            )
            items = list(search.get_all_items())
            # Ordena por nuvens
            return sorted(items, key=lambda x: x.properties.get("eo:cloud_cover", 100))
        except Exception as e:
            iface.messageBar().pushMessage("Erro STAC", str(e), Qgis.Critical)
            return []

    def load_vrt(self, item, composition_name):
        bands = self.compositions.get(composition_name)
        band_hrefs = []
        for band in bands:
            asset = item.assets.get(band)
            if asset:
                signed_href = planetary_computer.sign(asset.href)
                band_hrefs.append(f"/vsicurl/{signed_href}")

        if not band_hrefs: return False

        params = {'INPUT': band_hrefs, 'SEPARATE': True, 'OUTPUT': 'TEMPORARY_OUTPUT'}
        try:
            result = processing.run("gdal:buildvirtualraster", params)
            cloud_pct = item.properties.get("eo:cloud_cover", 0)
            
            # Identifica o satélite pelo ID do item para o nome da camada
            prefix = "S2" if "sentinel" in self.collection else "LS"
            layer_name = f"{prefix}_{item.id}_{composition_name} ({cloud_pct:.1f}% Nuvens)"
            
            vrt_layer = QgsRasterLayer(result['OUTPUT'], layer_name)
            if vrt_layer.isValid():
                QgsProject.instance().addMapLayer(vrt_layer)
                return True
        except Exception as e:
            iface.messageBar().pushMessage("Erro VRT", str(e), Qgis.Critical)
        return False

class SentinelSTACDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super(SentinelSTACDialog, self).__init__(parent)
        self.setupUi(self)
        self.loader = SentinelSTACLoader()
        
        self.comboBox_satelite.currentIndexChanged.connect(self.atualizar_parametros_satelite)
        self.tableWidget.cellClicked.connect(lambda row, col: self.spinBox_indice.setValue(row))

        self.atualizar_parametros_satelite()

    def atualizar_parametros_satelite(self):
        """Configura coleções e bandas para Sentinel-2, Landsat 8/9 e Landsat 5/7."""
        satelite = self.comboBox_satelite.currentText()
        
        if "Sentinel" in satelite:
            self.loader.collection = "sentinel-2-l2a"
            self.loader.compositions = {
                "True Color (B04, B03, B02)": ['B04', 'B03', 'B02'],
                "False Color NIR (B08, B04, B03)": ['B08', 'B04', 'B03'],
                "Agriculture (B11, B08, B02)": ['B11', 'B08', 'B02'],
                "Shortwave IR (B12, B08, B03)": ['B12', 'B08', 'B03']
            }
        elif "Landsat 8" in satelite:
            self.loader.collection = "landsat-c2-l2"
            self.loader.compositions = {
                "True Color (Red, Green, Blue)": ['red', 'green', 'blue'],
                "False Color NIR (NIR, Red, Green)": ['nir08', 'red', 'green'],
                "Agriculture (SWIR1, NIR, Blue)": ['swir16', 'nir08', 'blue']
            }
        elif "Landsat 5" in satelite or "Landsat 7" in satelite:
            # Landsat 4, 5 e 7 usam a mesma nomenclatura de bandas no PC
            self.loader.collection = "landsat-c2-l2"
            self.loader.compositions = {
                "True Color (Red, Green, Blue)": ['red', 'green', 'blue'],
                "False Color NIR (NIR, Red, Green)": ['nir', 'red', 'green'],
                "Agriculture (SWIR1, NIR, Blue)": ['swir1', 'nir', 'blue'],
                "Shortwave IR (SWIR2, NIR, Green)": ['swir2', 'nir', 'green']
            }
        
        self.comboBox_composicao.clear()
        self.comboBox_composicao.addItems(list(self.loader.compositions.keys()))

    # ... (manter as funções popular_tabela e process_stac_load iguais)

    def atualizar_indice_pelo_clique(self, row, column):
        """Atualiza o spinbox quando o usuário clica em uma linha da tabela."""
        self.spinBox_indice.setValue(row)

    def popular_tabela(self):
        """Busca as imagens e preenche o QTableWidget na interface."""
        data_inicio = self.dateEdit_inicio.date().toString("yyyy-MM-dd")
        data_final = self.dateEdit_final.date().toString("yyyy-MM-dd")
        
        bbox = self.loader.get_canvas_bbox()
        items = self.loader.search_images(bbox, data_inicio, data_final)
        
        if not items:
            iface.messageBar().pushMessage("STAC", "Nenhuma imagem encontrada.", Qgis.Warning)
            return

        self.tableWidget.setRowCount(0)
        for idx, item in enumerate(items):
            date_str = item.properties.get("datetime", "N/A")[:10]
            clouds = f"{item.properties.get('eo:cloud_cover', 0):.2f}%"
            img_id = item.id
            
            self.tableWidget.insertRow(idx)
            self.tableWidget.setItem(idx, 0, QtWidgets.QTableWidgetItem(str(idx)))
            self.tableWidget.setItem(idx, 1, QtWidgets.QTableWidgetItem(date_str))
            self.tableWidget.setItem(idx, 2, QtWidgets.QTableWidgetItem(clouds))
            self.tableWidget.setItem(idx, 3, QtWidgets.QTableWidgetItem(img_id))
            
        self.tableWidget.resizeColumnsToContents()
        iface.messageBar().pushMessage("Sucesso", f"{len(items)} imagens listadas.", Qgis.Info)

    def process_stac_load(self):
        """Executa o carregamento da imagem selecionada no SpinBox."""
        data_inicio = self.dateEdit_inicio.date().toString("yyyy-MM-dd")
        data_final = self.dateEdit_final.date().toString("yyyy-MM-dd")
        composicao = self.comboBox_composicao.currentText()
        indice = self.spinBox_indice.value()

        bbox = self.loader.get_canvas_bbox()
        items = self.loader.search_images(bbox, data_inicio, data_final)

        if not items or indice >= len(items):
            iface.messageBar().pushMessage("Erro", "Seleção inválida.", Qgis.Critical)
            return

        selected_item = items[indice]
        iface.messageBar().pushMessage("STAC", f"Carregando {self.loader.collection}...")
        self.loader.load_vrt(selected_item, composicao)