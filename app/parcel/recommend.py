"""
Data-center developer toolkit: sizing -> county recommendation -> parcel suitability.

Pure-Python, zero external deps, no LLM calls. All heuristics documented inline so
a developer can see exactly what drives every number.

Three public functions
-----------------------
1. acres_needed(mw=None, sqft=None)            -> {acres_needed, basis}
2. recommend_counties(mw, sqft, budget)        -> ranked [{name, fips, site_score, components, why}, ...]
3. suitable_parcels(parcels_geojson, min_acres)-> {"type": FeatureCollection, ...}  (filtered) + count

Run directly for a smoke test:  python parcel/recommend.py
"""

from __future__ import annotations

import json
import os
from typing import Optional

# --------------------------------------------------------------------------------------
# Data location
# --------------------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = os.path.join(os.path.dirname(_HERE), "data")  # app/data


def _load(name: str):
    """Load a JSON file from app/data. Returns None if it is missing."""
    path = os.path.join(_DATA, name)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ======================================================================================
# 1. SIZING  -- how many acres does a data center of size X need?
# ======================================================================================
#
# Industry rules of thumb (documented so they can be tuned):
#   * Building footprint        : gross building sqft / 43,560 sqft-per-acre.
#   * Full campus multiplier    : a hyperscale campus is parking + substation yard +
#                                 setbacks + stormwater ponds + security buffer + room
#                                 to phase additional halls. Net-to-gross site ratio for
#                                 a single-story data hall typically lands ~3-4x the
#                                 building footprint. We use 3.5x as the midpoint.
#   * MW -> acres (when no sqft) : a modern data hall draws roughly 150-250 W/sqft of IT
#                                 + cooling, so ~1 MW occupies on the order of a few
#                                 thousand sqft of building. Translated to land, the
#                                 commonly cited planning figure is ~1.5-2.5 acres per MW
#                                 of campus (building + all the campus support above).
#                                 We use 2.0 acres/MW as the midpoint and ALSO apply the
#                                 campus multiplier framing for transparency.
#
ACRES_PER_SQFT = 1.0 / 43560.0
CAMPUS_MULTIPLIER = 3.5          # full campus / building footprint  (range 3-4x)
ACRES_PER_MW_LOW = 1.5
ACRES_PER_MW_HIGH = 2.5
ACRES_PER_MW_MID = 2.0          # used when only MW is supplied


def acres_needed(mw: Optional[float] = None, sqft: Optional[float] = None) -> dict:
    """Estimate total campus acres for a data center.

    Provide building gross square footage (`sqft`) and/or capacity (`mw`).
    If both are given, the larger of the two estimates is returned (conservative
    for land-banking). If neither is given, raises ValueError.

    Returns
    -------
    {"acres_needed": float, "basis": str}
    """
    if mw is None and sqft is None:
        raise ValueError("Provide at least one of mw= or sqft=")

    estimates = []
    notes = []

    if sqft is not None:
        building_ac = sqft * ACRES_PER_SQFT
        campus_ac = building_ac * CAMPUS_MULTIPLIER
        estimates.append(campus_ac)
        notes.append(
            f"{sqft:,.0f} sqft building = {building_ac:,.1f} ac footprint x "
            f"{CAMPUS_MULTIPLIER} campus multiplier = {campus_ac:,.1f} ac"
        )

    if mw is not None:
        # Primary MW heuristic: direct acres/MW planning figure.
        campus_ac = mw * ACRES_PER_MW_MID
        estimates.append(campus_ac)
        notes.append(
            f"{mw:g} MW x {ACRES_PER_MW_MID} ac/MW (range "
            f"{ACRES_PER_MW_LOW}-{ACRES_PER_MW_HIGH} ac/MW, building + full campus) "
            f"= {campus_ac:,.1f} ac"
        )

    acres = round(max(estimates), 1)

    # Provide a low/high band for context when MW drives the estimate.
    if mw is not None and sqft is None:
        notes.append(
            f"plausible range {mw * ACRES_PER_MW_LOW:,.1f}-{mw * ACRES_PER_MW_HIGH:,.1f} ac"
        )

    return {"acres_needed": acres, "basis": "; ".join(notes)}


# ======================================================================================
# 2. COUNTY RECOMMENDATION  -- rank VA counties by composite SITE score
# ======================================================================================
#
# Component scores (each normalized to 0-100):
#   buildability : derived from the county's data-center posture in records.json
#                  (stance + zoning path + trajectory). No raw numeric score exists
#                  in records.json, so we map the qualitative posture to 0-100.
#   water        : va_water_scores.json  (surface-water availability; cooling supply)
#   fiber        : va_fiber_scores.json  (dark fiber / backbone proximity; latency)
#   energy       : transmission/substation proximity -- OPTIONAL. No pre-scored
#                  per-county energy file ships today, so this component is omitted
#                  and its weight is redistributed across the others (graceful
#                  degradation). Hook left in place: drop an energy score into the
#                  ENERGY_SCORES dict (by FIPS) and it is picked up automatically.
#   sentiment    : favorable-posture score, also derived from stance/trajectory --
#                  rewards "positive + loosening" and penalizes "restrictive +
#                  tightening". Captures political/permitting tailwind separate from
#                  the structural buildability read.
#
# Weights (sum to 1.0 over the components that are PRESENT for a given county):
WEIGHTS = {
    "buildability": 0.30,
    "water": 0.20,
    "fiber": 0.20,
    "energy": 0.15,
    "sentiment": 0.15,
}

# Optional per-FIPS energy scores. Empty today -> energy component is skipped and its
# weight is redistributed. Populate later (e.g. from transmission proximity) to enable.
ENERGY_SCORES: dict[str, float] = {}

# --- qualitative -> numeric maps -------------------------------------------------------
_STANCE_PTS = {"positive": 100, "neutral": 60, "restrictive": 25}
_ZONING_PTS = {"by-right": 100, "administrative": 75, "special-use": 45, "prohibited": 0}
_TRAJ_PTS = {"loosening": 100, "stable": 60, "tightening": 25}


def _buildability_score(rec: dict) -> float:
    """Composite of stance + zoning path + trajectory -> 0-100.

    Weighted 50% stance / 30% zoning path / 20% trajectory.
    """
    s = _STANCE_PTS.get((rec.get("stance") or "").lower(), 50)
    z = _ZONING_PTS.get((rec.get("zoning_path") or "").lower(), 50)
    t = _TRAJ_PTS.get((rec.get("trajectory") or "").lower(), 50)
    return round(0.50 * s + 0.30 * z + 0.20 * t, 1)


def _sentiment_score(rec: dict) -> float:
    """Political/permitting tailwind -> 0-100 (stance + trajectory only)."""
    s = _STANCE_PTS.get((rec.get("stance") or "").lower(), 50)
    t = _TRAJ_PTS.get((rec.get("trajectory") or "").lower(), 50)
    return round(0.60 * s + 0.40 * t, 1)


def _index_by_fips(rows, score_key):
    out = {}
    if not rows:
        return out
    for row in rows:
        fips = str(row.get("fips", "")).strip()
        if fips:
            out[fips] = row.get(score_key)
    return out


def recommend_counties(mw=None, sqft=None, budget=None, top_n: int = 8) -> list:
    """Rank VA counties by a composite SITE score for siting a data center.

    Parameters
    ----------
    mw, sqft : optional sizing inputs. Used to compute the acreage requirement that
               flows into the `why` narrative (does the county's posture support a
               campus of this size). They do NOT currently re-weight the score --
               the score reflects intrinsic county fitness.
    budget   : optional. Reserved for future land-cost screening; not used yet
               (no per-county land-price data ships). Accepted so callers don't break.
    top_n    : how many counties to return (default 8).

    Returns
    -------
    list of dicts, best first:
        {name, fips, site_score, components: {...}, why: "..."}
    """
    records = _load("records.json") or []
    water_by_fips = _index_by_fips(_load("va_water_scores.json"), "water_score")
    fiber_by_fips = _index_by_fips(_load("va_fiber_scores.json"), "fiber_score")

    # Acreage requirement (for the narrative only); tolerate no sizing inputs.
    try:
        need = acres_needed(mw=mw, sqft=sqft)["acres_needed"] if (mw or sqft) else None
    except ValueError:
        need = None

    ranked = []
    for rec in records:
        fips = str(rec.get("fips", "")).strip()
        name = rec.get("name", "?")

        comp = {
            "buildability": _buildability_score(rec),
            "water": _num(water_by_fips.get(fips)),
            "fiber": _num(fiber_by_fips.get(fips)),
            "energy": _num(ENERGY_SCORES.get(fips)),
            "sentiment": _sentiment_score(rec),
        }

        # Weighted blend over ONLY the components that are present (graceful degrade).
        num = 0.0
        wsum = 0.0
        for key, val in comp.items():
            if val is None:
                continue
            w = WEIGHTS[key]
            num += w * val
            wsum += w
        site_score = round(num / wsum, 1) if wsum else 0.0

        ranked.append(
            {
                "name": name,
                "fips": fips,
                "site_score": site_score,
                "components": {k: v for k, v in comp.items()},
                "why": _why(name, rec, comp, site_score, need),
            }
        )

    ranked.sort(key=lambda r: r["site_score"], reverse=True)
    return ranked[:top_n]


def _num(v):
    """Coerce to float or return None (so missing components are skipped cleanly)."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _why(name, rec, comp, site_score, need) -> str:
    bits = [f"SITE {site_score}/100"]
    sc = []
    for label, key in (("build", "buildability"), ("water", "water"),
                       ("fiber", "fiber"), ("energy", "energy"), ("sent", "sentiment")):
        v = comp.get(key)
        sc.append(f"{label} {v:.0f}" if v is not None else f"{label} n/a")
    bits.append("[" + ", ".join(sc) + "]")
    bits.append(f"stance={rec.get('stance')}/{rec.get('zoning_path')}, trajectory={rec.get('trajectory')}")
    kl = rec.get("key_limits")
    if kl and kl.lower() not in ("none", "none noted", ""):
        bits.append(f"limits: {kl}")
    if need is not None:
        bits.append(f"(needs ~{need:,.0f} ac for this size)")
    return " | ".join(bits)


# ======================================================================================
# 3. PARCEL SUITABILITY  -- filter a parcel FeatureCollection by minimum acreage
# ======================================================================================
def suitable_parcels(parcels_geojson, min_acres: float) -> dict:
    """Filter parcels to those with `acreage` >= min_acres.

    Parameters
    ----------
    parcels_geojson : a GeoJSON FeatureCollection dict, OR a path to one on disk.
    min_acres       : minimum parcel size to keep.

    Returns
    -------
    A FeatureCollection of the surviving parcels, with an added top-level
    "count" and the min_acres used:
        {"type": "FeatureCollection", "features": [...], "count": N, "min_acres": X}
    Sorted largest-acreage first.
    """
    if isinstance(parcels_geojson, str):
        with open(parcels_geojson, "r", encoding="utf-8") as fh:
            parcels_geojson = json.load(fh)

    features = parcels_geojson.get("features", []) if parcels_geojson else []

    kept = []
    for feat in features:
        props = (feat or {}).get("properties", {}) or {}
        ac = _num(props.get("acreage"))
        if ac is not None and ac >= min_acres:
            kept.append(feat)

    kept.sort(key=lambda f: _num((f.get("properties") or {}).get("acreage")) or 0.0,
              reverse=True)

    return {
        "type": "FeatureCollection",
        "features": kept,
        "count": len(kept),
        "min_acres": min_acres,
    }


# ======================================================================================
# Smoke test
# ======================================================================================
if __name__ == "__main__":
    print("=" * 78)
    print("1) acres_needed(mw=100)")
    res = acres_needed(mw=100)
    print(f"   acres_needed = {res['acres_needed']}")
    print(f"   basis        = {res['basis']}")
    print(f"   [check] acres_needed(sqft=250000) -> {acres_needed(sqft=250000)['acres_needed']} ac")
    print(f"   [check] acres_needed(mw=100, sqft=250000) -> {acres_needed(mw=100, sqft=250000)['acres_needed']} ac")

    print("=" * 78)
    print("2) recommend_counties(100, None, None) -> top 8")
    top = recommend_counties(100, None, None, top_n=8)
    for i, c in enumerate(top, 1):
        print(f"   {i}. {c['name']:<16} {c['site_score']:>5}/100  {c['why']}")

    print("=" * 78)
    print("3) suitable_parcels(parcel_sample.geojson, min_acres=50)")
    sample_path = os.path.join(_DATA, "parcel_sample.geojson")
    with open(sample_path, "r", encoding="utf-8") as fh:
        gj = json.load(fh)
    total = len(gj.get("features", []))
    out = suitable_parcels(gj, min_acres=50)
    print(f"   total parcels      = {total}")
    print(f"   parcels >= 50 ac   = {out['count']}")
    if out["features"]:
        biggest = out["features"][0]["properties"]
        print(f"   largest kept       = {biggest.get('acreage')} ac "
              f"({biggest.get('locality')}, parcel {biggest.get('parcel_id')})")
