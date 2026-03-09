# -*- coding: utf-8 -*-
import os
from qgis.PyQt import uic, QtWidgets
from qgis.PyQt.QtCore import QThread, pyqtSignal, Qt
from qgis.PyQt.QtGui import QPixmap
from qgis.core import (
    QgsRasterLayer, QgsProject, QgsCoordinateTransform,
    QgsCoordinateReferenceSystem, Qgis
)
from qgis.utils import iface
import processing

FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'sentinel_stac_loader_dialog_base.ui'))

# ── Enum compatibility: Qgis.MessageLevel (QGIS 4/Qt6) vs Qgis.* (QGIS 3/Qt5)
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

# ── Qt enum compatibility ─────────────────────────────────────────────────────
try:
    _KeepAspectRatio = Qt.AspectRatioMode.KeepAspectRatio       # Qt6
    _SmoothTransform = Qt.TransformationMode.SmoothTransformation
except AttributeError:
    _KeepAspectRatio = Qt.KeepAspectRatio                        # Qt5
    _SmoothTransform = Qt.SmoothTransformation


# ─────────────────────────────────────────────────────────────────────────────
# Worker: thumbnail download
# ─────────────────────────────────────────────────────────────────────────────
class ThumbnailWorker(QThread):
    """Downloads and scales a preview thumbnail in the background."""
    thumbnail_ready = pyqtSignal(QPixmap)
    failed          = pyqtSignal(str)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            import urllib.request
            req = urllib.request.Request(
                self.url,
                headers={"User-Agent": "QuickVRTImageryLoader/0.6"}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = resp.read()

            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                self.failed.emit("Could not decode image")
                return

            # Scale inside the thread — keeps the UI thread free
            pixmap = pixmap.scaled(240, 240, _KeepAspectRatio, _SmoothTransform)
            self.thumbnail_ready.emit(pixmap)

        except Exception as e:
            self.failed.emit(str(e)[:60])


# ─────────────────────────────────────────────────────────────────────────────
# Worker: STAC catalog search
# ─────────────────────────────────────────────────────────────────────────────
class SearchWorker(QThread):
    """Queries the STAC catalog in the background."""
    search_done  = pyqtSignal(list)
    search_error = pyqtSignal(str)

    def __init__(self, catalog_url, collection, bbox,
                 start_date, end_date, max_clouds, parent=None):
        super().__init__(parent)
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
            items = [i for i in items
                     if i.properties.get("eo:cloud_cover", 100) <= self.max_clouds]
            self.search_done.emit(items)
        except ImportError:
            self.search_error.emit("pystac-client library not found.")
        except Exception as e:
            self.search_error.emit(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Worker: URL signing + VRT build
# ─────────────────────────────────────────────────────────────────────────────
class VrtWorker(QThread):
    """Signs band URLs and builds the VRT file in the background.

    QgsRasterLayer and QgsProject.addMapLayer are NOT called here —
    QGIS objects must always be touched on the main thread.
    The worker only produces the VRT path and layer name, then emits them.
    """
    vrt_ready = pyqtSignal(str, str)   # (vrt_path, layer_name)
    vrt_error = pyqtSignal(str)

    def __init__(self, item, bands, collection, parent=None):
        super().__init__(parent)
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
                self.vrt_error.emit("No valid band assets found for this item.")
                return

            result     = processing.run("gdal:buildvirtualraster", {
                'INPUT':    band_hrefs,
                'SEPARATE': True,
                'OUTPUT':   'TEMPORARY_OUTPUT'
            })
            cloud_pct  = self.item.properties.get("eo:cloud_cover", 0)
            prefix     = "S2" if "sentinel" in self.collection else "LS"
            layer_name = (f"{prefix}_{self.item.id}_"
                          f"({cloud_pct:.1f}% Clouds)")

            self.vrt_ready.emit(result['OUTPUT'], layer_name)

        except ImportError:
            self.vrt_error.emit("planetary-computer library not found.")
        except Exception as e:
            self.vrt_error.emit(str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Data helper — only config and bbox, no network calls
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# Dialog
# ─────────────────────────────────────────────────────────────────────────────
class SentinelSTACDialog(QtWidgets.QDialog, FORM_CLASS):

    def __init__(self, parent=None):
        super(SentinelSTACDialog, self).__init__(parent)
        self.setupUi(self)
        self.loader         = SentinelSTACLoader()
        self.last_items     = []
        self._thumb_worker  = None
        self._search_worker = None
        self._vrt_worker    = None

        self.comboBox_satelite.currentIndexChanged.connect(self.atualizar_parametros_satelite)
        self.tableWidget.cellClicked.connect(self.atualizar_indice_pelo_clique)
        self.slider_clouds.valueChanged.connect(self._atualizar_label_clouds)

        self.atualizar_parametros_satelite()

    # ── Satellite / composition config ───────────────────────────────────────
    def atualizar_parametros_satelite(self):
        satelite = self.comboBox_satelite.currentText()

        if "Sentinel" in satelite:
            self.loader.collection  = "sentinel-2-l2a"
            self.loader.compositions = {
                "True Color (B04, B03, B02)":                 ['B04', 'B03', 'B02'],
                "False Color NIR (B08, B04, B03)":            ['B08', 'B04', 'B03'],
                "False Color SWIR (B12, B08, B04)":           ['B12', 'B08', 'B04'],
                "Agriculture (B11, B08, B02)":                ['B11', 'B08', 'B02'],
                "Geology (B12, B11, B02)":                    ['B12', 'B11', 'B02'],
                "Urban / Soil (B12, B11, B04)":               ['B12', 'B11', 'B04'],
                "Bathymetric (B04, B03, B01)":                ['B04', 'B03', 'B01'],
                "Atmospheric Penetration (B12, B11, B8A)":    ['B12', 'B11', 'B8A'],
                "Vegetation Index / Biomass (B08, B11, B04)": ['B08', 'B11', 'B04'],
                "Shortwave IR / Wildfires (B12, B08, B03)":   ['B12', 'B08', 'B03'],
            }
        elif "Landsat" in satelite:
            self.loader.collection  = "landsat-c2-l2"
            self.loader.compositions = {
                "True Color (R, G, B)":                       ['red',    'green',  'blue'],
                "False Color NIR (NIR, R, G)":                ['nir08',  'red',    'green'],
                "Agriculture (SWIR1, NIR, B)":                ['swir16', 'nir08',  'blue'],
                "Geology (SWIR2, SWIR1, B)":                  ['swir22', 'swir16', 'blue'],
                "Urban / Soil (SWIR2, SWIR1, R)":             ['swir22', 'swir16', 'red'],
                "Bathymetric (G, R, Coastal)":                ['green',  'red',    'coastal'],
                "Shortwave IR / Wildfires (SWIR2, NIR, G)":   ['swir22', 'nir08',  'green'],
                "Atmospheric Penetration (SWIR2, SWIR1, NIR)":['swir22', 'swir16', 'nir08'],
            }

        self.comboBox_composicao.clear()
        self.comboBox_composicao.addItems(list(self.loader.compositions.keys()))

    # ── Cloud slider label ────────────────────────────────────────────────────
    def _atualizar_label_clouds(self, value):
        self.label_clouds_value.setText(f"{value}%")

    # ── UI busy state ─────────────────────────────────────────────────────────
    def _set_ui_busy(self, busy, context="search"):
        """Disable controls and update button text while a worker is running."""
        self.btn_listar.setEnabled(not busy)
        self.btn_carregar.setEnabled(not busy)
        if context == "search":
            self.btn_listar.setText("Searching…" if busy else "List available images")
        elif context == "load":
            self.btn_carregar.setText("Loading…" if busy else "Load image")

    # ── Table row click ───────────────────────────────────────────────────────
    def atualizar_indice_pelo_clique(self, row, column):
        self.spinBox_indice.setValue(row)
        self._carregar_thumbnail(row)

    # ── Thumbnail ─────────────────────────────────────────────────────────────
    def _carregar_thumbnail(self, row):
        if row < 0 or row >= len(self.last_items):
            return

        item = self.last_items[row]

        date_str = item.properties.get("datetime", "N/A")[:10]
        clouds   = item.properties.get("eo:cloud_cover", 0)
        self.lbl_thumb_date.setText(date_str)
        self.lbl_thumb_clouds.setText(f"☁ {clouds:.1f}% cloud cover")
        self.lbl_thumb_id.setText(item.id)

        asset = item.assets.get('rendered_preview')
        if not asset:
            self.lbl_thumbnail.setText(
                "<html><body><p align='center'>"
                "<span style='color:#888888;'>No preview<br/>available</span>"
                "</p></body></html>")
            self.lbl_thumb_status.setText("")
            return

        if self._thumb_worker and self._thumb_worker.isRunning():
            self._thumb_worker.terminate()
            self._thumb_worker.wait()

        self.lbl_thumbnail.setText(
            "<html><body><p align='center'>"
            "<span style='color:#888888;'>Loading…</span>"
            "</p></body></html>")
        self.lbl_thumb_status.setText("Fetching preview…")

        self._thumb_worker = ThumbnailWorker(asset.href, parent=self)
        self._thumb_worker.thumbnail_ready.connect(self._exibir_thumbnail)
        self._thumb_worker.failed.connect(self._thumb_falhou)
        self._thumb_worker.start()

    def _exibir_thumbnail(self, pixmap):
        self.lbl_thumbnail.setText("")
        self.lbl_thumbnail.setPixmap(pixmap)
        self.lbl_thumb_status.setText("Preview loaded")

    def _thumb_falhou(self, reason):
        self.lbl_thumbnail.setText(
            "<html><body><p align='center'>"
            "<span style='color:#cc4444;'>Preview<br/>unavailable</span>"
            "</p></body></html>")
        self.lbl_thumb_status.setText(f"Error: {reason}")

    # ── Search ────────────────────────────────────────────────────────────────
    def popular_tabela(self):
        data_inicio = self.dateEdit_inicio.date().toString("yyyy-MM-dd")
        data_final  = self.dateEdit_final.date().toString("yyyy-MM-dd")
        max_clouds  = self.slider_clouds.value()
        bbox        = self.loader.get_canvas_bbox()

        self._set_ui_busy(True, context="search")
        self.tableWidget.setRowCount(0)
        self._reset_thumbnail_panel()
        iface.mainWindow().statusBar().showMessage(
            "Searching images on Planetary Computer STAC API…")

        self._search_worker = SearchWorker(
            self.loader.catalog_url, self.loader.collection,
            bbox, data_inicio, data_final, max_clouds,
            parent=self
        )
        self._search_worker.search_done.connect(self._on_search_done)
        self._search_worker.search_error.connect(self._on_search_error)
        self._search_worker.start()

    def _on_search_done(self, items):
        self._set_ui_busy(False, context="search")
        self.last_items = items
        iface.mainWindow().statusBar().clearMessage()

        if not items:
            iface.messageBar().pushMessage(
                "Quick VRT Imagery Loader",
                "No image found for selected parameters.",
                level=MsgLevel.Warning)
            return

        for idx, item in enumerate(items):
            date_str = item.properties.get("datetime", "N/A")[:10]
            clouds   = f"{item.properties.get('eo:cloud_cover', 0):.2f}%"
            self.tableWidget.insertRow(idx)
            self.tableWidget.setItem(idx, 0, QtWidgets.QTableWidgetItem(str(idx)))
            self.tableWidget.setItem(idx, 1, QtWidgets.QTableWidgetItem(date_str))
            self.tableWidget.setItem(idx, 2, QtWidgets.QTableWidgetItem(clouds))
            self.tableWidget.setItem(idx, 3, QtWidgets.QTableWidgetItem(item.id))

        self.tableWidget.resizeColumnsToContents()
        iface.messageBar().pushMessage(
            "Quick VRT Imagery Loader",
            f"{len(items)} images listed.",
            level=MsgLevel.Info)

    def _on_search_error(self, error_msg):
        self._set_ui_busy(False, context="search")
        iface.mainWindow().statusBar().clearMessage()
        iface.messageBar().pushMessage(
            "STAC error", error_msg, level=MsgLevel.Critical)

    # ── Load VRT ──────────────────────────────────────────────────────────────
    def process_stac_load(self):
        if not self.last_items:
            iface.messageBar().pushMessage(
                "Error",
                "Click 'List available images' before trying to load.",
                level=MsgLevel.Warning)
            return

        indice     = self.spinBox_indice.value()
        composicao = self.comboBox_composicao.currentText()

        if indice < 0 or indice >= len(self.last_items):
            iface.messageBar().pushMessage(
                "Error", "Invalid index number selected.",
                level=MsgLevel.Critical)
            return

        selected_item = self.last_items[indice]
        bands         = self.loader.compositions.get(composicao, [])

        self._set_ui_busy(True, context="load")
        iface.mainWindow().statusBar().showMessage(
            f"Building VRT for {selected_item.id}…")

        self._vrt_worker = VrtWorker(
            selected_item, bands, self.loader.collection, parent=self)
        self._vrt_worker.vrt_ready.connect(self._on_vrt_ready)
        self._vrt_worker.vrt_error.connect(self._on_vrt_error)
        self._vrt_worker.start()

    def _on_vrt_ready(self, vrt_path, layer_name):
        """Runs on the main thread — safe to touch QGIS objects here."""
        self._set_ui_busy(False, context="load")
        iface.mainWindow().statusBar().clearMessage()

        vrt_layer = QgsRasterLayer(vrt_path, layer_name)
        if vrt_layer.isValid():
            QgsProject.instance().addMapLayer(vrt_layer)
            iface.messageBar().pushMessage(
                "Quick VRT Imagery Loader", "Image loaded.",
                level=MsgLevel.Success)
        else:
            iface.messageBar().pushMessage(
                "VRT Error", "Layer is not valid after building VRT.",
                level=MsgLevel.Critical)

    def _on_vrt_error(self, error_msg):
        self._set_ui_busy(False, context="load")
        iface.mainWindow().statusBar().clearMessage()
        iface.messageBar().pushMessage(
            "VRT Error", error_msg, level=MsgLevel.Critical)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _reset_thumbnail_panel(self):
        self.lbl_thumbnail.setText(
            "<html><body><p align='center'>"
            "<span style='color:#888888;'>Select an image<br/>to preview</span>"
            "</p></body></html>")
        self.lbl_thumbnail.setPixmap(QPixmap())
        self.lbl_thumb_date.setText("")
        self.lbl_thumb_clouds.setText("")
        self.lbl_thumb_id.setText("")
        self.lbl_thumb_status.setText("")
