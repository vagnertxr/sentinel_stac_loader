# -*- coding: utf-8 -*-
import os
import tempfile
from pathlib import Path
from qgis.PyQt.QtCore import QThread, pyqtSignal, QCoreApplication
from osgeo import gdal, osr

class MosaicWorker(QThread):
    progress  = pyqtSignal(str)        # log messages
    finished  = pyqtSignal(str, str)   # (vrt_path, tif_path_or_empty)
    error     = pyqtSignal(str)
    item_selected = pyqtSignal(str, str, str) # (date, clouds, id)

    def __init__(self, params):
        super().__init__()
        self.params = params

    def tr(self, message):
        return QCoreApplication.translate('SentinelSTACDialogBase', message)

    def run(self):
        try:
            import planetary_computer as pc
            import pystac_client
            from shapely.geometry import box, mapping, shape

            p = self.params
            self.progress.emit(self.tr("Connecting to Planetary Computer STAC…"))

            catalog = pystac_client.Client.open(
                "https://planetarycomputer.microsoft.com/api/stac/v1",
                modifier=pc.sign_inplace,
            )

            bbox_coords = p["bbox"] # [W, S, E, N] in 4326
            bbox_poly = box(*bbox_coords)
            total_bbox_area = bbox_poly.area

            if p.get("items_list"):
                # Use provided items (e.g. from Browser tab)
                all_items = [pc.sign(item) for item in p["items_list"]]
            else:
                self.progress.emit(
                    self.tr("Searching for scenes ({}) from {} to {}, clouds < {}%…").format(
                        p["collection"], p["start_date"], p["end_date"], p["max_cloud"]
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

            # --- Selection Logic (Ordered by preference, covering until 100%) ---
            selected_items = []
            selected_ids = set()
            uncovered_area = bbox_poly
            
            self.progress.emit(self.tr("Analyzing scenes to reach 100% coverage…"))
            
            for item in all_items:
                if uncovered_area.is_empty or uncovered_area.area <= 1e-9:
                    break
                if len(selected_items) >= p.get("max_items", 100):
                    break
                
                item_geom = shape(item.geometry)
                contribution = item_geom.intersection(uncovered_area)
                
                # Only pick if it covers more than 0.05% of the total area
                if contribution.area > (total_bbox_area * 0.0005):
                    selected_items.append(item)
                    selected_ids.add(item.id)
                    uncovered_area = uncovered_area.difference(item_geom)
                    
                    clouds = item.properties.get("eo:cloud_cover", 0)
                    dt = item.properties.get("datetime", "")[:10]
                    covered_pct = (1.0 - (uncovered_area.area / total_bbox_area)) * 100
                    
                    self.item_selected.emit(dt, f"{clouds:.1f}%", item.id)
                    self.progress.emit(self.tr("  [+] Added {} (Coverage: {:.1f}%)").format(
                        item.id, covered_pct
                    ))

            if not selected_items:
                self.error.emit(self.tr("No images selected after spatial filtering."))
                return

            final_coverage = (1.0 - (uncovered_area.area / total_bbox_area)) * 100
            self.progress.emit(self.tr("Final coverage reached: {:.1f}%").format(final_coverage))

            # --- VRT / Mosaic Generation using GDAL Warp (Robust for multiple CRSs) ---
            tmp_dir = Path(tempfile.mkdtemp(prefix="qgis_mosaic_"))
            per_band_vrts = []
            
            # Use EPSG:3857 as target for the mosaic to handle any projection
            target_crs = "EPSG:3857"
            
            # Project 4326 Bbox to 3857 for Warp outputBounds
            s_srs = osr.SpatialReference(); s_srs.ImportFromEPSG(4326)
            t_srs = osr.SpatialReference(); t_srs.ImportFromEPSG(3857)
            tx = osr.CoordinateTransformation(s_srs, t_srs)
            # Transform corners to get bounds (W, S, E, N)
            p1 = tx.TransformPoint(bbox_coords[1], bbox_coords[0]) # lat, lon
            p2 = tx.TransformPoint(bbox_coords[3], bbox_coords[2])
            warp_bounds = [p1[0], p1[1], p2[0], p2[1]] # minx, miny, maxx, maxy in 3857

            for b_idx, band_name in enumerate(p["bands"], 1):
                urls = []
                for item in selected_items:
                    if band_name in item.assets:
                        urls.append(f"/vsicurl/{item.assets[band_name].href}")
                
                if not urls:
                    continue

                self.progress.emit(self.tr("Mosaicking band {} ({}/{})…").format(band_name, b_idx, len(p['bands'])))
                band_vrt = str(tmp_dir / f"band_{b_idx}_{band_name}.vrt")
                
                # Use GDAL Warp to create a virtual mosaic (handles reprojection and bounds)
                warp_opts = gdal.WarpOptions(
                    format="VRT",
                    outputBounds=warp_bounds,
                    dstSRS=target_crs,
                    srcNodata=p["nodata"],
                    dstNodata=p["nodata"],
                    resampleAlg="bilinear",
                    multithread=True
                )
                gdal.Warp(band_vrt, urls, options=warp_opts)
                per_band_vrts.append(band_vrt)

            if not per_band_vrts:
                self.error.emit(self.tr("No bands could be processed."))
                return

            # Final multi-band VRT (Combine warped bands)
            out_vrt = str(tmp_dir / "mosaic.vrt")
            self.progress.emit(self.tr("Finalizing VRT…"))
            gdal.BuildVRT(out_vrt, per_band_vrts, options=gdal.BuildVRTOptions(separate=True))

            # Optional GeoTIFF Export
            out_tif = ""
            if p.get("export_tif") and p.get("out_tif_path"):
                out_tif = p["out_tif_path"]
                self.progress.emit(self.tr("Exporting GeoTIFF → {}\n(this may take a few minutes…)").format(out_tif))
                self._export_tif(out_vrt, out_tif, p.get("compress", "DEFLATE"))

            self.finished.emit(out_vrt, out_tif)

        except Exception as exc:
            import traceback
            self.error.emit(f"{str(exc)}\n{traceback.format_exc()}")

    def _export_tif(self, vrt_path, out_tif, compress):
        co = ["TILED=YES", f"COMPRESS={compress}", "PREDICTOR=2", "BIGTIFF=IF_SAFER"]
        tmp = out_tif.replace(".tif", "_tmp.tif")
        gdal.Translate(tmp, vrt_path, format="GTiff", creationOptions=co)
        ds = gdal.Open(tmp, gdal.GA_Update)
        ds.BuildOverviews("NEAREST", [2, 4, 8, 16, 32])
        ds = None
        gdal.Translate(out_tif, tmp, format="GTiff", creationOptions=co + ["COPY_SRC_OVERVIEWS=YES"])
        if os.path.exists(tmp): os.remove(tmp)
