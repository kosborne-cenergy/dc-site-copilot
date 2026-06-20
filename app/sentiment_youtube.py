#!/usr/bin/env python3
"""
Public-sentiment layer from YouTube — per VA county, over the last ~12 months.
  GATHER   : YouTube Data API search (recent DC videos/meetings/news) + free transcripts
  CLASSIFY : Gemma 4 (local, FREE) -> per-county public sentiment toward data centers

Cost: YouTube search = free Google quota (~100 searches/day); transcripts free; Gemma free.
Resumable: caches per-county raw in data/yt/<fips>.json; stops gracefully on quota error.

    python sentiment_youtube.py            # gather + classify all counties
    python sentiment_youtube.py --limit 3  # quick test
"""
import os, json, sys, time, urllib.request, urllib.parse, pathlib

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"
YT = DATA / "yt"; YT.mkdir(parents=True, exist_ok=True)

YT_KEY = os.environ.get("YOUTUBE_API_KEY") or os.environ["GEMINI_API_KEY"]
PUBLISHED_AFTER = "2025-06-20T00:00:00Z"   # ~12 months
MAX_PER_COUNTY = 5
GEMMA = "gemma4:e4b-it-qat"
OLLAMA = "http://localhost:11434/api/generate"
TRANSCRIPT_CAP = 2500  # chars per video fed to Gemma

_src = ROOT / "counties_all.json"
COUNTIES = json.load(open(_src))["counties"] if _src.exists() else json.load(open(ROOT / "counties.json"))["counties"]
if "--limit" in sys.argv:
    COUNTIES = COUNTIES[: int(sys.argv[sys.argv.index("--limit") + 1])]


class QuotaError(Exception):
    pass


def yt_search(query):
    params = {"part": "snippet", "q": query, "type": "video", "maxResults": MAX_PER_COUNTY,
              "order": "relevance", "publishedAfter": PUBLISHED_AFTER,
              "relevanceLanguage": "en", "key": YT_KEY}
    url = "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            return json.load(r).get("items", [])
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        if e.code == 403 and "quota" in body.lower():
            raise QuotaError(body[:200])
        raise


def transcript(vid):
    try:
        from youtube_transcript_api import YouTubeTranscriptApi as Y
        try:
            segs = Y().fetch(vid)
            return " ".join(s.text for s in segs)
        except Exception:
            segs = Y.get_transcript(vid)
            return " ".join(s["text"] for s in segs)
    except Exception:
        return ""


def gemma(prompt):
    body = json.dumps({"model": GEMMA, "prompt": prompt, "stream": False, "format": "json",
                       "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 700}}).encode()
    req = urllib.request.Request(OLLAMA, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())["response"]


def parse_json(s):
    """Lenient: direct parse, else extract first {...} block."""
    try:
        return json.loads(s)
    except Exception:
        a, b = s.find("{"), s.rfind("}")
        if a >= 0 and b > a:
            return json.loads(s[a:b + 1])
        raise


# ---------- stage 1: gather ----------
def gather():
    for i, c in enumerate(COUNTIES, 1):
        f = YT / f"{c['fips']}.json"
        if f.exists():
            print(f"  [skip] ({i}/{len(COUNTIES)}) {c['name']}")
            continue
        try:
            items = yt_search(f"{c['name']} County Virginia data center")
        except QuotaError as q:
            print(f"  !! QUOTA EXHAUSTED at {c['name']} — stopping gather (resume later). {q}")
            return False
        vids = []
        for it in items:
            vid = it["id"].get("videoId")
            if not vid:
                continue
            sn = it["snippet"]
            tx = transcript(vid)
            vids.append({"id": vid, "title": sn["title"], "date": sn["publishedAt"][:10],
                         "channel": sn.get("channelTitle", ""), "transcript": tx[:TRANSCRIPT_CAP],
                         "has_transcript": bool(tx)})
        json.dump({**c, "videos": vids}, open(f, "w", encoding="utf-8"), indent=2)
        nt = sum(1 for v in vids if v["has_transcript"])
        print(f"  [ok]  ({i}/{len(COUNTIES)}) {c['name']:16} {len(vids)} vids, {nt} w/transcript")
        time.sleep(0.1)
    return True


# ---------- stage 2: classify (Gemma) ----------
CLASSIFY = """You analyze PUBLIC SENTIMENT toward data centers in {county} County, Virginia, from recent
YouTube videos (board meetings, local news, town halls). Below are video titles + transcript excerpts.
Return ONLY JSON:
  public_sentiment: one of "strongly_oppose","oppose","mixed","support","neutral","none"
  intensity: 0 to 1 (how heated/engaged the public is; 0 if no signal)
  top_concerns: array of short tags (e.g. "noise","water","power/grid","traffic","property values","tax revenue","environment")
  summary: one sentence on the public mood
  n_signal: integer count of videos that actually discuss data-center sentiment
Base it ONLY on the material. If nothing relevant, public_sentiment="none", intensity=0.

VIDEOS for {county} County:
{videos}"""


def classify():
    out = []
    files = sorted(YT.glob("*.json"))
    for i, f in enumerate(files, 1):
        d = json.load(open(f, encoding="utf-8"))
        vids = d.get("videos", [])
        if not vids:
            out.append({"name": d["name"], "fips": d["fips"], "public_sentiment": "none",
                        "intensity": 0, "top_concerns": [], "n_videos": 0, "summary": "No videos found."})
            continue
        blob = "\n\n".join(f"[{v['date']}] {v['title']}\n{v['transcript'][:1500]}" for v in vids)
        try:
            rec = parse_json(gemma(CLASSIFY.format(county=d["name"], videos=blob)))
        except Exception as e:
            print(f"  [ERR] {d['name']}: {repr(e)[:120]}")
            rec = {"public_sentiment": "unclear", "intensity": 0, "top_concerns": [], "summary": "parse failed"}
        rec.update({"name": d["name"], "fips": d["fips"], "n_videos": len(vids),
                    "sample_titles": [v["title"] for v in vids[:3]]})
        out.append(rec)
        print(f"  [ok]  ({i}/{len(files)}) {d['name']:16} {rec.get('public_sentiment'):16} int={rec.get('intensity')} n={len(vids)}")
    json.dump(out, open(DATA / "sentiment.json", "w", encoding="utf-8"), indent=2)
    print(f"  -> wrote {len(out)} sentiment records")


if __name__ == "__main__":
    stage = sys.argv[2] if (len(sys.argv) > 2 and sys.argv[1] == "--stage") else "all"
    if stage in ("all", "gather"):
        print("== GATHER (YouTube search + transcripts) =="); gather()
    if stage in ("all", "classify"):
        print("== CLASSIFY (Gemma 4 local) =="); classify()
    print("done.")
