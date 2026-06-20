# Virginia Data Center Water Resources — Research Brief

> Scope: How data centers use water, current and projected demand in Virginia,
> where the state's water actually is, and which regions are best suited for
> water-intensive siting. Compiled June 2026 from JLARC (2024), ICPRB (Dec 2025),
> Virginia DEQ, and USGS sources. See **Sources** at the end.

---

## 1. How data centers use water

Nearly all "consumed" water goes to cooling. The cooling method is the single
biggest driver of how much water a site needs.

| Cooling type | On-site water use | Trade-off |
|---|---|---|
| Evaporative / open-loop (most common) | High — a large campus can use ~5M gal/day; ~70–80% of withdrawn water evaporates and is "consumed" | Most energy-efficient |
| Closed-loop / dry / liquid (direct-to-chip, immersion) | Very low — a comparable closed-loop campus ~22,000 gal/day; Microsoft targets near-zero | Uses more electricity |

**Key nuances**
- **Indirect water dominates.** ~75% of a data center's true water footprint is
  off-site, at the power plants generating its electricity. Most "on-site"
  figures exclude this.
- **Demand is a design choice, not a fixed function of size.** JLARC found most
  Virginia data centers use about as much water as an average office building;
  some use substantially more. The difference is almost entirely cooling tech.

---

## 2. Virginia demand picture

Virginia is the world's largest data center market, concentrated in "Data Center
Alley" (Loudoun, Fairfax, Prince William, Fauquier).

- **2.1+ billion gallons** consumed statewide by data centers in 2023.
- **Loudoun County alone: ~900M gallons** in 2023 (~200 facilities).
- Northern Virginia consumption rose **63% from 2019 to 2023**.
- Statewide, data center water is **< 0.5% of total state withdrawals**.
- **~1/3 of data center water statewide is reclaimed/non-potable**, not new
  freshwater.
- JLARC (Dec 2024): use is **currently sustainable but growing and could be
  better managed**.

### ICPRB Washington Metropolitan Area (Potomac) projection

| Metric | 2025 | 2035 | 2050 |
|---|---|---|---|
| Average daily use | 4 MGD | 16 MGD | — |
| Peak daily use | 14.3 MGD | 58 MGD | up to 80 MGD |
| Share of area water | 8% | 25% | up to 33% (unconstrained, ~200 MGD) |

**The real risk is timing, not average supply.** Peak demand lands in summer,
when river flows are lowest and drought is most likely. ICPRB projects a rising
shortage risk: ~1% by 2030, up to ~5% by 2050, when Potomac flow below Little
Falls Dam can't keep up. The Potomac supplies 75% of the region's water for
5M people and is the sole source for Washington, D.C. and Arlington.

---

## 3. Where Virginia's water actually is

Statewide Virginia is wet (40+ inches precipitation/year, riparian "reasonable
use" rights), but availability is highly uneven.

**Surface-water-rich basins — generally better for siting**
- Major basins: James, Potomac, Rappahannock, Roanoke.
- Large reservoirs (Smith Mountain Lake, Lake Gaston) provide storage and
  drought resilience. Reservoir storage is the key feature — it buffers the
  summer low-flow problem.

**Groundwater-stressed coastal zone — generally poor for water-intensive siting**
- Coastal Plain east of I-95 (Hampton Roads, Eastern Shore, Tidewater).
- ~140 MGD current withdrawal; groundwater levels down as much as 200 ft;
  altered gradients create saltwater-intrusion potential.
- DEQ negotiated cuts with the 14 largest users (~80% of permitted withdrawals);
  new confined-aquifer permits must justify need over alternatives.

**Permitting nuance**
- Groundwater permits required only in Groundwater Management Areas (Eastern
  Virginia, Eastern Shore) for withdrawals ≥ 300,000 gal/month.
- Surface-water withdrawals outside a public utility require DEQ permits.

---

## 4. Location suitability

> Suitability depends heavily on cooling design and reclaimed-water use. A
> closed-loop or reclaimed-water facility can work almost anywhere; a large
> evaporative facility narrows the options sharply.

**Strongest water positions**
- **Roanoke basin / Southwest VA** (Botetourt, Roanoke Valley, near Smith
  Mountain Lake / Lake Gaston). Reservoir-backed, away from Potomac peak
  constraints. Google's proposed Botetourt campus is securing capacity via the
  Western Virginia Water Authority — a good model.
- **Southside VA** (Pittsylvania, Mecklenburg, Lake Gaston/Kerr corridor).
  Reservoir-backed, lower existing demand, targeted for economic development.
- **James basin / Central VA** (Richmond, I-95 corridor). Large reliable flows,
  but heaviest growth (290+ centers expected) → needs watershed-level planning.

**Proceed only with water-light cooling or reclaimed water**
- **Northern Virginia / Potomac.** Abundant in normal years, but peak-summer and
  drought-year reliability bind through 2050. New large evaporative loads add the
  most stress.

**Generally avoid for water-intensive facilities**
- **Eastern VA Coastal Plain, Hampton Roads, Eastern Shore.** Aquifer overdraft,
  subsidence, saltwater intrusion, tight permitting.

---

## 5. Practical site-selection takeaways

1. **Cooling tech is the real lever** — closed-loop/dry/liquid can cut on-site
   water ~99% vs evaporative, at the cost of more electricity.
2. **Reclaimed/non-potable water is the proven mitigant** — already >1/3 of VA
   data center water; pair sites with a wastewater utility's reclaimed stream.
3. **Favor reservoir-backed basins** (Roanoke, parts of James, Southside lakes)
   over run-of-river or groundwater supplies — storage solves the peak/drought
   timing problem.
4. **Secure water capacity contractually up front** (cf. Botetourt / Western
   Virginia Water Authority), don't assume municipal headroom.
5. **Account for indirect water** (~75% of footprint, from the power source) when
   comparing locations.

**Caveat — transparency:** No centralized public database tracks withdrawals or
consumption for all VA data centers; reporting isn't standardized; most operators
don't disclose facility-level use. Regional suitability is well understood, but
any single site needs direct utility-level due diligence.

---

## Sources

- JLARC, *Data Centers in Virginia* (Dec 2024) — https://jlarc.virginia.gov/landing-2024-data-centers-in-virginia.asp
- ICPRB, *2025 Washington Metropolitan Area Water Supply Study* (Dec 2025) — https://www.potomacriver.org/wp-content/uploads/2025/12/2025_WMA_Water_Supply_Study_ICPRB_Dec-2025.pdf
- ICPRB, *Data Centers and Water Use in the Potomac River Basin* (Mar 2026) — https://www.potomacriver.org/focus-areas/water-resources-and-drinking-water/water-resources/planning/data-centers-and-water-use-in-the-potomac-river-basin/
- Center for Secure Water (U. Illinois), *Data Center Expansion in Virginia* — https://securewater.illinois.edu/data-center-expansion-in-virginia-closing-critical-gaps-for-informed-water-planning-and-permitting/
- USGS, *Virginia Coastal Plain Aquifer System and Groundwater Resources* — https://www.usgs.gov/centers/virginia-and-west-virginia-water-science-center/science/virginia-coastal-plain-aquifer
- Virginia DEQ, Groundwater Withdrawal Permitting / Coastal Plain Initiative — https://www.deq.virginia.gov/water/water-quantity
- EESI, *Data Centers and Water Consumption* — https://www.eesi.org/articles/view/data-centers-and-water-consumption
- Vantage Data Centers, *Cooling Without the Drain* (closed-loop figures) — https://blog.vantage-dc.com/2026/04/22/cooling-without-the-drain-how-closed-loop-systems-cut-day-to-day-water-use/
- WSLS, *Botetourt County Google data center / water agreements* (Mar 2026) — https://www.wsls.com/news/local/2026/03/12/documents-reveal-key-details-of-proposed-google-data-center-project-in-botetourt-county/

*Figures are point-in-time (mid-2026) and approximate; verify against primary
sources before relying on them for decisions.*
