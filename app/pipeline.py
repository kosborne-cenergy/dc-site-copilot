"""
DC Policy Radar (VA) — data pipeline. 3 engines, clear division of labor:
  GATHER    Gemini 3.5-flash + Google Search grounding -> fresh, cited per-county facts
  CLASSIFY  Gemma 4 E4B (local, Ollama, FREE) -> structured stance record  [the heavy bulk]
  CONTAGION Gemini 3.1-pro -> whole-corpus cross-county trajectory + contagion + siting windows

Writes: data/raw/<fips>.json (gather), data/records.json (classify), data/contagion.json.
Resumable: skips counties already gathered. All inputs PUBLIC. Output labeled AI-generated (demo).

    python pipeline.py            # run all stages
    python pipeline.py --stage gather|classify|contagion
"""
import os, json, time, sys, urllib.request, pathlib
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"
RAW = DATA / "raw"
RAW.mkdir(parents=True, exist_ok=True)

GEMINI_GATHER = "gemini-3.5-flash"
GEMINI_CONTAGION = "gemini-3.1-pro-preview"
GEMMA = "gemma4:e4b-it-qat"
OLLAMA = "http://localhost:11434/api/generate"
GATHER_WORKERS = 6  # concurrent Gemini grounded calls

# Full statewide: all 95 VA counties (counties_all.json, built by get_geo.py).
# Falls back to the 15-county sample if the full list isn't built yet.
_src = ROOT / "counties_all.json"
if _src.exists():
    COUNTIES = json.load(open(_src))["counties"]
else:
    COUNTIES = json.load(open(ROOT / "counties.json"))["counties"]
# optional cap for quick tests: python pipeline.py --stage gather --limit 5
if "--limit" in sys.argv:
    COUNTIES = COUNTIES[: int(sys.argv[sys.argv.index("--limit") + 1])]

# ---------------- Gemini ----------------
from google import genai
from google.genai import types
_gem = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


def gemini(model, prompt, grounding=False, as_json=False):
    cfg = {"temperature": 0}
    if grounding:
        cfg["tools"] = [types.Tool(google_search=types.GoogleSearch())]
    if as_json:
        cfg["response_mime_type"] = "application/json"
    r = _gem.models.generate_content(model=model, contents=prompt,
                                     config=types.GenerateContentConfig(**cfg))
    return r.text


# ---------------- Gemma (local) ----------------
def gemma(prompt, as_json=True):
    body = json.dumps({"model": GEMMA, "prompt": prompt, "stream": False,
                       "format": "json" if as_json else "", "options": {"temperature": 0}}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())["response"]


# ---------------- Stage 1: GATHER ----------------
GATHER_PROMPT = """You are a land-use policy researcher. Using current, real sources, summarize the CURRENT
data-center (hyperscale/colocation) ZONING posture of {county} County, Virginia.

Report factually, with dates and source names where possible:
- Zoning process for a new data center: by-right, OR special-use/special-exception (SUP/SPEX/CUP), OR prohibited.
- Any ordinance amendment, overlay, performance standards, or MORATORIUM adopted or proposed (give the date/year).
- Key limits if any: setbacks, building height, noise (dBA at property line), acreage/size caps, water/power conditions.
- DIRECTION of recent policy change over the last ~2 years: loosening, stable, or tightening — and why.
Keep it under 200 words. State plainly if a county has little/no data-center-specific regulation."""


def _gather_one(c):
    f = RAW / f"{c['fips']}.json"
    if f.exists():
        return ("skip", c["name"], 0)
    t0 = time.time()
    try:
        txt = gemini(GEMINI_GATHER, GATHER_PROMPT.format(county=c["name"]), grounding=True)
        json.dump({**c, "facts": txt}, open(f, "w", encoding="utf-8"), indent=2)
        return ("ok", c["name"], time.time() - t0)
    except Exception as e:
        return ("ERR", f"{c['name']}: {repr(e)[:140]}", time.time() - t0)


def stage_gather():
    done = 0
    with ThreadPoolExecutor(max_workers=GATHER_WORKERS) as ex:
        futs = {ex.submit(_gather_one, c): c for c in COUNTIES}
        for fut in as_completed(futs):
            status, msg, dt = fut.result()
            done += 1
            print(f"  [{status:4}] ({done}/{len(COUNTIES)}) {msg} {dt:.1f}s")


# ---------------- Stage 2: CLASSIFY (Gemma) ----------------
CLASSIFY_PROMPT = """You are classifying a Virginia county's data-center zoning posture from this research summary.
Return ONLY JSON with keys:
  stance: one of "positive","neutral","restrictive","moratorium"
  zoning_path: one of "by-right","special-use","prohibited","unclear"
  trajectory: one of "loosening","stable","tightening"
  key_limits: short string of the main limits (or "none noted")
  recent_action: short string of the latest policy action (or "none noted")
  recent_action_year: 4-digit year or ""
  summary: one sentence
  confidence: number 0 to 1

RESEARCH SUMMARY for {county} County:
{facts}"""


def stage_classify():
    records = []
    for c in COUNTIES:
        f = RAW / f"{c['fips']}.json"
        if not f.exists():
            print(f"  [miss] {c['name']} (gather first)")
            continue
        raw = json.load(open(f, encoding="utf-8"))
        try:
            t0 = time.time()
            out = gemma(CLASSIFY_PROMPT.format(county=c["name"], facts=raw["facts"]))
            rec = json.loads(out)
            rec.update({"name": c["name"], "fips": c["fips"], "region": c.get("region", "")})
            records.append(rec)
            print(f"  [ok]  {c['name']:16} {rec.get('stance'):11} {rec.get('trajectory'):10} {time.time()-t0:.1f}s")
        except Exception as e:
            print(f"  [ERR] {c['name']}: {repr(e)[:160]}")
    json.dump(records, open(DATA / "records.json", "w", encoding="utf-8"), indent=2)
    print(f"  -> wrote {len(records)} records")


# ---------------- Stage 3: CONTAGION (Gemini pro) ----------------
CONTAGION_PROMPT = """You are a regional policy strategist. Below are data-center zoning records for {n} Virginia
counties (stance, trajectory, recent actions). Analyze the STATEWIDE PICTURE and return ONLY JSON:
  statewide_trend: 2-3 sentence summary of where VA data-center policy is moving overall.
  contagion: array of {{pattern: str, counties: [names], evidence: str}} — restrictions/moratoria spreading
             between neighboring or peer counties; who appears to be following whom.
  next_likely: array of {{county: str, prediction: str}} — counties most likely to tighten or adopt a
             moratorium next, with a one-line reason grounded in the data.
  siting_windows: {{opening: [county names where the window favors siting now], closing: [county names tightening]}}
Base every claim ONLY on the records provided. Be concrete.

RECORDS:
{records}"""


def stage_contagion():
    records = json.load(open(DATA / "records.json", encoding="utf-8"))
    slim = [{k: r.get(k) for k in ("name", "region", "stance", "zoning_path", "trajectory",
                                   "recent_action", "recent_action_year")} for r in records]
    out = gemini(GEMINI_CONTAGION, CONTAGION_PROMPT.format(n=len(records), records=json.dumps(slim, indent=2)),
                 as_json=True)
    obj = json.loads(out)
    json.dump(obj, open(DATA / "contagion.json", "w", encoding="utf-8"), indent=2)
    print(f"  -> contagion: {len(obj.get('contagion',[]))} patterns, {len(obj.get('next_likely',[]))} predictions")


if __name__ == "__main__":
    stage = sys.argv[2] if (len(sys.argv) > 2 and sys.argv[1] == "--stage") else "all"
    if stage in ("all", "gather"):
        print("== GATHER (Gemini 3.5-flash + grounding) =="); stage_gather()
    if stage in ("all", "classify"):
        print("== CLASSIFY (Gemma 4 E4B local) =="); stage_classify()
    if stage in ("all", "contagion"):
        print("== CONTAGION (Gemini 3.1-pro) =="); stage_contagion()
    print("done.")
