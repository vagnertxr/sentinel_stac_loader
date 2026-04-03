# -*- coding: utf-8 -*-
"""
SentinelSTACDialog - UI layer for Quick VRT Imagery Loader.
"""

import os
from datetime import date, timedelta

from qgis.PyQt import QtWidgets, QtCore
from qgis.PyQt.QtCore import Qt, QCoreApplication, QSize, pyqtSignal, QThread
from qgis.PyQt.QtGui import QPixmap, QFont, QColor, QIcon
from qgis.core import (
    QgsRasterLayer, QgsProject, QgsCoordinateTransform,
    QgsCoordinateReferenceSystem, Qgis, QgsMessageLog,
    QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
    QgsRectangle, QgsJsonUtils
)
from qgis.gui import QgsRubberBand
from qgis.utils import iface

from .mosaic_worker import MosaicWorker

import qgis.PyQt.QtCore as _qc
_QT6 = [int(x) for x in _qc.qVersion().split(".")][0] >= 6

def _flag(cls, name):
    """Return Qt flag/enum value, trying Qt6 nested enums first."""
    try:
        return getattr(cls, name)
    except AttributeError:
        parts = name.split(".")
        obj = cls
        for p in parts:
            obj = getattr(obj, p)
        return obj

if _QT6:
    _AlignCenter   = Qt.AlignmentFlag.AlignCenter
    _AlignRight    = Qt.AlignmentFlag.AlignRight
    _Horizontal    = Qt.Orientation.Horizontal
    _Vertical      = Qt.Orientation.Vertical
    _NoEditTrig    = QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
    _SelectRows    = QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
    _SingleSel     = QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
    _ExtendedSel   = QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
    _NoSel         = QtWidgets.QAbstractItemView.SelectionMode.NoSelection
    _NoFocus       = Qt.FocusPolicy.NoFocus
    _StyledPanel   = QtWidgets.QFrame.Shape.StyledPanel
    _WindowModal   = Qt.WindowModality.WindowModal
    _KeepAspect    = Qt.AspectRatioMode.KeepAspectRatio
    _SmoothTx      = Qt.TransformationMode.SmoothTransformation
    _WrapWord      = Qt.TextInteractionFlag.TextSelectableByMouse
else:
    _AlignCenter   = Qt.AlignCenter      
    _AlignRight    = Qt.AlignRight      
    _Horizontal    = Qt.Horizontal        
    _Vertical      = Qt.Vertical            
    _NoEditTrig    = QtWidgets.QAbstractItemView.NoEditTriggers
    _SelectRows    = QtWidgets.QAbstractItemView.SelectRows
    _SingleSel     = QtWidgets.QAbstractItemView.SingleSelection
    _ExtendedSel   = QtWidgets.QAbstractItemView.ExtendedSelection
    _NoSel         = QtWidgets.QAbstractItemView.NoSelection
    _NoFocus       = Qt.NoFocus             
    _StyledPanel   = QtWidgets.QFrame.StyledPanel
    _WindowModal   = Qt.WindowModal        
    _KeepAspect    = Qt.KeepAspectRatio     
    _SmoothTx      = Qt.SmoothTransformation  
    _WrapWord      = Qt.TextSelectableByMouse  

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

# Predefined band combinations for Sentinel-2 and Landsat.
SENTINEL2_COMPOSITIONS = {
    "True Color (B04, B03, B02)":              ["B04", "B03", "B02"],
    "False Color NIR (B08, B04, B03)":         ["B08", "B04", "B03"],
    "False Color SWIR (B12, B08, B04)":        ["B12", "B08", "B04"],
    "Agriculture (B11, B08, B02)":             ["B11", "B08", "B02"],
    "Healthy Vegetation (B8A, B11, B02)":      ["B8A", "B11", "B02"],
    "Red Edge / Stress (B08, B8A, B04)":       ["B08", "B8A", "B04"],
    "Vegetation / Biomass (B08, B11, B04)":    ["B08", "B11", "B04"],
    "Geology (B12, B11, B02)":                 ["B12", "B11", "B02"],
    "Urban / Soil (B12, B11, B04)":            ["B12", "B11", "B04"],
    "Bathymetric (B04, B03, B01)":             ["B04", "B03", "B01"],
    "Water Bodies (B03, B08, B11)":            ["B03", "B08", "B11"],
    "Wildfires / SWIR (B12, B08, B04)":        ["B12", "B08", "B04"],
    "Burn Area (B12, B8A, B04)":               ["B12", "B8A", "B04"],
    "Atmospheric Penetration (B12, B11, B8A)": ["B12", "B11", "B8A"],
    "Snow / Ice (B04, B03, B08)":              ["B04", "B03", "B08"],
}

LANDSAT_COMPOSITIONS = {
    "True Color (R, G, B)":                    ["red", "green", "blue"],
    "False Color NIR (NIR, R, G)":             ["nir08", "red", "green"],
    "Agriculture (SWIR1, NIR, B)":             ["swir16", "nir08", "blue"],
    "Healthy Vegetation (NIR, SWIR1, R)":      ["nir08", "swir16", "red"],
    "Geology (SWIR2, SWIR1, B)":               ["swir22", "swir16", "blue"],
    "Urban / Soil (SWIR2, SWIR1, R)":          ["swir22", "swir16", "red"],
    "Bathymetric (G, R, Coastal)":             ["green", "red", "coastal"],
    "Water Bodies (G, NIR, SWIR1)":            ["green", "nir08", "swir16"],
    "Wildfires (SWIR2, NIR, R)":               ["swir22", "nir08", "red"],
    "Burn Area (SWIR2, SWIR1, NIR)":           ["swir22", "swir16", "nir08"],
    "Atmospheric Penetration (SWIR2, SWIR1)":  ["swir22", "swir16", "nir08"],
    "Snow / Ice (R, G, NIR)":                  ["red", "green", "nir08"],
}

# ── Generoso defaults ────────────────────────────────────────────────────────
_DEFAULT_DAYS_BACK  = 180   # janela de busca padrão: 6 meses
_DEFAULT_MAX_CLOUDS = 40    # nuvens: até 40 %
_DEFAULT_MAX_SCENES = 50    # limite de cenas no Auto-Mosaic
# ─────────────────────────────────────────────────────────────────────────────

class ThumbnailWorker(QThread):
    thumbnail_ready = pyqtSignal(QPixmap)
    failed          = pyqtSignal(str)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            if not self.url.lower().startswith(("http://", "https://")):
                self.failed.emit("Invalid URL")
                return
            import urllib.request
            req = urllib.request.Request(
                self.url, headers={"User-Agent": "QuickVRTImageryLoader/0.7"}
            )
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = resp.read()
            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                self.failed.emit("Could not decode image")
                return
            pixmap = pixmap.scaled(320, 320, _KeepAspect, _SmoothTx)
            self.thumbnail_ready.emit(pixmap)
        except Exception as e:
            self.failed.emit(str(e)[:80])


class SearchWorker(QThread):
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
                datetime=f"{self.start_date}/{self.end_date}",
            )
            items = list(search.get_all_items())
            items = sorted(
                items, key=lambda x: x.properties.get("eo:cloud_cover", 100)
            )
            items = [
                i for i in items
                if i.properties.get("eo:cloud_cover", 100) <= self.max_clouds
            ]
            self.search_done.emit(items)
        except Exception as e:
            self.search_error.emit(str(e))


class VrtWorker(QThread):
    vrt_ready     = pyqtSignal(str, str)
    vrt_error     = pyqtSignal(str)
    load_progress = pyqtSignal(int)

    def __init__(self, items, bands, collection, parent=None):
        super().__init__(parent)
        self.items      = items if isinstance(items, list) else [items]
        self.bands      = bands
        self.collection = collection

    def run(self):
        try:
            import planetary_computer
            import processing
            from osgeo import gdal
            
            # Boost GDAL network resilience for /vsicurl/
            gdal.SetConfigOption("GDAL_HTTP_MAX_RETRY", "10")
            gdal.SetConfigOption("GDAL_HTTP_RETRY_DELAY", "1")
            gdal.SetConfigOption("VSI_CACHE", "TRUE")
            gdal.SetConfigOption("GDAL_HTTP_TIMEOUT", "30")
            
            total = len(self.items)
            for i, item in enumerate(self.items):
                try:
                    band_hrefs = []
                    for band in self.bands:
                        asset = item.assets.get(band)
                        if asset:
                            signed = planetary_computer.sign(asset.href)
                            band_hrefs.append(f"/vsicurl/{signed}")
                    
                    if not band_hrefs:
                        self.vrt_error.emit(f"Item {item.id}: No valid bands")
                        continue

                    result = processing.run(
                        "gdal:buildvirtualraster",
                        {"INPUT": band_hrefs, "SEPARATE": True, "OUTPUT": "TEMPORARY_OUTPUT"},
                    )
                    
                    clouds     = item.properties.get("eo:cloud_cover", 0)
                    prefix     = "S2" if "sentinel" in self.collection else "LS"
                    layer_name = f"{prefix}_{item.id} ({clouds:.1f}% clouds)"
                    self.vrt_ready.emit(result["OUTPUT"], layer_name)
                    
                    self.load_progress.emit(int(((i + 1) / total) * 100))
                except Exception as e:
                    self.vrt_error.emit(f"Error loading {item.id}: {str(e)}")
        except Exception as e:
            self.vrt_error.emit(str(e))


class SentinelSTACDialog(QtWidgets.QDialog):

    _STYLE = """
        QDialog {
            background-color: #1e1e2e;
            color: #cdd6f4;
        }
        QGroupBox {
            border: 1px solid #45475a;
            border-radius: 6px;
            margin-top: 8px;
            padding-top: 6px;
            color: #cdd6f4;
            font-weight: bold;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
            color: #89b4fa;
        }
        QLabel { color: #cdd6f4; }
        QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QLineEdit {
            background-color: #313244;
            border: 1px solid #45475a;
            border-radius: 4px;
            padding: 3px 6px;
            color: #cdd6f4;
            selection-background-color: #89b4fa;
        }
        QComboBox::drop-down { border: none; }
        QSlider::groove:horizontal {
            height: 4px;
            background: #45475a;
            border-radius: 2px;
        }
        QSlider::handle:horizontal {
            background: #89b4fa;
            width: 14px; height: 14px;
            margin: -5px 0;
            border-radius: 7px;
        }
        QSlider::sub-page:horizontal { background: #89b4fa; border-radius: 2px; }
        QTableWidget {
            background-color: #181825;
            alternate-background-color: #1e1e2e;
            gridline-color: #45475a;
            border: 1px solid #45475a;
            border-radius: 4px;
            color: #cdd6f4;
        }
        QHeaderView::section {
            background-color: #313244;
            color: #89b4fa;
            font-weight: bold;
            border: none;
            padding: 4px;
        }
        QTableWidget::item:selected {
            background-color: #89b4fa;
            color: #1e1e2e;
        }
        QTabWidget::pane {
            border: 1px solid #45475a;
            border-radius: 6px;
            background-color: #1e1e2e;
        }
        QTabBar::tab {
            background: #313244;
            color: #cdd6f4;
            padding: 6px 18px;
            border-radius: 4px 4px 0 0;
            margin-right: 2px;
        }
        QTabBar::tab:selected { background: #89b4fa; color: #1e1e2e; font-weight: bold; }
        QTabBar::tab:hover    { background: #45475a; }
        QPushButton {
            background-color: #313244;
            color: #cdd6f4;
            border: 1px solid #45475a;
            border-radius: 5px;
            padding: 5px 12px;
        }
        QPushButton:hover    { background-color: #45475a; }
        QPushButton:pressed  { background-color: #585b70; }
        QPushButton:disabled { color: #585b70; border-color: #313244; }
        QPushButton#btn_primary {
            background-color: #89b4fa;
            color: #1e1e2e;
            font-weight: bold;
            border: none;
        }
        QPushButton#btn_primary:hover   { background-color: #b4d0fb; }
        QPushButton#btn_primary:pressed { background-color: #74a8f9; }
        QPushButton#btn_danger {
            background-color: #f38ba8;
            color: #1e1e2e;
            font-weight: bold;
            border: none;
        }
        QTextEdit {
            background-color: #11111b;
            color: #a6e3a1;
            font-family: monospace;
            font-size: 9pt;
            border: 1px solid #45475a;
            border-radius: 4px;
        }
        QProgressBar {
            border: 1px solid #45475a;
            border-radius: 4px;
            background-color: #181825;
            height: 10px;
            text-align: center;
            color: transparent;
        }
        QProgressBar::chunk { background-color: #89b4fa; border-radius: 3px; }
        QCheckBox { color: #cdd6f4; spacing: 6px; }
        QCheckBox::indicator { width: 14px; height: 14px; }
        QFrame#preview_frame {
            background-color: #181825;
            border: 1px solid #45475a;
            border-radius: 6px;
        }
        QSplitter::handle { background-color: #45475a; }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SentinelSTACDialogBase")
        self.setWindowTitle(self.tr("Quick VRT Imagery Loader"))
        self.resize(1020, 840)
        self.setMinimumSize(QSize(860, 720))
        self.setStyleSheet(self._STYLE)

        # State
        self._collection   = "sentinel-2-l2a"
        self._compositions = SENTINEL2_COMPOSITIONS.copy()
        self.last_items    = []
        self._thumb_worker  = None
        self._search_worker = None
        self._vrt_worker    = None
        self._mosaic_worker = None
        self._rubber_band   = None
        
        # Debounce timer for thumbnails to avoid freezing during rapid clicking
        self._thumb_timer = QtCore.QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.timeout.connect(self._do_debounced_thumbnail)

        self._build_ui()
        self._retranslate()
        self._connect_signals()
        self._update_satellite_params()
        self._load_extent()


    def tr(self, msg):
        return QCoreApplication.translate("SentinelSTACDialogBase", msg)


    def _build_ui(self):

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)
        root.addLayout(self._build_header())
        root.addWidget(self._build_params_group())
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self._build_browser_tab(), self.tr("Browser"))
        self.tabs.addTab(self._build_mosaic_tab(),  self.tr("Auto-Mosaic"))
        root.addWidget(self.tabs, stretch=1)

    def _build_header(self):
        lay = QtWidgets.QHBoxLayout()

        icon_lbl = QtWidgets.QLabel()
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        if os.path.exists(icon_path):
            pix = QPixmap(icon_path).scaled(36, 36, _KeepAspect, _SmoothTx)
            icon_lbl.setPixmap(pix)
        lay.addWidget(icon_lbl)

        title_lay = QtWidgets.QVBoxLayout()
        self.lbl_title = QtWidgets.QLabel()
        f = QFont()
        f.setPointSize(14)
        f.setBold(True)
        self.lbl_title.setFont(f)
        self.lbl_title.setStyleSheet("color: #cdd6f4;")
        self.lbl_subtitle = QtWidgets.QLabel()
        self.lbl_subtitle.setStyleSheet("color: #6c7086; font-size: 9pt;")
        title_lay.addWidget(self.lbl_title)
        title_lay.addWidget(self.lbl_subtitle)
        lay.addLayout(title_lay)
        lay.addStretch()
        return lay

    def _build_params_group(self):
        self.grp_params = QtWidgets.QGroupBox()
        g = QtWidgets.QGridLayout(self.grp_params)
        g.setColumnStretch(1, 2)
        g.setColumnStretch(3, 3)
        g.setColumnStretch(5, 2)

        self.lbl_sat = QtWidgets.QLabel()
        g.addWidget(self.lbl_sat, 0, 0)
        self.comboBox_satelite = QtWidgets.QComboBox()
        self.comboBox_satelite.addItems(
            ["Sentinel-2 L2A", "Landsat Collection 2 Level-2"]
        )
        g.addWidget(self.comboBox_satelite, 0, 1)

        self.lbl_comp = QtWidgets.QLabel()
        g.addWidget(self.lbl_comp, 0, 2)
        self.comboBox_composicao = QtWidgets.QComboBox()
        g.addWidget(self.comboBox_composicao, 0, 3, 1, 3)

        self.lbl_period = QtWidgets.QLabel()
        g.addWidget(self.lbl_period, 1, 0)
        date_lay = QtWidgets.QHBoxLayout()
        date_style = "font-size: 8pt;"
        # ── data inicial: _DEFAULT_DAYS_BACK dias atrás ──────────────────────
        self.dateEdit_inicio = QtWidgets.QDateEdit(
            date.today() - timedelta(days=_DEFAULT_DAYS_BACK)
        )
        self.dateEdit_inicio.setCalendarPopup(True)
        self.dateEdit_inicio.setDisplayFormat("yyyy-MM-dd")
        self.dateEdit_inicio.setStyleSheet(date_style)
        self.dateEdit_final = QtWidgets.QDateEdit(date.today())
        self.dateEdit_final.setCalendarPopup(True)
        self.dateEdit_final.setDisplayFormat("yyyy-MM-dd")
        self.dateEdit_final.setStyleSheet(date_style)
        self.lbl_to = QtWidgets.QLabel()
        self.lbl_to.setStyleSheet(date_style)
        date_lay.addWidget(self.dateEdit_inicio)
        date_lay.addWidget(self.lbl_to)
        date_lay.addWidget(self.dateEdit_final)
        date_lay.addStretch()
        g.addLayout(date_lay, 1, 1)

        self.lbl_max_clouds = QtWidgets.QLabel()
        g.addWidget(self.lbl_max_clouds, 1, 2)
        cloud_lay = QtWidgets.QHBoxLayout()
        self.slider_clouds = QtWidgets.QSlider(_Horizontal)
        self.slider_clouds.setRange(0, 100)
        # ── cobertura de nuvens padrão: _DEFAULT_MAX_CLOUDS ──────────────────
        self.slider_clouds.setValue(_DEFAULT_MAX_CLOUDS)
        self.lbl_clouds_val = QtWidgets.QLabel(f"{_DEFAULT_MAX_CLOUDS}%")
        self.lbl_clouds_val.setFixedWidth(36)
        self.lbl_clouds_val.setAlignment(_AlignCenter)
        self.lbl_clouds_val.setStyleSheet("color: #89b4fa; font-weight: bold;")
        cloud_lay.addWidget(self.slider_clouds)
        cloud_lay.addWidget(self.lbl_clouds_val)
        g.addLayout(cloud_lay, 1, 3)

        self.lbl_bbox = QtWidgets.QLabel()
        g.addWidget(self.lbl_bbox, 2, 0)
        bbox_lay = QtWidgets.QHBoxLayout()
        bbox_lay.setSpacing(2)
        self.sp_west  = self._make_coord_spin(-180, 180)
        self.sp_south = self._make_coord_spin(-90,  90)
        self.sp_east  = self._make_coord_spin(-180, 180)
        self.sp_north = self._make_coord_spin(-90,  90)
        for lbl, sp in [("W:", self.sp_west), ("S:", self.sp_south),
                         ("E:", self.sp_east),  ("N:", self.sp_north)]:
            l = QtWidgets.QLabel(lbl)
            l.setStyleSheet("color: #89b4fa; font-weight: bold; margin-left: 4px;")
            bbox_lay.addWidget(l)
            bbox_lay.addWidget(sp)
        bbox_lay.addSpacing(6)
        self.btn_extent = QtWidgets.QPushButton()
        self.btn_extent.setFixedHeight(28)
        bbox_lay.addWidget(self.btn_extent)
        bbox_lay.addStretch()
        g.addLayout(bbox_lay, 2, 1, 1, 5)

        return self.grp_params

    def _build_browser_tab(self):
        tab = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(tab)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        self.btn_listar = QtWidgets.QPushButton()
        self.btn_listar.setObjectName("btn_primary")
        self.btn_listar.setMinimumHeight(34)
        lay.addWidget(self.btn_listar)
        splitter = QtWidgets.QSplitter(_Horizontal)
        left_w = QtWidgets.QWidget()
        left_lay = QtWidgets.QVBoxLayout(left_w)
        left_lay.setContentsMargins(0, 0, 0, 0)

        self.tableWidget = QtWidgets.QTableWidget()
        self.tableWidget.setColumnCount(4)
        self.tableWidget.setSelectionBehavior(_SelectRows)
        self.tableWidget.setSelectionMode(_ExtendedSel)
        self.tableWidget.setEditTriggers(_NoEditTrig)
        self.tableWidget.setAlternatingRowColors(True)
        self.tableWidget.verticalHeader().setVisible(False)
        self.tableWidget.horizontalHeader().setStretchLastSection(True)
        left_lay.addWidget(self.tableWidget)
        btn_row = QtWidgets.QHBoxLayout()
        self.btn_carregar = QtWidgets.QPushButton()
        self.btn_carregar.setObjectName("btn_primary")
        self.btn_carregar.setMinimumHeight(32)
        self.btn_mosaic_selected = QtWidgets.QPushButton()
        self.btn_mosaic_selected.setMinimumHeight(32)
        btn_row.addWidget(self.btn_carregar, 2)
        btn_row.addWidget(self.btn_mosaic_selected, 1)
        left_lay.addLayout(btn_row)
        self.browser_progress = QtWidgets.QProgressBar()
        self.browser_progress.setRange(0, 100)
        self.browser_progress.setValue(0)
        self.browser_progress.setVisible(False)
        self.browser_progress.setFixedHeight(6)
        self.browser_progress.setTextVisible(False)
        left_lay.addWidget(self.browser_progress)

        splitter.addWidget(left_w)

        right_w = QtWidgets.QFrame()
        right_w.setObjectName("preview_frame")
        right_lay = QtWidgets.QVBoxLayout(right_w)
        right_lay.setContentsMargins(8, 8, 8, 8)
        right_lay.setSpacing(6)

        self.lbl_thumbnail = QtWidgets.QLabel()
        self.lbl_thumbnail.setAlignment(_AlignCenter)
        self.lbl_thumbnail.setMinimumSize(260, 220)
        self.lbl_thumbnail.setStyleSheet(
            "background-color: #11111b; border-radius: 4px; color: #585b70;"
        )
        right_lay.addWidget(self.lbl_thumbnail, stretch=1)

        meta_grid = QtWidgets.QGridLayout()
        self.lbl_thumb_date   = self._meta_label()
        self.lbl_thumb_clouds = self._meta_label()
        self.lbl_thumb_clouds.setStyleSheet("color: #89b4fa; font-weight: bold;")
        self.lbl_thumb_id     = QtWidgets.QLabel()
        self.lbl_thumb_id.setWordWrap(True)
        self.lbl_thumb_id.setStyleSheet(
            "font-family: monospace; font-size: 8pt; color: #6c7086;"
        )
        meta_grid.addWidget(QtWidgets.QLabel(self.tr("Date")), 0, 0)
        meta_grid.addWidget(self.lbl_thumb_date,   0, 1)
        meta_grid.addWidget(QtWidgets.QLabel(self.tr("Clouds")),  1, 0)
        meta_grid.addWidget(self.lbl_thumb_clouds,  1, 1)
        meta_grid.addWidget(QtWidgets.QLabel(self.tr("ID")), 2, 0)
        meta_grid.addWidget(self.lbl_thumb_id,      2, 1)
        right_lay.addLayout(meta_grid)

        btn_row2 = QtWidgets.QHBoxLayout()
        self.btn_copy_id     = QtWidgets.QPushButton(self.tr("Copy ID"))
        self.btn_show_footprint = QtWidgets.QPushButton(self.tr("Show Footprint"))
        self.btn_show_footprint.setCheckable(True)
        btn_row2.addWidget(self.btn_copy_id)
        btn_row2.addWidget(self.btn_show_footprint)
        right_lay.addLayout(btn_row2)

        self.grp_export_browser = QtWidgets.QGroupBox()
        self.grp_export_browser.setCheckable(True)
        self.grp_export_browser.setChecked(False)
        eb_lay = QtWidgets.QHBoxLayout(self.grp_export_browser)
        self.lbl_tif_file_browser  = QtWidgets.QLabel()
        self.le_tif_browser        = QtWidgets.QLineEdit()
        self.le_tif_browser.setPlaceholderText(self.tr("Output .tif path"))
        self.btn_browse_tif_browser = QtWidgets.QPushButton("…")
        self.btn_browse_tif_browser.setFixedWidth(28)
        self.btn_browse_tif_browser.clicked.connect(
            lambda: self._browse_tif(self.le_tif_browser)
        )
        self.lbl_compress_browser = QtWidgets.QLabel()
        self.cb_compress_browser  = QtWidgets.QComboBox()
        self.cb_compress_browser.addItems(["DEFLATE", "LZW", "ZSTD", "NONE"])
        eb_lay.addWidget(self.lbl_tif_file_browser)
        eb_lay.addWidget(self.le_tif_browser, stretch=1)
        eb_lay.addWidget(self.btn_browse_tif_browser)
        eb_lay.addWidget(self.lbl_compress_browser)
        eb_lay.addWidget(self.cb_compress_browser)
        right_lay.addWidget(self.grp_export_browser)

        splitter.addWidget(right_w)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        lay.addWidget(splitter, stretch=1)

        return tab

    def _build_mosaic_tab(self):
        tab = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(tab)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        self.grp_mosaic_opt = QtWidgets.QGroupBox()
        m = QtWidgets.QGridLayout(self.grp_mosaic_opt)
        m.setColumnStretch(1, 1)
        m.setColumnStretch(3, 1)

        self.lbl_max_scenes = QtWidgets.QLabel()
        m.addWidget(self.lbl_max_scenes, 0, 0)
        self.sp_items = QtWidgets.QSpinBox()
        self.sp_items.setRange(1, 500)          # ── máximo ampliado para 500
        self.sp_items.setValue(_DEFAULT_MAX_SCENES)  # ── default: 50 cenas
        m.addWidget(self.sp_items, 0, 1)

        self.lbl_preference = QtWidgets.QLabel()
        m.addWidget(self.lbl_preference, 0, 2)
        self.cb_preference = QtWidgets.QComboBox()
        self.cb_preference.addItems([self.tr("Least Clouds"), self.tr("Most Recent")])
        m.addWidget(self.cb_preference, 0, 3)

        self.chk_export_tif = QtWidgets.QCheckBox()
        m.addWidget(self.chk_export_tif, 1, 0, 1, 4)

        exp_lay = QtWidgets.QHBoxLayout()
        self.lbl_tif_file  = QtWidgets.QLabel()
        self.le_tif        = QtWidgets.QLineEdit()
        self.le_tif.setEnabled(False)
        self.btn_browse_tif = QtWidgets.QPushButton("…")
        self.btn_browse_tif.setFixedWidth(28)
        self.btn_browse_tif.setEnabled(False)
        self.btn_browse_tif.clicked.connect(lambda: self._browse_tif(self.le_tif))
        self.lbl_compress  = QtWidgets.QLabel()
        self.cb_compress   = QtWidgets.QComboBox()
        self.cb_compress.addItems(["DEFLATE", "LZW", "ZSTD", "NONE"])
        self.cb_compress.setEnabled(False)
        exp_lay.addWidget(self.lbl_tif_file)
        exp_lay.addWidget(self.le_tif, stretch=1)
        exp_lay.addWidget(self.btn_browse_tif)
        exp_lay.addSpacing(12)
        exp_lay.addWidget(self.lbl_compress)
        exp_lay.addWidget(self.cb_compress)
        m.addLayout(exp_lay, 2, 0, 1, 4)

        self.chk_export_tif.toggled.connect(self.le_tif.setEnabled)
        self.chk_export_tif.toggled.connect(self.btn_browse_tif.setEnabled)
        self.chk_export_tif.toggled.connect(self.cb_compress.setEnabled)
        lay.addWidget(self.grp_mosaic_opt)

        v_splitter = QtWidgets.QSplitter(_Vertical)

        scene_w = QtWidgets.QWidget()
        scene_lay = QtWidgets.QVBoxLayout(scene_w)
        scene_lay.setContentsMargins(0, 0, 0, 0)
        self.lbl_selected_scenes = QtWidgets.QLabel()
        self.lbl_selected_scenes.setStyleSheet("font-weight: bold; color: #89b4fa;")
        scene_lay.addWidget(self.lbl_selected_scenes)
        self.tableMosaic = QtWidgets.QTableWidget()
        self.tableMosaic.setColumnCount(3)
        self.tableMosaic.setSelectionMode(_NoSel)
        self.tableMosaic.setFocusPolicy(_NoFocus)
        self.tableMosaic.setEditTriggers(_NoEditTrig)
        self.tableMosaic.setAlternatingRowColors(True)
        self.tableMosaic.verticalHeader().setVisible(False)
        self.tableMosaic.horizontalHeader().setStretchLastSection(True)
        scene_lay.addWidget(self.tableMosaic)
        v_splitter.addWidget(scene_w)

        log_w = QtWidgets.QWidget()
        log_lay = QtWidgets.QVBoxLayout(log_w)
        log_lay.setContentsMargins(0, 0, 0, 0)
        log_hdr = QtWidgets.QHBoxLayout()
        lbl_log = QtWidgets.QLabel(self.tr("Progress log"))
        lbl_log.setStyleSheet("font-weight: bold; color: #89b4fa;")
        self.btn_clear_log = QtWidgets.QPushButton(self.tr("Clear"))
        self.btn_clear_log.setFixedHeight(22)
        self.btn_clear_log.setFixedWidth(60)
        log_hdr.addWidget(lbl_log)
        log_hdr.addStretch()
        log_hdr.addWidget(self.btn_clear_log)
        log_lay.addLayout(log_hdr)
        self.log_panel = QtWidgets.QTextEdit()
        self.log_panel.setReadOnly(True)
        log_lay.addWidget(self.log_panel)
        v_splitter.addWidget(log_w)

        v_splitter.setStretchFactor(0, 2)
        v_splitter.setStretchFactor(1, 3)
        lay.addWidget(v_splitter, stretch=1)

        self.btn_run_mosaic = QtWidgets.QPushButton()
        self.btn_run_mosaic.setObjectName("btn_primary")
        self.btn_run_mosaic.setMinimumHeight(40)
        lay.addWidget(self.btn_run_mosaic)

        self.mosaic_progress = QtWidgets.QProgressBar()
        self.mosaic_progress.setRange(0, 0)
        self.mosaic_progress.setVisible(False)
        self.mosaic_progress.setFixedHeight(8)
        lay.addWidget(self.mosaic_progress)

        return tab

    def _connect_signals(self):
        self.comboBox_satelite.currentIndexChanged.connect(
            self._update_satellite_params
        )
        self.slider_clouds.valueChanged.connect(
            lambda v: self.lbl_clouds_val.setText(f"{v}%")
        )
        self.btn_extent.clicked.connect(self._load_extent)
        self.btn_listar.clicked.connect(self.popular_tabela)
        self.tableWidget.cellClicked.connect(
            lambda row, _: self._on_table_row_clicked(row)
        )
        self.tableWidget.itemSelectionChanged.connect(self._on_selection_changed)
        self.btn_carregar.clicked.connect(self.process_stac_load)
        self.btn_mosaic_selected.clicked.connect(self._run_mosaic_selected)
        self.btn_copy_id.clicked.connect(
            lambda: QtWidgets.QApplication.clipboard().setText(
                self.lbl_thumb_id.text()
            )
        )
        self.btn_show_footprint.toggled.connect(self._toggle_footprint)
        self.btn_run_mosaic.clicked.connect(self._run_mosaic)
        self.btn_clear_log.clicked.connect(self.log_panel.clear)

    def _retranslate(self):
        self.lbl_title.setText(self.tr("Quick VRT Imagery Loader"))
        self.lbl_subtitle.setText(
            self.tr("Browse Satellite product collections, load imagery and build compositions and mosaics very quickly!")
        )
        self.grp_params.setTitle(self.tr("Search Parameters"))
        self.lbl_sat.setText(self.tr("Satellite:"))
        self.lbl_comp.setText(self.tr("Composition:"))
        self.lbl_period.setText(self.tr("Period:"))
        self.lbl_to.setText(self.tr(" to "))
        self.lbl_max_clouds.setText(self.tr("Max clouds:"))
        self.lbl_bbox.setText(self.tr("Search area:"))
        self.btn_extent.setText(self.tr("🗺️ Get from map canvas"))
        self.btn_listar.setText(self.tr("🔍 Search available images"))
        self.tableWidget.setHorizontalHeaderLabels(
            [self.tr("#"), self.tr("Date"), self.tr("Clouds"), self.tr("Scene ID")]
        )
        self.btn_carregar.setText(self.tr("Load Selected"))
        self.btn_mosaic_selected.setText(self.tr("Mosaic Selected"))
        self.btn_copy_id.setText(self.tr("📋 Copy ID"))
        self.btn_show_footprint.setText(self.tr("Toggle Footprint"))
        self.grp_export_browser.setTitle(self.tr("Export GeoTIFF (optional)"))
        self.lbl_tif_file_browser.setText(self.tr("File:"))
        self.lbl_compress_browser.setText(self.tr("Compress:"))
        self.grp_mosaic_opt.setTitle(self.tr("Auto-Mosaic Options"))
        self.lbl_max_scenes.setText(self.tr("Scene limit:"))
        self.lbl_preference.setText(self.tr("Priority:"))
        self.chk_export_tif.setText(self.tr("Export GeoTIFF"))
        self.lbl_tif_file.setText(self.tr("File:"))
        self.lbl_compress.setText(self.tr("Compress:"))
        self.lbl_selected_scenes.setText(self.tr("Selected scenes:"))
        self.tableMosaic.setHorizontalHeaderLabels(
            [self.tr("Date"), self.tr("Clouds"), self.tr("Scene ID")]
        )
        self.btn_run_mosaic.setText(self.tr("Generate Mosaic"))

    def _update_satellite_params(self):
        if "Sentinel" in self.comboBox_satelite.currentText():
            self._collection   = "sentinel-2-l2a"
            self._compositions = SENTINEL2_COMPOSITIONS.copy()
        else:
            self._collection   = "landsat-c2-l2"
            self._compositions = LANDSAT_COMPOSITIONS.copy()
        self.comboBox_composicao.clear()
        self.comboBox_composicao.addItems(list(self._compositions.keys()))

    def _load_extent(self):
        canvas  = iface.mapCanvas()
        extent  = canvas.extent()
        src_crs = canvas.mapSettings().destinationCrs()
        tgt_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        if src_crs != tgt_crs:
            xform  = QgsCoordinateTransform(src_crs, tgt_crs, QgsProject.instance())
            extent = xform.transformBoundingBox(extent)
        self.sp_west.setValue(round(extent.xMinimum(), 6))
        self.sp_south.setValue(round(extent.yMinimum(), 6))
        self.sp_east.setValue(round(extent.xMaximum(), 6))
        self.sp_north.setValue(round(extent.yMaximum(), 6))

    def _current_bbox(self):
        return [
            self.sp_west.value(), self.sp_south.value(),
            self.sp_east.value(), self.sp_north.value(),
        ]

    def popular_tabela(self):
        if hasattr(self, "_search_worker") and self._search_worker and self._search_worker.isRunning():
            try:
                self._search_worker.search_done.disconnect()
                self._search_worker.search_error.disconnect()
            except:
                pass

        bbox = self._current_bbox()
        self.btn_listar.setText(self.tr("Searching…"))
        self.btn_listar.setEnabled(False)
        self._search_worker = SearchWorker(
            "https://planetarycomputer.microsoft.com/api/stac/v1",
            self._collection, bbox,
            self.dateEdit_inicio.date().toString("yyyy-MM-dd"),
            self.dateEdit_final.date().toString("yyyy-MM-dd"),
            self.slider_clouds.value(),
            parent=self,
        )
        self._search_worker.search_done.connect(self._on_search_done)
        self._search_worker.search_error.connect(self._on_search_error)
        self._search_worker.start()

    def _on_search_done(self, items):
        self.btn_listar.setText(self.tr("🔍 Search available images"))
        self.btn_listar.setEnabled(True)
        self.last_items = items
        self.tableWidget.setRowCount(0)
        for idx, item in enumerate(items):
            self.tableWidget.insertRow(idx)
            cc = item.properties.get("eo:cloud_cover", 0)
            self.tableWidget.setItem(idx, 0, QtWidgets.QTableWidgetItem(str(idx + 1)))
            self.tableWidget.setItem(
                idx, 1, QtWidgets.QTableWidgetItem(
                    item.properties.get("datetime", "N/A")[:10]
                )
            )
            self.tableWidget.setItem(idx, 2, QtWidgets.QTableWidgetItem(f"{cc:.1f}%"))
            self.tableWidget.setItem(idx, 3, QtWidgets.QTableWidgetItem(item.id))
        self.tableWidget.resizeColumnsToContents()
        self._reset_preview()

    def _on_search_error(self, err):
        self.btn_listar.setText(self.tr("🔍 Search available images"))
        self.btn_listar.setEnabled(True)
        iface.messageBar().pushMessage(self.tr("Search error"), err, level=MsgLevel.Critical)

    def _on_table_row_clicked(self, row):
        if row < 0 or row >= len(self.last_items):
            return
        
        # Immediate UI feedback for metadata
        item = self.last_items[row]
        self.lbl_thumb_date.setText(item.properties.get("datetime", "N/A")[:10])
        cc = item.properties.get("eo:cloud_cover", 0)
        self.lbl_thumb_clouds.setText(f"{cc:.1f}%")
        self.lbl_thumb_id.setText(item.id)
        self.lbl_thumb_id.setToolTip(item.id)
        
        # Debounce the thumbnail download (250ms delay)
        self._current_thumb_row = row
        self._thumb_timer.start(250)
        
        if self.btn_show_footprint.isChecked():
            self._draw_footprint(row)

    def _do_debounced_thumbnail(self):
        row = getattr(self, "_current_thumb_row", -1)
        if row < 0 or row >= len(self.last_items):
            return
            
        item = self.last_items[row]
        asset = item.assets.get("rendered_preview")
        if not asset:
            self.lbl_thumbnail.setText(self.tr("No preview available"))
            self.lbl_thumbnail.setPixmap(QPixmap())
            return

        self.lbl_thumbnail.setText(self.tr("Loading…"))
        
        # Safety: disconnect old signals and let them finish in background 
        # instead of terminate() + wait() which blocks the main thread.
        if self._thumb_worker and self._thumb_worker.isRunning():
            try:
                self._thumb_worker.thumbnail_ready.disconnect()
                self._thumb_worker.failed.disconnect()
            except:
                pass

        self._thumb_worker = ThumbnailWorker(asset.href, parent=self)
        self._thumb_worker.thumbnail_ready.connect(self._show_thumbnail)
        self._thumb_worker.failed.connect(
            lambda msg: self.lbl_thumbnail.setText(f"⚠ {msg}")
        )
        self._thumb_worker.start()

    def _on_selection_changed(self):
        rows = self.tableWidget.selectionModel().selectedRows()
        if rows:
            self._on_table_row_clicked(rows[0].row())

    def _load_thumbnail(self, row):
        # This method is now handled by _on_table_row_clicked + timer
        pass

    def _show_thumbnail(self, pixmap):
        self.lbl_thumbnail.setText("")
        self.lbl_thumbnail.setPixmap(pixmap)

    def _reset_preview(self):
        self.lbl_thumbnail.setText(self.tr("Select an image to preview"))
        self.lbl_thumbnail.setPixmap(QPixmap())
        self.lbl_thumb_date.setText("")
        self.lbl_thumb_clouds.setText("")
        self.lbl_thumb_id.setText("")

    def _toggle_footprint(self, checked):
        if checked:
            rows = self.tableWidget.selectionModel().selectedRows()
            if rows:
                self._draw_footprint(rows[0].row())
        else:
            self._clear_rubber_band()

    def _draw_footprint(self, row):
        if row < 0 or row >= len(self.last_items):
            return
        self._clear_rubber_band()
        item = self.last_items[row]
        
        try:
            import json
            geom_dict = item.geometry
            if not geom_dict:
                return
                
            # QgsJsonUtils.geometryFromGeoJson is the way in QGIS 3/4
            qgs_geom = QgsJsonUtils.geometryFromGeoJson(json.dumps(geom_dict))
            
            if not qgs_geom or qgs_geom.isEmpty():
                return

            src_crs = QgsCoordinateReferenceSystem("EPSG:4326")
            dst_crs = iface.mapCanvas().mapSettings().destinationCrs()
            if src_crs != dst_crs:
                xform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
                qgs_geom.transform(xform)

            # Safely get Polygon geometry type for RubberBand
            try:
                # QGIS 4/Qt6 way
                poly_type = Qgis.GeometryType.Polygon
            except AttributeError:
                # QGIS 3 way
                poly_type = 2
                
            rb = QgsRubberBand(iface.mapCanvas(), poly_type) 
            rb.setColor(QColor(137, 180, 250, 160))
            rb.setFillColor(QColor(137, 180, 250, 40))
            rb.setWidth(2)
            rb.setToGeometry(qgs_geom, None)
            rb.show()
            self._rubber_band = rb
        except Exception as e:
            QgsMessageLog.logMessage(f"Footprint error: {str(e)}", "QuickVRT", MsgLevel.Warning)

    def _clear_rubber_band(self):
        if self._rubber_band:
            try:
                self._rubber_band.reset()
            except:
                iface.mapCanvas().scene().removeItem(self._rubber_band)
            self._rubber_band = None

    def process_stac_load(self):
        rows = self.tableWidget.selectionModel().selectedRows()
        if not rows:
            return
        
        items = [self.last_items[r.row()] for r in rows]

        comp_name = self.comboBox_composicao.currentText()
        bands     = self._compositions.get(comp_name, [])
        export    = self.grp_export_browser.isChecked()
        out_tif   = self.le_tif_browser.text().strip()

        if export and not out_tif:
            QtWidgets.QMessageBox.warning(
                self, self.tr("Warning"), self.tr("Please provide the output .tif path.")
            )
            return

        if export:
            # For export, we only support one item at a time or use the first selected
            item = items[0]
            params = {
                "bbox": self._current_bbox(),
                "start_date": self.dateEdit_inicio.date().toString("yyyy-MM-dd"),
                "end_date":   self.dateEdit_final.date().toString("yyyy-MM-dd"),
                "collection": self._collection,
                "bands": bands,
                "max_cloud":  self.slider_clouds.value(),
                "max_items":  1,
                "preference": "N/A",
                "nodata": 0,
                "export_tif":   True,
                "out_tif_path": out_tif,
                "compress":     self.cb_compress_browser.currentText(),
                "items_list":   [item],
            }
            self.btn_carregar.setEnabled(False)
            self._start_mosaic_worker(params, re_enable=[self.btn_carregar])
        else:
            self.btn_carregar.setEnabled(False)
            # VrtWorker now handles a list of items
            self._vrt_worker = VrtWorker(items, bands, self._collection, parent=self)
            self._vrt_worker.vrt_ready.connect(self._on_vrt_ready)
            self._vrt_worker.vrt_error.connect(self._on_vrt_error)
            self._vrt_worker.load_progress.connect(self.browser_progress.setValue)
            self._vrt_worker.finished.connect(self._on_vrt_finished)
            self.browser_progress.setValue(0)
            self.browser_progress.setVisible(True)
            self._vrt_worker.start()

    def _on_vrt_ready(self, vrt_path, layer_name):
        layer = QgsRasterLayer(vrt_path, layer_name)
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)

    def _on_vrt_finished(self):
        self.btn_carregar.setEnabled(True)
        self.browser_progress.setValue(100)
        self.browser_progress.setVisible(False)

    def _on_vrt_error(self, err):
        # We don't re-enable btn_carregar here because _on_vrt_finished 
        # will be called anyway when the worker thread ends.
        iface.messageBar().pushMessage(self.tr("Load error"), err, level=MsgLevel.Critical)

    def _run_mosaic_selected(self):
        rows = self.tableWidget.selectionModel().selectedRows()
        if not rows:
            return
        items   = [self.last_items[r.row()] for r in rows]
        export  = self.grp_export_browser.isChecked()
        out_tif = self.le_tif_browser.text().strip()
        if export and not out_tif:
            QtWidgets.QMessageBox.warning(
                self, self.tr("Warning"), self.tr("Please provide the output .tif path.")
            )
            return
        params = {
            "bbox": self._current_bbox(),
            "start_date": self.dateEdit_inicio.date().toString("yyyy-MM-dd"),
            "end_date":   self.dateEdit_final.date().toString("yyyy-MM-dd"),
            "collection": self._collection,
            "bands": self._compositions.get(self.comboBox_composicao.currentText(), []),
            "max_cloud":  self.slider_clouds.value(),
            "max_items":  len(items),
            "preference": "Manual",
            "nodata": 0,
            "export_tif":   export,
            "out_tif_path": out_tif,
            "compress":     self.cb_compress_browser.currentText(),
            "items_list":   items,
        }
        self.btn_mosaic_selected.setEnabled(False)
        self.tabs.setCurrentIndex(1)
        self._start_mosaic_worker(params, re_enable=[self.btn_mosaic_selected])

    def _run_mosaic(self):
        export  = self.chk_export_tif.isChecked()
        out_tif = self.le_tif.text().strip()
        if export and not out_tif:
            QtWidgets.QMessageBox.warning(
                self, self.tr("Warning"), self.tr("Please provide the output .tif path.")
            )
            return
        params = {
            "bbox": self._current_bbox(),
            "start_date": self.dateEdit_inicio.date().toString("yyyy-MM-dd"),
            "end_date":   self.dateEdit_final.date().toString("yyyy-MM-dd"),
            "collection": self._collection,
            "bands": self._compositions.get(self.comboBox_composicao.currentText(), []),
            "max_cloud":  self.slider_clouds.value(),
            "max_items":  self.sp_items.value(),
            "preference": self.cb_preference.currentText(),
            "nodata": 0,
            "export_tif":   export,
            "out_tif_path": out_tif,
            "compress":     self.cb_compress.currentText(),
        }
        self.tableMosaic.setRowCount(0)
        self.log_panel.clear()
        self.btn_run_mosaic.setEnabled(False)
        self.mosaic_progress.setVisible(True)
        self._start_mosaic_worker(params, re_enable=[self.btn_run_mosaic])

    def _start_mosaic_worker(self, params, re_enable=None):
        if hasattr(self, "_mosaic_worker") and self._mosaic_worker and self._mosaic_worker.isRunning():
            self._mosaic_worker.terminate()
            self._mosaic_worker.wait()

        self._pending_re_enable = re_enable or []
        self._mosaic_worker = MosaicWorker(params)
        self._mosaic_worker.progress.connect(self._on_mosaic_progress)
        self._mosaic_worker.progress_pct.connect(self._on_mosaic_pct)
        self._mosaic_worker.item_selected.connect(self._on_mosaic_item_selected)
        self._mosaic_worker.finished.connect(self._on_mosaic_finished)
        self._mosaic_worker.error.connect(self._on_mosaic_error)
        self._mosaic_worker.start()

    def _on_mosaic_pct(self, pct):
        self.mosaic_progress.setRange(0, 100)
        self.mosaic_progress.setValue(pct)

    def _on_mosaic_progress(self, msg):
        iface.mainWindow().statusBar().showMessage(msg.replace("\n", " "), 4000)
        self.log_panel.append(msg)
        sb = self.log_panel.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_mosaic_item_selected(self, dt, clouds, item_id):
        row = self.tableMosaic.rowCount()
        self.tableMosaic.insertRow(row)
        self.tableMosaic.setItem(row, 0, QtWidgets.QTableWidgetItem(dt))
        self.tableMosaic.setItem(row, 1, QtWidgets.QTableWidgetItem(clouds))
        self.tableMosaic.setItem(row, 2, QtWidgets.QTableWidgetItem(item_id))
        self.tableMosaic.scrollToBottom()

    def _on_mosaic_finished(self, vrt, tif):
        self._re_enable_buttons()
        self.mosaic_progress.setRange(0, 0)
        self.mosaic_progress.setVisible(False)
        comp = self.comboBox_composicao.currentText()
        layer = QgsRasterLayer(vrt, self.tr("Mosaic – {}").format(comp))
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
        if tif:
            lt = QgsRasterLayer(tif, self.tr("GeoTIFF – {}").format(comp))
            if lt.isValid():
                QgsProject.instance().addMapLayer(lt)
        iface.messageBar().pushMessage(
            self.tr("Success"), self.tr("Mosaic ready."), level=MsgLevel.Success
        )

    def _on_mosaic_error(self, err):
        self._re_enable_buttons()
        self.mosaic_progress.setRange(0, 0)
        self.mosaic_progress.setVisible(False)
        self.log_panel.append(f"\n❌ ERROR:\n{err}")
        QtWidgets.QMessageBox.critical(self, self.tr("Mosaic error"), err[:800])

    def _re_enable_buttons(self):
        for btn in self._pending_re_enable:
            btn.setEnabled(True)
        self._pending_re_enable = []

    def _browse_tif(self, line_edit):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, self.tr("Save GeoTIFF"), "", "GeoTIFF (*.tif *.tiff)"
        )
        if path:
            if not path.lower().endswith((".tif", ".tiff")):
                path += ".tif"
            line_edit.setText(path)

    @staticmethod
    def _make_coord_spin(lo, hi):
        sp = QtWidgets.QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setDecimals(6)
        sp.setSingleStep(0.1)
        sp.setFixedWidth(90)
        sp.setStyleSheet("font-size: 8pt;")
        return sp

    @staticmethod
    def _meta_label():
        lbl = QtWidgets.QLabel()
        lbl.setAlignment(_AlignCenter)
        lbl.setStyleSheet("color: #cdd6f4; font-size: 9pt;")
        return lbl

    def closeEvent(self, event):
        self._clear_rubber_band()
        super().closeEvent(event)