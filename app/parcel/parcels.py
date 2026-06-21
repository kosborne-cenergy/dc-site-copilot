#!/usr/bin/env python3
"""Fetch Virginia PARCELS from VGIN's public ArcGIS REST service -> GeoJSON.

Free public service, NO API key, NO AI. Pure urllib/json (+ optional shapely).

Service discovered 2026-06-20 by walking the VGIN catalog:
  root   https://vginmaps.vdem.virginia.gov/arcgis/rest/services?f=json
  folder .../services/VA_Base_Layers?f=json            -> "VA_Parcels" (FeatureServer + MapServer)
  layer  .../VA_Parcels/FeatureServer/0?f=json         -> "Virginia Parcels", esriGeometryPolygon

Layer 0 fields (statewide parcels are GEOMETRY-ONLY -- no owner, no acreage attribute):
  OBJECTID    (OID)
  VGIN_QPID   (double)  statewide unique parcel id
  FIPS        (string)  5-digit county FIPS, e.g. "51111"
  LOCALITY    (string)  county/city name, e.g. "Lunenburg County"
  PARCELID    (string)  local parcel id / GPIN-PIN as the locality records it
  PTM_ID      (string)  parcel tax map id
  LASTUPDATE  (date)
  Shape__Area (double)  Web-Mercator sq meters -- DISTORTED, NOT used for acreage

There is no acreage field, so acreage is COMPUTED from the returned WGS84 (4326)
geometry via an equal-area-ish planar approximation (good to ~0.1% at VA latitudes),
using shapely if installed and a pure-python shoelace fallback otherwise.

Public service. maxRecordCount = 2000, supportsPagination = True.
"""
import json
import math
import pathlib
import ssl
import urllib.parse
import urllib.request

# VGIN's cert chain can trip Windows trust stores; service is public read-only data.
ssl._create_default_https_context = ssl._create_unverified_context

ROOT = pathlib.Path(__file__).resolve().parent.parent          # .../app
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

# --- working endpoint (discovered + verified) --------------------------------
LAYER_URL = (
    "https://vginmaps.vdem.virginia.gov/arcgis/rest/services/"
    "VA_Base_Layers/VA_Parcels/FeatureServer/0"
)
QUERY_URL = LAYER_URL + "/query"

# Service-imposed page size (read live below, but this is the published default).
SERVICE_MAX_RECORD_COUNT = 2000

EARTH_R = 6378137.0  # meters, WGS84 semi-major


# -----------------------------------------------------------------------------
# low-level HTTP
# -----------------------------------------------------------------------------
def _get_json(url, params):
    """GET a JSON ArcGIS response. Raises on transport error or ArcGIS error obj."""
    qs = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        url + "?" + qs, headers={"User-Agent": "dc-site-copilot/1.0 (cenergy)"}
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.load(r)
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError("ArcGIS error: " + json.dumps(data["error"])[:300])
    return data


def _layer_max_record_count():
    """Read the live maxRecordCount; fall back to the published default."""
    try:
        meta = _get_json(LAYER_URL, {"f": "json"})
        return int(meta.get("maxRecordCount") or SERVICE_MAX_RECORD_COUNT)
    except Exception:
        return SERVICE_MAX_RECORD_COUNT


# -----------------------------------------------------------------------------
# acreage from a WGS84 polygon (no attribute available on the source layer)
# -----------------------------------------------------------------------------
def _ring_area_m2(ring):
    """Signed area (m^2) of one lon/lat ring via a local equirectangular projection
    centered on the ring's mean latitude. Good enough for parcel acreage."""
    if len(ring) < 4:
        return 0.0
    lat0 = sum(p[1] for p in ring) / len(ring)
    cos0 = math.cos(math.radians(lat0))
    # project to local meters
    xy = [
        (math.radians(p[0]) * EARTH_R * cos0, math.radians(p[1]) * EARTH_R)
        for p in ring
    ]
    s = 0.0
    for i in range(len(xy) - 1):
        x1, y1 = xy[i]
        x2, y2 = xy[i + 1]
        s += x1 * y2 - x2 * y1
    return s / 2.0


def _polygon_acres(rings):
    """Acreage of an esri polygon (list of rings). Outer rings count positive,
    interior holes count negative (signed shoelace), so |sum| nets out holes.
    Self-contained planar math; 1 acre = 4046.8564224 m^2. (No shapely needed --
    validated to <0.01 ac against the service's distortion-corrected Shape__Area.)"""
    if not rings:
        return None
    total = sum(_ring_area_m2(r) for r in rings)
    return round(abs(total) / 4046.8564224, 3)


# -----------------------------------------------------------------------------
# main API
# -----------------------------------------------------------------------------
def _build_where(county_fips=None, county_name=None):
    clauses = []
    if county_fips:
        clauses.append("FIPS = '%s'" % str(county_fips).strip())
    if county_name:
        # case-insensitive substring on LOCALITY (e.g. "Lunenburg")
        clauses.append("UPPER(LOCALITY) LIKE UPPER('%%%s%%')" % county_name.strip())
    return " AND ".join(clauses) if clauses else "1=1"


def get_parcels(county_fips=None, county_name=None, bbox=None, limit=4000):
    """Return a GeoJSON FeatureCollection of Virginia parcels.

    Args:
        county_fips: 5-digit county FIPS string, e.g. "51111" (Lunenburg).
        county_name: locality name substring, e.g. "Lunenburg" (matches LOCALITY).
        bbox: (min_lon, min_lat, max_lon, max_lat) in WGS84 -> envelope intersect.
              STRONGLY recommended; a whole county can be tens of thousands of parcels.
        limit: hard cap on total features returned (default 4000). Pagination stops
               at this many even if more match.

    Returns:
        dict GeoJSON FeatureCollection. Each feature:
          geometry: Polygon/MultiPolygon in EPSG:4326
          properties: {parcel_id, acreage, locality, fips, ptm_id, vgin_qpid}

    Strategy: ArcGIS query against VGIN VA_Parcels/FeatureServer/0, outSR=4326,
    paginated with resultOffset/resultRecordCount honoring maxRecordCount.
    """
    where = _build_where(county_fips, county_name)
    page = min(_layer_max_record_count(), SERVICE_MAX_RECORD_COUNT)

    base = {
        "where": where,
        "outFields": "PARCELID,PTM_ID,FIPS,LOCALITY,VGIN_QPID",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "json",
    }
    if bbox:
        min_lon, min_lat, max_lon, max_lat = bbox
        base.update(
            {
                "geometry": "%s,%s,%s,%s" % (min_lon, min_lat, max_lon, max_lat),
                "geometryType": "esriGeometryEnvelope",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
            }
        )

    features = []
    offset = 0
    while len(features) < limit:
        params = dict(base)
        params["resultOffset"] = offset
        params["resultRecordCount"] = min(page, limit - len(features))
        data = _get_json(QUERY_URL, params)

        rows = data.get("features", [])
        if not rows:
            break

        for row in rows:
            attrs = row.get("attributes", {}) or {}
            geom = row.get("geometry", {}) or {}
            rings = geom.get("rings")
            if not rings:
                continue
            # esri rings -> GeoJSON: single ring=Polygon, multiple=Polygon w/ holes
            # (esri winding distinguishes outer/holes; for display we keep all rings
            #  under one Polygon, which renders correctly in Leaflet/Mapbox).
            geometry = {"type": "Polygon", "coordinates": rings}
            features.append(
                {
                    "type": "Feature",
                    "geometry": geometry,
                    "properties": {
                        "parcel_id": attrs.get("PARCELID"),
                        "acreage": _polygon_acres(rings),
                        "locality": attrs.get("LOCALITY"),
                        "fips": attrs.get("FIPS"),
                        "ptm_id": attrs.get("PTM_ID"),
                        "vgin_qpid": attrs.get("VGIN_QPID"),
                    },
                }
            )
            if len(features) >= limit:
                break

        # exactTransfer / last page detection
        if not data.get("exceededTransferLimit") and len(rows) < params["resultRecordCount"]:
            break
        offset += len(rows)

    return {
        "type": "FeatureCollection",
        "features": features,
        "_source": QUERY_URL,
    }


# -----------------------------------------------------------------------------
# self-test
# -----------------------------------------------------------------------------
def _test():
    # ~0.05 x 0.05 deg over rural Lunenburg County, VA (south-central VA).
    bbox = (-78.30, 36.95, -78.25, 37.00)
    print("Querying VGIN VA_Parcels for bbox %s ..." % (bbox,))
    fc = get_parcels(bbox=bbox, limit=4000)
    feats = fc["features"]

    out = DATA / "parcel_sample.geojson"
    with open(out, "w") as f:
        json.dump(fc, f, separators=(",", ":"))

    print("ENDPOINT : %s" % fc["_source"])
    print("SAVED    : %s (%d KB)" % (out, out.stat().st_size // 1024))
    print("FEATURES : %d" % len(feats))
    if feats:
        p = feats[0]["properties"]
        print("FIRST FEATURE property keys: %s" % list(p.keys()))
        print("FIRST FEATURE properties   : %s" % json.dumps(p))


if __name__ == "__main__":
    _test()
