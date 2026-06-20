#!/usr/bin/env python3
"""Pull HIFLD Electric Power Transmission Lines for VIRGINIA -> GeoJSON.
Free public ArcGIS REST (no key). Filters by VA envelope; paginates.
Writes data/va_transmission.geojson (simplified: VOLT_CLASS only, coords rounded)."""
import json, urllib.request, urllib.parse, pathlib

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"; DATA.mkdir(exist_ok=True)

CANDIDATES = [
    "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/Electric_Power_Transmission_Lines/FeatureServer/0",
    "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services/Transmission_Lines/FeatureServer/0",
    "https://services2.arcgis.com/FiaPA4ga0iQKduv3/arcgis/rest/services/Electric_Power_Transmission_Lines/FeatureServer/0",
]
VA_BBOX = (-83.70, 36.50, -75.24, 39.47)
OUTFIELDS = "VOLTAGE,VOLT_CLASS,OWNER"


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "cenergy-gis/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def find_endpoint():
    for ep in CANDIDATES:
        try:
            meta = get(ep + "?f=json")
            if meta.get("type") == "Feature Layer" or "fields" in meta:
                fields = {f["name"] for f in meta.get("fields", [])}
                print(f"OK endpoint: {ep}\n  maxRecordCount={meta.get('maxRecordCount')}")
                return ep, fields
        except Exception as e:
            print(f"  fail {ep}: {e}")
    return None, set()


def fetch(ep, fields):
    of = ",".join(f for f in OUTFIELDS.split(",") if f in fields) or "*"
    feats, offset, page = [], 0, 2000
    while True:
        params = {"where": "1=1", "geometry": ",".join(map(str, VA_BBOX)),
                  "geometryType": "esriGeometryEnvelope", "inSR": "4326", "outSR": "4326",
                  "spatialRel": "esriSpatialRelIntersects", "outFields": of,
                  "returnGeometry": "true", "f": "geojson",
                  "resultOffset": offset, "resultRecordCount": page}
        d = get(ep + "/query?" + urllib.parse.urlencode(params))
        fs = d.get("features", [])
        feats += fs
        print(f"  page offset={offset}: +{len(fs)} (total {len(feats)})")
        if len(fs) < page:
            break
        offset += page
    return feats


def simplify(feats):
    """Keep VOLT_CLASS only; round coords to 4 decimals (~11m); drop tiny attrs to shrink file."""
    def rnd(coords):
        if isinstance(coords[0], (int, float)):
            return [round(coords[0], 4), round(coords[1], 4)]
        return [rnd(c) for c in coords]
    out = []
    for f in feats:
        g = f.get("geometry")
        if not g:
            continue
        g["coordinates"] = rnd(g["coordinates"])
        p = f.get("properties", {})
        out.append({"type": "Feature", "geometry": g,
                    "properties": {"vc": p.get("VOLT_CLASS", "?"), "kv": p.get("VOLTAGE")}})
    return out


def main():
    ep, fields = find_endpoint()
    if not ep:
        print("NO WORKING ENDPOINT"); return
    feats = simplify(fetch(ep, fields))
    path = DATA / "va_transmission.geojson"
    json.dump({"type": "FeatureCollection", "features": feats}, open(path, "w"), separators=(",", ":"))
    cls = {}
    for f in feats:
        c = f["properties"]["vc"]; cls[c] = cls.get(c, 0) + 1
    print(f"\n{len(feats)} segments -> {path} ({path.stat().st_size//1024} KB)")
    print("by VOLT_CLASS:", dict(sorted(cls.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()
