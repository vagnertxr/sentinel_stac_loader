# -*- coding: utf-8 -*-
import os
import tempfile
import traceback
from pathlib import Path
from qgis.PyQt.QtCore import QThread, pyqtSignal, QCoreApplication
from osgeo import gdal, osr

from .indices import create_derived_vrt

gdal.UseExceptions()


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


class MosaicWorker(QThread):
    progress      = pyqtSignal(str)
    progress_pct  = pyqtSignal(int)       # 0-100
    finished      = pyqtSignal(str, str)
    error         = pyqtSignal(str)
    item_selected = pyqtSignal(str, str, str)

    def __init__(self, params):
        super().__init__()
        self.params = params
        self.formula = params.get("formula")

    def tr(self, msg):
        # Context kept as the pre-1.0 class name so existing pt/es .qm translations stay matched.
        return QCoreApplication.translate('SentinelSTACDialog', msg)

    def _search_items(self, catalog, attempts=3, **search_kwargs):
        """Run a STAC search, retrying on transient failures (e.g. Azure
        Front Door 504s on Planetary Computer) before giving up."""
        last_err = None
        for attempt in range(attempts):
            if self.isInterruptionRequested():
                return []
            try:
                search = catalog.search(**search_kwargs)
                return list(search.items())
            except Exception as e:
                last_err = e
                if attempt < attempts - 1:
                    self.progress.emit(
                        self.tr("Search request failed, retrying ({}/{})...").format(
                            attempt + 1, attempts - 1
                        )
                    )
                    self.msleep(4000)
        raise last_err

    def run(self):
        try:
            import pystac_client
            from shapely.geometry import box, mapping, shape

            p = self.params
            needs_signing = p.get("needs_signing", True)
            catalog_url = p.get("catalog_url", "https://planetarycomputer.microsoft.com/api/stac/v1")

            pc = None
            if needs_signing:
                import planetary_computer as pc

            gdal.SetConfigOption("GDAL_HTTP_MAX_RETRY", "5")
            gdal.SetConfigOption("GDAL_HTTP_RETRY_DELAY", "3")
            gdal.SetConfigOption("VSI_CACHE", "TRUE")
            gdal.SetConfigOption("GDAL_CACHEMAX", "512")
            gdal.SetConfigOption("GDAL_HTTP_TIMEOUT", "60")
            gdal.SetConfigOption("GDAL_HTTP_MERGE_CONSECUTIVE_HTTP_RETRIEVALS", "YES")
            # Some CBERS assets (e.g. WPM panchromatic, ~2GB, one strip per
            # pixel row, no overviews) are extremely inefficient to stream at
            # GDAL's tiny 16KB default chunk size - thousands of round trips.
            # A larger chunk/cache lets GDAL coalesce many strip reads into few requests.
            gdal.SetConfigOption("CPL_VSIL_CURL_CHUNK_SIZE", "1048576")
            gdal.SetConfigOption("VSI_CACHE_SIZE", "67108864")

            self.progress_pct.emit(0)
            self.progress.emit(self.tr("Connecting to STAC catalog..."))

            catalog = pystac_client.Client.open(
                catalog_url,
                modifier=pc.sign_inplace if needs_signing else None,
                # Fail fast rather than waiting out a CDN's own ~30s gateway
                # timeout on every attempt - our retry loop handles the rest.
                timeout=(10, 20),
            )

            bbox_coords     = p["bbox"]
            bbox_poly       = box(*bbox_coords).buffer(0)
            total_bbox_area = bbox_poly.area

            if p.get("items_list"):
                if needs_signing:
                    self.progress.emit(self.tr("Re-signing provided items..."))
                    all_items = [pc.sign(item) for item in p["items_list"]]
                else:
                    all_items = list(p["items_list"])
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

                all_items = self._search_items(
                    catalog,
                    collections=[p["collection"]],
                    intersects=mapping(bbox_poly),
                    datetime=f"{p['start_date']}/{p['end_date']}",
                    query={"eo:cloud_cover": {"lt": p["max_cloud"]}},
                    max_items=500,
                    sortby=[sort_param],
                )

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
                if self.isInterruptionRequested():
                    return
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

                # eo:cloud_cover is an explicit null for CBERS DN products
                # (WPM, PAN10M, PAN5M, WPM pansharpened TCI) - INPE doesn't
                # compute it for them.
                clouds      = item.properties.get("eo:cloud_cover")
                clouds_text = "N/A" if clouds is None else f"{clouds:.1f}%"
                dt          = item.properties.get("datetime", "")[:10]
                covered_pct = (1.0 - uncovered_area.area / total_bbox_area) * 100

                self.item_selected.emit(dt, clouds_text, item.id)
                self.progress.emit(
                    self.tr("  Added {id}  clouds={c}  coverage={pct:.1f}%").format(
                        id=item.id, c=clouds_text, pct=covered_pct
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

            if needs_signing:
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

            tmp_dir = Path(tempfile.mkdtemp(prefix="qgis_mosaic_"))
            per_band_vrts = []
            bands = p["bands"]
            nodata = p.get("nodata", 0)
            n_bands = len(bands)

            for b_idx, band_name in enumerate(bands, 1):
                if self.isInterruptionRequested():
                    return
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
                    self.tr("Mosaicking band {b} ({i}/{t}) - {n} scenes...").format(
                        b=band_name, i=b_idx, t=n_bands, n=len(urls)
                    )
                )

                pre_vrt = str(tmp_dir / f"pre_{b_idx:02d}_{band_name}.vrt")
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

            if len(per_band_vrts) != n_bands:
                self.error.emit(
                    self.tr("Only {done}/{total} requested bands could be processed.").format(
                        done=len(per_band_vrts), total=n_bands
                    )
                )
                return

            stack_vrt = str(tmp_dir / "mosaic_stack.vrt")
            self.progress.emit(self.tr("Assembling multi-band VRT..."))
            # Only stack as distinct bands when there's more than one single-band
            # source; a lone "band" that's actually a pre-fused multi-band asset
            # (e.g. a pansharpened TCI) must pass through with its native bands.
            gdal.BuildVRT(
                stack_vrt, per_band_vrts,
                options=gdal.BuildVRTOptions(separate=len(per_band_vrts) > 1),
            )

            final_vrt_path = stack_vrt
            if self.formula:
                final_vrt_path = str(tmp_dir / "mosaic_index.vrt")
                self.progress.emit(self.tr("Applying spectral index formula..."))
                create_derived_vrt(stack_vrt, final_vrt_path, self.formula)

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
                self._export_tif(final_vrt_path, out_tif, p.get("compress", "DEFLATE"))
                self.progress.emit(self.tr("GeoTIFF export complete."))

            self.progress_pct.emit(100)
            self.finished.emit(final_vrt_path, out_tif)

        except Exception as exc:
            summary = _summarize_error(str(exc))
            self.error.emit("{}\n\n{}\n{}".format(summary, exc, traceback.format_exc()))

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
