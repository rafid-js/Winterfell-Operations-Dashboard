"""
Supply Chain — Flask routes (Phase 1).

UI pages (rendered with render_template_string, matching the rest of the app)
and the /api/sc/* JSON endpoints backing them.
"""
from flask import request, jsonify, render_template_string

from . import sc_bp, sc_login_required
from . import models


# ── HTML pages ──────────────────────────────────────────────────────────────

@sc_bp.route('/supply-chain')
@sc_login_required
def sc_list_page():
    return render_template_string(SC_LIST_HTML)


@sc_bp.route('/supply-chain/po/<po_id>')
@sc_login_required
def sc_detail_page(po_id):
    return render_template_string(SC_DETAIL_HTML)


# ── JSON API ────────────────────────────────────────────────────────────────

@sc_bp.route('/api/sc/pos')
@sc_login_required
def api_pos():
    try:
        status = request.args.get('status')
        result = models.get_po_list(status=status)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@sc_bp.route('/api/sc/suppliers')
@sc_login_required
def api_suppliers():
    try:
        return jsonify(models.get_suppliers())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@sc_bp.route('/api/sc/suppliers', methods=['POST'])
@sc_login_required
def api_create_supplier():
    try:
        data = request.get_json(force=True, silent=True) or {}
        sid = models.create_supplier(data)
        return jsonify({'id': sid, 'name': (data.get('name') or '').strip()})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@sc_bp.route('/api/sc/pos', methods=['POST'])
@sc_login_required
def api_create_po():
    try:
        data = request.get_json(force=True, silent=True) or {}
        po_id = models.create_po(data)
        return jsonify({'po_id': po_id})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@sc_bp.route('/api/sc/po/<po_id>')
@sc_login_required
def api_po_detail(po_id):
    try:
        po = models.get_po_detail(po_id)
        if po is None:
            return jsonify({'error': 'PO not found'}), 404
        timeline = models.get_po_timeline(po_id)
        return jsonify({'po': po, 'timeline': timeline})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@sc_bp.route('/api/sc/po/<po_id>/log', methods=['POST'])
@sc_login_required
def api_log_pm(po_id):
    try:
        data = request.get_json(force=True, silent=True) or {}
        result = models.log_pm_event(po_id, data)
        return jsonify({
            'success': True,
            'event_id': result['event_id'],
            'new_status': result['new_status'],
            'new_stage': result['new_stage'],
        })
    except ValueError as e:
        return jsonify({'success': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ── shared CSS (dark theme, matches the rest of the app) ─────────────────────

SC_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --header-bg:#161b22;--header-border:#30363d;
  --bg-page:#F6F8FA;--bg-card:#FFFFFF;--bg-inner:#F9FAFB;
  --text-primary:#1A1F2E;--text-secondary:#4A5568;--text-tertiary:#718096;
  --border:#E1E7EF;--track:#E1E7EF;
  --teal:#1D9E75;--amber:#EF9F27;--red:#E24B4A;--purple:#7F77DD;
}
body{background:var(--bg-page);color:var(--text-primary);
     font-family:Arial,sans-serif;min-height:100vh}

/* ── header / nav (KEEP DARK to match rest of app) ─────────────────────── */
header{background:var(--header-bg);border-bottom:1px solid var(--header-border);
       padding:16px 24px;display:flex;align-items:center;justify-content:space-between}
header h1{font-size:1.2rem;font-weight:700;color:#f0f6fc}
.header-right{display:flex;align-items:center;gap:16px}
.top-nav{display:flex;gap:4px}
.nav-link{color:#8b949e;font-size:.8rem;text-decoration:none;padding:4px 12px;
          border:1px solid transparent;border-radius:6px;transition:.2s}
.nav-link:hover{border-color:var(--header-border);color:#e6edf3}
.nav-link.active{border-color:var(--header-border);color:#e6edf3;background:#21262d}
.logout{color:#8b949e;font-size:.8rem;text-decoration:none;padding:4px 10px;
        border:1px solid var(--header-border);border-radius:6px}
.logout:hover{border-color:#8b949e;color:#e6edf3}

/* ── light body ────────────────────────────────────────────────────────── */
.container{max-width:1100px;margin:0 auto;padding:24px}
.page-head{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:1.25rem}
.page-title{font-size:18px;font-weight:500;color:var(--text-primary)}
.page-sub{color:var(--teal);font-size:11px;font-weight:400;margin-top:4px;display:flex;align-items:center;gap:6px}
.page-sub .dot{width:7px;height:7px;border-radius:50%;background:var(--teal)}
.mono{font-family:'Courier New',monospace}
.btn{padding:8px 16px;border-radius:8px;border:none;cursor:pointer;font-size:12px;font-weight:500;font-family:Arial,sans-serif;transition:.2s}
.btn-primary{background:#1A1F2E;color:#fff}
.btn-primary:hover{filter:brightness(1.15)}
.btn-ghost{background:#fff;color:var(--text-primary);border:0.5px solid var(--border)}
.btn-ghost:hover{border-color:var(--text-tertiary)}

.pills{display:flex;gap:8px;margin-bottom:1.25rem;flex-wrap:wrap;justify-content:flex-end}
.pill{padding:6px 14px;border-radius:20px;border:0.5px solid var(--border);background:#F6F8FA;
      color:var(--text-secondary);font-size:12px;cursor:pointer;transition:.2s}
.pill:hover{color:var(--text-primary)}
.pill.active{background:#E1F5EE;color:#085041;border:0.5px solid #5DCAA5}

.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-bottom:1.25rem}
.metric{background:#fff;border:0.5px solid var(--border);border-radius:12px;padding:1rem 1.25rem}
.metric .val{font-size:22px;font-weight:500;color:var(--text-primary)}
.metric .lbl{color:var(--text-tertiary);font-size:11px;font-weight:400;margin-top:4px;text-transform:uppercase;letter-spacing:.05em}

.po-card{background:#fff;border:0.5px solid var(--border);border-left:3px solid var(--teal);
         border-radius:12px;padding:0;margin-bottom:10px;overflow:hidden}
.po-card.status-delayed{border-left-color:var(--red)}
.po-card.status-atrisk{border-left-color:var(--amber)}
.po-card.status-active{border-left-color:var(--teal)}
.po-summary{padding:.85rem 1.25rem;cursor:pointer;display:grid;grid-template-columns:1fr auto;align-items:center;gap:18px}
.po-summary:hover{background:var(--bg-inner)}
.po-main{flex:1;min-width:0}
.po-line1{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.po-id{font-family:'Courier New',monospace;font-size:11px;color:var(--text-tertiary);font-weight:400}
.po-name{font-weight:500;color:var(--text-primary);font-size:14px}
.po-line2{color:var(--text-tertiary);font-size:11px;font-weight:400;margin-top:5px;line-height:1.55}

.badge{display:inline-block;padding:3px 11px;border-radius:20px;font-size:11px;font-weight:500}
.badge-ontrack{background:#E1F5EE;color:#085041;border:0.5px solid #5DCAA5}
.badge-atrisk{background:#FAEEDA;color:#633806;border:0.5px solid #EF9F27}
.badge-delayed{background:#FCEBEB;color:#791F1F;border:0.5px solid #F09595}

.po-due{text-align:right;font-size:10px;color:var(--text-tertiary);white-space:nowrap}
.po-due .d{color:var(--text-primary);font-weight:500;font-size:13px}

/* stage track */
.stages{display:flex;align-items:center;margin:10px 0 2px 0;background:#F9FAFB;border-radius:8px;padding:.5rem .75rem}
.stage-dot{width:10px;height:10px;border-radius:50%;border:1.5px solid #CBD5E0;background:#fff;flex:0 0 auto}
.stage-dot.done{background:#1D9E75;border-color:#1D9E75}
.stage-dot.active{background:#7F77DD;border-color:#7F77DD;box-shadow:0 0 0 3px #EEEDFE}
.stage-line{height:3px;flex:1;background:var(--track);border-radius:2px}
.stage-line.done{background:#1D9E75}
.stage-line.active{background:linear-gradient(90deg,#1D9E75,#7F77DD)}

.po-detail{display:none;padding:1rem 1.25rem;border-top:0.5px solid var(--border);background:#fff}
.po-detail.open{display:block}

.cost-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:0 0 16px 0}
.cost-box{background:var(--bg-inner);border:0.5px solid var(--border);border-radius:8px;padding:10px 12px}
.cost-box .l{font-size:10px;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:.05em}
.cost-box .v{font-size:14px;font-weight:500;color:var(--text-primary);margin-top:3px}

.mini-tl{margin:10px 0}
.mini-ev{display:flex;gap:10px;align-items:flex-start;padding:7px 0;font-size:12px}
.src-ico{width:22px;height:22px;border-radius:50%;display:flex;align-items:center;justify-content:center;
         font-size:.62rem;font-weight:700;flex:0 0 auto}
.src-brain{background:#E1F5EE;color:#085041}
.src-pm{background:#EEEDFE;color:#3C3489}
.src-finance{background:#FAEEDA;color:#633806}
.src-supplier{background:#E6F1FB;color:#0C447C}
.src-alert{background:#FCEBEB;color:#791F1F}
.ev-title{color:var(--text-primary);font-weight:500;font-size:12px}
.ev-note{color:var(--text-secondary);font-size:11px;margin-top:2px;line-height:1.55}
.ev-meta{color:var(--text-tertiary);font-size:11px;margin-top:2px}
.detail-actions{display:flex;gap:8px;margin-top:14px}
.empty{text-align:center;color:var(--text-tertiary);font-size:12px;padding:50px 20px}

/* modal */
.modal-bg{display:none;position:fixed;inset:0;background:rgba(26,31,46,.45);z-index:100;
          align-items:flex-start;justify-content:center;overflow-y:auto;padding:40px 16px}
.modal-bg.open{display:flex}
.modal{background:#fff;border:0.5px solid var(--border);border-radius:12px;
       width:min(560px,96vw);box-shadow:0 12px 40px rgba(26,31,46,.18)}
.modal-head{padding:16px 20px;border-bottom:0.5px solid var(--border);
            display:flex;align-items:center;justify-content:space-between}
.modal-head h3{font-size:14px;font-weight:500;color:var(--text-primary)}
.modal-close{background:none;border:none;color:var(--text-tertiary);font-size:1.5rem;cursor:pointer;line-height:1}
.modal-body{padding:18px 20px}
.field{margin-bottom:14px}
.field label{display:block;font-size:11px;color:var(--text-secondary);margin-bottom:5px}
.field input,.field textarea{width:100%;background:var(--bg-inner);border:0.5px solid var(--border);
       border-radius:8px;padding:9px 11px;color:var(--text-primary);font-size:12px;font-family:Arial,sans-serif}
.field input:focus,.field textarea:focus{outline:none;border-color:var(--purple)}
.field textarea{min-height:64px;resize:vertical;line-height:1.55}
.row2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.form-err{color:#791F1F;background:#FCEBEB;border:0.5px solid #F09595;border-radius:8px;
          padding:8px 11px;font-size:11px;margin-bottom:12px;display:none}
.form-err.show{display:block}
"""


# ── PO list page ────────────────────────────────────────────────────────────

SC_LIST_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Winterfell — Supply Chain</title>
<style>""" + SC_CSS + """</style>
</head>
<body>
<header>
  <h1>&#9876;&#65039; Winterfell Operations</h1>
  <div class="header-right">
    <nav class="top-nav">
      <a href="/" class="nav-link">Operations</a>
      <a href="/products" class="nav-link">Products</a>
      <a href="/customers" class="nav-link">Customers</a>
      <a href="/orders" class="nav-link">Orders</a>
      <a href="/supply-chain" class="nav-link active">Supply Chain</a>
    </nav>
    <a href="/logout" class="logout">Logout</a>
  </div>
</header>
<div class="container">
  <div class="page-head">
    <div>
      <div class="page-title">Supply Chain / Active POs</div>
      <div class="page-sub"><span class="dot"></span> Brain connected</div>
    </div>
    <button class="btn btn-primary" onclick="openModal()">+ New PO</button>
  </div>

  <div class="pills" id="pills">
    <div class="pill active" data-status="all" onclick="setFilter(this)">All</div>
    <div class="pill" data-status="active" onclick="setFilter(this)">On track</div>
    <div class="pill" data-status="at_risk" onclick="setFilter(this)">At risk</div>
    <div class="pill" data-status="delayed" onclick="setFilter(this)">Delayed</div>
  </div>

  <div class="metrics">
    <div class="metric"><div class="val" id="m-active">&mdash;</div><div class="lbl">Active POs</div></div>
    <div class="metric"><div class="val" id="m-units">&mdash;</div><div class="lbl">Units in production</div></div>
    <div class="metric"><div class="val" id="m-capital">&mdash;</div><div class="lbl">Capital deployed</div></div>
    <div class="metric"><div class="val" id="m-delayed">&mdash;</div><div class="lbl">Delayed POs</div></div>
  </div>

  <div id="po-list"><div class="empty">Loading purchase orders&hellip;</div></div>
</div>

<!-- New PO modal -->
<div class="modal-bg" id="modal" onclick="modalBgClick(event)">
  <div class="modal">
    <div class="modal-head">
      <h3>New Purchase Order</h3>
      <button class="modal-close" onclick="closeModal()">&times;</button>
    </div>
    <div class="modal-body">
      <div class="form-err" id="form-err"></div>
      <div class="field">
        <label>Product Name *</label>
        <input id="f-product" type="text" placeholder="e.g. Winter Hoodie - Charcoal">
      </div>
      <div class="field">
        <label>SKU (optional)</label>
        <input id="f-sku" type="text" placeholder="e.g. HOOD-CHAR-M">
      </div>
      <div class="field">
        <label>Supplier</label>
        <input id="f-supplier" type="text" placeholder="Existing name links, new name creates">
      </div>
      <div class="row2">
        <div class="field">
          <label>Quantity *</label>
          <input id="f-qty" type="number" min="1" placeholder="0">
        </div>
        <div class="field">
          <label>Unit Cost (BDT)</label>
          <input id="f-cost" type="number" min="0" step="0.01" placeholder="0">
        </div>
      </div>
      <div class="field">
        <label>Due Date *</label>
        <input id="f-due" type="date">
      </div>
      <div class="field">
        <label>Notes</label>
        <textarea id="f-notes" placeholder="Anything the team should know&hellip;"></textarea>
      </div>
      <button class="btn btn-primary" style="width:100%" onclick="submitPo()">Create PO</button>
    </div>
  </div>
</div>

<script>
var STAGES = ['PO Issued', 'Fabric', 'Trims', 'Sewing', 'QC', 'Delivered'];
var currentFilter = 'all';

function fmtBDT(n){
  if(n === null || n === undefined || isNaN(n)) return '&#2547;0';
  var v = Math.round(Number(n));
  return '&#2547;' + v.toString().replace(/\\B(?=(\\d{3})+(?!\\d))/g, ',');
}
function fmtLakh(n){
  if(n === null || n === undefined || isNaN(n)) return '&#2547;0';
  var v = Number(n);
  if(v >= 100000){ return '&#2547;' + (v/100000).toFixed(1) + 'L'; }
  return fmtBDT(v);
}
function esc(s){
  if(s === null || s === undefined) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function statusClass(st){
  if(st === 'Delayed') return 'delayed';
  if(st === 'At Risk') return 'atrisk';
  return 'active';
}
function badgeFor(st){
  if(st === 'Delayed') return '<span class="badge badge-delayed">Delayed</span>';
  if(st === 'At Risk') return '<span class="badge badge-atrisk">At risk</span>';
  return '<span class="badge badge-ontrack">On track</span>';
}
function dueText(po){
  var d = po.due_date ? po.due_date.substring(0,10) : '&mdash;';
  var od = po.days_overdue || 0;
  var rem = '';
  if(od > 0){ rem = '<div style="color:#F7C1C1">' + od + ' days overdue</div>'; }
  return '<div class="d">' + esc(d) + '</div><div>due date</div>' + rem;
}
function stagesHtml(currentStage){
  var idx = STAGES.indexOf(currentStage);
  var html = '<div class="stages">';
  for(var i=0; i<STAGES.length; i++){
    var cls = 'stage-dot';
    if(i < idx) cls += ' done';
    else if(i === idx) cls += ' active';
    html += '<span class="' + cls + '" title="' + esc(STAGES[i]) + '"></span>';
    if(i < STAGES.length - 1){
      var lcls = 'stage-line';
      if(i < idx) lcls += ' done';
      else if(i === idx) lcls += ' active';
      html += '<span class="' + lcls + '"></span>';
    }
  }
  html += '</div>';
  return html;
}

function loadPos(){
  var url = '/api/sc/pos';
  if(currentFilter && currentFilter !== 'all'){ url += '?status=' + currentFilter; }
  fetch(url).then(function(r){ return r.json(); }).then(function(data){
    if(data.error){ document.getElementById('po-list').innerHTML = '<div class="empty">Error: ' + esc(data.error) + '</div>'; return; }
    renderSummary(data.summary);
    renderList(data.pos);
  }).catch(function(e){
    document.getElementById('po-list').innerHTML = '<div class="empty">Failed to load: ' + esc(e.message) + '</div>';
  });
}

function renderSummary(s){
  if(!s) return;
  document.getElementById('m-active').innerHTML = s.total;
  document.getElementById('m-units').innerHTML = (s.units_in_production || 0).toString().replace(/\\B(?=(\\d{3})+(?!\\d))/g, ',');
  document.getElementById('m-capital').innerHTML = fmtLakh(s.capital_deployed);
  document.getElementById('m-delayed').innerHTML = s.delayed;
}

function renderList(pos){
  var wrap = document.getElementById('po-list');
  if(!pos || pos.length === 0){ wrap.innerHTML = '<div class="empty">No purchase orders match this filter.</div>'; return; }
  var html = '';
  for(var i=0; i<pos.length; i++){
    var po = pos[i];
    var sc = statusClass(po.po_status);
    var sub = esc(po.supplier_name || 'No supplier') + ' &middot; ' + (po.quantity_ordered || 0) + ' pcs';
    if(po.notes){ sub += ' &middot; ' + esc(po.notes); }
    html += '<div class="po-card status-' + sc + '" id="card-' + esc(po.po_id) + '">';
    html += '  <div class="po-summary" onclick="toggleCard(\\'' + esc(po.po_id) + '\\')">';
    html += '    <div class="po-main">';
    html += '      <div class="po-line1"><span class="po-id mono">' + esc(po.po_id) + '</span>';
    html += '        <span class="po-name">' + esc(po.product_name || '') + '</span>' + badgeFor(po.po_status) + '</div>';
    html += '      <div class="po-line2">' + sub + '</div>';
    html += '      ' + stagesHtml(po.current_stage);
    html += '    </div>';
    html += '    <div class="po-due">' + dueText(po) + '</div>';
    html += '  </div>';
    html += '  <div class="po-detail" id="detail-' + esc(po.po_id) + '"></div>';
    html += '</div>';
  }
  wrap.innerHTML = html;
}

function toggleCard(poId){
  var d = document.getElementById('detail-' + poId);
  if(!d) return;
  if(d.classList.contains('open')){ d.classList.remove('open'); d.innerHTML = ''; return; }
  d.classList.add('open');
  d.innerHTML = '<div style="padding:14px 0;color:#8b949e">Loading&hellip;</div>';
  fetch('/api/sc/po/' + encodeURIComponent(poId)).then(function(r){ return r.json(); }).then(function(data){
    if(data.error){ d.innerHTML = '<div style="padding:14px 0;color:#F7C1C1">' + esc(data.error) + '</div>'; return; }
    d.innerHTML = detailHtml(data.po, data.timeline);
  }).catch(function(e){
    d.innerHTML = '<div style="padding:14px 0;color:#F7C1C1">Failed: ' + esc(e.message) + '</div>';
  });
}

function srcClass(t){
  if(t === 'brain') return 'src-brain';
  if(t === 'pm') return 'src-pm';
  if(t === 'finance') return 'src-finance';
  if(t === 'supplier') return 'src-supplier';
  return 'src-alert';
}
function srcLabel(t){
  if(t === 'brain') return 'B';
  if(t === 'pm') return 'PM';
  if(t === 'finance') return '&#2547;';
  if(t === 'supplier') return 'S';
  return '!';
}

function detailHtml(po, timeline){
  var h = '<div class="cost-grid">';
  h += '<div class="cost-box"><div class="l">Unit cost</div><div class="v">' + fmtBDT(po.unit_cost_bdt) + '</div></div>';
  h += '<div class="cost-box"><div class="l">Total cost</div><div class="v">' + fmtBDT(po.total_cost_bdt) + '</div></div>';
  h += '<div class="cost-box"><div class="l">Advance paid</div><div class="v">' + fmtBDT(po.advance_paid_bdt) + '</div></div>';
  h += '<div class="cost-box"><div class="l">Balance due</div><div class="v">' + fmtBDT(po.balance_due_bdt) + '</div></div>';
  h += '</div>';
  h += '<div class="mini-tl">';
  var evs = timeline || [];
  var recent = evs.slice(-4);
  if(recent.length === 0){ h += '<div style="color:#8b949e;font-size:.8rem">No timeline events yet.</div>'; }
  for(var i=recent.length-1; i>=0; i--){
    var ev = recent[i];
    var tcls = ev.is_alert ? 'src-alert' : srcClass(ev.source_type);
    var tlabel = ev.is_alert ? '!' : srcLabel(ev.source_type);
    h += '<div class="mini-ev"><div class="src-ico ' + tcls + '">' + tlabel + '</div>';
    h += '<div><div class="ev-title">' + esc(ev.event_title) + '</div>';
    if(ev.event_note){ h += '<div class="ev-note">' + esc(ev.event_note) + '</div>'; }
    h += '<div class="ev-meta">' + esc((ev.event_date || '').substring(0,16).replace('T',' ')) + ' &middot; ' + esc(ev.logged_by || '') + '</div>';
    h += '</div></div>';
  }
  h += '</div>';
  h += '<div class="detail-actions">';
  h += '<a class="btn btn-ghost" href="/supply-chain/po/' + encodeURIComponent(po.po_id) + '">View Timeline &rarr;</a>';
  h += '</div>';
  return h;
}

function setFilter(el){
  var pills = document.querySelectorAll('#pills .pill');
  for(var i=0;i<pills.length;i++){ pills[i].classList.remove('active'); }
  el.classList.add('active');
  currentFilter = el.getAttribute('data-status');
  loadPos();
}

function openModal(){ document.getElementById('modal').classList.add('open'); }
function closeModal(){ document.getElementById('modal').classList.remove('open'); document.getElementById('form-err').classList.remove('show'); }
function modalBgClick(e){ if(e.target === document.getElementById('modal')){ closeModal(); } }

function submitPo(){
  var err = document.getElementById('form-err');
  err.classList.remove('show');
  var product = document.getElementById('f-product').value.trim();
  var qty = document.getElementById('f-qty').value.trim();
  var due = document.getElementById('f-due').value.trim();
  if(!product){ err.innerHTML = 'Product name is required.'; err.classList.add('show'); return; }
  if(!qty || Number(qty) <= 0){ err.innerHTML = 'Quantity must be greater than zero.'; err.classList.add('show'); return; }
  if(!due){ err.innerHTML = 'Due date is required.'; err.classList.add('show'); return; }
  var body = {
    product_name: product,
    sku: document.getElementById('f-sku').value.trim(),
    supplier_name: document.getElementById('f-supplier').value.trim(),
    quantity_ordered: Number(qty),
    unit_cost_bdt: document.getElementById('f-cost').value.trim(),
    due_date: due,
    notes: document.getElementById('f-notes').value.trim()
  };
  fetch('/api/sc/pos', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  }).then(function(r){ return r.json(); }).then(function(data){
    if(data.error){ err.innerHTML = esc(data.error); err.classList.add('show'); return; }
    closeModal();
    document.getElementById('f-product').value = '';
    document.getElementById('f-sku').value = '';
    document.getElementById('f-supplier').value = '';
    document.getElementById('f-qty').value = '';
    document.getElementById('f-cost').value = '';
    document.getElementById('f-due').value = '';
    document.getElementById('f-notes').value = '';
    loadPos();
  }).catch(function(e){ err.innerHTML = 'Failed: ' + esc(e.message); err.classList.add('show'); });
}

loadPos();
</script>
</body>
</html>"""


# ── PO detail / timeline page ───────────────────────────────────────────────

SC_DETAIL_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Winterfell — PO Timeline</title>
<style>""" + SC_CSS + """
.crumb{color:var(--text-secondary);font-size:12px;margin-bottom:18px}
.crumb a{color:var(--purple);text-decoration:none}
.crumb a:hover{text-decoration:underline}
.banner{background:#fff;border:0.5px solid var(--border);border-radius:12px;
        padding:1rem 1.25rem;display:flex;justify-content:space-between;gap:18px;margin-bottom:1.25rem}
.banner .ttl{font-size:14px;font-weight:500;color:var(--text-primary)}
.banner .meta{color:var(--text-tertiary);font-size:11px;margin-top:6px;line-height:1.55}
.banner .right{text-align:right;font-size:10px;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:.05em}
.banner .right .big{font-size:14px;color:var(--text-primary);font-weight:500;text-transform:none;letter-spacing:0}
.bigstages{background:#fff;border:0.5px solid var(--border);border-radius:12px;
           padding:24px 28px;margin-bottom:1.25rem}
.bs-row{display:flex;align-items:center}
.bs-dot{width:14px;height:14px;border-radius:50%;border:1.5px solid #CBD5E0;background:#fff;flex:0 0 auto}
.bs-dot.done{background:#1D9E75;border-color:#1D9E75}
.bs-dot.active{background:#7F77DD;border-color:#7F77DD;box-shadow:0 0 0 3px #EEEDFE}
.bs-line{height:3px;flex:1;background:var(--track);border-radius:2px}
.bs-line.done{background:#1D9E75}
.bs-line.active{background:linear-gradient(90deg,#1D9E75,#7F77DD)}
.bs-labels{display:flex;margin-top:10px}
.bs-lbl{flex:1;text-align:center;font-size:10px;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:.05em}
.bs-lbl:first-child{text-align:left}
.bs-lbl:last-child{text-align:right}
.bs-lbl.active{color:var(--purple);font-weight:500}
.bs-lbl.done{color:var(--teal)}
.section-title{font-size:11px;font-weight:500;color:var(--text-secondary);text-transform:uppercase;letter-spacing:.07em;margin:0 0 14px 0}
.tl{background:#fff;border:0.5px solid var(--border);border-radius:12px;padding:1rem 1.25rem;margin-bottom:1.25rem}
.tl-ev{display:flex;gap:14px;padding:12px 0;border-bottom:0.5px solid var(--border)}
.tl-ev:last-child{border-bottom:none}
.tl-body{flex:1;border-left:2px solid var(--border);padding-left:12px}
.tl-body.b-brain{border-left-color:#1D9E75}
.tl-body.b-pm{border-left-color:#7F77DD}
.tl-body.b-finance{border-left-color:#BA7517}
.tl-body.b-supplier{border-left-color:#378ADD}
.tl-body.b-alert{border-left-color:var(--red)}
.tl-title{color:var(--text-primary);font-weight:500;font-size:12px}
.tl-note{color:var(--text-secondary);font-size:11px;margin-top:3px;line-height:1.55}
.tl-amt{color:#BA7517;font-weight:500;font-size:12px;margin-top:3px}
.tl-foot{display:flex;gap:10px;align-items:center;margin-top:5px}
.tl-date{color:var(--text-tertiary);font-size:11px}
.src-badge{font-size:10px;padding:2px 8px;border-radius:12px;text-transform:uppercase;letter-spacing:.05em}
.tl-pending{display:flex;gap:14px;padding:10px 0;opacity:.55}
.tl-pending .pdot{width:18px;height:18px;border-radius:50%;border:1.5px dashed #CBD5E0;flex:0 0 auto}
.logform{background:#fff;border:0.5px solid var(--border);border-radius:12px;padding:1rem 1.25rem}
.opt-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
.opt{padding:7px 14px;border-radius:8px;border:0.5px solid var(--border);background:#F6F8FA;
     color:var(--text-secondary);font-size:12px;cursor:pointer;transition:.2s}
.opt:hover{color:var(--text-primary)}
.opt.sel{background:#EEEDFE;color:#3C3489;border:0.5px solid #7F77DD}
#opt-status .opt.sel{background:#E1F5EE;color:#085041;border:0.5px solid #5DCAA5}
#opt-by .opt.sel{background:#E1F5EE;color:#085041;border:0.5px solid #5DCAA5}
.flabel{font-size:11px;color:var(--text-secondary);margin-bottom:7px;display:block}
.success-box{background:#E1F5EE;border:0.5px solid #5DCAA5;border-radius:8px;padding:18px 20px;color:#085041}
.success-box h4{color:#085041;font-size:14px;font-weight:500;margin-bottom:10px;display:flex;align-items:center;gap:8px}
.success-box ul{margin:8px 0 14px 18px;font-size:12px;line-height:1.55}
</style>
</head>
<body>
<header>
  <h1>&#9876;&#65039; Winterfell Operations</h1>
  <div class="header-right">
    <nav class="top-nav">
      <a href="/" class="nav-link">Operations</a>
      <a href="/products" class="nav-link">Products</a>
      <a href="/customers" class="nav-link">Customers</a>
      <a href="/orders" class="nav-link">Orders</a>
      <a href="/supply-chain" class="nav-link active">Supply Chain</a>
    </nav>
    <a href="/logout" class="logout">Logout</a>
  </div>
</header>
<div class="container">
  <div class="crumb"><a href="/supply-chain">Supply Chain</a> / <span id="cr-po" class="mono">&hellip;</span> / Timeline</div>

  <div id="banner" class="banner"><div><div class="ttl">Loading&hellip;</div></div></div>

  <div class="bigstages" id="bigstages"></div>

  <div class="section-title">Timeline</div>
  <div class="tl" id="timeline"><div style="color:#8b949e">Loading&hellip;</div></div>

  <div class="logform" id="logform">
    <div class="section-title">Log production milestone</div>
    <div id="log-content">
      <span class="flabel">Stage</span>
      <div class="opt-row" id="opt-stage">
        <div class="opt" data-v="Fabric" onclick="pick(this,'stage')">Fabric</div>
        <div class="opt" data-v="Trims" onclick="pick(this,'stage')">Trims</div>
        <div class="opt" data-v="Sewing" onclick="pick(this,'stage')">Sewing</div>
        <div class="opt" data-v="QC" onclick="pick(this,'stage')">QC</div>
        <div class="opt" data-v="Delivery" onclick="pick(this,'stage')">Delivery</div>
        <div class="opt" data-v="Other" onclick="pick(this,'stage')">Other</div>
      </div>
      <span class="flabel">Status</span>
      <div class="opt-row" id="opt-status">
        <div class="opt sel" data-v="In progress" onclick="pick(this,'status')">In progress</div>
        <div class="opt" data-v="Completed" onclick="pick(this,'status')">Completed</div>
        <div class="opt" data-v="Issue flagged" onclick="pick(this,'status')">Issue flagged</div>
        <div class="opt" data-v="Delayed" onclick="pick(this,'status')">Delayed</div>
      </div>
      <div class="field">
        <span class="flabel">Note</span>
        <textarea id="l-note" placeholder="e.g. Cutting done for all colours&hellip;"></textarea>
      </div>
      <div class="row2">
        <div class="field">
          <span class="flabel">Estimated completion</span>
          <input id="l-est" type="date">
        </div>
        <div class="field">
          <span class="flabel">Units completed</span>
          <input id="l-units" type="number" min="0" value="0">
        </div>
      </div>
      <span class="flabel">Logged by</span>
      <div class="opt-row" id="opt-by">
        <div class="opt sel" data-v="Production Manager" onclick="pick(this,'by')">Production Manager</div>
        <div class="opt" data-v="Warehouse (GRN)" onclick="pick(this,'by')">Warehouse (GRN)</div>
        <div class="opt" data-v="Rafid Hasan" onclick="pick(this,'by')">Rafid Hasan</div>
      </div>
      <div class="form-err" id="log-err"></div>
      <button class="btn btn-primary" onclick="saveLog()">Save to Brain</button>
    </div>
  </div>
</div>

<script>
var STAGES = ['PO Issued', 'Fabric', 'Trims', 'Sewing', 'QC', 'Delivered'];
var STAGE_PCT = {'PO Issued':0,'Fabric':17,'Trims':33,'Sewing':50,'QC':83,'Delivered':100};
var sel = {stage:'', status:'In progress', by:'Production Manager'};
var POID = '';

(function(){
  var parts = window.location.pathname.split('/');
  POID = decodeURIComponent(parts[parts.length - 1]);
})();

function esc(s){
  if(s === null || s === undefined) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function fmtBDT(n){
  if(n === null || n === undefined || isNaN(n)) return '&#2547;0';
  var v = Math.round(Number(n));
  return '&#2547;' + v.toString().replace(/\\B(?=(\\d{3})+(?!\\d))/g, ',');
}
function srcClass(t){
  if(t === 'brain') return 'brain';
  if(t === 'pm') return 'pm';
  if(t === 'finance') return 'finance';
  if(t === 'supplier') return 'supplier';
  return 'alert';
}
function srcBadgeStyle(t){
  if(t === 'brain') return 'background:#04342C;color:#9FE1CB';
  if(t === 'pm') return 'background:#26215C;color:#CECBF6';
  if(t === 'finance') return 'background:#412402;color:#FAC775';
  if(t === 'supplier') return 'background:#0C1F35;color:#7EB8F5';
  return 'background:#501313;color:#F7C1C1';
}
function srcLabel(t){
  if(t === 'brain') return 'B';
  if(t === 'pm') return 'PM';
  if(t === 'finance') return '&#2547;';
  if(t === 'supplier') return 'S';
  return '!';
}

function loadPo(){
  document.getElementById('cr-po').innerHTML = esc(POID);
  fetch('/api/sc/po/' + encodeURIComponent(POID)).then(function(r){ return r.json(); }).then(function(data){
    if(data.error){
      document.getElementById('banner').innerHTML = '<div><div class="ttl">' + esc(data.error) + '</div></div>';
      return;
    }
    renderBanner(data.po);
    renderBigStages(data.po.current_stage, data.po.expected_delivery || data.po.due_date);
    renderTimeline(data.timeline || [], data.po.current_stage, data.po.expected_delivery);
  }).catch(function(e){
    document.getElementById('banner').innerHTML = '<div><div class="ttl">Failed: ' + esc(e.message) + '</div></div>';
  });
}

function renderBanner(po){
  var arrive = po.actual_delivery || po.expected_delivery || po.due_date;
  arrive = arrive ? arrive.substring(0,10) : '&mdash;';
  var h = '<div>';
  h += '<div class="ttl"><span class="mono" style="color:#7F77DD">' + esc(po.po_id) + '</span> &nbsp; ' + esc(po.product_name || '') + '</div>';
  h += '<div class="meta">' + esc(po.supplier_name || 'No supplier') + ' &middot; ' + (po.quantity_ordered || 0) + ' pcs &middot; stage: ' + esc(po.current_stage) + '</div>';
  h += '</div>';
  h += '<div class="right"><div>Expected arrival</div><div class="big">' + esc(arrive) + '</div></div>';
  document.getElementById('banner').innerHTML = h;
}

function renderBigStages(currentStage, expected){
  var idx = STAGES.indexOf(currentStage);
  var h = '<div class="bs-row">';
  for(var i=0; i<STAGES.length; i++){
    var cls = 'bs-dot';
    if(i < idx) cls += ' done';
    else if(i === idx) cls += ' active';
    h += '<span class="' + cls + '"></span>';
    if(i < STAGES.length - 1){
      var lcls = 'bs-line';
      if(i < idx) lcls += ' done';
      else if(i === idx) lcls += ' active';
      h += '<span class="' + lcls + '"></span>';
    }
  }
  h += '</div><div class="bs-labels">';
  for(var j=0; j<STAGES.length; j++){
    var lc = 'bs-lbl';
    if(j < idx) lc += ' done';
    else if(j === idx) lc += ' active';
    h += '<span class="' + lc + '">' + esc(STAGES[j]) + '</span>';
  }
  h += '</div>';
  document.getElementById('bigstages').innerHTML = h;
}

function renderTimeline(events, currentStage, expected){
  var h = '';
  if(events.length === 0){ h += '<div style="color:#8b949e">No events yet.</div>'; }
  for(var i=0; i<events.length; i++){
    var ev = events[i];
    var stype = ev.is_alert ? 'alert' : ev.source_type;
    var bcls = ev.is_alert ? 'alert' : srcClass(ev.source_type);
    h += '<div class="tl-ev">';
    h += '<div class="src-ico ' + ('src-' + bcls) + '">' + (ev.is_alert ? '!' : srcLabel(ev.source_type)) + '</div>';
    h += '<div class="tl-body b-' + bcls + '">';
    h += '<div class="tl-title">' + esc(ev.event_title) + '</div>';
    if(ev.event_note){ h += '<div class="tl-note">' + esc(ev.event_note) + '</div>'; }
    if(ev.amount_bdt){ h += '<div class="tl-amt">' + fmtBDT(ev.amount_bdt) + '</div>'; }
    h += '<div class="tl-foot">';
    h += '<span class="tl-date">' + esc((ev.event_date || '').substring(0,16).replace('T',' ')) + '</span>';
    h += '<span class="src-badge" style="' + srcBadgeStyle(stype) + '">' + esc(ev.logged_by || ev.source_type) + '</span>';
    h += '</div></div></div>';
  }
  // Pending stages greyed out.
  var idx = STAGES.indexOf(currentStage);
  for(var k=idx+1; k<STAGES.length; k++){
    var waitTxt = 'Waiting';
    if(expected){ waitTxt += ' &mdash; expected ' + esc(expected.substring(0,10)); }
    h += '<div class="tl-pending"><div class="pdot"></div><div class="tl-body"><div class="tl-title">' + esc(STAGES[k]) + '</div><div class="tl-note">' + waitTxt + '</div></div></div>';
  }
  document.getElementById('timeline').innerHTML = h;
}

function pick(el, group){
  var parent = el.parentNode;
  var opts = parent.querySelectorAll('.opt');
  for(var i=0;i<opts.length;i++){ opts[i].classList.remove('sel'); }
  el.classList.add('sel');
  sel[group] = el.getAttribute('data-v');
}

function saveLog(){
  var err = document.getElementById('log-err');
  err.classList.remove('show');
  if(!sel.stage){ err.innerHTML = 'Please select a stage.'; err.classList.add('show'); return; }
  var body = {
    stage: sel.stage,
    status: sel.status,
    note: document.getElementById('l-note').value.trim(),
    est_date: document.getElementById('l-est').value.trim(),
    units_done: Number(document.getElementById('l-units').value.trim() || 0),
    logged_by: sel.by
  };
  fetch('/api/sc/po/' + encodeURIComponent(POID) + '/log', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body)
  }).then(function(r){ return r.json(); }).then(function(data){
    if(!data.success){ err.innerHTML = esc(data.error || 'Failed to save.'); err.classList.add('show'); return; }
    showSuccess(data);
    loadPo();
  }).catch(function(e){ err.innerHTML = 'Failed: ' + esc(e.message); err.classList.add('show'); });
}

function showSuccess(data){
  var h = '<div class="success-box"><h4>&#10003; Saved to Brain</h4><ul>';
  h += '<li>Event logged: ' + esc(data.event_id) + '</li>';
  h += '<li>Stage now: ' + esc(data.new_stage) + '</li>';
  h += '<li>Status now: ' + esc(data.new_status) + '</li>';
  h += '</ul>';
  h += '<button class="btn btn-ghost" onclick="window.location.href=\\'/supply-chain\\'">Back to all POs</button> ';
  h += '<button class="btn btn-primary" onclick="resetForm()">Log another</button>';
  h += '</div>';
  document.getElementById('log-content').innerHTML = h;
}

function resetForm(){ window.location.reload(); }

loadPo();
</script>
</body>
</html>"""
