# Dominion SCC Data-Center Grid Intelligence (Virginia)

**Energy-dimension intelligence for DC Site Copilot — distilled from Dominion's own regulatory filings.**

> **The one-line thesis the demo can sell:** Virginia's data-center power demand is ~70 GW in the queue — *nearly triple* the entire Dominion peak — and the buildable window is **migrating out of Data Center Alley into Southside / Central Virginia**, where Dominion is *actively building* new 230/500 kV data-center substations. This brief is the primary-source evidence for that story.

---

## What this is & how it was built

- **Source corpus:** 68,000 Dominion Energy Virginia (VEPCO) filings at the Virginia State Corporation Commission (SCC), 1989→2026 — applications, testimony, staff reports, orders.
- **Method:** a local 1.1M-chunk RAG (mxbai-embed-large 1024-d + BM25 hybrid + cross-encoder rerank) queried with 16 data-center-siting questions, then hand-synthesized with a **case number + document URL on every fact**.
- **Local + free:** the RAG runs on-device (LanceDB + Ollama). Teammates can't re-query it, so the machine-readable layer is **pre-computed → [`app/scc_dc_grid.json`](../app/scc_dc_grid.json)**.
- **Confidence:** every MW / date / dollar traces to a primary SCC filing but is tagged **`likely`** until re-verified against the cited document. Demo intelligence — not engineering or legal advice.

---

## 1. The demand wave (why DC siting is *the* infrastructure problem)

| Metric | Value | Source |
|---|---|---|
| Dominion system peak by 2039 | **> 45,000 MW** (from ~18,400 MW today) | PUR-2025-00058 (witness Green) |
| Data-center load, Dec 2024 | **~3,600 MW = ~20%** of peak | PUR-2025-00058 (Sierra Club) |
| Share of *net* load growth from DCs | **100%** (non-DC load flat-to-declining to ~2030) | PUR-2025-00058 (PEC) |
| Large-load requests in the queue | **~70,000 MW** ≈ *nearly 3× DOM Zone peak* | PUR-2026-00011 (Staff) |
| — assigned connection dates through 2031 | ~25,000 MW | PUR-2026-00011 |
| — still under study (as of Dec 31 2025) | ~45,000 MW | PUR-2026-00011 |
| Contracted: signed ESAs / CLOA / ELOA | **9,000 / 6,000 / 26,000 MW** | PUR-2025-00058 |
| Largest single campus request | **3,596 MW = twelve 300 MW substations** | PUR-2026-00011 (PEC) |

System load factor climbs 63% → 72% by 2039 because DC load runs at a 92-93% load factor — i.e., the grid is growing from the **baseload up**, not from peaking. The siting question isn't "is there demand" — it's **"where can Dominion physically deliver the power."**

---

## 2. The migration map — NoVA is closing, Southside/Central is opening

This is the heart of the energy dimension. Every load area below is keyed by county in [`app/scc_dc_grid.json`](../app/scc_dc_grid.json) so the app can color the energy score per county.

### 🟥 SATURATED — Data Center Alley (avoid for *new* large load)
- **Loudoun** — ~181 data centers in ~30 sq mi. Power-constrained since 2022; Board **ended "by-right" DC development March 18, 2025**. Relief needs a whole new 500 kV loop: **Aspen-Golden** (target COD **June 1, 2028**), **Mars-Wishing Star**, **Golden-Mars**. Power, not land, is the binding constraint. *(PUR-2025-00056, PUR-2024-00032)*
- **Prince William** — ~30M sq ft approved ≈ **4.5 GW**, but the lines feeding it route through contested Loudoun corridors. High delivery risk. *(PUR-2025-00056)*

### 🟩 BUILDING — Dominion is actively energizing new DC substations here (the whitespace)
- **Mecklenburg — South Hill / Boydton (Southside).** **600 MW** across three campuses: **Tunstall** (180 MW), **Evans Creek** (300 MW), **Raines** (120 MW) + Nebula Switching Station; co-op MEC adds 221 MW by 2035. The southern "Data Center Alley." *(PUR-2022-00167, PUR-2025-00014)*
- **Henrico — White Oak Technology Park (WOTP).** Established node (Meta + QTS on existing Portugee/White Oak subs) expanding **+600 MW**: **Bunker Substation** (300 MW) + **Saltwood Switching Station** (300 MW) on the new Technology Boulevard 230 kV project. *(PUR-2025-00042, PUR-2023-00110)*
- **Chesterfield — Western Chesterfield (Upper Magnolia Green).** Load **approaching 1 GW by 2033**; new **Duval / Garnet / Topaz / Amethyst** 230 kV substations; Duval-Midlothian 230 kV lines energize **June 2028**. Existing 34.5 kV subs can't serve it — all-new 230 kV build. *(PUR-2025-00073)*
- **Stafford — Centreport (I-95).** **262 MW** DC on the new **Centreport 230 kV** loop + substation. Exurban-NoVA with more land than Loudoun. *(PUR-2024-00170)*
- **Charlotte / Halifax / Mecklenburg — Southside transmission belt.** New **Butler Farm** 230 kV substation + **Finneywood 500/230 kV** switching station + Butler Farm–Clover / –Finneywood 230 kV lines, built for a DC customer, tying into the 500 kV backbone. The long-run frontier. *(PUR-2022-00175)*

### 🟨 EMERGING — early runway
- **Goochland — West Creek.** New **West Creek 230 kV** loop + substation (37 MW + future), relieving Rockville/North Pole/Short Pump/River Road as they fill 2028-2030. Just west of Short Pump/Richmond. *(PUR-2026-00009)*

---

## 3. The cost & contract regime a VA data center signs into (GS-5)

Effective **Jan 1, 2027**, Dominion's new **GS-5** rate class governs DC economics — material to any siting pro-forma:

- **Who:** ≥ 25 MW contracted demand **and** ≥ 75% load factor (≈ data centers); 139 accounts today.
- **Term:** **14-year** contract.
- **Minimum demand charges:** **85%** of contracted demand (T&D) + **60%** (generation) — you pay for the capacity whether you use it or not.
- **Exit:** allowed after 5 years (ex-ramp), but **exit fee = 36 months of minimum charges**.
- **Collateral:** 50% of total minimum charges for the full term if below credit thresholds.
- **Load study fee:** one-time **$10k–$100k**.
- **Why:** protect ratepayers from stranded cost + cross-subsidization; discourage speculative overbuild.

*(PUR-2025-00058 witnesses Baine/Wishart; Amazon/joint stipulation; PUR-2026-00011 PEC)*

**Large-load queue process** (PUR-2026-00011): applies to DP requests ≥ ~100 MW in the DOM Zone, **each DP capped at 300 MW**; four stages (Initiation → Feasibility → Development → Execution); the EDC files through the Delivery Point Exchange (DPE); **executed-ESA projects get priority** (firmer than speculative queue entries).

---

## 4. The faster-power play (flexibility / BYOC) — the innovation angle

The newest lever a DC developer has when the grid is full:

- **Flexible / phased connection + bring-your-own-capacity (BYOC)** lets a DC **connect 3-5 years faster** and avoids **$78M + 273 MW of capacity buildout per GW** of DC demand (Emerald AI / Princeton ZERO Lab, PUR-2026-00011).
- **Conditional firm service** — accept curtailment during system-stress windows in exchange for early power.
- **Static ramped connection** — start at reduced load within existing limits, scale to nameplate after upgrades (Sierra Club, PUR-2025-00058).

This pairs directly with the app's "energy" + "land use" scoring: a constrained county can still be viable for a *flexible* DC.

---

## 5. How to wire it into the app

`app/scc_dc_grid.json` is structured for direct consumption:
- `statewide_demand`, `large_load_queue`, `rate_regime_gs5`, `flexibility_fast_track` → headline stats for the dashboard / pitch.
- `load_areas[]` → keyed by `county` with `tier` (saturated / building / emerging), `dc_load_mw`, named substations, transmission, `dc_takeaway`, and `cases[]` + `url`.

**Energy-dimension scoring suggestion:** map `tier` → score (building = high, emerging = medium, saturated = low-for-new-load) and surface the `dc_takeaway` + a clickable SCC `url` in the county popup. This turns the energy score from "nearest substation" into "is Dominion *actually building* DC capacity here, per its own filings."

---

## Source index (primary SCC cases)

| Case | Topic |
|---|---|
| PUR-2025-00058 | 2025 Biennial Review — DC load forecast, GS-5 rate class, flexibility |
| PUR-2026-00011 | Large-load interconnection queue process (70 GW, 300 MW DP cap) |
| PUR-2025-00056 | Golden-Mars 500-230 kV — DCA relief loop; Loudoun saturation testimony |
| PUR-2024-00032 | Aspen-Golden 500-230 kV (DCA loop, COD June 2028) |
| PUR-2022-00183 | Wishing Star / Mars 500-230 kV — NoVA load-growth violations |
| PUR-2022-00167 | Mecklenburg South Hill — Tunstall/Evans Creek/Raines (600 MW) |
| PUR-2025-00014 | Mecklenburg Boydton/South Hill — Nebula 230 kV |
| PUR-2025-00042 | Henrico WOTP — Bunker + Saltwood (600 MW) |
| PUR-2023-00110 | Henrico White Oak 230 kV (Meta/QTS area) |
| PUR-2025-00073 | Western Chesterfield — Duval/Garnet/Topaz/Amethyst (~1 GW by 2033) |
| PUR-2024-00170 | Stafford Centreport 230 kV (262 MW) |
| PUR-2026-00009 | Goochland West Creek 230 kV |
| PUR-2022-00175 | Southside Butler Farm / Finneywood 500-230 kV |
| PUR-2025-00057 | APCo large-load tariff — 88 DC-driven transmission projects (~$2.4B) |

_Generated 2026-06-20 from the local Dominion SCC RAG. Facts tagged `likely` pending re-verification against the cited filing. Demo intelligence, not engineering advice._
