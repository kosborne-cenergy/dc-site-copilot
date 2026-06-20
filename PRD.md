# PRD — SiteSignal: AI Data-Center Siting Copilot (Virginia)

**GDG Newport Beach · Google I/O Extended Hackathon · 2026-06-20 · 4 people · 3-hour build**

---

## 1. One-liner
A web app that helps **data-center developers find optimal sites** — screen all of Virginia by policy/sentiment/energy at the **county** level, then drill to a **specific parcel** and auto-generate a **due-diligence map exhibit** (wetlands, flood, topography/slope, tree canopy) with a **fiber · water · energy · land-use** scorecard.

## 2. Problem & goal
DC developers burn weeks manually screening where to build — chasing zoning, grid, water, fiber, and community sentiment across hundreds of jurisdictions, then doing parcel-level environmental diligence by hand. **Goal:** compress that to minutes — macro screen → parcel pick → instant exhibit + scorecard.

## 3. Target user
Data-center site-selection / development manager. Wants: "show me where I *can* build (welcoming + powered + watered + fibered + buildable land), then prove this parcel works."

## 4. Hard constraints
- **3 hours, 4 people.**
- **Multi-model** functionality required in the web app (Gemini + Gemma + Claude).
- Evaluation across the 4 developer dimensions: **fiber, water, energy, land use.**
- **Citizen sentiment** as an additional evaluation/filter layer.
- Shared **GitHub repo** (separate from Kevin's claude-knowledge), team-collaborative.
- End product: developer finds a **specific parcel** → pipeline downloads **National Wetland Inventory, FEMA Flood Hazard, USGS 2-ft contours + slope, tree-inventory layer** → renders a **map exhibit**.

## 5. The two levels of the product
**A. Macro screen (county) — already built (reuse "DC Policy Radar").**
- 94/95 VA counties classified by data-center stance + trajectory + buildability.
- Sentiment filter (stance/trajectory = citizen/board sentiment proxy).
- Energy macro: HIFLD transmission overlay (already pulled).
- Developer filters VA → shortlist of favorable counties.

**B. Micro diligence (parcel) — the new build.**
- Developer picks a parcel (draw on map / enter address or coords / pick a demo parcel).
- Pipeline auto-pulls 4 environmental layers and renders a **map exhibit**:
  1. **National Wetland Inventory** (USFWS NWI ArcGIS REST)
  2. **FEMA Flood Hazard** (FEMA NFHL ArcGIS REST)
  3. **USGS topography** — 2-ft contours + slope (USGS 3DEP DEM → derive; reuse `/solar-site-topo-canopy`)
  4. **Tree-inventory / canopy** layer (Meta canopy height or NLCD; reuse Cenergy)
- **Parcel scorecard** across the 4 dimensions + sentiment.

## 6. The 4 evaluation dimensions (+ sentiment)
| Dimension | Signal | Data source | Model role |
|---|---|---|---|
| **Energy** | Transmission proximity, voltage, utility | HIFLD transmission (done) + substations | rule-based + Gemini gather |
| **Water** | Cooling-water access, wetlands constraint | NWI + NHD water bodies | rule-based |
| **Fiber** | Long-haul / carrier proximity | FCC broadband / qualitative grounded | Gemini grounded (stretch) |
| **Land use** | Zoning stance, buildable area (flood/wetland/slope) | county radar + FEMA + NWI + slope | Gemma classify + rule-based |
| **Sentiment** (filter) | Board/citizen stance + trajectory | county radar (+ optional concern points) | Gemma classify |

## 7. Scope — MVP (must demo) vs Stretch
**MVP (must have in 3 hrs):**
- Macro map screen (reuse radar) → filter to favorable counties.
- Pick **≥1 demo parcel** in a top county → pipeline pulls **NWI + FEMA + USGS 2ft contour/slope + tree canopy** → **map exhibit** renders.
- Parcel **scorecard**: energy (transmission dist) · water (wetland/water) · land-use (buildable area, flood) · sentiment (county stance); fiber = qualitative.
- **Multi-model visible**: Gemini grounded gather + Gemma classify + Claude synthesis/orchestration.
- Deployed/served + on the shared GitHub repo.

**Stretch (if time):**
- Live parcel search by address (geocode) instead of demo parcels.
- Fiber layer (FCC) as real data.
- Community-concern points (Brockovich-style).
- Auto-rank parcels within a county.
- PDF exhibit export.
- ADK "ask the radar" agent.

## 8. Architecture
```
[ Web app (single page) ]
   Tab 1: Macro screen  -> county choropleth + filters + transmission   (REUSE radar)
   Tab 2: Parcel exhibit -> pick parcel -> exhibit map + scorecard

[ Pipeline / backend (Python) ]
   gather   : Gemini 3.5-flash + Search grounding (fiber/utility/water facts)   [paid, cheap]
   classify : Gemma 4 E4B/12B local via Ollama (stance, extraction)             [FREE]
   synth    : Claude (scorecard reasoning, exhibit narrative, orchestration)    [FREE, subscription]
   GIS      : NWI / FEMA / USGS 3DEP / canopy ArcGIS REST + raster -> exhibit   (REUSE Cenergy GIS)
```
Multi-model = the explicit constraint: **Gemma does the free heavy bulk, Gemini does grounded live data, Claude orchestrates + synthesizes.**

## 9. Data sources (all public, no-key preferred)
- NWI: USFWS National Wetlands Inventory MapServer (ArcGIS REST).
- FEMA flood: FEMA NFHL (National Flood Hazard Layer) MapServer.
- Topo: USGS 3DEP DEM (1m/10m) → 2-ft contours + slope (gdal/ezdxf; reuse solar-site-topo-canopy).
- Tree canopy: Meta DinoV3 canopy height model (reuse) or NLCD Tree Canopy.
- Energy: HIFLD Electric Power Transmission Lines (done) + substations.
- County zoning/sentiment: our radar pipeline (Gemini gather + Gemma classify).

## 10. Work breakdown — 4 people, parallel
- **P1 — Macro + integration (Kevin):** reuse radar; add parcel-handoff; own the pitch/demo narrative.
- **P2 — Parcel GIS exhibit (longest pole):** NWI + FEMA + USGS 2ft topo/slope + tree canopy → exhibit map. REUSE Cenergy GIS scripts. Test endpoints FIRST.
- **P3 — Scorecard + multi-model:** 4-dimension + sentiment scoring; Gemini grounded gather (fiber/water/utility), Gemma classify, Claude synth.
- **P4 — Web app shell + repo + demo:** single-page UI tying Tab 1 ↔ Tab 2; GitHub repo setup + glue; deploy; rehearse demo.

## 11. Timeline (3 hours)
- **0:00–0:20** Repo clone, divide, agree demo parcel(s) + endpoints. P2 smoke-tests NWI/FEMA/USGS now.
- **0:20–2:00** Parallel build.
- **2:00–2:30** Integrate: county → parcel → exhibit → scorecard.
- **2:30–2:50** Polish, demo data, disclaimer, multi-model labels.
- **2:50–3:00** Rehearse 2-min pitch.

## 12. Success criteria (demo)
1. Filter VA to a favorable county (sentiment + energy).
2. Pick a parcel there → **one click** → exhibit with **all 4 layers** renders.
3. Scorecard shows fiber/water/energy/land-use + sentiment verdict.
4. All 3 models visibly used.
5. Live from the shared GitHub repo.

## 13. Judging criteria — how we win each
Scored on: **impact · innovation · execution · use of AI · presentation.**
| Criterion | Our strength | Move to maximize |
|---|---|---|
| **Impact** | DC siting = #1 AI-era infrastructure problem; VA = world's largest DC market; cuts weeks of multi-disciplinary screening to minutes; touches grid, water, community | Open with the stakes + a number; position as a decision tool both developers AND counties need |
| **Innovation** | Policy-**MOTION** + contagion (predictive, not a static map) fused with **macro→micro** (county screen → parcel diligence); cost-smart **multi-model** architecture | Sell "policy is moving, the siting window is migrating out of Data Center Alley"; emphasize the macro→micro pipeline |
| **Execution** | Working county radar (94 counties, real grounded data, polished UI) + transmission overlay | **Finish ONE parcel end-to-end** (exhibit + scorecard). Working-and-narrow beats broad-and-broken — this is the gap |
| **Use of AI** | Three models, each for its edge: Gemma 4 local (free heavy classify) + Gemini 3.5 grounded (live data) + Claude (synthesis/orchestration) | Make it **visible** in the demo — narrate each model's job; optional live "ask the radar" agent for a wow moment |
| **Presentation** | Strong narrative + a real, current insight | Tight **2-min**: impact → live demo (county→parcel→exhibit) → AI architecture → scale vision. Rehearse. Slide with the 3-model diagram |

**Priority for the 3 hours (by score-per-hour):** 1) Execution — finish the parcel exhibit. 2) Use-of-AI visible. 3) Presentation script + slide. Impact/Innovation = framing, cheap.

## 14. Risks
- **Parcel GIS is the long pole** → reuse Cenergy scripts; pre-pick 1–2 demo parcels; P2 starts at minute 0.
- **Fiber data is sparse** → qualitative/grounded, labeled.
- **NWI/FEMA/USGS endpoints + reprojection** fiddly → smoke-test in first 20 min; have a cached fallback exhibit.
- **3 hrs is tight** → MVP is one parcel done end-to-end; breadth (many parcels) is stretch.
- Data is AI-assisted/demo — label "not legal/engineering advice; verify."

## 15. Naming
Working name **SiteSignal** (alt: GridSite, ParcelScout, OptiSite). Team's call.
