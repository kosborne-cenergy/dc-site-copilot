# DC Site Copilot — AI Data-Center Siting (Virginia)

**GDG Newport Beach · Google I/O Extended Hackathon · 2026-06-20**

Helps **data-center developers find optimal sites**: screen all of Virginia by
**policy / citizen sentiment / energy** at the county level, then drill to a
**specific parcel** and auto-generate a **due-diligence map exhibit** (wetlands,
flood, topography/slope, tree canopy) with a **fiber · water · energy · land-use** scorecard.

> Full plan + scope + work split: see **[PRD.md](PRD.md)**.

## Multi-model architecture (the constraint)
| Model | Role | Cost |
|---|---|---|
| **Gemma 4** (E4B/12B, local via Ollama) | heavy bulk: classify ordinances, extract | **free** |
| **Gemini 3.5 Flash** (+ Google Search grounding) | live grounded gather (policy, fiber, utility, water) | pay-as-you-go (cheap) |
| **Claude** | orchestration, scorecard synthesis, exhibit narrative | subscription |

## Two levels
1. **Macro screen (county)** — 94/95 VA counties classified by data-center stance + trajectory + buildability; HIFLD transmission overlay. → shortlist favorable counties.
2. **Micro diligence (parcel)** — pick a parcel → pipeline pulls **National Wetland Inventory + FEMA Flood Hazard + USGS 2-ft contours/slope + tree canopy** → renders a **map exhibit** + 4-dimension scorecard.

## Repo layout
```
PRD.md              product requirements + 4-person work split + 3-hr timeline
app/
  counties.json       target counties
  get_geo.py          VA county geometry (census geojson)
  get_transmission.py HIFLD transmission lines (energy layer)
  get_fiber.py        FIBER layer: scrape data.virginia.gov + compile hubs/routes/dark-fiber (see FIBER.md)
  pipeline.py         gather (Gemini) -> classify (Gemma) -> contagion (Claude)
  build_site.py       renders self-contained map + dashboard (Leaflet)
  data/  (gitignored) generated geojson + records
  dist/  (gitignored) built index.html (open or serve)
```
Parcel exhibit pipeline (NWI / FEMA / USGS topo / tree canopy) — **TO BUILD** (see PRD §10, P2). Reuse proven GIS patterns.

## Quickstart
```bash
# 1. local model (free heavy lifting)
ollama pull gemma4:e4b-it-qat

# 2. Gemini key for grounded gather
export GEMINI_API_KEY=...

# 3. run the county pipeline + build the map
cd app
python get_geo.py
python pipeline.py          # gather -> classify -> contagion
python get_transmission.py  # energy layer
python get_fiber.py         # fiber layer (scrape + hubs/routes/dark-fiber, statewide)
python build_site.py        # -> dist/index.html
python -m http.server 8777 --directory dist   # open http://127.0.0.1:8777
```

## Status (start of hackathon)
- ✅ Macro county radar (94/95) + dashboard + transmission overlay
- ✅ Fiber layer (statewide): scraped fiber buildout + hubs/routes/dark-fiber + per-county fiber score (see [FIBER.md](app/FIBER.md))
- ⬜ Parcel selection + exhibit pipeline (NWI/FEMA/USGS/tree) — **the build**
- ⬜ 4-dimension + sentiment scorecard
- ⬜ Web app integration (county ↔ parcel)

_Data is AI-assisted / public-source — demo, not legal or engineering advice; verify against source records._
