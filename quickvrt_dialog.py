# -*- coding: utf-8 -*-
"""
QuickVRTDialog - UI layer for Quick VRT Imagery Loader.
"""

import os
from datetime import date, timedelta

from qgis.PyQt import QtWidgets, QtCore
from qgis.PyQt.QtCore import Qt, QCoreApplication, QSize, QSettings, pyqtSignal, QThread
from qgis.PyQt.QtGui import QPixmap, QFont, QColor, QIcon, QPalette
from qgis.core import (
    QgsRasterLayer, QgsProject, QgsCoordinateTransform,
    QgsCoordinateReferenceSystem, Qgis, QgsMessageLog,
    QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
    QgsRectangle, QgsWkbTypes,
    QgsColorRampShader, QgsRasterShader, QgsSingleBandPseudoColorRenderer
)
from qgis.gui import QgsRubberBand
from qgis.utils import iface

from .indices import create_derived_vrt
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
    _AlignTop      = Qt.AlignmentFlag.AlignTop
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
    _TextBrowserInteraction = Qt.TextInteractionFlag.TextBrowserInteraction
    _PointingHand  = Qt.CursorShape.PointingHandCursor
    _ResizeEvent   = QtCore.QEvent.Type.Resize
else:
    _AlignCenter   = Qt.AlignCenter
    _AlignRight    = Qt.AlignRight
    _AlignTop      = Qt.AlignTop
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
    _TextBrowserInteraction = Qt.TextBrowserInteraction
    _PointingHand  = Qt.PointingHandCursor
    _ResizeEvent   = QtCore.QEvent.Resize

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

# Predefined band combinations and indices for Sentinel-2 and Landsat.
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
    "NDVI (Normalized Difference Vegetation)": {"bands": ["B08", "B04"], "formula": "ndvi"},
    "EVI (Enhanced Vegetation Index)":         {"bands": ["B08", "B04", "B02"], "formula": "evi"},
    "NDWI (Normalized Difference Water)":      {"bands": ["B03", "B08"], "formula": "ndwi"},
    "NDMI (Normalized Difference Moisture)":   {"bands": ["B08", "B11"], "formula": "ndmi"},
}

LANDSAT_COMPOSITIONS = {
    "True Color (R, G, B)":                    ["red", "green", "blue"],
    "False Color NIR (NIR, R, G)":             ["nir08", "red", "green"],
    "Agriculture (SWIR1, NIR, B)":             ["swir16", "nir08", "blue"],
    "Healthy Vegetation (SWIR1, NIR, R)":      ["swir16", "nir08", "red"],
    "Geology (SWIR2, SWIR1, B)":               ["swir22", "swir16", "blue"],
    "Urban / Soil (SWIR2, SWIR1, R)":          ["swir22", "swir16", "red"],
    "Bathymetric (G, R, Coastal)":             ["green", "red", "coastal"],
    "Water Bodies (G, NIR, SWIR1)":            ["green", "nir08", "swir16"],
    "Wildfires (SWIR2, NIR, R)":               ["swir22", "nir08", "red"],
    "Burn Area (SWIR2, SWIR1, NIR)":           ["swir22", "swir16", "nir08"],
    "Atmospheric Penetration (SWIR2, SWIR1)":  ["swir22", "swir16", "nir08"],
    "Snow / Ice (R, G, NIR)":                  ["red", "green", "nir08"],
    "NDVI (Normalized Difference Vegetation)": {"bands": ["nir08", "red"], "formula": "ndvi"},
    "EVI (Enhanced Vegetation Index)":         {"bands": ["nir08", "red", "blue"], "formula": "evi"},
    "NDWI (Normalized Difference Water)":      {"bands": ["green", "nir08"], "formula": "ndwi"},
    "NDMI (Normalized Difference Moisture)":   {"bands": ["nir08", "swir16"], "formula": "ndmi"},
}

# CBERS band compositions (INPE / Brazil Data Cube STAC). Band roles verified
# against live item assets: MUX/WFI Surface Reflectance share the same
# blue/green/red/nir layout (only the band numbers shift), WPM adds a
# panchromatic band, and PAN10M has no blue band at all.
CBERS_MUX_COMPOSITIONS = {
    "True Color (B7, B6, B5)":                 ["BAND7", "BAND6", "BAND5"],
    "False Color Infrared (B8, B7, B6)":       ["BAND8", "BAND7", "BAND6"],
    "NDVI (Normalized Difference Vegetation)": {"bands": ["BAND8", "BAND7"], "formula": "ndvi"},
    "NDWI (Normalized Difference Water)":      {"bands": ["BAND6", "BAND8"], "formula": "ndwi"},
}

CBERS_WFI_COMPOSITIONS = {
    "True Color (B15, B14, B13)":              ["BAND15", "BAND14", "BAND13"],
    "False Color Infrared (B16, B15, B14)":    ["BAND16", "BAND15", "BAND14"],
    "NDVI (Normalized Difference Vegetation)": {"bands": ["BAND16", "BAND15"], "formula": "ndvi"},
    "NDWI (Normalized Difference Water)":      {"bands": ["BAND14", "BAND16"], "formula": "ndwi"},
}

CBERS_WPM_COMPOSITIONS = {
    "True Color (B3, B2, B1) 8m":              ["BAND3", "BAND2", "BAND1"],
    "False Color Infrared (B4, B3, B2) 8m":    ["BAND4", "BAND3", "BAND2"],
    "Panchromatic (B0) 2m":                    ["BAND0"],
    "NDVI (Normalized Difference Vegetation)": {"bands": ["BAND4", "BAND3"], "formula": "ndvi"},
    "NDWI (Normalized Difference Water)":      {"bands": ["BAND2", "BAND4"], "formula": "ndwi"},
}

# The PCA-fused product ships a single pre-pansharpened 3-band RGB asset
# ("tci"), not separate single-band files, so its only "composition" is that
# asset itself.
CBERS_WPM_FUSED_COMPOSITIONS = {
    "True Color Pansharpened (TCI) 2m": ["tci"],
}

CBERS_PAN10M_COMPOSITIONS = {
    "Color Infrared (B4, B3, B2) 10m":         ["BAND4", "BAND3", "BAND2"],
    "NDVI (Normalized Difference Vegetation)": {"bands": ["BAND4", "BAND3"], "formula": "ndvi"},
}

CBERS_PAN5M_COMPOSITIONS = {
    "Panchromatic (B1) 5m": ["BAND1"],
}

# Earth Search (Element84 / AWS) serves Sentinel-2 with common-name asset
# keys (red/green/blue/nir/...) over public HTTPS. Its Landsat collection is
# NOT included: those assets live in the requester-pays usgs-landsat S3
# bucket and fail without AWS credentials.
EARTHSEARCH_S2_COMPOSITIONS = {
    "True Color (red, green, blue)":           ["red", "green", "blue"],
    "False Color NIR (nir, red, green)":       ["nir", "red", "green"],
    "False Color SWIR (swir22, nir, red)":     ["swir22", "nir", "red"],
    "Agriculture (swir16, nir, blue)":         ["swir16", "nir", "blue"],
    "Healthy Vegetation (nir08, swir16, blue)": ["nir08", "swir16", "blue"],
    "Red Edge / Stress (nir, nir08, red)":     ["nir", "nir08", "red"],
    "Vegetation / Biomass (nir, swir16, red)": ["nir", "swir16", "red"],
    "Geology (swir22, swir16, blue)":          ["swir22", "swir16", "blue"],
    "Urban / Soil (swir22, swir16, red)":      ["swir22", "swir16", "red"],
    "Bathymetric (red, green, coastal)":       ["red", "green", "coastal"],
    "Water Bodies (green, nir, swir16)":       ["green", "nir", "swir16"],
    "Burn Area (swir22, nir08, red)":          ["swir22", "nir08", "red"],
    "Atmospheric Penetration (swir22, swir16, nir08)": ["swir22", "swir16", "nir08"],
    "Snow / Ice (red, green, nir)":            ["red", "green", "nir"],
    "NDVI (Normalized Difference Vegetation)": {"bands": ["nir", "red"], "formula": "ndvi"},
    "EVI (Enhanced Vegetation Index)":         {"bands": ["nir", "red", "blue"], "formula": "evi"},
    "NDWI (Normalized Difference Water)":      {"bands": ["green", "nir"], "formula": "ndwi"},
    "NDMI (Normalized Difference Moisture)":   {"bands": ["nir", "swir16"], "formula": "ndmi"},
}

# STAC providers available to the user. Each satellite entry carries
# everything downstream code needs to stay provider-agnostic: which
# collection to query, how to build compositions, whether assets need
# Planetary Computer signing, the nodata value for mosaic warping, and a
# short prefix used when naming loaded layers.
STAC_PROVIDERS = [
    {
        "name": "Microsoft Planetary Computer",
        "url": "https://planetarycomputer.microsoft.com/api/stac/v1",
        "needs_signing": True,
        "thumbnail_asset": "rendered_preview",
        "satellites": [
            {
                "label": "Sentinel-2 L2A", "collection": "sentinel-2-l2a",
                "compositions": SENTINEL2_COMPOSITIONS, "prefix": "S2", "nodata": 0,
            },
            {
                "label": "Landsat Collection 2 Level-2", "collection": "landsat-c2-l2",
                "compositions": LANDSAT_COMPOSITIONS, "prefix": "LS", "nodata": 0,
            },
        ],
    },
    {
        "name": "Element84 Earth Search (AWS)",
        "url": "https://earth-search.aws.element84.com/v1",
        "needs_signing": False,
        "thumbnail_asset": "thumbnail",
        "satellites": [
            {
                "label": "Sentinel-2 L2A", "collection": "sentinel-2-l2a",
                "compositions": EARTHSEARCH_S2_COMPOSITIONS, "prefix": "S2", "nodata": 0,
            },
        ],
    },
    {
        "name": "INPE / Brazil Data Cube (CBERS)",
        "url": "https://data.inpe.br/bdc/stac/v1",
        "needs_signing": False,
        "thumbnail_asset": "thumbnail",
        "satellites": [
            {
                "label": "CBERS-4 MUX (20m)", "collection": "CB4-MUX-L4-SR-1",
                "compositions": CBERS_MUX_COMPOSITIONS, "prefix": "CB4-MUX", "nodata": -9999,
            },
            {
                "label": "CBERS-4A MUX (16.5m)", "collection": "CB4A-MUX-L4-SR-1",
                "compositions": CBERS_MUX_COMPOSITIONS, "prefix": "CB4A-MUX", "nodata": -9999,
            },
            {
                "label": "CBERS-4 WFI (64m)", "collection": "CB4-WFI-L4-SR-1",
                "compositions": CBERS_WFI_COMPOSITIONS, "prefix": "CB4-WFI", "nodata": -9999,
            },
            {
                "label": "CBERS-4A WFI (55m)", "collection": "CB4A-WFI-L4-SR-1",
                "compositions": CBERS_WFI_COMPOSITIONS, "prefix": "CB4A-WFI", "nodata": -9999,
            },
            {
                "label": "CBERS-4A WPM Multispectral + Pan (8m/2m)", "collection": "CB4A-WPM-L4-DN-1",
                "compositions": CBERS_WPM_COMPOSITIONS, "prefix": "CB4A-WPM", "nodata": 0,
            },
            {
                "label": "CBERS-4A WPM Pansharpened True Color (2m)", "collection": "CB4A-WPM-PCA-FUSED-1",
                "compositions": CBERS_WPM_FUSED_COMPOSITIONS, "prefix": "CB4A-WPM-TCI", "nodata": 0,
            },
            {
                "label": "CBERS-4 PAN10M Multispectral (10m)", "collection": "CB4-PAN10M-L4-DN-1",
                "compositions": CBERS_PAN10M_COMPOSITIONS, "prefix": "CB4-PAN10M", "nodata": 0,
            },
            {
                "label": "CBERS-4 PAN5M Panchromatic (5m)", "collection": "CB4-PAN5M-L4-DN-1",
                "compositions": CBERS_PAN5M_COMPOSITIONS, "prefix": "CB4-PAN5M", "nodata": 0,
            },
        ],
    },
]

# ── Generoso defaults ────────────────────────────────────────────────────────
_DEFAULT_DAYS_BACK  = 180   # janela de busca padrão: 6 meses
_DEFAULT_MAX_CLOUDS = 40    # nuvens: até 40 %
_DEFAULT_MAX_SCENES = 50    # limite de cenas no Auto-Mosaic
_SEARCH_PAGE_SIZE   = 200   # Browser: cenas por página de busca ("load more")
# ─────────────────────────────────────────────────────────────────────────────


def _cloud_cover(properties):
    """eo:cloud_cover is an explicit null for CBERS DN products (WPM, PAN10M,
    PAN5M, WPM pansharpened TCI) since INPE doesn't compute it for them."""
    return properties.get("eo:cloud_cover")


def _passes_cloud_filter(properties, max_clouds):
    cc = _cloud_cover(properties)
    return cc is None or cc <= max_clouds


def _format_clouds(properties):
    cc = _cloud_cover(properties)
    return "N/A" if cc is None else f"{cc:.1f}%"


def _summarize_error(text):
    """STAC/HTTP failures (e.g. a CDN's 502/504 gateway page) sometimes
    surface as a full HTML error page in the exception text. Collapse that
    into a short, readable summary instead of dumping raw markup in the UI."""
    stripped = text.strip()
    lower = stripped.lower()
    if not (lower.startswith("<!doctype") or lower.startswith("<html") or "<html" in lower[:200]):
        return text
    import re
    status_match = re.search(r"<h1>\s*(\d{3})\s*</h1>", text, re.IGNORECASE)
    title_match = re.search(r"<title>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
    status = status_match.group(1) if status_match else None
    title = title_match.group(1).strip() if title_match else None
    if status and title:
        return f"Server error {status}: {title}. The remote service may be temporarily unavailable - please try again shortly."
    if title:
        return f"Server error: {title}. The remote service may be temporarily unavailable - please try again shortly."
    return "The remote server returned an error page. It may be temporarily unavailable - please try again shortly."

class ThumbnailWorker(QThread):
    thumbnail_ready = pyqtSignal(QPixmap)
    failed          = pyqtSignal(str)

    def __init__(self, url, parent=None):
        super().__init__(parent)
        self.url = url

    def run(self):
        try:
            import urllib.parse
            import urllib.request

            parsed = urllib.parse.urlparse(self.url)
            if parsed.scheme != "https" or not parsed.netloc:
                self.failed.emit("Invalid URL")
                return

            req = urllib.request.Request(
                self.url, headers={"User-Agent": "QuickVRTImageryLoader/0.7"}
            )
            with urllib.request.urlopen(req, timeout=12) as resp:  # nosec B310
                data = resp.read()
            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                self.failed.emit("Could not decode image")
                return
            self.thumbnail_ready.emit(pixmap)
        except Exception as e:
            self.failed.emit(str(e)[:80])


class SearchWorker(QThread):
    search_done  = pyqtSignal(list, bool)
    search_error = pyqtSignal(str)

    def __init__(self, catalog_url, collection, bbox,
                 start_date, end_date, max_clouds, max_results=200, parent=None):
        super().__init__(parent)
        self.catalog_url = catalog_url
        self.collection  = collection
        self.bbox        = bbox
        self.start_date  = start_date
        self.end_date    = end_date
        self.max_clouds  = max_clouds
        self.max_results = max_results

    def run(self):
        import pystac_client

        attempts = 3
        last_err = None
        for attempt in range(attempts):
            if self.isInterruptionRequested():
                return
            try:
                # Fail fast rather than waiting out a CDN's own ~30s gateway
                # timeout on every attempt - our retry loop handles the rest.
                catalog = pystac_client.Client.open(self.catalog_url, timeout=(10, 20))
                search  = catalog.search(
                    collections=[self.collection],
                    bbox=self.bbox,
                    datetime=f"{self.start_date}/{self.end_date}",
                    max_items=self.max_results,
                )
                items = list(search.items())
                if self.isInterruptionRequested():
                    return
                # A wide bbox over a long period can match thousands of
                # scenes; the fetch is capped, and the caller offers "load
                # more" when the server reports additional matches.
                try:
                    matched = search.matched()
                except Exception:
                    matched = None
                has_more = bool(matched and matched > len(items))
                # Unknown cloud cover sorts last but is never filtered out - some
                # collections (CBERS DN products) simply don't compute it.
                items = sorted(
                    items,
                    key=lambda x: (
                        _cloud_cover(x.properties)
                        if _cloud_cover(x.properties) is not None else 101
                    ),
                )
                items = [
                    i for i in items
                    if _passes_cloud_filter(i.properties, self.max_clouds)
                ]
                self.search_done.emit(items, has_more)
                return
            except Exception as e:
                last_err = e
                # Transient outages (e.g. Azure Front Door 504s on Planetary
                # Computer) are common; retry a couple times before giving up.
                if attempt < attempts - 1:
                    self.msleep(4000)
        if not self.isInterruptionRequested():
            self.search_error.emit(_summarize_error(str(last_err)))


class VrtWorker(QThread):
    vrt_ready     = pyqtSignal(str, str)
    vrt_error     = pyqtSignal(str)
    load_progress = pyqtSignal(int)

    def __init__(self, items, bands, collection, formula=None, needs_signing=True,
                 prefix="", parent=None):
        super().__init__(parent)
        self.items         = items if isinstance(items, list) else [items]
        self.bands         = bands
        self.collection    = collection
        self.formula       = formula
        self.needs_signing = needs_signing
        self.prefix        = prefix

    def run(self):
        try:
            import processing
            from osgeo import gdal
            import tempfile
            from pathlib import Path

            sign_href = None
            if self.needs_signing:
                import planetary_computer
                sign_href = planetary_computer.sign

            # Boost GDAL network resilience for /vsicurl/
            gdal.SetConfigOption("GDAL_HTTP_MAX_RETRY", "10")
            gdal.SetConfigOption("GDAL_HTTP_RETRY_DELAY", "1")
            gdal.SetConfigOption("VSI_CACHE", "TRUE")
            gdal.SetConfigOption("GDAL_HTTP_TIMEOUT", "60")
            gdal.SetConfigOption("GDAL_HTTP_MERGE_CONSECUTIVE_HTTP_RETRIEVALS", "YES")
            # Some CBERS assets (e.g. WPM panchromatic, ~2GB, one strip per
            # pixel row, no overviews) are extremely inefficient to stream at
            # GDAL's tiny 16KB default chunk size - thousands of round trips.
            # A larger chunk/cache lets GDAL coalesce many strip reads into few requests.
            gdal.SetConfigOption("CPL_VSIL_CURL_CHUNK_SIZE", "1048576")
            gdal.SetConfigOption("VSI_CACHE_SIZE", "67108864")

            total = len(self.items)
            for i, item in enumerate(self.items):
                try:
                    band_hrefs = []
                    for band in self.bands:
                        asset = item.assets.get(band)
                        if asset:
                            href = sign_href(asset.href) if sign_href else asset.href
                            band_hrefs.append(f"/vsicurl/{href}")

                    if not band_hrefs:
                        self.vrt_error.emit(f"Item {item.id}: No valid bands")
                        continue

                    if len(band_hrefs) != len(self.bands):
                        self.vrt_error.emit(
                            f"Item {item.id}: Only {len(band_hrefs)}/{len(self.bands)} requested bands are available"
                        )
                        continue

                    # If we have a formula, we need to build a multi-band VRT and then a derived one
                    if self.formula:
                        tmp_dir = Path(tempfile.mkdtemp(prefix="qgis_vrt_"))
                        stack_vrt = str(tmp_dir / "stack.vrt")
                        stack_ds = gdal.BuildVRT(
                            stack_vrt,
                            band_hrefs,
                            options=gdal.BuildVRTOptions(
                                separate=True,
                                resolution="highest",
                                resampleAlg="bilinear",
                            ),
                        )
                        if stack_ds is None:
                            raise RuntimeError("Could not build source stack VRT")
                        stack_ds.FlushCache()
                        stack_ds = None

                        derived_vrt = str(tmp_dir / "derived.vrt")
                        create_derived_vrt(stack_vrt, derived_vrt, self.formula)
                        test_ds = gdal.Open(derived_vrt)
                        if test_ds is None:
                            raise RuntimeError("Could not open derived index VRT")
                        test_ds = None
                        output_path = derived_vrt
                    else:
                        # Only stack as distinct bands when we have more than one
                        # single-band source; a lone asset (e.g. a pre-fused
                        # multi-band TCI) must pass through with its native bands.
                        result = processing.run(
                            "gdal:buildvirtualraster",
                            {
                                "INPUT": band_hrefs,
                                "SEPARATE": len(band_hrefs) > 1,
                                "OUTPUT": "TEMPORARY_OUTPUT",
                            },
                        )
                        output_path = result["OUTPUT"]

                    prefix     = f"{self.prefix}_" if self.prefix else ""
                    layer_name = f"{prefix}{item.id} (clouds: {_format_clouds(item.properties)})"
                    if self.formula:
                        layer_name = f"{self.formula.upper()} - {layer_name}"

                    self.vrt_ready.emit(output_path, layer_name)

                    self.load_progress.emit(int(((i + 1) / total) * 100))
                except Exception as e:
                    self.vrt_error.emit(f"Error loading {item.id}: {str(e)}")
        except Exception as e:
            self.vrt_error.emit(str(e))


class QuickVRTDialog(QtWidgets.QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("QuickVRTDialogBase")
        self.setWindowTitle(self.tr("Quick VRT Imagery Loader"))
        self.setMinimumSize(QSize(760, 560))
        self.setSizeGripEnabled(True)
        self._theme = self._theme_colors()
        self.setStyleSheet(self._build_stylesheet())

        # State
        self._provider      = STAC_PROVIDERS[0]
        self._satellite     = STAC_PROVIDERS[0]["satellites"][0]
        self._collection    = self._satellite["collection"]
        self._compositions  = self._satellite["compositions"].copy()
        self._active_thumbnail_asset = self._provider["thumbnail_asset"]
        self.last_items    = []
        self._thumb_worker  = None
        self._search_worker = None
        self._vrt_worker    = None
        self._mosaic_worker = None
        self._rubber_bands  = []
        self._search_cap    = _SEARCH_PAGE_SIZE
        self._current_thumbnail_pixmap = QPixmap()

        # Debounce timer for thumbnails to avoid freezing during rapid clicking
        self._thumb_timer = QtCore.QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.timeout.connect(self._do_debounced_thumbnail)

        self._build_ui()
        self._retranslate()
        self._connect_signals()
        self._update_provider_params()
        self._restore_settings()
        self._load_extent()
        self._fit_to_available_screen()


    def tr(self, msg):
        # Context kept as the pre-1.0 class name so existing pt/es .qm translations stay matched.
        return QCoreApplication.translate("SentinelSTACDialog", msg)

    @staticmethod
    def _theme_colors():
        """Semantic color tokens for the dark/light stylesheet. Dark stays
        close to Catppuccin Mocha; light is a proper Catppuccin Latte-style
        palette rather than a naive hex swap, so both feel intentionally
        designed instead of one being a patched version of the other."""
        palette = QtWidgets.QApplication.palette()
        window_role = QPalette.ColorRole.Window if _QT6 else QPalette.Window
        is_dark = palette.color(window_role).lightness() < 128
        if is_dark:
            return {
                "bg":             "#1e1e2e",
                "bg_alt":         "#181825",
                "bg_input":       "#313244",
                "bg_deepest":     "#11111b",
                "fg":             "#cdd6f4",
                "muted":          "#a6adc8",
                "border":         "rgba(255, 255, 255, 0.09)",
                "border_strong":  "rgba(255, 255, 255, 0.16)",
                "accent":         "#89b4fa",
                "accent_hover":   "#a5c8fb",
                "accent_pressed": "#74a8f9",
                "accent_text":    "#1e1e2e",
                "danger":         "#f38ba8",
                "danger_text":    "#1e1e2e",
                "log_text":       "#a6e3a1",
            }
        return {
            "bg":             "#eff1f5",
            "bg_alt":         "#ffffff",
            "bg_input":       "#ffffff",
            "bg_deepest":     "#11111b",
            "fg":             "#4c4f69",
            "muted":          "#6c6f85",
            "border":         "rgba(76, 79, 105, 0.14)",
            "border_strong":  "rgba(76, 79, 105, 0.22)",
            "accent":         "#1e66f5",
            "accent_hover":   "#3c7bf6",
            "accent_pressed": "#1552c9",
            "accent_text":    "#ffffff",
            "danger":         "#d20f39",
            "danger_text":    "#ffffff",
            "log_text":       "#a6e3a1",
        }

    def _build_stylesheet(self):
        t = self._theme
        return f"""
            QDialog {{
                background-color: {t['bg']};
                color: {t['fg']};
            }}
            QGroupBox {{
                border: 1px solid {t['border_strong']};
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
                color: {t['fg']};
                font-weight: 600;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
                color: {t['muted']};
            }}
            QLabel {{ color: {t['fg']}; }}
            QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QLineEdit {{
                background-color: {t['bg_input']};
                border: 1px solid {t['border']};
                border-radius: 6px;
                padding: 4px 8px;
                color: {t['fg']};
                selection-background-color: {t['accent']};
                selection-color: {t['accent_text']};
            }}
            QComboBox::drop-down {{ border: none; width: 20px; }}
            QComboBox QAbstractItemView {{
                background-color: {t['bg_input']};
                border: 1px solid {t['border_strong']};
                selection-background-color: {t['accent']};
                selection-color: {t['accent_text']};
                outline: none;
            }}
            QSlider::groove:horizontal {{
                height: 4px;
                background: {t['border_strong']};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                background: {t['accent']};
                width: 14px; height: 14px;
                margin: -5px 0;
                border-radius: 7px;
            }}
            QSlider::sub-page:horizontal {{ background: {t['accent']}; border-radius: 2px; }}
            QTableWidget {{
                background-color: {t['bg_alt']};
                alternate-background-color: {t['bg']};
                gridline-color: transparent;
                border: 1px solid {t['border_strong']};
                border-radius: 8px;
                color: {t['fg']};
            }}
            QHeaderView::section {{
                background-color: transparent;
                color: {t['muted']};
                font-weight: 600;
                border: none;
                border-bottom: 1px solid {t['border_strong']};
                padding: 6px 4px;
            }}
            QTableWidget::item {{ padding: 2px 4px; }}
            QTableWidget::item:selected {{
                background-color: {t['accent']};
                color: {t['accent_text']};
            }}
            QTabWidget::pane {{
                border: 1px solid {t['border_strong']};
                border-radius: 8px;
                background-color: {t['bg']};
                top: -1px;
            }}
            QTabBar::tab {{
                background: transparent;
                color: {t['muted']};
                font-size: 9pt;
                padding: 7px 16px;
                min-width: 86px;
                border-radius: 6px;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{ background: {t['bg_input']}; color: {t['fg']}; font-weight: 600; }}
            QTabBar::tab:hover:!selected {{ color: {t['fg']}; }}
            QPushButton {{
                background-color: {t['bg_input']};
                color: {t['fg']};
                border: 1px solid {t['border']};
                border-radius: 6px;
                padding: 6px 14px;
            }}
            QPushButton:hover    {{ border-color: {t['border_strong']}; }}
            QPushButton:pressed  {{ background-color: {t['border_strong']}; }}
            QPushButton:disabled {{ color: {t['muted']}; border-color: {t['border']}; }}
            QPushButton#btn_primary {{
                background-color: {t['accent']};
                color: {t['accent_text']};
                font-weight: 600;
                border: none;
            }}
            QPushButton#btn_primary:hover   {{ background-color: {t['accent_hover']}; }}
            QPushButton#btn_primary:pressed {{ background-color: {t['accent_pressed']}; }}
            QPushButton#btn_danger {{
                background-color: {t['danger']};
                color: {t['danger_text']};
                font-weight: 600;
                border: none;
            }}
            QPushButton#btn_link {{
                background: transparent;
                border: none;
                color: {t['muted']};
                padding: 2px 6px;
            }}
            QPushButton#btn_link:hover {{ color: {t['accent']}; }}
            QTextEdit {{
                background-color: {t['bg_deepest']};
                color: {t['log_text']};
                font-family: monospace;
                font-size: 9pt;
                border: 1px solid {t['border_strong']};
                border-radius: 8px;
            }}
            QProgressBar {{
                border: 1px solid {t['border']};
                border-radius: 4px;
                background-color: {t['bg_alt']};
                height: 10px;
                text-align: center;
                color: transparent;
            }}
            QProgressBar::chunk {{ background-color: {t['accent']}; border-radius: 3px; }}
            QCheckBox {{ color: {t['fg']}; spacing: 8px; }}
            QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 3px; }}
            QFrame#preview_frame {{
                background-color: {t['bg_alt']};
                border: 1px solid {t['border_strong']};
                border-radius: 8px;
            }}
            QSplitter::handle {{ background-color: transparent; }}
        """

    def _style_text(self, color_key="fg", extra=""):
        color = self._theme[color_key]
        return "color: {};{}".format(color, (" " + extra) if extra else "")

    def prepare_for_open(self):
        self._load_extent()

    _SETTINGS_PREFIX = "quickvrt/"

    def _save_settings(self):
        s = QSettings()
        p = self._SETTINGS_PREFIX
        # Provider is intentionally NOT persisted: the plugin always opens on
        # Microsoft Planetary Computer (the original default the userbase
        # expects). Satellite/composition are also left out since they only
        # make sense within a provider.
        s.setValue(p + "max_clouds",       self.slider_clouds.value())
        s.setValue(p + "scene_limit",      self.sp_items.value())
        s.setValue(p + "preference",       self.cb_preference.currentIndex())
        s.setValue(p + "compress_browser", self.cb_compress_browser.currentText())
        s.setValue(p + "compress_mosaic",  self.cb_compress.currentText())
        s.setValue(p + "tif_browser",      self.le_tif_browser.text())
        s.setValue(p + "tif_mosaic",       self.le_tif.text())

    def _restore_settings(self):
        s = QSettings()
        p = self._SETTINGS_PREFIX

        def restore_combo(combo, key):
            text = s.value(p + key, "", type=str)
            if text:
                idx = combo.findText(text)
                if idx >= 0:
                    combo.setCurrentIndex(idx)

        # Provider/satellite/composition are deliberately not restored: the
        # plugin always opens on Microsoft Planetary Computer with its default
        # collection, matching the behaviour the userbase is used to.
        self.slider_clouds.setValue(s.value(p + "max_clouds", _DEFAULT_MAX_CLOUDS, type=int))
        self.sp_items.setValue(s.value(p + "scene_limit", _DEFAULT_MAX_SCENES, type=int))
        pref_idx = s.value(p + "preference", 0, type=int)
        if 0 <= pref_idx < self.cb_preference.count():
            self.cb_preference.setCurrentIndex(pref_idx)
        restore_combo(self.cb_compress_browser, "compress_browser")
        restore_combo(self.cb_compress, "compress_mosaic")
        self.le_tif_browser.setText(s.value(p + "tif_browser", "", type=str))
        self.le_tif.setText(s.value(p + "tif_mosaic", "", type=str))


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

    def _fit_to_available_screen(self):
        screen = QtWidgets.QApplication.primaryScreen()
        if not screen:
            self.resize(960, 720)
            return

        available = screen.availableGeometry()
        min_w = max(640, min(760, available.width() - 120))
        min_h = max(500, min(560, available.height() - 120))
        target_w = max(min_w, min(1020, available.width() - 80))
        target_h = max(min_h, min(760, available.height() - 80))

        self.setMinimumSize(QSize(min_w, min_h))
        self.resize(target_w, target_h)

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
        self.lbl_title.setStyleSheet(self._style_text("fg"))
        self.lbl_subtitle = QtWidgets.QLabel()
        self.lbl_subtitle.setStyleSheet(self._style_text("muted", "font-size: 9pt;"))
        title_lay.addWidget(self.lbl_title)
        title_lay.addWidget(self.lbl_subtitle)
        lay.addLayout(title_lay)
        lay.addStretch()

        self.btn_credits = QtWidgets.QPushButton()
        self.btn_credits.setObjectName("btn_link")
        self.btn_credits.setCursor(_PointingHand)
        lay.addWidget(self.btn_credits, alignment=_AlignTop)
        return lay

    def _plugin_version(self):
        try:
            meta_path = os.path.join(os.path.dirname(__file__), "metadata.txt")
            with open(meta_path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("version="):
                        return line.split("=", 1)[1].strip()
        except OSError:
            pass
        return ""

    def _show_credits(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(self.tr("Credits"))
        dlg.setStyleSheet(self._build_stylesheet())
        dlg.setFixedWidth(300)

        lay = QtWidgets.QVBoxLayout(dlg)
        lay.setContentsMargins(24, 24, 24, 20)
        lay.setSpacing(8)

        icon_lbl = QtWidgets.QLabel()
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        if os.path.exists(icon_path):
            icon_lbl.setPixmap(QPixmap(icon_path).scaled(48, 48, _KeepAspect, _SmoothTx))
        icon_lbl.setAlignment(_AlignCenter)
        lay.addWidget(icon_lbl)

        title_lbl = QtWidgets.QLabel(self.tr("Quick VRT Imagery Loader"))
        title_lbl.setAlignment(_AlignCenter)
        title_lbl.setWordWrap(True)
        f = QFont()
        f.setPointSize(11)
        f.setBold(True)
        title_lbl.setFont(f)
        lay.addWidget(title_lbl)

        version = self._plugin_version()
        if version:
            version_lbl = QtWidgets.QLabel(self.tr("Version {}").format(version))
            version_lbl.setAlignment(_AlignCenter)
            version_lbl.setStyleSheet(self._style_text("muted", "font-size: 8pt;"))
            lay.addWidget(version_lbl)

        author_lbl = QtWidgets.QLabel(self.tr("Created by Vagner Teixeira"))
        author_lbl.setAlignment(_AlignCenter)
        author_lbl.setStyleSheet(self._style_text("muted"))
        lay.addSpacing(8)
        lay.addWidget(author_lbl)

        link_lbl = QtWidgets.QLabel(
            '<a href="https://github.com/vagnertxr" style="color:{}; text-decoration:none;">'
            'github.com/vagnertxr</a>'.format(self._theme["accent"])
        )
        link_lbl.setAlignment(_AlignCenter)
        link_lbl.setOpenExternalLinks(True)
        link_lbl.setTextInteractionFlags(_TextBrowserInteraction)
        link_lbl.setCursor(_PointingHand)
        lay.addWidget(link_lbl)

        lay.addSpacing(12)
        btn_close = QtWidgets.QPushButton(self.tr("Close"))
        btn_close.setObjectName("btn_primary")
        btn_close.clicked.connect(dlg.accept)
        lay.addWidget(btn_close)

        dlg.exec()

    def _build_params_group(self):
        self.grp_params = QtWidgets.QGroupBox()
        g = QtWidgets.QGridLayout(self.grp_params)
        g.setColumnStretch(1, 2)
        g.setColumnStretch(3, 3)
        g.setColumnStretch(5, 2)

        self.lbl_provider = QtWidgets.QLabel()
        g.addWidget(self.lbl_provider, 0, 0)
        self.comboBox_provider = QtWidgets.QComboBox()
        self.comboBox_provider.addItems([p["name"] for p in STAC_PROVIDERS])
        g.addWidget(self.comboBox_provider, 0, 1, 1, 5)

        self.lbl_sat = QtWidgets.QLabel()
        g.addWidget(self.lbl_sat, 1, 0)
        self.comboBox_satelite = QtWidgets.QComboBox()
        g.addWidget(self.comboBox_satelite, 1, 1)

        self.lbl_comp = QtWidgets.QLabel()
        g.addWidget(self.lbl_comp, 1, 2)
        self.comboBox_composicao = QtWidgets.QComboBox()
        g.addWidget(self.comboBox_composicao, 1, 3, 1, 3)

        self.lbl_period = QtWidgets.QLabel()
        g.addWidget(self.lbl_period, 2, 0)
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
        g.addLayout(date_lay, 2, 1)

        self.lbl_max_clouds = QtWidgets.QLabel()
        g.addWidget(self.lbl_max_clouds, 2, 2)
        cloud_lay = QtWidgets.QHBoxLayout()
        self.slider_clouds = QtWidgets.QSlider(_Horizontal)
        self.slider_clouds.setRange(0, 100)
        # ── cobertura de nuvens padrão: _DEFAULT_MAX_CLOUDS ──────────────────
        self.slider_clouds.setValue(_DEFAULT_MAX_CLOUDS)
        self.lbl_clouds_val = QtWidgets.QLabel(f"{_DEFAULT_MAX_CLOUDS}%")
        self.lbl_clouds_val.setFixedWidth(36)
        self.lbl_clouds_val.setAlignment(_AlignCenter)
        self.lbl_clouds_val.setStyleSheet(self._style_text("accent", "font-weight: bold;"))
        cloud_lay.addWidget(self.slider_clouds)
        cloud_lay.addWidget(self.lbl_clouds_val)
        g.addLayout(cloud_lay, 2, 3)

        self.lbl_bbox = QtWidgets.QLabel()
        g.addWidget(self.lbl_bbox, 3, 0)
        bbox_lay = QtWidgets.QHBoxLayout()
        bbox_lay.setSpacing(2)
        self.sp_west  = self._make_coord_spin(-180, 180)
        self.sp_south = self._make_coord_spin(-90,  90)
        self.sp_east  = self._make_coord_spin(-180, 180)
        self.sp_north = self._make_coord_spin(-90,  90)
        for lbl, sp in [("W:", self.sp_west), ("S:", self.sp_south),
                         ("E:", self.sp_east),  ("N:", self.sp_north)]:
            l = QtWidgets.QLabel(lbl)
            l.setStyleSheet(self._style_text("accent", "font-weight: bold; margin-left: 4px;"))
            bbox_lay.addWidget(l)
            bbox_lay.addWidget(sp)
        bbox_lay.addSpacing(6)
        self.btn_extent = QtWidgets.QPushButton()
        self.btn_extent.setFixedHeight(28)
        bbox_lay.addWidget(self.btn_extent)
        bbox_lay.addStretch()
        g.addLayout(bbox_lay, 3, 1, 1, 5)

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
        self.btn_load_more = QtWidgets.QPushButton()
        self.btn_load_more.setObjectName("btn_link")
        self.btn_load_more.setCursor(_PointingHand)
        self.btn_load_more.setVisible(False)
        left_lay.addWidget(self.btn_load_more)
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
        self.lbl_thumbnail.setScaledContents(False)
        self.lbl_thumbnail.installEventFilter(self)
        self.lbl_thumbnail.setStyleSheet(
            "background-color: {}; border-radius: 8px; color: {};".format(
                self._theme["bg_alt"], self._theme["muted"]
            )
        )
        right_lay.addWidget(self.lbl_thumbnail, stretch=1)

        meta_grid = QtWidgets.QGridLayout()
        self.lbl_thumb_date   = self._meta_label()
        self.lbl_thumb_clouds = self._meta_label()
        self.lbl_thumb_clouds.setStyleSheet(self._style_text("accent", "font-weight: bold;"))
        self.lbl_thumb_id     = QtWidgets.QLabel()
        self.lbl_thumb_id.setWordWrap(True)
        self.lbl_thumb_id.setStyleSheet(
            self._style_text("muted", "font-family: monospace; font-size: 8pt;")
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
        self.lbl_selected_scenes.setStyleSheet(self._style_text("accent", "font-weight: bold;"))
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
        lbl_log.setStyleSheet(self._style_text("accent", "font-weight: bold;"))
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
        self.comboBox_provider.currentIndexChanged.connect(
            self._update_provider_params
        )
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
        self.btn_credits.clicked.connect(self._show_credits)
        self.btn_load_more.clicked.connect(self._load_more_results)

    def _retranslate(self):
        self.lbl_title.setText(self.tr("Quick VRT Imagery Loader"))
        self.lbl_subtitle.setText(
            self.tr("Browse Satellite product collections, load imagery and build compositions and mosaics very quickly!")
        )
        self.btn_credits.setText(self.tr("Credits"))
        self.grp_params.setTitle(self.tr("Search Parameters"))
        self.lbl_provider.setText(self.tr("STAC Provider:"))
        self.lbl_sat.setText(self.tr("Satellite:"))
        self.lbl_comp.setText(self.tr("Composition:"))
        self.lbl_period.setText(self.tr("Period:"))
        self.lbl_to.setText(self.tr(" to "))
        self.lbl_max_clouds.setText(self.tr("Max clouds:"))
        self.lbl_bbox.setText(self.tr("Search area:"))
        self.btn_extent.setText(self.tr("Get from map canvas"))
        self.btn_listar.setText(self.tr("Search available images"))
        self.tableWidget.setHorizontalHeaderLabels(
            [self.tr("#"), self.tr("Date"), self.tr("Clouds"), self.tr("Scene ID")]
        )
        self.btn_load_more.setText(self.tr("Load more results"))
        self.btn_carregar.setText(self.tr("Load Selected"))
        self.btn_mosaic_selected.setText(self.tr("Mosaic Selected"))
        self.btn_copy_id.setText(self.tr("Copy ID"))
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

    def _update_provider_params(self):
        idx = self.comboBox_provider.currentIndex()
        self._provider = STAC_PROVIDERS[idx if idx >= 0 else 0]
        self.comboBox_satelite.blockSignals(True)
        self.comboBox_satelite.clear()
        self.comboBox_satelite.addItems(
            [s["label"] for s in self._provider["satellites"]]
        )
        self.comboBox_satelite.blockSignals(False)
        self._update_satellite_params()

    def _update_satellite_params(self):
        idx = self.comboBox_satelite.currentIndex()
        satellites = self._provider["satellites"]
        self._satellite     = satellites[idx if idx >= 0 else 0]
        self._collection    = self._satellite["collection"]
        self._compositions  = self._satellite["compositions"].copy()
        self.comboBox_composicao.clear()
        self.comboBox_composicao.addItems(list(self._compositions.keys()))
        self._reset_search_results()

    def _reset_search_results(self):
        """A provider/satellite change invalidates whatever is on screen:
        those results belong to a different collection with different band
        and asset keys, so loading them against the new provider would fail.
        Clear the table, preview, footprints and pagination for a clean slate."""
        # Guard: this funnels through _update_satellite_params, which also runs
        # once during __init__ before the browser widgets are wired up.
        if not hasattr(self, "tableWidget"):
            return
        if self._search_worker and self._search_worker.isRunning():
            self._cancel_search()
        self.last_items = []
        self.tableWidget.setRowCount(0)
        self._clear_rubber_bands()
        if self.btn_show_footprint.isChecked():
            self.btn_show_footprint.blockSignals(True)
            self.btn_show_footprint.setChecked(False)
            self.btn_show_footprint.blockSignals(False)
        self.btn_load_more.setVisible(False)
        self._search_cap = _SEARCH_PAGE_SIZE
        self._active_thumbnail_asset = self._provider["thumbnail_asset"]
        self._reset_preview()

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
        # While a search is in flight the same button acts as its cancel.
        if self._search_worker and self._search_worker.isRunning():
            self._cancel_search()
            return
        self._search_cap = _SEARCH_PAGE_SIZE
        self._start_search()

    def _load_more_results(self):
        if self._search_worker and self._search_worker.isRunning():
            return
        self._search_cap += _SEARCH_PAGE_SIZE
        self._start_search()

    def _start_search(self):
        bbox = self._current_bbox()
        self.btn_listar.setText(self.tr("Cancel search"))
        self.btn_load_more.setVisible(False)
        self._search_worker = SearchWorker(
            self._provider["url"],
            self._collection, bbox,
            self.dateEdit_inicio.date().toString("yyyy-MM-dd"),
            self.dateEdit_final.date().toString("yyyy-MM-dd"),
            self.slider_clouds.value(),
            max_results=self._search_cap,
            parent=self,
        )
        self._search_worker.search_done.connect(self._on_search_done)
        self._search_worker.search_error.connect(self._on_search_error)
        self._search_worker.start()

    def _cancel_search(self):
        worker = self._search_worker
        if worker:
            try:
                worker.search_done.disconnect()
                worker.search_error.disconnect()
            except (TypeError, RuntimeError):
                pass
            worker.requestInterruption()
        self.btn_listar.setText(self.tr("Search available images"))

    def _on_search_done(self, items, has_more):
        self.btn_listar.setText(self.tr("Search available images"))
        self.btn_load_more.setVisible(has_more)
        self.last_items = items
        # Freeze the provider's thumbnail asset key at search time so a later
        # provider switch can't make it mismatch with these already-loaded items.
        self._active_thumbnail_asset = self._provider["thumbnail_asset"]
        self._clear_rubber_bands()
        self.tableWidget.setRowCount(0)
        for idx, item in enumerate(items):
            self.tableWidget.insertRow(idx)
            self.tableWidget.setItem(idx, 0, QtWidgets.QTableWidgetItem(str(idx + 1)))
            self.tableWidget.setItem(
                idx, 1, QtWidgets.QTableWidgetItem(
                    item.properties.get("datetime", "N/A")[:10]
                )
            )
            self.tableWidget.setItem(idx, 2, QtWidgets.QTableWidgetItem(_format_clouds(item.properties)))
            self.tableWidget.setItem(idx, 3, QtWidgets.QTableWidgetItem(item.id))
        self.tableWidget.resizeColumnsToContents()
        self._reset_preview()

    def _on_search_error(self, err):
        self.btn_listar.setText(self.tr("Search available images"))
        iface.messageBar().pushMessage(self.tr("Search error"), err, level=MsgLevel.Critical)

    def _on_table_row_clicked(self, row):
        if row < 0 or row >= len(self.last_items):
            return
        
        # Immediate UI feedback for metadata
        item = self.last_items[row]
        self.lbl_thumb_date.setText(item.properties.get("datetime", "N/A")[:10])
        self.lbl_thumb_clouds.setText(_format_clouds(item.properties))
        self.lbl_thumb_id.setText(item.id)
        self.lbl_thumb_id.setToolTip(item.id)
        
        # Debounce the thumbnail download (250ms delay)
        self._current_thumb_row = row
        self._thumb_timer.start(250)
        
        if self.btn_show_footprint.isChecked():
            self._draw_selected_footprints()

    def _do_debounced_thumbnail(self):
        row = getattr(self, "_current_thumb_row", -1)
        if row < 0 or row >= len(self.last_items):
            return
            
        item = self.last_items[row]
        asset = item.assets.get(self._active_thumbnail_asset)
        if not asset:
            self._current_thumbnail_pixmap = QPixmap()
            self.lbl_thumbnail.setText(self.tr("No preview available"))
            self.lbl_thumbnail.setPixmap(QPixmap())
            return

        self._current_thumbnail_pixmap = QPixmap()
        self.lbl_thumbnail.setText(self.tr("Loading…"))
        self.lbl_thumbnail.setPixmap(QPixmap())
        
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
        self._thumb_worker.failed.connect(self._on_thumbnail_failed)
        self._thumb_worker.start()

    def _on_selection_changed(self):
        rows = self.tableWidget.selectionModel().selectedRows()
        if rows:
            self._on_table_row_clicked(rows[0].row())
        elif self.btn_show_footprint.isChecked():
            self._clear_rubber_bands()

    def _load_thumbnail(self, row):
        # This method is now handled by _on_table_row_clicked + timer
        pass

    def _show_thumbnail(self, pixmap):
        self.lbl_thumbnail.setText("")
        self._current_thumbnail_pixmap = pixmap
        self._update_thumbnail_pixmap()

    def _on_thumbnail_failed(self, msg):
        self._current_thumbnail_pixmap = QPixmap()
        self.lbl_thumbnail.setPixmap(QPixmap())
        self.lbl_thumbnail.setText(self.tr("Preview error: {}").format(msg))

    def _update_thumbnail_pixmap(self):
        if self._current_thumbnail_pixmap.isNull():
            return

        target_size = self.lbl_thumbnail.contentsRect().size()
        if target_size.width() <= 0 or target_size.height() <= 0:
            return

        fitted = self._current_thumbnail_pixmap.scaled(
            target_size, _KeepAspect, _SmoothTx
        )
        self.lbl_thumbnail.setPixmap(fitted)

    def _reset_preview(self):
        self._current_thumbnail_pixmap = QPixmap()
        self.lbl_thumbnail.setText(self.tr("Select an image to preview"))
        self.lbl_thumbnail.setPixmap(QPixmap())
        self.lbl_thumb_date.setText("")
        self.lbl_thumb_clouds.setText("")
        self.lbl_thumb_id.setText("")

    def _toggle_footprint(self, checked):
        if checked:
            self._draw_selected_footprints()
        else:
            self._clear_rubber_bands()

    def _draw_selected_footprints(self):
        rows = self.tableWidget.selectionModel().selectedRows()
        row_numbers = sorted({r.row() for r in rows})
        self._draw_footprints(row_numbers)

    def _draw_footprints(self, rows):
        self._clear_rubber_bands()
        if not rows:
            return

        try:
            dst_crs = iface.mapCanvas().mapSettings().destinationCrs()
            src_crs = QgsCoordinateReferenceSystem("EPSG:4326")
            xform = None
            if src_crs != dst_crs:
                xform = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())

            # Safely get Polygon geometry type for RubberBand
            try:
                # QGIS 4/Qt6 way
                poly_type = Qgis.GeometryType.Polygon
            except AttributeError:
                poly_type = QgsWkbTypes.PolygonGeometry

            for row in rows:
                # Per-item isolation: one bad geometry must not kill the
                # footprints of every other selected row.
                try:
                    if row < 0 or row >= len(self.last_items):
                        continue

                    item = self.last_items[row]
                    geom_dict = item.geometry
                    if not geom_dict:
                        QgsMessageLog.logMessage(
                            f"Footprint: item {item.id} has no geometry",
                            "QuickVRT", MsgLevel.Warning)
                        continue

                    qgs_geom = self._geometry_from_geojson(geom_dict)

                    if not qgs_geom or qgs_geom.isEmpty():
                        QgsMessageLog.logMessage(
                            f"Footprint: could not parse geometry of {item.id}",
                            "QuickVRT", MsgLevel.Warning)
                        continue

                    if xform:
                        qgs_geom.transform(xform)

                    rb = QgsRubberBand(iface.mapCanvas(), poly_type)
                    rb.setColor(QColor(137, 180, 250, 180))
                    rb.setFillColor(QColor(137, 180, 250, 28))
                    rb.setWidth(2)
                    rb.setToGeometry(qgs_geom, None)
                    rb.show()
                    if rb.numberOfVertices() == 0:
                        QgsMessageLog.logMessage(
                            f"Footprint: empty rubber band for {item.id}",
                            "QuickVRT", MsgLevel.Warning)
                    self._rubber_bands.append(rb)
                except Exception as e:
                    QgsMessageLog.logMessage(
                        f"Footprint error on row {row}: {e}",
                        "QuickVRT", MsgLevel.Warning)
        except Exception as e:
            QgsMessageLog.logMessage(f"Footprint error: {str(e)}", "QuickVRT", MsgLevel.Warning)

    @staticmethod
    def _geometry_from_geojson(geom_dict):
        """Build a QgsGeometry straight from GeoJSON coordinate arrays.
        QgsJsonUtils.geometryFromGeoJson (and QgsGeometry.fromJson) are
        missing from older QGIS 3.x releases such as 3.34, so footprints
        must not depend on either API."""
        gtype  = (geom_dict or {}).get("type", "")
        coords = (geom_dict or {}).get("coordinates")
        if not coords:
            return None

        def ring_to_points(ring):
            return [QgsPointXY(pt[0], pt[1]) for pt in ring]

        try:
            if gtype == "Polygon":
                return QgsGeometry.fromPolygonXY(
                    [ring_to_points(r) for r in coords]
                )
            if gtype == "MultiPolygon":
                return QgsGeometry.fromMultiPolygonXY(
                    [[ring_to_points(r) for r in poly] for poly in coords]
                )
        except Exception:
            return None
        return None

    def _clear_rubber_bands(self):
        for rb in self._rubber_bands:
            try:
                rb.reset()
            except:
                iface.mapCanvas().scene().removeItem(rb)
        self._rubber_bands = []

    def process_stac_load(self):
        rows = self.tableWidget.selectionModel().selectedRows()
        if not rows:
            return
        
        items = [self.last_items[r.row()] for r in rows]

        comp_name = self.comboBox_composicao.currentText()
        comp_data = self._compositions.get(comp_name, [])
        if isinstance(comp_data, dict):
            bands   = comp_data.get("bands", [])
            formula = comp_data.get("formula")
        else:
            bands   = comp_data
            formula = None

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
                "catalog_url":   self._provider["url"],
                "needs_signing": self._provider["needs_signing"],
                "bands": bands,
                "formula": formula,
                "max_cloud":  self.slider_clouds.value(),
                "max_items":  1,
                "preference": "N/A",
                "nodata": self._satellite.get("nodata", 0),
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
            self._vrt_worker = VrtWorker(
                items, bands, self._collection, formula=formula,
                needs_signing=self._provider["needs_signing"],
                prefix=self._satellite["prefix"], parent=self,
            )
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
            formula = self._index_formula_from_layer_name(layer_name)
            if formula:
                self._apply_index_renderer(layer, formula)
            QgsProject.instance().addMapLayer(layer)

    @staticmethod
    def _index_formula_from_layer_name(layer_name):
        formula = layer_name.split(" - ", 1)[0].lower()
        return formula if formula in {"ndvi", "ndwi", "ndmi", "evi"} else None

    @staticmethod
    def _apply_index_renderer(layer, formula):
        provider = layer.dataProvider()
        ramp_items = QuickVRTDialog._index_color_ramp(formula)
        color_ramp = QgsColorRampShader()
        try:
            color_ramp.setColorRampType(QgsColorRampShader.Type.Interpolated)
        except AttributeError:
            color_ramp.setColorRampType(QgsColorRampShader.Interpolated)
        color_ramp.setColorRampItemList(
            [
                QgsColorRampShader.ColorRampItem(value, QColor(color), label)
                for value, color, label in ramp_items
            ]
        )

        raster_shader = QgsRasterShader()
        raster_shader.setRasterShaderFunction(color_ramp)
        renderer = QgsSingleBandPseudoColorRenderer(provider, 1, raster_shader)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

    @staticmethod
    def _index_color_ramp(formula):
        ramps = {
            "ndvi": [
                (-1.0, "#1f5aa6", "Water / cloud shadow"),
                (0.0, "#d9c89e", "Bare soil"),
                (0.2, "#f3e55b", "Sparse vegetation"),
                (0.5, "#48a23f", "Healthy vegetation"),
                (0.8, "#0b5d1e", "Dense vegetation"),
                (1.0, "#063b14", "Very dense vegetation"),
            ],
            "evi": [
                (-1.0, "#4c2c69", "Low response"),
                (0.0, "#d9c89e", "Bare soil"),
                (0.2, "#f3e55b", "Sparse vegetation"),
                (0.5, "#48a23f", "Healthy vegetation"),
                (0.8, "#0b5d1e", "Dense vegetation"),
                (1.0, "#063b14", "Very dense vegetation"),
            ],
            "ndwi": [
                (-1.0, "#8c510a", "Dry land"),
                (-0.1, "#dfc27d", "Low water signal"),
                (0.0, "#f7f7f7", "Neutral"),
                (0.2, "#80cdc1", "Moist / shallow water"),
                (0.6, "#018571", "Water"),
                (1.0, "#003c30", "Strong water signal"),
            ],
            "ndmi": [
                (-1.0, "#8c510a", "Very dry"),
                (0.0, "#dfc27d", "Dry"),
                (0.2, "#c7eae5", "Moderate moisture"),
                (0.6, "#35978f", "Moist"),
                (1.0, "#01665e", "Very moist"),
            ],
        }
        return ramps.get(formula, ramps["ndvi"])

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
        comp_name = self.comboBox_composicao.currentText()
        comp_data = self._compositions.get(comp_name, [])
        if isinstance(comp_data, dict):
            bands   = comp_data.get("bands", [])
            formula = comp_data.get("formula")
        else:
            bands   = comp_data
            formula = None

        params = {
            "bbox": self._current_bbox(),
            "start_date": self.dateEdit_inicio.date().toString("yyyy-MM-dd"),
            "end_date":   self.dateEdit_final.date().toString("yyyy-MM-dd"),
            "collection": self._collection,
            "catalog_url":   self._provider["url"],
            "needs_signing": self._provider["needs_signing"],
            "bands":   bands,
            "formula": formula,
            "max_cloud":  self.slider_clouds.value(),
            "max_items":  len(items),
            "preference": "Manual",
            "nodata": self._satellite.get("nodata", 0),
            "export_tif":   export,
            "out_tif_path": out_tif,
            "compress":     self.cb_compress_browser.currentText(),
            "items_list":   items,
        }
        self.btn_mosaic_selected.setEnabled(False)
        self.tabs.setCurrentIndex(1)
        self._start_mosaic_worker(params, re_enable=[self.btn_mosaic_selected])

    def _run_mosaic(self):
        # While a mosaic is in flight the same button acts as its cancel.
        if self._mosaic_worker and self._mosaic_worker.isRunning():
            self._cancel_mosaic()
            return
        export  = self.chk_export_tif.isChecked()
        out_tif = self.le_tif.text().strip()
        if export and not out_tif:
            QtWidgets.QMessageBox.warning(
                self, self.tr("Warning"), self.tr("Please provide the output .tif path.")
            )
            return
        comp_name = self.comboBox_composicao.currentText()
        comp_data = self._compositions.get(comp_name, [])
        if isinstance(comp_data, dict):
            bands   = comp_data.get("bands", [])
            formula = comp_data.get("formula")
        else:
            bands   = comp_data
            formula = None

        params = {
            "bbox": self._current_bbox(),
            "start_date": self.dateEdit_inicio.date().toString("yyyy-MM-dd"),
            "end_date":   self.dateEdit_final.date().toString("yyyy-MM-dd"),
            "collection": self._collection,
            "catalog_url":   self._provider["url"],
            "needs_signing": self._provider["needs_signing"],
            "bands":   bands,
            "formula": formula,
            "max_cloud":  self.slider_clouds.value(),
            "max_items":  self.sp_items.value(),
            "preference": self.cb_preference.currentText(),
            "nodata": self._satellite.get("nodata", 0),
            "export_tif":   export,
            "out_tif_path": out_tif,
            "compress":     self.cb_compress.currentText(),
        }
        self.tableMosaic.setRowCount(0)
        self.log_panel.clear()
        self.mosaic_progress.setVisible(True)
        self._start_mosaic_worker(params)

    def _start_mosaic_worker(self, params, re_enable=None):
        if self._mosaic_worker and self._mosaic_worker.isRunning():
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
        self.btn_run_mosaic.setText(self.tr("Cancel mosaic"))

    def _cancel_mosaic(self):
        worker = self._mosaic_worker
        if worker:
            for sig in (worker.progress, worker.progress_pct,
                        worker.item_selected, worker.finished, worker.error):
                try:
                    sig.disconnect()
                except (TypeError, RuntimeError):
                    pass
            worker.requestInterruption()
            if worker.isRunning():
                worker.terminate()
                worker.wait()
        self._re_enable_buttons()
        self.btn_run_mosaic.setText(self.tr("Generate Mosaic"))
        self.mosaic_progress.setRange(0, 0)
        self.mosaic_progress.setVisible(False)
        self.log_panel.append(self.tr("Cancelled by user."))

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
        self.btn_run_mosaic.setText(self.tr("Generate Mosaic"))
        self.mosaic_progress.setRange(0, 0)
        self.mosaic_progress.setVisible(False)
        comp = self.comboBox_composicao.currentText()
        layer = QgsRasterLayer(vrt, self.tr("Mosaic – {}").format(comp))
        if layer.isValid():
            comp_data = self._compositions.get(comp, {})
            if isinstance(comp_data, dict) and comp_data.get("formula"):
                self._apply_index_renderer(layer, comp_data.get("formula"))
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
        self.btn_run_mosaic.setText(self.tr("Generate Mosaic"))
        self.mosaic_progress.setRange(0, 0)
        self.mosaic_progress.setVisible(False)
        self.log_panel.append(self.tr("\nERROR:\n{}").format(err))
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

    def _meta_label(self):
        lbl = QtWidgets.QLabel()
        lbl.setAlignment(_AlignCenter)
        lbl.setStyleSheet(self._style_text("fg", "font-size: 9pt;"))
        return lbl

    def closeEvent(self, event):
        self._save_settings()
        self._clear_rubber_bands()
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_thumbnail_pixmap()

    def eventFilter(self, watched, event):
        if watched is getattr(self, "lbl_thumbnail", None):
            if event.type() == _ResizeEvent:
                self._update_thumbnail_pixmap()
        return super().eventFilter(watched, event)
