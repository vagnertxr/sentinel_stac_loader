# -*- coding: utf-8 -*-
"""
sentinel_automosaic.py
----------------------
Script standalone para criar mosaicos automáticos de Sentinel-2
via Microsoft Planetary Computer STAC API.

Uso:
    python sentinel_automosaic.py

Dependências:
    pip install pystac-client planetary-computer gdal shapely requests

Autor: baseado no plugin Quick VRT Imagery Loader (Vagner Teixeira)
"""

import os
import sys
import json
import tempfile
import datetime
import argparse
from pathlib import Path

# ── Verificação de dependências ──────────────────────────────────────────────
def check_deps():
    missing = []
    for pkg, imp in [("pystac-client", "pystac_client"),
                     ("planetary-computer", "planetary_computer"),
                     ("shapely", "shapely"),
                     ("requests", "requests")]:
        try:
            __import__(imp)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[ERRO] Instale as dependências: pip install {' '.join(missing)}")
        sys.exit(1)

check_deps()

import requests
import planetary_computer as pc
import pystac_client
from shapely.geometry import box, mapping

try:
    from osgeo import gdal, osr
    GDAL_OK = True
except ImportError:
    GDAL_OK = False
    print("[AVISO] GDAL não encontrado via osgeo. Usando subprocess como fallback.")
    import subprocess


# ── Composições de bandas disponíveis ───────────────────────────────────────
COMPOSITIONS = {
    # Sentinel-2
    "S2_TrueColor":       {"collection": "sentinel-2-l2a", "bands": ["B04", "B03", "B02"], "label": "True Color (RGB)"},
    "S2_FalseColorNIR":   {"collection": "sentinel-2-l2a", "bands": ["B08", "B04", "B03"], "label": "False Color NIR"},
    "S2_FalseColorSWIR":  {"collection": "sentinel-2-l2a", "bands": ["B11", "B08", "B04"], "label": "False Color SWIR"},
    "S2_Agriculture":     {"collection": "sentinel-2-l2a", "bands": ["B11", "B08", "B02"], "label": "Agriculture"},
    "S2_Geology":         {"collection": "sentinel-2-l2a", "bands": ["B12", "B11", "B02"], "label": "Geology"},
    "S2_Urban":           {"collection": "sentinel-2-l2a", "bands": ["B12", "B11", "B04"], "label": "Urban"},
    "S2_Vegetation":      {"collection": "sentinel-2-l2a", "bands": ["B08", "B11", "B04"], "label": "Vegetation Analysis"},
    "S2_NDVI_Visual":     {"collection": "sentinel-2-l2a", "bands": ["B08", "B04", "B03"], "label": "NDVI Visual"},
    # Landsat-8/9
    "L8_TrueColor":       {"collection": "landsat-c2-l2",  "bands": ["red", "green", "blue"],  "label": "Landsat True Color"},
    "L8_FalseColorNIR":   {"collection": "landsat-c2-l2",  "bands": ["nir08", "red", "green"], "label": "Landsat False Color NIR"},
}


# ── Funções principais ───────────────────────────────────────────────────────

def search_images(bbox, start_date, end_date, collection, max_cloud=20, max_items=10):
    """
    Busca cenas no Planetary Computer STAC e retorna lista de items
    ordenados por cobertura de nuvens (menor primeiro).
    """
    catalog = pystac_client.Client.open(
        "https://planetarycomputer.microsoft.com/api/stac/v1",
        modifier=pc.sign_inplace,
    )

    geometry = mapping(box(*bbox))  # (minx, miny, maxx, maxy)

    search = catalog.search(
        collections=[collection],
        intersects=geometry,
        datetime=f"{start_date}/{end_date}",
        query={"eo:cloud_cover": {"lt": max_cloud}},
        max_items=max_items,
        sortby=["+eo:cloud_cover"],
    )

    items = list(search.items())
    if not items:
        print("[INFO] Nenhuma imagem encontrada com os parâmetros informados.")
        return []

    # Ordena por nuvens
    items.sort(key=lambda i: i.properties.get("eo:cloud_cover", 999))
    print(f"[INFO] {len(items)} cena(s) encontrada(s).")
    for idx, item in enumerate(items):
        clouds = item.properties.get("eo:cloud_cover", "?")
        date   = item.properties.get("datetime", "?")[:10]
        print(f"  [{idx}] {date}  nuvens={clouds:.1f}%  id={item.id}")

    return items


def build_band_vrt(items, bands, bbox_epsg4326, out_vrt_path, nodata=0):
    """
    Constrói um VRT multi-banda mosaico a partir de múltiplas cenas STAC.
    Cada banda é um mosaico separado (VRT de VRTs).

    Parâmetros
    ----------
    items         : lista de pystac.Item (já assinados)
    bands         : lista de nomes de banda, ex: ["B04", "B03", "B02"]
    bbox_epsg4326 : (minx, miny, maxx, maxy) em EPSG:4326
    out_vrt_path  : caminho do VRT final de saída
    nodata        : valor NoData (padrão 0)
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="automosaic_"))
    per_band_vrts = []

    for band_idx, band_name in enumerate(bands, start=1):
        band_urls = []
        for item in items:
            if band_name in item.assets:
                href = item.assets[band_name].href
                band_urls.append(f"/vsicurl/{href}")
            else:
                print(f"[AVISO] Banda {band_name} não encontrada em {item.id}, pulando.")

        if not band_urls:
            print(f"[ERRO] Nenhuma URL encontrada para banda {band_name}.")
            continue

        # VRT do mosaico desta banda
        band_vrt = str(tmp_dir / f"band_{band_idx}_{band_name}.vrt")

        if GDAL_OK:
            vrt_opts = gdal.BuildVRTOptions(
                resampleAlg="bilinear",
                srcNodata=nodata,
                VRTNodata=nodata,
                outputBounds=_reproject_bbox(bbox_epsg4326),  # em UTM ou projeção nativa
                separate=False,
            )
            ds = gdal.BuildVRT(band_vrt, band_urls, options=vrt_opts)
            ds.FlushCache()
            ds = None
        else:
            _buildvrt_subprocess(band_vrt, band_urls, nodata)

        per_band_vrts.append(band_vrt)
        print(f"  [✓] Banda {band_name} → {band_vrt}")

    if not per_band_vrts:
        raise RuntimeError("Nenhuma banda processada com sucesso.")

    # VRT final multi-banda (separate=True → empilha as bandas)
    if GDAL_OK:
        vrt_opts = gdal.BuildVRTOptions(
            separate=True,
            srcNodata=nodata,
            VRTNodata=nodata,
        )
        ds = gdal.BuildVRT(out_vrt_path, per_band_vrts, options=vrt_opts)
        ds.FlushCache()
        ds = None
    else:
        _buildvrt_subprocess(out_vrt_path, per_band_vrts, nodata, separate=True)

    print(f"\n[✓] VRT mosaico criado: {out_vrt_path}")
    return out_vrt_path


def export_geotiff(vrt_path, out_tif_path, compress="DEFLATE", overview=True):
    """
    Exporta o VRT como GeoTIFF Cloud-Optimized (COG).
    """
    print(f"\n[INFO] Exportando GeoTIFF... (pode demorar dependendo da área)")

    creation_opts = [
        "TILED=YES",
        "COMPRESS=" + compress,
        "PREDICTOR=2",
        "BIGTIFF=IF_SAFER",
        "COPY_SRC_OVERVIEWS=YES",
    ]

    if GDAL_OK:
        # Primeiro passa: traduZ para TIF temporário
        tmp_tif = out_tif_path.replace(".tif", "_tmp.tif")
        translate_opts = gdal.TranslateOptions(
            format="GTiff",
            creationOptions=creation_opts,
        )
        gdal.Translate(tmp_tif, vrt_path, options=translate_opts)

        if overview:
            ds = gdal.Open(tmp_tif, gdal.GA_Update)
            ds.BuildOverviews("NEAREST", [2, 4, 8, 16, 32])
            ds = None

        # Segunda passa: COG real
        gdal.Translate(out_tif_path, tmp_tif, options=translate_opts)
        os.remove(tmp_tif)

    else:
        cmd = [
            "gdal_translate",
            "-of", "GTiff",
            "-co", "TILED=YES",
            "-co", f"COMPRESS={compress}",
            "-co", "PREDICTOR=2",
            "-co", "BIGTIFF=IF_SAFER",
            vrt_path, out_tif_path,
        ]
        subprocess.run(cmd, check=True)
        if overview:
            subprocess.run(["gdaladdo", "-r", "nearest", out_tif_path,
                            "2", "4", "8", "16", "32"], check=True)

    size_mb = os.path.getsize(out_tif_path) / 1e6
    print(f"[✓] GeoTIFF exportado: {out_tif_path}  ({size_mb:.1f} MB)")
    return out_tif_path


# ── Helpers internos ─────────────────────────────────────────────────────────

def _reproject_bbox(bbox_epsg4326):
    """Retorna o bbox no sistema original (4326) — GDAL reprojetará automaticamente."""
    return bbox_epsg4326  # BuildVRT lida com reprojeção via /vsicurl


def _buildvrt_subprocess(out_vrt, inputs, nodata, separate=False):
    cmd = ["gdalbuildvrt", "-srcnodata", str(nodata), "-vrtnodata", str(nodata)]
    if separate:
        cmd.append("-separate")
    cmd.append(out_vrt)
    cmd.extend(inputs)
    import subprocess
    subprocess.run(cmd, check=True)


# ── Interface de linha de comando ────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Cria mosaicos Sentinel-2/Landsat via STAC e exporta como VRT ou GeoTIFF."
    )
    p.add_argument("--bbox",   required=True,
                   help="Bounding box: 'minx,miny,maxx,maxy' em EPSG:4326")
    p.add_argument("--start",  required=True, help="Data inicial (YYYY-MM-DD)")
    p.add_argument("--end",    required=True, help="Data final   (YYYY-MM-DD)")
    p.add_argument("--comp",   default="S2_TrueColor",
                   choices=list(COMPOSITIONS.keys()),
                   help="Composição de bandas (padrão: S2_TrueColor)")
    p.add_argument("--clouds", type=float, default=20,
                   help="Máximo de nuvens %% (padrão: 20)")
    p.add_argument("--items",  type=int, default=10,
                   help="Máximo de cenas a buscar (padrão: 10)")
    p.add_argument("--out",    default="mosaic",
                   help="Prefixo do arquivo de saída (padrão: mosaic)")
    p.add_argument("--export-tif", action="store_true",
                   help="Exportar GeoTIFF além do VRT")
    p.add_argument("--compress", default="DEFLATE",
                   choices=["DEFLATE", "LZW", "ZSTD", "NONE"],
                   help="Compressão do GeoTIFF (padrão: DEFLATE)")
    p.add_argument("--list-comps", action="store_true",
                   help="Lista composições disponíveis e sai")
    return p.parse_args()


def main():
    args = parse_args()

    if args.list_comps:
        print("\nComposições disponíveis:")
        for key, val in COMPOSITIONS.items():
            print(f"  {key:<22} → {val['label']}")
        return

    # Parse bbox
    try:
        bbox = tuple(float(x) for x in args.bbox.split(","))
        assert len(bbox) == 4
    except Exception:
        print("[ERRO] --bbox deve ser 'minx,miny,maxx,maxy'")
        sys.exit(1)

    comp     = COMPOSITIONS[args.comp]
    out_vrt  = args.out + ".vrt"
    out_tif  = args.out + ".tif"

    print(f"\n{'='*55}")
    print(f"  Auto-Mosaic Sentinel-2 / Landsat")
    print(f"{'='*55}")
    print(f"  Composição : {comp['label']}")
    print(f"  Coleção    : {comp['collection']}")
    print(f"  Bandas     : {', '.join(comp['bands'])}")
    print(f"  Bbox       : {bbox}")
    print(f"  Período    : {args.start} → {args.end}")
    print(f"  Nuvens máx : {args.clouds}%")
    print(f"{'='*55}\n")

    # 1. Busca
    items = search_images(
        bbox=bbox,
        start_date=args.start,
        end_date=args.end,
        collection=comp["collection"],
        max_cloud=args.clouds,
        max_items=args.items,
    )
    if not items:
        sys.exit(0)

    # 2. Monta VRT
    build_band_vrt(items, comp["bands"], bbox, out_vrt)

    # 3. (Opcional) Exporta GeoTIFF
    if args.export_tif:
        export_geotiff(out_vrt, out_tif, compress=args.compress)

    print(f"\n✅ Concluído! Arquivos gerados:")
    print(f"   VRT : {os.path.abspath(out_vrt)}")
    if args.export_tif:
        print(f"   TIF : {os.path.abspath(out_tif)}")


if __name__ == "__main__":
    main()
