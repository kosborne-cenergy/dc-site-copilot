"""Download US counties GeoJSON, filter to Virginia (FIPS 51), split counties vs independent cities.
Writes data/va_geo.geojson (all VA county-equivalents) + counties_all.json (authoritative name+fips list)."""
import json, urllib.request, pathlib

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"; DATA.mkdir(exist_ok=True)
SRC = "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json"

print("downloading US counties geojson...")
raw = json.loads(urllib.request.urlopen(SRC, timeout=120).read())

va_feats, counties, cities = [], [], []
for f in raw["features"]:
    fips = f.get("id", "")
    if not fips.startswith("51"):
        continue
    p = f.get("properties", {})
    name = p.get("NAME", "")
    lsad = (p.get("LSAD", "") or "").lower()
    f["properties"]["_name"] = name
    f["properties"]["_fips"] = fips
    va_feats.append(f)
    rec = {"name": name, "fips": fips, "type": "city" if "city" in lsad else "county"}
    (cities if rec["type"] == "city" else counties).append(rec)

json.dump({"type": "FeatureCollection", "features": va_feats},
          open(DATA / "va_geo.geojson", "w"), separators=(",", ":"))
counties.sort(key=lambda r: r["name"]); cities.sort(key=lambda r: r["name"])
json.dump({"state": "Virginia", "counties": counties, "cities": cities},
          open(ROOT / "counties_all.json", "w"), indent=2)
print(f"VA features: {len(va_feats)} | counties: {len(counties)} | independent cities: {len(cities)}")
print("first counties:", ", ".join(c["name"] for c in counties[:8]))
