# -*- coding: utf-8 -*-
"""
mosaic_tool.py
--------------
Módulo de mosaico automático para integração ao plugin Quick VRT Imagery Loader.

Como integrar ao plugin existente
----------------------------------
1. Copie este arquivo para a pasta do plugin (junto com sentinel_stac_loader.py).
2. No menu do plugin (sentinel_stac_loader.py), adicione uma ação "Auto-Mosaic":

        self.mosaic_action = QAction("Auto-Mosaic Sentinel-2", self.iface.mainWindow())
        self.mosaic_action.triggered.connect(self.open_mosaic_tool)
        self.iface.addToolBarIcon(self.mosaic_action)

3. Adicione o método ao plugin:

        def open_mosaic_tool(self):
            from .mosaic_tool import MosaicDialog
            dlg = MosaicDialog(self.iface)
            dlg.exec_()
"""

import os
import sys
import tempfile
from pathlib import Path
from datetime import date, timedelta

from qgis.PyQt import QtCore, QtWidgets, QtGui
from qgis.PyQt.QtCore import Qt, QThread, pyqtSignal

# ── Compatibilidade Qt5 (QGIS 3.x) / Qt6 (QGIS 4.x) ──────────────────────
import qgis.PyQt.QtCore as _qc
_QT_VERSION = [int(x) for x in _qc.qVersion().split(".")]
_QT6 = _QT_VERSION[0] >= 6

# Orientação do slider
_Qt_Horizontal = Qt.Orientation.Horizontal if _QT6 else Qt.Horizontal  # type: ignore[attr-defined]
from qgis.core import (
    QgsProject, QgsRasterLayer, QgsMessageLog, Qgis,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsRectangle, QgsTask, QgsApplication,
)

LOG_TAG = "AutoMosaic"


# ── Worker (thread separada para não travar o QGIS) ──────────────────────────

class MosaicWorker(QThread):
    progress  = pyqtSignal(str)        # mensagens de log
    finished  = pyqtSignal(str, str)   # (vrt_path, tif_path_ou_vazio)
    error     = pyqtSignal(str)

    def __init__(self, params):
        super().__init__()
        self.params = params

    def run(self):
        try:
            import planetary_computer as pc
            import pystac_client
            from shapely.geometry import box, mapping

            p = self.params
            self.progress.emit("Conectando ao Planetary Computer STAC…")

            catalog = pystac_client.Client.open(
                "https://planetarycomputer.microsoft.com/api/stac/v1",
                modifier=pc.sign_inplace,
            )

            geometry = mapping(box(*p["bbox"]))
            self.progress.emit(
                f"Buscando cenas ({p['collection']}) de "
                f"{p['start_date']} a {p['end_date']}, nuvens < {p['max_cloud']}%…"
            )

            search = catalog.search(
                collections=[p["collection"]],
                intersects=geometry,
                datetime=f"{p['start_date']}/{p['end_date']}",
                query={"eo:cloud_cover": {"lt": p["max_cloud"]}},
                max_items=p["max_items"],
                sortby=["+eo:cloud_cover"],
            )

            items = list(search.items())
            if not items:
                self.error.emit("Nenhuma imagem encontrada com os parâmetros informados.")
                return

            items.sort(key=lambda i: i.properties.get("eo:cloud_cover", 999))
            lines = []
            for idx, item in enumerate(items):
                clouds = item.properties.get("eo:cloud_cover", 0)
                dt     = item.properties.get("datetime", "")[:10]
                lines.append(f"  [{idx}] {dt}  {clouds:.1f}%  {item.id}")
            self.progress.emit(f"{len(items)} cena(s) encontrada(s):\n" + "\n".join(lines))

            # Monta VRT por banda
            tmp_dir = Path(tempfile.mkdtemp(prefix="qgis_mosaic_"))
            per_band_vrts = []

            for b_idx, band_name in enumerate(p["bands"], 1):
                urls = []
                for item in items:
                    if band_name in item.assets:
                        urls.append(f"/vsicurl/{item.assets[band_name].href}")
                if not urls:
                    self.error.emit(f"Banda {band_name} não encontrada em nenhuma cena.")
                    return

                self.progress.emit(f"Construindo VRT – banda {band_name} ({b_idx}/{len(p['bands'])})…")
                band_vrt = str(tmp_dir / f"band_{b_idx}_{band_name}.vrt")
                self._build_single_vrt(band_vrt, urls, p["nodata"])
                per_band_vrts.append(band_vrt)

            # VRT final multi-banda
            out_vrt = str(tmp_dir / "mosaic.vrt")
            self.progress.emit("Combinando bandas no VRT final…")
            self._build_single_vrt(out_vrt, per_band_vrts, p["nodata"], separate=True)

            # Exportação opcional para GeoTIFF
            out_tif = ""
            if p.get("export_tif") and p.get("out_tif_path"):
                out_tif = p["out_tif_path"]
                self.progress.emit(f"Exportando GeoTIFF → {out_tif}\n(pode levar alguns minutos…)")
                self._export_tif(out_vrt, out_tif, p.get("compress", "DEFLATE"))

            self.finished.emit(out_vrt, out_tif)

        except Exception as exc:
            self.error.emit(str(exc))

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _build_single_vrt(self, out_vrt, inputs, nodata, separate=False):
        try:
            from osgeo import gdal
            opts = gdal.BuildVRTOptions(
                separate=separate,
                srcNodata=nodata,
                VRTNodata=nodata,
                resampleAlg="bilinear",
            )
            ds = gdal.BuildVRT(out_vrt, inputs, options=opts)
            ds.FlushCache()
            ds = None
        except ImportError:
            import subprocess
            cmd = ["gdalbuildvrt", "-srcnodata", str(nodata), "-vrtnodata", str(nodata)]
            if separate:
                cmd.append("-separate")
            cmd += [out_vrt] + inputs
            subprocess.run(cmd, check=True)

    def _export_tif(self, vrt_path, out_tif, compress):
        try:
            from osgeo import gdal
            co = ["TILED=YES", f"COMPRESS={compress}", "PREDICTOR=2", "BIGTIFF=IF_SAFER"]
            tmp = out_tif.replace(".tif", "_tmp.tif")
            gdal.Translate(tmp, vrt_path, format="GTiff", creationOptions=co)
            ds = gdal.Open(tmp, gdal.GA_Update)
            ds.BuildOverviews("NEAREST", [2, 4, 8, 16, 32])
            ds = None
            gdal.Translate(out_tif, tmp, format="GTiff", creationOptions=co)
            os.remove(tmp)
        except ImportError:
            import subprocess
            subprocess.run(["gdal_translate", "-of", "GTiff",
                            "-co", "TILED=YES", "-co", f"COMPRESS={compress}",
                            "-co", "PREDICTOR=2", vrt_path, out_tif], check=True)
            subprocess.run(["gdaladdo", "-r", "nearest", out_tif,
                            "2", "4", "8", "16", "32"], check=True)


# ── Caixa de diálogo principal ───────────────────────────────────────────────

COMPOSITIONS = {
    "S2_TrueColor":      {"collection": "sentinel-2-l2a", "bands": ["B04","B03","B02"], "label": "True Color (RGB)"},
    "S2_FalseColorNIR":  {"collection": "sentinel-2-l2a", "bands": ["B08","B04","B03"], "label": "False Color NIR"},
    "S2_FalseColorSWIR": {"collection": "sentinel-2-l2a", "bands": ["B11","B08","B04"], "label": "False Color SWIR"},
    "S2_Agriculture":    {"collection": "sentinel-2-l2a", "bands": ["B11","B08","B02"], "label": "Agriculture"},
    "S2_Geology":        {"collection": "sentinel-2-l2a", "bands": ["B12","B11","B02"], "label": "Geology"},
    "S2_Urban":          {"collection": "sentinel-2-l2a", "bands": ["B12","B11","B04"], "label": "Urban"},
    "L8_TrueColor":      {"collection": "landsat-c2-l2",  "bands": ["red","green","blue"],  "label": "Landsat True Color"},
    "L8_FalseColorNIR":  {"collection": "landsat-c2-l2",  "bands": ["nir08","red","green"], "label": "Landsat False Color NIR"},
}


class MosaicDialog(QtWidgets.QDialog):

    def __init__(self, iface, parent=None):
        super().__init__(parent or iface.mainWindow())
        self.iface   = iface
        self.worker  = None
        self.setWindowTitle("Auto-Mosaic Sentinel-2 / Landsat")
        self.setMinimumWidth(520)
        self._build_ui()

    # ── UI ──────────────────────────────────────────────────────────────────

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        # ── Seção: Parâmetros ──
        grp_params = QtWidgets.QGroupBox("Parâmetros de busca")
        form = QtWidgets.QFormLayout(grp_params)

        # Composição
        self.cb_comp = QtWidgets.QComboBox()
        for key, val in COMPOSITIONS.items():
            self.cb_comp.addItem(val["label"], key)
        form.addRow("Composição:", self.cb_comp)

        # Bbox (usa extensão atual do mapa)
        bbox_row = QtWidgets.QHBoxLayout()
        self.lbl_bbox = QtWidgets.QLabel("(extensão atual do mapa)")
        self.lbl_bbox.setStyleSheet("color: gray; font-style: italic;")
        btn_use_extent = QtWidgets.QPushButton("Usar extensão do mapa")
        btn_use_extent.clicked.connect(self._load_extent)
        bbox_row.addWidget(self.lbl_bbox)
        bbox_row.addWidget(btn_use_extent)
        form.addRow("Área (bbox):", bbox_row)

        # Datas
        today      = date.today()
        month_ago  = today - timedelta(days=30)
        self.de_start = QtWidgets.QDateEdit(QtCore.QDate(*month_ago.timetuple()[:3]))
        self.de_start.setCalendarPopup(True)
        self.de_end   = QtWidgets.QDateEdit(QtCore.QDate(*today.timetuple()[:3]))
        self.de_end.setCalendarPopup(True)
        date_row = QtWidgets.QHBoxLayout()
        date_row.addWidget(self.de_start)
        date_row.addWidget(QtWidgets.QLabel(" até "))
        date_row.addWidget(self.de_end)
        form.addRow("Período:", date_row)

        # Nuvens
        self.sld_clouds = QtWidgets.QSlider(_Qt_Horizontal)
        self.sld_clouds.setRange(0, 100)
        self.sld_clouds.setValue(20)
        self.lbl_clouds = QtWidgets.QLabel("20%")
        self.sld_clouds.valueChanged.connect(lambda v: self.lbl_clouds.setText(f"{v}%"))
        clouds_row = QtWidgets.QHBoxLayout()
        clouds_row.addWidget(self.sld_clouds)
        clouds_row.addWidget(self.lbl_clouds)
        form.addRow("Máx. nuvens:", clouds_row)

        # Máx. cenas
        self.sp_items = QtWidgets.QSpinBox()
        self.sp_items.setRange(1, 50)
        self.sp_items.setValue(10)
        form.addRow("Máx. cenas:", self.sp_items)

        layout.addWidget(grp_params)

        # ── Seção: Exportação ──
        grp_export = QtWidgets.QGroupBox("Exportar GeoTIFF (opcional)")
        grp_export.setCheckable(True)
        grp_export.setChecked(False)
        self.grp_export = grp_export
        exp_form = QtWidgets.QFormLayout(grp_export)

        tif_row = QtWidgets.QHBoxLayout()
        self.le_tif = QtWidgets.QLineEdit()
        self.le_tif.setPlaceholderText("Caminho do arquivo .tif de saída")
        btn_browse = QtWidgets.QPushButton("…")
        btn_browse.setFixedWidth(30)
        btn_browse.clicked.connect(self._browse_tif)
        tif_row.addWidget(self.le_tif)
        tif_row.addWidget(btn_browse)
        exp_form.addRow("Arquivo .tif:", tif_row)

        self.cb_compress = QtWidgets.QComboBox()
        for c in ["DEFLATE", "LZW", "ZSTD", "NONE"]:
            self.cb_compress.addItem(c)
        exp_form.addRow("Compressão:", self.cb_compress)

        layout.addWidget(grp_export)

        # ── Log ──
        self.txt_log = QtWidgets.QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setFixedHeight(140)
        self.txt_log.setStyleSheet("font-family: monospace; font-size: 10px;")
        layout.addWidget(self.txt_log)

        # ── Barra de progresso ──
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 0)   # indeterminado
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        # ── Botões ──
        btn_row = QtWidgets.QHBoxLayout()
        self.btn_run = QtWidgets.QPushButton("▶  Gerar Mosaico")
        self.btn_run.setDefault(True)
        self.btn_run.clicked.connect(self._run)
        btn_close = QtWidgets.QPushButton("Fechar")
        btn_close.clicked.connect(self.reject)
        btn_row.addWidget(self.btn_run)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        # Carrega extent inicial
        self._load_extent()

    # ── Slots ────────────────────────────────────────────────────────────────

    def _load_extent(self):
        """Lê a extensão atual do mapa e converte para EPSG:4326."""
        canvas = self.iface.mapCanvas()
        extent = canvas.extent()
        src_crs = canvas.mapSettings().destinationCrs()
        tgt_crs = QgsCoordinateReferenceSystem("EPSG:4326")

        if src_crs != tgt_crs:
            xform  = QgsCoordinateTransform(src_crs, tgt_crs, QgsProject.instance())
            extent = xform.transformBoundingBox(extent)

        self._bbox = (
            round(extent.xMinimum(), 6),
            round(extent.yMinimum(), 6),
            round(extent.xMaximum(), 6),
            round(extent.yMaximum(), 6),
        )
        self.lbl_bbox.setText(
            f"{self._bbox[0]}, {self._bbox[1]}, {self._bbox[2]}, {self._bbox[3]}"
        )
        self.lbl_bbox.setStyleSheet("color: #333;")

    def _browse_tif(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Salvar GeoTIFF", "", "GeoTIFF (*.tif *.tiff)"
        )
        if path:
            if not path.endswith((".tif", ".tiff")):
                path += ".tif"
            self.le_tif.setText(path)

    def _log(self, msg):
        self.txt_log.appendPlainText(msg)
        QgsMessageLog.logMessage(msg, LOG_TAG, Qgis.Info)

    def _run(self):
        if not hasattr(self, "_bbox"):
            self._load_extent()

        comp_key = self.cb_comp.currentData()
        comp     = COMPOSITIONS[comp_key]

        export_tif  = self.grp_export.isChecked()
        out_tif_path = self.le_tif.text().strip() if export_tif else ""

        if export_tif and not out_tif_path:
            QtWidgets.QMessageBox.warning(self, "Aviso",
                "Informe o caminho do arquivo .tif de saída.")
            return

        params = {
            "bbox":         self._bbox,
            "start_date":   self.de_start.date().toString("yyyy-MM-dd"),
            "end_date":     self.de_end.date().toString("yyyy-MM-dd"),
            "collection":   comp["collection"],
            "bands":        comp["bands"],
            "max_cloud":    self.sld_clouds.value(),
            "max_items":    self.sp_items.value(),
            "nodata":       0,
            "export_tif":   export_tif,
            "out_tif_path": out_tif_path,
            "compress":     self.cb_compress.currentText(),
        }

        self.txt_log.clear()
        self._log(f"Iniciando mosaico: {comp['label']}")
        self.btn_run.setEnabled(False)
        self.progress_bar.setVisible(True)

        self.worker = MosaicWorker(params)
        self.worker.progress.connect(self._log)
        self.worker.finished.connect(self._on_finished)
        self.worker.error.connect(self._on_error)
        self.worker.start()

    def _on_finished(self, vrt_path, tif_path):
        self.progress_bar.setVisible(False)
        self.btn_run.setEnabled(True)

        comp_key = self.cb_comp.currentData()
        name     = COMPOSITIONS[comp_key]["label"]

        # Carrega VRT no QGIS
        layer = QgsRasterLayer(vrt_path, f"Mosaico – {name}")
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            self._log(f"\n✅ Camada adicionada ao QGIS: Mosaico – {name}")
        else:
            self._log(f"[AVISO] VRT gerado mas não pôde ser carregado: {vrt_path}")

        if tif_path:
            layer_tif = QgsRasterLayer(tif_path, f"Mosaico GeoTIFF – {name}")
            if layer_tif.isValid():
                QgsProject.instance().addMapLayer(layer_tif)
                self._log(f"✅ GeoTIFF adicionado: {tif_path}")

        self._log("\nConcluído!")

    def _on_error(self, msg):
        self.progress_bar.setVisible(False)
        self.btn_run.setEnabled(True)
        self._log(f"\n[ERRO] {msg}")
        QtWidgets.QMessageBox.critical(self, "Erro no mosaico", msg)