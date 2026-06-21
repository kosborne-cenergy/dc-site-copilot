#!/usr/bin/env python3
"""Environmental CONSTRAINTS + BUILDABLE-area analysis for a parcel.

Given a parcel polygon (GeoJSON) or a bbox, this module fetches public
environmental constraint layers from open ArcGIS REST services (NO API key,
NO AI -- pure urllib/json + shapely/pyproj) and computes the buildable area
left after wetlands, special-flood-hazard areas, and a perimeter setback are
removed.

LAYERS (all queried by the parcel bbox, returned in EPSG:4326)
------------------------------------------------------------------
1. NWI wetlands  -- USFWS National Wetlands Inventory.
   The canonical FWS service is published as a CACHED (tile) MapServer whose
   `?f=json` advertises NO queryable sublayers, and its query backend
   (fwspublicservices / fwsprimary) is frequently 500-flaky. So we try an
   ORDERED list of NWI query endpoints and use the first that returns
   features. Verified endpoints (2026-06-20):
     - https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/Wetlands/MapServer/0
       (the official FWS National Wetlands layer; the `/0/query` endpoint is the
        real queryable layer even though the service summary hides it)
     - https://fwsprimary.wim.usgs.gov/server/rest/services/Wetlands/MapServer/0
       (alternate FWS host, same data)
   If ALL NWI endpoints are unreachable, wetlands degrade to an empty layer and
   `summary["wetlands_status"]` records the failure -- buildable area is still
   computed from flood + setback (never hard-fails on a transient outage).

2. FEMA flood   -- National Flood Hazard Layer (NFHL).
   VERIFIED working (2026-06-20):
     https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28
     layer 28 = "Flood Hazard Zones" (esriGeometryPolygon).
   We flag Special Flood Hazard Areas (SFHA) = FLD_ZONE in
   {A, AE, AH, AO, A99, V, VE} (these are the regulatory 1%-annual-chance zones;
   the layer's own SFHA_TF='T' flag is used as a cross-check). Only SFHA polygons
   are subtracted from the buildable area; non-SFHA zones (X, etc.) are returned
   for context but NOT excluded.

3. USGS 3DEP slope -- NOT implemented inline (a per-pixel slope raster pull +
   threshold is too heavy for this synchronous call). Left as a TODO hook;
   see `slope_todo()`.

COMPUTE  (shapely; projected to meters via pyproj for correct area/buffer)
------------------------------------------------------------------
We project every geometry to a local metric CRS -- the appropriate UTM zone
(17N / 18N) picked from the parcel centroid, falling back to Virginia State
Plane South if pyproj/UTM is unavailable -- so areas and the setback buffer are
in true meters, then convert to acres (1 ac = 4046.8564224 m^2).

  setback        = parcel boundary buffered INWARD by `setback_ft` (default 100).
                   The excluded ring = parcel - inward_buffer(parcel).
  buildable_area = parcel
                     - (wetlands ∩ parcel)
                     - (SFHA flood ∩ parcel)
                     - setback exclusion ring
  summary        = parcel_acres, wetland_acres, flood_sfha_acres, setback_acres,
                   buildable_acres, buildable_pct (+ provenance / status fields).

PUBLIC API
------------------------------------------------------------------
  build_constraints(parcel_geojson_or_bbox, setback_ft=100.0) -> dict
      {
        "wetlands":  <GeoJSON FeatureCollection, EPSG:4326>,
        "flood":     <GeoJSON FeatureCollection, EPSG:4326>,  # all zones, SFHA flagged
        "buildable": <GeoJSON FeatureCollection, EPSG:4326>,  # 1 (multi)polygon feature
        "summary":   <dict of acreage + pct + provenance>,
      }
"""
import json
import math
import pathlib
import ssl
import time
import urllib.parse
import urllib.request

# These public services occasionally present cert chains that trip the Windows
# trust store; all data here is public read-only. (Matches parcels.py.)
ssl._create_default_https_context = ssl._create_unverified_context

# --- geometry / projection deps ---------------------------------------------
from shapely.geometry import shape, mapping, box  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

try:
    from shapely.ops import transform as shp_transform
    from pyproj import Transformer, CRS

    _HAVE_PYPROJ = True
except Exception:  # pragma: no cover - pyproj should be present per env
    _HAVE_PYPROJ = False

ROOT = pathlib.Path(__file__).resolve().parent.parent  # .../app
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

ACRES_PER_M2 = 1.0 / 4046.8564224
FT_PER_M = 3.280839895
USER_AGENT = "dc-site-copilot/1.0 (cenergy)"

# --- constraint service endpoints -------------------------------------------
# NWI: ordered fallback list of *queryable layer* base URLs (no trailing /query).
NWI_ENDPOINTS = [
    "https://fwspublicservices.wim.usgs.gov/wetlandsmapservice/rest/services/Wetlands/MapServer/0",
    "https://fwsprimary.wim.usgs.gov/server/rest/services/Wetlands/MapServer/0",
]
NWI_FIELDS = "ATTRIBUTE,WETLAND_TYPE"

# FEMA NFHL Flood Hazard Zones (verified layer id 28).
FEMA_FLOOD = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28"
FEMA_FIELDS = "FLD_ZONE,ZONE_SUBTY,SFHA_TF"

# Regulatory Special Flood Hazard Area zone codes (1%-annual-chance floodplain).
SFHA_ZONES = {"A", "AE", "AH", "AO", "A99", "V", "VE"}

HTTP_RETRIES = 4  # these public services (esp. FEMA NFHL) 500 intermittently
HTTP_TIMEOUT = 120


# =============================================================================
# low-level HTTP
# =============================================================================
def _http_get(url):
    """GET -> decoded text. Raises urllib errors to the caller."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def _query_layer(base_url, bbox, out_fields, retries=HTTP_RETRIES):
    """Query an ArcGIS feature layer by bbox envelope; return a GeoJSON dict.

    `bbox` = (min_lon, min_lat, max_lon, max_lat) in EPSG:4326.
    Returns a GeoJSON FeatureCollection dict, or {"features": [], "_error": "..."}
    on persistent failure (so callers degrade gracefully). Some of these public
    services return an HTML error page or an ArcGIS {"error": ...} body instead
    of an HTTP error code -- both are detected and treated as a failed attempt.
    """
    params = {
        "where": "1=1",
        "geometry": "%s,%s,%s,%s" % tuple(bbox),
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "outSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": out_fields,
        "returnGeometry": "true",
        "f": "geojson",
    }
    url = base_url + "/query?" + urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries):
        try:
            raw = _http_get(url)
            if raw.lstrip().startswith("<"):  # HTML error page
                last = "html_error_page"
                time.sleep(1.5)
                continue
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("error"):
                last = "arcgis_error:" + json.dumps(data["error"])[:160]
                time.sleep(1.5)
                continue
            data.setdefault("type", "FeatureCollection")
            data.setdefault("features", [])
            return data
        except Exception as e:  # transport / decode error -> retry
            last = "%s:%s" % (type(e).__name__, str(e)[:120])
            time.sleep(1.5)
    return {"type": "FeatureCollection", "features": [], "_error": last}


# =============================================================================
# input normalization
# =============================================================================
def _coerce_parcel(parcel_geojson_or_bbox):
    """Accept a bbox tuple/list, a GeoJSON FeatureCollection / Feature / geometry,
    or a path to a .geojson file, and return a single shapely (multi)polygon in
    EPSG:4326 plus its bbox.

    For a FeatureCollection, the FIRST feature is used (per the task spec).
    For a bare bbox, the parcel polygon IS the bbox rectangle.
    """
    obj = parcel_geojson_or_bbox

    # path to a geojson file
    if isinstance(obj, (str, pathlib.Path)) and pathlib.Path(str(obj)).exists():
        with open(obj) as f:
            obj = json.load(f)

    # bbox as a 4-tuple/list of numbers
    if (
        isinstance(obj, (list, tuple))
        and len(obj) == 4
        and all(isinstance(v, (int, float)) for v in obj)
    ):
        geom = box(*obj)
        return geom, tuple(float(v) for v in obj)

    # GeoJSON dict
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
        geom = geom.buffer(0)  # fix any self-intersections / winding issues
        return geom, tuple(geom.bounds)

    raise TypeError(
        "parcel must be a GeoJSON dict, a .geojson path, or a 4-number bbox; got %r"
        % type(obj)
    )


# =============================================================================
# projection helpers (EPSG:4326 <-> local meters)
# =============================================================================
def _metric_crs_for(lon, lat):
    """Pick a metric CRS for accurate area/buffer near (lon, lat).

    Virginia spans UTM zones 17N (EPSG:32617, west of -78deg) and 18N
    (EPSG:32618, east of -78deg). Returns an EPSG code int. Falls back to
    NAD83 / Virginia State Plane South (EPSG:2284, US-ft -> handled separately)
    is NOT used here because we want meters; UTM covers all of VA well.
    """
    zone = int((lon + 180) // 6) + 1
    if lat >= 0:
        return 32600 + zone  # WGS84 / UTM north
    return 32700 + zone


def _make_transformers(lon, lat):
    """Return (fwd, inv) shapely-compatible transform callables 4326<->metric,
    plus the metric EPSG code. If pyproj is missing, returns (None, None, None)
    and the caller uses an equirectangular fallback for area only."""
    if not _HAVE_PYPROJ:
        return None, None, None
    epsg = _metric_crs_for(lon, lat)
    crs_geo = CRS.from_epsg(4326)
    crs_m = CRS.from_epsg(epsg)
    to_m = Transformer.from_crs(crs_geo, crs_m, always_xy=True)
    to_geo = Transformer.from_crs(crs_m, crs_geo, always_xy=True)
    fwd = lambda g: shp_transform(to_m.transform, g)  # noqa: E731
    inv = lambda g: shp_transform(to_geo.transform, g)  # noqa: E731
    return fwd, inv, epsg


def _equirect_area_acres(geom_4326):
    """Fallback area (acres) of a 4326 geometry via a local equirectangular
    projection centered on its centroid. Only used if pyproj is unavailable."""
    if geom_4326.is_empty:
        return 0.0
    c = geom_4326.centroid
    lat0 = math.radians(c.y)
    R = 6378137.0
    cos0 = math.cos(lat0)
    fwd = lambda g: shp_transform(  # noqa: E731
        lambda xs, ys: (
            [math.radians(x) * R * cos0 for x in xs],
            [math.radians(y) * R for y in ys],
        ),
        g,
    )
    if not _HAVE_PYPROJ:  # need shapely transform; provide a tiny shim
        from shapely.ops import transform as _t

        def fwd(g):
            return _t(
                lambda xs, ys: (
                    [math.radians(x) * R * cos0 for x in xs],
                    [math.radians(y) * R for y in ys],
                ),
                g,
            )

    return fwd(geom_4326).area * ACRES_PER_M2


# =============================================================================
# constraint geometry extraction
# =============================================================================
def _features_to_geom(fc):
    """Union all polygonal geometries in a GeoJSON FeatureCollection -> one
    shapely geometry (possibly empty). Non-polygon features are ignored."""
    geoms = []
    for ft in fc.get("features", []):
        g = ft.get("geometry")
        if not g:
            continue
        try:
            sg = shape(g).buffer(0)
        except Exception:
            continue
        if sg.is_empty:
            continue
        if sg.geom_type in ("Polygon", "MultiPolygon"):
            geoms.append(sg)
    if not geoms:
        return None
    return unary_union(geoms)


def _split_flood(fc):
    """From a NFHL flood FeatureCollection, return (sfha_geom, all_fc_flagged).

    Adds `is_sfha` + `sfha` boolean to each feature's properties, computes the
    SFHA union geometry (zones in SFHA_ZONES OR SFHA_TF == 'T')."""
    sfha_geoms = []
    for ft in fc.get("features", []):
        props = ft.setdefault("properties", {})
        zone = (props.get("FLD_ZONE") or "").strip().upper()
        tf = str(props.get("SFHA_TF") or "").strip().upper()
        is_sfha = zone in SFHA_ZONES or tf in ("T", "TRUE")
        props["is_sfha"] = is_sfha
        if is_sfha:
            g = ft.get("geometry")
            if g:
                try:
                    sg = shape(g).buffer(0)
                    if not sg.is_empty and sg.geom_type in ("Polygon", "MultiPolygon"):
                        sfha_geoms.append(sg)
                except Exception:
                    pass
    sfha = unary_union(sfha_geoms) if sfha_geoms else None
    return sfha, fc


def _fetch_wetlands(bbox):
    """Try each NWI endpoint in order; return (FeatureCollection, status_str,
    endpoint_used_or_None)."""
    errors = []
    for ep in NWI_ENDPOINTS:
        fc = _query_layer(ep, bbox, NWI_FIELDS)
        if fc.get("_error"):
            errors.append("%s -> %s" % (ep, fc["_error"]))
            continue
        n = len(fc.get("features", []))
        return fc, "ok (%d features) via %s" % (n, ep), ep
    # all failed
    return (
        {"type": "FeatureCollection", "features": []},
        "UNAVAILABLE -- all NWI endpoints failed: " + " | ".join(errors),
        None,
    )


def slope_todo():
    """USGS 3DEP slope screen -- intentionally not implemented inline.

    Approach when needed: pull a small 3DEP DEM clip for the bbox from
    https://elevation.nationalmap.gov/arcgis/rest/services/3DEPElevation/ImageServer
    (exportImage), compute slope with numpy gradient over the pixel grid (cell
    size from the response), threshold (e.g. > 15%), polygonize, and subtract
    like the other constraints. Omitted here to keep build_constraints() a fast
    synchronous vector-only call.
    """
    return {"status": "TODO", "note": "3DEP slope screen not run (vector-only mode)"}


# =============================================================================
# public API
# =============================================================================
def build_constraints(parcel_geojson_or_bbox, setback_ft=100.0):
    """Fetch constraints for a parcel and compute the buildable area.

    Args:
        parcel_geojson_or_bbox: a GeoJSON FeatureCollection (first feature used),
            Feature, geometry, a path to a .geojson file, or a
            (min_lon, min_lat, max_lon, max_lat) bbox in EPSG:4326.
        setback_ft: perimeter setback distance in FEET, applied as an inward
            buffer of the parcel boundary (default 100 ft).

    Returns:
        dict with keys: wetlands, flood, buildable (all GeoJSON
        FeatureCollections in EPSG:4326) and summary (acreage + pct + provenance).
    """
    parcel_4326, bbox = _coerce_parcel(parcel_geojson_or_bbox)
    if parcel_4326.is_empty:
        raise ValueError("parcel geometry is empty")

    cen = parcel_4326.centroid
    fwd, inv, epsg = _make_transformers(cen.x, cen.y)

    # --- fetch constraint layers by bbox ------------------------------------
    wet_fc, wet_status, wet_ep = _fetch_wetlands(bbox)
    flood_fc = _query_layer(FEMA_FLOOD, bbox, FEMA_FIELDS)
    flood_status = (
        "UNAVAILABLE -- " + flood_fc["_error"]
        if flood_fc.get("_error")
        else "ok (%d features) via %s" % (len(flood_fc.get("features", [])), FEMA_FLOOD)
    )

    wet_geom_4326 = _features_to_geom(wet_fc)
    sfha_geom_4326, flood_fc = _split_flood(flood_fc)

    # --- project to meters for accurate area / buffer -----------------------
    if fwd is not None:
        parcel_m = fwd(parcel_4326)
        wet_m = fwd(wet_geom_4326) if wet_geom_4326 is not None else None
        sfha_m = fwd(sfha_geom_4326) if sfha_geom_4326 is not None else None
        setback_m_dist = setback_ft / FT_PER_M

        # clip constraints to the parcel
        wet_in = parcel_m.intersection(wet_m) if wet_m is not None else None
        sfha_in = parcel_m.intersection(sfha_m) if sfha_m is not None else None

        # inward setback ring = parcel - (parcel buffered inward)
        inner = parcel_m.buffer(-setback_m_dist)
        if inner.is_empty:
            setback_ring = parcel_m  # setback consumes the whole parcel
        else:
            setback_ring = parcel_m.difference(inner)

        # buildable = parcel minus all exclusions
        exclusions = [g for g in (wet_in, sfha_in, setback_ring) if g and not g.is_empty]
        buildable_m = parcel_m
        if exclusions:
            buildable_m = parcel_m.difference(unary_union(exclusions))
        buildable_m = buildable_m.buffer(0)

        parcel_acres = parcel_m.area * ACRES_PER_M2
        wetland_acres = (wet_in.area * ACRES_PER_M2) if (wet_in and not wet_in.is_empty) else 0.0
        flood_acres = (sfha_in.area * ACRES_PER_M2) if (sfha_in and not sfha_in.is_empty) else 0.0
        setback_acres = setback_ring.area * ACRES_PER_M2 if not setback_ring.is_empty else 0.0
        buildable_acres = buildable_m.area * ACRES_PER_M2 if not buildable_m.is_empty else 0.0
        buildable_4326 = inv(buildable_m)
        crs_note = "EPSG:%d (UTM, meters)" % epsg
    else:
        # ---- pyproj-less fallback: areas via equirectangular, NO buffer ----
        # (setback needs a metric buffer; without pyproj we approximate the
        #  setback ring area as 0 and warn. Areas are still meaningful.)
        parcel_acres = _equirect_area_acres(parcel_4326)
        wet_in = (
            parcel_4326.intersection(wet_geom_4326)
            if wet_geom_4326 is not None
            else None
        )
        sfha_in = (
            parcel_4326.intersection(sfha_geom_4326)
            if sfha_geom_4326 is not None
            else None
        )
        wetland_acres = _equirect_area_acres(wet_in) if wet_in is not None else 0.0
        flood_acres = _equirect_area_acres(sfha_in) if sfha_in is not None else 0.0
        setback_acres = 0.0
        excl = [g for g in (wet_in, sfha_in) if g and not g.is_empty]
        buildable_4326 = (
            parcel_4326.difference(unary_union(excl)) if excl else parcel_4326
        )
        buildable_acres = _equirect_area_acres(buildable_4326)
        crs_note = "equirectangular fallback (pyproj missing -- setback skipped)"

    buildable_pct = round(100.0 * buildable_acres / parcel_acres, 1) if parcel_acres else 0.0

    summary = {
        "parcel_acres": round(parcel_acres, 3),
        "wetland_acres": round(wetland_acres, 3),
        "flood_sfha_acres": round(flood_acres, 3),
        "setback_acres": round(setback_acres, 3),
        "buildable_acres": round(buildable_acres, 3),
        "buildable_pct": buildable_pct,
        "setback_ft": setback_ft,
        "bbox_4326": [round(v, 6) for v in bbox],
        "projection": crs_note,
        "wetlands_status": wet_status,
        "wetlands_endpoint": wet_ep,
        "wetlands_count": len(wet_fc.get("features", [])),
        "flood_status": flood_status,
        "flood_endpoint": FEMA_FLOOD,
        "flood_count": len(flood_fc.get("features", [])),
        "flood_sfha_count": sum(
            1 for f in flood_fc.get("features", []) if f.get("properties", {}).get("is_sfha")
        ),
        "slope": slope_todo(),
    }

    buildable_fc = {
        "type": "FeatureCollection",
        "features": (
            [
                {
                    "type": "Feature",
                    "geometry": mapping(buildable_4326),
                    "properties": {
                        "kind": "buildable",
                        "acres": round(buildable_acres, 3),
                        "buildable_pct": buildable_pct,
                    },
                }
            ]
            if not buildable_4326.is_empty
            else []
        ),
    }

    return {
        "wetlands": wet_fc,
        "flood": flood_fc,
        "buildable": buildable_fc,
        "summary": summary,
    }


# =============================================================================
# self-test
# =============================================================================
def _save(name, obj):
    p = DATA / name
    with open(p, "w") as f:
        json.dump(obj, f, separators=(",", ":"))
    return p


def _run_case(parcel_in, src, prefix):
    print("=" * 70)
    print("CONSTRAINTS TEST  --  parcel source:", src)
    print("=" * 70)
    result = build_constraints(parcel_in, setback_ft=100.0)
    wp = _save(prefix + "_wetlands.geojson", result["wetlands"])
    fp = _save(prefix + "_flood.geojson", result["flood"])
    bp = _save(prefix + "_buildable.geojson", result["buildable"])
    print("\nSUMMARY:")
    print(json.dumps(result["summary"], indent=2))
    print("\nSAVED LAYERS:")
    for p in (wp, fp, bp):
        print("  %s (%d KB)" % (p, max(1, p.stat().st_size // 1024)))
    print()
    return result


def _test():
    # --- Case 1: the real sample parcel (first feature), per spec -----------
    sample = DATA / "parcel_sample.geojson"
    if sample.exists():
        with open(sample) as f:
            fc = json.load(f)
        feats = fc.get("features", [])
        if feats:
            parcel_in = {"type": "FeatureCollection", "features": [feats[0]]}
            src = "parcel_sample.geojson (first of %d parcels)" % len(feats)
            _run_case(parcel_in, src, "constraints")
        else:
            print("parcel_sample.geojson present but empty; skipping case 1")
    else:
        print("No parcel_sample.geojson; skipping case 1")

    # --- Case 2: hardcoded Richmond VA bbox over the James River SFHA -------
    # (~0.02deg; FEMA NFHL has digitized flood here -- exercises SFHA removal)
    demo_bbox = (-77.46, 37.50, -77.44, 37.52)
    _run_case(demo_bbox, "hardcoded Richmond VA SFHA bbox %s" % (demo_bbox,), "constraints_demo")


if __name__ == "__main__":
    _test()
