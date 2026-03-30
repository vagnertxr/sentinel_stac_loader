# -*- coding: utf-8 -*-
import os
import tempfile
import traceback
from pathlib import Path
from qgis.PyQt.QtCore import QThread, pyqtSignal, QCoreApplication
from osgeo import gdal, osr

gdal.UseExceptions()

class MosaicWorker(QThread):
    progress      = pyqtSignal(str)
    progress_pct  = pyqtSignal(int)       # 0-100
    finished      = pyqtSignal(str, str)
    error         = pyqtSignal(str)
    item_selected = pyqtSignal(str, str, str)

    def __init__(self, params):
        super().__init__()
        self.params = params

    def tr(self, msg):
        return QCoreApplication.translate('SentinelSTACDialogBase', msg)

    def run(self):
        try:
            import planetary_computer as pc
            import pystac_client
            from shapely.geometry import box, mapping, shape

            gdal.SetConfigOption("GDAL_HTTP_MAX_RETRY", "5")
            gdal.SetConfigOption("GDAL_HTTP_RETRY_DELAY", "3")
            gdal.SetConfigOption("VSI_CACHE", "TRUE")
            gdal.SetConfigOption("GDAL_CACHEMAX", "512")
            gdal.SetConfigOption("GDAL_HTTP_MERGE_CONSECUTIVE_HTTP_RETRIEVALS", "YES")

            p = self.params
            self.progress_pct.emit(0)
            self.progress.emit(self.tr("Connecting to Planetary Computer STAC..."))

            catalog = pystac_client.Client.open(
                "https://planetarycomputer.microsoft.com/api/stac/v1",
                modifier=pc.sign_inplace,
            )

            bbox_coords     = p["bbox"]
            bbox_poly       = box(*bbox_coords).buffer(0)
            total_bbox_area = bbox_poly.area

            if p.get("items_list"):
                self.progress.emit(self.tr("Re-signing provided items..."))
                all_items = [pc.sign(item) for item in p["items_list"]]
            else:
                self.progress.emit(
                    self.tr("Searching scenes ({col}) {s} to {e}, clouds < {c}%...").format(
                        col=p["collection"], s=p["start_date"],
                        e=p["end_date"],     c=p["max_cloud"],
                    )
                )
                sort_param = "+eo:cloud_cover"
                if p.get("preference") == "Most Recent":
                    sort_param = "-properties.datetime"

                search = catalog.search(
                    collections=[p["collection"]],
                    intersects=mapping(bbox_poly),
                    datetime=f"{p['start_date']}/{p['end_date']}",
                    query={"eo:cloud_cover": {"lt": p["max_cloud"]}},
                    max_items=500,
                    sortby=[sort_param],
                )
                all_items = list(search.items())

            if not all_items:
                self.error.emit(self.tr("No images found with the provided parameters."))
                return

            self.progress_pct.emit(10)

            selected_items = []
            uncovered_area = bbox_poly

            self.progress.emit(
                self.tr("Selecting scenes for full coverage ({n} candidates)...").format(
                    n=len(all_items)
                )
            )

            for item in all_items:
                if uncovered_area.is_empty or uncovered_area.area <= 1e-9:
                    break
                if len(selected_items) >= p.get("max_items", 100):
                    break

                item_geom    = shape(item.geometry).buffer(0)
                contribution = item_geom.intersection(uncovered_area)

                if contribution.area <= (total_bbox_area * 0.0005):
                    continue

                selected_items.append(item)
                uncovered_area = uncovered_area.difference(item_geom).buffer(0)

                clouds      = item.properties.get("eo:cloud_cover", 0)
                dt          = item.properties.get("datetime", "")[:10]
                covered_pct = (1.0 - uncovered_area.area / total_bbox_area) * 100

                self.item_selected.emit(dt, f"{clouds:.1f}%", item.id)
                self.progress.emit(
                    self.tr("  Added {id}  clouds={c:.1f}%  coverage={pct:.1f}%").format(
                        id=item.id, c=clouds, pct=covered_pct
                    )
                )

            if not selected_items:
                self.error.emit(self.tr("No images selected after spatial filtering."))
                return

            final_pct = (1.0 - uncovered_area.area / total_bbox_area) * 100
            self.progress.emit(
                self.tr("Coverage: {pct:.1f}%  ({n} scenes selected)").format(
                    pct=final_pct, n=len(selected_items)
                )
            )
            self.progress_pct.emit(20)

            self.progress.emit(self.tr("Re-signing selected scenes..."))
            selected_items = [pc.sign(item) for item in selected_items]

            s_srs = osr.SpatialReference()
            s_srs.ImportFromEPSG(4326)
            s_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

            t_srs = osr.SpatialReference()
            t_srs.ImportFromEPSG(3857)
            t_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)

            tx = osr.CoordinateTransformation(s_srs, t_srs)
            sw = tx.TransformPoint(bbox_coords[0], bbox_coords[1])
            ne = tx.TransformPoint(bbox_coords[2], bbox_coords[3])
            warp_bounds = [sw[0], sw[1], ne[0], ne[1]]

            self.progress.emit(
                self.tr("Warp bounds (EPSG:3857): xmin={:.0f} ymin={:.0f} xmax={:.0f} ymax={:.0f}").format(
                    *warp_bounds
                )
            )


            tmp_dir       = Path(tempfile.mkdtemp(prefix="qgis_mosaic_"))
            per_band_vrts = []
            bands         = p["bands"]
            nodata        = p.get("nodata", 0)
            n_bands       = len(bands)

            for b_idx, band_name in enumerate(bands, 1):
                urls = [
                    "/vsicurl/" + item.assets[band_name].href
                    for item in selected_items
                    if band_name in item.assets
                ]

                if not urls:
                    self.progress.emit(
                        self.tr("  Band {b}: no assets found, skipped.").format(b=band_name)
                    )
                    continue

                self.progress.emit(
                    self.tr("Mosaicking band {b} ({i}/{t}) — {n} scenes...").format(
                        b=band_name, i=b_idx, t=n_bands, n=len(urls)
                    )
                )

                pre_vrt  = str(tmp_dir / f"pre_{b_idx:02d}_{band_name}.vrt")
                band_vrt = str(tmp_dir / f"band_{b_idx:02d}_{band_name}.vrt")

                gdal.BuildVRT(
                    pre_vrt, urls,
                    options=gdal.BuildVRTOptions(
                        srcNodata=nodata,
                        VRTNodata=nodata,
                    ),
                )

                warp_opts = gdal.WarpOptions(
                    format="VRT",
                    outputBounds=warp_bounds,
                    outputBoundsSRS="EPSG:3857",
                    dstSRS="EPSG:3857",
                    srcNodata=nodata,
                    dstNodata=nodata,
                    resampleAlg="bilinear",
                    multithread=False,
                    warpMemoryLimit=1024,
                )
                gdal.Warp(band_vrt, pre_vrt, options=warp_opts)
                per_band_vrts.append(band_vrt)

                pct = 20 + int(60 * b_idx / n_bands)
                self.progress_pct.emit(pct)
                self.progress.emit(self.tr("  Band {b} done.").format(b=band_name))

            if not per_band_vrts:
                self.error.emit(self.tr("No bands could be processed."))
                return

            out_vrt = str(tmp_dir / "mosaic.vrt")
            self.progress.emit(self.tr("Assembling multi-band VRT..."))
            gdal.BuildVRT(
                out_vrt, per_band_vrts,
                options=gdal.BuildVRTOptions(separate=True),
            )
            self.progress_pct.emit(85)
            self.progress.emit(self.tr("VRT ready."))

            out_tif = ""
            if p.get("export_tif") and p.get("out_tif_path"):
                out_tif = p["out_tif_path"]
                self.progress.emit(
                    self.tr("Exporting GeoTIFF to {p}  (may take a few minutes)...").format(
                        p=out_tif
                    )
                )
                self._export_tif(out_vrt, out_tif, p.get("compress", "DEFLATE"))
                self.progress.emit(self.tr("GeoTIFF export complete."))

            self.progress_pct.emit(100)
            self.finished.emit(out_vrt, out_tif)

        except Exception as exc:
            self.error.emit("{}\n{}".format(exc, traceback.format_exc()))

    def _export_tif(self, vrt_path, out_tif, compress):
        co  = ["TILED=YES", "COMPRESS={}".format(compress), "PREDICTOR=2", "BIGTIFF=IF_SAFER"]
        tmp = out_tif.replace(".tif", "_tmp.tif")
        gdal.Translate(tmp, vrt_path, format="GTiff", creationOptions=co)
        ds = gdal.Open(tmp, gdal.GA_Update)
        if ds:
            ds.BuildOverviews("NEAREST", [2, 4, 8, 16, 32])
            ds = None
        gdal.Translate(
            out_tif, tmp,
            format="GTiff",
            creationOptions=co + ["COPY_SRC_OVERVIEWS=YES"],
        )
        if os.path.exists(tmp):
            os.remove(tmp)
