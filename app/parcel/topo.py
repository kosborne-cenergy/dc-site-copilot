#!/usr/bin/env python3
"""Elevation contours + slope for a parcel from USGS 3DEP -- for a site exhibit.

Public source, NO API key, NO AI. Pure urllib/json (+ numpy, matplotlib, shapely;
rasterio optional). Standalone module -- imports nothing else in this repo.

PUBLIC SOURCE (no key)
  USGS 3DEP Elevation ImageServer (1m/3m/10m seamless DEM, F32 meters):
    https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer
  Verified 2026-06-20: pixelType F32, native SR 3857, capabilities Image,Metadata,Catalog.

PIPELINE
  1. bbox(parcel) in WGS84 (padded a hair so edge contours close cleanly).
  2. Pull a modest (default 60x60) elevation grid over that bbox:
       PRIMARY  exportImage -> GeoTIFF (f=image), read with rasterio if present.
       FALLBACK getSamples  -> elevation at each grid node (no rasterio needed).
     Grid is sampled in WGS84 so the pixel<->lon/lat mapping is a simple affine.
  3. Convert grid meters -> feet; contour at interval_ft via matplotlib.contour
     (marching squares); each contour polyline's grid coords map back to lon/lat.
  4. Slope (%) from the gradient of the elevation surface in TRUE ground units
     (degrees -> meters via the local-latitude scale), reported mean + max.

RETURNS a dict (see topo() docstring). NEVER raises -- on any failure returns
{"status": "none", ...} with a "source"/"error" note so the caller can degrade
gracefully and still build the rest of the exhibit.
"""
import io
import json
import math
import ssl
import urllib.parse
import urllib.request

import numpy as np

# matplotlib only for marching-squares contour extraction -- force a headless
# backend so this works in a server/worker process with no display.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from shapely.geometry import shape  # noqa: E402

# rasterio is OPTIONAL: used only to read the exportImage GeoTIFF. If it is not
# installed (or fails to import its GDAL deps) we transparently fall back to the
# getSamples node-sampling path, which needs nothing but urllib.
try:
    import rasterio  # noqa: E402
    _HAVE_RASTERIO = True
except Exception:  # pragma: no cover - environment dependent
    _HAVE_RASTERIO = False

# USGS's cert chain can trip Windows trust stores; the service is public read-only.
ssl._create_default_https_context = ssl._create_unverified_context

IMAGESERVER = (
    "https://elevation.nationalmap.gov/arcgis/rest/services/"
    "3DEPElevation/ImageServer"
)
_UA = {"User-Agent": "dc-site-copilot/1.0 (cenergy)"}

M_PER_FT = 0.3048
FT_PER_M = 1.0 / M_PER_FT          # 3.280839895...
EARTH_R = 6378137.0               # WGS84 semi-major axis, meters


# -----------------------------------------------------------------------------
# low-level HTTP (the 3DEP gateway throws transient 502s -- always retry)
# -----------------------------------------------------------------------------
def _http(url, data=None, timeout=120, tries=3):
    """Return raw response bytes. POST if `data` (dict) given, else GET. Retries
    transient gateway errors. Raises only after all tries fail."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request(url, data=body, headers=_UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # transient 502/timeout/reset -> retry
            last = e
    raise last


def _http_json(url, data=None, timeout=120, tries=3):
    raw = _http(url, data=data, timeout=timeout, tries=tries)
    obj = json.loads(raw.decode("utf-8"))
    if isinstance(obj, dict) and "error" in obj:
        raise RuntimeError("ArcGIS error: " + json.dumps(obj["error"])[:300])
    return obj


# -----------------------------------------------------------------------------
# elevation grid acquisition
# -----------------------------------------------------------------------------
def _grid_via_export(bbox, n):
    """PRIMARY path: exportImage -> GeoTIFF -> numpy. Needs rasterio.

    Returns an (n_rows, n_cols) float32 array of ELEVATION IN METERS, oriented so
    row 0 = TOP of the image (max latitude), matching the affine used by _grid_xy.
    Raises if rasterio missing or the request/parse fails.
    """
    if not _HAVE_RASTERIO:
        raise RuntimeError("rasterio not available")
    min_lon, min_lat, max_lon, max_lat = bbox
    params = {
        "bbox": "%.10f,%.10f,%.10f,%.10f" % (min_lon, min_lat, max_lon, max_lat),
        "bboxSR": "4326",
        "imageSR": "4326",
        "size": "%d,%d" % (n, n),
        "format": "tiff",
        "pixelType": "F32",
        "interpolation": "RSP_BilinearInterpolation",
        "f": "image",
    }
    url = IMAGESERVER + "/exportImage?" + urllib.parse.urlencode(params)
    raw = _http(url, timeout=120)
    if not raw[:2] in (b"II", b"MM"):           # not a TIFF -> probably a JSON error
        raise RuntimeError("exportImage did not return a TIFF")
    with rasterio.open(io.BytesIO(raw)) as ds:
        arr = ds.read(1).astype("float64")
        nodata = ds.nodata
    # mask nodata / sentinel values to NaN so contouring/slope ignore them
    arr = np.where(np.isfinite(arr), arr, np.nan)
    if nodata is not None:
        arr = np.where(arr == nodata, np.nan, arr)
    arr = np.where(arr < -1e5, np.nan, arr)     # 3DEP voids show as huge negatives
    # rasterio row 0 is the north edge already (standard north-up GeoTIFF).
    return arr


def _grid_via_samples(bbox, n):
    """FALLBACK path: getSamples at each of the n*n grid nodes. No rasterio.

    Returns an (n, n) float64 array of ELEVATION IN METERS with row 0 = TOP
    (max latitude), columns west->east -- same orientation as _grid_via_export.
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    lons = np.linspace(min_lon, max_lon, n)
    lats = np.linspace(max_lat, min_lat, n)      # top row = max lat
    pts = [[float(lon), float(lat)] for lat in lats for lon in lons]

    # getSamples preserves input point ORDER in its samples list, but be defensive:
    # build the grid by nearest-node index from each returned location.
    geom = {"points": pts, "spatialReference": {"wkid": 4326}}
    out = _http_json(
        IMAGESERVER + "/getSamples",
        data={
            "geometryType": "esriGeometryMultipoint",
            "geometry": json.dumps(geom),
            "returnFirstValueOnly": "true",
            "f": "json",
        },
        timeout=180,
    )
    samples = out.get("samples", []) or []
    grid = np.full((n, n), np.nan, dtype="float64")
    dlon = (max_lon - min_lon) or 1.0
    dlat = (max_lat - min_lat) or 1.0
    for s in samples:
        val = s.get("value")
        loc = s.get("location") or {}
        try:
            v = float(val)
        except (TypeError, ValueError):
            continue
        lon = loc.get("x")
        lat = loc.get("y")
        if lon is None or lat is None:
            continue
        c = int(round((lon - min_lon) / dlon * (n - 1)))
        r = int(round((max_lat - lat) / dlat * (n - 1)))   # top row = max lat
        if 0 <= r < n and 0 <= c < n:
            grid[r, c] = v
    if not np.isfinite(grid).any():
        raise RuntimeError("getSamples returned no usable elevations")
    return grid


def _fill_nans(grid):
    """Fill scattered NaNs with the grid's nanmean so contour/slope stay defined.
    (3DEP coverage in the lower-48 is essentially complete; this only patches the
    rare edge void so a few NaNs don't blow a whole contour level.)"""
    if not np.isnan(grid).any():
        return grid
    m = np.nanmean(grid)
    return np.where(np.isnan(grid), m, grid)


# -----------------------------------------------------------------------------
# grid index -> lon/lat affine (north-up, row 0 = max lat)
# -----------------------------------------------------------------------------
def _grid_xy(col, row, bbox, n):
    """Map fractional grid (col, row) -> (lon, lat). col in [0,n-1] west->east,
    row in [0,n-1] north->south. matplotlib contour returns vertices in this
    (col=x, row=y) index space."""
    min_lon, min_lat, max_lon, max_lat = bbox
    lon = min_lon + (col / (n - 1)) * (max_lon - min_lon)
    lat = max_lat - (row / (n - 1)) * (max_lat - min_lat)
    return lon, lat


# -----------------------------------------------------------------------------
# slope (% grade) from the elevation surface, in TRUE ground units
# -----------------------------------------------------------------------------
def _slope_pct(elev_m, bbox, n):
    """Return (mean_slope_pct, max_slope_pct) of the rise/run gradient.

    Cell ground spacing differs in x vs y because 1 deg lon shrinks by cos(lat):
      dy (meters) = d(lat) * EARTH_R * pi/180
      dx (meters) = d(lon) * EARTH_R * pi/180 * cos(lat0)
    slope% = 100 * sqrt((dz/dx)^2 + (dz/dy)^2).
    """
    min_lon, min_lat, max_lon, max_lat = bbox
    lat0 = math.radians((min_lat + max_lat) / 2.0)
    deg_m = EARTH_R * math.pi / 180.0
    dlat = (max_lat - min_lat) / (n - 1)
    dlon = (max_lon - min_lon) / (n - 1)
    dy = abs(dlat) * deg_m or 1.0
    dx = abs(dlon) * deg_m * math.cos(lat0) or 1.0
    # np.gradient: axis 0 = rows (north-south, spacing dy), axis 1 = cols (e-w, dx)
    gy, gx = np.gradient(elev_m, dy, dx)
    grade = np.sqrt(gx * gx + gy * gy) * 100.0
    grade = grade[np.isfinite(grade)]
    if grade.size == 0:
        return 0.0, 0.0
    return float(np.mean(grade)), float(np.max(grade))


# -----------------------------------------------------------------------------
# contour extraction (matplotlib marching squares) -> GeoJSON LineStrings
# -----------------------------------------------------------------------------
def _contours_geojson(elev_ft, bbox, n, interval_ft):
    """Contour the feet-grid at every multiple of interval_ft between its min and
    max, convert each polyline's grid vertices to lon/lat, return a GeoJSON
    FeatureCollection of LineStrings tagged with properties.elev_ft."""
    zmin = float(np.nanmin(elev_ft))
    zmax = float(np.nanmax(elev_ft))
    # contour levels on the interval grid strictly inside [zmin, zmax]
    lo = math.floor(zmin / interval_ft) * interval_ft
    levels = []
    lvl = lo
    while lvl <= zmax + 1e-9:
        if zmin < lvl < zmax:           # interior levels only (endpoints never close)
            levels.append(round(lvl, 6))
        lvl += interval_ft
    features = []
    if not levels:
        return {"type": "FeatureCollection", "features": features}, levels

    fig = plt.figure()
    try:
        ax = fig.add_subplot(111)
        cs = ax.contour(elev_ft, levels=levels)
        # matplotlib >=3.8: cs.allsegs aligns with cs.levels (the levels actually used)
        used = list(cs.levels)
        for lev, segs in zip(used, cs.allsegs):
            for seg in segs:
                if len(seg) < 2:
                    continue
                coords = []
                for (cx, cy) in seg:        # cx=col(x), cy=row(y) in grid index space
                    lon, lat = _grid_xy(float(cx), float(cy), bbox, n)
                    # cast to native float: numpy scalars break strict GeoJSON/DXF consumers
                    coords.append([round(float(lon), 8), round(float(lat), 8)])
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": coords},
                    "properties": {"elev_ft": round(float(lev), 2)},
                })
    finally:
        plt.close(fig)

    return {"type": "FeatureCollection", "features": features}, levels


# -----------------------------------------------------------------------------
# public API
# -----------------------------------------------------------------------------
def topo(parcel_geojson, interval_ft=2, grid=60, pad_frac=0.04):
    """Elevation contours + slope for a parcel, from USGS 3DEP.

    Args:
        parcel_geojson: a GeoJSON geometry, Feature, or FeatureCollection. If a
            FeatureCollection, ALL features' extents are unioned for the bbox.
            (Pass a single Feature/geometry to scope to one parcel.)
        interval_ft: contour interval in feet (default 2).
        grid: elevation grid resolution per side (default 60 -> 60x60 samples).
        pad_frac: fraction of bbox span to pad on each edge so boundary contours
            close cleanly (default 0.04 = 4%).

    Returns dict:
        {
          "contours_geojson": <FeatureCollection of LineStrings, props.elev_ft, WGS84>,
          "interval_ft":   <int/float>,
          "min_elev_ft":   <float>,
          "max_elev_ft":   <float>,
          "mean_slope_pct":<float>,
          "max_slope_pct": <float>,
          "source":        <str>,
          "status":        "ok" | "none",
        }
      On ANY failure returns status "none" (never raises), with an "error" note
      and empty contour collection so the caller can still build the exhibit.
    """
    src = IMAGESERVER
    fail = lambda msg: {                      # noqa: E731 - tiny local helper
        "contours_geojson": {"type": "FeatureCollection", "features": []},
        "interval_ft": interval_ft,
        "min_elev_ft": None,
        "max_elev_ft": None,
        "mean_slope_pct": None,
        "max_slope_pct": None,
        "source": src,
        "status": "none",
        "error": msg,
    }
    try:
        # --- bbox of the parcel(s) in WGS84 ---------------------------------
        gj = parcel_geojson
        if isinstance(gj, dict) and gj.get("type") == "FeatureCollection":
            geoms = [shape(f["geometry"]) for f in gj.get("features", [])
                     if f.get("geometry")]
            if not geoms:
                return fail("no geometry in FeatureCollection")
            from shapely.ops import unary_union
            geom = unary_union(geoms)
        elif isinstance(gj, dict) and gj.get("type") == "Feature":
            geom = shape(gj["geometry"])
        else:
            geom = shape(gj)                  # bare geometry dict

        min_lon, min_lat, max_lon, max_lat = geom.bounds
        # pad so edge contours/slope aren't clipped at the parcel boundary
        dlon = (max_lon - min_lon) or 1e-4
        dlat = (max_lat - min_lat) or 1e-4
        bbox = (
            min_lon - dlon * pad_frac, min_lat - dlat * pad_frac,
            max_lon + dlon * pad_frac, max_lat + dlat * pad_frac,
        )
        n = int(grid)
        if n < 4:
            n = 4

        # --- elevation grid (meters): exportImage+rasterio, else getSamples --
        elev_m = None
        try:
            elev_m = _grid_via_export(bbox, n)
            src = IMAGESERVER + " (exportImage/GeoTIFF)"
        except Exception:
            elev_m = _grid_via_samples(bbox, n)
            src = IMAGESERVER + " (getSamples)"

        elev_m = _fill_nans(elev_m)
        if not np.isfinite(elev_m).any():
            return fail("no elevation data returned")

        elev_ft = elev_m * FT_PER_M
        min_ft = float(np.nanmin(elev_ft))
        max_ft = float(np.nanmax(elev_ft))

        mean_slope, max_slope = _slope_pct(elev_m, bbox, n)
        fc, _levels = _contours_geojson(elev_ft, bbox, n, float(interval_ft))
        fc["_source"] = src

        return {
            "contours_geojson": fc,
            "interval_ft": interval_ft,
            "min_elev_ft": round(min_ft, 2),
            "max_elev_ft": round(max_ft, 2),
            "mean_slope_pct": round(mean_slope, 2),
            "max_slope_pct": round(max_slope, 2),
            "source": src,
            "status": "ok",
        }
    except Exception as e:                     # absolutely never raise
        return fail(repr(e)[:300])


# -----------------------------------------------------------------------------
# self-test
# -----------------------------------------------------------------------------
def _test():
    import pathlib
    sample = (
        pathlib.Path(__file__).resolve().parent.parent
        / "data" / "parcel_sample.geojson"
    )
    with open(sample) as f:
        fc = json.load(f)
    feat0 = fc["features"][0]                 # scope to the FIRST parcel only

    print("PARCEL   : %s (%s ac) in %s"
          % (feat0["properties"].get("parcel_id"),
             feat0["properties"].get("acreage"),
             feat0["properties"].get("locality")))

    res = topo(feat0, interval_ft=2)

    print("STATUS   : %s" % res["status"])
    print("SOURCE   : %s" % res["source"])
    if res["status"] != "ok":
        print("ERROR    : %s" % res.get("error"))
        return
    print("ELEV ft  : min %.2f  max %.2f  (relief %.2f ft)"
          % (res["min_elev_ft"], res["max_elev_ft"],
             res["max_elev_ft"] - res["min_elev_ft"]))
    print("SLOPE %%   : mean %.2f  max %.2f"
          % (res["mean_slope_pct"], res["max_slope_pct"]))
    feats = res["contours_geojson"]["features"]
    print("CONTOURS : %d LineString(s) at %s-ft interval"
          % (len(feats), res["interval_ft"]))
    if feats:
        elevs = sorted({fe["properties"]["elev_ft"] for fe in feats})
        print("LEVELS   : %s" % elevs)
        v0 = feats[0]["geometry"]["coordinates"][:2]
        print("SAMPLE   : elev_ft=%s  first verts=%s"
              % (feats[0]["properties"]["elev_ft"], v0))


if __name__ == "__main__":
    _test()
