# -*- coding: utf-8 -*-
"""Helpers for GDAL VRT-derived spectral index layers."""

from xml.sax.saxutils import escape


INDEX_FORMULAS = {
    "ndvi": {
        "description": "NDVI",
        "band_count": 2,
        "code": """import numpy as np
def ndvi(in_ar, out_ar, xoff, yoff, xsize, ysize, raster_xsize, raster_ysize, buf_xsize, buf_ysize, res):
    nir = in_ar[0].astype(np.float32)
    red = in_ar[1].astype(np.float32)
    denom = nir + red
    with np.errstate(divide="ignore", invalid="ignore"):
        out_ar[:] = np.where(denom != 0, (nir - red) / denom, np.nan)
""",
    },
    "ndwi": {
        "description": "NDWI",
        "band_count": 2,
        "code": """import numpy as np
def ndwi(in_ar, out_ar, xoff, yoff, xsize, ysize, raster_xsize, raster_ysize, buf_xsize, buf_ysize, res):
    green = in_ar[0].astype(np.float32)
    nir = in_ar[1].astype(np.float32)
    denom = green + nir
    with np.errstate(divide="ignore", invalid="ignore"):
        out_ar[:] = np.where(denom != 0, (green - nir) / denom, np.nan)
""",
    },
    "ndmi": {
        "description": "NDMI",
        "band_count": 2,
        "code": """import numpy as np
def ndmi(in_ar, out_ar, xoff, yoff, xsize, ysize, raster_xsize, raster_ysize, buf_xsize, buf_ysize, res):
    nir = in_ar[0].astype(np.float32)
    swir1 = in_ar[1].astype(np.float32)
    denom = nir + swir1
    with np.errstate(divide="ignore", invalid="ignore"):
        out_ar[:] = np.where(denom != 0, (nir - swir1) / denom, np.nan)
""",
    },
    "evi": {
        "description": "EVI",
        "band_count": 3,
        "code": """import numpy as np
def evi(in_ar, out_ar, xoff, yoff, xsize, ysize, raster_xsize, raster_ysize, buf_xsize, buf_ysize, res):
    nir = in_ar[0].astype(np.float32) * 0.0001
    red = in_ar[1].astype(np.float32) * 0.0001
    blue = in_ar[2].astype(np.float32) * 0.0001
    denom = nir + 6.0 * red - 7.5 * blue + 1.0
    with np.errstate(divide="ignore", invalid="ignore"):
        out_ar[:] = np.where(denom != 0, 2.5 * ((nir - red) / denom), np.nan)
""",
    },
}


def create_derived_vrt(source_vrt, out_vrt, formula):
    """Create a one-band Float32 VRT using a named spectral index formula."""
    from osgeo import gdal

    formula_key = (formula or "").lower()
    spec = INDEX_FORMULAS.get(formula_key)
    if spec is None:
        raise ValueError("Unsupported index formula: {}".format(formula))

    gdal.SetConfigOption("GDAL_VRT_ENABLE_PYTHON", "YES")
    gdal.SetConfigOption("VRT_ENABLE_PYTHON", "YES")

    ds = gdal.Open(source_vrt)
    if ds is None:
        raise RuntimeError("Could not open source VRT: {}".format(source_vrt))

    try:
        if ds.RasterCount < spec["band_count"]:
            raise RuntimeError(
                "{} needs {} source bands, but the VRT has {}.".format(
                    spec["description"], spec["band_count"], ds.RasterCount
                )
            )
        xsize = ds.RasterXSize
        ysize = ds.RasterYSize
        try:
            gt = ds.GetGeoTransform(can_return_null=True)
        except TypeError:
            gt = ds.GetGeoTransform()
        proj = ds.GetProjection()
    finally:
        ds = None

    sources = []
    for band_idx in range(1, spec["band_count"] + 1):
        sources.append(
            """    <SimpleSource>
      <SourceFilename relativeToVRT="0">{source}</SourceFilename>
      <SourceBand>{band}</SourceBand>
    </SimpleSource>""".format(
                source=escape(source_vrt),
                band=band_idx,
            )
        )

    geo_xml = ""
    if proj:
        geo_xml += '  <SRS dataAxisToSRSAxisMapping="1,2">{}</SRS>\n'.format(
            escape(proj)
        )
    if gt:
        geo_xml += "  <GeoTransform>{}</GeoTransform>\n".format(
            ", ".join("{:.16g}".format(v) for v in gt)
        )

    vrt_xml = """<VRTDataset rasterXSize="{xsize}" rasterYSize="{ysize}">
{geo_xml}  <VRTRasterBand dataType="Float32" band="1" subClass="VRTDerivedRasterBand">
    <Description>{description}</Description>
    <NoDataValue>nan</NoDataValue>
    <ColorInterp>Gray</ColorInterp>
    <PixelFunctionType>{formula}</PixelFunctionType>
    <PixelFunctionLanguage>Python</PixelFunctionLanguage>
    <PixelFunctionCode><![CDATA[{code}]]></PixelFunctionCode>
{sources}
  </VRTRasterBand>
</VRTDataset>
""".format(
        xsize=xsize,
        ysize=ysize,
        geo_xml=geo_xml,
        description=spec["description"],
        formula=formula_key,
        code=spec["code"],
        sources="\n".join(sources),
    )

    with open(out_vrt, "w", encoding="utf-8") as f:
        f.write(vrt_xml)
