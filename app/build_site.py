"""Build the self-contained DC Policy Radar (dist/index.html): Map tab + Developer Dashboard tab.
Bakes VA geojson + classified records + contagion. Computes a transparent buildability score in Python.
No server needed — open the HTML."""
import json, pathlib, datetime, shutil

ROOT = pathlib.Path(__file__).parent
DATA = ROOT / "data"
DIST = ROOT / "dist"; DIST.mkdir(exist_ok=True)

geo = json.load(open(DATA / "va_geo.geojson"))
records = json.load(open(DATA / "records.json", encoding="utf-8"))
try:
    contagion = json.load(open(DATA / "contagion.json", encoding="utf-8"))
except FileNotFoundError:
    contagion = {}
try:
    fiber = {r["fips"]: r for r in json.load(open(DATA / "va_fiber_scores.json", encoding="utf-8"))}
except FileNotFoundError:
    fiber = {}

# ---------- transparent buildability score (permitting dimension) ----------
STANCE_BASE = {"positive": 50, "neutral": 35, "restrictive": 15, "moratorium": 0}
PATH_ADJ = {"by-right": 25, "special-use": 10, "unclear": 5, "prohibited": -25}
TRAJ_ADJ = {"loosening": 20, "stable": 8, "tightening": -12}


def score_one(r):
    s = STANCE_BASE.get(r.get("stance"), 25)
    s += PATH_ADJ.get(r.get("zoning_path"), 0)
    s += TRAJ_ADJ.get(r.get("trajectory"), 0)
    s = max(0, min(100, s))
    stance, traj = r.get("stance"), r.get("trajectory")
    if stance == "moratorium":
        tier = "Avoid"
    elif s >= 72:
        tier = "Tier 1 — Build now"
    elif s >= 52:
        tier = "Tier 2 — Workable"
    elif s >= 32:
        tier = "Tier 3 — Hard"
    else:
        tier = "Avoid"
    window = ""
    if stance in ("positive", "neutral") and traj == "tightening":
        window = "closing"   # favorable now but tightening -> move fast
    elif stance == "positive" and traj == "loosening":
        window = "open"
    # one-line developer rationale
    bits = []
    bits.append({"by-right": "by-right (fast approval)", "special-use": "special-use permit needed",
                 "prohibited": "effectively prohibited", "unclear": "process unclear"}.get(r.get("zoning_path"), ""))
    bits.append({"loosening": "policy loosening", "tightening": "policy tightening", "stable": "policy stable"}.get(traj, ""))
    why = "; ".join(b for b in bits if b)
    r["score"], r["tier"], r["window"], r["why"] = s, tier, window, why
    return r


for r in records:
    score_one(r)
records.sort(key=lambda r: r["score"], reverse=True)

built = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
n = len(records)
t1 = sum(1 for r in records if r["tier"].startswith("Tier 1"))
avoid = sum(1 for r in records if r["tier"] == "Avoid")
closing = sum(1 for r in records if r["window"] == "closing")

HTML = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DC Policy Radar — Virginia</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
 :root{--pos:#2e7d32;--neu:#9e9e9e;--res:#ef6c00;--mor:#c62828;--ink:#10243e;--blue:#1f6feb;}
 *{box-sizing:border-box} html,body{margin:0;height:100%;font-family:Arial,Helvetica,sans-serif;color:var(--ink)}
 header{background:var(--ink);color:#fff;padding:9px 18px;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
 header h1{font-size:19px;margin:0} header .sub{opacity:.82;font-size:12px}
 .tabs{margin-left:auto;display:flex;gap:6px}
 .tab{background:#24405f;color:#cfe0f5;border:1px solid #3a5a80;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px;font-weight:bold}
 .tab.on{background:var(--blue);color:#fff;border-color:var(--blue)}
 .view{display:none} .view.on{display:block}
 /* map view */
 #mapwrap{display:grid;grid-template-columns:1fr 370px;height:calc(100vh - 92px)}
 #map{height:100%} aside{border-left:1px solid #e3e3e3;overflow:auto;padding:14px;background:#fafbfc}
 .modes{display:flex;gap:6px;margin-bottom:8px}
 .mode{background:#eef2f7;border:1px solid #d7dee8;padding:4px 10px;border-radius:6px;cursor:pointer;font-size:12px}
 .mode.on{background:var(--blue);color:#fff;border-color:var(--blue)}
 .legend{display:flex;gap:10px;flex-wrap:wrap;font-size:12px;margin:4px 0 10px}
 .legend i{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:4px;vertical-align:-1px}
 h2{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:#5a6b80;border-bottom:1px solid #e3e3e3;padding-bottom:5px;margin:16px 0 8px}
 .county-name{font-size:18px;font-weight:bold;margin:0}
 .kv{font-size:13px;margin:4px 0} .kv b{color:#5a6b80;font-weight:600}
 .tag{font-size:11px;font-weight:bold;padding:2px 8px;border-radius:4px;color:#fff}
 .card{background:#fff;border:1px solid #e6e6e6;border-radius:8px;padding:10px;margin:8px 0;font-size:13px}
 .card .t{font-weight:bold;margin-bottom:3px} .muted{color:#7a899c;font-size:12px}
 /* dashboard view */
 #dash{padding:18px 24px;height:calc(100vh - 92px);overflow:auto;background:#f7f9fc}
 .tiles{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:16px}
 .tile{background:#fff;border:1px solid #e4e9f0;border-radius:10px;padding:14px 18px;min-width:150px}
 .tile .num{font-size:30px;font-weight:bold} .tile .lbl{font-size:12px;color:#6a7888;text-transform:uppercase;letter-spacing:.03em}
 table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #e4e9f0;border-radius:10px;overflow:hidden;font-size:13px}
 th,td{padding:8px 11px;text-align:left;border-bottom:1px solid #eef1f5} th{background:#eef3fa;color:#46586e;font-size:11px;text-transform:uppercase;letter-spacing:.03em;cursor:pointer}
 tr:hover td{background:#f4f8ff} .rank{font-weight:bold;color:#8a98ab;width:34px}
 .score{font-weight:bold} .bar{height:7px;border-radius:4px;background:#e6ebf2;margin-top:3px;overflow:hidden} .bar>span{display:block;height:100%}
 .tierp{font-size:11px;font-weight:bold;padding:2px 7px;border-radius:4px;white-space:nowrap}
 .flag{font-size:11px;font-weight:bold;color:var(--mor)}
 footer{background:#eef0f3;font-size:11px;color:#6a7888;padding:7px 18px;border-top:1px solid #e0e0e0}
 .hint{color:#7a899c;font-style:italic;font-size:13px}
</style></head><body>
<header>
  <h1>📡 DC Policy Radar — Virginia</h1>
  <span class="sub">Where data-center policy is <b>moving</b> — all 95 counties, classified by AI from public ordinances</span>
  <div class="tabs">
    <span class="tab on" data-v="map" onclick="showView('map')">🗺 Map</span>
    <span class="tab" data-v="dash" onclick="showView('dash')">📊 Developer Dashboard</span>
  </div>
</header>

<div id="view-map" class="view on">
 <div id="mapwrap">
  <div id="map"></div>
  <aside>
   <div class="modes"><span class="mode on" data-mode="stance" onclick="setMode('stance')">Stance</span>
     <span class="mode" data-mode="trajectory" onclick="setMode('trajectory')">Trajectory</span>
     <span class="mode" data-mode="score" onclick="setMode('score')">Buildability</span>
     <span class="mode" id="txbtn" onclick="toggleTx()">⚡ Transmission</span>
     <span class="mode" id="fibtn" onclick="toggleFiber()">🔌 Fiber</span></div>
   <div class="legend" id="legend"></div>
   <div id="trend"></div>
   <div id="detail"><p class="hint">Click a county to see its data-center stance, trajectory, and the policy action behind it.</p></div>
   <div id="contagion"></div>
  </aside>
 </div>
</div>

<div id="view-dash" class="view">
 <div id="dash">
  <div class="tiles">
   <div class="tile"><div class="num" style="color:var(--pos)">__T1__</div><div class="lbl">Tier 1 — Build now</div></div>
   <div class="tile"><div class="num" style="color:var(--mor)">__AVOID__</div><div class="lbl">Avoid (moratorium/hard)</div></div>
   <div class="tile"><div class="num" style="color:var(--res)">__CLOSING__</div><div class="lbl">⏳ Window closing</div></div>
   <div class="tile"><div class="num">__N__</div><div class="lbl">Counties scored</div></div>
  </div>
  <p class="muted" style="margin:-6px 0 12px">Developer view — ranked by <b>permitting buildability</b> (stance + zoning path + policy trajectory). Toggle ⚡ Transmission + 🔌 Fiber overlays on the map; click any locality for its fiber score. AI-generated; verify against the source ordinance.</p>
  <table id="rank"><thead><tr><th>#</th><th>County</th><th>Buildability</th><th>Tier</th><th>Stance</th><th>Path</th><th>Trajectory</th><th>Why / flag</th></tr></thead><tbody id="rankbody"></tbody></table>
 </div>
</div>
<footer id="foot"></footer>

<script>
const GEO=__GEO__, REC=__REC__, CON=__CON__, FIB=__FIB__, BUILT="__BUILT__";
const byFips={}; REC.forEach(r=>byFips[r.fips]=r);
const STANCE={positive:'#2e7d32',neutral:'#9e9e9e',restrictive:'#ef6c00',moratorium:'#c62828'};
const TRAJ={loosening:'#2e7d32',stable:'#cfd6df',tightening:'#c62828'};
const SLABEL={positive:'Positive',neutral:'Neutral',restrictive:'Restrictive',moratorium:'Moratorium'};
const TIERC={'Tier 1 — Build now':'#2e7d32','Tier 2 — Workable':'#1f6feb','Tier 3 — Hard':'#ef6c00','Avoid':'#c62828'};
function scoreColor(s){return s>=72?'#2e7d32':s>=52?'#1f6feb':s>=32?'#ef6c00':'#c62828';}
let mode='stance', map, layer;

function showView(v){document.querySelectorAll('.view').forEach(e=>e.classList.remove('on'));
  document.getElementById('view-'+v).classList.add('on');
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('on',t.dataset.v===v));
  if(v==='map'&&map) setTimeout(()=>map.invalidateSize(),60);}

// ---- map ----
map=L.map('map').setView([37.6,-78.9],7);
L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',{maxZoom:12,attribution:'&copy; OpenStreetMap, &copy; CARTO'}).addTo(map);
function colorFor(fips){const r=byFips[fips]; if(!r) return '#e8e8e8';
  if(mode==='stance') return STANCE[r.stance]||'#e8e8e8';
  if(mode==='trajectory') return TRAJ[r.trajectory]||'#cfd6df';
  return scoreColor(r.score);}
function style(f){const fips=f.properties._fips,has=byFips[fips];
  return {fillColor:colorFor(fips),weight:has?1:.5,color:has?'#fff':'#ccc',fillOpacity:has?.85:.25};}
layer=L.geoJSON(GEO,{style,onEachFeature:(f,l)=>{const fips=f.properties._fips,r=byFips[fips];
  l.on('click',()=>showDetail(fips));
  l.bindTooltip(f.properties._name+(r?(' — '+SLABEL[r.stance]+' · '+r.score):''),{sticky:true});}}).addTo(map);
function setMode(m){mode=m;document.querySelectorAll('.mode[data-mode]').forEach(e=>e.classList.toggle('on',e.dataset.mode===m));layer.setStyle(style);legend();}
// transmission overlay (HIFLD), lazy-loaded so the page stays light
let txLayer=null;
function txStyle(f){const vc=f.properties.vc;let c='#9aa6b3',w=1;
  if(vc==='500'||vc==='345'||vc==='735 AND ABOVE'){c='#c62828';w=2.3;}
  else if(vc==='220-287'){c='#7b3fbf';w=1.5;}
  return {color:c,weight:w,opacity:.8};}
function toggleTx(){const b=document.getElementById('txbtn');
  if(txLayer){map.removeLayer(txLayer);txLayer=null;b.classList.remove('on');b.textContent='⚡ Transmission';return;}
  b.classList.add('on');b.textContent='⚡ loading…';
  fetch('./va_transmission.geojson').then(r=>r.json()).then(d=>{
    txLayer=L.geoJSON(d,{style:txStyle}).addTo(map);b.textContent=`⚡ Transmission (${d.features.length})`;
  }).catch(e=>{b.textContent='⚡ needs localhost';b.classList.remove('on');});}
// fiber overlay (hubs + long-haul corridors) — public-source, compiled + scraped
let fibLayer=null;
const CTYPE={strategic:{color:'#0aa',weight:3.4,dash:null},dark:{color:'#2e7d32',weight:3,dash:'7 6'},backbone:{color:'#1f6feb',weight:2,dash:null}};
function toggleFiber(){const b=document.getElementById('fibtn');
  if(fibLayer){map.removeLayer(fibLayer);fibLayer=null;b.classList.remove('on');b.textContent='🔌 Fiber';return;}
  b.classList.add('on');b.textContent='🔌 loading…';
  fetch('./va_fiber.geojson').then(r=>r.json()).then(d=>{
    fibLayer=L.geoJSON(d,{
      style:f=>{const s=CTYPE[f.properties.ctype]||CTYPE.backbone;return {color:s.color,weight:s.weight,opacity:.85,dashArray:s.dash};},
      pointToLayer:(f,ll)=>{const t=f.properties.tier;const r=t===1?8:t===2?6:5;
        return L.circleMarker(ll,{radius:r,fillColor:t===1?'#c62828':t===2?'#ef6c00':'#8e44ad',color:'#fff',weight:1.5,fillOpacity:.95});},
      onEachFeature:(f,l)=>{const p=f.properties;
        l.bindPopup(p.kind==='hub'
          ?`<b>${p.name}</b><br><i>${p.type} · tier ${p.tier} hub</i><br>${p.why}`
          :`<b>${p.name}</b><br><i>${p.ctype} corridor</i><br>Carriers: ${p.owners}<br>${p.why}`);}
    }).addTo(map);
    b.textContent=`🔌 Fiber (${d.features.length})`;
  }).catch(e=>{b.textContent='🔌 needs localhost';b.classList.remove('on');});}
function arrow(t){return t==='tightening'?'<b style="color:var(--mor)">▲ tightening</b>':t==='loosening'?'<b style="color:var(--pos)">▼ loosening</b>':'<b style="color:#888">▬ stable</b>';}
function fiberBlock(fips){const f=FIB[fips];if(!f)return '';
  const c=f.fiber_score>=70?'#2e7d32':f.fiber_score>=50?'#1f6feb':f.fiber_score>=30?'#ef6c00':'#c62828';
  const act=f.dark_fiber_action==='lease'?'<b style="color:var(--pos)">lease now</b>':'<b style="color:var(--res)">build/extend</b>';
  return `<div class="card"><div class="t">🔌 Fiber — <span style="color:${c}">${f.fiber_score}/100</span> · ${f.fiber_tier}</div>
   <div class="kv"><b>Dark fiber:</b> ${f.dark_fiber} (${f.dark_fiber_region}) — ${act}</div>
   <div class="kv"><b>Nearest hub:</b> ${f.nearest_hub} (${f.nearest_hub_km} km)</div>
   ${f.on_corridor?`<div class="kv"><b>On corridor:</b> ${f.on_corridor}</div>`:''}
   ${f.fiber_premises_funded!=null?`<div class="muted">${f.fiber_premises_funded} fiber-to-premises locations funded (scraped, data.virginia.gov)</div>`:''}</div>`;}
function showDetail(fips){const r=byFips[fips],d=document.getElementById('detail');
  if(!r){d.innerHTML='<p class="hint">No classified data for this locality.</p>'+fiberBlock(fips);return;}
  d.innerHTML=`<p class="county-name">${r.name} County</p>
   <span class="tag" style="background:${STANCE[r.stance]}">${SLABEL[r.stance]||r.stance}</span> ${arrow(r.trajectory)}
   <div class="kv" style="margin-top:8px"><b>Buildability:</b> <span class="score" style="color:${scoreColor(r.score)}">${r.score}/100</span> — ${r.tier}</div>
   <div class="kv"><b>Zoning path:</b> ${r.zoning_path||'—'}</div>
   <div class="kv"><b>Key limits:</b> ${r.key_limits||'—'}</div>
   <div class="kv"><b>Recent action:</b> ${r.recent_action||'—'} ${r.recent_action_year?('('+r.recent_action_year+')'):''}</div>
   <div class="kv" style="margin-top:6px">${r.summary||''}</div>
   <div class="muted" style="margin-top:6px">model confidence: ${r.confidence!=null?r.confidence:'—'}</div>`+fiberBlock(fips);}
function legend(){document.getElementById('legend').innerHTML = mode==='stance'
   ? Object.keys(STANCE).map(k=>`<span><i style="background:${STANCE[k]}"></i>${SLABEL[k]}</span>`).join('')
   : mode==='trajectory' ? Object.keys(TRAJ).map(k=>`<span><i style="background:${TRAJ[k]}"></i>${k}</span>`).join('')
   : '<span><i style="background:#2e7d32"></i>72+</span><span><i style="background:#1f6feb"></i>52-71</span><span><i style="background:#ef6c00"></i>32-51</span><span><i style="background:#c62828"></i>&lt;32</span>';}
function trend(){document.getElementById('trend').innerHTML = CON.statewide_trend?`<h2>Statewide trend</h2><div class="card">${CON.statewide_trend}</div>`:'';}
function contagionPanel(){const el=document.getElementById('contagion');let h='';const w=CON.siting_windows;
  if(w){h+=`<h2>Siting windows</h2><div class="card"><b style="color:var(--pos)">▲ Opening:</b> ${(w.opening||[]).join(', ')||'—'}<br><b style="color:var(--mor)">▼ Closing:</b> ${(w.closing||[]).join(', ')||'—'}</div>`;}
  if((CON.contagion||[]).length){h+='<h2>Policy contagion</h2>';CON.contagion.forEach(p=>h+=`<div class="card"><div class="t">${p.pattern}</div><div class="muted">${(p.counties||[]).join(', ')}</div><div>${p.evidence||''}</div></div>`);}
  if((CON.next_likely||[]).length){h+='<h2>Predicted next moves</h2>';CON.next_likely.forEach(p=>h+=`<div class="card"><div class="t">${p.county}</div><div>${p.prediction||''}</div></div>`);}
  el.innerHTML=h;}

// ---- dashboard ----
function dash(){const tb=document.getElementById('rankbody');
  tb.innerHTML=REC.map((r,i)=>`<tr onclick="showView('map');showDetail('${r.fips}');map.setView([37.6,-78.9],7)">
    <td class="rank">${i+1}</td><td><b>${r.name}</b><div class="muted">${r.region||''}</div></td>
    <td style="width:130px"><span class="score" style="color:${scoreColor(r.score)}">${r.score}</span><div class="bar"><span style="width:${r.score}%;background:${scoreColor(r.score)}"></span></div></td>
    <td><span class="tierp" style="background:${TIERC[r.tier]};color:#fff">${r.tier.replace('Tier ','T').replace(' — ',' ')}</span></td>
    <td><span class="tag" style="background:${STANCE[r.stance]}">${SLABEL[r.stance]||r.stance}</span></td>
    <td>${r.zoning_path||'—'}</td><td>${arrow(r.trajectory)}</td>
    <td>${r.why||''} ${r.window==='closing'?'<span class="flag">⏳ window closing</span>':r.window==='open'?'<span class="flag" style="color:var(--pos)">✓ wide open</span>':''}</td></tr>`).join('');}

legend();trend();contagionPanel();dash();
document.getElementById('foot').innerHTML=`Engines: <b>Gemini 3.5 Flash</b> (Search-grounded gather) + <b>Gemma 4 E4B</b> local (classification) + <b>Claude</b> (contagion synthesis + orchestration). Inputs: public county zoning ordinances. <b>AI-generated — demo, not legal advice; verify against the source ordinance.</b> Built ${BUILT}. ${REC.length} counties.`;
</script></body></html>"""

out = (HTML
       .replace("__GEO__", json.dumps(geo, separators=(",", ":")))
       .replace("__REC__", json.dumps(records, separators=(",", ":")))
       .replace("__CON__", json.dumps(contagion, separators=(",", ":")))
       .replace("__FIB__", json.dumps(fiber, separators=(",", ":")))
       .replace("__BUILT__", built).replace("__N__", str(n))
       .replace("__T1__", str(t1)).replace("__AVOID__", str(avoid)).replace("__CLOSING__", str(closing)))
(DIST / "index.html").write_text(out, encoding="utf-8")
# copy the transmission overlay (HIFLD) next to the html for lazy fetch
tx = DATA / "va_transmission.geojson"
if tx.exists():
    shutil.copy(tx, DIST / "va_transmission.geojson")
    print(f"copied transmission overlay ({tx.stat().st_size//1024} KB)")
fib = DATA / "va_fiber.geojson"
if fib.exists():
    shutil.copy(fib, DIST / "va_fiber.geojson")
    print(f"copied fiber overlay ({fib.stat().st_size//1024} KB)")
print(f"wrote dist/index.html ({len(out)//1024} KB) | {n} counties | Tier1={t1} Avoid={avoid} closing={closing}")
print("top 8:", ", ".join(f"{r['name']}({r['score']})" for r in records[:8]))
