#!/usr/bin/env python3
"""ALL existing building footprints in/near a parcel -> a GeoJSON polygon layer.

Where ``dwellings.py`` finds the single nearest *residential* dwelling, this
module returns **every** building footprint (any occupancy class) that intersects
the parcel plus a small buffer, as polygons -- the "existing structures" context
layer for the site exhibit (DXF + web map).

NO API key, NO AI -- pure urllib/json + shapely + pyproj (matches parcels.py /
constraints.py / dwellings.py / dxf_export.py house style).

DATA SOURCE: FEMA / Oak Ridge "USA Structures" building footprints, the SAME
public ArcGIS REST FeatureServer view ``dwellings.py`` uses (discovered + verified
2026-06-20 -- see dwellings.py header for the catalog walk). We hit layer 0's
``/query`` with ``f=geojson`` + ``esriSpatialRelIntersects`` and paginate to the
service ``maxRecordCount`` (2000). Unlike dwellings.py we do NOT filter on
``OCC_CLS`` -- we want ALL footprints -- and we KEEP the polygon geometry rather
than reducing each to a representative point.

DISTANCE/BUFFER: the parcel is buffered outward by ``buffer_ft`` (default 200 ft)
in true ground meters (UTM zone from the parcel centroid) so we catch structures
straddling / just outside the boundary; that buffered footprint's lon/lat bbox is
the ArcGIS query envelope, and each returned footprint is kept only if it actually
intersects the buffered parcel (the envelope is a superset).

PUBLIC API
----------
    buildings_in_parcel(parcel, buffer_ft=200) -> dict

    Returns this EXACT shape:
      {
        "buildings_geojson": <GeoJSON FeatureCollection of building footprint
                              polygons (WGS84) intersecting parcel + buffer>,
        "count":  int,
        "status": "ok" | "none",
        ... (a few provenance keys: source, endpoint, buffer_ft)
      }
    On ANY failure (no parcel, source down, nothing found) returns the same dict
    with an empty FeatureCollection, count 0, status "none" -- NEVER raises.
"""
import json
import math
import pathlib
import ssl
import time
import urllib.parse
import urllib.request

# These public services occasionally present cert chains that trip the Windows
# trust store; all data here is public, read-only. (Matches the sibling modules.)
ssl._create_default_https_context = ssl._create_unverified_context

from shapely.geometry import shape, mapping, Point, box  # noqa: E402

try:
    from shapely.ops import transform as shp_transform
    from pyproj import Transformer, CRS

    _HAVE_PYPROJ = True
except Exception:  # pragma: no cover - pyproj is present per env
    _HAVE_PYPROJ = False

ROOT = pathlib.Path(__file__).resolve().parent.parent  # .../app
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

FT_PER_M = 3.280839895
M_PER_FT = 1.0 / FT_PER_M
USER_AGENT = "dc-site-copilot/1.0 (cenergy)"
HTTP_TIMEOUT = 120
HTTP_RETRIES = 3

# --- source: FEMA / Oak Ridge USA Structures (same public view as dwellings) --
USA_STRUCTURES_URL = (
    "https://services2.arcgis.com/FiaPA4ga0iQKduv3/ArcGIS/rest/services/"
    "USA_Structures_View/FeatureServer/0/query"
)
# keep the occupancy descriptors as attributes (no WHERE filter -> all classes)
USA_OUTFIELDS = "OCC_CLS,PRIM_OCC,PROP_ADDR"
USA_PAGE = 2000  # service maxRecordCount


# =============================================================================
# low-level HTTP
# =============================================================================
def _http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


# =============================================================================
# input normalization (accept Feature / FeatureCollection / geometry / path)
# =============================================================================
def _coerce_parcel(parcel):
    """Accept a GeoJSON Feature / FeatureCollection (first feature) / geometry,
    a [lon, lat] point, a 4-number bbox, a shapely geometry, or a path to a
    .geojson file -> return a single shapely geometry in EPSG:4326 (or None)."""
    obj = parcel

    if isinstance(obj, (str, pathlib.Path)) and pathlib.Path(str(obj)).exists():
        with open(obj) as f:
            obj = json.load(f)

    try:
        from shapely.geometry.base import BaseGeometry

        if isinstance(obj, BaseGeometry):
            return obj if not obj.is_empty else None
    except Exception:
        pass

    if isinstance(obj, (list, tuple)) and all(
        isinstance(v, (int, float)) for v in obj
    ):
        if len(obj) == 2:
            return Point(float(obj[0]), float(obj[1]))
        if len(obj) == 4:
            return box(*(float(v) for v in obj))
        raise ValueError("numeric parcel must be [lon,lat] or a 4-number bbox")

    if isinstance(obj, dict):
        t = obj.get("type")
        if t == "FeatureCollection":
            feats = obj.get("features") or []
            if not feats:
                return None
            geom = shape(feats[0]["geometry"])
        elif t == "Feature":
            geom = shape(obj["geometry"])
        elif t in (
            "Polygon", "MultiPolygon", "Point", "MultiPoint",
            "LineString", "MultiLineString", "GeometryCollection",
        ):
            geom = shape(obj)
        else:
            raise ValueError("Unsupported GeoJSON type: %r" % t)
        if geom.is_empty:
            return None
        if geom.geom_type in ("Polygon", "MultiPolygon"):
            geom = geom.buffer(0)
            if geom.is_empty:
                return None
        return geom

    raise TypeError(
        "parcel must be a GeoJSON dict, a .geojson path, a [lon,lat] point, a "
        "4-number bbox, or a shapely geometry; got %r" % type(parcel)
    )


# =============================================================================
# projection helpers (EPSG:4326 <-> UTM meters) -- same approach as dwellings.py
# =============================================================================
def _utm_epsg(lon, lat):
    """WGS84/UTM EPSG for (lon,lat). VA spans 17N (EPSG:32617) and 18N (32618)."""
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _make_transformers(lon, lat):
    """(to_metric, to_geo) shapely-compatible transforms, or (None, None) if
    pyproj is missing (then we degrade to an equirectangular metric approx)."""
    if not _HAVE_PYPROJ:
        return None, None
    epsg = _utm_epsg(lon, lat)
    to_m = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(epsg), always_xy=True)
    to_geo = Transformer.from_crs(CRS.from_epsg(epsg), CRS.from_epsg(4326), always_xy=True)
    fwd = lambda g: shp_transform(to_m.transform, g)  # noqa: E731
    inv = lambda g: shp_transform(to_geo.transform, g)  # noqa: E731
    return fwd, inv


def _equirect_transformers(lon0, lat0):
    """Fallback metric transforms via a local equirectangular projection centered
    on (lon0,lat0). Used only when pyproj is unavailable."""
    R = 6378137.0
    cos0 = math.cos(math.radians(lat0))

    def fwd(g):
        return shp_transform(
            lambda xs, ys: (
                [math.radians(x) * R * cos0 for x in xs],
                [math.radians(y) * R for y in ys],
            ),
            g,
        )

    def inv(g):
        return shp_transform(
            lambda xs, ys: (
                [math.degrees(x / (R * cos0)) for x in xs],
                [math.degrees(y / R) for y in ys],
            ),
            g,
        )

    return fwd, inv


# =============================================================================
# fetch: USA Structures footprints intersecting a bbox (ALL occupancy classes)
# =============================================================================
def _fetch_usa_footprints(bbox):
    """Query ALL USA-Structures footprints intersecting `bbox` (4326).

    Returns (list[shapely geometry in 4326], status_str). Paginates up to the
    service maxRecordCount. On persistent failure returns ([], error_str)."""
    geoms = []
    offset = 0
    last_err = None
    while True:
        params = {
            "where": "1=1",                       # ALL footprints, no occ filter
            "geometry": "%s,%s,%s,%s" % tuple(bbox),
            "geometryType": "esriGeometryEnvelope",
            "inSR": "4326",
            "outSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": USA_OUTFIELDS,
            "returnGeometry": "true",
            "resultOffset": offset,
            "resultRecordCount": USA_PAGE,
            "f": "geojson",
        }
        url = USA_STRUCTURES_URL + "?" + urllib.parse.urlencode(params)

        data = None
        for _ in range(HTTP_RETRIES):
            try:
                raw = _http_get(url)
                if raw.lstrip().startswith("<"):  # HTML error page
                    last_err = "html_error_page"
                    time.sleep(1.0)
                    continue
                d = json.loads(raw)
                if isinstance(d, dict) and d.get("error"):
                    last_err = "arcgis_error:" + json.dumps(d["error"])[:160]
                    time.sleep(1.0)
                    continue
                data = d
                break
            except Exception as e:  # transport / decode -> retry
                last_err = "%s:%s" % (type(e).__name__, str(e)[:120])
                time.sleep(1.0)

        if data is None:
            # transport failed on this page; return what we have if any.
            return (geoms, ("ok_partial" if geoms else (last_err or "failed")))

        feats = data.get("features", []) or []
        for ft in feats:
            try:
                g = shape(ft["geometry"])
            except Exception:
                continue
            if g.is_empty:
                continue
            if g.geom_type in ("Polygon", "MultiPolygon"):
                g = g.buffer(0)  # repair winding / self-intersection
                if g.is_empty:
                    continue
            geoms.append(g)

        exceeded = bool(
            data.get("exceededTransferLimit")
            or data.get("properties", {}).get("exceededTransferLimit")
        )
        if len(feats) < USA_PAGE and not exceeded:
            break
        if not feats:
            break
        offset += len(feats)
        if offset > 200000:  # hard safety stop
            break

    return geoms, ("ok (%d footprints)" % len(geoms))


# =============================================================================
# public API
# =============================================================================
def _none_result(buffer_ft, status_note, count=0):
    return {
        "buildings_geojson": {"type": "FeatureCollection", "features": []},
        "count": count,
        "status": "none",
        "source": None,
        "endpoint": None,
        "buffer_ft": float(buffer_ft),
        "note": status_note,
    }


def buildings_in_parcel(parcel, buffer_ft=200):
    """Return ALL existing building footprints in / near `parcel`.

    Args:
        parcel: a GeoJSON Feature / FeatureCollection (first feature) / geometry,
            a [lon, lat] point, a 4-number bbox, a shapely geometry, or a path to
            a .geojson file. EPSG:4326 assumed.
        buffer_ft: outward buffer (ft) added to the parcel before selecting
            footprints, so structures straddling / just outside the boundary are
            included (default 200 ft).

    Returns:
        dict: {buildings_geojson (FeatureCollection of footprint polygons, WGS84),
               count (int), status "ok"|"none", + provenance keys}.
        Never raises -- any failure returns an empty FC with status "none".
    """
    # ---- 1. coerce + validate the parcel -----------------------------------
    try:
        parcel_4326 = _coerce_parcel(parcel)
    except Exception as e:  # noqa: BLE001
        return _none_result(buffer_ft, "bad parcel input: %s" % e)
    if parcel_4326 is None or parcel_4326.is_empty:
        return _none_result(buffer_ft, "empty/None parcel geometry")

    cen = parcel_4326.centroid

    # ---- 2. buffer the parcel outward (in meters) for the selection area ----
    fwd, inv = _make_transformers(cen.x, cen.y)
    if fwd is None:  # pyproj missing -> equirectangular metric fallback
        fwd, inv = _equirect_transformers(cen.x, cen.y)
        crs_note = "equirectangular (pyproj missing)"
    else:
        crs_note = "UTM meters"

    buffer_m = float(buffer_ft) * M_PER_FT
    try:
        parcel_m = fwd(parcel_4326)
        buffered_m = parcel_m.buffer(buffer_m) if buffer_m > 0 else parcel_m
        buffered_4326 = inv(buffered_m)
    except Exception as e:  # noqa: BLE001
        return _none_result(buffer_ft, "buffer/projection failed: %s" % e)

    minx, miny, maxx, maxy = buffered_4326.bounds
    bbox = (minx, miny, maxx, maxy)

    # ---- 3. fetch ALL footprints intersecting the bbox ---------------------
    try:
        geoms, src_status = _fetch_usa_footprints(bbox)
    except Exception as e:  # noqa: BLE001
        return _none_result(buffer_ft, "usa_structures fetch failed: %s" % e)

    if not geoms:
        return _none_result(buffer_ft, "no footprints returned: %s" % src_status)

    # ---- 4. keep only footprints that intersect the buffered parcel --------
    feats = []
    for g in geoms:
        try:
            if not buffered_4326.intersects(g):
                continue
        except Exception:
            continue
        feats.append({
            "type": "Feature",
            "geometry": mapping(g),
            "properties": {"kind": "existing_building"},
        })

    if not feats:
        return _none_result(
            buffer_ft,
            "%d footprints in bbox but none intersect parcel+buffer" % len(geoms),
        )

    return {
        "buildings_geojson": {"type": "FeatureCollection", "features": feats},
        "count": len(feats),
        "status": "ok",
        "source": "USA_Structures",
        "endpoint": USA_STRUCTURES_URL,
        "buffer_ft": float(buffer_ft),
        "projection": crs_note,
        "source_status": src_status,
    }


# =============================================================================
# self-test
# =============================================================================
def _test():
    sample = DATA / "parcel_sample.geojson"
    if sample.exists():
        with open(sample) as f:
            fc = json.load(f)
        feats = fc.get("features", [])
        if feats:
            parcel_in = feats[0]
            src = "parcel_sample.geojson (first of %d features)" % len(feats)
        else:
            parcel_in = [-78.45, 38.13]
            src = "fallback point [-78.45, 38.13] (sample empty)"
    else:
        parcel_in = [-78.45, 38.13]
        src = "fallback point [-78.45, 38.13] (no sample file)"

    print("=" * 70)
    print("BUILDINGS IN PARCEL TEST")
    print("=" * 70)
    print("parcel source : %s" % src)
    print("buffer_ft     : 200")
    print()

    t = time.time()
    result = buildings_in_parcel(parcel_in, buffer_ft=200)
    print("elapsed       : %.1f s" % (time.time() - t))
    print()
    print("status        : %s" % result["status"])
    print("count         : %s" % result["count"])
    print("source        : %s" % result.get("source"))
    print("note          : %s" % result.get("note", result.get("source_status")))
    nfeat = len(result["buildings_geojson"]["features"])
    print("features in FC : %d" % nfeat)


if __name__ == "__main__":
    _test()
