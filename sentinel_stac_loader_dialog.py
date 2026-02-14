# -*- coding: utf-8 -*-
import os
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

class SentinelSTACLoader:
    """Classe responsável pelo processamento de dados e chamadas STAC"""
    
    def __init__(self):
        self.catalog_url = "https://planetarycomputer.microsoft.com/api/stac/v1"
        self.collection = "sentinel-2-l2a"
        self.compositions = {}

    def get_canvas_bbox(self):
        """Retorna o Bounding Box do Canvas em WGS-84."""
        canvas = iface.mapCanvas()
        extent = canvas.extent()
        crs_src = canvas.mapSettings().destinationCrs()
        crs_dest = QgsCoordinateReferenceSystem("EPSG:4326")
        
        xform = QgsCoordinateTransform(crs_src, crs_dest, QgsProject.instance())
        
        p1 = xform.transform(extent.xMinimum(), extent.yMinimum())
        p2 = xform.transform(extent.xMaximum(), extent.yMaximum())
        
        return [p1.x(), p1.y(), p2.x(), p2.y()]

    def search_images(self, bbox, start_date, end_date):
        """Busca imagens no STAC com Lazy Import."""
        try:
            # LAZY IMPORT: Só importa quando a função é chamada
            import pystac_client
            
            catalog = pystac_client.Client.open(self.catalog_url)
            search = catalog.search(
                collections=[self.collection],
                bbox=bbox,
                datetime=f"{start_date}/{end_date}"
            )
            items = list(search.get_all_items())
            # Ordena por cobertura de nuvens
            return sorted(items, key=lambda x: x.properties.get("eo:cloud_cover", 100))
        except ImportError:
            iface.messageBar().pushMessage("Erro", "Biblioteca pystac-client não encontrada.", Qgis.Critical)
            return []
        except Exception as e:
            iface.messageBar().pushMessage("Erro STAC", str(e), Qgis.Critical)
            return []

    def load_vrt(self, item, composition_name):
        """Gera o VRT e carrega no QGIS."""
        try:
            # LAZY IMPORT
            import planetary_computer
            
            bands = self.compositions.get(composition_name)
            band_hrefs = []
            
            for band in bands:
                asset = item.assets.get(band)
                if asset:
                    # Assina a URL para permitir o download/acesso
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
            iface.messageBar().pushMessage("Erro", "Biblioteca planetary-computer não encontrada.", Qgis.Critical)
        except Exception as e:
            iface.messageBar().pushMessage("Erro VRT", str(e), Qgis.Critical)
        return False

class SentinelSTACDialog(QtWidgets.QDialog, FORM_CLASS):
    def __init__(self, parent=None):
        super(SentinelSTACDialog, self).__init__(parent)
        self.setupUi(self)
        self.loader = SentinelSTACLoader()
        
        # Armazena os itens da última busca para não precisar consultar a API de novo ao carregar
        self.last_items = []
        
        # Conexões
        self.comboBox_satelite.currentIndexChanged.connect(self.atualizar_parametros_satelite)
        self.tableWidget.cellClicked.connect(self.atualizar_indice_pelo_clique)

        self.atualizar_parametros_satelite()

    def atualizar_parametros_satelite(self):
        """Configura coleções e bandas conforme o satélite selecionado."""
        satelite = self.comboBox_satelite.currentText()
        
        if "Sentinel" in satelite:
            self.loader.collection = "sentinel-2-l2a"
            self.loader.compositions = {
                "True Color (B04, B03, B02)": ['B04', 'B03', 'B02'],
                "False Color NIR (B08, B04, B03)": ['B08', 'B04', 'B03'],
                "False Color SWIR (B12, B08, B04)": ['B12', 'B08', 'B04'],
                "Agriculture (B11, B08, B02)": ['B11', 'B08', 'B02'],
                "Geology (B12, B11, B02)": ['B12', 'B11', 'B02'],
                "Urban / Soil (B12, B11, B04)": ['B12', 'B11', 'B04']
            }
        elif "Landsat" in satelite:
            self.loader.collection = "landsat-c2-l2"
            self.loader.compositions = {
                "True Color (R, G, B)": ['red', 'green', 'blue'],
                "False Color NIR (NIR, R, G)": ['nir08', 'red', 'green'],
                "Agriculture (SWIR1, NIR, B)": ['swir16', 'nir08', 'blue']
            }
                    
        self.comboBox_composicao.clear()
        self.comboBox_composicao.addItems(list(self.loader.compositions.keys()))

    def atualizar_indice_pelo_clique(self, row, column):
        """Sincroniza o SpinBox com a linha clicada na tabela."""
        self.spinBox_indice.setValue(row)

    def popular_tabela(self):
        """Faz a busca e preenche a tabela."""
        data_inicio = self.dateEdit_inicio.date().toString("yyyy-MM-dd")
        data_final = self.dateEdit_final.date().toString("yyyy-MM-dd")
        
        bbox = self.loader.get_canvas_bbox()
        
        # Feedback visual
        iface.mainWindow().statusBar().showMessage("Buscando imagens na API STAC...")
        
        self.last_items = self.loader.search_images(bbox, data_inicio, data_final)
        
        if not self.last_items:
            iface.messageBar().pushMessage("Aviso", "Nenhuma imagem encontrada para os parâmetros.", Qgis.Warning)
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
        iface.messageBar().pushMessage("Sucesso", f"{len(self.last_items)} imagens encontradas.", Qgis.Info)

    def process_stac_load(self):
        """Carrega a imagem selecionada usando o cache local (last_items)."""
        if not self.last_items:
            iface.messageBar().pushMessage("Erro", "Primeiro clique em 'Listar Imagens'.", Qgis.Warning)
            return

        indice = self.spinBox_indice.value()
        composicao = self.comboBox_composicao.currentText()

        if indice < 0 or indice >= len(self.last_items):
            iface.messageBar().pushMessage("Erro", "Índice selecionado inválido.", Qgis.Critical)
            return

        selected_item = self.last_items[indice]
        iface.messageBar().pushMessage("Quick VRT", f"Gerando VRT para {selected_item.id}...", Qgis.Info)
        
        success = self.loader.load_vrt(selected_item, composicao)
        if success:
            iface.messageBar().pushMessage("Sucesso", "Camada adicionada ao projeto.", Qgis.Success)