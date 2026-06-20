#!/usr/bin/env python3
"""Build the FIBER layer for VIRGINIA (data-center "Fiber" dimension).

Two parts, both PUBLIC-source:
  1. SCRAPE  data.virginia.gov DataStore -> statewide fiber-to-premises buildout
             points (BEAD-funded, technology code 50), aggregated per county.
             This is the live, scraped signal (proves real public data).
  2. COMPILE the DC-relevant fiber intelligence that is NOT cleanly scrapable
             (carrier route geometry is proprietary): interconnection HUBS,
             long-haul CORRIDORS, and DARK-FIBER regions — identified from
             public sources (Equinix/PeeringDB, submarinenetworks.com, MBC,
             carrier route maps, the InterTubes long-haul study).

Writes:
  data/va_fiber.geojson        hubs (points) + corridors (lines) overlay
  data/va_fiber_scores.json    per-county fiber score + dark-fiber rating

Transparent, proximity-based score (same spirit as the energy/transmission
layer): dark-fiber region base + nearest-hub bonus + on-corridor bonus.
All outputs labeled AI-assisted / public-source — demo, verify before use.

    python get_fiber.py            # scrape + compile + score
    python get_fiber.py --no-scrape  # skip the live scrape (offline)
"""
import json, math, sys, urllib.request, urllib.parse, pathlib

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"; DATA.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# 1. HUBS — fiber interconnection points that matter for DC siting.
#    tier 1 = top-of-state interconnection; 2 = strong regional; 3 = R&E/emerging.
#    (lat, lon are best-estimate decimal degrees for map placement.)
# ---------------------------------------------------------------------------
HUBS = [
    ("Ashburn / Data Center Alley (Equinix, LINX NoVA)", "carrier-hotel", 39.0164, -77.4590, 1,
     "Densest interconnection point on the US East Coast; descends from MAE-East. 350+ networks."),
    ("Virginia Beach subsea landing (MAREA/BRUSA/Dunant, Globalinx)", "subsea-landing", 36.7780, -76.0240, 1,
     "Only major mid-Atlantic transatlantic gateway; subsea capacity backhauled to Ashburn."),
    ("Richmond QTS NAP (White Oak)", "carrier-hotel", 37.5246, -77.3119, 1,
     "Inland hinge: where VA Beach subsea routes aggregate before Ashburn. 17+ carriers, cloud on-ramps."),
    ("Culpeper carrier hotel (Equinix)", "carrier-hotel", 38.4900, -77.9900, 2,
     "Diverse-route relay on the Richmond<->NoVA path; carrier-neutral since 2008."),
    ("Norfolk (Globalinx)", "colocation", 36.8700, -76.2070, 2,
     "Extends the Virginia Beach subsea ecosystem into the Hampton Roads metro."),
    ("Manassas / Prince William cluster", "data-center-campus", 38.7509, -77.4753, 2,
     "Fast-growing NoVA data-center cluster (PW Digital Gateway); SummitIG/Tenebris dark fiber."),
    ("Danville (nDanville open-access)", "open-access", 36.5860, -79.3950, 2,
     "First US municipal open-access network; cheap leasable dark fiber, MBC on-net."),
    ("Roanoke Valley (RVBA) / Botetourt (Google)", "open-access", 37.2710, -79.9414, 3,
     "RVBA open-access dark fiber; Google's Botetourt campus pulling new long-haul into SW VA."),
    ("Blacksburg / Virginia Tech (MARIA 100G)", "research-network", 37.2300, -80.4220, 3,
     "Anchor of Virginia's 100G research/education backbone (Internet2)."),
    ("Charlottesville / UVA (MARIA)", "research-network", 38.0330, -78.5080, 3,
     "Central-VA R&E aggregation node; on the high-traffic US-29 long-haul conduit."),
]

# ---------------------------------------------------------------------------
# City waypoints (approx decimal degrees) used to draw corridor polylines.
# ---------------------------------------------------------------------------
CITY = {
    "Alexandria": (38.8048, -77.0469), "Arlington": (38.8799, -77.1068),
    "Fairfax": (38.8462, -77.3064), "Manassas": (38.7509, -77.4753),
    "Gainesville": (38.7956, -77.6147), "Warrenton": (38.7135, -77.7958),
    "Front Royal": (38.9182, -78.1944), "Winchester": (39.1857, -78.1633),
    "Strasburg": (38.9893, -78.3589), "Harrisonburg": (38.4496, -78.8689),
    "Staunton": (38.1496, -79.0717), "Waynesboro": (38.0685, -78.8895),
    "Lexington": (37.7840, -79.4428), "Covington": (37.7935, -79.9939),
    "Roanoke": (37.2710, -79.9414), "Christiansburg": (37.1299, -80.4089),
    "Wytheville": (36.9485, -81.0848), "Abingdon": (36.7098, -81.9776),
    "Bristol": (36.5951, -82.1887), "Fredericksburg": (38.3032, -77.4605),
    "Richmond": (37.5407, -77.4360), "Sandston": (37.5246, -77.3119),
    "Petersburg": (37.2279, -77.4019), "Emporia": (36.6857, -77.5425),
    "Charlottesville": (38.0293, -78.4767), "Lynchburg": (37.4138, -79.1422),
    "Chatham": (36.8262, -79.3973), "Danville": (36.5860, -79.3950),
    "Martinsville": (36.6915, -79.8725), "South Boston": (36.6999, -78.9014),
    "Culpeper": (38.4732, -77.9961), "Goochland": (37.6884, -77.8847),
    "New Kent": (37.5188, -76.9930), "Williamsburg": (37.2707, -76.7075),
    "Newport News": (36.9788, -76.4280), "Hampton": (37.0299, -76.3452),
    "Norfolk": (36.8508, -76.2859), "Virginia Beach": (36.7780, -76.0240),
    "Ashburn": (39.0164, -77.4590),
}

# ---------------------------------------------------------------------------
# 2. CORRIDORS — long-haul fiber routes through VA (public route knowledge).
#    kind: "strategic" (subsea spine) | "dark" (open-access/dark fiber) | "backbone".
# ---------------------------------------------------------------------------
CORRIDORS = [
    ("VA Beach <-> Richmond <-> Ashburn subsea spine", "strategic",
     ["Virginia Beach", "Richmond", "Culpeper", "Ashburn"],
     "Globalinx/Lumos & Windstream/MBC 'Beach Route'",
     "Carries transatlantic subsea capacity (MAREA/BRUSA/Dunant) inland to Data Center Alley."),
    ("I-95 spine (DC -> Richmond -> NC)", "backbone",
     ["Alexandria", "Fredericksburg", "Richmond", "Petersburg", "Emporia"],
     "Lumen, Zayo, Windstream/Uniti, Crown Castle, FiberLight",
     "Primary terrestrial spine linking Ashburn to Richmond, the Carolinas and Atlanta."),
    ("I-81 Shenandoah Valley (inland N<->S)", "backbone",
     ["Winchester", "Strasburg", "Harrisonburg", "Staunton", "Lexington", "Roanoke",
      "Christiansburg", "Wytheville", "Abingdon", "Bristol"],
     "Osprey/VDOT conduit, Lumen, Shentel, Segra/Lumos",
     "Inland storm-diverse route toward Knoxville/Atlanta; redundant egress for Ashburn."),
    ("I-64 East (Richmond -> Hampton Roads subsea)", "strategic",
     ["Richmond", "New Kent", "Williamsburg", "Newport News", "Hampton", "Norfolk", "Virginia Beach"],
     "Telxius, Globalinx, FiberLight/MFN, Cox, Verizon",
     "Backhauls VA Beach subsea landings to Richmond; the headline diversity route."),
    ("I-64 West (Richmond -> Charlottesville -> WV)", "backbone",
     ["Richmond", "Goochland", "Charlottesville", "Waynesboro", "Staunton", "Covington"],
     "Zayo (Columbus<->Ashburn via WV), Segra, Lumen",
     "Inland diversity toward WV and the Midwest, off the I-95 axis."),
    ("I-66 (NoVA -> Front Royal -> I-81)", "backbone",
     ["Arlington", "Fairfax", "Manassas", "Gainesville", "Front Royal", "Strasburg"],
     "Osprey/VDOT, Lumen, Zayo, Verizon",
     "Links the Prince William/Manassas DC cluster westward to the inland I-81 route."),
    ("US-29 (NoVA -> Danville -> NC)", "backbone",
     ["Gainesville", "Warrenton", "Culpeper", "Charlottesville", "Lynchburg", "Chatham", "Danville"],
     "MBC, Segra, Lumen, Zayo (Norfolk Southern ROW)",
     "VA's highest-traffic long-haul conduit (InterTubes); diverse SW escape from NoVA."),
    ("MBC Southside open-access (dark fiber)", "dark",
     ["Martinsville", "Danville", "South Boston", "Emporia"],
     "Mid-Atlantic Broadband Communities Corp (open-access)",
     "~2,500 mi open-access dark fiber across 41 Southside localities; on-net to Ashburn/Richmond/Atlanta/VA Beach."),
]

# ---------------------------------------------------------------------------
# 3. DARK-FIBER regions -> rating + leasable counties/cities (public-source read).
#    rating drives the score base; "lease" = available today, "build" = gap.
# ---------------------------------------------------------------------------
DARK_REGIONS = [
    ("Northern Virginia", "high", "lease", [
        "Loudoun", "Fairfax", "Prince William", "Arlington", "Alexandria",
        "Fairfax City", "Falls Church", "Manassas", "Manassas Park", "Stafford", "Fauquier"]),
    ("Hampton Roads", "high", "lease", [
        "Virginia Beach", "Norfolk", "Chesapeake", "Portsmouth", "Suffolk",
        "Newport News", "Hampton", "York", "Poquoson", "Williamsburg", "James City"]),
    ("Richmond metro", "medium-high", "lease", [
        "Richmond", "Henrico", "Chesterfield", "Hanover", "Goochland"]),
    ("Southside (MBC open-access)", "medium", "lease", [
        "Halifax", "Mecklenburg", "Pittsylvania", "Danville", "Charlotte", "Brunswick",
        "Greensville", "Emporia", "Martinsville", "Henry", "Patrick", "Nottoway",
        "Lunenburg", "Campbell", "Prince Edward", "Buckingham", "Cumberland", "Amelia",
        "Dinwiddie", "Sussex", "Southampton", "Franklin"]),
    ("Roanoke Valley", "medium", "lease", [
        "Roanoke", "Salem", "Botetourt", "Montgomery", "Radford", "Pulaski", "Floyd"]),
    ("Shenandoah / I-81", "low-medium", "build", [
        "Frederick", "Winchester", "Warren", "Shenandoah", "Page", "Rockingham",
        "Harrisonburg", "Augusta", "Staunton", "Waynesboro", "Rockbridge", "Lexington",
        "Buena Vista", "Clarke"]),
    ("Southwest VA", "low", "build", [
        "Wise", "Lee", "Scott", "Russell", "Buchanan", "Tazewell", "Dickenson",
        "Washington", "Bristol", "Smyth", "Wythe", "Bland", "Grayson", "Carroll",
        "Galax", "Norton", "Giles", "Craig", "Alleghany", "Covington", "Bath", "Highland"]),
    ("Eastern Shore", "desert", "build", ["Accomack", "Northampton"]),
]
RATING_BASE = {"high": 55, "medium-high": 45, "medium": 35, "low-medium": 22, "low": 12, "desert": 5}
RATING_DEFAULT = ("Central / Piedmont", "low-medium", "build")  # counties not listed above


# ---------------------------------------------------------------------------
# geo helpers (pure stdlib — no shapely/geopandas, matching the rest of the app)
# ---------------------------------------------------------------------------
def haversine(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlmb = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _rings(geom):
    """Yield coordinate rings (lon,lat lists) from Polygon/MultiPolygon."""
    t, c = geom.get("type"), geom.get("coordinates", [])
    if t == "Polygon":
        for ring in c:
            yield ring
    elif t == "MultiPolygon":
        for poly in c:
            for ring in poly:
                yield ring


def centroid_bbox(geom):
    xs, ys = [], []
    for ring in _rings(geom):
        for x, y in ring:
            xs.append(x); ys.append(y)
    return (sum(xs) / len(xs), sum(ys) / len(ys),
            (min(xs), min(ys), max(xs), max(ys)))  # lon,lat centroid + bbox


def point_in_geom(lon, lat, geom):
    """Ray-cast PIP over the outer ring(s); good enough for county aggregation."""
    inside = False
    for ring in _rings(geom):
        n = len(ring)
        j = n - 1
        for i in range(n):
            xi, yi = ring[i][0], ring[i][1]
            xj, yj = ring[j][0], ring[j][1]
            if ((yi > lat) != (yj > lat)) and \
               (lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
                inside = not inside
            j = i
    return inside


def dist_point_to_polyline(lat, lon, pts):
    """Min great-circle-ish distance (km) from a point to a polyline (list of (lat,lon))."""
    best = float("inf")
    for i in range(len(pts) - 1):
        best = min(best, _seg_dist(lat, lon, pts[i], pts[i + 1]))
    return best


def _seg_dist(lat, lon, a, b):
    # planar approx in local degrees scaled to km — fine at VA latitudes / segment lengths
    kx = 111.32 * math.cos(math.radians(lat))
    ky = 110.57
    px, py = lon * kx, lat * ky
    ax, ay = a[1] * kx, a[0] * ky
    bx, by = b[1] * kx, b[0] * ky
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0 if L2 == 0 else max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / L2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


# ---------------------------------------------------------------------------
# build the geojson overlay (hubs + corridors)
# ---------------------------------------------------------------------------
def build_geojson():
    feats = []
    for name, typ, lat, lon, tier, why in HUBS:
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates": [round(lon, 4), round(lat, 4)]},
                      "properties": {"kind": "hub", "name": name, "type": typ, "tier": tier, "why": why}})
    for name, kind, cities, owners, why in CORRIDORS:
        coords = [[round(CITY[c][1], 4), round(CITY[c][0], 4)] for c in cities if c in CITY]
        feats.append({"type": "Feature",
                      "geometry": {"type": "LineString", "coordinates": coords},
                      "properties": {"kind": "corridor", "name": name, "ctype": kind,
                                     "owners": owners, "why": why, "cities": cities}})
    return {"type": "FeatureCollection", "features": feats}


# ---------------------------------------------------------------------------
# region lookup (county/city name -> dark-fiber rating)
# ---------------------------------------------------------------------------
def region_for(name):
    n = name.lower().replace(" county", "").replace(" city", "").strip()
    for region, rating, action, members in DARK_REGIONS:
        for m in members:
            if n == m.lower():
                return region, rating, action
    return RATING_DEFAULT


# corridor polylines as (lat,lon) for distance tests
_CORR_PTS = [(name, kind, [(CITY[c][0], CITY[c][1]) for c in cities if c in CITY])
             for name, kind, cities, _o, _w in CORRIDORS]


def score_county(name, clat, clon, geom):
    region, rating, action = region_for(name)
    base = RATING_BASE[rating]

    # nearest hub (weighted by tier) -> proximity bonus.
    # distance is 0 if the hub falls inside the county (handles large counties
    # like Loudoun whose centroid sits far from the Ashburn corner).
    best_hub, best_km, hub_bonus = None, float("inf"), 0
    for hname, _t, hlat, hlon, tier, _w in HUBS:
        d = 0.0 if point_in_geom(hlon, hlat, geom) else haversine(clat, clon, hlat, hlon)
        w = {1: 1.0, 2: 0.7, 3: 0.5}[tier]
        b = (30 if d <= 15 else 20 if d <= 40 else 10 if d <= 80 else 0) * w
        if b > hub_bonus or (b == hub_bonus and d < best_km):
            hub_bonus = b
        if d < best_km:
            best_km, best_hub = d, hname

    # nearest long-haul corridor -> on-corridor bonus + which one.
    # distance is 0 if any corridor waypoint falls inside the county.
    best_corr, corr_km = None, float("inf")
    for cname, _kind, pts in _CORR_PTS:
        if len(pts) < 2:
            continue
        d = 0.0 if any(point_in_geom(lon, lat, geom) for lat, lon in pts) \
            else dist_point_to_polyline(clat, clon, pts)
        if d < corr_km:
            corr_km, best_corr = d, cname
    corr_bonus = 15 if corr_km <= 5 else 8 if corr_km <= 20 else 0

    score = max(0, min(100, round(base + hub_bonus + corr_bonus)))
    tier = ("Tier 1 — fiber-rich" if score >= 70 else
            "Tier 2 — well-connected" if score >= 50 else
            "Tier 3 — thin" if score >= 30 else "Build-required")
    return {
        "fiber_score": score, "fiber_tier": tier,
        "dark_fiber": rating, "dark_fiber_region": region, "dark_fiber_action": action,
        "nearest_hub": best_hub, "nearest_hub_km": round(best_km, 1),
        "on_corridor": best_corr if corr_km <= 20 else None,
        "corridor_km": round(corr_km, 1),
    }


# ---------------------------------------------------------------------------
# live scrape: data.virginia.gov fiber-to-premises buildout (tech code 50)
# ---------------------------------------------------------------------------
BEAD_RID = "891d9440-ea3f-4c7f-aa27-40dce8a26120"  # VA BEAD Final Proposal Awarded Locations
BEAD_URL = "https://data.virginia.gov/api/3/action/datastore_search"


def scrape_fiber_points():
    pts, offset, page = [], 0, 10000
    while True:
        q = urllib.parse.urlencode({"resource_id": BEAD_RID, "limit": page, "offset": offset,
                                    "fields": "latitude,longitude,technology"})
        req = urllib.request.Request(BEAD_URL + "?" + q, headers={"User-Agent": "dc-site-copilot/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            recs = json.load(r)["result"]["records"]
        if not recs:
            break
        for rec in recs:
            if str(rec.get("technology")) == "50":  # fiber-to-premises
                try:
                    pts.append((float(rec["latitude"]), float(rec["longitude"])))
                except (TypeError, ValueError):
                    pass
        print(f"  scraped offset={offset}: +{len(recs)} (fiber pts so far {len(pts)})")
        if len(recs) < page:
            break
        offset += page
    return pts


def aggregate_to_counties(feats, pts):
    """Point-in-polygon count of scraped fiber points per county (bbox pre-filter)."""
    boxed = []
    for f in feats:
        clon, clat, bbox = centroid_bbox(f["geometry"])
        boxed.append((f, clat, clon, bbox))
    counts = {f["properties"]["_fips"]: 0 for f, *_ in boxed}
    for lat, lon in pts:
        for f, _cl, _cn, (xmin, ymin, xmax, ymax) in boxed:
            if xmin <= lon <= xmax and ymin <= lat <= ymax and point_in_geom(lon, lat, f["geometry"]):
                counts[f["properties"]["_fips"]] += 1
                break
    return counts


def main():
    geo_path = DATA / "va_geo.geojson"
    if not geo_path.exists():
        print("MISSING data/va_geo.geojson — run `python get_geo.py` first."); return
    geo = json.load(open(geo_path))
    feats = geo["features"]

    # overlay
    fc = build_geojson()
    json.dump(fc, open(DATA / "va_fiber.geojson", "w"), separators=(",", ":"))
    nh = sum(1 for f in fc["features"] if f["properties"]["kind"] == "hub")
    nc = len(fc["features"]) - nh
    print(f"overlay: {nh} hubs + {nc} corridors -> data/va_fiber.geojson")

    # scrape (optional)
    counts = {}
    if "--no-scrape" not in sys.argv:
        try:
            print("== SCRAPE data.virginia.gov fiber-to-premises (tech 50) ==")
            pts = scrape_fiber_points()
            counts = aggregate_to_counties(feats, pts)
            print(f"  {len(pts)} fiber points aggregated to counties")
        except Exception as e:
            print(f"  scrape skipped ({repr(e)[:120]}) — scores still build from hubs/corridors/regions")

    # per-county score
    out = []
    for f in feats:
        p = f["properties"]
        clon, clat, _bb = centroid_bbox(f["geometry"])
        rec = {"fips": p["_fips"], "name": p["_name"]}
        rec.update(score_county(p["_name"], clat, clon, f["geometry"]))
        if counts:
            rec["fiber_premises_funded"] = counts.get(p["_fips"], 0)
        out.append(rec)
    out.sort(key=lambda r: r["fiber_score"], reverse=True)
    json.dump(out, open(DATA / "va_fiber_scores.json", "w"), indent=2)

    t1 = sum(1 for r in out if r["fiber_tier"].startswith("Tier 1"))
    build = sum(1 for r in out if r["dark_fiber_action"] == "build")
    print(f"scores: {len(out)} localities -> data/va_fiber_scores.json "
          f"(Tier1={t1}, build-required={build})")
    print("top 8 fiber:", ", ".join(f"{r['name']}({r['fiber_score']})" for r in out[:8]))


if __name__ == "__main__":
    main()
