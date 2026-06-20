# Fiber dimension — Virginia (statewide)

The **Fiber** leg of the 4-dimension scorecard (fiber · water · energy · land use).
Built by [`get_fiber.py`](get_fiber.py) → `data/va_fiber.geojson` (map overlay) +
`data/va_fiber_scores.json` (per-locality score), surfaced in the map as the
**🔌 Fiber** overlay and in every locality's detail panel.

> All public-source. Carrier strand geometry is proprietary; what's public is
> (a) *which* carriers run a named corridor, (b) the cities/ROW it follows, and
> (c) hub/landing facts. AI-assisted demo — verify before use.

## What's scraped vs. compiled

| Part | Source | How |
|---|---|---|
| **Fiber-to-premises buildout** (per-county count) | `data.virginia.gov` BEAD Final Proposal Awarded Locations (resource `891d9440…`), technology code 50 | **Scraped live** (65,536 fiber points), point-in-polygon aggregated to all 134 localities |
| **Interconnection hubs** | Equinix/PeeringDB, submarinenetworks.com, MARIA, MBC | Compiled (10 hubs) — carrier-hotel / subsea-landing / colocation / R&E |
| **Long-haul corridors** | Carrier route maps (Lumen/Zayo/Crown Castle/Windstream), MBC, InterTubes study | Compiled (8 corridors) as approximate polylines through named cities |
| **Dark-fiber regions** | MBC, SummitIG, FiberLight, Zayo, RVBA/nDanville, state broadband reports | Compiled → per-region rating (high → desert) + lease/build action |

## Scoring (transparent, proximity-based)

`fiber_score (0–100) = dark-fiber region base + nearest-hub bonus + on-corridor bonus`,
clamped. A hub or corridor waypoint *inside* a county counts as distance 0 (so big
counties like Loudoun aren't penalized for a distant centroid).

- region base: high 55 · medium-high 45 · medium 35 · low-medium 22 · low 12 · desert 5
- hub bonus: ≤15 km +30 · ≤40 +20 · ≤80 +10, × tier weight (T1 1.0 / T2 0.7 / T3 0.5)
- corridor bonus: ≤5 km +15 · ≤20 +8
- tiers: ≥70 fiber-rich · ≥50 well-connected · ≥30 thin · else build-required

## 1. City hubs (the interconnection points)

| Hub | Type | Tier | Why it matters |
|---|---|---|---|
| **Ashburn / Data Center Alley** (Equinix, LINX NoVA) | carrier hotel | 1 | Densest interconnection on the US East Coast; descends from MAE-East. 350+ networks. |
| **Virginia Beach** (MAREA / BRUSA / Dunant, Globalinx) | subsea landing | 1 | Only major mid-Atlantic transatlantic gateway; subsea capacity backhauled to Ashburn. |
| **Richmond QTS NAP** (White Oak) | carrier hotel | 1 | Inland hinge where VA Beach subsea routes aggregate before Ashburn. 17+ carriers, cloud on-ramps. |
| **Culpeper** (Equinix) | carrier hotel | 2 | Diverse-route relay on the Richmond↔NoVA path; carrier-neutral since 2008. |
| **Norfolk** (Globalinx) | colocation | 2 | Extends the VA Beach subsea ecosystem into Hampton Roads. |
| **Manassas / Prince William** | DC campus | 2 | Fast-growing NoVA cluster (PW Digital Gateway); SummitIG / Tenebris dark fiber. |
| **Danville** (nDanville) | open-access | 2 | First US municipal open-access net; cheap leasable dark fiber, MBC on-net. |
| **Roanoke Valley (RVBA) / Botetourt (Google)** | open-access | 3 | RVBA open-access dark fiber; Google campus pulling new long-haul into SW VA. |
| **Blacksburg / Virginia Tech** (MARIA 100G) | research net | 3 | Anchor of VA's 100G research/education backbone (Internet2). |
| **Charlottesville / UVA** (MARIA) | research net | 3 | Central-VA R&E node; on the high-traffic US-29 conduit. |

## 2. Routes it goes through (long-haul corridors)

`strategic` = subsea spine · `dark` = open-access/dark fiber · `backbone` = national long-haul.

| Corridor | Kind | Path | Carriers |
|---|---|---|---|
| **VA Beach ↔ Richmond ↔ Ashburn subsea spine** | strategic | VA Beach → Richmond → Culpeper → Ashburn | Globalinx/Lumos, Windstream/MBC "Beach Route" |
| **I-95 spine** | backbone | Alexandria → Fredericksburg → Richmond → Petersburg → Emporia → NC | Lumen, Zayo, Windstream/Uniti, Crown Castle, FiberLight |
| **I-81 Shenandoah** | backbone | Winchester → Harrisonburg → Staunton → Roanoke → Wytheville → Bristol | Osprey/VDOT, Lumen, Shentel, Segra/Lumos |
| **I-64 East (subsea)** | strategic | Richmond → Williamsburg → Newport News → Norfolk → VA Beach | Telxius, Globalinx, FiberLight/MFN, Cox, Verizon |
| **I-64 West** | backbone | Richmond → Charlottesville → Staunton → Covington → WV | Zayo (Columbus↔Ashburn via WV), Segra, Lumen |
| **I-66** | backbone | Arlington → Manassas → Front Royal → Strasburg | Osprey/VDOT, Lumen, Zayo, Verizon |
| **US-29** | backbone | Gainesville → Culpeper → Charlottesville → Lynchburg → Danville → NC | MBC, Segra, Lumen, Zayo (NS rail ROW) |
| **MBC Southside open-access** | dark | Martinsville → Danville → South Boston → Emporia | Mid-Atlantic Broadband Communities Corp |

The **US-29 Lynchburg↔Charlottesville** segment is the single highest-traffic long-haul
conduit documented in Virginia (InterTubes study). Note: up to ~19 ISPs can share one
conduit, so reward **physical-path diversity**, not carrier count.

## 3. Locations to do dark fiber

**Lease today (fast siting):** NoVA (Loudoun/Fairfax/Prince William — saturated,
multi-provider), Hampton Roads (subsea-adjacent), Richmond/Henrico (QTS corridor),
**Southside via MBC** (Halifax, Mecklenburg, Pittsylvania, Danville — the value play:
cheap open-access dark fiber + cheap power/land), Roanoke Valley (RVBA), Danville (nDanville).

**Build / extend (gaps near otherwise-good sites):** the **I-81 / Shenandoah Valley**
(power + land, thin fiber — the likely next NoVA-overflow corridor), **Southwest VA**
(sparse post-BVU), and the **Eastern Shore** (Accomack/Northampton — desert, middle-mile only).

| Region | Rating | Action |
|---|---|---|
| Northern Virginia | high | lease |
| Hampton Roads | high | lease |
| Richmond metro | medium-high | lease |
| Southside (MBC) | medium | lease |
| Roanoke Valley | medium | lease |
| Shenandoah / I-81 | low-medium | build |
| Southwest VA | low | build |
| Eastern Shore | desert | build |

## Run it

```bash
cd app
python get_geo.py        # county polygons (prereq)
python get_fiber.py      # scrape + compile + score  (--no-scrape to skip live pull)
python build_site.py     # bakes the 🔌 Fiber overlay + per-county fiber score
```

## Sources
Equinix / PeeringDB · submarinenetworks.com (VA Beach CLS, MAREA/BRUSA/Dunant) ·
Mid-Atlantic Broadband (mbc-va.com) · Windstream Wholesale "Beach Route" · Zayo /
Lumen / Crown Castle / FiberLight / SummitIG route maps · MARIA (marialliance.net) ·
RVBA / nDanville · InterTubes (Durairajan et al., SIGCOMM 2015) · data.virginia.gov
(BEAD / FCC BDC). Endpoints that exist but were *not* usable for statewide route
geometry: HIFLD communications (public portal closed Aug 2025), InterTubes geometry
(gated behind IMPACT), VGIN ArcGIS (no fiber layer), MBC map (no open REST endpoint).
