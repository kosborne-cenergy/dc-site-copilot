"""
nano_banana.py — Photorealistic aerial render of a PROPOSED DATA CENTER on a parcel.

Uses Google's "Nano Banana" image models (Gemini image generation) via the
`google-genai` SDK to produce a drone-perspective, photorealistic rendering of a
hyperscale data-center campus sited on a rural parcel.

Public API:
    render_datacenter(parcel_summary: dict, out_path="data/parcel_render.png",
                      mw=None, sqft=None) -> {"image_path": str, "model": str}

Requires env var GEMINI_API_KEY.
"""

from __future__ import annotations

import os
from pathlib import Path

from google import genai

# Image models to try, in order of preference.
#   gemini-3-pro-image      = "Nano Banana Pro" (highest quality)
#   gemini-2.5-flash-image  = "Nano Banana" (fast fallback)
_IMAGE_MODELS = ["gemini-3-pro-image", "gemini-2.5-flash-image"]


def _num(d, *keys):
    """First present, numeric value among d[key] for key in keys, else None."""
    if not isinstance(d, dict):
        return None
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _build_constraints_clause(constraints) -> str:
    """Turn the pipeline's constraint/summary dict into a grounding clause so the
    render reflects the REAL site (buildable area, forested buffer, wetlands /
    floodplain, terrain slope, dwelling setback). Returns "" if no usable data.

    Recognized keys (all optional): buildable_acres, parcel_acres, canopy_pct,
    mean_slope_pct, wetlands_present / has_wetlands, flood_present / has_flood /
    in_floodplain, nearest_dwelling_ft.
    """
    if not isinstance(constraints, dict) or not constraints:
        return ""

    buildable = _num(constraints, "buildable_acres", "buildable")
    parcel_ac = _num(constraints, "parcel_acres", "acres", "acreage", "area_acres")
    canopy_pct = _num(constraints, "canopy_pct")
    slope = _num(constraints, "mean_slope_pct")
    dwell_ft = _num(constraints, "nearest_dwelling_ft", "distance_ft")

    # wetlands / flood presence can arrive as booleans, counts, or acreages
    def _present(*keys):
        for k in keys:
            v = constraints.get(k)
            if isinstance(v, bool):
                if v:
                    return True
            elif isinstance(v, (int, float)):
                if v > 0:
                    return True
            elif isinstance(v, str) and v.strip().lower() in ("yes", "true", "present"):
                return True
        return False

    wet = _present("wetlands_present", "has_wetlands", "wetlands_acres", "wetlands")
    flood = _present("flood_present", "has_flood", "in_floodplain", "flood_acres", "flood")

    parts = []
    if buildable is not None:
        parts.append(
            f"site the data-center buildings and parking strictly on the "
            f"~{buildable:g}-acre developable/buildable area"
            + (f" of the {parcel_ac:g}-acre parcel" if parcel_ac is not None else "")
        )
    if canopy_pct is not None and canopy_pct > 0:
        parts.append(
            f"preserve the existing ~{canopy_pct:g}% forested tree canopy as a "
            "vegetated screening buffer, especially the wooded parcel edges"
        )
    if wet and flood:
        parts.append("keep all structures out of the on-site wetlands and FEMA floodplain (leave them as undisturbed open space)")
    elif wet:
        parts.append("keep all structures out of the on-site wetlands (leave them undisturbed)")
    elif flood:
        parts.append("keep all structures out of the FEMA floodplain (leave it as open space)")
    if slope is not None:
        terr = "gently sloping, near-level" if slope < 5 else ("moderately sloping" if slope < 12 else "steeper, rolling")
        parts.append(f"grade the buildings to the {terr} terrain (~{slope:g}% average slope)")
    if dwell_ft is not None and dwell_ft > 0:
        parts.append(f"maintain a vegetated setback from the nearest residential dwelling about {dwell_ft:,.0f} ft away")

    if not parts:
        return ""
    return (
        " Ground the layout in the real site constraints: "
        + "; ".join(parts)
        + "."
    )


def _build_prompt(acres, mw, sqft, constraints=None) -> str:
    """Construct the text prompt describing the proposed data-center site.

    When ``constraints`` (the pipeline summary dict) is supplied, a grounding
    clause is appended so the render reflects the site's actual buildable area,
    forested buffer, wetlands/floodplain, terrain, and dwelling setback.
    """
    acres_txt = f"~{acres:g}" if acres is not None else "several hundred"
    mw_txt = f"{mw:g} MW" if mw is not None else "large-scale"
    sqft_clause = ""
    if sqft is not None:
        sqft_clause = f" with roughly {sqft:,.0f} square feet of building footprint"

    base = (
        "An aerial, photorealistic view of a modern hyperscale data center campus"
        f"{sqft_clause}: several long, low, windowless server buildings with metal "
        "roofs and rooftop cooling units, large surface parking lots, an on-site "
        "electrical substation with transformers and switchgear, perimeter security "
        "fencing, internal access roads, and landscaped setbacks with buffer trees. "
        f"The campus is a {mw_txt} facility sited on a rural Virginia parcel of "
        f"{acres_txt} acres, surrounded by farmland and forest. Bright daytime "
        "lighting, clear sky, high-altitude drone perspective looking down at an "
        "angle. Make it look like a real, professional site rendering / aerial "
        "photograph — crisp detail, realistic shadows, accurate scale."
    )
    return base + _build_constraints_clause(constraints)


def _extract_image_bytes(response):
    """Pull the first inline image payload (bytes, mime_type) out of a response."""
    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                return inline.data, getattr(inline, "mime_type", None)
    return None, None


def _save_as_png(img_bytes: bytes, mime_type, out: Path) -> None:
    """
    Write image bytes to `out` as a genuine PNG.

    Nano Banana Pro often returns JPEG bytes even when we want a .png file, so if
    the payload isn't already PNG we re-encode it via Pillow. Falls back to writing
    raw bytes if Pillow is unavailable.
    """
    is_png = img_bytes[:8] == b"\x89PNG\r\n\x1a\n" or (mime_type == "image/png")
    if is_png:
        out.write_bytes(img_bytes)
        return
    try:
        import io
        from PIL import Image
        Image.open(io.BytesIO(img_bytes)).convert("RGB").save(out, format="PNG")
    except Exception:
        # Pillow missing/failed — write raw bytes so the call still succeeds.
        out.write_bytes(img_bytes)


def render_datacenter(parcel_summary: dict, out_path: str = "data/parcel_render.png",
                      mw=None, sqft=None, constraints=None) -> dict:
    """
    Generate a photorealistic aerial render of a proposed data center on a parcel.

    Args:
        parcel_summary: dict describing the parcel; reads "acres" (also accepts
                        "acreage"/"area_acres"), "mw", and "sqft" if present.
        out_path:       where to write the PNG.
        mw:             megawatt rating (overrides parcel_summary["mw"]).
        sqft:           building square footage (overrides parcel_summary["sqft"]).
        constraints:    OPTIONAL dict of real site constraints (the pipeline
                        summary) -- buildable_acres, parcel_acres, canopy_pct,
                        mean_slope_pct, wetlands/flood presence, nearest_dwelling_ft.
                        When given, the image prompt is GROUNDED in these so the
                        render sites the campus on the buildable area, preserves
                        the forested buffer, avoids wetlands/floodplain, matches
                        the terrain, and respects the dwelling setback. Defaults
                        to ``parcel_summary`` itself if not supplied.

    Returns:
        {"image_path": <abs path to saved PNG>, "model": <model id that worked>}

    Raises:
        RuntimeError if no model returns image bytes (after trying all fallbacks).
    """
    parcel_summary = parcel_summary or {}
    acres = parcel_summary.get("acres",
            parcel_summary.get("acreage",
            parcel_summary.get("area_acres")))
    if mw is None:
        mw = parcel_summary.get("mw")
    if sqft is None:
        sqft = parcel_summary.get("sqft")
    # fall back to the parcel_summary for grounding context if none passed in
    if constraints is None:
        constraints = parcel_summary

    prompt = _build_prompt(acres, mw, sqft, constraints=constraints)

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY env var is not set.")
    client = genai.Client(api_key=api_key)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    errors = []
    for model in _IMAGE_MODELS:
        try:
            response = client.models.generate_content(model=model, contents=prompt)
        except Exception as e:  # API/network/quota error — record and try fallback
            errors.append(f"{model}: {type(e).__name__}: {e}")
            continue

        img_bytes, mime_type = _extract_image_bytes(response)
        if img_bytes:
            _save_as_png(img_bytes, mime_type, out)
            return {"image_path": str(out.resolve()), "model": model}

        errors.append(f"{model}: response contained no inline image data")

    raise RuntimeError(
        "No image returned by any model. Attempts:\n  " + "\n  ".join(errors)
    )


if __name__ == "__main__":
    try:
        result = render_datacenter({"acres": 300}, mw=100)
        path = Path(result["image_path"])
        size = path.stat().st_size if path.exists() else 0
        print(f"image_path: {result['image_path']}")
        print(f"bytes:      {size:,}")
        print(f"model:      {result['model']}")
        if size > 10_000:
            print("OK: real PNG written (size > 10KB).")
        else:
            print("WARNING: file is suspiciously small (<= 10KB).")
    except Exception as e:
        # Surface spend-cap / 429 / quota errors clearly; module stays usable.
        msg = str(e)
        print(f"ERROR: {type(e).__name__}: {msg}")
        if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower() \
                or "spend" in msg.lower():
            print("NOTE: This looks like a rate-limit / spend-cap (429). "
                  "The module is ready; re-run when quota allows.")
