#!/usr/bin/env python3
"""Pull HIFLD Electric Substations for VIRGINIA -> GeoJSON.
Free public ArcGIS REST (no key). Filters by VA envelope; paginates.
Writes data/va_substations.geojson (simplified: VOLTAGE, STATUS, NAME only, coords rounded)."""
import json, urllib.request, urllib.parse, pathlib, ssl
ssl._create_default_https_context = ssl._create_unverified_context

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"; DATA.mkdir(exist_ok=True)

VA_BBOX = (36.50, -83.70, 39.47, -75.24) # south, west, north, east for Overpass

def fetch_overpass():
    # Overpass QL query for power substations in VA bounding box
    # Using a slightly smaller bbox or just a limit to avoid massive data for the hackathon demo
    query = f"""
    [out:json][timeout:60];
    nwr["power"="substation"]({VA_BBOX[0]},{VA_BBOX[1]},{VA_BBOX[2]},{VA_BBOX[3]});
    out center;
    """
    url = "https://overpass-api.de/api/interpreter"
    data = query.encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "cenergy-gis/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)

def simplify(data):
    feats = []
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        
        # Get coordinates depending on type
        if el["type"] == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else: # way or relation with out center
            center = el.get("center", {})
            lat, lon = center.get("lat"), center.get("lon")
            
        if lat is None or lon is None:
            continue
            
        feats.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "name": tags.get("name", "Unknown Substation"),
                "voltage": tags.get("voltage", "Unknown"),
                "operator": tags.get("operator", "Unknown")
            }
        })
    return feats

def main():
    print("Fetching substations via Overpass API...")
    data = fetch_overpass()
    feats = simplify(data)
    path = DATA / "va_substations.geojson"
    json.dump({"type": "FeatureCollection", "features": feats}, open(path, "w"), separators=(",", ":"))
    print(f"\n{len(feats)} substations -> {path} ({path.stat().st_size//1024} KB)")

if __name__ == "__main__":
    main()

