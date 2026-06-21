#!/usr/bin/env python3
"""WATER layer for VA: USGS NHD waterbodies (reservoirs / large lakes / tidal, area > 1 km2)
-> per-county surface-water score + map overlay. Public NHD ArcGIS REST (no key).
Mirrors get_fiber.py / get_transmission.py. Surface water = the cooling-water signal for DC siting."""
import json, urllib.request, urllib.parse, pathlib

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"; DATA.mkdir(exist_ok=True)
NHD = "https://hydro.nationalmap.gov/arcgis/rest/services/nhd/MapServer/10/query"
VA_BBOX = (-83.70, 36.50, -75.24, 39.47)
MIN_SQKM = 1.0          # ignore tiny ponds
OVERLAY_MIN_SQKM = 2.0  # only larger bodies drawn on the map


def fetch():
    feats, offset = [], 0
    while True:
        params = {"where": f"areasqkm>{MIN_SQKM}", "geometry": ",".join(map(str, VA_BBOX)),
                  "geometryType": "esriGeometryEnvelope", "inSR": "4326", "outSR": "4326",
                  "spatialRel": "esriSpatialRelIntersects", "outFields": "GNIS_NAME,AREASQKM,FTYPE",
                  "returnGeometry": "true", "f": "geojson", "resultOffset": offset, "resultRecordCount": 1000}
        req = urllib.request.Request(NHD + "?" + urllib.parse.urlencode(params), headers={"User-Agent": "cenergy-gis/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.load(r)
        fs = d.get("features", [])
        feats += fs
        print(f"  page offset={offset}: +{len(fs)} (total {len(feats)})")
        if len(fs) < 1000:
            break
        offset += 1000
    return feats


def aggregate(feats):
    from shapely.geometry import shape, mapping
    vg = json.load(open(DATA / "va_geo.geojson"))
    counties = [(f["properties"]["_fips"], f["properties"]["_name"], shape(f["geometry"]).buffer(0))
                for f in vg["features"]]
    agg = {fips: {"name": nm, "fips": fips, "sqkm": 0.0, "n": 0, "big": ("", 0.0)} for fips, nm, _ in counties}
    overlay = []
    for f in feats:
        try:
            g = shape(f["geometry"])
        except Exception:
            continue
        p = f.get("properties", {})
        area = p.get("AREASQKM") or 0
        name = (p.get("GNIS_NAME") or "").strip()
        cen = g.representative_point()
        hit = None
        for fips, nm, poly in counties:
            if poly.contains(cen):
                hit = fips; break
        if not hit:      # centroid outside VA -> drop (state-level only)
            continue
        a = agg[hit]; a["sqkm"] += area; a["n"] += 1
        if area > a["big"][1]:
            a["big"] = (name, area)
        if area >= OVERLAY_MIN_SQKM:
            overlay.append({"type": "Feature", "geometry": mapping(g.simplify(0.002)),
                            "properties": {"n": name or "Waterbody", "a": round(area, 1)}})
    return agg, overlay


def score(agg):
    out = []
    for fips, a in agg.items():
        s = a["sqkm"]
        sc = 100 if s > 50 else 80 if s >= 10 else 60 if s >= 3 else 45 if s >= 1 else 25
        tier = "Abundant" if sc >= 80 else "Adequate" if sc >= 45 else "Limited"
        out.append({"fips": fips, "name": a["name"], "water_score": sc, "water_tier": tier,
                    "surface_water_sqkm": round(a["sqkm"], 1), "n_waterbodies": a["n"],
                    "largest_waterbody": a["big"][0] or "—"})
    return out


def main():
    feats = fetch()
    agg, overlay = aggregate(feats)
    scores = score(agg)
    json.dump(scores, open(DATA / "va_water_scores.json", "w"), indent=2)
    json.dump({"type": "FeatureCollection", "features": overlay}, open(DATA / "va_water.geojson", "w"),
              separators=(",", ":"))
    tier = {}
    for s in scores:
        tier[s["water_tier"]] = tier.get(s["water_tier"], 0) + 1
    top = sorted(scores, key=lambda x: -x["surface_water_sqkm"])[:8]
    print(f"\n{len(scores)} counties scored; {len(overlay)} waterbodies in overlay -> data/va_water*.{{json,geojson}}")
    print("tiers:", tier)
    print("top water:", ", ".join(f"{t['name']}({t['surface_water_sqkm']}km2)" for t in top))


if __name__ == "__main__":
    main()
