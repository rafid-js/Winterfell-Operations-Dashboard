"""
Inventory — Flask routes + single-page UI (render_template_string, matching the
rest of the app). The UI READS reorder_queue / dead_stock_log; it never runs the
formula (that's reorder_engine on a 6-hour cron, or the manual /run endpoint).
"""
from flask import request, jsonify, render_template_string

from . import inv_bp, inv_login_required
from . import models


# ── pages ─────────────────────────────────────────────────────────────────────
@inv_bp.route('/inventory')
@inv_login_required
def inventory_page():
    return render_template_string(INVENTORY_HTML)


# ── JSON API ──────────────────────────────────────────────────────────────────
@inv_bp.route('/api/inventory/metrics')
@inv_login_required
def api_metrics():
    try:
        return jsonify(models.get_metrics())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@inv_bp.route('/api/inventory/stock-health')
@inv_login_required
def api_stock_health():
    try:
        return jsonify(models.get_stock_health())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@inv_bp.route('/api/inventory/reorder-queue')
@inv_login_required
def api_reorder_queue():
    try:
        urgency = request.args.get('urgency')
        return jsonify({
            'rows': models.get_reorder_queue(urgency=urgency),
            'suppressed': models.get_suppressed(),
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@inv_bp.route('/api/inventory/dead-stock')
@inv_login_required
def api_dead_stock():
    try:
        return jsonify(models.get_dead_stock())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@inv_bp.route('/api/inventory/reorder/<path:sku_base>/po-prefill')
@inv_login_required
def api_po_prefill(sku_base):
    try:
        data = models.get_po_prefill(sku_base)
        if data is None:
            return jsonify({'error': 'No reorder row for ' + sku_base}), 404
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@inv_bp.route('/api/inventory/reorder/run', methods=['POST'])
@inv_login_required
def api_run_engine():
    try:
        from . import reorder_engine
        counts = reorder_engine.run()
        return jsonify({'success': True, 'counts': counts})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@inv_bp.route('/api/inventory/dead-stock/<int:row_id>/action', methods=['POST'])
@inv_login_required
def api_dead_stock_action(row_id):
    try:
        data = request.get_json(force=True, silent=True) or {}
        status = (data.get('status') or '').strip()
        if not status:
            return jsonify({'error': 'status required'}), 400
        models.update_dead_stock_action(row_id, status)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@inv_bp.route('/api/inventory/sku/batch-type', methods=['POST'])
@inv_login_required
def api_batch_type():
    try:
        data = request.get_json(force=True, silent=True) or {}
        sku = (data.get('sku') or '').strip()
        bt = (data.get('batch_type') or '').strip()
        if not sku or not bt:
            return jsonify({'error': 'sku and batch_type required'}), 400
        models.set_batch_type(sku, bt, data.get('qty'))
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@inv_bp.route('/api/inventory/reorder/<path:sku_base>/mark-po', methods=['POST'])
@inv_login_required
def api_mark_po(sku_base):
    try:
        data = request.get_json(force=True, silent=True) or {}
        po_id = (data.get('po_id') or '').strip()
        if not po_id:
            return jsonify({'error': 'po_id required'}), 400
        models.mark_po_created(sku_base, po_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── UI (single page, tabs via JS) ─────────────────────────────────────────────
INVENTORY_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --header-bg:#161b22;--header-border:#30363d;
  --bg-page:#F6F8FA;--bg-card:#FFFFFF;--bg-inner:#F9FAFB;
  --text-primary:#1A1F2E;--text-secondary:#4A5568;--text-tertiary:#718096;
  --border:#E1E7EF;--track:#E1E7EF;
  --teal:#1D9E75;--amber:#EF9F27;--red:#E24B4A;--purple:#7F77DD;--blue:#378ADD;
}
body{background:var(--bg-page);color:var(--text-primary);font-family:Arial,sans-serif;min-height:100vh}
header{background:var(--header-bg);border-bottom:1px solid var(--header-border);
       padding:14px 24px;display:grid;grid-template-columns:1fr auto 1fr;
       grid-template-areas:'brand nav actions';align-items:center;gap:8px}
header h1{font-size:1.2rem;font-weight:700;color:#f0f6fc;grid-area:brand}
.hdr-actions{grid-area:actions;display:flex;align-items:center;gap:12px;justify-content:flex-end}
.top-nav{grid-area:nav;display:flex;gap:4px}
.nav-link{color:#8b949e;font-size:.8rem;text-decoration:none;padding:4px 12px;
          border:1px solid transparent;border-radius:6px;transition:.2s}
.nav-link:hover{border-color:var(--header-border);color:#e6edf3}
.nav-link.active{border-color:var(--header-border);color:#e6edf3;background:#21262d}
.logout{color:#8b949e;font-size:.8rem;text-decoration:none;padding:4px 10px;
        border:1px solid var(--header-border);border-radius:6px;white-space:nowrap}
.ham{display:none;flex-direction:column;gap:5px;cursor:pointer;background:none;border:none;padding:4px}
.ham span{width:20px;height:2px;background:#8b949e;border-radius:2px;display:block}
.mob-nav{display:none;position:absolute;top:100%;left:0;right:0;background:var(--header-bg);
         border-bottom:1px solid var(--header-border);padding:10px 16px;flex-direction:column;gap:4px;z-index:49}
.mob-nav a{color:#8b949e;font-size:.9rem;text-decoration:none;padding:8px 12px;border-radius:6px;display:block}
.mob-nav a:hover,.mob-nav a.active{color:#e6edf3;background:#21262d}
@media(max-width:700px){
  header{grid-template-columns:1fr auto;grid-template-areas:'brand actions';position:relative}
  .top-nav{display:none!important}.ham{display:flex}.mob-nav.open{display:flex}
  .container{padding:14px 12px}
}
.container{max-width:1100px;margin:0 auto;padding:24px}
.page-head{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:1.1rem;gap:12px;flex-wrap:wrap}
.page-title{font-size:18px;font-weight:500}
.page-sub{color:var(--teal);font-size:11px;margin-top:4px;display:flex;align-items:center;gap:6px}
.page-sub .dot{width:7px;height:7px;border-radius:50%;background:var(--teal)}
.btn{padding:8px 16px;border-radius:8px;border:none;cursor:pointer;font-size:12px;font-weight:500;font-family:Arial;transition:.2s}
.btn-primary{background:#1A1F2E;color:#fff}.btn-primary:hover{filter:brightness(1.15)}
.btn-ghost{background:#fff;color:var(--text-primary);border:.5px solid var(--border)}
.btn-ghost:hover{border-color:var(--text-tertiary)}
.tabs{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:1.1rem;border-bottom:1px solid var(--border)}
.tab{padding:9px 16px;font-size:13px;color:var(--text-tertiary);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
.tab:hover{color:var(--text-primary)}
.tab.active{color:var(--purple);border-bottom-color:var(--purple);font-weight:500}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px;margin-bottom:1.1rem}
.metric{background:#fff;border:.5px solid var(--border);border-radius:12px;padding:1rem 1.1rem}
.metric .val{font-size:21px;font-weight:500}
.metric .lbl{color:var(--text-tertiary);font-size:11px;margin-top:4px;text-transform:uppercase;letter-spacing:.05em}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:1rem}
.pill{padding:6px 14px;border-radius:20px;border:.5px solid var(--border);background:#F6F8FA;
      color:var(--text-secondary);font-size:12px;cursor:pointer}
.pill.active{background:#EEEDFE;color:#3C3489;border-color:#7F77DD}
.search{width:100%;background:#fff;border:.5px solid var(--border);border-radius:8px;padding:9px 12px;
        font-size:12px;font-family:Arial;margin-bottom:1rem;color:var(--text-primary)}
.search:focus{outline:none;border-color:var(--purple)}
.card{background:#fff;border:.5px solid var(--border);border-radius:12px;padding:0;margin-bottom:10px;
      border-left:3px solid var(--teal);overflow:hidden}
.card.u-critical{border-left-color:var(--red)}
.card.u-rush{border-left-color:var(--amber)}
.card.u-monitor{border-left-color:var(--blue)}
.card.u-healthy{border-left-color:var(--teal)}
.card.u-dead{border-left-color:#888780}
.card-top{padding:.85rem 1.1rem;display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap}
.c-name{font-size:14px;font-weight:500}
.c-sku{font-family:'Courier New',monospace;font-size:11px;color:var(--text-tertiary)}
.c-meta{font-size:11px;color:var(--text-tertiary);margin-top:4px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:500}
.b-critical{background:#FCEBEB;color:#791F1F;border:.5px solid #F09595}
.b-rush{background:#FAEEDA;color:#633806;border:.5px solid #EF9F27}
.b-monitor{background:#E6F1FB;color:#0C447C;border:.5px solid #85B7EB}
.b-healthy{background:#E1F5EE;color:#085041;border:.5px solid #5DCAA5}
.b-dead{background:#F1EFE8;color:#444441;border:.5px solid #D3D1C7}
.sizegrid{display:flex;gap:6px;flex-wrap:wrap;padding:0 1.1rem 1rem}
.sz{border:.5px solid var(--border);border-radius:8px;padding:6px 9px;min-width:58px;text-align:center;background:var(--bg-inner)}
.sz .l{font-size:11px;font-weight:600;color:var(--text-primary)}
.sz .s{font-size:13px;font-weight:500;margin-top:2px}
.sz .v{font-size:9px;color:var(--text-tertiary);margin-top:1px}
.sz.ok .s{color:#1D9E75}
.sz.low{background:#FAEEDA}.sz.low .s{color:#BA7517}
.sz.zero{background:#FCEBEB}.sz.zero .s{color:#E24B4A;font-weight:600}
.card-actions{display:flex;gap:8px;padding:0 1.1rem 1rem;flex-wrap:wrap}
.qrow{display:grid;grid-template-columns:auto 1fr auto auto;gap:14px;align-items:center;
      padding:.85rem 1.1rem;border-bottom:.5px solid var(--border)}
.qrow:last-child{border-bottom:none}
.q-num{font-size:13px;color:var(--text-tertiary);font-weight:600}
.q-total{font-size:15px;font-weight:600}
.empty{text-align:center;color:var(--text-tertiary);font-size:12px;padding:50px 20px}
.section-h{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;
           margin:18px 0 8px;color:var(--text-secondary)}
.ai-rec{background:var(--bg-inner);border:.5px solid var(--border);border-radius:8px;padding:9px 11px;
        font-size:12px;color:var(--text-secondary);margin:0 1.1rem 1rem;line-height:1.5}
.strikes{font-size:13px;letter-spacing:2px}
.coming{background:#fff;border:.5px solid var(--border);border-radius:12px;padding:40px;text-align:center;color:var(--text-tertiary)}
"""

INVENTORY_HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Winterfell — Inventory</title>
<style>""" + INVENTORY_CSS + """</style></head>
<body>
<header>
  <h1>&#9876;&#65039; Winterfell Operations</h1>
  <nav class="top-nav">
    <a href="/" class="nav-link">Operations</a>
    <a href="/products" class="nav-link">Products</a>
    <a href="/customers" class="nav-link">Customers</a>
    <a href="/orders" class="nav-link">Orders</a>
    <a href="/inventory" class="nav-link active">Inventory</a>
    <a href="/supply-chain" class="nav-link">Supply Chain</a>
  </nav>
  <div class="hdr-actions">
    <a href="/logout" class="logout">Logout</a>
    <button class="ham" onclick="document.getElementById('mnav').classList.toggle('open')"><span></span><span></span><span></span></button>
  </div>
  <nav class="mob-nav" id="mnav">
    <a href="/">Operations</a><a href="/products">Products</a><a href="/customers">Customers</a>
    <a href="/orders">Orders</a><a href="/inventory" class="active">Inventory</a>
    <a href="/supply-chain">Supply Chain</a><a href="/logout">Logout</a>
  </nav>
</header>
<div class="container">
  <div class="page-head">
    <div>
      <div class="page-title">Inventory / <span id="tab-title">Stock Health</span></div>
      <div class="page-sub"><span class="dot"></span> <span id="sub">Brain connected</span></div>
    </div>
    <button class="btn btn-primary" onclick="runEngine(this)">&#8635; Recalculate</button>
  </div>

  <div class="metrics" id="metrics"></div>

  <div class="tabs">
    <div class="tab active" data-tab="stock"      onclick="setTab('stock',this)">Stock Health</div>
    <div class="tab"        data-tab="reorder"    onclick="setTab('reorder',this)">Reorder Queue</div>
    <div class="tab"        data-tab="dead"       onclick="setTab('dead',this)">Dead Stock</div>
    <div class="tab"        data-tab="demand"     onclick="setTab('demand',this)">True Demand</div>
    <div class="tab"        data-tab="size"       onclick="setTab('size',this)">Size Intel</div>
  </div>

  <div id="panel"><div class="empty">Loading&hellip;</div></div>
</div>

<script>
var TAB='stock';
function esc(s){if(s==null)return '';return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function fmtBDT(n){if(n==null||isNaN(n))return '&#2547;0';var v=Math.round(Number(n));
  if(v>=100000)return '&#2547;'+(v/100000).toFixed(1)+'L';
  return '&#2547;'+v.toString().replace(/\\B(?=(\\d{3})+(?!\\d))/g,',');}
function uClass(u){return 'u-'+(u||'healthy').toLowerCase();}
function uBadge(u){var k=(u||'Healthy').toLowerCase();return '<span class="badge b-'+k+'">'+esc(u)+'</span>';}
var TITLES={stock:'Stock Health',reorder:'Reorder Queue',dead:'Dead Stock',demand:'True Demand',size:'Size Intel'};

function loadMetrics(){
  fetch('/api/inventory/metrics').then(function(r){return r.json();}).then(function(m){
    if(m.error)return;
    document.getElementById('metrics').innerHTML=
      card(m.critical||0,'Critical reorder')+card(m.rush||0,'Rush reorder')+
      card(m.healthy||0,'Healthy')+card(m.dead_stock_count||0,'Dead stock')+
      card(fmtBDT(m.ghost_revenue||0),'Ghost revenue',true);
  });
}
function card(v,l,raw){return '<div class="metric"><div class="val">'+(raw?v:esc(v))+'</div><div class="lbl">'+esc(l)+'</div></div>';}

function setTab(t,el){
  TAB=t;document.getElementById('tab-title').textContent=TITLES[t];
  var tabs=document.querySelectorAll('.tab');for(var i=0;i<tabs.length;i++)tabs[i].classList.remove('active');
  if(el)el.classList.add('active');
  render();
}

function render(){
  var p=document.getElementById('panel');p.innerHTML='<div class="empty">Loading&hellip;</div>';
  if(TAB==='stock')return loadStock(p);
  if(TAB==='reorder')return loadReorder(p);
  if(TAB==='dead')return loadDead(p);
  p.innerHTML='<div class="coming">'+TITLES[TAB]+' &mdash; coming in the next build phase.<br><small>Engines (true_demand.py / size_intelligence.py) ship next.</small></div>';
}

function sizeGrid(row){
  var sb=row.size_breakdown||{},stk=row.current_stock_breakdown||{},s30=row.sales_30d_breakdown||{};
  var keys=Object.keys(stk.length?stk:sb);
  keys=Object.keys(Object.assign({},stk,sb));
  var h='<div class="sizegrid">';
  for(var i=0;i<keys.length;i++){
    var k=keys[i],st=stk[k]||0,sold=s30[k]||0,vel=(sold/30).toFixed(1);
    var cls=st===0?'zero':st<=5?'low':'ok';
    h+='<div class="sz '+cls+'"><div class="l">'+esc(k)+'</div><div class="s">'+st+'</div><div class="v">'+vel+'/day</div></div>';
  }
  return h+'</div>';
}

function loadStock(p){
  fetch('/api/inventory/stock-health').then(function(r){return r.json();}).then(function(rows){
    if(rows.error){p.innerHTML='<div class="empty">'+esc(rows.error)+'</div>';return;}
    window._stock=rows;
    p.innerHTML='<input id="q" class="search" placeholder="Search product or SKU..." oninput="renderStock()">'
               +'<div id="stock-list"></div>';
    renderStock();
  }).catch(function(e){p.innerHTML='<div class="empty">Failed: '+esc(e.message)+'</div>';});
}
function renderStock(){
  var rows=window._stock||[];
  var qEl=document.getElementById('q'),q=qEl?qEl.value.toLowerCase():'';
  var list=rows.filter(function(r){return !q||(r.product_name||'').toLowerCase().indexOf(q)>=0||(r.sku_base||'').toLowerCase().indexOf(q)>=0;});
  list.sort(function(a,b){
    var sa=Object.values(a.sales_30d_breakdown||{}).reduce(function(t,v){return t+v;},0);
    var sb=Object.values(b.sales_30d_breakdown||{}).reduce(function(t,v){return t+v;},0);
    return sb-sa;
  });
  var listEl=document.getElementById('stock-list');
  if(!listEl)return;
  if(!list.length){listEl.innerHTML='<div class="empty">No SKUs. Run the reorder engine first.</div>';return;}
  var h='';
  for(var i=0;i<list.length;i++){
    var r=list[i];
    h+='<div class="card '+uClass(r.urgency)+'"><div class="card-top"><div>'
      +'<div class="c-name">'+esc(r.product_name)+'</div>'
      +'<div class="c-sku">'+esc(r.sku_base)+'</div>'
      +'<div class="c-meta">'+(r.category?esc(r.category)+' &middot; ':'')+uBadge(r.urgency)
      +(r.days_until_stockout!=null?' &middot; stockout in '+r.days_until_stockout+'d':'')
      +(r.total_waiting_orders?' &middot; '+r.total_waiting_orders+' waiting':'')+'</div></div>'
      +'<div style="text-align:right"><div class="q-total">'+(r.recommended_total||0)+'</div><div class="c-sku">to order</div></div>'
      +'</div>'+sizeGrid(r)+'</div>';
  }
  listEl.innerHTML=h;
}

function loadReorder(p){
  fetch('/api/inventory/reorder-queue').then(function(r){return r.json();}).then(function(d){
    if(d.error){p.innerHTML='<div class="empty">'+esc(d.error)+'</div>';return;}
    var rows=d.rows||[],sup=d.suppressed||[];
    var groups={Critical:[],Rush:[],Monitor:[]};
    for(var i=0;i<rows.length;i++){var u=rows[i].urgency;if(groups[u])groups[u].push(rows[i]);}
    var h='';
    ['Critical','Rush','Monitor'].forEach(function(g){
      if(!groups[g].length)return;
      h+='<div class="section-h">'+g+' ('+groups[g].length+')</div>';
      for(var i=0;i<groups[g].length;i++)h+=qrow(groups[g][i]);
    });
    if(!h)h='<div class="empty">Nothing to reorder. All healthy. &#9989;</div>';
    if(sup.length){
      h+='<div class="section-h">&#9940; Suppressed &mdash; Kill Chain active ('+sup.length+')</div>';
      for(var j=0;j<sup.length;j++){
        var s=sup[j];
        h+='<div class="card u-dead"><div class="card-top"><div><div class="c-name">'+esc(s.product_name)+'</div>'
          +'<div class="c-meta">Blocked &mdash; Kill Chain stage '+esc(s.kill_chain_stage||'')+'</div></div></div></div>';
      }
    }
    p.innerHTML=h;
  }).catch(function(e){p.innerHTML='<div class="empty">Failed: '+esc(e.message)+'</div>';});
}
function qrow(r){
  var btn=r.po_created
    ? '<a class="btn btn-ghost" href="/supply-chain/po/'+encodeURIComponent(r.po_id)+'">View '+esc(r.po_id)+' &#8599;</a>'
    : '<a class="btn btn-primary" href="/supply-chain?prefill='+encodeURIComponent(r.sku_base)+'">Create PO &#8599;</a>';
  return '<div class="card '+uClass(r.urgency)+'"><div class="qrow">'
    +'<div class="q-num">'+uBadge(r.urgency)+'</div>'
    +'<div><div class="c-name">'+esc(r.product_name)+'</div><div class="c-sku">'+esc(r.sku_base)
    +(r.days_until_stockout!=null?' &middot; stockout in '+r.days_until_stockout+'d':'')
    +(r.total_waiting_orders?' &middot; '+r.total_waiting_orders+' waiting':'')+'</div></div>'
    +'<div style="text-align:right"><div class="q-total">'+(r.recommended_total||0)+'</div><div class="c-sku">'+fmtBDT(r.capital_at_risk_bdt)+' at risk</div></div>'
    +'<div>'+btn+'</div></div></div>';
}

function loadDead(p){
  fetch('/api/inventory/dead-stock').then(function(r){return r.json();}).then(function(rows){
    if(rows.error){p.innerHTML='<div class="empty">'+esc(rows.error)+'</div>';return;}
    if(!rows.length){p.innerHTML='<div class="empty">No dead stock. Inventory is healthy. &#9989;</div>';return;}
    var order=['Dead','Liquidate','Bundle','Markdown','Watch'],icons={Dead:'&#9760;',Liquidate:'&#128308;',Bundle:'&#128992;',Markdown:'&#128993;',Watch:'&#9898;'};
    var by={};for(var i=0;i<rows.length;i++){(by[rows[i].kill_chain_stage]=by[rows[i].kill_chain_stage]||[]).push(rows[i]);}
    var h='';
    order.forEach(function(st){
      if(!by[st])return;
      h+='<div class="section-h">'+icons[st]+' '+st+' ('+by[st].length+')</div>';
      for(var i=0;i<by[st].length;i++){
        var d=by[st][i];
        var strikes='';for(var s=0;s<3;s++)strikes+=(s<(d.strike_count||0)?'&#9679;':'&#9675;');
        var rec='';try{var j=JSON.parse(d.claude_recommendation);rec=j.ops_instruction||'';}catch(e){rec=d.claude_recommendation||'';}
        h+='<div class="card u-dead"><div class="card-top"><div>'
          +'<div class="c-name">'+esc(d.product_name)+'</div>'
          +'<div class="c-meta">'+(d.units_stuck||0)+' pcs &middot; '+fmtBDT(d.capital_locked_bdt)+' locked &middot; '
          +(d.days_since_last_sale!=null?d.days_since_last_sale+'d no sale':'')+' &middot; score '+(d.kill_chain_score||0)+'</div>'
          +'<div class="c-meta"><span class="strikes">'+strikes+'</span>'
          +(d.suggested_discount_pct?' &middot; suggest '+d.suggested_discount_pct+'% off':'')
          +(d.bundle_with_sku?' &middot; bundle with '+esc(d.bundle_with_sku):'')+'</div></div></div>'
          +(rec?'<div class="ai-rec">&#129504; '+esc(rec)+'</div>':'')
          +'<div class="card-actions">'
          +'<button class="btn btn-ghost" onclick="dsAction('+d.id+',\\'In Progress\\')">Mark in progress</button>'
          +'<button class="btn btn-ghost" onclick="dsAction('+d.id+',\\'Cleared\\')">Cleared</button>'
          +'<button class="btn btn-ghost" onclick="dsAction('+d.id+',\\'Written Off\\')">Write off</button>'
          +'</div></div>';
      }
    });
    p.innerHTML=h;
  }).catch(function(e){p.innerHTML='<div class="empty">Failed: '+esc(e.message)+'</div>';});
}
function dsAction(id,status){
  fetch('/api/inventory/dead-stock/'+id+'/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:status})})
    .then(function(r){return r.json();}).then(function(){render();loadMetrics();});
}

function runEngine(btn){
  btn.disabled=true;var old=btn.innerHTML;btn.innerHTML='Calculating&hellip;';
  fetch('/api/inventory/reorder/run',{method:'POST'}).then(function(r){return r.json();}).then(function(d){
    btn.disabled=false;btn.innerHTML=old;
    if(d.error){alert('Failed: '+d.error);return;}
    loadMetrics();render();
  }).catch(function(e){btn.disabled=false;btn.innerHTML=old;alert('Failed: '+e.message);});
}

loadMetrics();render();
</script>
</body></html>"""
