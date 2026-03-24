# -*- coding: utf-8 -*-
import os
from qgis.PyQt import uic, QtWidgets
from qgis.PyQt.QtCore import QThread, pyqtSignal, Qt, QCoreApplication
from qgis.PyQt.QtGui import QPixmap
from qgis.core import (
    QgsRasterLayer, QgsProject, QgsCoordinateTransform,
    QgsCoordinateReferenceSystem, Qgis
)
from qgis.utils import iface
import processing

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'sentinel_stac_loader_dialog_base.ui'))

try:
    _ml = Qgis.MessageLevel
    class MsgLevel:
        Info     = _ml.Info
        Warning  = _ml.Warning
        Critical = _ml.Critical
        Success  = _ml.Success
except AttributeError:
    class MsgLevel:
        Info     = Qgis.Info
        Warning  = Qgis.Warning
        Critical = Qgis.Critical
        Success  = Qgis.Success

try:
    _KeepAspectRatio = Qt.AspectRatioMode.KeepAspectRatio
    _SmoothTransform = Qt.TransformationMode.SmoothTransformation
except AttributeError:
    _KeepAspectRatio = Qt.KeepAspectRatio
    _SmoothTransform = Qt.SmoothTransformation


class SentinelSTACLoader:
    def __init__(self):
        self.catalog_url  = "https://planetarycomputer.microsoft.com/api/stac/v1"
        self.collection   = "sentinel-2-l2a"
        self.compositions = {}

    def get_canvas_bbox(self):
        canvas   = iface.mapCanvas()
        extent   = canvas.extent()
        crs_src  = canvas.mapSettings().destinationCrs()
        crs_dest = QgsCoordinateReferenceSystem("EPSG:4326")
        xform    = QgsCoordinateTransform(crs_src, crs_dest, QgsProject.instance())
        p1 = xform.transform(extent.xMinimum(), extent.yMinimum())
        p2 = xform.transform(extent.xMaximum(), extent.yMaximum())
        return [p1.x(), p1.y(), p2.x(), p2.y()]


class ThumbnailWorker(QThread):
    thumbnail_ready = pyqtSignal(QPixmap)
    failed          = pyqtSignal(str)

    def __init__(self, url, parent=None):
        super(ThumbnailWorker, self).__init__(parent)
        self.url = url

    def run(self):
        try:
            if not self.url.lower().startswith(('http://', 'https://')):
                self.failed.emit("Invalid URL")
                return

            import urllib.request
            req = urllib.request.Request(self.url, headers={"User-Agent": "QuickVRTImageryLoader/0.6"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()
                
            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                self.failed.emit("Could not decode image")
                return
            pixmap = pixmap.scaled(240, 240, _KeepAspectRatio, _SmoothTransform)
            self.thumbnail_ready.emit(pixmap)
        except Exception as e:
            self.failed.emit(str(e)[:60])

class SearchWorker(QThread):
    search_done  = pyqtSignal(list)
    search_error = pyqtSignal(str)

    def __init__(self, catalog_url, collection, bbox, start_date, end_date, max_clouds, parent=None):
        super(SearchWorker, self).__init__(parent)
        self.catalog_url = catalog_url
        self.collection  = collection
        self.bbox        = bbox
        self.start_date  = start_date
        self.end_date    = end_date
        self.max_clouds  = max_clouds

    def run(self):
        try:
            import pystac_client
            catalog = pystac_client.Client.open(self.catalog_url)
            search  = catalog.search(
                collections=[self.collection],
                bbox=self.bbox,
                datetime=f"{self.start_date}/{self.end_date}"
            )
            items = list(search.get_all_items())
            items = sorted(items, key=lambda x: x.properties.get("eo:cloud_cover", 100))
            items = [i for i in items if i.properties.get("eo:cloud_cover", 100) <= self.max_clouds]
            self.search_done.emit(items)
        except Exception as e:
            self.search_error.emit(str(e))

class VrtWorker(QThread):
    vrt_ready = pyqtSignal(str, str)
    vrt_error = pyqtSignal(str)

    def __init__(self, item, bands, collection, parent=None):
        super(VrtWorker, self).__init__(parent)
        self.item       = item
        self.bands      = bands
        self.collection = collection

    def run(self):
        try:
            import planetary_computer
            band_hrefs = []
            for band in self.bands:
                asset = self.item.assets.get(band)
                if asset:
                    signed_href = planetary_computer.sign(asset.href)
                    band_hrefs.append(f"/vsicurl/{signed_href}")
            if not band_hrefs:
                self.vrt_error.emit("No valid band assets found")
                return
            result = processing.run("gdal:buildvirtualraster", {
                'INPUT': band_hrefs, 'SEPARATE': True, 'OUTPUT': 'TEMPORARY_OUTPUT'
            })
            cloud_pct  = self.item.properties.get("eo:cloud_cover", 0)
            prefix     = "S2" if "sentinel" in self.collection else "LS"
            layer_name = f"{prefix}_{self.item.id}_({cloud_pct:.1f}% Clouds)"
            self.vrt_ready.emit(result['OUTPUT'], layer_name)
        except Exception as e:
            self.vrt_error.emit(str(e))


class SentinelSTACDialog(QtWidgets.QDialog, FORM_CLASS):

    def __init__(self, parent=None):
        super(SentinelSTACDialog, self).__init__(parent)
        self.setupUi(self)
        self.loader         = SentinelSTACLoader()
        self.last_items     = []
        self._thumb_worker  = None
        self._search_worker = None
        self._vrt_worker    = None

        self._retranslateUi()

        self.comboBox_satelite.currentIndexChanged.connect(self.atualizar_parametros_satelite)
        self.tableWidget.cellClicked.connect(self.atualizar_indice_pelo_clique)
        self.slider_clouds.valueChanged.connect(self._atualizar_label_clouds)

        self.atualizar_parametros_satelite()
        self._reset_thumbnail_panel()

    # Translation helper
    def tr(self, message):
        return QCoreApplication.translate('SentinelSTACDialogBase', message)

    def _retranslateUi(self):
        # Window title
        self.setWindowTitle(self.tr("Quick VRT Imagery Loader"))

        # Title label (HTML wrapper preserved, only the visible text is translated)
        self.label_title.setText(
            '<html><head/><body><p align="center">'
            '<span style=" font-size:15pt; font-weight:600;">'
            + self.tr("Quick VRT Imagery Loader") +
            '</span></p></body></html>'
        )

        # Subtitle
        self.label_subtitle.setText(
            '<html><head/><body><p align="center">'
            '<span style=" color:#686868;">'
            + self.tr("Select parameters for searching images in the current map extent.") +
            '</span></p></body></html>'
        )

        # Section header — IMAGE PARAMETERS
        self.label_section_params.setText(
            '<html><head/><body><p>'
            '<span style=" font-size:8pt; color:#888888; font-weight:600;'
            ' text-transform:uppercase; letter-spacing:1px;">'
            + self.tr("IMAGE PARAMETERS") +
            '</span></p></body></html>'
        )

        # Satellite label
        self.label_satellite.setText(
            '<html><head/><body><p>'
            '<span style=" font-size:10pt; font-weight:600;">'
            + self.tr("Satellite:") +
            '</span></p></body></html>'
        )

        # Composition label
        self.label_composition.setText(
            '<html><head/><body><p>'
            '<span style=" font-size:10pt; font-weight:600;">'
            + self.tr("Composition:") +
            '</span></p></body></html>'
        )

        # Start date label
        self.label_start_date.setText(
            '<html><head/><body><p>'
            '<span style=" font-size:10pt; font-weight:600;">'
            + self.tr("Start date:") +
            '</span></p></body></html>'
        )

        # End date label
        self.label_end_date.setText(
            '<html><head/><body><p>'
            '<span style=" font-size:10pt; font-weight:600;">'
            + self.tr("End date:") +
            '</span></p></body></html>'
        )

        # Max clouds label
        self.label_clouds.setText(
            '<html><head/><body><p>'
            '<span style=" font-size:10pt; font-weight:600;">'
            + self.tr("Max clouds:") +
            '</span></p></body></html>'
        )

        # Slider tooltip
        self.slider_clouds.setToolTip(
            self.tr("Filter images by maximum cloud cover percentage")
        )

        # List button
        self.btn_listar.setText(self.tr("List available images"))

        # Section header — RESULTS
        self.label_section_results.setText(
            '<html><head/><body><p>'
            '<span style=" font-size:8pt; color:#888888; font-weight:600;">'
            + self.tr("RESULTS") +
            '</span></p></body></html>'
        )

        # Table column headers
        self.tableWidget.horizontalHeaderItem(0).setText(self.tr("Index"))
        self.tableWidget.horizontalHeaderItem(1).setText(self.tr("Image date"))
        self.tableWidget.horizontalHeaderItem(2).setText(self.tr("Clouds (%)"))
        self.tableWidget.horizontalHeaderItem(3).setText(self.tr("ID"))

        # Section header — PREVIEW
        self.label_section_results_2.setText(
            '<html><head/><body><p>'
            '<span style=" font-size:8pt; font-weight:600; color:#888888;">'
            + self.tr("PREVIEW") +
            '</span></p></body></html>'
        )

        # Thumbnail placeholder (also reset by _reset_thumbnail_panel on init)
        self.lbl_thumbnail.setText(
            '<html><head/><body><p align="center">'
            '<span style=" color:#888888;">'
            + self.tr("Select an image to preview") +
            '</span></p></body></html>'
        )

        # Image ID tooltip and copy button tooltip
        self.lbl_thumb_id.setToolTip(
            self.tr("Full image ID — hover to read, click 📋 to copy")
        )
        self.btn_copy_id.setToolTip(
            self.tr("Copy image ID to clipboard")
        )

        # Choose image label
        self.label_choose.setText(
            '<html><head/><body><p>'
            '<span style=" font-size:10pt; font-weight:600;">'
            + self.tr("Choose image:") +
            '</span></p></body></html>'
        )

        # SpinBox tooltip
        self.spinBox_indice.setToolTip(
            self.tr("0 = Cleanest image, 1 = Second best, etc.")
        )

        # Hint label
        self.label_hint.setText(
            '<html><head/><body><p>'
            '<span style=" color:#888888; font-size:8pt; font-style:italic;">'
            + self.tr("Sorted by cloud cover, lowest to highest. Click a row to preview.") +
            '</span></p></body></html>'
        )

        # Load button
        self.btn_carregar.setText(self.tr("Load image"))

    # compositions

    def atualizar_parametros_satelite(self):
        satelite = self.comboBox_satelite.currentText()

        if "Sentinel" in satelite:
            self.loader.collection = "sentinel-2-l2a"
            self.loader.compositions = {
            "True Color (B04, B03, B02)":                     ['B04', 'B03', 'B02'],
            "False Color NIR (B08, B04, B03)":                ['B08', 'B04', 'B03'],
            "False Color SWIR (B12, B08, B04)":               ['B12', 'B08', 'B04'],
            "Agriculture (B11, B08, B02)":                    ['B11', 'B08', 'B02'],
            "Healthy Vegetation (B8A, B11, B02)":             ['B8A', 'B11', 'B02'],
            "Red Edge / Stress Vegetal (B08, B8A, B04)":      ['B08', 'B8A', 'B04'],
            "Vegetation Index / Biomass (B08, B11, B04)":     ['B08', 'B11', 'B04'],
            "Geology (B12, B11, B02)":                        ['B12', 'B11', 'B02'],
            "Urban / Soil (B12, B11, B04)":                   ['B12', 'B11', 'B04'],
            "Bathymetric (B04, B03, B01)":                    ['B04', 'B03', 'B01'],
            "Water Bodies (B03, B08, B11)":                   ['B03', 'B08', 'B11'],
            "Shortwave IR / Wildfires (B12, B08, B04)":       ['B12', 'B08', 'B04'],
            "Burn Area (B12, B8A, B04)":                      ['B12', 'B8A', 'B04'],
            "Atmospheric Penetration (B12, B11, B8A)":        ['B12', 'B11', 'B8A'],
            "Snow / Ice (B04, B03, B08)":                     ['B04', 'B03', 'B08'],
        }

        elif "Landsat" in satelite:
            self.loader.collection = "landsat-c2-l2"
            self.loader.compositions = {
            "True Color (R, G, B)":                           ['red',    'green',  'blue'],
            "False Color NIR (NIR, R, G)":                    ['nir08',  'red',    'green'],
            "Agriculture (SWIR1, NIR, B)":                    ['swir16', 'nir08',  'blue'],
            "Healthy Vegetation (NIR, SWIR1, R)":             ['nir08',  'swir16', 'red'],
            "Geology (SWIR2, SWIR1, B)":                      ['swir22', 'swir16', 'blue'],
            "Urban / Soil (SWIR2, SWIR1, R)":                 ['swir22', 'swir16', 'red'],
            "Bathymetric (G, R, Coastal)":                    ['green',  'red',    'coastal'],
            "Water Bodies (G, NIR, SWIR1)":                   ['green',  'nir08',  'swir16'],
            "Shortwave IR / Wildfires (SWIR2, NIR, R)":       ['swir22', 'nir08',  'red'],
            "Burn Area (SWIR2, SWIR1, NIR)":                  ['swir22', 'swir16', 'nir08'],
            "Atmospheric Penetration (SWIR2, SWIR1, NIR)":    ['swir22', 'swir16', 'nir08'],
            "Snow / Ice (R, G, NIR)":                         ['red',    'green',  'nir08'],
        }

        self.comboBox_composicao.clear()
        self.comboBox_composicao.addItems(list(self.loader.compositions.keys()))

    def _atualizar_label_clouds(self, value):
        self.label_clouds_value.setText(f"{value}%")

    def _set_ui_busy(self, busy, context="search"):
        self.btn_listar.setEnabled(not busy)
        self.btn_carregar.setEnabled(not busy)
        if context == "search":
            self.btn_listar.setText(
                self.tr("Searching…") if busy else self.tr("List available images")
            )
        elif context == "load":
            self.btn_carregar.setText(
                self.tr("Loading…") if busy else self.tr("Load image")
            )

    def atualizar_indice_pelo_clique(self, row, column):
        self.spinBox_indice.setValue(row)
        self._carregar_thumbnail(row)


    def _carregar_thumbnail(self, row):
        if row < 0 or row >= len(self.last_items): return
        item = self.last_items[row]
        self.lbl_thumb_date.setText(item.properties.get("datetime", "N/A")[:10])
        self.lbl_thumb_clouds.setText(f"☁ {item.properties.get('eo:cloud_cover', 0):.1f}%")
        self.lbl_thumb_id.setText(item.id)

        asset = item.assets.get('rendered_preview')
        if not asset:
            self.lbl_thumbnail.setText(self.tr("No preview available"))
            return

        self.lbl_thumbnail.setText(self.tr("Loading…"))
        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.terminate()
            self._thumb_worker.wait()
        self._thumb_worker = ThumbnailWorker(asset.href, parent=self)
        self._thumb_worker.thumbnail_ready.connect(self._exibir_thumbnail)
        self._thumb_worker.start()

    def _exibir_thumbnail(self, pixmap):
        self.lbl_thumbnail.setText("")
        self.lbl_thumbnail.setPixmap(pixmap)

    def popular_tabela(self):
        data_inicio = self.dateEdit_inicio.date().toString("yyyy-MM-dd")
        data_final  = self.dateEdit_final.date().toString("yyyy-MM-dd")
        bbox        = self.loader.get_canvas_bbox()
        self._set_ui_busy(True, "search")
        iface.mainWindow().statusBar().showMessage(
            self.tr("Searching images on Planetary Computer STAC API…")
        )
        self._search_worker = SearchWorker(
            self.loader.catalog_url, self.loader.collection,
            bbox, data_inicio, data_final, self.slider_clouds.value(),
            parent=self
        )
        self._search_worker.search_done.connect(self._on_search_done)
        self._search_worker.search_error.connect(self._on_search_error)
        self._search_worker.start()

    def _on_search_done(self, items):
        self._set_ui_busy(False, "search")
        iface.mainWindow().statusBar().clearMessage()
        self.last_items = items
        self.tableWidget.setRowCount(0)
        for idx, item in enumerate(items):
            self.tableWidget.insertRow(idx)
            self.tableWidget.setItem(idx, 0, QtWidgets.QTableWidgetItem(str(idx)))
            self.tableWidget.setItem(idx, 1, QtWidgets.QTableWidgetItem(
                item.properties.get("datetime", "N/A")[:10]))
            self.tableWidget.setItem(idx, 2, QtWidgets.QTableWidgetItem(
                f"{item.properties.get('eo:cloud_cover', 0):.2f}%"))
            self.tableWidget.setItem(idx, 3, QtWidgets.QTableWidgetItem(item.id))
        self.tableWidget.resizeColumnsToContents()

    def _on_search_error(self, error_msg):
        self._set_ui_busy(False, "search")
        iface.mainWindow().statusBar().clearMessage()
        iface.messageBar().pushMessage(
            self.tr("Search error"), error_msg,
            level=MsgLevel.Critical, duration=8
        )

    def process_stac_load(self):
        if not self.last_items: return
        indice = self.spinBox_indice.value()
        selected_item = self.last_items[indice]
        bands = self.loader.compositions.get(self.comboBox_composicao.currentText(), [])
        self._set_ui_busy(True, "load")
        self._vrt_worker = VrtWorker(
            selected_item, bands, self.loader.collection, parent=self
        )
        self._vrt_worker.vrt_ready.connect(self._on_vrt_ready)
        self._vrt_worker.vrt_error.connect(self._on_vrt_error)
        self._vrt_worker.start()

    def _on_vrt_ready(self, vrt_path, layer_name):
        self._set_ui_busy(False, "load")
        vrt_layer = QgsRasterLayer(vrt_path, layer_name)
        if vrt_layer.isValid():
            QgsProject.instance().addMapLayer(vrt_layer)

    def _on_vrt_error(self, error_msg):
        self._set_ui_busy(False, "load")
        iface.messageBar().pushMessage(
            self.tr("Load error"), error_msg,
            level=MsgLevel.Critical, duration=8
        )

    def _reset_thumbnail_panel(self):
        self.lbl_thumbnail.setText(self.tr("Select an image to preview"))
        self.lbl_thumbnail.setPixmap(QPixmap())
