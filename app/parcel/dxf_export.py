#!/usr/bin/env python3
"""Generate an AutoCAD DXF site exhibit for a parcel -- NO AI, pure CAD math.

Takes the GeoJSON outputs of the sibling modules (``parcels.py`` -> the parcel,
``constraints.py`` -> buildable / setback / wetlands / flood, and a future
``dwellings.py`` -> a nearest-dwelling line) and renders a layered, real-survey-
feet DXF that opens cleanly in AutoCAD / Civil 3D / any DXF viewer.

PIPELINE (ezdxf + shapely + pyproj -- no network, no AI)
--------------------------------------------------------
1. Coerce every input (Feature / FeatureCollection / bare geometry / .geojson
   path) to a shapely geometry in EPSG:4326. (Mirrors ``constraints._coerce_*``.)
2. Reproject WGS84 -> Virginia State Plane, US **survey feet**, so the drawing is
   in true ground feet. The zone is chosen from the parcel centroid LATITUDE
   (the correct discriminator for a North/South State-Plane split, boundary
   ~37.83 deg N):
       lat >= 37.83  -> EPSG:2283  NAD83 / Virginia North (ftUS)
       lat <  37.83  -> EPSG:2284  NAD83 / Virginia South (ftUS)
   (NOTE: pyproj names 2283=North, 2284=South -- the inverse of the loose
   "2283=South" wording in the build request; the codes above are the verified
   EPSG identities and are mapped by true zone, not by that wording. A small
   longitude check is applied only to disambiguate parcels straddling the
   boundary latitude.)
3. Build an ezdxf R2018 document with one LAYER per theme, each its own ACI
   color (and a dashed linetype on SETBACK):
       PARCEL          white   (boundary)
       SETBACK         yellow  (dashed)
       BUILDABLE       green   (outline + SOLID hatch)
       WETLANDS        cyan
       FLOOD           blue
       DWELLING-OFFSET red     (line + distance text)
       ANNOTATION      white   (title / acreage / labels)
       TITLEBLOCK      white   (corner MTEXT block)
4. Draw each geometry as LWPOLYLINE(s) -- Polygon (incl. holes), MultiPolygon,
   and LineString are all handled. Closed rings get ``close=True``.
5. Add TEXT annotations (title, parcel acreage, buildable acreage, nearest-
   dwelling distance) and a corner MTEXT title block.
6. Save to ``out_path`` and return ``(out_path, summary)`` where ``summary`` is
   a dict (layer count, entity count, EPSG, bbox, acreages, ...).

PUBLIC API
----------
    build_dxf(parcel_geojson,
              buildable_geojson=None, setback_geojson=None,
              wetlands_geojson=None, flood_geojson=None,
              dwelling_line_geojson=None,
              out_path="data/parcel_exhibit.dxf",
              label="Proposed Data Center Site") -> (out_path, summary_dict)
"""
import json
import math
import pathlib

import ezdxf
from ezdxf.enums import TextEntityAlignment

from shapely.geometry import shape, mapping  # noqa: F401  (mapping handy for debug)
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shp_transform, unary_union
from pyproj import Transformer, CRS

# --- paths (match parcels.py / constraints.py) -------------------------------
ROOT = pathlib.Path(__file__).resolve().parent.parent  # .../app
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)

# --- units / constants -------------------------------------------------------
ACRES_PER_FT2 = 1.0 / 43560.0            # 1 acre = 43,560 ft^2
FT_PER_M = 3.280839895
SETBACK_FT_DEFAULT = 100.0               # inward setback if none supplied

# Virginia State Plane, NAD83, US survey FEET. (pyproj: 2283=North, 2284=South.)
EPSG_VA_NORTH_FT = 2283
EPSG_VA_SOUTH_FT = 2284
# Practical NAD83 VA North/South zone boundary latitude (deg N). The two zones'
# areas of use overlap ~37.77-38.28; ~37.83 is the conventional split.
VA_ZONE_SPLIT_LAT = 37.83
# If a parcel sits within this band of the split latitude, fall back to a
# longitude nudge (eastern Tidewater tends to stay South a touch higher).
VA_ZONE_AMBIG_BAND = 0.10

# --- ACI colors (AutoCAD Color Index) ----------------------------------------
ACI_RED = 1
ACI_YELLOW = 2
ACI_GREEN = 3
ACI_CYAN = 4
ACI_BLUE = 5
ACI_WHITE = 7

# --- layer definitions: name -> (color, linetype) ----------------------------
LAYERS = {
    "PARCEL":          (ACI_WHITE,  "CONTINUOUS"),
    "SETBACK":         (ACI_YELLOW, "DASHED"),
    "BUILDABLE":       (ACI_GREEN,  "CONTINUOUS"),
    "WETLANDS":        (ACI_CYAN,   "CONTINUOUS"),
    "FLOOD":           (ACI_BLUE,   "CONTINUOUS"),
    "DWELLING-OFFSET": (ACI_RED,    "CONTINUOUS"),
    "ANNOTATION":      (ACI_WHITE,  "CONTINUOUS"),
    "TITLEBLOCK":      (ACI_WHITE,  "CONTINUOUS"),
}


# =============================================================================
# input normalization  (accept Feature / FeatureCollection / geometry / path)
# =============================================================================
def _coerce_geom(obj, all_features=True):
    """Coerce a GeoJSON input to a single shapely geometry in EPSG:4326.

    Accepts: a shapely geometry (passed through), a GeoJSON dict
    (FeatureCollection / Feature / bare geometry), or a path to a .geojson file.
    Returns ``None`` for a null / empty input so optional layers can be skipped.

    For a FeatureCollection with ``all_features=True`` (the default) every
    feature's geometry is unioned -- right for multi-feature constraint layers
    (e.g. FEMA flood returns many polygons). With ``all_features=False`` only the
    FIRST feature is taken (used for the parcel, per the sample-data convention
    that the first feature is THE parcel).
    """
    if obj is None:
        return None

    # already a shapely geometry
    if isinstance(obj, BaseGeometry):
        return obj if not obj.is_empty else None

    # path to a .geojson file
    if isinstance(obj, (str, pathlib.Path)):
        p = pathlib.Path(str(obj))
        if not p.exists():
            raise FileNotFoundError("GeoJSON path not found: %s" % p)
        with open(p) as f:
            obj = json.load(f)

    if not isinstance(obj, dict):
        raise TypeError("Unsupported GeoJSON input type: %r" % type(obj))

    t = obj.get("type")
    if t == "FeatureCollection":
        feats = [ft for ft in (obj.get("features") or []) if ft and ft.get("geometry")]
        if not feats:
            return None
        if not all_features:
            return _safe_shape(feats[0]["geometry"])
        geoms = [g for g in (_safe_shape(ft["geometry"]) for ft in feats) if g is not None]
        if not geoms:
            return None
        return unary_union(geoms) if len(geoms) > 1 else geoms[0]

    if t == "Feature":
        return _safe_shape(obj.get("geometry"))

    if t in (
        "Polygon", "MultiPolygon", "LineString", "MultiLineString",
        "Point", "MultiPoint", "GeometryCollection",
    ):
        return _safe_shape(obj)

    raise ValueError("Unsupported GeoJSON type: %r" % t)


def _safe_shape(geojson_geom):
    """shape() a GeoJSON geometry, repair polygons with buffer(0); None on empty."""
    if not geojson_geom:
        return None
    try:
        g = shape(geojson_geom)
    except Exception:
        return None
    if g.is_empty:
        return None
    # buffer(0) cleans self-intersections/winding on (multi)polygons; leave
    # lines & points untouched (buffer(0) would erase them).
    if g.geom_type in ("Polygon", "MultiPolygon"):
        g = g.buffer(0)
        if g.is_empty:
            return None
    return g


# =============================================================================
# projection: WGS84 -> Virginia State Plane US survey feet
# =============================================================================
def _pick_va_stateplane_ft(lon, lat):
    """Return the EPSG code for the VA State Plane (US-ft) zone covering (lon,lat).

    Primary discriminator is LATITUDE vs the ~37.83 deg N North/South split. For
    a parcel within ~0.1 deg of that boundary the choice is ambiguous, so we add
    a small longitude tie-break (far-east Tidewater leans South a touch higher).
    """
    if lat >= VA_ZONE_SPLIT_LAT + VA_ZONE_AMBIG_BAND:
        return EPSG_VA_NORTH_FT
    if lat <= VA_ZONE_SPLIT_LAT - VA_ZONE_AMBIG_BAND:
        return EPSG_VA_SOUTH_FT
    # ambiguous band: eastern (lon > -77.3) stays South a bit higher, else split.
    if lon > -77.3:
        return EPSG_VA_SOUTH_FT
    return EPSG_VA_NORTH_FT if lat >= VA_ZONE_SPLIT_LAT else EPSG_VA_SOUTH_FT


def _make_to_ft(lon, lat):
    """Build a shapely-compatible 4326 -> VA-State-Plane-ft transform.

    Returns (transform_callable, epsg_int, crs_name).
    """
    epsg = _pick_va_stateplane_ft(lon, lat)
    to_ft = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_epsg(epsg), always_xy=True)
    fwd = lambda g: shp_transform(to_ft.transform, g)  # noqa: E731
    return fwd, epsg, CRS.from_epsg(epsg).name


# =============================================================================
# geometry -> DXF entities
# =============================================================================
def _ring_points(ring_coords):
    """GeoJSON/shapely ring coords -> list[(x, y)] (drop any z)."""
    return [(float(c[0]), float(c[1])) for c in ring_coords]


def _add_polygon(msp, poly, layer):
    """Add a shapely Polygon (exterior + holes) as closed LWPOLYLINE(s).

    Returns the number of polyline entities added (1 exterior + one per hole).
    """
    n = 0
    ext = list(poly.exterior.coords)
    if len(ext) >= 2:
        msp.add_lwpolyline(_ring_points(ext), close=True, dxfattribs={"layer": layer})
        n += 1
    for ring in poly.interiors:
        pts = list(ring.coords)
        if len(pts) >= 2:
            msp.add_lwpolyline(_ring_points(pts), close=True, dxfattribs={"layer": layer})
            n += 1
    return n


def _add_geometry(msp, geom, layer):
    """Add any shapely geometry to a layer as LWPOLYLINE(s).

    Handles Polygon, MultiPolygon, LineString, MultiLineString, and
    GeometryCollection (recursively). Points are skipped (nothing to draw as a
    polyline). Returns the entity count added.
    """
    if geom is None or geom.is_empty:
        return 0
    gt = geom.geom_type
    if gt == "Polygon":
        return _add_polygon(msp, geom, layer)
    if gt == "MultiPolygon":
        return sum(_add_polygon(msp, p, layer) for p in geom.geoms)
    if gt == "LineString":
        pts = _ring_points(list(geom.coords))
        if len(pts) >= 2:
            msp.add_lwpolyline(pts, close=False, dxfattribs={"layer": layer})
            return 1
        return 0
    if gt == "MultiLineString":
        return sum(_add_geometry(msp, ls, layer) for ls in geom.geoms)
    if gt == "GeometryCollection":
        return sum(_add_geometry(msp, g, layer) for g in geom.geoms)
    return 0  # Point / MultiPoint: nothing to render as a polyline


def _add_solid_hatch(msp, geom, layer, color, transparency=0.80):
    """Add a translucent SOLID hatch for a (Multi)Polygon (buildable fill).

    Holes are honored via per-loop flags. Returns the hatch entity count (1 if
    added, else 0). A hatch failure must never abort the drawing.
    """
    polys = []
    if geom.geom_type == "Polygon":
        polys = [geom]
    elif geom.geom_type == "MultiPolygon":
        polys = list(geom.geoms)
    if not polys:
        return 0
    try:
        hatch = msp.add_hatch(color=color, dxfattribs={"layer": layer})
        hatch.set_solid_fill()
        for poly in polys:
            ext = _ring_points(list(poly.exterior.coords))
            if len(ext) >= 3:
                hatch.paths.add_polyline_path(ext, is_closed=True, flags=1)  # external
            for ring in poly.interiors:
                hp = _ring_points(list(ring.coords))
                if len(hp) >= 3:
                    hatch.paths.add_polyline_path(hp, is_closed=True, flags=0)  # hole
        try:
            hatch.set_transparency(transparency)  # 0=opaque .. 1=fully transparent
        except Exception:
            pass
        return 1
    except Exception:
        return 0


# =============================================================================
# acreage (geometry is already in FEET here)
# =============================================================================
def _acres_ft(geom):
    """Polygonal area in acres for a geometry already in survey feet."""
    if geom is None or geom.is_empty or geom.geom_type not in ("Polygon", "MultiPolygon"):
        return 0.0
    return geom.area * ACRES_PER_FT2


def _line_len_ft(geom):
    """Length in feet of a (Multi)LineString already in survey feet (else 0)."""
    if geom is None or geom.is_empty:
        return 0.0
    if geom.geom_type in ("LineString", "MultiLineString"):
        return float(geom.length)
    return 0.0


# =============================================================================
# public API
# =============================================================================
def build_dxf(
    parcel_geojson,
    buildable_geojson=None,
    setback_geojson=None,
    wetlands_geojson=None,
    flood_geojson=None,
    dwelling_line_geojson=None,
    out_path="data/parcel_exhibit.dxf",
    label="Proposed Data Center Site",
):
    """Build a layered, survey-feet DXF site exhibit for a parcel.

    Args:
        parcel_geojson: the parcel -- GeoJSON Feature / FeatureCollection (FIRST
            feature is taken) / bare geometry / path to a .geojson. REQUIRED.
        buildable_geojson: buildable area (from constraints.py). Drawn green w/
            a translucent solid hatch. Optional.
        setback_geojson: setback ring / line (from constraints.py). Drawn yellow
            dashed. If omitted, a 100 ft inward setback ring is computed from the
            parcel so the exhibit always shows a setback. Optional.
        wetlands_geojson: wetlands polygons (constraints.py). Cyan. Optional.
        flood_geojson: FEMA flood polygons (constraints.py). Blue. Optional.
        dwelling_line_geojson: nearest-dwelling line (dwellings.py) -- a
            LineString from the site to the closest off-site dwelling. Red, with
            a distance-in-feet label. Optional.
        out_path: output .dxf path. Relative paths resolve under ``app/`` (the
            project root), matching the other modules' ``data/`` convention.
        label: title text + title-block project name.

    Returns:
        (out_path_str, summary_dict). ``summary`` keys: ok, out_path, epsg,
        crs_name, layer_count, entity_count, entities_by_layer, parcel_acres,
        buildable_acres, setback_source, nearest_dwelling_ft, bbox_ft, label.
    """
    # ----- 1. coerce inputs to 4326 shapely ---------------------------------
    parcel = _coerce_geom(parcel_geojson, all_features=False)  # first feature = parcel
    if parcel is None or parcel.is_empty:
        raise ValueError("parcel_geojson did not yield a non-empty geometry")
    if parcel.geom_type not in ("Polygon", "MultiPolygon"):
        raise ValueError("parcel must be a (Multi)Polygon; got %s" % parcel.geom_type)

    buildable = _coerce_geom(buildable_geojson, all_features=True)
    setback = _coerce_geom(setback_geojson, all_features=True)
    wetlands = _coerce_geom(wetlands_geojson, all_features=True)
    flood = _coerce_geom(flood_geojson, all_features=True)
    dwelling = _coerce_geom(dwelling_line_geojson, all_features=True)

    # ----- 2. projection 4326 -> VA State Plane US feet ---------------------
    cen = parcel.centroid
    to_ft, epsg, crs_name = _make_to_ft(cen.x, cen.y)

    parcel_ft = to_ft(parcel)
    buildable_ft = to_ft(buildable) if buildable is not None else None
    wetlands_ft = to_ft(wetlands) if wetlands is not None else None
    flood_ft = to_ft(flood) if flood is not None else None
    dwelling_ft = to_ft(dwelling) if dwelling is not None else None

    # setback: use supplied, else compute a 100 ft inward ring (parcel - inset).
    setback_source = "none"
    if setback is not None:
        setback_ft = to_ft(setback)
        setback_source = "supplied"
    else:
        inner = parcel_ft.buffer(-SETBACK_FT_DEFAULT)
        if inner.is_empty:
            setback_ft = parcel_ft  # setback consumes the whole parcel
        else:
            setback_ft = parcel_ft.difference(inner)
        setback_source = "computed_%.0fft_inward" % SETBACK_FT_DEFAULT

    # ----- 3. DXF doc + layers ----------------------------------------------
    doc = ezdxf.new("R2018", setup=True)  # setup=True loads standard linetypes (DASHED, ...)
    doc.header["$INSUNITS"] = 2  # 2 = feet (US survey foot family) for the drawing
    doc.header["$MEASUREMENT"] = 0  # imperial
    msp = doc.modelspace()

    for name, (color, ltype) in LAYERS.items():
        lt = ltype if ltype in doc.linetypes else "CONTINUOUS"
        doc.layers.add(name=name, color=color, linetype=lt)

    counts = {name: 0 for name in LAYERS}

    # ----- 4. draw geometries (order = back-to-front) -----------------------
    # buildable fill first (behind outlines)
    if buildable_ft is not None and not buildable_ft.is_empty:
        counts["BUILDABLE"] += _add_solid_hatch(msp, buildable_ft, "BUILDABLE", ACI_GREEN)
    if flood_ft is not None:
        counts["FLOOD"] += _add_geometry(msp, flood_ft, "FLOOD")
    if wetlands_ft is not None:
        counts["WETLANDS"] += _add_geometry(msp, wetlands_ft, "WETLANDS")
    if buildable_ft is not None:
        counts["BUILDABLE"] += _add_geometry(msp, buildable_ft, "BUILDABLE")
    counts["SETBACK"] += _add_geometry(msp, setback_ft, "SETBACK")
    counts["PARCEL"] += _add_geometry(msp, parcel_ft, "PARCEL")  # boundary on top

    # ----- 5. acreages + dwelling distance (all in feet) --------------------
    parcel_acres = round(_acres_ft(parcel_ft), 3)
    buildable_acres = round(_acres_ft(buildable_ft), 3) if buildable_ft is not None else None

    nearest_dwelling_ft = None
    if dwelling_ft is not None and not dwelling_ft.is_empty:
        counts["DWELLING-OFFSET"] += _add_geometry(msp, dwelling_ft, "DWELLING-OFFSET")
        nearest_dwelling_ft = round(_line_len_ft(dwelling_ft), 1)
        # distance label at the line midpoint
        if nearest_dwelling_ft and nearest_dwelling_ft > 0:
            line0 = dwelling_ft.geoms[0] if dwelling_ft.geom_type == "MultiLineString" else dwelling_ft
            try:
                mid = line0.interpolate(0.5, normalized=True)
                txt = msp.add_text(
                    "Nearest dwelling: %s ft" % ("{:,.0f}".format(nearest_dwelling_ft)),
                    dxfattribs={"layer": "DWELLING-OFFSET", "height": _label_h(parcel_ft), "color": ACI_RED},
                )
                txt.set_placement((mid.x, mid.y), align=TextEntityAlignment.BOTTOM_LEFT)
                counts["DWELLING-OFFSET"] += 1
            except Exception:
                pass

    # ----- 6. annotations + title block -------------------------------------
    counts["ANNOTATION"] += _add_annotations(
        msp, parcel_ft, label, parcel_acres, buildable_acres, nearest_dwelling_ft
    )
    counts["TITLEBLOCK"] += _add_title_block(
        msp, parcel_ft, label, epsg, crs_name, parcel_acres, buildable_acres, nearest_dwelling_ft
    )

    # zoom extents so the file opens framed on the content
    try:
        ezdxf.zoom.extents(msp, factor=1.1)
    except Exception:
        pass

    # ----- 7. save ----------------------------------------------------------
    out = pathlib.Path(out_path)
    if not out.is_absolute():
        out = ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(out)

    entity_count = sum(counts.values())
    minx, miny, maxx, maxy = parcel_ft.bounds
    summary = {
        "ok": True,
        "out_path": str(out),
        "epsg": epsg,
        "crs_name": crs_name,
        "layer_count": len(LAYERS),
        "entity_count": entity_count,
        "entities_by_layer": counts,
        "parcel_acres": parcel_acres,
        "buildable_acres": buildable_acres,
        "setback_source": setback_source,
        "nearest_dwelling_ft": nearest_dwelling_ft,
        "bbox_ft": [round(minx, 2), round(miny, 2), round(maxx, 2), round(maxy, 2)],
        "label": label,
    }
    return str(out), summary


# =============================================================================
# annotation helpers
# =============================================================================
def _label_h(parcel_ft):
    """Pick a readable text height (ft) ~2.5% of the parcel's larger extent."""
    minx, miny, maxx, maxy = parcel_ft.bounds
    span = max(maxx - minx, maxy - miny) or 100.0
    return round(max(8.0, span * 0.025), 2)


def _add_annotations(msp, parcel_ft, label, parcel_acres, buildable_acres, nearest_ft):
    """Title + acreage/distance lines as TEXT above the parcel. Returns count."""
    minx, miny, maxx, maxy = parcel_ft.bounds
    h = _label_h(parcel_ft)
    x = minx
    y = maxy + h * 2.0  # stack above the top edge
    n = 0

    title = msp.add_text(label, dxfattribs={"layer": "ANNOTATION", "height": h * 1.6, "color": ACI_WHITE})
    title.set_placement((x, y), align=TextEntityAlignment.BOTTOM_LEFT)
    n += 1
    y -= h * 2.4

    lines = ["Parcel: %s ac" % ("{:,.2f}".format(parcel_acres))]
    if buildable_acres is not None:
        pct = (100.0 * buildable_acres / parcel_acres) if parcel_acres else 0.0
        lines.append("Buildable: %s ac (%.1f%%)" % ("{:,.2f}".format(buildable_acres), pct))
    if nearest_ft is not None:
        lines.append("Nearest dwelling: %s ft" % ("{:,.0f}".format(nearest_ft)))

    for ln in lines:
        t = msp.add_text(ln, dxfattribs={"layer": "ANNOTATION", "height": h, "color": ACI_WHITE})
        t.set_placement((x, y), align=TextEntityAlignment.BOTTOM_LEFT)
        n += 1
        y -= h * 1.5
    return n


def _add_title_block(msp, parcel_ft, label, epsg, crs_name, parcel_acres, buildable_acres, nearest_ft):
    """Corner MTEXT title block (lower-left, below the parcel). Returns count."""
    minx, miny, maxx, maxy = parcel_ft.bounds
    h = _label_h(parcel_ft)
    span = max(maxx - minx, maxy - miny) or 100.0

    rows = [
        r"\fArial|b1;DC SITE EXHIBIT\fArial|b0;",
        label,
        "CRS: EPSG:%d  (%s)" % (epsg, crs_name),
        "Units: US survey feet",
        "Parcel area: %s ac" % ("{:,.2f}".format(parcel_acres)),
    ]
    if buildable_acres is not None:
        rows.append("Buildable: %s ac" % ("{:,.2f}".format(buildable_acres)))
    if nearest_ft is not None:
        rows.append("Nearest dwelling: %s ft" % ("{:,.0f}".format(nearest_ft)))
    rows.append("Generated by dc-site-copilot (no AI; ezdxf+shapely+pyproj)")

    mtext = msp.add_mtext("\\P".join(rows), dxfattribs={"layer": "TITLEBLOCK", "char_height": h})
    mtext.set_location((minx, miny - h * 2.0), attachment_point=7)  # 7 = top-left
    try:
        mtext.dxf.width = span * 0.6  # wrap width
    except Exception:
        pass
    return 1


# =============================================================================
# self-test
# =============================================================================
def _first_feature_fc(path):
    """Load a .geojson and return a FeatureCollection holding only its 1st feature."""
    with open(path) as f:
        fc = json.load(f)
    feats = fc.get("features", [])
    if not feats:
        raise ValueError("%s has no features" % path)
    return {"type": "FeatureCollection", "features": [feats[0]]}, len(feats)


def _synth_dwelling_line(parcel_path):
    """Build a demo nearest-dwelling LineString (4326) from the parcel centroid
    outward to a point ~1200 ft NE of the parcel -- only used when no real
    dwellings.py output exists, so the DWELLING-OFFSET layer is exercised."""
    parcel = _coerce_geom(parcel_path, all_features=False)
    cen = parcel.centroid
    # ~ 0.003 deg lon / 0.0025 deg lat offset ~= a few hundred meters in VA
    end = (cen.x + 0.0045, cen.y + 0.0035)
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[cen.x, cen.y], list(end)]},
        "properties": {"kind": "nearest_dwelling", "synthetic": True},
    }


def _test():
    sample = DATA / "parcel_sample.geojson"
    if not sample.exists():
        print("No data/parcel_sample.geojson -- run parcels.py first. Skipping test.")
        return

    parcel_in, n_parcels = _first_feature_fc(sample)
    print("=" * 70)
    print("DXF EXPORT TEST")
    print("=" * 70)
    print("parcel source : parcel_sample.geojson (first of %d features)" % n_parcels)

    # use real constraints outputs if present, else let build_dxf compute setback
    def _opt(name):
        p = DATA / name
        return str(p) if p.exists() and p.stat().st_size > 0 else None

    buildable = _opt("constraints_buildable.geojson")
    flood = _opt("constraints_flood.geojson")
    wetlands = _opt("constraints_wetlands.geojson")

    # if the real flood/wetlands are empty FCs (rural parcel), prefer the demo
    # layers so FLOOD/WETLANDS get exercised; fall back to None otherwise.
    def _nonempty_fc(path):
        if not path:
            return None
        try:
            d = json.load(open(path))
            return path if d.get("features") else None
        except Exception:
            return None

    flood = _nonempty_fc(flood) or _nonempty_fc(_opt("constraints_demo_flood.geojson"))
    wetlands = _nonempty_fc(wetlands) or _nonempty_fc(_opt("constraints_demo_wetlands.geojson"))

    dwelling = _synth_dwelling_line(sample)  # synthetic line to exercise the red layer

    print("buildable     : %s" % (buildable or "(none -> outline skipped)"))
    print("flood         : %s" % (flood or "(none)"))
    print("wetlands      : %s" % (wetlands or "(none)"))
    print("setback       : (none supplied -> 100 ft inward computed)")
    print("dwelling line : synthetic demo LineString")
    print()

    out_path, summary = build_dxf(
        parcel_in,
        buildable_geojson=buildable,
        setback_geojson=None,            # exercise the computed-setback path
        wetlands_geojson=wetlands,
        flood_geojson=flood,
        dwelling_line_geojson=dwelling,
        out_path="data/parcel_exhibit.dxf",
        label="Proposed Data Center Site",
    )

    print("SUMMARY")
    print(json.dumps(summary, indent=2))
    print()
    print("OUT FILE : %s" % out_path)
    print("DXF written OK (%d entities across %d layers)."
          % (summary["entity_count"], summary["layer_count"]))


if __name__ == "__main__":
    _test()
