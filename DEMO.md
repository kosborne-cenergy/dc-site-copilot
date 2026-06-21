# Demo Script + Pitch — DC Site Copilot

**Judged on: Impact · Innovation · Execution · Use of AI · Presentation. This script is built to hit HIGH on each.**

---

## The one-liner (memorize)
> "AI is eating the grid. Virginia is the world's #1 data-center market — and every county is rewriting its rules. **DC Site Copilot** screens all 95 counties across five siting dimensions and tells a developer where they can actually build — in minutes, not weeks."

## 2-minute demo (beat by beat)

**1. Hook — IMPACT (15s)**
> "Data centers are the biggest infrastructure fight in America right now. Virginia is ground zero. A developer trying to site one burns *weeks* chasing zoning, grid, water, fiber, and community sentiment across 95 counties. We compress that to minutes."

**2. What it is — INNOVATION (15s)**
> "DC Site Copilot. We classified **every** Virginia county across five dimensions — policy, public sentiment, energy, fiber, and water — and we don't just show a snapshot, we show where policy is *moving*."

**3. Live demo — EXECUTION (60s)** — share the running app
- **Stance map:** "54% of counties are tightening. The restriction wave is spreading out of Data Center Alley — and the siting window is *migrating* to rural Southwest and Southside Virginia." *(the insight)*
- **Toggle the modes:** Trajectory → Public sentiment (red opposition clusters in NoVA) → ⚡ Transmission → 🔌 Fiber. "One map, five dimensions, all public data."
- **Developer Dashboard tab:** "Ranked buildability — Botetourt, Pulaski, Wise score 95: welcoming, by-right, loosening, wired." Click a county → evidence (policy action, public concerns, fiber score).

**4. The AI — USE OF AI (20s)** — this is our edge, say it explicitly
> "AI is the engine, not a sprinkle. **Three models, each for its strength:** Gemma 4 runs *locally* for the heavy classification — free, no rate limits, on a laptop GPU. Gemini 3.5 grounds every county against live sources with Google Search. Claude synthesizes the contagion analysis and the ranking. That split is also how we kept it nearly free."

**5. Close — PRESENTATION (10s)**
> "From 'where do I even start' to a ranked, evidence-backed shortlist — in minutes. That's DC Site Copilot."

---

## Architecture slide (draw this)
```
   PUBLIC DATA                  AI ENGINE (multi-model)            OUTPUT
 ┌──────────────┐    ┌─────────────────────────────────┐   ┌──────────────┐
 │ Ordinances   │    │ GEMINI 3.5  grounded gather      │   │  Statewide   │
 │ YouTube      │──► │ GEMMA 4     local classify (free)│──►│  map (5 dims)│
 │ HIFLD grid   │    │ CLAUDE      synth + contagion +  │   │  + Developer │
 │ data.va.gov  │    │             orchestration        │   │  buildability│
 │ NWI/FEMA/USGS│    └─────────────────────────────────┘   │  leaderboard │
 └──────────────┘                                           └──────────────┘
  5 DIMENSIONS:  policy/land-use · public sentiment · energy · fiber · water
```

## How we hit HIGH on each criterion
- **Impact** — concrete user (DC developer), real market (VA #1), tangible value (weeks→minutes, $-stakes). Open with it.
- **Innovation** — *motion not snapshot* (trajectory + contagion), macro→micro, 5-dimension fusion incl. YouTube sentiment, cost-smart multi-model. Not "a map."
- **Execution** — working unified prototype, all public data, clean modular repo, smooth tab/toggle flow. Rehearse the click-through; no dead clicks.
- **Use of AI** — AI is *core* (every layer is model-driven); 3 models each for its edge; say "engine, not sprinkle."
- **Presentation** — this script + the architecture slide + the one insight ("window migrating out of Data Center Alley"). Tight, confident, ≤2 min.

## Risks to avoid (the "low score" column)
- Don't let any click be broken/dead → test the demo path first.
- Don't bury the AI → narrate the 3-model split out loud.
- Don't over-explain → one insight, one flow, one close.
- Label data AI-assisted / public-source (credibility, not a hedge).
