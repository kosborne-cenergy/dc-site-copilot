"""Interactive parcel-pipeline Flask blueprint for the DC Site Copilot.

Mounts into main.py via:
    from parcel_app import bp as parcel_bp
    app.register_blueprint(parcel_bp)

JSON APIs (all under the app root, no url_prefix so /api/* + /downloads/* are clean):
  GET  /api/recommend?mw=&sqft=&budget=   -> {acres_needed, ranked:[...]}
  GET  /api/parcels?fips=&min_acres=&bbox= -> GeoJSON FeatureCollection (suitable filtered)
  POST /api/pipeline   (body = a parcel GeoJSON Feature)
        -> {summary, constraints{wetlands,flood,buildable,setback}, buildable,
            dwelling, dxf_url, image_url}
  GET  /downloads/<f>  -> serves app/dist/downloads/<f>
  GET  /app            -> the interactive parcel.html page (also served by main.py)
  GET  /api/counties   -> light county list (name+fips+centroid+bbox) for the map

Every heavy module (parcels/constraints/dwellings/dxf_export/nano_banana/recommend)
is imported DEFENSIVELY so a missing or broken one degrades gracefully instead of
taking down the whole app. Per-request handlers also try/except so a live-service
outage (ArcGIS, Gemini) returns a friendly JSON error, never a 500 crash.
"""

from __future__ import annotations

import json
import os
import time
import traceback
import uuid

from flask import Blueprint, jsonify, request, send_from_directory

# --------------------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))            # .../app
_DIST = os.path.join(_HERE, "dist")
_DATA = os.path.join(_HERE, "data")
_DOWNLOADS = os.path.join(_DIST, "downloads")
os.makedirs(_DOWNLOADS, exist_ok=True)

# --------------------------------------------------------------------------------------
# defensive imports -- a missing/broken module must NOT crash the blueprint
# --------------------------------------------------------------------------------------
_IMPORT_ERRORS = {}


def _try_import(name):
    """Import parcel.<name>; on failure record the error and return None."""
    try:
        mod = __import__("parcel.%s" % name, fromlist=[name])
        return mod
    except Exception as e:  # noqa: BLE001 - we genuinely want to swallow anything
        _IMPORT_ERRORS[name] = "%s: %s" % (type(e).__name__, e)
        return None


parcels = _try_import("parcels")
constraints = _try_import("constraints")
dwellings = _try_import("dwellings")       # may not exist yet -> graceful
dxf_export = _try_import("dxf_export")
nano_banana = _try_import("nano_banana")
recommend = _try_import("recommend")

bp = Blueprint("parcel", __name__)


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------
def _f(name, default=None):
    """Parse a float query arg; return default if absent/blank/unparseable."""
    v = request.args.get(name)
    if v is None or str(v).strip() == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _err(msg, code=200, **extra):
    """Friendly JSON error (HTTP 200 by default so the frontend can show a message
    rather than treating it as a hard network failure)."""
    payload = {"ok": False, "error": str(msg)}
    payload.update(extra)
    return jsonify(payload), code


def _county_index():
    """Map FIPS -> {name, fips, centroid:[lon,lat], bbox:[w,s,e,n]} from va_geo.geojson.
    Cached after first build. Returns {} if the file is missing/unreadable."""
    cache = getattr(_county_index, "_cache", None)
    if cache is not None:
        return cache
    out = {}
    path = os.path.join(_DATA, "va_geo.geojson")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            gj = json.load(fh)
        for feat in gj.get("features", []):
            props = feat.get("properties", {}) or {}
            fips = str(props.get("_fips") or props.get("FIPS") or "").strip()
            if not fips:
                continue
            w, s, e, n = _geom_bbox(feat.get("geometry"))
            if w is None:
                continue
            out[fips] = {
                "name": props.get("_name") or props.get("NAME") or fips,
                "fips": fips,
                "centroid": [round((w + e) / 2.0, 6), round((s + n) / 2.0, 6)],
                "bbox": [round(w, 6), round(s, 6), round(e, 6), round(n, 6)],
            }
    except Exception as e:  # noqa: BLE001
        _IMPORT_ERRORS["va_geo"] = "%s: %s" % (type(e).__name__, e)
    _county_index._cache = out
    return out


def _geom_bbox(geom):
    """Bounding box (w,s,e,n) of a GeoJSON Polygon/MultiPolygon. (None,)*4 on failure."""
    if not geom:
        return (None, None, None, None)
    xs, ys = [], []

    def _walk(coords, depth):
        # descend to coordinate pairs
        if depth == 0:
            try:
                xs.append(float(coords[0]))
                ys.append(float(coords[1]))
            except Exception:
                pass
            return
        for c in coords:
            _walk(c, depth - 1)

    t = geom.get("type")
    coords = geom.get("coordinates")
    try:
        if t == "Polygon":
            _walk(coords, 2)
        elif t == "MultiPolygon":
            _walk(coords, 3)
        else:
            _walk(coords, 2)
    except Exception:
        return (None, None, None, None)
    if not xs or not ys:
        return (None, None, None, None)
    return (min(xs), min(ys), max(xs), max(ys))


def _feature_centroid(feature):
    """Rough centroid (lon, lat) of a parcel GeoJSON Feature/geometry."""
    geom = feature.get("geometry") if isinstance(feature, dict) and "geometry" in feature else feature
    w, s, e, n = _geom_bbox(geom)
    if w is None:
        return None
    return ((w + e) / 2.0, (s + n) / 2.0)


def _nearest_dwelling_safe(feature, summary):
    """Call dwellings.nearest_dwelling defensively across plausible signatures.

    Returns (dwelling_payload_dict_or_None, line_geojson_for_dxf_or_None, status_str).
    dwelling_payload = {distance_ft, line: <GeoJSON Feature LineString>, ...}.
    Never raises.
    """
    if dwellings is None:
        return None, None, "dwellings module not available (%s)" % _IMPORT_ERRORS.get("dwellings", "missing")

    fn = getattr(dwellings, "nearest_dwelling", None)
    if fn is None:
        return None, None, "dwellings.nearest_dwelling not found"

    # try a few call conventions; the module spec says nearest_dwelling(parcel)
    attempts = (
        lambda: fn(feature),
        lambda: fn(feature["geometry"] if isinstance(feature, dict) and "geometry" in feature else feature),
    )
    last = None
    for call in attempts:
        try:
            res = call()
        except Exception as e:  # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, e)
            continue
        return _normalize_dwelling(res)
    return None, None, "nearest_dwelling failed: %s" % last


def _normalize_dwelling(res):
    """Coerce whatever dwellings.nearest_dwelling returns into our payload shape."""
    if res is None:
        return None, None, "no dwelling found"

    line_fc = None
    dist_ft = None
    payload = {}

    if isinstance(res, dict):
        payload = dict(res)
        # distance
        for k in ("distance_ft", "dist_ft", "feet", "nearest_dwelling_ft", "distance"):
            if res.get(k) is not None:
                try:
                    dist_ft = float(res[k])
                    break
                except (TypeError, ValueError):
                    pass
        # line geometry (Feature, FeatureCollection, or bare geometry)
        for k in ("line", "line_geojson", "geometry", "feature"):
            if res.get(k):
                line_fc = res[k]
                break
        if line_fc is None and res.get("type") in ("Feature", "FeatureCollection", "LineString"):
            line_fc = res
    payload.setdefault("distance_ft", dist_ft)
    return payload, line_fc, "ok"


def _save_download(src_path, prefer_name):
    """Copy/move a produced file into dist/downloads and return its /downloads URL.
    If the file is already under downloads, just return its URL."""
    if not src_path or not os.path.exists(src_path):
        return None
    base = os.path.basename(src_path)
    # uniquify to avoid collisions across requests
    stamp = time.strftime("%H%M%S")
    name = "%s_%s_%s" % (os.path.splitext(prefer_name)[0], stamp, base) if prefer_name else base
    dest = os.path.join(_DOWNLOADS, name)
    try:
        if os.path.abspath(src_path) != os.path.abspath(dest):
            import shutil
            shutil.copy2(src_path, dest)
        else:
            dest = src_path
    except Exception:
        dest = src_path
    return "/downloads/%s" % os.path.basename(dest)


# --------------------------------------------------------------------------------------
# routes
# --------------------------------------------------------------------------------------
@bp.route("/app")
def parcel_page():
    """Serve the interactive parcel flow page."""
    return send_from_directory(_DIST, "parcel.html")


@bp.route("/downloads/<path:fname>")
def downloads(fname):
    """Serve produced DXF/PNG files."""
    return send_from_directory(_DOWNLOADS, fname)


@bp.route("/api/health")
def api_health():
    return jsonify({
        "ok": True,
        "modules": {
            "parcels": parcels is not None,
            "constraints": constraints is not None,
            "dwellings": dwellings is not None,
            "dxf_export": dxf_export is not None,
            "nano_banana": nano_banana is not None,
            "recommend": recommend is not None,
        },
        "import_errors": _IMPORT_ERRORS,
        "counties_loaded": len(_county_index()),
    })


@bp.route("/api/counties")
def api_counties():
    """Light county list for the map (name, fips, centroid, bbox)."""
    idx = _county_index()
    return jsonify({"ok": True, "counties": sorted(idx.values(), key=lambda c: c["name"])})


@bp.route("/api/recommend")
def api_recommend():
    """Sizing + county recommendation.

    Query: mw, sqft, budget (any subset). Returns acres_needed + ranked counties,
    each annotated with the centroid/bbox from va_geo.geojson so the frontend can
    zoom straight to a chosen county.
    """
    if recommend is None:
        return _err("recommend module unavailable: %s" % _IMPORT_ERRORS.get("recommend", "missing"))

    mw = _f("mw")
    sqft = _f("sqft")
    budget = _f("budget")

    try:
        acres = None
        if mw is not None or sqft is not None:
            acres = recommend.acres_needed(mw=mw, sqft=sqft)
        ranked = recommend.recommend_counties(mw=mw, sqft=sqft, budget=budget, top_n=8)
    except Exception as e:  # noqa: BLE001
        return _err("recommend failed: %s" % e, trace=traceback.format_exc(limit=3))

    # decorate ranked rows with map geometry (centroid + bbox)
    idx = _county_index()
    for row in ranked:
        geo = idx.get(str(row.get("fips", "")).strip())
        if geo:
            row["centroid"] = geo["centroid"]
            row["bbox"] = geo["bbox"]

    return jsonify({
        "ok": True,
        "acres_needed": acres,                       # {acres_needed, basis} or None
        "min_acres": (acres or {}).get("acres_needed") if acres else None,
        "ranked": ranked,
    })


@bp.route("/api/parcels")
def api_parcels():
    """Fetch parcels for a county/bbox, filtered to those meeting min_acres.

    Query:
        fips      : 5-digit county FIPS (e.g. 51111). Optional if bbox given.
        min_acres : minimum parcel acreage to keep (suitable filter). Optional.
        bbox      : "w,s,e,n" in EPSG:4326. Optional; if omitted and fips given,
                    the county bbox from va_geo.geojson is used to bound the query.
        limit     : hard cap on parcels fetched (default 1500 for snappy demo).
    Returns a GeoJSON FeatureCollection; each suitable feature gets
    properties.suitable=True. Includes counts in top-level metadata.
    """
    if parcels is None:
        return _err("parcels module unavailable: %s" % _IMPORT_ERRORS.get("parcels", "missing"))

    fips = (request.args.get("fips") or "").strip() or None
    min_acres = _f("min_acres", 0.0) or 0.0
    limit = int(_f("limit", 1500) or 1500)

    bbox = None
    bbox_arg = (request.args.get("bbox") or "").strip()
    if bbox_arg:
        try:
            parts = [float(x) for x in bbox_arg.split(",")]
            if len(parts) == 4:
                bbox = tuple(parts)
        except ValueError:
            bbox = None
    # if no bbox but we have a county, bound by the county bbox (keeps it fast)
    if bbox is None and fips:
        geo = _county_index().get(fips)
        if geo:
            bbox = tuple(geo["bbox"])

    try:
        fc = parcels.get_parcels(county_fips=fips, bbox=bbox, limit=limit)
    except Exception as e:  # noqa: BLE001
        return _err("parcels.get_parcels failed: %s" % e, trace=traceback.format_exc(limit=3))

    total = len(fc.get("features", []))

    # mark suitable ones; keep ALL features so the map can show context (gray vs green)
    suitable_count = 0
    if recommend is not None and min_acres:
        try:
            suit = recommend.suitable_parcels(fc, min_acres=min_acres)
            suitable_ids = {id(f) for f in suit.get("features", [])}
        except Exception:
            suitable_ids = set()
        for f in fc.get("features", []):
            props = f.setdefault("properties", {})
            ac = props.get("acreage")
            is_ok = ac is not None and ac >= min_acres
            props["suitable"] = bool(is_ok)
            if is_ok:
                suitable_count += 1
    else:
        for f in fc.get("features", []):
            f.setdefault("properties", {})["suitable"] = True
        suitable_count = total

    fc["ok"] = True
    fc["count"] = total
    fc["suitable_count"] = suitable_count
    fc["min_acres"] = min_acres
    fc["fips"] = fips
    return jsonify(fc)


@bp.route("/api/pipeline", methods=["POST"])
def api_pipeline():
    """Full per-parcel pipeline: constraints + nearest dwelling + DXF + render.

    Body: a parcel GeoJSON Feature (or geometry / 1-feature FeatureCollection).
    Returns {summary, constraints{...}, buildable, dwelling, dxf_url, image_url}
    plus per-step status flags so the UI can show what worked.
    """
    feature = request.get_json(silent=True)
    if not feature:
        return _err("POST body must be a parcel GeoJSON Feature (JSON).", code=400)

    # normalize to a single-feature FeatureCollection for the downstream modules
    if feature.get("type") == "FeatureCollection":
        feats = feature.get("features") or []
        if not feats:
            return _err("FeatureCollection has no features.", code=400)
        parcel_feature = feats[0]
    elif feature.get("type") == "Feature":
        parcel_feature = feature
    elif feature.get("type") in ("Polygon", "MultiPolygon"):
        parcel_feature = {"type": "Feature", "geometry": feature, "properties": {}}
    else:
        return _err("Unsupported GeoJSON type: %r" % feature.get("type"), code=400)

    parcel_fc = {"type": "FeatureCollection", "features": [parcel_feature]}
    status = {}
    uid = uuid.uuid4().hex[:8]

    # ---- 1. constraints + buildable -------------------------------------------------
    con = {}
    summary = {}
    buildable_fc = None
    if constraints is None:
        status["constraints"] = "unavailable: %s" % _IMPORT_ERRORS.get("constraints", "missing")
    else:
        try:
            con = constraints.build_constraints(parcel_fc, setback_ft=100.0)
            summary = con.get("summary", {}) or {}
            buildable_fc = con.get("buildable")
            status["constraints"] = "ok"
        except Exception as e:  # noqa: BLE001
            status["constraints"] = "failed: %s" % e

    # ---- 2. nearest dwelling --------------------------------------------------------
    dwelling_payload, dwelling_line, dstatus = _nearest_dwelling_safe(parcel_feature, summary)
    status["dwelling"] = dstatus

    # ---- 3. DXF export --------------------------------------------------------------
    dxf_url = None
    if dxf_export is None:
        status["dxf"] = "unavailable: %s" % _IMPORT_ERRORS.get("dxf_export", "missing")
    else:
        try:
            out_path = os.path.join(_DOWNLOADS, "site_%s.dxf" % uid)
            res = dxf_export.build_dxf(
                parcel_fc,
                buildable_geojson=buildable_fc,
                setback_geojson=None,
                wetlands_geojson=con.get("wetlands"),
                flood_geojson=con.get("flood"),
                dwelling_line_geojson=dwelling_line,
                out_path=out_path,
                label="Proposed Data Center Site",
            )
            produced = res[0] if isinstance(res, (list, tuple)) else out_path
            dxf_url = _save_download(produced, "site")
            status["dxf"] = "ok"
            # pick up dxf-computed nearest distance if dwellings module gave none
            try:
                dsum = res[1] if isinstance(res, (list, tuple)) and len(res) > 1 else {}
                if dwelling_payload is None and dsum.get("nearest_dwelling_ft"):
                    dwelling_payload = {"distance_ft": dsum["nearest_dwelling_ft"], "synthetic": False}
            except Exception:
                pass
        except Exception as e:  # noqa: BLE001
            status["dxf"] = "failed: %s" % e

    # ---- 4. nano banana render ------------------------------------------------------
    image_url = None
    if nano_banana is None:
        status["image"] = "unavailable: %s" % _IMPORT_ERRORS.get("nano_banana", "missing")
    else:
        try:
            render_summary = dict(summary)
            render_summary.setdefault("acres", summary.get("buildable_acres") or summary.get("parcel_acres"))
            out_png = os.path.join(_DOWNLOADS, "render_%s.png" % uid)
            r = nano_banana.render_datacenter(render_summary, out_path=out_png)
            image_url = _save_download(r.get("image_path"), "render")
            status["image"] = "ok (%s)" % r.get("model")
        except Exception as e:  # noqa: BLE001
            # Gemini quota / no key -> friendly, don't crash the pipeline
            status["image"] = "failed: %s" % str(e)[:200]

    return jsonify({
        "ok": True,
        "status": status,
        "summary": summary,
        "buildable": buildable_fc,
        "constraints": {
            "wetlands": con.get("wetlands"),
            "flood": con.get("flood"),
            "buildable": buildable_fc,
        },
        "dwelling": (
            {**(dwelling_payload or {}), "line": dwelling_line}
            if (dwelling_payload or dwelling_line) else None
        ),
        "dxf_url": dxf_url,
        "image_url": image_url,
    })


# Smoke test: `python -c "import parcel_app"` then optionally hit the routes.
if __name__ == "__main__":
    print("parcel_app blueprint OK")
    print("import errors:", _IMPORT_ERRORS or "none")
    print("counties loaded:", len(_county_index()))
