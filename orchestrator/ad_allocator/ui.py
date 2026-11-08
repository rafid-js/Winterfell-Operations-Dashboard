"""Ad Allocator — dashboard page (single string, served by routes.ad_allocator_page).

Dark theme matched to the published design artifact (#0e1116 bg, #3ec3b0 teal
accent, #f2994a orange for loss-makers). Vanilla JS + inline SVG charts, no CDN
(consistent with the other blueprints). Consumes the /api/ad-allocator/* JSON.
"""

PAGE_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ad Allocator · Winterfell OS</title>
<style>
:root{
  --bg:#0e1116; --panel:#161b22; --panel2:#1d242e; --line:#262d38; --line2:#2f3846;
  --tx:#e6edf3; --mut:#9aa7b5; --mut2:#7b8696; --teal:#3ec3b0; --teal-d:#12332f;
  --teal-l:#7fe3d3; --blue:#6aa8ff; --blue-l:#9ec5ff; --orange:#f2994a; --yellow:#f6b26b;
  --red:#ff9d8a;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
  font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;font-size:14px}
a{color:var(--teal);text-decoration:none}
.wrap{max-width:1360px;margin:0 auto;padding:16px}
header{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:16px}
.brand{font-weight:700;font-size:1.15rem;letter-spacing:-.01em}
.brand small{color:var(--mut);font-weight:500;font-size:.72rem;margin-left:6px;
  border:1px solid var(--line2);border-radius:5px;padding:2px 6px}
.badge{margin-left:auto;font-size:.72rem;font-weight:600;padding:4px 10px;border-radius:999px;
  border:1px solid var(--line2)}
.badge.sample{color:var(--yellow);background:#2a220f;border-color:#4a3c1a}
.badge.live{color:var(--teal-l);background:var(--teal-d);border-color:#1c5049}
.chips{display:flex;gap:4px;flex-wrap:wrap}
.chip{background:var(--panel);border:1px solid var(--line);color:var(--mut);
  border-radius:7px;padding:5px 11px;font-size:.8rem;cursor:pointer}
.chip:hover{border-color:var(--line2);color:var(--tx)}
.chip.on{background:var(--teal-d);border-color:#1c5049;color:var(--teal-l)}
.filters{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0 16px;align-items:center}
.filters label{color:var(--mut2);font-size:.75rem;margin-right:4px}
select{background:var(--panel);border:1px solid var(--line);color:var(--tx);
  border-radius:7px;padding:6px 8px;font-size:.8rem}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:16px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi .l{color:var(--mut2);font-size:.72rem;text-transform:uppercase;letter-spacing:.04em}
.kpi .v{font-size:1.35rem;font-weight:700;margin-top:4px;font-variant-numeric:tabular-nums}
.kpi .s{color:var(--mut);font-size:.72rem;margin-top:2px}
.kpi .v.pos{color:var(--teal-l)} .kpi .v.neg{color:var(--red)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
.card h3{margin:0 0 10px;font-size:.82rem;font-weight:600;color:var(--tx)}
.card h3 span{color:var(--mut2);font-weight:400}
svg{display:block;width:100%;height:auto}
table{width:100%;border-collapse:collapse;font-size:.78rem}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:10px}
th,td{padding:8px 10px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--line)}
th{color:var(--mut2);font-weight:600;text-transform:uppercase;font-size:.68rem;
  position:sticky;top:0;background:var(--panel2)}
td:first-child,th:first-child,td.l,th.l{text-align:left}
tr:hover td{background:#12171e}
.pill{font-size:.68rem;padding:2px 8px;border-radius:999px;font-weight:600;display:inline-block}
.pill.increase{color:var(--teal-l);background:var(--teal-d)}
.pill.decrease{color:var(--blue-l);background:#13233d}
.pill.hold{color:var(--mut);background:#1c2029}
.pill.pause,.pill.cap_stop{color:var(--red);background:#2a1215}
.pill.low_stock,.pill.reorder{color:var(--yellow);background:#2a220f}
.pill.test{color:var(--blue-l);background:#13233d}
.neg{color:var(--red)} .pos{color:var(--teal-l)}
.section-title{font-size:.95rem;font-weight:700;margin:22px 0 10px}
.btn{background:var(--teal-d);border:1px solid #1c5049;color:var(--teal-l);
  border-radius:7px;padding:7px 13px;font-size:.78rem;cursor:pointer;font-weight:600}
.btn:hover{background:#15423c} .btn.ghost{background:var(--panel);border-color:var(--line);color:var(--mut)}
.btn.warn{background:#2a1215;border-color:#5c2b2e;color:var(--red)}
.muted{color:var(--mut2)} .mono{font-variant-numeric:tabular-nums}
.row-actions{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}
details{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:12px}
summary{cursor:pointer;font-weight:600;font-size:.85rem}
input[type=text],input[type=number]{background:var(--bg);border:1px solid var(--line);
  color:var(--tx);border-radius:6px;padding:6px 8px;font-size:.78rem}
.formrow{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
.legend{display:flex;gap:14px;font-size:.72rem;color:var(--mut);margin-top:8px;flex-wrap:wrap}
.legend i{width:10px;height:10px;border-radius:2px;display:inline-block;margin-right:5px;vertical-align:-1px}
@media(max-width:900px){.kpis{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}}
</style></head>
<body><div class="wrap">
<header>
  <div class="brand">Ad Allocator <small>Warchest · Growth</small></div>
  <nav class="chips" style="margin-left:8px">
    <a class="chip" href="/">← Operations</a>
  </nav>
  <span id="badge" class="badge live">LIVE</span>
</header>

<div class="chips" id="periods">
  <button class="chip on" data-p="7d">Last 7d</button>
  <button class="chip" data-p="15d">15d</button>
  <button class="chip" data-p="30d">30d</button>
  <button class="chip" data-p="6m">6 months</button>
  <button class="chip" data-p="1y">1 year</button>
</div>

<div class="filters">
  <span><label>Rank by</label>
    <select id="rankby"><option value="est_profit_7d">Profit</option>
      <option value="revenue">Revenue</option><option value="units">Quantity</option>
      <option value="orders">Orders</option><option value="cpp">CPP</option></select></span>
  <span><label>Stock</label>
    <select id="stock"><option value="all">All</option><option value="healthy">Healthy</option>
      <option value="low">Low</option><option value="out">Out</option></select></span>
  <span><label>Source</label>
    <select id="mode"><option value="live">Live</option><option value="sample">Sample</option></select></span>
  <span class="muted" id="range" style="margin-left:auto;font-size:.74rem"></span>
</div>

<div class="kpis" id="kpis"></div>

<div class="grid">
  <div class="card"><h3>Product ranking <span>· loss-makers in orange</span></h3><div id="c-rank"></div></div>
  <div class="card"><h3>Actual vs recommended spend share</h3><div id="c-share"></div>
    <div class="legend"><span><i style="background:var(--mut2)"></i>Actual</span>
      <span><i style="background:var(--teal)"></i>Recommended</span></div></div>
  <div class="card"><h3>CPP vs break-even CPP</h3><div id="c-cpp"></div>
    <div class="legend"><span><i style="background:var(--blue)"></i>CPP</span>
      <span><i style="background:var(--teal)"></i>Break-even</span></div></div>
  <div class="card"><h3>Stock cover <span>· vs 20-day lead</span></h3><div id="c-cover"></div></div>
</div>

<div class="card" style="margin-bottom:16px"><h3>70 / 20 / 10 budget split <span>· next 7 days, with guardrails</span></h3>
  <div id="pools"></div></div>

<div class="section-title">Allocation</div>
<div class="tablewrap"><table id="alloc"><thead><tr>
  <th class="l">#</th><th class="l">Product</th><th>Units</th><th>Orders</th><th>Revenue</th>
  <th>Spend</th><th>Est. profit</th><th>CPP / BE</th><th>Cover</th>
  <th>Rec share</th><th>Next-7d ৳</th><th>Gap</th><th class="l">Action</th>
</tr></thead><tbody></tbody></table></div>

<div class="section-title">Approval queue <span class="muted" style="font-weight:400;font-size:.8rem">· budget changes reach Meta only after you approve</span></div>
<div class="card">
  <div class="row-actions">
    <button class="btn" id="save-run">Save allocation run</button>
    <button class="btn ghost" id="build-changes">Prepare Meta changes →</button>
    <button class="btn warn" id="push">Push approved to Meta</button>
    <span class="muted" id="run-note" style="align-self:center"></span>
  </div>
  <div class="tablewrap"><table id="changes"><thead><tr>
    <th class="l">#</th><th class="l">Group</th><th class="l">Ad set</th><th>From ৳/day</th>
    <th>To ৳/day</th><th class="l">Status</th><th class="l">Approve</th>
  </tr></thead><tbody></tbody></table></div>
</div>

<details style="margin-top:16px"><summary>Mapping admin · ads → product groups</summary>
  <div class="formrow">
    <input type="text" id="g-name" placeholder="Product group name (e.g. Cargo Pants)">
    <input type="text" id="g-cat" placeholder="category" style="width:120px">
    <label><input type="checkbox" id="g-new"> new</label>
    <label><input type="checkbox" id="g-clr"> clearance</label>
    <button class="btn" id="g-add">Add group</button>
    <button class="btn ghost" id="suggest">Suggest from ad names</button>
  </div>
  <div id="groups-list" class="muted" style="font-size:.78rem"></div>
  <div id="suggest-list" style="font-size:.78rem;margin-top:8px"></div>
</details>

<script>
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const fmt=n=>'৳'+Math.round(n||0).toLocaleString('en-US');
const fmt0=n=>Math.round(n||0).toLocaleString('en-US');
let STATE={period:'7d',mode:'live',rankby:'est_profit_7d',stock:'all',data:null,runId:null};

async function jget(u){const r=await fetch(u);return r.json()}
async function jpost(u,b){const r=await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});return r.json()}
async function jdel(u){const r=await fetch(u,{method:'DELETE'});return r.json()}

// ---- charts (inline SVG) ----
function barChart(el,items,valKey,labelKey,colorFn,unit){
  const W=520,H=Math.max(60,items.length*26+10),pad=8,bw=W-170;
  const max=Math.max(1,...items.map(i=>Math.abs(i[valKey])||0));
  let s=`<svg viewBox="0 0 ${W} ${H}">`;
  items.forEach((it,i)=>{
    const y=i*26+6, v=it[valKey]||0, w=Math.abs(v)/max*bw, col=colorFn(it);
    s+=`<text x="0" y="${y+13}" fill="#9aa7b5" font-size="11">${(it[labelKey]||'').slice(0,20)}</text>`;
    s+=`<rect x="150" y="${y+3}" width="${w}" height="15" rx="3" fill="${col}"/>`;
    s+=`<text x="${150+w+5}" y="${y+14}" fill="#c3cdd8" font-size="10">${unit?unit(v):fmt0(v)}</text>`;
  });
  el.innerHTML=s+'</svg>';
}
function groupedShare(el,items){
  const W=520,H=Math.max(60,items.length*30+10),bw=W-180;
  let s=`<svg viewBox="0 0 ${W} ${H}">`;
  items.forEach((it,i)=>{
    const y=i*30+6;
    s+=`<text x="0" y="${y+16}" fill="#9aa7b5" font-size="11">${(it.name||'').slice(0,20)}</text>`;
    s+=`<rect x="150" y="${y+2}" width="${(it.actual_share||0)*bw}" height="9" rx="2" fill="#7b8696"/>`;
    s+=`<rect x="150" y="${y+13}" width="${(it.rec_share||0)*bw}" height="9" rx="2" fill="#3ec3b0"/>`;
  });
  el.innerHTML=s+'</svg>';
}
function cppChart(el,items){
  const W=520,H=Math.max(60,items.length*26+10),bw=W-180;
  const max=Math.max(1,...items.map(i=>Math.max(i.cpp,i.break_even_cpp)));
  let s=`<svg viewBox="0 0 ${W} ${H}">`;
  items.forEach((it,i)=>{
    const y=i*26+6, over=it.cpp>it.break_even_cpp;
    s+=`<text x="0" y="${y+14}" fill="#9aa7b5" font-size="11">${(it.name||'').slice(0,18)}</text>`;
    s+=`<rect x="150" y="${y+3}" width="${it.cpp/max*bw}" height="7" rx="2" fill="${over?'#f2994a':'#6aa8ff'}"/>`;
    s+=`<rect x="150" y="${y+12}" width="${it.break_even_cpp/max*bw}" height="4" rx="2" fill="#3ec3b0"/>`;
  });
  el.innerHTML=s+'</svg>';
}
function coverChart(el,items){
  const W=520,H=Math.max(60,items.length*24+16),bw=W-180,cap=60;
  const lead=(STATE.cfg&&STATE.cfg.cover_reorder_days)||20;
  let s=`<svg viewBox="0 0 ${W} ${H}">`;
  const lx=150+Math.min(lead,cap)/cap*bw;
  s+=`<line x1="${lx}" y1="0" x2="${lx}" y2="${H-14}" stroke="#f6b26b" stroke-dasharray="3 3"/>`;
  s+=`<text x="${lx+3}" y="10" fill="#f6b26b" font-size="9">${lead}d lead</text>`;
  items.forEach((it,i)=>{
    const y=i*24+14, c=Math.min(it.cover_days,cap), col=it.cover_days<10?'#ff9d8a':it.cover_days<lead?'#f6b26b':'#3ec3b0';
    s+=`<text x="0" y="${y+12}" fill="#9aa7b5" font-size="11">${(it.name||'').slice(0,18)}</text>`;
    s+=`<rect x="150" y="${y+3}" width="${c/cap*bw}" height="12" rx="3" fill="${col}"/>`;
    s+=`<text x="${150+c/cap*bw+4}" y="${y+13}" fill="#c3cdd8" font-size="9">${it.cover_days>=999?'∞':Math.round(it.cover_days)+'d'}</text>`;
  });
  el.innerHTML=s+'</svg>';
}

// ---- render ----
function renderKpis(k){
  const t=[
    ['Ad spend',fmt(k.ad_spend),k.unmapped_spend?('৳'+fmt0(k.unmapped_spend)+' unmapped'):''],
    ['Orders',fmt0(k.orders),fmt0(k.units)+' units'],
    ['Order revenue',fmt(k.revenue),''],
    ['Blended CPP',fmt(k.blended_cpp),''],
    ['ROAS',(k.roas||0).toFixed(2)+'×',''],
    ['Est. profit',fmt(k.est_profit),'',k.est_profit>=0?'pos':'neg'],
    ['After fixed cost',fmt(k.after_fixed),'',k.after_fixed>=0?'pos':'neg'],
    ['Unmapped spend',fmt(k.unmapped_spend),'reconciles totals'],
  ];
  $('#kpis').innerHTML=t.map(r=>`<div class="kpi"><div class="l">${r[0]}</div>
    <div class="v ${r[3]||''}">${r[1]}</div><div class="s">${r[2]||''}</div></div>`).join('');
}
function filteredRows(){
  let rows=[...(STATE.data.rows||[])];
  const s=STATE.stock;
  if(s!=='all') rows=rows.filter(r=>{
    if(s==='out')return !r.in_stock||r.on_hand<=0;
    if(s==='low')return r.cover_days<20; if(s==='healthy')return r.cover_days>=20&&r.on_hand>0; return true;});
  rows.sort((a,b)=>(b[STATE.rankby]||0)-(a[STATE.rankby]||0));
  return rows;
}
function renderCharts(rows){
  const top=rows.slice(0,8);
  barChart($('#c-rank'),top,STATE.rankby,'name',it=>it.est_profit_7d<0?'#f2994a':'#3ec3b0',
    v=>STATE.rankby.includes('profit')||STATE.rankby==='revenue'||STATE.rankby==='cpp'?fmt(v):fmt0(v));
  groupedShare($('#c-share'),top);
  cppChart($('#c-cpp'),top);
  coverChart($('#c-cover'),top.filter(r=>r.on_hand>0));
}
function renderPools(p){
  const tot=(p.proven+p.test+p.retargeting)||1;
  const seg=(label,v,col)=>`<div style="flex:${v};min-width:60px;background:${col};padding:8px;border-radius:7px">
    <div style="font-size:.7rem;color:#0e1116;font-weight:700">${label}</div>
    <div style="font-size:.9rem;color:#0e1116;font-weight:700">${fmt(v)}</div></div>`;
  $('#pools').innerHTML=`<div style="display:flex;gap:6px">
    ${seg('Proven 70%',p.proven,'#3ec3b0')}${seg('Test 20%',p.test,'#6aa8ff')}${seg('Retargeting 10%',p.retargeting,'#f6b26b')}</div>
    <div class="legend"><span>Guardrails: max ±20%/ad-set/day · min 2 days data · learning threshold ~50 purchases/wk</span></div>`;
}
function renderTable(rows){
  $('#alloc tbody').innerHTML=rows.map(r=>`<tr>
    <td class="l mono">${r.rank}</td><td class="l">${r.name}${r.is_clearance?' <span class="pill cap_stop">clr</span>':''}${r.is_new?' <span class="pill test">new</span>':''}</td>
    <td class="mono">${fmt0(r.units)}</td><td class="mono">${fmt0(r.orders)}</td>
    <td class="mono">${fmt(r.revenue)}</td><td class="mono">${fmt(r.spend)}</td>
    <td class="mono ${r.est_profit<0?'neg':'pos'}">${fmt(r.est_profit)}</td>
    <td class="mono">${fmt0(r.cpp)} / ${fmt0(r.break_even_cpp)}</td>
    <td class="mono">${r.cover_days>=999?'∞':Math.round(r.cover_days)+'d'}</td>
    <td class="mono">${((r.rec_share||0)*100).toFixed(1)}%</td>
    <td class="mono">${fmt(r.rec_amount)}</td>
    <td class="mono ${r.gap<0?'neg':'pos'}">${(r.gap<0?'':'+')+fmt(r.gap)}</td>
    <td class="l"><span class="pill ${r.action}">${r.action.replace('_','/')}</span></td></tr>`).join('');
}
async function load(){
  const d=await jget(`/api/ad-allocator/overview?period=${STATE.period}&mode=${STATE.mode}`);
  STATE.data=d; STATE.cfg=(await jget('/api/ad-allocator/config'));
  const sample=d.mode==='sample'||d.auto_sample;
  $('#badge').className='badge '+(sample?'sample':'live'); $('#badge').textContent=sample?'SAMPLE':'LIVE';
  $('#range').textContent=d.since==='sample'?'sample data':`${d.since} → ${d.until} · ${d.days}d`;
  renderKpis(d.kpis);
  const rows=filteredRows(); renderCharts(rows); renderPools(d.pools); renderTable(rows);
}
async function loadChanges(){
  if(!STATE.runId){$('#changes tbody').innerHTML='';return;}
  const cr=await jget('/api/ad-allocator/changes?run_id='+STATE.runId);
  $('#changes tbody').innerHTML=cr.map(c=>`<tr><td class="l mono">${c.id}</td><td class="l">${c.group_name||''}</td>
    <td class="l mono">${c.ad_set_id}</td><td class="mono">${c.from_amount==null?'—':fmt(c.from_amount)}</td>
    <td class="mono">${fmt(c.to_amount)}</td><td class="l"><span class="pill ${c.status==='pushed'?'increase':c.status==='failed'||c.status==='rejected'?'pause':'hold'}">${c.status}</span></td>
    <td class="l">${c.status==='pending'?`<button class="btn" onclick="approve(${c.id})">Approve</button> <button class="btn ghost" onclick="reject(${c.id})">✕</button>`:'—'}</td></tr>`).join('')
    ||'<tr><td colspan=7 class="muted">No changes yet — Prepare Meta changes from a saved run.</td></tr>';
}
window.approve=async id=>{await jpost(`/api/ad-allocator/changes/${id}/approve`);loadChanges();};
window.reject=async id=>{await jpost(`/api/ad-allocator/changes/${id}/reject`);loadChanges();};

// ---- events ----
$('#periods').onclick=e=>{if(e.target.dataset.p){$$('#periods .chip').forEach(c=>c.classList.remove('on'));
  e.target.classList.add('on');STATE.period=e.target.dataset.p;load();}};
$('#rankby').onchange=e=>{STATE.rankby=e.target.value;const r=filteredRows();renderCharts(r);renderTable(r);};
$('#stock').onchange=e=>{STATE.stock=e.target.value;const r=filteredRows();renderCharts(r);renderTable(r);};
$('#mode').onchange=e=>{STATE.mode=e.target.value;load();};
$('#save-run').onclick=async()=>{const r=await jpost('/api/ad-allocator/run',{period:STATE.period});
  STATE.runId=r.run_id;$('#run-note').textContent='Saved run #'+r.run_id;loadChanges();};
$('#build-changes').onclick=async()=>{if(!STATE.runId){alert('Save an allocation run first.');return;}
  const r=await jpost(`/api/ad-allocator/runs/${STATE.runId}/build-changes`);
  $('#run-note').textContent=`${r.created} change(s) prepared from run #${STATE.runId}`;loadChanges();};
$('#push').onclick=async()=>{if(!confirm('Push all APPROVED budget changes to Meta?'))return;
  const r=await jpost('/api/ad-allocator/changes/push');
  $('#run-note').textContent='Pushed: '+JSON.stringify(r.results.map(x=>x.status));loadChanges();};
$('#g-add').onclick=async()=>{const n=$('#g-name').value.trim();if(!n)return;
  await jpost('/api/ad-allocator/groups',{name:n,category:$('#g-cat').value,is_new:$('#g-new').checked,is_clearance:$('#g-clr').checked});
  $('#g-name').value='';loadGroups();};
$('#suggest').onclick=async()=>{const s=await jget('/api/ad-allocator/suggest');
  $('#suggest-list').innerHTML='<b>Suggestions</b> (click to map): '+(s.length?s.map(x=>
    `<button class="btn ghost" onclick='addMap(${x.product_group_id},"${x.adset_id}")'>${x.adset_name||x.adset_id} → ${x.group_name}</button>`).join(' '):'<span class="muted">none</span>');};
window.addMap=async(g,a)=>{await jpost('/api/ad-allocator/maps',{product_group_id:g,meta_adset_id:a});alert('Mapped.');};
async function loadGroups(){const g=await jget('/api/ad-allocator/groups');
  $('#groups-list').innerHTML='<b>Groups:</b> '+(g.length?g.map(x=>`${x.name}${x.is_new?' (new)':''}${x.is_clearance?' (clr)':''} <a onclick="delGroup(${x.id})" style="cursor:pointer">✕</a>`).join(' · '):'none yet');}
window.delGroup=async id=>{await jdel('/api/ad-allocator/groups/'+id);loadGroups();};

load(); loadGroups();
</script>
</div></body></html>
"""
