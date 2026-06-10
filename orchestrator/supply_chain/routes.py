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


@sc_bp.route('/api/sc/po/<po_id>', methods=['PUT'])
@sc_login_required
def api_update_po(po_id):
    try:
        data = request.get_json(force=True, silent=True) or {}
        models.update_po(po_id, data)
        return jsonify({'success': True})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@sc_bp.route('/api/sc/po/<po_id>', methods=['DELETE'])
@sc_login_required
def api_delete_po(po_id):
    try:
        models.delete_po(po_id)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@sc_bp.route('/api/sc/po/<po_id>/stock-received', methods=['POST'])
@sc_login_required
def api_stock_received(po_id):
    try:
        data = request.get_json(force=True, silent=True) or {}
        result = models.receive_po_stock(po_id, data)
        return jsonify(result)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@sc_bp.route('/api/sc/po/<po_id>/waiting-orders')
@sc_login_required
def api_waiting_orders(po_id):
    try:
        data = models.get_waiting_orders(po_id)
        if data is None:
            return jsonify({'error': 'PO not found'}), 404
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@sc_bp.route('/api/sc/po/<po_id>/link-so', methods=['POST'])
@sc_login_required
def api_link_so(po_id):
    try:
        data = request.get_json(force=True, silent=True) or {}
        so = (data.get('so_number') or '').strip()
        if not so:
            return jsonify({'error': 'so_number required'}), 400
        models.link_so_to_po(po_id, so)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@sc_bp.route('/api/sc/po/<po_id>/unlink-so', methods=['POST'])
@sc_login_required
def api_unlink_so(po_id):
    try:
        data = request.get_json(force=True, silent=True) or {}
        so = (data.get('so_number') or '').strip()
        if not so:
            return jsonify({'error': 'so_number required'}), 400
        models.unlink_so_from_po(po_id, so)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@sc_bp.route('/supply-chain/suppliers')
@sc_login_required
def sc_suppliers_page():
    return render_template_string(SC_SUPPLIERS_HTML)


@sc_bp.route('/api/sc/suppliers/<int:supplier_id>')
@sc_login_required
def api_supplier_detail(supplier_id):
    try:
        data = models.get_supplier_detail(supplier_id)
        if data is None:
            return jsonify({'error': 'Supplier not found'}), 404
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@sc_bp.route('/supply-chain/po/<po_id>/waiting-orders')
@sc_login_required
def sc_waiting_orders_page(po_id):
    return render_template_string(SC_WAITING_ORDERS_HTML)


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
.logout:hover{border-color:#8b949e;color:#e6edf3}
.ham{display:none;flex-direction:column;gap:5px;cursor:pointer;background:none;border:none;padding:4px}
.ham span{width:20px;height:2px;background:#8b949e;border-radius:2px;display:block}
.mob-nav{display:none;position:absolute;top:100%;left:0;right:0;background:var(--header-bg);
         border-bottom:1px solid var(--header-border);padding:10px 16px;
         flex-direction:column;gap:4px;z-index:49}
.mob-nav a{color:#8b949e;font-size:.9rem;text-decoration:none;padding:8px 12px;border-radius:6px;display:block}
.mob-nav a:hover,.mob-nav a.active{color:#e6edf3;background:#21262d}
@media(max-width:700px){
  header{grid-template-columns:1fr auto;grid-template-areas:'brand actions';position:relative}
  .top-nav{display:none!important}
  .ham{display:flex}
  .mob-nav.open{display:flex}
  .container{padding:14px 12px}
  .tbl-wrap,.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
  .bigstages{padding:16px}
  .banner{flex-direction:column}
}

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
.btn-danger{background:#fff;color:#791F1F;border:0.5px solid #F5C6C6}
.btn-danger:hover{background:#FCEBEB;border-color:#E24B4A}

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
.stages{margin:10px 0 4px 0;background:#F9FAFB;border-radius:8px;padding:.5rem .75rem}
.st-row{display:flex;align-items:center}
.stage-dot{width:16px;height:16px;border-radius:50%;border:1.5px solid #CBD5E0;background:#fff;flex:0 0 auto;display:flex;align-items:center;justify-content:center}
.stage-dot.done{background:#1D9E75;border-color:#1D9E75}
.stage-dot.active{background:#7F77DD;border-color:#7F77DD;box-shadow:0 0 0 3px #EEEDFE}
.stage-line{height:3px;flex:1;background:var(--track);border-radius:2px}
.stage-line.done{background:#1D9E75}
.stage-line.active{background:linear-gradient(90deg,#1D9E75,#7F77DD)}
.st-lbls{display:flex;margin-top:7px}
.st-lbl{flex:1;text-align:center;font-size:9px;color:var(--text-tertiary)}
.st-lbl:first-child{text-align:left}
.st-lbl:last-child{text-align:right}
.st-lbl.done{color:var(--teal)}
.st-lbl.active{color:var(--purple);font-weight:600}

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


# ── Supplier Scoreboard page ─────────────────────────────────────────────────

SC_SUPPLIERS_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Winterfell — Suppliers</title>
<style>""" + SC_CSS + """
.sup-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:12px;margin-bottom:1.5rem}
.sup-card{background:#fff;border:0.5px solid var(--border);border-radius:12px;padding:1rem 1.25rem;cursor:pointer;transition:.2s}
.sup-card:hover{border-color:var(--text-tertiary);box-shadow:0 2px 8px rgba(26,31,46,.07)}
.sup-card.preferred{border-left:3px solid #1D9E75}
.sup-card.blacklisted{border-left:3px solid #E24B4A;opacity:.75}
.sup-name{font-size:14px;font-weight:600;color:var(--text-primary)}
.sup-loc{font-size:11px;color:var(--text-tertiary);margin-bottom:10px}
.sup-tags{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:12px;min-height:20px}
.sup-tag{font-size:10px;padding:2px 8px;border-radius:12px;background:#F1EFE8;color:#444441;border:0.5px solid #D3D1C7}
.sup-score-row{display:flex;align-items:flex-end;gap:10px;margin-bottom:10px}
.sup-score-num{font-size:28px;font-weight:700;line-height:1;color:var(--text-primary)}
.sup-score-of{font-size:12px;color:var(--text-tertiary);margin-bottom:2px}
.sup-score-bar-wrap{flex:1}
.sup-score-lbl{font-size:10px;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px}
.sup-score-bar{height:5px;border-radius:3px;background:var(--border)}
.sup-score-fill{height:100%;border-radius:3px}
.sup-stats{display:flex;gap:16px;margin-top:4px}
.sup-stat .sv{font-size:13px;font-weight:600;color:var(--text-primary)}
.sup-stat .sl{font-size:10px;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:.04em;margin-top:2px}
.sup-footer{display:flex;gap:6px;margin-top:12px;padding-top:10px;border-top:0.5px solid var(--border)}
.preferred-badge{font-size:10px;padding:2px 8px;border-radius:12px;background:#E1F5EE;color:#085041;border:0.5px solid #5DCAA5}
.blacklisted-badge{font-size:10px;padding:2px 8px;border-radius:12px;background:#FCEBEB;color:#791F1F;border:0.5px solid #F5C6C6}
.active-po-badge{font-size:10px;padding:2px 8px;border-radius:12px;background:#EEEDFE;color:#3C3489;border:0.5px solid #7F77DD}

/* detail slide-in panel */
.sup-detail{display:none;background:#fff;border:0.5px solid var(--border);border-radius:12px;
            padding:1.25rem;margin-bottom:1rem}
.sup-detail.open{display:block}
.po-hist-tbl{width:100%;border-collapse:collapse;font-size:11px}
.po-hist-tbl th{text-align:left;font-weight:600;color:var(--text-tertiary);text-transform:uppercase;
                 letter-spacing:.05em;padding:6px 8px;border-bottom:0.5px solid var(--border)}
.po-hist-tbl td{padding:7px 8px;border-bottom:0.5px solid var(--border);color:var(--text-secondary)}
.po-hist-tbl tr:last-child td{border-bottom:none}
.po-hist-tbl tr:hover td{background:var(--bg-inner)}
</style>
</head>
<body>
<header>
  <h1>&#9876;&#65039; Winterfell Operations</h1>
  <nav class="top-nav">
    <a href="/" class="nav-link">Operations</a>
    <a href="/products" class="nav-link">Products</a>
    <a href="/customers" class="nav-link">Customers</a>
    <a href="/orders" class="nav-link">Orders</a>
    <a href="/supply-chain" class="nav-link active">Supply Chain</a>
  </nav>
  <div class="hdr-actions">
    <a href="/logout" class="logout">Logout</a>
    <button class="ham" onclick="document.getElementById('mnav-sup').classList.toggle('open')" aria-label="Menu">
      <span></span><span></span><span></span>
    </button>
  </div>
  <nav class="mob-nav" id="mnav-sup">
    <a href="/">Operations</a>
    <a href="/products">Products</a>
    <a href="/customers">Customers</a>
    <a href="/orders">Orders</a>
    <a href="/supply-chain" class="active">Supply Chain</a>
    <a href="/logout">Logout</a>
  </nav>
</header>
<div class="container">
  <div class="page-head">
    <div>
      <div class="page-title">Supply Chain / Suppliers</div>
      <div class="page-sub"><a href="/supply-chain" style="color:var(--teal);text-decoration:none;font-size:11px">&#8592; Back to POs</a></div>
    </div>
    <button class="btn btn-primary" onclick="openAddModal()">+ Add Supplier</button>
  </div>

  <div class="metrics" id="sup-metrics">
    <div class="metric"><div class="val" id="m-total">&mdash;</div><div class="lbl">Total suppliers</div></div>
    <div class="metric"><div class="val" id="m-preferred">&mdash;</div><div class="lbl">Preferred</div></div>
    <div class="metric"><div class="val" id="m-ontime">&mdash;</div><div class="lbl">Avg on-time %</div></div>
    <div class="metric"><div class="val" id="m-active-pos">&mdash;</div><div class="lbl">Active POs</div></div>
  </div>

  <div id="sup-detail-panel" class="sup-detail"></div>
  <div id="sup-grid" class="sup-grid"><div class="empty">Loading suppliers&hellip;</div></div>
</div>

<!-- Add Supplier modal -->
<div class="modal-bg" id="add-modal" onclick="if(event.target===this)closeAddModal()">
  <div class="modal">
    <div class="modal-head">
      <h3>Add Supplier</h3>
      <button class="modal-close" onclick="closeAddModal()">&times;</button>
    </div>
    <div class="modal-body">
      <div class="form-err" id="add-err"></div>
      <div class="field"><label>Name *</label><input id="a-name" type="text" placeholder="e.g. MS Fashion"></div>
      <div class="row2">
        <div class="field"><label>Phone</label><input id="a-phone" type="text" placeholder="+880..."></div>
        <div class="field"><label>WhatsApp</label><input id="a-wa" type="text" placeholder="+880..."></div>
      </div>
      <div class="field"><label>Location</label><input id="a-loc" type="text" placeholder="e.g. Mirpur, Dhaka"></div>
      <div class="field"><label>Notes</label><textarea id="a-notes" placeholder="Speciality, payment terms&hellip;"></textarea></div>
      <button class="btn btn-primary" style="width:100%" onclick="submitSupplier()">Add Supplier</button>
    </div>
  </div>
</div>

<script>
var openDetailId = null;

function esc(s){
  if(s==null) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}
function fmtBDT(n){
  if(n==null||isNaN(n)) return '&#2547;0';
  return '&#2547;' + Math.round(Number(n)).toString().replace(/\\B(?=(\\d{3})+(?!\\d))/g,',');
}
function scoreColor(s){
  var n = Number(s||0);
  if(n>=7) return '#1D9E75';
  if(n>=4) return '#BA7517';
  return '#E24B4A';
}

function loadSuppliers(){
  fetch('/api/sc/suppliers').then(function(r){return r.json();}).then(function(data){
    if(data.error){ document.getElementById('sup-grid').innerHTML='<div class="empty">Error: '+esc(data.error)+'</div>'; return; }
    var sups = Array.isArray(data) ? data : [];
    renderMetrics(sups);
    renderGrid(sups);
  }).catch(function(e){
    document.getElementById('sup-grid').innerHTML='<div class="empty">Failed to load.</div>';
  });
}

function renderMetrics(sups){
  document.getElementById('m-total').textContent = sups.length;
  document.getElementById('m-preferred').textContent = sups.filter(function(s){return s.is_preferred;}).length;
  var activePOs = sups.reduce(function(a,s){return a+(s.active_po_count||0);},0);
  document.getElementById('m-active-pos').textContent = activePOs;
  var withPos = sups.filter(function(s){return (s.total_pos||0)>0;});
  if(withPos.length){
    var avgOt = withPos.reduce(function(a,s){return a+(parseFloat(s.on_time_pct)||0);},0)/withPos.length;
    document.getElementById('m-ontime').textContent = avgOt.toFixed(0)+'%';
  } else {
    document.getElementById('m-ontime').textContent = '—';
  }
}

function renderGrid(sups){
  if(!sups.length){ document.getElementById('sup-grid').innerHTML='<div class="empty">No suppliers yet. Add your first supplier.</div>'; return; }
  var html='';
  for(var i=0;i<sups.length;i++){
    var s=sups[i];
    var cls='sup-card'+(s.is_preferred?' preferred':'')+(s.is_blacklisted?' blacklisted':'');
    var score=parseFloat(s.reliability_score||0);
    var fillW=(score/10*100).toFixed(0);
    var fillC=scoreColor(score);
    var tags=(s.speciality||[]).map(function(t){return '<span class="sup-tag">'+esc(t)+'</span>';}).join('');
    var onTimePct=parseFloat(s.on_time_pct||0).toFixed(0);
    var activePOs=s.active_po_count||0;
    var totalPOs=s.total_pos||0;
    html+='<div class="'+cls+'" onclick="toggleDetail('+s.id+')">';
    html+='<div class="sup-name">'+esc(s.name)+'</div>';
    html+='<div class="sup-loc">'+(s.location?esc(s.location):'No location')+'</div>';
    html+='<div class="sup-tags">'+(tags||'<span style="font-size:11px;color:var(--text-tertiary)">No tags</span>')+'</div>';
    html+='<div class="sup-score-row">';
    html+='<div><div class="sup-score-num" style="color:'+fillC+'">'+score.toFixed(1)+'</div></div>';
    html+='<div class="sup-score-of">/10</div>';
    html+='<div class="sup-score-bar-wrap">';
    html+='<div class="sup-score-lbl">Reliability</div>';
    html+='<div class="sup-score-bar"><div class="sup-score-fill" style="width:'+fillW+'%;background:'+fillC+'"></div></div>';
    html+='</div></div>';
    html+='<div class="sup-stats">';
    html+='<div class="sup-stat"><div class="sv">'+onTimePct+'%</div><div class="sl">On time</div></div>';
    html+='<div class="sup-stat"><div class="sv">'+totalPOs+'</div><div class="sl">Total POs</div></div>';
    html+='<div class="sup-stat"><div class="sv">'+activePOs+'</div><div class="sl">Active</div></div>';
    html+='</div>';
    html+='<div class="sup-footer">';
    if(s.is_preferred) html+='<span class="preferred-badge">&#9733; Preferred</span>';
    if(s.is_blacklisted) html+='<span class="blacklisted-badge">&#9888; Blacklisted</span>';
    if(activePOs>0) html+='<span class="active-po-badge">'+activePOs+' active PO'+(activePOs>1?'s':'')+'</span>';
    html+='</div>';
    html+='</div>';
  }
  document.getElementById('sup-grid').innerHTML=html;
}

function toggleDetail(id){
  var panel=document.getElementById('sup-detail-panel');
  if(openDetailId===id){ panel.classList.remove('open'); panel.innerHTML=''; openDetailId=null; return; }
  openDetailId=id;
  panel.classList.add('open');
  panel.innerHTML='<div style="color:#8b949e;padding:14px 0">Loading&hellip;</div>';
  fetch('/api/sc/suppliers/'+id).then(function(r){return r.json();}).then(function(data){
    if(data.error){ panel.innerHTML='<div style="color:#F7C1C1">'+esc(data.error)+'</div>'; return; }
    panel.innerHTML=detailHtml(data.supplier,data.pos||[]);
    panel.scrollIntoView({behavior:'smooth',block:'nearest'});
  }).catch(function(e){ panel.innerHTML='<div style="color:#F7C1C1">Failed: '+esc(e.message)+'</div>'; });
}

function detailHtml(s,pos){
  var h='<div style="display:flex;align-items:flex-start;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:16px">';
  h+='<div><div style="font-size:15px;font-weight:700;color:var(--text-primary)">'+esc(s.name)+'</div>';
  if(s.phone) h+='<div style="font-size:11px;color:var(--text-tertiary);margin-top:3px">Phone: '+esc(s.phone)+'</div>';
  if(s.whatsapp) h+='<div style="font-size:11px;color:var(--text-tertiary)">WhatsApp: '+esc(s.whatsapp)+'</div>';
  if(s.location) h+='<div style="font-size:11px;color:var(--text-tertiary)">Location: '+esc(s.location)+'</div>';
  h+='</div>';
  h+='<a href="/supply-chain?supplier='+encodeURIComponent(s.id)+'" class="btn btn-ghost" style="font-size:11px">View POs &#8594;</a>';
  h+='</div>';
  if(!pos.length){ h+='<div style="color:#8b949e;font-size:12px">No purchase orders yet.</div>'; return h; }
  h+='<div style="font-size:11px;font-weight:600;color:var(--text-secondary);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">PO History ('+pos.length+')</div>';
  h+='<div class="tbl-wrap"><table class="po-hist-tbl">';
  h+='<thead><tr><th>PO ID</th><th>Product</th><th>Qty</th><th>Status</th><th>Stage</th><th>Due</th></tr></thead><tbody>';
  for(var i=0;i<pos.length;i++){
    var p=pos[i];
    var stCls=p.po_status==='Delayed'?'badge-delayed':p.po_status==='At Risk'?'badge-atrisk':'badge-ontrack';
    h+='<tr>';
    h+='<td><a href="/supply-chain/po/'+encodeURIComponent(p.po_id)+'" style="color:var(--purple);font-family:monospace">'+esc(p.po_id)+'</a></td>';
    h+='<td>'+esc(p.product_name||'')+'</td>';
    h+='<td>'+(p.quantity_ordered||0)+'</td>';
    h+='<td><span class="badge '+stCls+'">'+esc(p.po_status||'')+'</span></td>';
    h+='<td>'+esc(p.current_stage||'')+'</td>';
    h+='<td>'+esc((p.due_date||'').substring(0,10))+'</td>';
    h+='</tr>';
  }
  h+='</tbody></table></div>';
  return h;
}

function openAddModal(){ document.getElementById('add-err').classList.remove('show'); document.getElementById('add-modal').classList.add('open'); }
function closeAddModal(){ document.getElementById('add-modal').classList.remove('open'); }

function submitSupplier(){
  var err=document.getElementById('add-err');
  err.classList.remove('show');
  var name=document.getElementById('a-name').value.trim();
  if(!name){ err.innerHTML='Name is required.'; err.classList.add('show'); return; }
  var body={name:name,phone:document.getElementById('a-phone').value.trim(),
            whatsapp:document.getElementById('a-wa').value.trim(),
            location:document.getElementById('a-loc').value.trim(),
            notes:document.getElementById('a-notes').value.trim()};
  fetch('/api/sc/suppliers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
    .then(function(r){return r.json();}).then(function(data){
      if(data.error){ err.innerHTML=esc(data.error); err.classList.add('show'); return; }
      closeAddModal();
      document.getElementById('a-name').value='';
      document.getElementById('a-phone').value='';
      document.getElementById('a-wa').value='';
      document.getElementById('a-loc').value='';
      document.getElementById('a-notes').value='';
      loadSuppliers();
    }).catch(function(e){ err.innerHTML='Failed: '+esc(e.message); err.classList.add('show'); });
}

loadSuppliers();
</script>
</body>
</html>"""


# ── Waiting Orders page ───────────────────────────────────────────────────────

SC_WAITING_ORDERS_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Winterfell — Waiting Orders</title>
<style>""" + SC_CSS + """
.wo-banner{background:#E1F5EE;border:0.5px solid #5DCAA5;border-radius:12px;
           padding:1rem 1.25rem;display:flex;justify-content:space-between;
           align-items:flex-start;gap:12px;margin-bottom:1.25rem}
.wo-banner-left .ttl{font-size:13px;font-weight:600;color:#085041}
.wo-banner-left .pname{font-size:18px;font-weight:700;color:#085041;margin-top:2px}
.wo-banner-left .meta{font-size:11px;color:#1D9E75;margin-top:5px}
.wo-banner-right{text-align:right;font-size:10px;color:#1D9E75;text-transform:uppercase;letter-spacing:.05em}
.wo-banner-right .big{font-size:16px;font-weight:700;color:#085041;text-transform:none;letter-spacing:0;margin-top:3px}
.wo-tbl{width:100%;border-collapse:collapse;font-size:12px}
.wo-tbl th{text-align:left;font-weight:600;color:var(--text-tertiary);text-transform:uppercase;
            letter-spacing:.05em;padding:8px 10px;border-bottom:0.5px solid var(--border);font-size:10px}
.wo-tbl td{padding:9px 10px;border-bottom:0.5px solid var(--border);color:var(--text-secondary)}
.wo-tbl tr:last-child td{border-bottom:none}
.wo-tbl tr:hover td{background:var(--bg-inner)}
.wait-urgent{color:#791F1F;font-weight:600}
.wait-warn{color:#633806}
.link-form{display:flex;gap:8px;margin-bottom:1rem}
.link-form input{flex:1;background:var(--bg-inner);border:0.5px solid var(--border);
                  border-radius:8px;padding:8px 11px;font-size:12px;font-family:Arial,sans-serif;color:var(--text-primary)}
.link-form input:focus{outline:none;border-color:var(--purple)}
</style>
</head>
<body>
<header>
  <h1>&#9876;&#65039; Winterfell Operations</h1>
  <nav class="top-nav">
    <a href="/" class="nav-link">Operations</a>
    <a href="/products" class="nav-link">Products</a>
    <a href="/customers" class="nav-link">Customers</a>
    <a href="/orders" class="nav-link">Orders</a>
    <a href="/supply-chain" class="nav-link active">Supply Chain</a>
  </nav>
  <div class="hdr-actions">
    <a href="/logout" class="logout">Logout</a>
    <button class="ham" onclick="document.getElementById('mnav-wo').classList.toggle('open')" aria-label="Menu">
      <span></span><span></span><span></span>
    </button>
  </div>
  <nav class="mob-nav" id="mnav-wo">
    <a href="/">Operations</a>
    <a href="/products">Products</a>
    <a href="/customers">Customers</a>
    <a href="/orders">Orders</a>
    <a href="/supply-chain" class="active">Supply Chain</a>
    <a href="/logout">Logout</a>
  </nav>
</header>
<div class="container">
  <div class="crumb"><a href="/supply-chain">Supply Chain</a> / <span id="cr-po" class="mono">&hellip;</span> / <a id="cr-tl" href="#">Timeline</a> / Waiting Orders</div>

  <div id="wo-banner" class="wo-banner"><div><div class="ttl">Loading&hellip;</div></div></div>

  <div class="metrics" id="wo-metrics">
    <div class="metric"><div class="val" id="wm-orders">&mdash;</div><div class="lbl">Orders waiting</div></div>
    <div class="metric"><div class="val" id="wm-revenue">&mdash;</div><div class="lbl">Revenue held</div></div>
    <div class="metric"><div class="val" id="wm-avg">&mdash;</div><div class="lbl">Avg days waiting</div></div>
    <div class="metric"><div class="val" id="wm-urgent">&mdash;</div><div class="lbl">20+ day waiters</div></div>
  </div>

  <div class="tl" style="margin-bottom:1.25rem">
    <div class="section-title">Link a waiting order</div>
    <div class="link-form">
      <input id="link-so" type="text" placeholder="Enter SO number (e.g. SO-12345)">
      <button class="btn btn-primary" onclick="linkSO()">Link SO</button>
    </div>
    <div id="link-msg" style="font-size:11px;display:none"></div>
  </div>

  <div class="section-title">Linked waiting orders</div>
  <div class="tl tbl-wrap" id="wo-table"><div style="color:#8b949e">Loading&hellip;</div></div>
</div>

<script>
var POID = '';
(function(){
  var parts = window.location.pathname.split('/');
  var idx = parts.indexOf('po');
  if(idx>=0 && idx+1<parts.length) POID = decodeURIComponent(parts[idx+1]);
})();

function esc(s){ if(s==null) return ''; return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function fmtBDT(n){
  if(n==null||isNaN(n)) return '&#2547;0';
  var v=Math.round(Number(n));
  if(v>=100000) return '&#2547;'+(v/100000).toFixed(1)+'L';
  return '&#2547;'+v.toString().replace(/\\B(?=(\\d{3})+(?!\\d))/g,',');
}

function loadData(){
  document.getElementById('cr-po').textContent = POID;
  document.getElementById('cr-tl').href = '/supply-chain/po/'+encodeURIComponent(POID);
  fetch('/api/sc/po/'+encodeURIComponent(POID)+'/waiting-orders')
    .then(function(r){return r.json();})
    .then(function(data){
      if(data.error){ document.getElementById('wo-banner').innerHTML='<div><div class="ttl">'+esc(data.error)+'</div></div>'; return; }
      renderBanner(data.po);
      renderMetrics(data);
      renderTable(data.orders||[]);
    }).catch(function(e){
      document.getElementById('wo-banner').innerHTML='<div><div class="ttl">Failed: '+esc(e.message)+'</div></div>';
    });
}

function renderBanner(po){
  var h='<div class="wo-banner-left">';
  h+='<div class="ttl"><span class="mono" style="font-size:12px">'+esc(po.po_id)+'</span></div>';
  h+='<div class="pname">'+esc(po.product_name||'')+'</div>';
  h+='<div class="meta">'+esc(po.sku||'')+(po.quantity_ordered?' &middot; '+po.quantity_ordered+' units ordered':'')+'</div>';
  h+='</div>';
  h+='<div class="wo-banner-right"><div>Units ordered</div><div class="big">'+(po.quantity_ordered||0)+'</div></div>';
  document.getElementById('wo-banner').innerHTML=h;
}

function renderMetrics(data){
  var orders=data.orders||[];
  document.getElementById('wm-orders').textContent=orders.length;
  document.getElementById('wm-revenue').innerHTML=fmtBDT(data.revenue_held||0);
  var urgent=orders.filter(function(o){return (o.days_waiting||0)>=20;}).length;
  document.getElementById('wm-urgent').textContent=urgent;
  if(orders.length){
    var avg=orders.reduce(function(a,o){return a+(o.days_waiting||0);},0)/orders.length;
    document.getElementById('wm-avg').textContent=avg.toFixed(0)+' days';
  } else {
    document.getElementById('wm-avg').textContent='—';
  }
}

function renderTable(orders){
  var wrap=document.getElementById('wo-table');
  if(!orders.length){
    wrap.innerHTML='<div style="color:#8b949e;font-size:12px;padding:8px 0">No linked orders yet. Use the form above to link waiting SO numbers.</div>';
    return;
  }
  var h='<table class="wo-tbl"><thead><tr>';
  h+='<th>#</th><th>SO Number</th><th>Customer</th><th>Amount</th><th>Status</th><th>Waiting</th><th>Unlink</th>';
  h+='</tr></thead><tbody>';
  for(var i=0;i<orders.length;i++){
    var o=orders[i];
    var dw=o.days_waiting||0;
    var wCls=dw>=20?'wait-urgent':dw>=12?'wait-warn':'';
    h+='<tr>';
    h+='<td>'+(i+1)+'</td>';
    h+='<td><span class="mono" style="color:var(--purple)">'+esc(o.so_number)+'</span></td>';
    h+='<td>'+esc(o.customer_name||'')+'</td>';
    h+='<td>'+fmtBDT(o.total_receivable||0)+'</td>';
    h+='<td>'+esc(o.nuport_status||o.payment_status||'')+'</td>';
    h+='<td class="'+wCls+'">'+dw+' days</td>';
    h+='<td><button class="btn btn-ghost" style="font-size:10px;padding:3px 8px" onclick="unlinkSO(\\'' + esc(o.so_number) + '\\')">Unlink</button></td>';
    h+='</tr>';
  }
  h+='</tbody></table>';
  wrap.innerHTML=h;
}

function linkSO(){
  var so=document.getElementById('link-so').value.trim();
  var msg=document.getElementById('link-msg');
  if(!so){ msg.style.display='block'; msg.style.color='#791F1F'; msg.textContent='Enter an SO number.'; return; }
  fetch('/api/sc/po/'+encodeURIComponent(POID)+'/link-so',{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({so_number:so})
  }).then(function(r){return r.json();}).then(function(data){
    if(data.error){ msg.style.display='block'; msg.style.color='#791F1F'; msg.textContent=data.error; return; }
    msg.style.display='block'; msg.style.color='#085041'; msg.textContent=so+' linked.';
    document.getElementById('link-so').value='';
    setTimeout(function(){msg.style.display='none';},2000);
    loadData();
  }).catch(function(e){ msg.style.display='block'; msg.style.color='#791F1F'; msg.textContent='Failed: '+e.message; });
}

function unlinkSO(so){
  if(!confirm('Unlink '+so+' from this PO?')) return;
  fetch('/api/sc/po/'+encodeURIComponent(POID)+'/unlink-so',{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({so_number:so})
  }).then(function(r){return r.json();}).then(function(data){
    if(data.error){ alert('Failed: '+data.error); return; }
    loadData();
  }).catch(function(e){ alert('Failed: '+e.message); });
}

loadData();
</script>
</body>
</html>"""


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
  <nav class="top-nav">
    <a href="/" class="nav-link">Operations</a>
    <a href="/products" class="nav-link">Products</a>
    <a href="/customers" class="nav-link">Customers</a>
    <a href="/orders" class="nav-link">Orders</a>
    <a href="/supply-chain" class="nav-link active">Supply Chain</a>
  </nav>
  <div class="hdr-actions">
    <a href="/logout" class="logout">Logout</a>
    <button class="ham" onclick="document.getElementById('mnav-sc').classList.toggle('open')" aria-label="Menu">
      <span></span><span></span><span></span>
    </button>
  </div>
  <nav class="mob-nav" id="mnav-sc">
    <a href="/">Operations</a>
    <a href="/products">Products</a>
    <a href="/customers">Customers</a>
    <a href="/orders">Orders</a>
    <a href="/supply-chain" class="active">Supply Chain</a>
    <a href="/logout">Logout</a>
  </nav>
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
  var html = '<div class="stages"><div class="st-row">';
  for(var i=0; i<STAGES.length; i++){
    var cls = 'stage-dot';
    var inner = '';
    if(i < idx){ cls += ' done'; inner = '<span style="color:#fff;font-size:9px;line-height:1;font-weight:700">&#10003;</span>'; }
    else if(i === idx){ cls += ' active'; inner = '<span style="width:5px;height:5px;border-radius:50%;background:#fff;display:inline-block"></span>'; }
    html += '<span class="' + cls + '" title="' + esc(STAGES[i]) + '">' + inner + '</span>';
    if(i < STAGES.length - 1){
      var lcls = 'stage-line';
      if(i < idx) lcls += ' done';
      else if(i === idx) lcls += ' active';
      html += '<span class="' + lcls + '"></span>';
    }
  }
  html += '</div><div class="st-lbls">';
  for(var j=0; j<STAGES.length; j++){
    var lc = 'st-lbl';
    if(j < idx) lc += ' done';
    else if(j === idx) lc += ' active';
    html += '<span class="' + lc + '">' + esc(STAGES[j]) + '</span>';
  }
  html += '</div></div>';
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
  h += '<a class="btn btn-ghost" href="/supply-chain/po/' + encodeURIComponent(po.po_id) + '/waiting-orders">Waiting Orders &rarr;</a>';
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
.banner .ttl{font-size:15px;font-weight:600;color:var(--text-secondary)}
.banner .pname{font-size:22px;font-weight:700;color:var(--text-primary);margin-top:3px;line-height:1.25}
.banner .meta{color:var(--text-tertiary);font-size:11px;margin-top:6px;line-height:1.55}
.banner .right{text-align:right;font-size:10px;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:.05em}
.banner .right .big{font-size:14px;color:var(--text-primary);font-weight:500;text-transform:none;letter-spacing:0}
.bigstages{background:#fff;border:0.5px solid var(--border);border-radius:12px;
           padding:24px 28px;margin-bottom:1.25rem}
.bs-row{display:flex;align-items:center}
.bs-dot{width:22px;height:22px;border-radius:50%;border:1.5px solid #CBD5E0;background:#fff;flex:0 0 auto;display:flex;align-items:center;justify-content:center}
.bs-dot.done{background:#1D9E75;border-color:#1D9E75}
.bs-dot.active{background:#7F77DD;border-color:#7F77DD;box-shadow:0 0 0 3px #EEEDFE}
.bs-line{height:3px;flex:1;background:var(--track);border-radius:2px}
.bs-line.done{background:#1D9E75}
.bs-line.active{background:linear-gradient(90deg,#1D9E75,#7F77DD)}
.bs-labels{display:flex;margin-top:10px}
.bs-lbl{flex:1;text-align:center;font-size:10px;color:var(--text-tertiary)}
.bs-lbl:first-child{text-align:left}
.bs-lbl:last-child{text-align:right}
.bs-lbl.active{color:var(--purple);font-weight:500}
.bs-lbl.done{color:var(--teal)}
.section-title{font-size:11px;font-weight:500;color:var(--text-secondary);text-transform:uppercase;letter-spacing:.07em;margin:0 0 14px 0}
.tl{background:#fff;border:0.5px solid var(--border);border-radius:12px;padding:1rem 1.25rem;margin-bottom:1.25rem}
.tl-ev{padding:5px 0}
.tl-body{border-left:2px solid var(--border);padding-left:12px}
.src-ico{display:none!important}
.tl-sgroup{margin-bottom:18px}
.tl-shead{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.tl-sdot{width:18px;height:18px;border-radius:50%;border:1.5px solid #CBD5E0;background:#fff;flex:0 0 auto;display:flex;align-items:center;justify-content:center}
.tl-sdot.done{background:#1D9E75;border-color:#1D9E75}
.tl-sdot.active{background:#7F77DD;border-color:#7F77DD;box-shadow:0 0 0 3px #EEEDFE}
.tl-sdot.pending{border-style:dashed;opacity:.6}
.tl-sname{font-size:14px;font-weight:700;color:var(--text-primary)}
.tl-sname.done{color:#1D9E75}
.tl-sname.active{color:#7F77DD}
.tl-sname.pending{color:var(--text-tertiary);font-weight:400}
.tl-sevents{margin-left:26px;padding-left:12px;border-left:2px solid var(--border);padding-top:2px;padding-bottom:2px}
.tl-body.b-brain{border-left-color:#1D9E75}
.tl-body.b-pm{border-left-color:#7F77DD}
.tl-body.b-finance{border-left-color:#BA7517}
.tl-body.b-supplier{border-left-color:#378ADD}
.tl-body.b-alert{border-left-color:var(--red)}
.tl-title{color:var(--text-primary);font-weight:700;font-size:14px}
.tl-note{color:var(--text-secondary);font-size:13px;margin-top:3px;line-height:1.55}
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
  <nav class="top-nav">
    <a href="/" class="nav-link">Operations</a>
    <a href="/products" class="nav-link">Products</a>
    <a href="/customers" class="nav-link">Customers</a>
    <a href="/orders" class="nav-link">Orders</a>
    <a href="/supply-chain" class="nav-link active">Supply Chain</a>
  </nav>
  <div class="hdr-actions">
    <a href="/logout" class="logout">Logout</a>
    <button class="ham" onclick="document.getElementById('mnav-sc').classList.toggle('open')" aria-label="Menu">
      <span></span><span></span><span></span>
    </button>
  </div>
  <nav class="mob-nav" id="mnav-sc">
    <a href="/">Operations</a>
    <a href="/products">Products</a>
    <a href="/customers">Customers</a>
    <a href="/orders">Orders</a>
    <a href="/supply-chain" class="active">Supply Chain</a>
    <a href="/logout">Logout</a>
  </nav>
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
        <div class="opt" data-v="Cutting" onclick="pick(this,'stage')">Cutting</div>
        <div class="opt" data-v="Print/Ambo" onclick="pick(this,'stage')">Print/Ambo</div>
        <div class="opt" data-v="Trims" onclick="pick(this,'stage')">Trims</div>
        <div class="opt" data-v="Sewing" onclick="pick(this,'stage')">Sewing</div>
        <div class="opt" data-v="Wash" onclick="pick(this,'stage')">Wash</div>
        <div class="opt" data-v="QC" onclick="pick(this,'stage')">QC</div>
        <div class="opt" data-v="Delivery" onclick="pick(this,'stage')">Delivery</div>
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
var currentPo = null;

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
  if(t === 'brain') return 'background:#E1F5EE;color:#085041';
  if(t === 'pm') return 'background:#EEEDFE;color:#3C3489';
  if(t === 'finance') return 'background:#FAEEDA;color:#633806';
  if(t === 'supplier') return 'background:#E6F1FB;color:#0C447C';
  return 'background:#FCEBEB;color:#791F1F';
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
    currentPo = data.po;
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
  h += '<div class="ttl"><span class="mono" style="color:#7F77DD">' + esc(po.po_id) + '</span></div>';
  h += '<div class="pname">' + esc(po.product_name || '') + '</div>';
  h += '<div class="meta">' + esc(po.supplier_name || 'No supplier') + ' &middot; ' + (po.quantity_ordered || 0) + ' pcs &middot; Due: ' + esc(arrive) + '</div>';
  h += '<div style="display:flex;gap:8px;margin-top:12px;flex-wrap:wrap">';
  h += '<button class="btn btn-ghost" onclick="openEditModal()" style="font-size:12px">&#9998; Edit PO</button>';
  h += '<a class="btn btn-ghost" href="/supply-chain/po/' + esc(po.po_id) + '/waiting-orders" style="font-size:12px">&#9201; Waiting Orders</a>';
  if(po.po_status !== 'Completed' && po.po_status !== 'Cancelled'){
    h += '<button class="btn btn-primary" onclick="openReceiveModal()" style="font-size:12px;background:#1D9E75">&#10003; Receive Stock</button>';
  } else {
    h += '<span style="font-size:11px;color:#1D9E75;padding:5px 0;align-self:center">&#10003; Completed '+(po.actual_delivery?po.actual_delivery.substring(0,10):'')+'</span>';
  }
  h += '<button class="btn btn-danger" onclick="confirmDelete()" style="font-size:12px">&#128465; Delete</button>';
  h += '</div>';
  h += '</div>';
  h += '<div class="right"><div>Expected arrival</div><div class="big">' + esc(arrive) + '</div></div>';
  document.getElementById('banner').innerHTML = h;
}

function renderBigStages(currentStage, expected){
  var idx = STAGES.indexOf(currentStage);
  var h = '<div class="bs-row">';
  for(var i=0; i<STAGES.length; i++){
    var cls = 'bs-dot';
    var inner = '';
    if(i < idx){ cls += ' done'; inner = '<span style="color:#fff;font-size:11px;line-height:1;font-weight:700">&#10003;</span>'; }
    else if(i === idx){ cls += ' active'; inner = '<span style="width:7px;height:7px;border-radius:50%;background:#fff;display:inline-block"></span>'; }
    h += '<span class="' + cls + '">' + inner + '</span>';
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
  var idx = STAGES.indexOf(currentStage);
  var stageMap = {};
  for(var i=0; i<events.length; i++){
    var ev = events[i];
    var s = ev.stage || 'PO Issued';
    if(!stageMap[s]) stageMap[s] = [];
    stageMap[s].push(ev);
  }
  var rendered = {};
  var h = '';

  function evHtml(ev2){
    var stype2 = ev2.is_alert ? 'alert' : ev2.source_type;
    var bcls2 = ev2.is_alert ? 'alert' : srcClass(ev2.source_type);
    var r = '<div class="tl-ev"><div class="tl-body b-' + bcls2 + '">';
    r += '<div class="tl-title">' + esc(ev2.event_title) + '</div>';
    if(ev2.event_note){ r += '<div class="tl-note">' + esc(ev2.event_note) + '</div>'; }
    if(ev2.amount_bdt){ r += '<div class="tl-amt">' + fmtBDT(ev2.amount_bdt) + '</div>'; }
    r += '<div class="tl-foot">';
    r += '<span class="tl-date">' + esc((ev2.event_date || '').substring(0,16).replace('T',' ')) + '</span>';
    r += '<span class="src-badge" style="' + srcBadgeStyle(stype2) + '">' + esc(ev2.logged_by || ev2.source_type) + '</span>';
    r += '</div></div></div>';
    return r;
  }

  var knownSet = {};
  for(var ks=0; ks<STAGES.length; ks++) knownSet[STAGES[ks]] = true;

  // Build stage display order from the chronological event stream,
  // then append any unseen pending STAGES at the end
  var seenOrder = [];
  var seenSet = {};
  for(var ei2=0; ei2<events.length; ei2++){
    var s2 = events[ei2].stage || 'PO Issued';
    if(!seenSet[s2]){ seenOrder.push(s2); seenSet[s2] = true; }
  }
  // Active stage with no events yet still needs to appear
  if(currentStage && !seenSet[currentStage]){ seenOrder.push(currentStage); seenSet[currentStage] = true; }
  // Append pending STAGES not yet seen
  for(var ps=0; ps<STAGES.length; ps++){
    if(!seenSet[STAGES[ps]]){ seenOrder.push(STAGES[ps]); seenSet[STAGES[ps]] = true; }
  }

  for(var oi=0; oi<seenOrder.length; oi++){
    var sname = seenOrder[oi];
    var knownIdx2 = STAGES.indexOf(sname);
    var sevs = stageMap[sname] || [];
    var sdone = knownIdx2 >= 0 && knownIdx2 < idx;
    var sactive = knownIdx2 >= 0 && knownIdx2 === idx;
    var spending = knownIdx2 < 0 ? false : knownIdx2 > idx;
    var isExtra = knownIdx2 < 0; // not in main progress stages
    var dotcls = 'tl-sdot';
    var namecls = 'tl-sname';
    var dotinner = '';
    if(sdone){
      dotcls += ' done'; namecls += ' done';
      dotinner = '<span style="color:#fff;font-size:10px;line-height:1;font-weight:700">&#10003;</span>';
    } else if(sactive){
      dotcls += ' active'; namecls += ' active';
      dotinner = '<span style="width:5px;height:5px;border-radius:50%;background:#fff;display:inline-block"></span>';
    } else if(isExtra && sevs.length > 0){
      dotcls += ' active'; namecls += ' active';
      dotinner = '<span style="width:5px;height:5px;border-radius:50%;background:#fff;display:inline-block"></span>';
    } else {
      dotcls += ' pending'; namecls += ' pending';
    }
    h += '<div class="tl-sgroup">';
    h += '<div class="tl-shead"><span class="' + dotcls + '">' + dotinner + '</span><span class="' + namecls + '">' + esc(sname) + '</span>';
    if(spending && expected){
      h += ' <span style="font-size:11px;color:var(--text-tertiary);font-weight:400">&#8212; expected ' + esc(expected.substring(0,10)) + '</span>';
    }
    h += '</div>';
    if(sevs.length > 0){
      h += '<div class="tl-sevents">';
      for(var ei=0; ei<sevs.length; ei++){ h += evHtml(sevs[ei]); }
      h += '</div>';
    }
    h += '</div>';
  }

  if(h === ''){ h = '<div style="color:#8b949e">No events yet.</div>'; }
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

function openEditModal(){
  if(!currentPo) return;
  document.getElementById('e-product').value = currentPo.product_name || '';
  document.getElementById('e-sku').value = currentPo.sku || '';
  document.getElementById('e-supplier').value = currentPo.supplier_name || '';
  document.getElementById('e-qty').value = currentPo.quantity_ordered || '';
  document.getElementById('e-cost').value = currentPo.unit_cost_bdt || '';
  document.getElementById('e-advance').value = currentPo.advance_paid_bdt || '';
  document.getElementById('e-due').value = (currentPo.due_date || '').substring(0,10);
  document.getElementById('e-notes').value = currentPo.notes || '';
  document.getElementById('edit-err').classList.remove('show');
  document.getElementById('edit-modal').classList.add('open');
}
function closeEditModal(){ document.getElementById('edit-modal').classList.remove('open'); }
function editBgClick(e){ if(e.target === document.getElementById('edit-modal')) closeEditModal(); }

function saveEdit(){
  var err = document.getElementById('edit-err');
  err.classList.remove('show');
  var product = document.getElementById('e-product').value.trim();
  var qty = document.getElementById('e-qty').value.trim();
  var due = document.getElementById('e-due').value.trim();
  if(!product){ err.innerHTML = 'Product name is required.'; err.classList.add('show'); return; }
  if(!qty || Number(qty) <= 0){ err.innerHTML = 'Quantity must be greater than zero.'; err.classList.add('show'); return; }
  if(!due){ err.innerHTML = 'Due date is required.'; err.classList.add('show'); return; }
  var body = {
    product_name: product,
    sku: document.getElementById('e-sku').value.trim(),
    supplier_name: document.getElementById('e-supplier').value.trim(),
    quantity_ordered: Number(qty),
    unit_cost_bdt: document.getElementById('e-cost').value.trim(),
    advance_paid_bdt: document.getElementById('e-advance').value.trim(),
    due_date: due,
    notes: document.getElementById('e-notes').value.trim()
  };
  fetch('/api/sc/po/' + encodeURIComponent(POID), {
    method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  }).then(function(r){ return r.json(); }).then(function(data){
    if(data.error){ err.innerHTML = esc(data.error); err.classList.add('show'); return; }
    closeEditModal(); loadPo();
  }).catch(function(e){ err.innerHTML = 'Failed: ' + esc(e.message); err.classList.add('show'); });
}

function confirmDelete(){
  if(!confirm('Delete ' + POID + '? This permanently removes the PO and all timeline events.')) return;
  fetch('/api/sc/po/' + encodeURIComponent(POID), {method: 'DELETE'})
    .then(function(r){ return r.json(); })
    .then(function(data){
      if(data.success){ window.location.href = '/supply-chain'; }
      else { alert('Delete failed: ' + (data.error || 'Unknown error')); }
    }).catch(function(e){ alert('Delete failed: ' + e.message); });
}

function openReceiveModal(){
  document.getElementById('r-units').value = '';
  document.getElementById('r-rejected').value = '0';
  document.getElementById('r-notes').value = '';
  document.getElementById('recv-err').classList.remove('show');
  document.getElementById('recv-modal').classList.add('open');
}
function closeReceiveModal(){ document.getElementById('recv-modal').classList.remove('open'); }
function recvBgClick(e){ if(e.target === document.getElementById('recv-modal')) closeReceiveModal(); }

function submitReceiveStock(){
  var err = document.getElementById('recv-err');
  err.classList.remove('show');
  var units = document.getElementById('r-units').value.trim();
  if(!units || Number(units) < 0){ err.innerHTML = 'Units received is required.'; err.classList.add('show'); return; }
  var body = {
    units_received: Number(units),
    units_rejected: Number(document.getElementById('r-rejected').value.trim() || 0),
    notes: document.getElementById('r-notes').value.trim()
  };
  fetch('/api/sc/po/' + encodeURIComponent(POID) + '/stock-received', {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  }).then(function(r){ return r.json(); }).then(function(data){
    if(data.error){ err.innerHTML = esc(data.error); err.classList.add('show'); return; }
    closeReceiveModal(); loadPo();
  }).catch(function(e){ err.innerHTML = 'Failed: ' + esc(e.message); err.classList.add('show'); });
}

loadPo();
</script>

<!-- Edit PO modal -->
<div class="modal-bg" id="edit-modal" onclick="editBgClick(event)">
  <div class="modal">
    <div class="modal-head">
      <h3>Edit Purchase Order</h3>
      <button class="modal-close" onclick="closeEditModal()">&times;</button>
    </div>
    <div class="modal-body">
      <div class="form-err" id="edit-err"></div>
      <div class="field"><label>Product Name *</label><input id="e-product" type="text" placeholder="e.g. Winter Hoodie"></div>
      <div class="field"><label>SKU (optional)</label><input id="e-sku" type="text" placeholder="e.g. HOOD-CHAR-M"></div>
      <div class="field"><label>Supplier</label><input id="e-supplier" type="text" placeholder="Supplier name"></div>
      <div class="row2">
        <div class="field"><label>Quantity *</label><input id="e-qty" type="number" min="1" placeholder="0"></div>
        <div class="field"><label>Unit Cost (BDT)</label><input id="e-cost" type="number" min="0" step="0.01" placeholder="0"></div>
      </div>
      <div class="row2">
        <div class="field"><label>Advance Paid (BDT)</label><input id="e-advance" type="number" min="0" step="0.01" placeholder="0"></div>
        <div class="field"><label>Due Date *</label><input id="e-due" type="date"></div>
      </div>
      <div class="field"><label>Notes</label><textarea id="e-notes" placeholder="Any notes for the team&hellip;"></textarea></div>
      <button class="btn btn-primary" style="width:100%" onclick="saveEdit()">Save Changes</button>
    </div>
  </div>
</div>

<!-- Receive Stock modal -->
<div class="modal-bg" id="recv-modal" onclick="recvBgClick(event)">
  <div class="modal">
    <div class="modal-head">
      <h3>Receive Stock (GRN)</h3>
      <button class="modal-close" onclick="closeReceiveModal()">&times;</button>
    </div>
    <div class="modal-body">
      <div class="form-err" id="recv-err"></div>
      <div class="row2">
        <div class="field"><label>Units Received *</label><input id="r-units" type="number" min="0" placeholder="0"></div>
        <div class="field"><label>Units Rejected</label><input id="r-rejected" type="number" min="0" value="0" placeholder="0"></div>
      </div>
      <div class="field"><label>Notes (optional)</label><textarea id="r-notes" placeholder="e.g. Minor quality issues in 5 pcs&hellip;"></textarea></div>
      <button class="btn btn-primary" style="width:100%;background:#1D9E75" onclick="submitReceiveStock()">&#10003; Confirm Receipt</button>
    </div>
  </div>
</div>
</body>
</html>"""
