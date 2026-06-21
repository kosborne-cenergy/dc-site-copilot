#!/usr/bin/env python3
"""TREE-CANOPY extraction for a parcel -> treed-area polygons + canopy stats.

Given a parcel polygon (GeoJSON Feature / FeatureCollection / bare geometry, or a
path to a .geojson file) this module pulls a public land-cover RASTER over the
parcel bbox, isolates the tree class, polygonizes the treed pixels, clips them to
the parcel, and reports the canopy percentage / acreage. Output feeds a site
exhibit (DXF + web map). NO API key, NO AI -- pure urllib/json + shapely/pyproj +
rasterio/numpy.

SOURCE (public, no key)
------------------------------------------------------------------
ESRI / Impact Observatory **Sentinel-2 10 m Land Cover** ImageServer:
    https://ic.imagery1.arcgis.com/arcgis/rest/services/Sentinel2_10m_LandCover/ImageServer
A 9-class global LULC product (10 m, Sentinel-2 derived, annually updated). The
ImageServer's `exportImage` op returns a clipped GeoTIFF over any bbox with NO
token (verified 2026-06-20). Class **value 2 = "Trees"** (legend confirmed live).

Why this over the dedicated NLCD Tree Canopy Cover (TCC) product: the ESRI/USGS
Living-Atlas NLCD layers now require an ArcGIS token ("Token Required"), the
MRLC geoserver WCS is 400/404-flaky, and the USFS canopy AGOL host (di-usfsdata)
is DNS-flaky from some networks. The io-LULC ImageServer is the reliable,
key-less, pixel-returning path -- so it is the primary. The `year=` arg is passed
through to the service's time dimension when supplied (the product is multitemporal).

NLCD TCC remains the gold standard for a *percent-canopy* raster; if a future
keyless TCC endpoint is wired in, swap `SOURCE_*` + the threshold branch in
`_classify_treed()` -- everything downstream (polygonize / clip / area) is
source-agnostic.

If raster sampling is "percent canopy" (0-100, e.g. NLCD TCC) instead of a class
label, set `CANOPY_PCT_THRESHOLD` (default 20%) -- pixels >= threshold = "treed".
For the categorical io-LULC source we simply test value == TREE_CLASS_VALUE.

COMPUTE  (rasterio polygonize + shapely; areas in true meters via pyproj)
------------------------------------------------------------------
  1. exportImage the bbox at ~10 m/px (size capped so the call stays light).
  2. read band 1 with rasterio (in-memory, never written to disk).
  3. treed_mask = (pixels are the tree class)  [or >= pct threshold].
  4. rasterio.features.shapes(mask) -> raw treed polygons in the raster CRS.
  5. union, reproject to the parcel CRS (4326), clip to the parcel polygon.
  6. simplify lightly for a clean exhibit; compute % + acres in a metric CRS.

PUBLIC API
------------------------------------------------------------------
  tree_canopy(parcel_geojson, year=None) -> dict
      {
        "canopy_geojson": <GeoJSON FeatureCollection of treed polygons, WGS84>,
        "canopy_pct":     float,   # % of parcel area that is tree canopy
        "canopy_acres":   float,   # treed acreage inside the parcel
        "source":         str,     # provenance / endpoint
        "status":         "ok" | "none",
      }
  On ANY failure it returns status "none" with an empty FeatureCollection -- it
  never raises.
"""
import json
import math
import pathlib
import ssl
import time
import urllib.parse
import urllib.request

# These public services occasionally present cert chains that trip the Windows
# trust store; all data here is public read-only. (Matches constraints.py.)
ssl._create_default_https_context = ssl._create_unverified_context

# --- geometry / projection deps ---------------------------------------------
from shapely.geometry import shape, mapping, box, MultiPolygon  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

try:
    from shapely.ops import transform as shp_transform
    from pyproj import Transformer, CRS

    _HAVE_PYPROJ = True
except Exception:  # pragma: no cover - pyproj should be present per env
    _HAVE_PYPROJ = False

# rasterio + numpy are required for the raster pull/polygonize. If they are
# somehow missing we degrade to status "none" rather than crashing the import.
try:
    import numpy as np
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.features import shapes as rio_shapes

    _HAVE_RASTER = True
except Exception:  # pragma: no cover
    _HAVE_RASTER = False

ROOT = pathlib.Path(__file__).resolve().parent.parent  # .../app
DATA = ROOT / "data"
try:
    DATA.mkdir(exist_ok=True)
except Exception:
    pass

ACRES_PER_M2 = 1.0 / 4046.8564224
USER_AGENT = "dc-site-copilot/1.0 (cenergy)"

# --- canopy source -----------------------------------------------------------
# ESRI / Impact Observatory Sentinel-2 10 m Land Cover (categorical, value 2 = Trees).
SOURCE_NAME = "ESRI/IO Sentinel-2 10m Land Cover (io-LULC) ImageServer"
SOURCE_URL = (
    "https://ic.imagery1.arcgis.com/arcgis/rest/services/"
    "Sentinel2_10m_LandCover/ImageServer"
)
TREE_CLASS_VALUE = 2  # io-LULC: 1=Water 2=Trees 4=FloodedVeg 5=Crops 7=Built ...

# If a *percent-canopy* raster is wired in instead (e.g. NLCD TCC), pixels with
# value >= this are treated as "treed". Unused for the categorical io-LULC path.
CANOPY_PCT_THRESHOLD = 20.0

# Target ground sample distance (m/px) and a hard pixel cap so exportImage stays
# light regardless of parcel size. ~10 m matches the native io-LULC resolution.
TARGET_GSD_M = 10.0
MAX_PIXELS = 1024  # per side; bbox is sampled at min(native, this) resolution

HTTP_RETRIES = 4
HTTP_TIMEOUT = 120


# =============================================================================
# low-level HTTP
# =============================================================================
def _http_get_bytes(url, retries=HTTP_RETRIES):
    """GET -> (bytes, content_type). Returns (None, err_str) on persistent failure."""
    last = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                return r.read(), r.headers.get("Content-Type", "")
        except Exception as e:
            last = "%s:%s" % (type(e).__name__, str(e)[:140])
            time.sleep(1.5)
    return None, last


# =============================================================================
# input normalization  (mirrors constraints._coerce_parcel)
# =============================================================================
def _coerce_parcel(parcel_geojson):
    """Accept a GeoJSON FeatureCollection (FIRST feature used) / Feature /
    geometry, a path to a .geojson file, or a (minlon,minlat,maxlon,maxlat)
    bbox, and return a single shapely (multi)polygon in EPSG:4326 + its bbox."""
    obj = parcel_geojson

    if isinstance(obj, (str, pathlib.Path)) and pathlib.Path(str(obj)).exists():
        with open(obj) as f:
            obj = json.load(f)

    if (
        isinstance(obj, (list, tuple))
        and len(obj) == 4
        and all(isinstance(v, (int, float)) for v in obj)
    ):
        geom = box(*obj)
        return geom, tuple(float(v) for v in obj)

    if isinstance(obj, dict):
        t = obj.get("type")
        if t == "FeatureCollection":
            feats = obj.get("features") or []
            if not feats:
                raise ValueError("FeatureCollection has no features")
            geom = shape(feats[0]["geometry"])
        elif t == "Feature":
            geom = shape(obj["geometry"])
        elif t in (
            "Polygon",
            "MultiPolygon",
            "Point",
            "LineString",
            "MultiPoint",
            "MultiLineString",
            "GeometryCollection",
        ):
            geom = shape(obj)
        else:
            raise ValueError("Unsupported GeoJSON type: %r" % t)
        geom = geom.buffer(0)  # fix self-intersections / winding
        return geom, tuple(geom.bounds)

    raise TypeError(
        "parcel must be a GeoJSON dict, a .geojson path, or a 4-number bbox; got %r"
        % type(obj)
    )


# =============================================================================
# projection helpers (EPSG:4326 <-> local meters)  (mirrors constraints.py)
# =============================================================================
def _metric_crs_for(lon, lat):
    """WGS84/UTM EPSG code for accurate area near (lon, lat)."""
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _make_transformers(lon, lat):
    if not _HAVE_PYPROJ:
        return None, None, None
    epsg = _metric_crs_for(lon, lat)
    to_m = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(epsg), always_xy=True)
    fwd = lambda g: shp_transform(to_m.transform, g)  # noqa: E731
    return fwd, epsg, None


def _area_acres_metric(geom_4326, fwd):
    """Area in acres of a 4326 geometry, projected to meters (UTM) if possible,
    else via a local equirectangular fallback."""
    if geom_4326 is None or geom_4326.is_empty:
        return 0.0
    if fwd is not None:
        return fwd(geom_4326).area * ACRES_PER_M2
    # equirectangular fallback
    c = geom_4326.centroid
    cos0 = math.cos(math.radians(c.y))
    R = 6378137.0
    g = shp_transform(
        lambda xs, ys: (
            [math.radians(x) * R * cos0 for x in xs],
            [math.radians(y) * R for y in ys],
        ),
        geom_4326,
    )
    return g.area * ACRES_PER_M2


def _utm_bbox_size(bbox, fwd):
    """Pixel grid size (cols, rows) for the bbox at TARGET_GSD_M, capped at
    MAX_PIXELS per side. Uses the metric projection of the bbox corners for a
    true ground span; falls back to a degree->meter approximation."""
    minx, miny, maxx, maxy = bbox
    if fwd is not None:
        try:
            bm = fwd(box(*bbox))
            mnx, mny, mxx, mxy = bm.bounds
            w_m, h_m = (mxx - mnx), (mxy - mny)
        except Exception:
            fwd = None
    if fwd is None:
        latc = 0.5 * (miny + maxy)
        w_m = (maxx - minx) * 111320.0 * math.cos(math.radians(latc))
        h_m = (maxy - miny) * 110540.0
    cols = max(2, min(MAX_PIXELS, int(round(w_m / TARGET_GSD_M))))
    rows = max(2, min(MAX_PIXELS, int(round(h_m / TARGET_GSD_M))))
    return cols, rows


# =============================================================================
# raster pull + classify
# =============================================================================
def _export_image(bbox, cols, rows, year=None):
    """exportImage a GeoTIFF over bbox (EPSG:4326) at cols x rows.
    Returns (bytes, used_url) or (None, err_str)."""
    params = {
        "bbox": "%s,%s,%s,%s" % tuple(bbox),
        "bboxSR": "4326",
        "imageSR": "4326",
        "size": "%d,%d" % (cols, rows),
        "format": "tiff",
        "pixelType": "U8",
        "interpolation": "RSP_NearestNeighbor",  # categorical: never blend classes
        "adjustAspectRatio": "false",
        "f": "image",
    }
    if year is not None:
        # io-LULC is multitemporal; pass a calendar-year time extent (ms epoch).
        try:
            import calendar

            y = int(year)
            t0 = int(calendar.timegm((y, 1, 1, 0, 0, 0)) * 1000)
            t1 = int(calendar.timegm((y, 12, 31, 23, 59, 59)) * 1000)
            params["time"] = "%d,%d" % (t0, t1)
        except Exception:
            pass
    url = SOURCE_URL + "/exportImage?" + urllib.parse.urlencode(params)
    data, ct = _http_get_bytes(url)
    if data is None:
        return None, ct  # ct holds the error string here
    # an ArcGIS error comes back as a tiny JSON body, not a TIFF
    if not (data[:4] in (b"II*\x00", b"MM\x00*")):
        try:
            j = json.loads(data.decode("utf-8", "replace"))
            return None, "arcgis_error:" + json.dumps(j.get("error", j))[:160]
        except Exception:
            return None, "non_tiff_response (%d bytes, ct=%s)" % (len(data), ct)
    return data, url


def _classify_treed(arr, source_is_pct=False):
    """bool mask of treed pixels. Categorical io-LULC: value == TREE_CLASS_VALUE.
    Percent-canopy raster: value >= CANOPY_PCT_THRESHOLD (and < 254 nodata)."""
    if source_is_pct:
        return (arr >= CANOPY_PCT_THRESHOLD) & (arr <= 100)
    return arr == TREE_CLASS_VALUE


def _polygonize_treed(tiff_bytes):
    """Read the GeoTIFF, build the treed mask, polygonize -> (shapely geom in the
    raster CRS, rasterio CRS, n_raw_polys). Returns (None, None, 0) if no canopy."""
    with MemoryFile(tiff_bytes) as mf, mf.open() as ds:
        band = ds.read(1)
        rcrs = ds.crs
        transform = ds.transform
    mask = _classify_treed(band)
    if not mask.any():
        return None, rcrs, 0
    mask8 = mask.astype("uint8")
    polys = []
    for geom, val in rio_shapes(mask8, mask=mask, transform=transform):
        if val == 1:
            try:
                g = shape(geom).buffer(0)
                if not g.is_empty:
                    polys.append(g)
            except Exception:
                continue
    if not polys:
        return None, rcrs, 0
    return unary_union(polys), rcrs, len(polys)


def _to_4326(geom, src_crs):
    """Reproject a shapely geom from src_crs to EPSG:4326 (no-op if already)."""
    if geom is None or geom.is_empty or src_crs is None:
        return geom
    try:
        src = CRS.from_user_input(src_crs)
        if src.to_epsg() == 4326:
            return geom
        tr = Transformer.from_crs(src, CRS.from_epsg(4326), always_xy=True)
        return shp_transform(tr.transform, geom)
    except Exception:
        return geom  # assume already 4326 if we can't reproject


def _as_feature_collection(geom_4326):
    """Explode a (multi)polygon into a FeatureCollection of Polygon features."""
    feats = []
    if geom_4326 is not None and not geom_4326.is_empty:
        if isinstance(geom_4326, MultiPolygon):
            parts = list(geom_4326.geoms)
        elif geom_4326.geom_type == "GeometryCollection":
            parts = [g for g in geom_4326.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
        else:
            parts = [geom_4326]
        for p in parts:
            if p.is_empty or p.geom_type not in ("Polygon", "MultiPolygon"):
                continue
            feats.append(
                {"type": "Feature", "geometry": mapping(p), "properties": {"kind": "tree_canopy"}}
            )
    return {"type": "FeatureCollection", "features": feats}


def _empty(source, status="none"):
    return {
        "canopy_geojson": {"type": "FeatureCollection", "features": []},
        "canopy_pct": 0.0,
        "canopy_acres": 0.0,
        "source": source,
        "status": status,
    }


# =============================================================================
# public API
# =============================================================================
def tree_canopy(parcel_geojson, year=None):
    """Tree canopy within a parcel.

    Args:
        parcel_geojson: GeoJSON FeatureCollection (FIRST feature used) / Feature /
            geometry, a path to a .geojson file, or a 4-number EPSG:4326 bbox.
        year: optional acquisition year passed to the land-cover service's time
            dimension (the io-LULC product is annual; None = service default/latest).

    Returns:
        {"canopy_geojson", "canopy_pct", "canopy_acres", "source", "status"}.
        Never raises -- any failure yields status "none" with an empty collection.
    """
    src = SOURCE_NAME
    try:
        if not _HAVE_RASTER:
            return _empty(src + " [rasterio/numpy unavailable]")

        parcel_4326, bbox = _coerce_parcel(parcel_geojson)
        if parcel_4326.is_empty:
            return _empty(src + " [empty parcel geometry]")

        cen = parcel_4326.centroid
        fwd, epsg, _ = _make_transformers(cen.x, cen.y)
        parcel_acres = _area_acres_metric(parcel_4326, fwd)

        cols, rows = _utm_bbox_size(bbox, fwd)
        tiff, used = _export_image(bbox, cols, rows, year=year)
        if tiff is None:
            return _empty(src + " [exportImage failed: %s]" % used)

        treed_raw, rcrs, n_raw = _polygonize_treed(tiff)
        if treed_raw is None:
            # raster fetched fine but parcel bbox has no tree pixels
            return {
                "canopy_geojson": {"type": "FeatureCollection", "features": []},
                "canopy_pct": 0.0,
                "canopy_acres": 0.0,
                "source": "%s (%dx%d px, value==%d=Trees)"
                % (src, cols, rows, TREE_CLASS_VALUE),
                "status": "ok",
            }

        treed_4326 = _to_4326(treed_raw, rcrs)

        # Simplify FIRST (light, for a clean exhibit), THEN clip -- clipping must
        # be the final op so no simplified vertex lands outside the parcel edge.
        deg_per_px = max((bbox[2] - bbox[0]) / cols, (bbox[3] - bbox[1]) / rows)
        treed_s = treed_4326.simplify(deg_per_px * 0.3, preserve_topology=True).buffer(0)
        if treed_s.is_empty:
            treed_s = treed_4326

        # clip to the parcel (the bbox export overshoots the polygon edges)
        clipped_s = treed_s.intersection(parcel_4326).buffer(0)
        if clipped_s.is_empty:
            return {
                "canopy_geojson": {"type": "FeatureCollection", "features": []},
                "canopy_pct": 0.0,
                "canopy_acres": 0.0,
                "source": "%s (%dx%d px)" % (src, cols, rows),
                "status": "ok",
            }

        canopy_acres = _area_acres_metric(clipped_s, fwd)
        canopy_pct = round(100.0 * canopy_acres / parcel_acres, 2) if parcel_acres else 0.0
        canopy_acres = round(canopy_acres, 3)

        fc = _as_feature_collection(clipped_s)
        return {
            "canopy_geojson": fc,
            "canopy_pct": canopy_pct,
            "canopy_acres": canopy_acres,
            "source": "%s (%dx%d px @ ~%.0fm, value==%d=Trees%s)"
            % (
                src,
                cols,
                rows,
                TARGET_GSD_M,
                TREE_CLASS_VALUE,
                "" if year is None else ", year=%s" % year,
            ),
            "status": "ok",
        }
    except Exception as e:  # never raise -- degrade to "none"
        return _empty("%s [error: %s:%s]" % (src, type(e).__name__, str(e)[:160]))


# =============================================================================
# self-test
# =============================================================================
def _test():
    sample = DATA / "parcel_sample.geojson"
    print("=" * 70)
    print("TREE CANOPY TEST  --  source:", SOURCE_NAME)
    print("=" * 70)
    if not sample.exists():
        print("No parcel_sample.geojson at", sample)
        return
    with open(sample) as f:
        fc = json.load(f)
    feats = fc.get("features", [])
    if not feats:
        print("parcel_sample.geojson is empty")
        return
    parcel_in = {"type": "FeatureCollection", "features": [feats[0]]}
    print("parcel: first of %d features\n" % len(feats))

    res = tree_canopy(parcel_in)

    print("status      :", res["status"])
    print("canopy_pct  :", res["canopy_pct"], "%")
    print("canopy_acres:", res["canopy_acres"])
    print("source      :", res["source"])
    print("polygons    :", len(res["canopy_geojson"]["features"]))

    # save the layer for the exhibit / web map
    try:
        out = DATA / "canopy_sample.geojson"
        with open(out, "w") as f:
            json.dump(res["canopy_geojson"], f, separators=(",", ":"))
        print("saved       :", out, "(%d KB)" % max(1, out.stat().st_size // 1024))
    except Exception as e:
        print("save skipped:", e)


if __name__ == "__main__":
    _test()
