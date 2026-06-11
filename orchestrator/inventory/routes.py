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


# ── Phase 2: True Demand / Size Intelligence / Test Batch ─────────────────────
@inv_bp.route('/api/inventory/true-demand')
@inv_login_required
def api_true_demand():
    try:
        return jsonify(models.get_true_demand())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@inv_bp.route('/api/inventory/size-intel')
@inv_login_required
def api_size_intel():
    try:
        return jsonify(models.get_size_intelligence())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@inv_bp.route('/api/inventory/test-batch')
@inv_login_required
def api_test_batch():
    try:
        return jsonify(models.get_test_batches())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@inv_bp.route('/api/inventory/true-demand/run', methods=['POST'])
@inv_login_required
def api_run_true_demand():
    try:
        from . import true_demand
        return jsonify({'success': True, 'result': true_demand.run()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@inv_bp.route('/api/inventory/size-intel/run', methods=['POST'])
@inv_login_required
def api_run_size_intel():
    try:
        from . import size_intelligence
        return jsonify({'success': True, 'result': size_intelligence.run()})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@inv_bp.route('/api/inventory/test-batch/run', methods=['POST'])
@inv_login_required
def api_run_test_batch():
    try:
        from . import test_batch
        return jsonify({'success': True, 'result': test_batch.run()})
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
.page-title{font-size:22px;font-weight:600}
.page-sub{color:var(--teal);font-size:13px;margin-top:5px;display:flex;align-items:center;gap:6px}
.page-sub .dot{width:8px;height:8px;border-radius:50%;background:var(--teal)}
.btn{padding:9px 18px;border-radius:8px;border:none;cursor:pointer;font-size:14px;font-weight:500;font-family:Arial;transition:.2s}
.btn-primary{background:#1A1F2E;color:#fff}.btn-primary:hover{filter:brightness(1.15)}
.btn-ghost{background:#fff;color:var(--text-primary);border:.5px solid var(--border)}
.btn-ghost:hover{border-color:var(--text-tertiary)}
.tabs{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:1.1rem;border-bottom:1px solid var(--border)}
.tab{padding:10px 18px;font-size:15px;color:var(--text-tertiary);cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-1px}
.tab:hover{color:var(--text-primary)}
.tab.active{color:var(--purple);border-bottom-color:var(--purple);font-weight:600}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin-bottom:1.1rem}
.metric{background:#fff;border:.5px solid var(--border);border-radius:12px;padding:1.1rem 1.2rem}
.metric .val{font-size:26px;font-weight:600}
.metric .lbl{color:var(--text-tertiary);font-size:13px;margin-top:5px;text-transform:uppercase;letter-spacing:.05em}
.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:1rem}
.pill{padding:7px 16px;border-radius:20px;border:.5px solid var(--border);background:#F6F8FA;
      color:var(--text-secondary);font-size:14px;cursor:pointer}
.pill.active{background:#EEEDFE;color:#3C3489;border-color:#7F77DD}
.search{width:100%;background:#fff;border:.5px solid var(--border);border-radius:8px;padding:11px 14px;
        font-size:15px;font-family:Arial;margin-bottom:1rem;color:var(--text-primary)}
.search:focus{outline:none;border-color:var(--purple)}
.card{background:#fff;border:.5px solid var(--border);border-radius:12px;padding:0;margin-bottom:10px;
      border-left:3px solid var(--teal);overflow:hidden}
.card.u-critical{border-left-color:var(--red)}
.card.u-rush{border-left-color:var(--amber)}
.card.u-monitor{border-left-color:var(--blue)}
.card.u-healthy{border-left-color:var(--teal)}
.card.u-dead{border-left-color:#888780}
.card-top{padding:.95rem 1.1rem;display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap}
.ct-left{display:flex;gap:13px;align-items:flex-start;min-width:0}
.thumb{width:56px;height:56px;object-fit:cover;border-radius:9px;border:.5px solid var(--border);background:#fff;flex-shrink:0}
.thumb-ph{width:56px;height:56px;border-radius:9px;border:.5px solid var(--border);background:var(--bg-inner);
          display:flex;align-items:center;justify-content:center;font-size:24px;flex-shrink:0}
.c-name{font-size:16px;font-weight:600;line-height:1.3}
.c-sku{font-family:'Courier New',monospace;font-size:13px;color:var(--text-tertiary)}
.c-meta{font-size:14px;font-weight:500;color:var(--text-secondary);margin-top:5px;display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.c-sub{font-size:14px;font-weight:500;color:var(--text-secondary);margin-top:4px}
.badge{display:inline-block;padding:4px 12px;border-radius:20px;font-size:14px;font-weight:600}
.b-critical{background:#FCEBEB;color:#791F1F;border:.5px solid #F09595}
.b-rush{background:#FAEEDA;color:#633806;border:.5px solid #EF9F27}
.b-monitor{background:#E6F1FB;color:#0C447C;border:.5px solid #85B7EB}
.b-healthy{background:#E1F5EE;color:#085041;border:.5px solid #5DCAA5}
.b-dead{background:#F1EFE8;color:#444441;border:.5px solid #D3D1C7}
.sizegrid{display:flex;gap:6px;flex-wrap:wrap;padding:0 1.1rem 1rem}
.sz{border:.5px solid var(--border);border-radius:8px;padding:7px 10px;min-width:62px;text-align:center;background:var(--bg-inner)}
.sz .l{font-size:14px;font-weight:700;color:var(--text-primary)}
.sz .s{font-size:18px;font-weight:700;margin-top:3px}
.sz .v{font-size:13px;font-weight:500;color:var(--text-secondary);margin-top:2px}
.sz.ok .s{color:#1D9E75}
.sz.low{background:#FAEEDA}.sz.low .s{color:#BA7517}
.sz.zero{background:#FCEBEB}.sz.zero .s{color:#E24B4A;font-weight:600}
.card-actions{display:flex;gap:8px;padding:0 1.1rem 1rem;flex-wrap:wrap}
.qrow{display:grid;grid-template-columns:auto auto 1fr auto auto;gap:14px;align-items:center;
      padding:.95rem 1.1rem;border-bottom:.5px solid var(--border)}
.qrow:last-child{border-bottom:none}
.q-num{font-size:14px;color:var(--text-tertiary);font-weight:600}
.q-total{font-size:20px;font-weight:700}
.q-label{font-size:13px;font-weight:600;color:var(--text-secondary)}
.empty{text-align:center;color:var(--text-tertiary);font-size:15px;padding:50px 20px}
.section-h{font-size:14px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;
           margin:18px 0 8px;color:var(--text-secondary)}
.ai-rec{background:var(--bg-inner);border:.5px solid var(--border);border-radius:8px;padding:10px 12px;
        font-size:14px;color:var(--text-secondary);margin:0 1.1rem 1rem;line-height:1.55}
.strikes{font-size:15px;letter-spacing:2px}
.coming{background:#fff;border:.5px solid var(--border);border-radius:12px;padding:40px;text-align:center;color:var(--text-tertiary);font-size:15px}
.kpi{display:flex;gap:20px;flex-wrap:wrap;padding:.2rem 1.1rem 1rem}
.kpi .k{font-size:13px;font-weight:500;color:var(--text-tertiary)}
.kpi .k b{display:block;font-size:18px;color:var(--text-primary);font-weight:700;margin-top:2px}
.kpi .k.ghost b{color:var(--red)}
.cat-card{background:#fff;border:.5px solid var(--border);border-radius:12px;padding:1.1rem 1.2rem;margin-bottom:12px}
.cat-card h3{font-size:17px;font-weight:600;margin-bottom:3px}
.cat-card .meta{font-size:13px;font-weight:500;color:var(--text-tertiary);margin-bottom:14px}
.sizebar-row{display:grid;grid-template-columns:60px 1fr 64px;gap:12px;align-items:center;margin-bottom:9px}
.sizebar-row .lbl{font-size:14px;font-weight:700}
.sizebar-row .pct{font-size:14px;font-weight:700;text-align:right;color:var(--text-secondary)}
.bar{height:22px;border-radius:6px;background:var(--track);position:relative;overflow:hidden;min-width:30px}
.bar>span{position:absolute;left:0;top:0;bottom:0;background:var(--purple);border-radius:6px}
.b-winner{background:#E1F5EE;color:#085041;border:.5px solid #5DCAA5}
.b-promising{background:#E6F1FB;color:#0C447C;border:.5px solid #85B7EB}
.b-pending{background:#F1EFE8;color:#444441;border:.5px solid #D3D1C7}
.b-kill{background:#FCEBEB;color:#791F1F;border:.5px solid #F09595}
.tb-form{background:#fff;border:.5px solid var(--border);border-radius:12px;padding:1rem 1.1rem;margin-bottom:14px;
         display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end}
.tb-form .fld{display:flex;flex-direction:column;gap:4px}
.tb-form label{font-size:12px;font-weight:600;color:var(--text-secondary)}
.tb-form input{background:#fff;border:.5px solid var(--border);border-radius:8px;padding:9px 11px;font-size:14px;font-family:Arial;color:var(--text-primary)}
.tb-form input:focus{outline:none;border-color:var(--purple)}
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
    <div class="tab"        data-tab="test"       onclick="setTab('test',this)">Test Batch</div>
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
function thumb(r){return r&&r.image_url
  ? '<img src="'+esc(r.image_url)+'" class="thumb" loading="lazy" onerror="imgErr(this)">'
  : '<div class="thumb-ph">&#128230;</div>';}
function imgErr(el){var ph=document.createElement('div');ph.className='thumb-ph';ph.innerHTML='&#128230;';el.parentNode.replaceChild(ph,el);}
function searchBar(id,fn){return '<input id="'+id+'" class="search" placeholder="Search product or SKU..." oninput="'+fn+'()">';}
function matchQ(r,q){return !q||(r.product_name||'').toLowerCase().indexOf(q)>=0||(r.sku_base||'').toLowerCase().indexOf(q)>=0;}
function curQ(id){var el=document.getElementById(id);return el?el.value.toLowerCase():'';}
var TITLES={stock:'Stock Health',reorder:'Reorder Queue',dead:'Dead Stock',demand:'True Demand',size:'Size Intel',test:'Test Batch'};

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
  if(TAB==='demand')return loadDemand(p);
  if(TAB==='size')return loadSize(p);
  if(TAB==='test')return loadTest(p);
  p.innerHTML='<div class="coming">'+TITLES[TAB]+' &mdash; coming soon.</div>';
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

var STOCK_FILTER='all';
var STOCK_PILLS=[['all','All'],['Critical','Critical'],['Rush','Rush'],['Monitor','Monitor'],['Healthy','Healthy'],['Dead','Dead']];
function stockPills(){
  var rows=window._stock||[];
  var counts={all:rows.length};
  for(var i=0;i<rows.length;i++){var u=rows[i].urgency||'Healthy';counts[u]=(counts[u]||0)+1;}
  var h='<div class="filters" id="stock-filters">';
  for(var j=0;j<STOCK_PILLS.length;j++){
    var key=STOCK_PILLS[j][0],lbl=STOCK_PILLS[j][1],n=counts[key]||0;
    if(key!=='all'&&!n)continue;
    h+='<button class="pill'+(STOCK_FILTER===key?' active':'')+'" onclick="setStockFilter(\\''+key+'\\')">'+esc(lbl)+' ('+n+')</button>';
  }
  return h+'</div>';
}
function setStockFilter(f){
  STOCK_FILTER=f;
  var fe=document.getElementById('stock-filters');if(fe)fe.outerHTML=stockPills();
  renderStock();
}
function loadStock(p){
  fetch('/api/inventory/stock-health').then(function(r){return r.json();}).then(function(rows){
    if(rows.error){p.innerHTML='<div class="empty">'+esc(rows.error)+'</div>';return;}
    window._stock=rows;
    p.innerHTML=stockPills()+searchBar('q-stock','renderStock')+'<div id="stock-list"></div>';
    renderStock();
  }).catch(function(e){p.innerHTML='<div class="empty">Failed: '+esc(e.message)+'</div>';});
}
function renderStock(){
  var rows=window._stock||[],q=curQ('q-stock');
  var list=rows.filter(function(r){
    if(STOCK_FILTER!=='all'&&(r.urgency||'Healthy')!==STOCK_FILTER)return false;
    return matchQ(r,q);
  });
  list.sort(function(a,b){
    var sa=Object.values(a.sales_30d_breakdown||{}).reduce(function(t,v){return t+v;},0);
    var sb=Object.values(b.sales_30d_breakdown||{}).reduce(function(t,v){return t+v;},0);
    return sb-sa;
  });
  var listEl=document.getElementById('stock-list');
  if(!listEl)return;
  if(!list.length){
    var hasData=(window._stock||[]).length>0;
    listEl.innerHTML='<div class="empty">'+(hasData?'No SKUs match this filter.':'No SKUs. Run the reorder engine first.')+'</div>';return;
  }
  var h='';
  for(var i=0;i<list.length;i++){
    var r=list[i];
    h+='<div class="card '+uClass(r.urgency)+'"><div class="card-top"><div class="ct-left">'+thumb(r)+'<div>'
      +'<div class="c-name">'+esc(r.product_name)+'</div>'
      +'<div class="c-sku">'+esc(r.sku_base)+'</div>'
      +'<div class="c-meta">'+(r.category?esc(r.category)+' &middot; ':'')+uBadge(r.urgency)
      +(r.days_until_stockout!=null?' &middot; stockout in '+r.days_until_stockout+'d':'')
      +(r.total_waiting_orders?' &middot; '+r.total_waiting_orders+' waiting':'')+'</div></div></div>'
      +'<div style="text-align:right"><div class="q-total">'+(r.recommended_total||0)+'</div><div class="c-sku">to order</div></div>'
      +'</div>'+sizeGrid(r)+'</div>';
  }
  listEl.innerHTML=h;
}

function loadReorder(p){
  fetch('/api/inventory/reorder-queue').then(function(r){return r.json();}).then(function(d){
    if(d.error){p.innerHTML='<div class="empty">'+esc(d.error)+'</div>';return;}
    window._reorder=d;
    p.innerHTML=searchBar('q-reorder','renderReorder')+'<div id="reorder-list"></div>';
    renderReorder();
  }).catch(function(e){p.innerHTML='<div class="empty">Failed: '+esc(e.message)+'</div>';});
}
function renderReorder(){
  var d=window._reorder||{},q=curQ('q-reorder');
  var rows=(d.rows||[]).filter(function(r){return matchQ(r,q);});
  var sup=(d.suppressed||[]).filter(function(r){return matchQ(r,q);});
  var listEl=document.getElementById('reorder-list');if(!listEl)return;
  var groups={Critical:[],Rush:[],Monitor:[]};
  for(var i=0;i<rows.length;i++){var u=rows[i].urgency;if(groups[u])groups[u].push(rows[i]);}
  ['Critical','Rush','Monitor'].forEach(function(g){
    groups[g].sort(function(a,b){return (b.recommended_total||0)-(a.recommended_total||0);});
  });
  var h='';
  ['Critical','Rush','Monitor'].forEach(function(g){
    if(!groups[g].length)return;
    h+='<div class="section-h">'+g+' ('+groups[g].length+')</div>';
    for(var i=0;i<groups[g].length;i++)h+=qrow(groups[g][i]);
  });
  if(!h&&!sup.length)h='<div class="empty">'+(q?'No matches.':'Nothing to reorder. All healthy. &#9989;')+'</div>';
  if(sup.length){
    h+='<div class="section-h">&#9940; Suppressed &mdash; Kill Chain active ('+sup.length+')</div>';
    for(var j=0;j<sup.length;j++){
      var s=sup[j];
      h+='<div class="card u-dead"><div class="card-top"><div class="ct-left">'+thumb(s)+'<div><div class="c-name">'+esc(s.product_name)+'</div>'
        +'<div class="c-meta">Blocked &mdash; Kill Chain stage '+esc(s.kill_chain_stage||'')+'</div></div></div></div></div>';
    }
  }
  listEl.innerHTML=h;
}
function qrow(r){
  var btn=r.po_created
    ? '<a class="btn btn-ghost" href="/supply-chain/po/'+encodeURIComponent(r.po_id)+'">View '+esc(r.po_id)+' &#8599;</a>'
    : '<a class="btn btn-primary" href="/supply-chain?prefill='+encodeURIComponent(r.sku_base)+'">Create PO &#8599;</a>';
  var sub='';
  if(r.days_until_stockout!=null)sub+='Stockout in '+r.days_until_stockout+'d';
  if(r.total_waiting_orders)sub+=(sub?' &middot; ':'')+r.total_waiting_orders+' waiting orders';
  return '<div class="card '+uClass(r.urgency)+'"><div class="qrow">'
    +'<div class="q-num">'+uBadge(r.urgency)+'</div>'
    +'<div>'+thumb(r)+'</div>'
    +'<div><div class="c-name">'+esc(r.product_name)+'</div>'
    +(sub?'<div class="c-sub">'+sub+'</div>':'')+'</div>'
    +'<div style="text-align:right"><div class="q-total">'+(r.recommended_total||0)+' pcs</div>'
    +'<div class="q-label">'+fmtBDT(r.capital_at_risk_bdt)+' at risk</div></div>'
    +'<div>'+btn+'</div></div></div>';
}

function loadDead(p){
  fetch('/api/inventory/dead-stock').then(function(r){return r.json();}).then(function(rows){
    if(rows.error){p.innerHTML='<div class="empty">'+esc(rows.error)+'</div>';return;}
    window._dead=rows;
    p.innerHTML=searchBar('q-dead','renderDead')+'<div id="dead-list"></div>';
    renderDead();
  }).catch(function(e){p.innerHTML='<div class="empty">Failed: '+esc(e.message)+'</div>';});
}
function renderDead(){
  var all=window._dead||[],q=curQ('q-dead');
  var rows=all.filter(function(r){return matchQ(r,q);});
  var listEl=document.getElementById('dead-list');if(!listEl)return;
  if(!rows.length){listEl.innerHTML='<div class="empty">'+(q?'No matches.':'No dead stock. Inventory is healthy. &#9989;')+'</div>';return;}
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
      h+='<div class="card u-dead"><div class="card-top"><div class="ct-left">'+thumb(d)+'<div>'
        +'<div class="c-name">'+esc(d.product_name)+'</div>'
        +'<div class="c-meta">'+(d.units_stuck||0)+' pcs &middot; '+fmtBDT(d.capital_locked_bdt)+' locked &middot; '
        +(d.days_since_last_sale!=null?d.days_since_last_sale+'d no sale':'')+' &middot; score '+(d.kill_chain_score||0)+'</div>'
        +'<div class="c-meta"><span class="strikes">'+strikes+'</span>'
        +(d.suggested_discount_pct?' &middot; suggest '+d.suggested_discount_pct+'% off':'')
        +(d.bundle_with_sku?' &middot; bundle with '+esc(d.bundle_with_sku):'')+'</div></div></div></div>'
        +(rec?'<div class="ai-rec">&#129504; '+esc(rec)+'</div>':'')
        +'<div class="card-actions">'
        +'<button class="btn btn-ghost" onclick="dsAction('+d.id+',\\'In Progress\\')">Mark in progress</button>'
        +'<button class="btn btn-ghost" onclick="dsAction('+d.id+',\\'Cleared\\')">Cleared</button>'
        +'<button class="btn btn-ghost" onclick="dsAction('+d.id+',\\'Written Off\\')">Write off</button>'
        +'</div></div>';
    }
  });
  listEl.innerHTML=h;
}
// ── True Demand ───────────────────────────────────────────────────────────
function loadDemand(p){
  fetch('/api/inventory/true-demand').then(function(r){return r.json();}).then(function(rows){
    if(rows.error){p.innerHTML='<div class="empty">'+esc(rows.error)+'</div>';return;}
    window._demand=rows;
    p.innerHTML=searchBar('q-demand','renderDemand')+'<div id="demand-list"></div>';
    renderDemand();
  }).catch(function(e){p.innerHTML='<div class="empty">Failed: '+esc(e.message)+'</div>';});
}
function renderDemand(){
  var all=window._demand||[],q=curQ('q-demand');
  var list=all.filter(function(r){return matchQ(r,q);});
  var el=document.getElementById('demand-list');if(!el)return;
  if(!list.length){el.innerHTML='<div class="empty">'+(q?'No matches.':'No demand data yet. Hit Recalculate to run the True Demand engine.')+'</div>';return;}
  var h='';
  for(var i=0;i<list.length;i++){
    var r=list[i],conv=(r.conversion_pct!=null?r.conversion_pct+'%':'—');
    h+='<div class="card u-monitor"><div class="card-top"><div class="ct-left">'+thumb(r)+'<div>'
      +'<div class="c-name">'+esc(r.product_name)+'</div>'
      +'<div class="c-meta">'+(r.category?esc(r.category):'')+'</div></div></div>'
      +'<div style="text-align:right"><div class="q-total">'+fmtBDT(r.ghost_revenue_bdt)+'</div><div class="q-label">ghost revenue</div></div></div>'
      +'<div class="kpi">'
      +'<div class="k">True demand<b>'+(r.true_demand||0)+'</b></div>'
      +'<div class="k">Delivered<b>'+(r.orders_delivered||0)+'</b></div>'
      +'<div class="k">Conversion<b>'+conv+'</b></div>'
      +'<div class="k">Cancelled<b>'+(r.orders_cancelled||0)+'</b></div>'
      +'<div class="k">Waiting<b>'+((r.true_demand||0)-(r.orders_delivered||0)-(r.orders_cancelled||0))+'</b></div>'
      +'</div></div>';
  }
  el.innerHTML=h;
}

// ── Size Intelligence ─────────────────────────────────────────────────────
function loadSize(p){
  fetch('/api/inventory/size-intel').then(function(r){return r.json();}).then(function(cats){
    if(cats.error){p.innerHTML='<div class="empty">'+esc(cats.error)+'</div>';return;}
    if(!cats.length){p.innerHTML='<div class="empty">No size profiles yet. Hit Recalculate to learn the size curve.</div>';return;}
    var h='';
    for(var i=0;i<cats.length;i++){
      var c=cats[i];
      h+='<div class="cat-card"><h3>'+esc(c.category)+'</h3>'
        +'<div class="meta">Learned from '+(c.sample_size||0)+' delivered units</div>';
      for(var j=0;j<c.sizes.length;j++){
        var s=c.sizes[j],pct=s.distribution_pct||0;
        h+='<div class="sizebar-row"><div class="lbl">'+esc(s.size)+'</div>'
          +'<div class="bar"><span style="width:'+Math.min(pct,100)+'%"></span></div>'
          +'<div class="pct">'+pct+'%</div></div>';
      }
      h+='</div>';
    }
    p.innerHTML=h;
  }).catch(function(e){p.innerHTML='<div class="empty">Failed: '+esc(e.message)+'</div>';});
}

// ── Test Batch ────────────────────────────────────────────────────────────
function vBadge(v){var k=(v||'Pending').toLowerCase();return '<span class="badge b-'+k+'">'+esc(v||'Pending')+'</span>';}
function loadTest(p){
  fetch('/api/inventory/test-batch').then(function(r){return r.json();}).then(function(rows){
    if(rows.error){p.innerHTML='<div class="empty">'+esc(rows.error)+'</div>';return;}
    window._test=rows;
    p.innerHTML=tbForm()+searchBar('q-test','renderTest')+'<div id="test-list"></div>';
    renderTest();
  }).catch(function(e){p.innerHTML='<div class="empty">Failed: '+esc(e.message)+'</div>';});
}
function tbForm(){
  return '<div class="tb-form">'
    +'<div class="fld"><label>SKU</label><input id="tb-sku" placeholder="exact SKU code"></div>'
    +'<div class="fld"><label>Test batch qty</label><input id="tb-qty" type="number" min="1" placeholder="e.g. 30"></div>'
    +'<button class="btn btn-primary" onclick="markTest(this)">Flag as Test batch</button></div>';
}
function markTest(btn){
  var sku=(document.getElementById('tb-sku').value||'').trim();
  var qty=parseInt(document.getElementById('tb-qty').value,10);
  if(!sku){alert('Enter a SKU');return;}
  btn.disabled=true;
  fetch('/api/inventory/sku/batch-type',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({sku:sku,batch_type:'Test',qty:isNaN(qty)?null:qty})})
    .then(function(r){return r.json();}).then(function(d){
      btn.disabled=false;
      if(d.error){alert('Failed: '+d.error);return;}
      loadTest(document.getElementById('panel'));
    }).catch(function(e){btn.disabled=false;alert('Failed: '+e.message);});
}
function renderTest(){
  var all=window._test||[],q=curQ('q-test');
  var list=all.filter(function(r){return !q||(r.product_name||'').toLowerCase().indexOf(q)>=0||(r.sku||'').toLowerCase().indexOf(q)>=0;});
  var el=document.getElementById('test-list');if(!el)return;
  if(!list.length){el.innerHTML='<div class="empty">'+(q?'No matches.':'No test batches yet. Flag a SKU above, then Recalculate.')+'</div>';return;}
  var h='';
  for(var i=0;i<list.length;i++){
    var r=list[i],st=(r.test_day7_sellthrough!=null?r.test_day7_sellthrough+'%':'—');
    h+='<div class="card u-monitor"><div class="card-top"><div class="ct-left">'+thumb(r)+'<div>'
      +'<div class="c-name">'+esc(r.product_name)+'</div>'
      +'<div class="c-meta"><span class="c-sku">'+esc(r.sku)+'</span> &middot; '+vBadge(r.test_verdict)+'</div></div></div>'
      +'<div style="text-align:right"><div class="q-total">'+st+'</div><div class="q-label">day-7 sell-through</div></div></div>'
      +'<div class="kpi">'
      +'<div class="k">Test qty<b>'+(r.test_batch_qty||0)+'</b></div>'
      +'<div class="k">In stock<b>'+(r.current_stock||0)+'</b></div>'
      +'<div class="k">Days elapsed<b>'+(r.days_elapsed!=null?r.days_elapsed:'—')+'</b></div>'
      +'<div class="k">Started<b>'+esc((r.test_batch_date||'').slice(0,10))+'</b></div>'
      +'</div></div>';
  }
  el.innerHTML=h;
}

function dsAction(id,status){
  fetch('/api/inventory/dead-stock/'+id+'/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:status})})
    .then(function(r){return r.json();}).then(function(){render();loadMetrics();});
}

var RUN_ENDPOINT={stock:'/api/inventory/reorder/run',reorder:'/api/inventory/reorder/run',
  dead:'/api/inventory/reorder/run',demand:'/api/inventory/true-demand/run',
  size:'/api/inventory/size-intel/run',test:'/api/inventory/test-batch/run'};
function runEngine(btn){
  var ep=RUN_ENDPOINT[TAB]||'/api/inventory/reorder/run';
  btn.disabled=true;var old=btn.innerHTML;btn.innerHTML='Calculating&hellip;';
  fetch(ep,{method:'POST'}).then(function(r){return r.json();}).then(function(d){
    btn.disabled=false;btn.innerHTML=old;
    if(d.error){alert('Failed: '+d.error);return;}
    loadMetrics();render();
  }).catch(function(e){btn.disabled=false;btn.innerHTML=old;alert('Failed: '+e.message);});
}

loadMetrics();render();
</script>
</body></html>"""
