#!/usr/bin/env python3
"""Nearest RESIDENTIAL dwelling to a parcel, with offset distance + a draw line.

Given a parcel (GeoJSON Feature / FeatureCollection / geometry / [lon,lat] point),
this finds the closest residential building within a search radius and returns the
distance from the PARCEL EDGE to that building, the building point, and a GeoJSON
LineString feature (parcel-edge -> dwelling) the DXF exporter draws on the
DWELLING-OFFSET layer.

NO API key, NO AI -- pure urllib/json + shapely + pyproj (matches parcels.py /
constraints.py / dxf_export.py house style).

DATA SOURCE (primary): FEMA / Oak Ridge "USA Structures" building footprints,
published as a public ArcGIS REST FeatureServer VIEW on the FEMA org. Discovered +
verified 2026-06-20 by walking the org's service catalog:
  catalog  https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services?f=json
           -> "USA_Structures_View" (FeatureServer)
  layer 0  .../USA_Structures_View/FeatureServer/0?f=json
           -> name "USA_Structures_B", esriGeometryPolygon, maxRecordCount 2000,
              supportsPagination=true, supports f=geoJSON + esriSpatialRelIntersects.

  Occupancy field (confirmed via /0/query returnDistinctValues on OCC_CLS):
      OCC_CLS  (string) top-level occupancy class. Distinct values include
      Agriculture, Assembly, Commercial, Education, Government, Industrial,
      Mixed Use, Residential, Unclassified, Utility and Misc.
      -> residential filter = OCC_CLS = 'Residential'
      (PRIM_OCC carries the finer descriptor: Single Family Dwelling, Multi-Family
       Dwelling, Manufactured Home, etc. -- not needed for the screen.)

  Live check 2026-06-20: a ~3 mi envelope over rural Lunenburg Co, VA returned
  1,939 Residential footprints as GeoJSON polygons in ~1.3 s.

FALLBACK: if every USA-Structures attempt fails (transport error / ArcGIS error /
empty), fall back to OpenStreetMap via the Overpass API -- buildings tagged as a
residential type (building in {house, residential, detached, semidetached_house,
apartments, terrace, bungalow, dormitory, cabin, farm, ...}) within the same
radius. Overpass returns ways/relations; we use each element's `center` (Overpass
`out center`) as the building point.

DISTANCE: everything is projected to METERS in the appropriate UTM zone (17N
EPSG:32617 west of -78 deg, 18N EPSG:32618 east of -78 deg) chosen from the parcel
centroid, so the parcel-edge -> building distance is true ground meters; reported
in feet (x3.280839895) and meters.

PUBLIC API
----------
    nearest_dwelling(parcel, search_miles=3) -> dict

    Returns this EXACT shape (parcel_app.py consumes it):
      {
        "nearest_dwelling_ft": float | None,
        "nearest_dwelling_m":  float | None,
        "dwelling_point":      [lon, lat] | None,
        "line":   <GeoJSON LineString Feature: parcel-edge -> dwelling> | None,
        "count_within_search": int,
        "status": "ok" | "none",
        ... (a few provenance keys: source, endpoint, search_miles)
      }
    On ANY failure (no parcel, both sources down, nothing in range) returns the
    same dict with status "none" and null distance fields -- never raises.
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
from shapely.ops import unary_union  # noqa: E402

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
METERS_PER_MILE = 1609.344
USER_AGENT = "dc-site-copilot/1.0 (cenergy)"
HTTP_TIMEOUT = 120
HTTP_RETRIES = 3

# --- primary source: FEMA / Oak Ridge USA Structures (public ArcGIS view) ----
USA_STRUCTURES_URL = (
    "https://services2.arcgis.com/FiaPA4ga0iQKduv3/ArcGIS/rest/services/"
    "USA_Structures_View/FeatureServer/0/query"
)
OCC_FIELD = "OCC_CLS"
OCC_RESIDENTIAL = "Residential"
USA_OUTFIELDS = "OCC_CLS,PRIM_OCC,PROP_ADDR"
USA_PAGE = 2000  # service maxRecordCount

# --- fallback source: OpenStreetMap Overpass --------------------------------
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
# building= values we treat as a dwelling for the OSM fallback.
OSM_RESIDENTIAL = (
    "house|residential|detached|semidetached_house|apartments|terrace|"
    "bungalow|dormitory|cabin|farm|houseboat|static_caravan|manufactured_home"
)


# =============================================================================
# low-level HTTP
# =============================================================================
def _http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def _http_post(url, data_str):
    req = urllib.request.Request(
        url,
        data=data_str.encode("utf-8"),
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


# =============================================================================
# input normalization
# =============================================================================
def _coerce_parcel(parcel):
    """Accept a GeoJSON Feature / FeatureCollection (first feature) / geometry,
    a [lon, lat] point, a 4-number bbox, a shapely geometry, or a path to a
    .geojson file -> return a single shapely geometry in EPSG:4326 (or None).

    A bare [lon, lat] becomes a Point; a 4-number bbox becomes its rectangle.
    """
    obj = parcel

    # path to a .geojson file
    if isinstance(obj, (str, pathlib.Path)) and pathlib.Path(str(obj)).exists():
        with open(obj) as f:
            obj = json.load(f)

    # already a shapely geometry
    try:
        from shapely.geometry.base import BaseGeometry

        if isinstance(obj, BaseGeometry):
            return obj if not obj.is_empty else None
    except Exception:
        pass

    # [lon, lat] point  OR  [min_lon, min_lat, max_lon, max_lat] bbox
    if isinstance(obj, (list, tuple)) and all(
        isinstance(v, (int, float)) for v in obj
    ):
        if len(obj) == 2:
            return Point(float(obj[0]), float(obj[1]))
        if len(obj) == 4:
            return box(*(float(v) for v in obj))
        raise ValueError("numeric parcel must be [lon,lat] or a 4-number bbox")

    # GeoJSON dict
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
        # repair winding / self-intersection on polygons only
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
# projection helpers (EPSG:4326 <-> UTM meters)
# =============================================================================
def _utm_epsg(lon, lat):
    """WGS84/UTM EPSG for (lon,lat). VA spans 17N (EPSG:32617) and 18N (32618)."""
    zone = int((lon + 180) // 6) + 1
    return (32600 if lat >= 0 else 32700) + zone


def _make_transformers(lon, lat):
    """(to_metric, to_geo, epsg) shapely-compatible transforms, or (None,None,None)
    if pyproj is missing (then we degrade to an equirectangular metric approx)."""
    if not _HAVE_PYPROJ:
        return None, None, None
    epsg = _utm_epsg(lon, lat)
    to_m = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(epsg), always_xy=True)
    to_geo = Transformer.from_crs(CRS.from_epsg(epsg), CRS.from_epsg(4326), always_xy=True)
    fwd = lambda g: shp_transform(to_m.transform, g)  # noqa: E731
    inv = lambda g: shp_transform(to_geo.transform, g)  # noqa: E731
    return fwd, inv, epsg


def _equirect_transformers(lon0, lat0):
    """Fallback metric transforms via a local equirectangular projection centered
    on (lon0,lat0). Used only when pyproj is unavailable. Distances are good to
    well under 1% over a few-mile search radius."""
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

    return fwd, inv, None


def _bbox_4326(lon, lat, radius_m):
    """Lon/lat envelope padded by ~radius_m around (lon,lat)."""
    dlat = radius_m / 111320.0
    dlon = radius_m / (111320.0 * max(math.cos(math.radians(lat)), 1e-6))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


# =============================================================================
# building point extraction
# =============================================================================
def _feature_point_4326(geom):
    """Representative [lon,lat] for a building geometry (centroid for a polygon,
    the point itself for a point). Uses representative_point() so the point is
    guaranteed to lie on/in the footprint."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "Point":
        return [geom.x, geom.y]
    try:
        p = geom.representative_point()
    except Exception:
        p = geom.centroid
    return [p.x, p.y]


# =============================================================================
# primary fetch: USA Structures (FEMA / Oak Ridge)
# =============================================================================
def _fetch_usa_structures(bbox):
    """Query residential USA-Structures footprints intersecting `bbox` (4326).

    Returns (list[[lon,lat], ...] building points, status_str). Paginates up to
    the service maxRecordCount. On persistent failure returns ([], error_str)."""
    points = []
    offset = 0
    last_err = None
    while True:
        params = {
            "where": "%s = '%s'" % (OCC_FIELD, OCC_RESIDENTIAL),
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
            # transport failed on this page; if we already have some points,
            # return what we have, else signal failure.
            return (points, ("ok_partial" if points else (last_err or "failed")))

        feats = data.get("features", []) or []
        for ft in feats:
            try:
                g = shape(ft["geometry"])
            except Exception:
                continue
            pt = _feature_point_4326(g)
            if pt is not None:
                points.append(pt)

        # stop when the page wasn't full (last page) or no transfer-limit hit
        exceeded = bool(data.get("exceededTransferLimit") or data.get("properties", {}).get("exceededTransferLimit"))
        if len(feats) < USA_PAGE and not exceeded:
            break
        if not feats:
            break
        offset += len(feats)
        if offset > 200000:  # hard safety stop
            break

    return points, ("ok (%d residential)" % len(points))


# =============================================================================
# fallback fetch: OpenStreetMap Overpass
# =============================================================================
def _fetch_osm(bbox):
    """Overpass query for residential buildings in `bbox` (4326).

    Returns (list[[lon,lat]], status_str). `bbox` -> Overpass order is
    (south, west, north, east). Uses `out center` so ways/relations carry a
    centroid we can use directly."""
    min_lon, min_lat, max_lon, max_lat = bbox
    s, w, n, e = min_lat, min_lon, max_lat, max_lon
    q = (
        "[out:json][timeout:60];("
        '  way["building"~"^(%s)$"](%f,%f,%f,%f);'
        '  relation["building"~"^(%s)$"](%f,%f,%f,%f);'
        ");out center;"
    ) % (OSM_RESIDENTIAL, s, w, n, e, OSM_RESIDENTIAL, s, w, n, e)

    last_err = None
    for ep in OVERPASS_URLS:
        for _ in range(HTTP_RETRIES):
            try:
                raw = _http_post(ep, "data=" + urllib.parse.quote(q))
                d = json.loads(raw)
                pts = []
                for el in d.get("elements", []):
                    if "center" in el:
                        pts.append([el["center"]["lon"], el["center"]["lat"]])
                    elif el.get("type") == "node" and "lon" in el:
                        pts.append([el["lon"], el["lat"]])
                return pts, "ok_osm (%d residential) via %s" % (len(pts), ep)
            except Exception as ex:
                last_err = "%s:%s" % (type(ex).__name__, str(ex)[:120])
                time.sleep(1.5)
    return [], "osm_failed:" + str(last_err)


# =============================================================================
# public API
# =============================================================================
def _none_result(search_miles, status_note, count=0):
    return {
        "nearest_dwelling_ft": None,
        "nearest_dwelling_m": None,
        "dwelling_point": None,
        "line": None,
        "count_within_search": count,
        "status": "none",
        "source": None,
        "endpoint": None,
        "search_miles": search_miles,
        "note": status_note,
    }


def nearest_dwelling(parcel, search_miles=3):
    """Find the nearest residential building to `parcel` within `search_miles`.

    Args:
        parcel: a GeoJSON Feature / FeatureCollection (first feature) / geometry,
            a [lon, lat] point, a 4-number bbox, a shapely geometry, or a path to
            a .geojson file. EPSG:4326 assumed.
        search_miles: search radius from the parcel, in miles (default 3).

    Returns:
        dict (the exact shape parcel_app.py consumes):
          nearest_dwelling_ft, nearest_dwelling_m, dwelling_point [lon,lat],
          line (GeoJSON LineString Feature parcel-edge -> dwelling),
          count_within_search (int), status "ok"|"none", + provenance keys.
        Never raises -- any failure returns status "none" with null distances.
    """
    # ---- 1. coerce + validate the parcel -----------------------------------
    try:
        parcel_4326 = _coerce_parcel(parcel)
    except Exception as e:  # noqa: BLE001
        return _none_result(search_miles, "bad parcel input: %s" % e)
    if parcel_4326 is None or parcel_4326.is_empty:
        return _none_result(search_miles, "empty/None parcel geometry")

    radius_m = float(search_miles) * METERS_PER_MILE
    cen = parcel_4326.centroid
    bbox = _bbox_4326(cen.x, cen.y, radius_m)

    # ---- 2. fetch residential building points (primary -> fallback) --------
    points, src_status, source, endpoint = [], "", None, None
    try:
        points, src_status = _fetch_usa_structures(bbox)
    except Exception as e:  # noqa: BLE001
        src_status = "usa_exception:%s" % e
        points = []
    if points:
        source, endpoint = "USA_Structures", USA_STRUCTURES_URL
    else:
        # primary empty or failed -> OSM fallback
        try:
            points, osm_status = _fetch_osm(bbox)
        except Exception as e:  # noqa: BLE001
            osm_status = "osm_exception:%s" % e
            points = []
        src_status = "%s | %s" % (src_status, osm_status)
        if points:
            source, endpoint = "OSM_Overpass", OVERPASS_URLS[0]

    if not points:
        return _none_result(search_miles, "no source returned points: %s" % src_status)

    # ---- 3. project to meters; measure parcel-edge -> each building --------
    fwd, inv, epsg = _make_transformers(cen.x, cen.y)
    if fwd is None:  # pyproj missing -> equirectangular metric fallback
        fwd, inv, epsg = _equirect_transformers(cen.x, cen.y)
        crs_note = "equirectangular (pyproj missing)"
    else:
        crs_note = "EPSG:%d (UTM, meters)" % epsg

    parcel_m = fwd(parcel_4326)
    # distance from a point INSIDE/ON the parcel uses the parcel exterior so a
    # building that happens to fall inside the parcel still gets a sane edge
    # distance; for the typical off-site dwelling this equals point->polygon dist.
    try:
        parcel_boundary_m = parcel_m.boundary
    except Exception:
        parcel_boundary_m = parcel_m

    radius_m_eff = radius_m
    best = None  # (dist_m, [lon,lat], building_point_m)
    count_within = 0
    for lonlat in points:
        bp_m = fwd(Point(lonlat[0], lonlat[1]))
        # distance parcel polygon -> building (0 if building inside parcel)
        d_poly = parcel_m.distance(bp_m)
        if d_poly > radius_m_eff:
            continue
        count_within += 1
        # the line should start at the nearest point ON the parcel edge
        d_edge = parcel_boundary_m.distance(bp_m)
        d_rank = d_poly if d_poly > 0 else d_edge
        if best is None or d_rank < best[0]:
            best = (d_rank, lonlat, bp_m)

    if best is None:
        return _none_result(
            search_miles,
            "%d residential found but none within %.2f mi" % (len(points), search_miles),
            count=0,
        )

    dist_m, dwelling_lonlat, bp_m = best

    # ---- 4. nearest point on the parcel edge (for the draw line) -----------
    try:
        from shapely.ops import nearest_points

        edge_pt_m, _bp = nearest_points(parcel_boundary_m, bp_m)
        edge_pt_4326 = inv(edge_pt_m)
        edge_lonlat = [edge_pt_4326.x, edge_pt_4326.y]
    except Exception:
        edge_lonlat = [cen.x, cen.y]  # safe fallback: parcel centroid

    # recompute distance edge->dwelling in meters for the reported value, so the
    # number matches the line that is actually drawn.
    line_len_m = fwd(Point(*edge_lonlat)).distance(bp_m)
    # use the polygon distance when the building is outside (more correct as a
    # setback figure); they coincide for an off-site dwelling.
    report_m = dist_m if dist_m > 0 else line_len_m
    nearest_ft = round(report_m * FT_PER_M, 1)
    nearest_m = round(report_m, 2)

    line_feature = {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [edge_lonlat, dwelling_lonlat],
        },
        "properties": {
            "kind": "nearest_dwelling",
            "distance_ft": nearest_ft,
            "distance_m": nearest_m,
            "source": source,
        },
    }

    return {
        "nearest_dwelling_ft": nearest_ft,
        "nearest_dwelling_m": nearest_m,
        "dwelling_point": [round(dwelling_lonlat[0], 7), round(dwelling_lonlat[1], 7)],
        "line": line_feature,
        "count_within_search": count_within,
        "status": "ok",
        "source": source,
        "endpoint": endpoint,
        "search_miles": float(search_miles),
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
    print("NEAREST DWELLING TEST")
    print("=" * 70)
    print("parcel source : %s" % src)
    print("search_miles  : 3")
    print()

    t = time.time()
    result = nearest_dwelling(parcel_in, search_miles=3)
    print("elapsed       : %.1f s" % (time.time() - t))
    print()
    print("RESULT DICT:")
    print(json.dumps(result, indent=2))
    print()
    if result["status"] == "ok":
        print(
            "OK -> nearest residential dwelling %s ft (%s m) away, "
            "%d within 3 mi, via %s"
            % (
                "{:,.1f}".format(result["nearest_dwelling_ft"]),
                "{:,.2f}".format(result["nearest_dwelling_m"]),
                result["count_within_search"],
                result["source"],
            )
        )
    else:
        print("NO DWELLING FOUND -> status=none (%s)" % result.get("note"))


if __name__ == "__main__":
    _test()
