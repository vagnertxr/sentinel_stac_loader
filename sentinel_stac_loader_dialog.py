# -*- coding: utf-8 -*-
import os
from qgis.PyQt import uic, QtWidgets, QtCore
from qgis.PyQt.QtCore import QThread, pyqtSignal, Qt, QCoreApplication, QSize
from qgis.PyQt.QtGui import QPixmap, QFont, QIcon
from qgis.core import (
    QgsRasterLayer, QgsProject, QgsCoordinateTransform,
    QgsCoordinateReferenceSystem, Qgis, QgsMessageLog
)
from qgis.utils import iface
import processing
from datetime import date, timedelta
from .mosaic_worker import MosaicWorker

# ── Compatibility Qt5 (QGIS 3.x) / Qt6 (QGIS 4.x) ──────────────────────
import qgis.PyQt.QtCore as _qc
_QT_VERSION = [int(x) for x in _qc.qVersion().split(".")]
_QT6 = _QT_VERSION[0] >= 6

if _QT6:
    _Qt_AlignRight = Qt.AlignmentFlag.AlignRight
    _Qt_AlignCenter = Qt.AlignmentFlag.AlignCenter
    _Qt_Horizontal = Qt.Orientation.Horizontal
    _Qt_Vertical = Qt.Orientation.Vertical
else:
    _Qt_AlignRight = Qt.AlignRight  # type: ignore[attr-defined]
    _Qt_AlignCenter = Qt.AlignCenter  # type: ignore[attr-defined]
    _Qt_Horizontal = Qt.Horizontal  # type: ignore[attr-defined]
    _Qt_Vertical = Qt.Vertical  # type: ignore[attr-defined]

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
        xform    = QgsCoordinateTransform(crs_src, cr_dest, QgsProject.instance())
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
            pixmap = pixmap.scaled(320, 320, _KeepAspectRatio, _SmoothTransform)
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


class SentinelSTACDialog(QtWidgets.QDialog):

    def __init__(self, parent=None):
        super(SentinelSTACDialog, self).__init__(parent)
        self.loader         = SentinelSTACLoader()
        self.last_items     = []
        self._thumb_worker  = None
        self._search_worker = None
        self._vrt_worker    = None
        self._mosaic_worker = None

        self._setup_ui()
        self._retranslateUi()

        # Connect signals
        self.comboBox_satelite.currentIndexChanged.connect(self.atualizar_parametros_satelite)
        self.tableWidget.cellClicked.connect(self.atualizar_indice_pelo_clique)
        self.slider_clouds.valueChanged.connect(self._atualizar_label_clouds)
        
        # Initial states
        self.atualizar_parametros_satelite()
        self._reset_thumbnail_panel()
        self._load_extent()

    def tr(self, message):
        return QCoreApplication.translate('SentinelSTACDialogBase', message)

    def _setup_ui(self):
        self.setObjectName("SentinelSTACDialogBase")
        self.resize(950, 850)
        self.setMinimumSize(QSize(850, 750))
        
        self.main_layout = QtWidgets.QVBoxLayout(self)
        self.main_layout.setContentsMargins(15, 15, 15, 15)
        self.main_layout.setSpacing(10)

        # ── Header ──
        header_layout = QtWidgets.QVBoxLayout()
        self.label_title = QtWidgets.QLabel()
        font_title = QFont()
        font_title.setPointSize(16)
        font_title.setBold(True)
        self.label_title.setFont(font_title)
        self.label_title.setAlignment(_Qt_AlignCenter)
        header_layout.addWidget(self.label_title)

        self.label_subtitle = QtWidgets.QLabel()
        self.label_subtitle.setStyleSheet("color: #686868;")
        self.label_subtitle.setAlignment(_Qt_AlignCenter)
        header_layout.addWidget(self.label_subtitle)
        self.main_layout.addLayout(header_layout)

        # ── Global Parameters Group ──
        self.grp_params = QtWidgets.QGroupBox()
        params_grid = QtWidgets.QGridLayout(self.grp_params)

        self.lbl_sat = QtWidgets.QLabel()
        params_grid.addWidget(self.lbl_sat, 0, 0)
        self.comboBox_satelite = QtWidgets.QComboBox()
        self.comboBox_satelite.addItems(["Sentinel-2", "Landsat Collection 2 Level-2"])
        params_grid.addWidget(self.comboBox_satelite, 0, 1)

        self.lbl_comp = QtWidgets.QLabel()
        params_grid.addWidget(self.lbl_comp, 0, 2)
        self.comboBox_composicao = QtWidgets.QComboBox()
        self.comboBox_composicao.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)
        params_grid.addWidget(self.comboBox_composicao, 0, 3)

        date_layout = QtWidgets.QHBoxLayout()
        self.lbl_period = QtWidgets.QLabel()
        params_grid.addWidget(self.lbl_period, 1, 0)
        self.dateEdit_inicio = QtWidgets.QDateEdit(date.today() - timedelta(days=30))
        self.dateEdit_inicio.setCalendarPopup(True)
        self.dateEdit_final = QtWidgets.QDateEdit(date.today())
        self.dateEdit_final.setCalendarPopup(True)
        date_layout.addWidget(self.dateEdit_inicio)
        self.lbl_to = QtWidgets.QLabel()
        date_layout.addWidget(self.lbl_to)
        date_layout.addWidget(self.dateEdit_final)
        params_grid.addLayout(date_layout, 1, 1)

        cloud_layout = QtWidgets.QHBoxLayout()
        self.lbl_max_clouds = QtWidgets.QLabel()
        params_grid.addWidget(self.lbl_max_clouds, 1, 2)
        self.slider_clouds = QtWidgets.QSlider(_Qt_Horizontal)
        self.slider_clouds.setRange(0, 100)
        self.slider_clouds.setValue(20)
        self.label_clouds_value = QtWidgets.QLabel("20%")
        cloud_layout.addWidget(self.slider_clouds)
        cloud_layout.addWidget(self.label_clouds_value)
        params_grid.addLayout(cloud_layout, 1, 3)

        self.main_layout.addWidget(self.grp_params)

        # ── Search Area Group ──
        self.grp_bbox = QtWidgets.QGroupBox()
        bbox_h_layout = QtWidgets.QHBoxLayout(self.grp_bbox)
        self.sp_west = QtWidgets.QDoubleSpinBox()
        self.sp_south = QtWidgets.QDoubleSpinBox()
        self.sp_east = QtWidgets.QDoubleSpinBox()
        self.sp_north = QtWidgets.QDoubleSpinBox()
        for sp in [self.sp_west, self.sp_south, self.sp_east, self.sp_north]:
            sp.setRange(-180, 180)
            sp.setDecimals(4)
            sp.setSingleStep(0.1)
        self.sp_south.setRange(-90, 90)
        self.sp_north.setRange(-90, 90)
        
        bbox_h_layout.addWidget(QtWidgets.QLabel("W:"))
        bbox_h_layout.addWidget(self.sp_west)
        bbox_h_layout.addWidget(QtWidgets.QLabel("S:"))
        bbox_h_layout.addWidget(self.sp_south)
        bbox_h_layout.addWidget(QtWidgets.QLabel("E:"))
        bbox_h_layout.addWidget(self.sp_east)
        bbox_h_layout.addWidget(QtWidgets.QLabel("N:"))
        bbox_h_layout.addWidget(self.sp_north)
        
        self.btn_use_extent = QtWidgets.QPushButton()
        self.btn_use_extent.clicked.connect(self._load_extent)
        bbox_h_layout.addWidget(self.btn_use_extent)
        self.main_layout.addWidget(self.grp_bbox)

        # ── Tabs ──
        self.tabs = QtWidgets.QTabWidget()
        
        # --- Tab 1: Browser ---
        self.tab_browser = QtWidgets.QWidget()
        browser_layout = QtWidgets.QVBoxLayout(self.tab_browser)
        
        self.btn_listar = QtWidgets.QPushButton()
        self.btn_listar.setStyleSheet("font-weight: bold; padding: 5px;")
        self.btn_listar.clicked.connect(self.popular_tabela)
        browser_layout.addWidget(self.btn_listar)

        self.browser_splitter = QtWidgets.QSplitter(_Qt_Horizontal)
        self.tableWidget = QtWidgets.QTableWidget()
        self.tableWidget.setColumnCount(4)
        self.tableWidget.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.tableWidget.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tableWidget.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.browser_splitter.addWidget(self.tableWidget)
        
        self.preview_panel = QtWidgets.QFrame()
        self.preview_panel.setFrameShape(QtWidgets.QFrame.Shape.StyledPanel)
        preview_v_layout = QtWidgets.QVBoxLayout(self.preview_panel)
        self.lbl_thumbnail = QtWidgets.QLabel()
        self.lbl_thumbnail.setAlignment(_Qt_AlignCenter)
        self.lbl_thumbnail.setMinimumSize(240, 240)
        self.lbl_thumbnail.setStyleSheet("color: #888888; border: 1px solid #ddd; border-radius: 4px;")
        preview_v_layout.addWidget(self.lbl_thumbnail)
        
        self.lbl_thumb_date = QtWidgets.QLabel()
        self.lbl_thumb_date.setAlignment(_Qt_AlignCenter)
        self.lbl_thumb_clouds = QtWidgets.QLabel()
        self.lbl_thumb_clouds.setAlignment(_Qt_AlignCenter)
        self.lbl_thumb_id = QtWidgets.QLabel()
        self.lbl_thumb_id.setWordWrap(True)
        self.lbl_thumb_id.setStyleSheet("font-family: monospace; font-size: 9px;")
        
        preview_v_layout.addWidget(self.lbl_thumb_date)
        preview_v_layout.addWidget(self.lbl_thumb_clouds)
        preview_v_layout.addWidget(self.lbl_thumb_id)
        
        self.btn_copy_id = QtWidgets.QPushButton()
        self.btn_copy_id.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.lbl_thumb_id.text()))
        preview_v_layout.addWidget(self.btn_copy_id)
        
        self.browser_splitter.addWidget(self.preview_panel)
        self.browser_splitter.setStretchFactor(0, 3)
        self.browser_splitter.setStretchFactor(1, 1)
        browser_layout.addWidget(self.browser_splitter)
        
        self.grp_export_browser = QtWidgets.QGroupBox()
        self.grp_export_browser.setCheckable(True)
        self.grp_export_browser.setChecked(False)
        eb_layout = QtWidgets.QHBoxLayout(self.grp_export_browser)
        
        self.lbl_tif_file_browser = QtWidgets.QLabel()
        eb_layout.addWidget(self.lbl_tif_file_browser)
        self.le_tif_browser = QtWidgets.QLineEdit()
        eb_layout.addWidget(self.le_tif_browser)
        self.btn_browse_tif_browser = QtWidgets.QPushButton("…")
        self.btn_browse_tif_browser.setFixedWidth(30)
        self.btn_browse_tif_browser.clicked.connect(lambda: self._browse_tif(self.le_tif_browser))
        eb_layout.addWidget(self.btn_browse_tif_browser)
        
        self.lbl_compress_browser = QtWidgets.QLabel()
        eb_layout.addWidget(self.lbl_compress_browser)
        self.cb_compress_browser = QtWidgets.QComboBox()
        self.cb_compress_browser.addItems(["DEFLATE", "LZW", "ZSTD", "NONE"])
        eb_layout.addWidget(self.cb_compress_browser)
        
        browser_layout.addWidget(self.grp_export_browser)

        load_btn_layout = QtWidgets.QHBoxLayout()
        self.btn_carregar = QtWidgets.QPushButton()
        self.btn_carregar.setStyleSheet("font-weight: bold; height: 35px;")
        self.btn_carregar.clicked.connect(self.process_stac_load)
        load_btn_layout.addWidget(self.btn_carregar, 2)
        
        self.btn_mosaic_selected = QtWidgets.QPushButton()
        self.btn_mosaic_selected.setStyleSheet("font-weight: bold; height: 35px;")
        self.btn_mosaic_selected.clicked.connect(self._run_mosaic_selected)
        load_btn_layout.addWidget(self.btn_mosaic_selected, 1)
        
        browser_layout.addLayout(load_btn_layout)
        self.tabs.addTab(self.tab_browser, "Browser")

        # --- Tab 2: Auto-Mosaic ---
        self.tab_mosaic = QtWidgets.QWidget()
        mosaic_layout = QtWidgets.QVBoxLayout(self.tab_mosaic)
        
        self.grp_mosaic_opt = QtWidgets.QGroupBox()
        m_grid = QtWidgets.QGridLayout(self.grp_mosaic_opt)
        
        self.lbl_max_scenes = QtWidgets.QLabel()
        m_grid.addWidget(self.lbl_max_scenes, 0, 0)
        self.sp_items = QtWidgets.QSpinBox()
        self.sp_items.setRange(1, 100)
        self.sp_items.setValue(20)
        m_grid.addWidget(self.sp_items, 0, 1)
        
        self.lbl_preference = QtWidgets.QLabel()
        m_grid.addWidget(self.lbl_preference, 0, 2)
        self.cb_preference = QtWidgets.QComboBox()
        self.cb_preference.addItems([self.tr("Least Clouds"), self.tr("Most Recent")])
        m_grid.addWidget(self.cb_preference, 0, 3)
        
        self.chk_export_tif = QtWidgets.QCheckBox()
        m_grid.addWidget(self.chk_export_tif, 1, 0, 1, 4)
        
        export_row_layout = QtWidgets.QHBoxLayout()
        self.lbl_tif_file = QtWidgets.QLabel()
        export_row_layout.addWidget(self.lbl_tif_file)
        self.le_tif = QtWidgets.QLineEdit()
        self.le_tif.setPlaceholderText(self.tr("Output .tif file path"))
        self.le_tif.setEnabled(False)
        export_row_layout.addWidget(self.le_tif)
        
        self.btn_browse_tif = QtWidgets.QPushButton("…")
        self.btn_browse_tif.setFixedWidth(30)
        self.btn_browse_tif.setEnabled(False)
        self.btn_browse_tif.clicked.connect(lambda: self._browse_tif(self.le_tif))
        export_row_layout.addWidget(self.btn_browse_tif)
        
        export_row_layout.addSpacing(20)
        self.lbl_compress = QtWidgets.QLabel()
        export_row_layout.addWidget(self.lbl_compress)
        self.cb_compress = QtWidgets.QComboBox()
        self.cb_compress.addItems(["DEFLATE", "LZW", "ZSTD", "NONE"])
        self.cb_compress.setEnabled(False)
        export_row_layout.addWidget(self.cb_compress)
        
        m_grid.addLayout(export_row_layout, 2, 0, 1, 4)
        
        self.chk_export_tif.toggled.connect(self.le_tif.setEnabled)
        self.chk_export_tif.toggled.connect(self.btn_browse_tif.setEnabled)
        self.chk_export_tif.toggled.connect(self.cb_compress.setEnabled)
        
        mosaic_layout.addWidget(self.grp_mosaic_opt)
        
        self.lbl_selected_scenes = QtWidgets.QLabel()
        self.lbl_selected_scenes.setStyleSheet("font-weight: bold; color: #555;")
        mosaic_layout.addWidget(self.lbl_selected_scenes)
        
        self.tableMosaic = QtWidgets.QTableWidget()
        self.tableMosaic.setColumnCount(3)
        self.tableMosaic.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.tableMosaic.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.tableMosaic.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tableMosaic.setFixedHeight(180)
        self.tableMosaic.setStyleSheet("QHeaderView::section { background-color: #eee; }")
        self.tableMosaic.horizontalHeader().setStretchLastSection(True)
        mosaic_layout.addWidget(self.tableMosaic)
        
        self.btn_run_mosaic = QtWidgets.QPushButton()
        self.btn_run_mosaic.setStyleSheet("font-weight: bold; min-height: 40px;")
        self.btn_run_mosaic.clicked.connect(self._run_mosaic)
        mosaic_layout.addWidget(self.btn_run_mosaic)
        
        self.mosaic_progress = QtWidgets.QProgressBar()
        self.mosaic_progress.setRange(0, 0)
        self.mosaic_progress.setVisible(False)
        mosaic_layout.addWidget(self.mosaic_progress)
        
        mosaic_layout.addStretch()
        self.tabs.addTab(self.tab_mosaic, "Auto-Mosaic")
        self.main_layout.addWidget(self.tabs)

    def _retranslateUi(self):
        self.setWindowTitle(self.tr("Quick VRT Imagery Loader"))
        self.label_title.setText(self.tr("Quick VRT Imagery Loader"))
        self.label_subtitle.setText(self.tr("Search and load satellite imagery from Microsoft Planetary Computer"))
        self.grp_params.setTitle(self.tr("Search Parameters"))
        self.lbl_sat.setText(self.tr("Satellite:"))
        self.lbl_comp.setText(self.tr("Composition:"))
        self.lbl_period.setText(self.tr("Period:"))
        self.lbl_to.setText(self.tr(" to "))
        self.lbl_max_clouds.setText(self.tr("Max clouds:"))
        self.grp_bbox.setTitle(self.tr("Search Area (W, S, E, N)"))
        self.btn_use_extent.setText(self.tr("Get from Map"))
        self.btn_use_extent.setToolTip(self.tr("Use current canvas extent"))
        self.tabs.setTabText(0, self.tr("Browser"))
        self.tabs.setTabText(1, self.tr("Auto-Mosaic"))
        self.btn_listar.setText(self.tr("List available images"))
        self.tableWidget.setHorizontalHeaderLabels([self.tr("Index"), self.tr("Image date"), self.tr("Clouds (%)"), self.tr("ID")])
        self.lbl_thumbnail.setText(self.tr("Select an image to preview"))
        self.btn_copy_id.setText("📋 " + self.tr("Copy ID"))
        self.btn_carregar.setText(self.tr("Load image"))
        self.btn_mosaic_selected.setText(self.tr("Mosaic Selected"))
        self.grp_export_browser.setTitle(self.tr("Export GeoTIFF (optional)"))
        self.lbl_tif_file_browser.setText(self.tr(".tif File:"))
        self.le_tif_browser.setPlaceholderText(self.tr("Output .tif file path"))
        self.lbl_compress_browser.setText(self.tr("Compression:"))
        self.grp_mosaic_opt.setTitle(self.tr("Auto-Mosaic Options"))
        self.lbl_max_scenes.setText(self.tr("Safety Limit (Scenes):"))
        self.lbl_preference.setText(self.tr("Preference:"))
        self.chk_export_tif.setText(self.tr("Export GeoTIFF"))
        self.lbl_tif_file.setText(self.tr(".tif File:"))
        self.le_tif.setPlaceholderText(self.tr("Output .tif file path"))
        self.lbl_compress.setText(self.tr("Compression:"))
        self.lbl_selected_scenes.setText(self.tr("Selected Scenes:"))
        self.tableMosaic.setHorizontalHeaderLabels([self.tr("Date"), self.tr("Clouds"), self.tr("ID")])
        self.btn_run_mosaic.setText(self.tr("▶ Generate Mosaic"))

    def _load_extent(self):
        canvas = iface.mapCanvas()
        extent = canvas.extent()
        src_crs = canvas.mapSettings().destinationCrs()
        tgt_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        if src_crs != tgt_crs:
            xform = QgsCoordinateTransform(src_crs, tgt_crs, QgsProject.instance())
            extent = xform.transformBoundingBox(extent)
        self.sp_west.setValue(round(extent.xMinimum(), 6))
        self.sp_south.setValue(round(extent.yMinimum(), 6))
        self.sp_east.setValue(round(extent.xMaximum(), 6))
        self.sp_north.setValue(round(extent.yMaximum(), 6))

    def atualizar_parametros_satelite(self):
        satelite = self.comboBox_satelite.currentText()
        if "Sentinel" in satelite:
            self.loader.collection = "sentinel-2-l2a"
            self.loader.compositions = {
                "True Color (B04, B03, B02)": ['B04', 'B03', 'B02'],
                "False Color NIR (B08, B04, B03)": ['B08', 'B04', 'B03'],
                "False Color SWIR (B12, B08, B04)": ['B12', 'B08', 'B04'],
                "Agriculture (B11, B08, B02)": ['B11', 'B08', 'B02'],
                "Healthy Vegetation (B8A, B11, B02)": ['B8A', 'B11', 'B02'],
                "Red Edge / Stress Vegetal (B08, B8A, B04)": ['B08', 'B8A', 'B04'],
                "Vegetation Index / Biomass (B08, B11, B04)": ['B08', 'B11', 'B04'],
                "Geology (B12, B11, B02)": ['B12', 'B11', 'B02'],
                "Urban / Soil (B12, B11, B04)": ['B12', 'B11', 'B04'],
                "Bathymetric (B04, B03, B01)": ['B04', 'B03', 'B01'],
                "Water Bodies (B03, B08, B11)": ['B03', 'B08', 'B11'],
                "Shortwave IR / Wildfires (B12, B08, B04)": ['B12', 'B08', 'B04'],
                "Burn Area (B12, B8A, B04)": ['B12', 'B8A', 'B04'],
                "Atmospheric Penetration (B12, B11, B8A)": ['B12', 'B11', 'B8A'],
                "Snow / Ice (B04, B03, B08)": ['B04', 'B03', 'B08']
            }
        else:
            self.loader.collection = "landsat-c2-l2"
            self.loader.compositions = {
                "True Color (R, G, B)": ['red', 'green', 'blue'],
                "False Color NIR (NIR, R, G)": ['nir08', 'red', 'green'],
                "Agriculture (SWIR1, NIR, B)": ['swir16', 'nir08', 'blue'],
                "Healthy Vegetation (NIR, SWIR1, R)": ['nir08', 'swir16', 'red'],
                "Geology (SWIR2, SWIR1, B)": ['swir22', 'swir16', 'blue'],
                "Urban / Soil (SWIR2, SWIR1, R)": ['swir22', 'swir16', 'red'],
                "Bathymetric (G, R, Coastal)": ['green', 'red', 'coastal'],
                "Water Bodies (G, NIR, SWIR1)": ['green', 'nir08', 'swir16'],
                "Shortwave IR / Wildfires (SWIR2, NIR, R)": ['swir22', 'nir08', 'red'],
                "Burn Area (SWIR2, SWIR1, NIR)": ['swir22', 'swir16', 'nir08'],
                "Atmospheric Penetration (SWIR2, SWIR1, NIR)": ['swir22', 'swir16', 'nir08'],
                "Snow / Ice (R, G, NIR)": ['red', 'green', 'nir08']
            }
        self.comboBox_composicao.clear()
        self.comboBox_composicao.addItems(list(self.loader.compositions.keys()))

    def _atualizar_label_clouds(self, value):
        self.label_clouds_value.setText(f"{value}%")

    def atualizar_indice_pelo_clique(self, row, column):
        self._carregar_thumbnail(row)

    def _carregar_thumbnail(self, row):
        if row < 0 or row >= len(self.last_items):
            return
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
        bbox = [self.sp_west.value(), self.sp_south.value(), self.sp_east.value(), self.sp_north.value()]
        self.btn_listar.setText(self.tr("Searching…"))
        self.btn_listar.setEnabled(False)
        self._search_worker = SearchWorker(
            self.loader.catalog_url, self.loader.collection, bbox,
            self.dateEdit_inicio.date().toString("yyyy-MM-dd"),
            self.dateEdit_final.date().toString("yyyy-MM-dd"),
            self.slider_clouds.value(), parent=self
        )
        self._search_worker.search_done.connect(self._on_search_done)
        self._search_worker.search_error.connect(self._on_search_error)
        self._search_worker.start()

    def _on_search_done(self, items):
        self.btn_listar.setText(self.tr("List available images"))
        self.btn_listar.setEnabled(True)
        self.last_items = items
        self.tableWidget.setRowCount(0)
        for idx, item in enumerate(items):
            self.tableWidget.insertRow(idx)
            self.tableWidget.setItem(idx, 0, QtWidgets.QTableWidgetItem(str(idx)))
            self.tableWidget.setItem(idx, 1, QtWidgets.QTableWidgetItem(item.properties.get("datetime", "N/A")[:10]))
            self.tableWidget.setItem(idx, 2, QtWidgets.QTableWidgetItem(f"{item.properties.get('eo:cloud_cover', 0):.2f}%"))
            self.tableWidget.setItem(idx, 3, QtWidgets.QTableWidgetItem(item.id))
        self.tableWidget.resizeColumnsToContents()

    def _on_search_error(self, err):
        self.btn_listar.setText(self.tr("List available images"))
        self.btn_listar.setEnabled(True)
        iface.messageBar().pushMessage(self.tr("Search error"), err, level=MsgLevel.Critical)

    def process_stac_load(self):
        rows = self.tableWidget.selectionModel().selectedRows()
        if not rows:
            return
        idx = rows[0].row()
        selected_item = self.last_items[idx]
        export_tif = self.grp_export_browser.isChecked()
        out_tif = self.le_tif_browser.text().strip()
        if export_tif and not out_tif:
            QtWidgets.QMessageBox.warning(self, self.tr("Warning"), self.tr("Please provide the output .tif file path."))
            return
        comp_name = self.comboBox_composicao.currentText()
        bands = self.loader.compositions.get(comp_name, [])
        if export_tif:
            params = {
                "bbox": [self.sp_west.value(), self.sp_south.value(), self.sp_east.value(), self.sp_north.value()],
                "start_date": self.dateEdit_inicio.date().toString("yyyy-MM-dd"),
                "end_date": self.dateEdit_final.date().toString("yyyy-MM-dd"),
                "collection": self.loader.collection,
                "bands": bands,
                "max_cloud": self.slider_clouds.value(),
                "max_items": 1,
                "preference": "N/A",
                "nodata": 0,
                "export_tif": True,
                "out_tif_path": out_tif,
                "compress": self.cb_compress_browser.currentText(),
                "items_list": [selected_item]
            }
            self.btn_carregar.setEnabled(False)
            self._mosaic_worker = MosaicWorker(params)
            self._mosaic_worker.progress.connect(lambda m: iface.mainWindow().statusBar().showMessage(m, 3000))
            self._mosaic_worker.finished.connect(self._on_mosaic_finished)
            self._mosaic_worker.error.connect(self._on_mosaic_error)
            self._mosaic_worker.start()
        else:
            self.btn_carregar.setEnabled(False)
            self._vrt_worker = VrtWorker(selected_item, bands, self.loader.collection, parent=self)
            self._vrt_worker.vrt_ready.connect(self._on_vrt_ready)
            self._vrt_worker.vrt_error.connect(self._on_vrt_error)
            self._vrt_worker.start()

    def _on_vrt_ready(self, vrt_path, layer_name):
        self.btn_carregar.setEnabled(True)
        layer = QgsRasterLayer(vrt_path, layer_name)
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)

    def _on_vrt_error(self, err):
        self.btn_carregar.setEnabled(True)
        iface.messageBar().pushMessage(self.tr("Load error"), err, level=MsgLevel.Critical)

    def _browse_tif(self, line_edit):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, self.tr("Save GeoTIFF"), "", "GeoTIFF (*.tif *.tiff)")
        if path:
            if not path.endswith((".tif", ".tiff")):
                path += ".tif"
            line_edit.setText(path)

    def _run_mosaic_selected(self):
        rows = self.tableWidget.selectionModel().selectedRows()
        if not rows:
            return
        items = [self.last_items[r.row()] for r in rows]
        export_tif = self.grp_export_browser.isChecked()
        out_tif = self.le_tif_browser.text().strip()
        if export_tif and not out_tif:
            QtWidgets.QMessageBox.warning(self, self.tr("Warning"), self.tr("Please provide the output .tif file path."))
            return
        params = {
            "bbox": [self.sp_west.value(), self.sp_south.value(), self.sp_east.value(), self.sp_north.value()],
            "start_date": self.dateEdit_inicio.date().toString("yyyy-MM-dd"),
            "end_date": self.dateEdit_final.date().toString("yyyy-MM-dd"),
            "collection": self.loader.collection,
            "bands": self.loader.compositions.get(self.comboBox_composicao.currentText(), []),
            "max_cloud": self.slider_clouds.value(),
            "max_items": len(items),
            "preference": "Manual",
            "nodata": 0,
            "export_tif": export_tif,
            "out_tif_path": out_tif,
            "compress": self.cb_compress_browser.currentText(),
            "items_list": items
        }
        self.btn_mosaic_selected.setEnabled(False)
        self._mosaic_worker = MosaicWorker(params)
        self._mosaic_worker.progress.connect(lambda m: iface.mainWindow().statusBar().showMessage(m, 3000))
        self._mosaic_worker.finished.connect(self._on_mosaic_finished)
        self._mosaic_worker.error.connect(self._on_mosaic_error)
        self._mosaic_worker.start()

    def _run_mosaic(self):
        export_tif = self.chk_export_tif.isChecked()
        out_tif = self.le_tif.text().strip()
        if export_tif and not out_tif:
            QtWidgets.QMessageBox.warning(self, self.tr("Warning"), self.tr("Please provide the output .tif file path."))
            return
        params = {
            "bbox": [self.sp_west.value(), self.sp_south.value(), self.sp_east.value(), self.sp_north.value()],
            "start_date": self.dateEdit_inicio.date().toString("yyyy-MM-dd"),
            "end_date": self.dateEdit_final.date().toString("yyyy-MM-dd"),
            "collection": self.loader.collection,
            "bands": self.loader.compositions.get(self.comboBox_composicao.currentText(), []),
            "max_cloud": self.slider_clouds.value(),
            "max_items": self.sp_items.value(),
            "preference": self.cb_preference.currentText(),
            "nodata": 0,
            "export_tif": export_tif,
            "out_tif_path": out_tif,
            "compress": self.cb_compress.currentText()
        }
        self.tableMosaic.setRowCount(0)
        self.btn_run_mosaic.setEnabled(False)
        self.mosaic_progress.setVisible(True)
        self._mosaic_worker = MosaicWorker(params)
        self._mosaic_worker.progress.connect(lambda m: iface.mainWindow().statusBar().showMessage(m, 3000))
        self._mosaic_worker.item_selected.connect(self._on_mosaic_item_selected)
        self._mosaic_worker.finished.connect(self._on_mosaic_finished)
        self._mosaic_worker.error.connect(self._on_mosaic_error)
        self._mosaic_worker.start()

    def _on_mosaic_item_selected(self, date, clouds, item_id):
        row = self.tableMosaic.rowCount()
        self.tableMosaic.insertRow(row)
        self.tableMosaic.setItem(row, 0, QtWidgets.QTableWidgetItem(date))
        self.tableMosaic.setItem(row, 1, QtWidgets.QTableWidgetItem(clouds))
        self.tableMosaic.setItem(row, 2, QtWidgets.QTableWidgetItem(item_id))
        self.tableMosaic.resizeColumnsToContents()

    def _on_mosaic_finished(self, vrt, tif):
        self.btn_run_mosaic.setEnabled(True)
        self.btn_mosaic_selected.setEnabled(True)
        self.btn_carregar.setEnabled(True)
        self.mosaic_progress.setVisible(False)
        comp = self.comboBox_composicao.currentText()
        l = QgsRasterLayer(vrt, self.tr("Mosaic – {}").format(comp))
        if l.isValid():
            QgsProject.instance().addMapLayer(l)
        if tif:
            lt = QgsRasterLayer(tif, self.tr("GeoTIFF Mosaic – {}").format(comp))
            if lt.isValid():
                QgsProject.instance().addMapLayer(lt)
        iface.messageBar().pushMessage(self.tr("Success"), self.tr("Mosaic generation finished."), level=MsgLevel.Success)

    def _on_mosaic_error(self, err):
        self.btn_run_mosaic.setEnabled(True)
        self.btn_mosaic_selected.setEnabled(True)
        self.btn_carregar.setEnabled(True)
        self.mosaic_progress.setVisible(False)
        QtWidgets.QMessageBox.critical(self, self.tr("Mosaic error"), err)

    def _reset_thumbnail_panel(self):
        self.lbl_thumbnail.setText(self.tr("Select an image to preview"))
        self.lbl_thumbnail.setPixmap(QPixmap())
