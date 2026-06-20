#!/usr/bin/env python3
"""
Public-sentiment layer from YouTube — per VA county, LAST 6 MONTHS.
Captures BOTH local news AND board-meeting speeches (one broad query returns both).

Stages (run separately so we never bulk-hammer YouTube):
  search      : YouTube Data API search per county (free quota) -> data/yt/<fips>.json
  transcripts : POLITE one-at-a-time transcript fetch (5s spacing + backoff on IpBlocked,
                resumable per-video) -> data/transcripts/<vid>.json   [avoids throttle]
  classify    : Gemma 4 local (FREE) -> per-county public sentiment -> data/sentiment.json

    python sentiment_youtube.py --stage search
    python sentiment_youtube.py --stage transcripts   # slow; run in background
    python sentiment_youtube.py --stage classify
"""
import os, json, sys, time, urllib.request, urllib.parse, pathlib

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"
YT = DATA / "yt"; YT.mkdir(parents=True, exist_ok=True)
TX = DATA / "transcripts"; TX.mkdir(parents=True, exist_ok=True)

YT_KEY = os.environ.get("YOUTUBE_API_KEY") or os.environ["GEMINI_API_KEY"]
SINCE_6MO = "2025-12-20T00:00:00Z"      # last 6 months (today = 2026-06-20)
SINCE_DATE = "2025-12-20"
MAXRES = 15
GEMMA = "gemma4:e4b-it-qat"
OLLAMA = "http://localhost:11434/api/generate"
TX_DELAY = 5.0          # seconds between transcript fetches (politeness)
TX_BACKOFF = 45         # base seconds to wait when Ip-blocked

_src = ROOT / "counties_all.json"
COUNTIES = json.load(open(_src))["counties"] if _src.exists() else json.load(open(ROOT / "counties.json"))["counties"]
if "--limit" in sys.argv:
    COUNTIES = COUNTIES[: int(sys.argv[sys.argv.index("--limit") + 1])]


# ---------------- search ----------------
class QuotaError(Exception):
    pass


def yt_search(query):
    params = {"part": "snippet", "q": query, "type": "video", "maxResults": MAXRES,
              "order": "relevance", "publishedAfter": SINCE_6MO, "relevanceLanguage": "en", "key": YT_KEY}
    url = "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.load(r).get("items", [])
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if e.code == 403 and "quota" in body.lower():
            raise QuotaError(body[:160])
        raise


def stage_search():
    for i, c in enumerate(COUNTIES, 1):
        f = YT / f"{c['fips']}.json"
        if f.exists():
            print(f"  [skip] ({i}/{len(COUNTIES)}) {c['name']}"); continue
        try:
            items = yt_search(f"{c['name']} County Virginia data center")
        except QuotaError as q:
            print(f"  !! QUOTA at {c['name']} — stop, resume later. {q}"); return
        vids = []
        for it in items:
            vid = it["id"].get("videoId")
            if not vid:
                continue
            sn = it["snippet"]
            vids.append({"id": vid, "title": sn["title"], "date": sn["publishedAt"][:10],
                         "channel": sn.get("channelTitle", "")})
        json.dump({**c, "videos": vids}, open(f, "w", encoding="utf-8"), indent=2)
        print(f"  [ok]  ({i}/{len(COUNTIES)}) {c['name']:16} {len(vids)} videos")


# ---------------- transcripts (polite) ----------------
def all_videos_6mo():
    """Unique videos across counties, kept to last 6 months."""
    seen, out = set(), []
    for c in COUNTIES:
        f = YT / f"{c['fips']}.json"
        if not f.exists():
            continue
        for v in json.load(open(f, encoding="utf-8")).get("videos", []):
            if v["date"] < SINCE_DATE:
                continue
            if v["id"] in seen:
                continue
            seen.add(v["id"]); out.append(v)
    return out


def fetch_one(vid):
    """Returns (text, status). status in ok|none|blocked. Backs off on IpBlocked."""
    from youtube_transcript_api import YouTubeTranscriptApi as Y
    for attempt in range(5):
        try:
            segs = Y().fetch(vid)
            return " ".join(s.text for s in segs), "ok"
        except Exception as e:
            name = type(e).__name__
            if "IpBlocked" in name or "TooManyRequests" in name:
                wait = TX_BACKOFF * (attempt + 1)
                print(f"      ...blocked, backoff {wait}s")
                time.sleep(wait); continue
            return "", "none"   # captions disabled / unavailable
    return "", "blocked"


def stage_transcripts():
    vids = all_videos_6mo()
    todo = [v for v in vids if not (TX / f"{v['id']}.json").exists()]
    print(f"  {len(vids)} unique 6-mo videos; {len(todo)} to fetch (rest cached)")
    ok = none = blocked = 0
    for i, v in enumerate(todo, 1):
        text, status = fetch_one(v["id"])
        json.dump({"id": v["id"], "title": v["title"], "date": v["date"],
                   "text": text[:6000], "status": status},
                  open(TX / f"{v['id']}.json", "w", encoding="utf-8"))
        ok += status == "ok"; none += status == "none"; blocked += status == "blocked"
        mark = {"ok": "OK", "none": "--", "blocked": "XX"}[status]
        print(f"  [{mark}] ({i}/{len(todo)}) {len(text):5}c  {v['title'][:55]}")
        time.sleep(TX_DELAY)
    print(f"  done: ok={ok} no-captions={none} still-blocked={blocked}")


# ---------------- classify (Gemma) ----------------
def gemma(prompt):
    body = json.dumps({"model": GEMMA, "prompt": prompt, "stream": False, "format": "json",
                       "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 700}}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())["response"]


def parse_json(s):
    try:
        return json.loads(s)
    except Exception:
        a, b = s.find("{"), s.rfind("}")
        if a >= 0 and b > a:
            return json.loads(s[a:b + 1])
        raise


CLASSIFY = """Analyze PUBLIC SENTIMENT toward data centers in {county} County, Virginia, from recent
(last 6 months) local YouTube — both news coverage and board-meeting speeches. Material below.
Return ONLY JSON:
  public_sentiment: "strongly_oppose","oppose","mixed","support","neutral", or "none"
  intensity: 0 to 1 (public engagement/heat; 0 if no signal)
  top_concerns: array of short tags (noise, water, power/grid, traffic, property values, tax revenue, environment, transparency)
  drivers: array of short phrases of what residents/officials actually said
  summary: one sentence on the public mood
  n_signal: integer count of items that actually discuss data-center sentiment
Base ONLY on the material. If nothing relevant, public_sentiment="none", intensity=0.

MATERIAL for {county} County:
{material}"""


def stage_classify():
    # index transcripts by id
    txi = {}
    for p in TX.glob("*.json"):
        d = json.load(open(p, encoding="utf-8"))
        if d.get("status") == "ok" and d.get("text"):
            txi[d["id"]] = d["text"]
    out = []
    for i, c in enumerate(COUNTIES, 1):
        f = YT / f"{c['fips']}.json"
        if not f.exists():
            continue
        vids = [v for v in json.load(open(f, encoding="utf-8")).get("videos", []) if v["date"] >= SINCE_DATE]
        if not vids:
            out.append({"name": c["name"], "fips": c["fips"], "public_sentiment": "none",
                        "intensity": 0, "top_concerns": [], "n_videos": 0, "n_transcripts": 0,
                        "summary": "No recent videos."}); continue
        parts, nt = [], 0
        for v in vids:
            kind = "MEETING" if any(k in v["title"].lower() for k in ("board", "supervisor", "meeting", "hearing", "commission", "town hall")) else "NEWS"
            t = txi.get(v["id"], "")
            if t:
                nt += 1
            parts.append(f"[{v['date']}|{kind}] {v['title']}\n{t[:1800]}")
        material = "\n\n".join(parts)
        try:
            rec = parse_json(gemma(CLASSIFY.format(county=c["name"], material=material)))
        except Exception as e:
            print(f"  [ERR] {c['name']}: {repr(e)[:100]}")
            rec = {"public_sentiment": "unclear", "intensity": 0, "top_concerns": [], "summary": "parse failed"}
        rec.update({"name": c["name"], "fips": c["fips"], "n_videos": len(vids), "n_transcripts": nt,
                    "sample_titles": [v["title"] for v in vids[:3]]})
        out.append(rec)
        print(f"  [ok] ({i}/{len(COUNTIES)}) {c['name']:16} {rec.get('public_sentiment'):16} int={rec.get('intensity')} vids={len(vids)} tx={nt}")
    json.dump(out, open(DATA / "sentiment.json", "w", encoding="utf-8"), indent=2)
    print(f"  -> wrote {len(out)} sentiment records")


if __name__ == "__main__":
    stage = sys.argv[2] if (len(sys.argv) > 2 and sys.argv[1] == "--stage") else "all"
    if stage in ("all", "search"):
        print("== SEARCH (YouTube, 6mo) =="); stage_search()
    if stage in ("all", "transcripts"):
        print("== TRANSCRIPTS (polite, slow) =="); stage_transcripts()
    if stage in ("all", "classify"):
        print("== CLASSIFY (Gemma 4 local) =="); stage_classify()
    print("done.")
