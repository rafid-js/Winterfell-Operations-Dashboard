"""
Winterfell Operations Dashboard — Flask web app.

Routes:
  GET  /           login page or main dashboard
  POST /login      authenticate with DASHBOARD_PASSWORD
  GET  /logout     clear session
  GET  /api/status         all system_status rows + summary
  GET  /api/logs/<script>  last 50 alerts_log entries
  POST /api/run/<script>   trigger cron script in background thread
  GET  /api/health         DB connection + table counts
  POST /api/toggle/<script> enable/disable script
"""
import os
import sys
import json
import subprocess
import threading
from datetime import datetime
from functools import wraps

from flask import Flask, request, session, redirect, url_for, jsonify, render_template_string
from dotenv import load_dotenv
from sqlalchemy import text

BRAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'brain')
sys.path.insert(0, BRAIN)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

load_dotenv(os.path.join(BRAIN, '.env'))

from db import get_connection

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'wf-secret-2024-xk9')

DASHBOARD_PASSWORD = os.getenv('DASHBOARD_PASSWORD', 'winterfell')

# Maps script_name → cron module path
CRON_SCRIPTS = {
    'nuport_sync':    'orchestrator.cron_nuport',
    'wc_sync':        'orchestrator.cron_wc',
    'zoho_sync':      'orchestrator.cron_zoho',
    'pathao_sync':    'orchestrator.cron_pathao',
    'meta_sync':      'orchestrator.cron_meta',
    'reorder_engine': 'orchestrator.cron_reorder',
    'daily_briefing': 'orchestrator.cron_briefing',
}

# ── Auth ──────────────────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated


@app.route('/login', methods=['GET', 'POST'])
def login_page():
    error = ''
    if request.method == 'POST':
        pwd = request.form.get('password', '')
        if pwd == DASHBOARD_PASSWORD:
            session['authenticated'] = True
            return redirect(url_for('dashboard'))
        error = 'Invalid password'
    return render_template_string(LOGIN_HTML, error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route('/')
@login_required
def dashboard():
    return render_template_string(DASHBOARD_HTML)


# ── API ───────────────────────────────────────────────────────────────────────

@app.route('/api/status')
@login_required
def api_status():
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT script_name, display_name, schedule,
                   last_run_at, last_run_status, last_run_duration_sec,
                   last_error, run_count, fail_count, is_enabled, next_run_at
            FROM system_status
            ORDER BY display_name
        """)).mappings().all()

        summary = conn.execute(text("""
            SELECT
                COUNT(*) FILTER (WHERE last_run_status = 'SUCCESS') AS ok,
                COUNT(*) FILTER (WHERE last_run_status = 'FAILED')  AS failed,
                COUNT(*) FILTER (WHERE last_run_status = 'RUNNING') AS running,
                COUNT(*) FILTER (WHERE last_run_status = 'NEVER')   AS never,
                COUNT(*)                                              AS total
            FROM system_status
        """)).mappings().one()

    def fmt_row(r):
        d = dict(r)
        for k in ('last_run_at', 'next_run_at'):
            if d[k]:
                d[k] = d[k].isoformat()
        return d

    return jsonify({
        'scripts': [fmt_row(r) for r in rows],
        'summary': dict(summary),
        'server_time': datetime.now().isoformat(),
    })


@app.route('/api/logs/<script_name>')
@login_required
def api_logs(script_name):
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT severity, title, message, created_at
            FROM alerts_log
            WHERE title LIKE :prefix
            ORDER BY created_at DESC
            LIMIT 50
        """), {'prefix': f'{script_name}:%'}).mappings().all()

    def fmt(r):
        d = dict(r)
        d['created_at'] = d['created_at'].isoformat()
        return d

    return jsonify([fmt(r) for r in rows])


@app.route('/api/health')
def api_health():
    try:
        with get_connection() as conn:
            counts = {}
            for tbl in ('orders', 'customers', 'skus', 'order_items',
                        'financials', 'pathao_waybills', 'ad_spend', 'alerts_log'):
                try:
                    n = conn.execute(text(f'SELECT COUNT(*) FROM {tbl}')).scalar()
                    counts[tbl] = n
                except Exception:
                    counts[tbl] = None
        return jsonify({'status': 'ok', 'tables': counts, 'ts': datetime.now().isoformat()})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/run/<script_name>', methods=['POST'])
@login_required
def api_run(script_name):
    if script_name not in CRON_SCRIPTS:
        return jsonify({'error': f'Unknown script: {script_name}'}), 404

    module = CRON_SCRIPTS[script_name]
    repo_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')

    def _run():
        subprocess.run(
            [sys.executable, '-m', module],
            cwd=repo_root,
            capture_output=False,
        )

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return jsonify({'queued': script_name, 'ts': datetime.now().isoformat()})


@app.route('/api/toggle/<script_name>', methods=['POST'])
@login_required
def api_toggle(script_name):
    with get_connection() as conn:
        conn.execute(text("""
            UPDATE system_status
            SET is_enabled = NOT is_enabled
            WHERE script_name = :s
        """), {'s': script_name})
        conn.commit()
        row = conn.execute(text(
            "SELECT is_enabled FROM system_status WHERE script_name = :s"
        ), {'s': script_name}).mappings().one_or_none()

    if not row:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({'script_name': script_name, 'is_enabled': row['is_enabled']})


@app.route('/products')
@login_required
def products_page():
    return render_template_string(PRODUCTS_HTML)


_PRODUCT_STATUS_FILTERS = {
    'delivered': "o.nuport_status IN ('DELIVERED', 'Delivered', 'delivered', 'COMPLETED')",
    'on_hold':   "o.nuport_status IN ('ON_HOLD', 'On_Hold', 'on_hold')",
    'total':     "o.nuport_status IS NOT NULL",
}

@app.route('/api/products')
@login_required
def api_products():
    try:
        limit = max(1, min(500, int(request.args.get('limit', 50))))
        days_raw = request.args.get('days')
        days = max(1, min(3650, int(days_raw))) if days_raw else None
        group = request.args.get('group', 'delivered')
        if group not in _PRODUCT_STATUS_FILTERS:
            group = 'delivered'
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid parameters'}), 400

    status_filter = _PRODUCT_STATUS_FILTERS[group]
    date_filter = f"AND COALESCE(o.order_date, o.delivered_date, o.shipped_date) >= NOW() - INTERVAL '{days} days'" if days else ""

    with get_connection() as conn:
        rows = conn.execute(text(f"""
            SELECT
                TRIM(regexp_replace(
                    COALESCE(s.product_name, oi.product_name),
                    '\\s*-\\s*(XS|S|M|L|XL|2XL|XXL|3XL|XXXL|4XL|5XL|[23][0-9])\\s*$',
                    '',
                    'i'
                )) AS base_name,
                MIN(s.image_url)                 AS image_url,
                SUM(oi.quantity)                 AS qty_sold,
                COUNT(DISTINCT o.so_number)      AS orders,
                COALESCE(SUM(oi.total_price), 0) AS revenue
            FROM order_items oi
            JOIN orders o ON oi.so_number = o.so_number
            LEFT JOIN skus s ON oi.sku = s.sku
            WHERE {status_filter}
              {date_filter}
            GROUP BY base_name
            ORDER BY qty_sold DESC
            LIMIT {limit}
        """)).fetchall()

    return jsonify({
        'products': [
            {
                'rank': i + 1,
                'name': r[0] or '—',
                'image_url': r[1],
                'qty': int(r[2]),
                'orders': int(r[3]),
                'revenue': float(r[4]),
            }
            for i, r in enumerate(rows)
        ],
        'limit': limit,
        'days': days,
        'group': group,
        'count': len(rows),
    })


@app.route('/customers')
@login_required
def customers_page():
    return render_template_string(CUSTOMERS_HTML)


@app.route('/api/customers')
@login_required
def api_customers():
    try:
        limit = max(1, min(500, int(request.args.get('limit', 50))))
        days_raw = request.args.get('days')
        days = max(1, min(3650, int(days_raw))) if days_raw else None
        group = request.args.get('group', 'delivered')
        if group not in _PRODUCT_STATUS_FILTERS:
            group = 'delivered'
        sort = request.args.get('sort', 'orders')
        if sort not in ('qty', 'orders', 'revenue'):
            sort = 'orders'
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid parameters'}), 400

    status_filter = _PRODUCT_STATUS_FILTERS[group]
    date_filter = f"AND COALESCE(o.order_date, o.delivered_date, o.shipped_date) >= NOW() - INTERVAL '{days} days'" if days else ""
    sort_col = {'qty': 'qty_bought', 'orders': 'total_orders', 'revenue': 'revenue'}[sort]

    with get_connection() as conn:
        rows = conn.execute(text(f"""
            WITH item_totals AS (
                SELECT so_number, SUM(quantity) AS qty
                FROM order_items
                GROUP BY so_number
            )
            SELECT
                o.customer_phone                           AS phone,
                MAX(COALESCE(c.name, o.customer_name))     AS cust_name,
                MAX(c.address)                             AS location,
                COALESCE(SUM(it.qty), 0)                   AS qty_bought,
                COUNT(DISTINCT o.so_number)                AS total_orders,
                COALESCE(SUM(o.product_total), 0)          AS revenue
            FROM orders o
            LEFT JOIN customers c ON o.customer_phone = c.phone
            LEFT JOIN item_totals it ON o.so_number = it.so_number
            WHERE {status_filter}
              {date_filter}
              AND o.customer_phone IS NOT NULL
              AND LENGTH(o.customer_phone) >= 10
              AND o.customer_phone ~ '^[+0-9]'
              AND o.so_number ~ '^(SO|WIN)-[0-9]+$'
            GROUP BY o.customer_phone
            ORDER BY {sort_col} DESC
            LIMIT {limit}
        """)).fetchall()

    return jsonify({
        'customers': [
            {
                'rank':     i + 1,
                'name':     r[1] or '—',
                'phone':    r[0] or '—',
                'location': r[2] or '',
                'qty':      int(r[3]) if r[3] else 0,
                'orders':   int(r[4]) if r[4] else 0,
                'revenue':  float(r[5]),
            }
            for i, r in enumerate(rows)
        ],
        'limit': limit,
        'days':  days,
        'group': group,
        'sort':  sort,
        'count': len(rows),
    })


# ── HTML Templates ─────────────────────────────────────────────────────────────

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Winterfell — Login</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',system-ui,sans-serif;
     display:flex;align-items:center;justify-content:center;min-height:100vh}
.card{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:40px 48px;width:360px}
h1{font-size:1.4rem;font-weight:600;margin-bottom:6px;color:#f0f6fc}
.sub{color:#8b949e;font-size:.85rem;margin-bottom:28px}
label{display:block;font-size:.8rem;color:#8b949e;margin-bottom:6px;text-transform:uppercase;letter-spacing:.05em}
input{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px;
      color:#e6edf3;font-size:.95rem;padding:10px 14px;outline:none;transition:border-color .2s}
input:focus{border-color:#388bfd}
.btn{width:100%;margin-top:20px;background:#238636;border:none;border-radius:6px;
     color:#fff;cursor:pointer;font-size:.95rem;font-weight:600;padding:11px;
     transition:background .2s}
.btn:hover{background:#2ea043}
.err{color:#f85149;font-size:.85rem;margin-top:12px;text-align:center}
</style>
</head>
<body>
<div class="card">
  <h1>⚔️ Winterfell</h1>
  <p class="sub">Operations Dashboard</p>
  <form method="post">
    <label>Password</label>
    <input type="password" name="password" autofocus placeholder="Enter dashboard password">
    <button class="btn" type="submit">Enter</button>
    {% if error %}<p class="err">{{ error }}</p>{% endif %}
  </form>
</div>
</body>
</html>"""


DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Winterfell — Operations Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh}
header{background:#161b22;border-bottom:1px solid #30363d;padding:16px 24px;
       display:flex;align-items:center;justify-content:space-between}
header h1{font-size:1.2rem;font-weight:700;color:#f0f6fc}
.header-right{display:flex;align-items:center;gap:16px}
#server-time{color:#8b949e;font-size:.8rem}
.top-nav{display:flex;gap:4px}
.nav-link{color:#8b949e;font-size:.8rem;text-decoration:none;padding:4px 12px;
          border:1px solid transparent;border-radius:6px;transition:.2s}
.nav-link:hover{border-color:#30363d;color:#e6edf3}
.nav-link.active{border-color:#30363d;color:#e6edf3;background:#21262d}
.logout{color:#8b949e;font-size:.8rem;text-decoration:none;padding:4px 10px;
        border:1px solid #30363d;border-radius:6px;transition:border-color .2s}
.logout:hover{border-color:#8b949e;color:#e6edf3}
.container{max-width:1200px;margin:0 auto;padding:24px}
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:28px}
.stat{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px 20px;text-align:center}
.stat .num{font-size:2rem;font-weight:700}
.stat .lbl{color:#8b949e;font-size:.75rem;margin-top:4px;text-transform:uppercase;letter-spacing:.05em}
.green{color:#3fb950}.red{color:#f85149}.yellow{color:#d29922}.gray{color:#8b949e}
.scripts{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:20px}
.card-header{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:12px}
.card-title{font-weight:600;font-size:.95rem;color:#f0f6fc}
.card-sched{color:#8b949e;font-size:.75rem;margin-top:3px}
.badge{display:inline-block;padding:2px 10px;border-radius:20px;font-size:.72rem;font-weight:600;text-transform:uppercase}
.badge-SUCCESS{background:#0d4429;color:#3fb950;border:1px solid #1a7f37}
.badge-FAILED{background:#3d0c0c;color:#f85149;border:1px solid #8e1a1a}
.badge-RUNNING{background:#1c2a3f;color:#58a6ff;border:1px solid #1f6feb}
.badge-NEVER{background:#1c1c1c;color:#8b949e;border:1px solid #30363d}
.badge-DISABLED{background:#1c1c1c;color:#8b949e;border:1px solid #30363d}
.card-meta{display:flex;gap:16px;font-size:.78rem;color:#8b949e;margin-top:10px}
.card-error{margin-top:10px;font-size:.75rem;color:#f85149;background:#1a0a0a;
            border:1px solid #3d1515;border-radius:6px;padding:8px 10px;
            white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}
.card-actions{display:flex;gap:8px;margin-top:14px}
.btn{padding:6px 14px;border-radius:6px;border:none;cursor:pointer;font-size:.78rem;font-weight:600;transition:.2s}
.btn-run{background:#0f3d20;color:#3fb950;border:1px solid #1a7f37}
.btn-run:hover{background:#1a6b38}
.btn-run:disabled{opacity:.4;cursor:not-allowed}
.btn-logs{background:#1c2030;color:#79c0ff;border:1px solid #1f4070}
.btn-logs:hover{background:#1f3050}
.btn-toggle{background:#1c1c1c;color:#8b949e;border:1px solid #30363d}
.btn-toggle:hover{background:#252525}
.health{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:20px;margin-top:28px}
.health h2{font-size:.9rem;font-weight:600;margin-bottom:14px;color:#f0f6fc}
.health-grid{display:flex;flex-wrap:wrap;gap:12px}
.health-item{background:#0d1117;border:1px solid #21262d;border-radius:8px;
             padding:10px 16px;min-width:130px;text-align:center}
.health-item .tbl{font-size:.7rem;color:#8b949e;text-transform:uppercase;letter-spacing:.05em}
.health-item .cnt{font-size:1.3rem;font-weight:700;color:#79c0ff;margin-top:2px}
/* Modal */
.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:100;
          align-items:center;justify-content:center}
.modal-bg.open{display:flex}
.modal{background:#161b22;border:1px solid #30363d;border-radius:12px;
       width:min(700px,95vw);max-height:80vh;display:flex;flex-direction:column}
.modal-header{padding:16px 20px;border-bottom:1px solid #30363d;
              display:flex;align-items:center;justify-content:space-between}
.modal-header h3{font-size:.95rem;font-weight:600}
.modal-close{background:none;border:none;color:#8b949e;font-size:1.4rem;cursor:pointer;line-height:1}
.modal-body{overflow-y:auto;padding:16px 20px;flex:1}
.log-entry{margin-bottom:10px;font-size:.78rem;border-left:3px solid #30363d;padding-left:10px}
.log-entry.SUCCESS{border-color:#3fb950}
.log-entry.ERROR{border-color:#f85149}
.log-entry.INFO{border-color:#388bfd}
.log-ts{color:#8b949e;margin-bottom:2px}
.log-title{font-weight:600;margin-bottom:2px}
.log-msg{color:#8b949e;white-space:pre-wrap;word-break:break-word;max-height:120px;overflow-y:auto}
</style>
</head>
<body>
<header>
  <h1>⚔️ Winterfell Operations</h1>
  <div class="header-right">
    <nav class="top-nav">
      <a href="/" class="nav-link active">Operations</a>
      <a href="/products" class="nav-link">Products</a>
      <a href="/customers" class="nav-link">Customers</a>
    </nav>
    <span id="server-time">—</span>
    <a href="/logout" class="logout">Logout</a>
  </div>
</header>
<div class="container">
  <div class="summary" id="summary">
    <div class="stat"><div class="num green" id="s-ok">—</div><div class="lbl">Healthy</div></div>
    <div class="stat"><div class="num red"   id="s-fail">—</div><div class="lbl">Failed</div></div>
    <div class="stat"><div class="num yellow" id="s-run">—</div><div class="lbl">Running</div></div>
    <div class="stat"><div class="num gray"  id="s-never">—</div><div class="lbl">Never Run</div></div>
    <div class="stat"><div class="num"       id="s-total">—</div><div class="lbl">Total Scripts</div></div>
  </div>
  <div class="scripts" id="scripts-grid"></div>
  <div class="health">
    <h2>Database Health</h2>
    <div class="health-grid" id="health-grid"><span style="color:#8b949e;font-size:.8rem">Loading...</span></div>
  </div>
</div>

<!-- Log Modal -->
<div class="modal-bg" id="log-modal">
  <div class="modal">
    <div class="modal-header">
      <h3 id="modal-title">Logs</h3>
      <button class="modal-close" onclick="closeModal()">✕</button>
    </div>
    <div class="modal-body" id="modal-body"></div>
  </div>
</div>

<script>
let refreshTimer;

function statusBadge(s, enabled) {
  if (!enabled) return '<span class="badge badge-DISABLED">Disabled</span>';
  const cls = ['SUCCESS','FAILED','RUNNING','NEVER'].includes(s) ? s : 'NEVER';
  return `<span class="badge badge-${cls}">${cls}</span>`;
}

function relTime(iso) {
  if (!iso) return 'Never';
  const diff = Math.floor((Date.now() - new Date(iso)) / 1000);
  if (diff < 60) return diff + 's ago';
  if (diff < 3600) return Math.floor(diff/60) + 'm ago';
  if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
  return Math.floor(diff/86400) + 'd ago';
}

async function loadStatus() {
  try {
    const r = await fetch('/api/status');
    const data = await r.json();
    const { scripts, summary, server_time } = data;

    document.getElementById('s-ok').textContent    = summary.ok;
    document.getElementById('s-fail').textContent  = summary.failed;
    document.getElementById('s-run').textContent   = summary.running;
    document.getElementById('s-never').textContent = summary.never;
    document.getElementById('s-total').textContent = summary.total;
    document.getElementById('server-time').textContent =
      new Date(server_time).toLocaleTimeString();

    const grid = document.getElementById('scripts-grid');
    grid.innerHTML = scripts.map(sc => {
      const err = sc.last_error
        ? `<div class="card-error" title="${sc.last_error.replace(/"/g,'&quot;')}" onclick="showError(this)">${sc.last_error}</div>`
        : '';
      const dur = sc.last_run_duration_sec ? `${sc.last_run_duration_sec}s` : '—';
      const runs = `${sc.run_count} runs · ${sc.fail_count} fails`;
      const toggleLabel = sc.is_enabled ? 'Disable' : 'Enable';
      return `<div class="card" id="card-${sc.script_name}">
        <div class="card-header">
          <div>
            <div class="card-title">${sc.display_name || sc.script_name}</div>
            <div class="card-sched">${sc.schedule || '—'}</div>
          </div>
          ${statusBadge(sc.last_run_status, sc.is_enabled)}
        </div>
        <div class="card-meta">
          <span>Last run: ${relTime(sc.last_run_at)}</span>
          <span>Duration: ${dur}</span>
          <span>${runs}</span>
        </div>
        ${err}
        <div class="card-actions">
          <button class="btn btn-run" onclick="runScript('${sc.script_name}',this)">▶ Run Now</button>
          <button class="btn btn-logs" onclick="showLogs('${sc.script_name}','${sc.display_name||sc.script_name}')">Logs</button>
          <button class="btn btn-toggle" onclick="toggleScript('${sc.script_name}',this)">${toggleLabel}</button>
        </div>
      </div>`;
    }).join('');
  } catch(e) {
    console.error('Status load failed', e);
  }
}

async function loadHealth() {
  try {
    const r = await fetch('/api/health');
    const data = await r.json();
    if (data.status !== 'ok') return;
    const grid = document.getElementById('health-grid');
    grid.innerHTML = Object.entries(data.tables).map(([t,n]) =>
      `<div class="health-item"><div class="tbl">${t}</div><div class="cnt">${n!=null ? n.toLocaleString() : '—'}</div></div>`
    ).join('');
  } catch(e) {}
}

async function runScript(name, btn) {
  btn.disabled = true;
  btn.textContent = '⏳ Queued';
  try {
    await fetch(`/api/run/${name}`, {method:'POST'});
    setTimeout(() => { btn.disabled=false; btn.textContent='▶ Run Now'; loadStatus(); }, 3000);
  } catch(e) {
    btn.disabled=false; btn.textContent='▶ Run Now';
  }
}

async function toggleScript(name, btn) {
  try {
    const r = await fetch(`/api/toggle/${name}`, {method:'POST'});
    const d = await r.json();
    btn.textContent = d.is_enabled ? 'Disable' : 'Enable';
    loadStatus();
  } catch(e) {}
}

async function showLogs(name, title) {
  document.getElementById('modal-title').textContent = title + ' — Logs';
  document.getElementById('modal-body').innerHTML = '<p style="color:#8b949e;font-size:.8rem">Loading...</p>';
  document.getElementById('log-modal').classList.add('open');
  try {
    const r = await fetch(`/api/logs/${name}`);
    const logs = await r.json();
    if (!logs.length) {
      document.getElementById('modal-body').innerHTML = '<p style="color:#8b949e;font-size:.8rem">No logs yet.</p>';
      return;
    }
    document.getElementById('modal-body').innerHTML = logs.map(l =>
      `<div class="log-entry ${l.severity}">
        <div class="log-ts">${new Date(l.created_at).toLocaleString()}</div>
        <div class="log-title">${l.title}</div>
        <div class="log-msg">${l.message||''}</div>
      </div>`
    ).join('');
  } catch(e) {
    document.getElementById('modal-body').innerHTML = '<p style="color:#f85149;font-size:.8rem">Failed to load logs.</p>';
  }
}

function closeModal() {
  document.getElementById('log-modal').classList.remove('open');
}

function showError(el) {
  alert(el.title);
}

document.getElementById('log-modal').addEventListener('click', function(e) {
  if (e.target === this) closeModal();
});

function refresh() {
  loadStatus();
  loadHealth();
}

refresh();
refreshTimer = setInterval(refresh, 30000);
</script>
</body>
</html>"""


PRODUCTS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Winterfell — Top Products</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh}
header{background:#161b22;border-bottom:1px solid #30363d;padding:16px 24px;
       display:flex;align-items:center;justify-content:space-between}
header h1{font-size:1.2rem;font-weight:700;color:#f0f6fc}
.header-right{display:flex;align-items:center;gap:16px}
.top-nav{display:flex;gap:4px}
.nav-link{color:#8b949e;font-size:.8rem;text-decoration:none;padding:4px 12px;
          border:1px solid transparent;border-radius:6px;transition:.2s}
.nav-link:hover{border-color:#30363d;color:#e6edf3}
.nav-link.active{border-color:#30363d;color:#e6edf3;background:#21262d}
.logout{color:#8b949e;font-size:.8rem;text-decoration:none;padding:4px 10px;
        border:1px solid #30363d;border-radius:6px;transition:.2s}
.logout:hover{border-color:#8b949e;color:#e6edf3}
.container{max-width:1100px;margin:0 auto;padding:24px}
.page-title{font-size:1.05rem;font-weight:700;color:#f0f6fc;margin-bottom:18px}
.filters{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
         background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 18px;margin-bottom:16px}
.filter-label{color:#8b949e;font-size:.72rem;text-transform:uppercase;letter-spacing:.06em}
.period-btns{display:flex;gap:6px}
.period-btn{background:#0d1117;border:1px solid #30363d;border-radius:6px;
            color:#8b949e;cursor:pointer;font-size:.8rem;padding:5px 14px;transition:.2s}
.period-btn:hover{border-color:#58a6ff;color:#79c0ff}
.period-btn.active{background:#1c2a3f;border-color:#1f6feb;color:#58a6ff;font-weight:600}
.sep{color:#30363d}
.limit-sel{background:#0d1117;border:1px solid #30363d;border-radius:6px;
           color:#e6edf3;font-size:.8rem;padding:5px 10px;cursor:pointer;outline:none}
.limit-sel:focus{border-color:#388bfd}
.group-tabs{display:flex;gap:0;margin-bottom:18px;border-bottom:2px solid #21262d}
.group-tab{background:none;border:none;border-bottom:3px solid transparent;
           color:#8b949e;cursor:pointer;font-size:.9rem;font-weight:500;
           padding:10px 22px;transition:.2s;margin-bottom:-2px}
.group-tab:hover{color:#e6edf3}
.group-tab.active{border-bottom-color:#58a6ff;color:#58a6ff;font-weight:700}
.result-info{color:#8b949e;font-size:.78rem;margin-bottom:10px;min-height:1.2em}
.table-wrap{background:#161b22;border:1px solid #30363d;border-radius:10px;overflow:auto}
table{width:100%;border-collapse:collapse}
thead th{background:#1c2030;color:#8b949e;font-size:.7rem;font-weight:600;
         text-transform:uppercase;letter-spacing:.05em;padding:10px 16px;text-align:left;
         border-bottom:1px solid #30363d;white-space:nowrap}
thead th.r{text-align:right}
tbody tr{border-bottom:1px solid #21262d;transition:background .12s}
tbody tr:last-child{border-bottom:none}
tbody tr:hover{background:#1c2030}
td{padding:10px 16px;font-size:.83rem}
td.rank{color:#8b949e;font-weight:600;width:44px}
td.name{color:#f0f6fc;max-width:380px}
.name-cell{display:flex;align-items:center;gap:10px}
.prod-img{width:64px;height:64px;object-fit:cover;border-radius:8px;
          border:1px solid #30363d;flex-shrink:0;background:#21262d}
.prod-img-ph{width:64px;height:64px;border-radius:8px;border:1px solid #30363d;
             flex-shrink:0;background:#21262d;display:flex;align-items:center;
             justify-content:center;color:#30363d;font-size:1.5rem}
td.r{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.qty{color:#3fb950;font-weight:600}
td.ord{color:#79c0ff}
td.rev{color:#e6edf3}
.state{text-align:center;padding:48px;color:#8b949e;font-size:.85rem}
.state.err{color:#f85149}
</style>
</head>
<body>
<header>
  <h1>⚔️ Winterfell Operations</h1>
  <div class="header-right">
    <nav class="top-nav">
      <a href="/" class="nav-link">Operations</a>
      <a href="/products" class="nav-link active">Products</a>
      <a href="/customers" class="nav-link">Customers</a>
    </nav>
    <a href="/logout" class="logout">Logout</a>
  </div>
</header>
<div class="container">
  <div class="page-title">Top Products</div>
  <div class="group-tabs">
    <button class="group-tab active" onclick="setGroup('delivered',this)">Delivered</button>
    <button class="group-tab" onclick="setGroup('on_hold',this)">On Hold (Pre-orders)</button>
    <button class="group-tab" onclick="setGroup('total',this)">Total (All Statuses)</button>
  </div>
  <div class="filters">
    <span class="filter-label">Period</span>
    <div class="period-btns">
      <button class="period-btn active" onclick="setPeriod(null,this)">All Time</button>
      <button class="period-btn" onclick="setPeriod(90,this)">3 Months</button>
      <button class="period-btn" onclick="setPeriod(30,this)">1 Month</button>
      <button class="period-btn" onclick="setPeriod(7,this)">7 Days</button>
    </div>
    <span class="sep">|</span>
    <span class="filter-label">Show</span>
    <select class="limit-sel" id="limit-sel" onchange="load()">
      <option value="20">Top 20</option>
      <option value="50" selected>Top 50</option>
      <option value="100">Top 100</option>
      <option value="200">Top 200</option>
    </select>
  </div>
  <div class="result-info" id="result-info">&nbsp;</div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Product</th>
          <th class="r">Qty Sold</th>
          <th class="r">Orders</th>
          <th class="r">Revenue (৳)</th>
        </tr>
      </thead>
      <tbody id="tbody">
        <tr><td colspan="5" class="state">Loading...</td></tr>
      </tbody>
    </table>
  </div>
</div>
<script>
let currentDays = null;
let currentGroup = 'delivered';

function setGroup(group, btn) {
  currentGroup = group;
  document.querySelectorAll('.group-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  load();
}

function setPeriod(days, btn) {
  currentDays = days;
  document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  load();
}

function imgErr(el) {
  var ph = document.createElement('div');
  ph.className = 'prod-img-ph';
  ph.innerHTML = '&#x1F4E6;';
  el.replaceWith(ph);
}

async function load() {
  const tbody = document.getElementById('tbody');
  const infoEl = document.getElementById('result-info');
  const limitEl = document.getElementById('limit-sel');
  if (!tbody || !limitEl) return;

  const limit = limitEl.value;
  const params = new URLSearchParams({limit, group: currentGroup});
  if (currentDays) params.set('days', currentDays);

  tbody.innerHTML = '<tr><td colspan="5" class="state">Loading...</td></tr>';
  if (infoEl) infoEl.innerHTML = '&nbsp;';

  try {
    const r = await fetch('/api/products?' + params);
    if (!r.ok) throw new Error(r.statusText);
    const data = await r.json();

    if (!data.products || !data.products.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="state">No data for this period.</td></tr>';
      return;
    }

    const periodLabel = currentDays === 90 ? 'last 3 months'
                      : currentDays === 30 ? 'last month'
                      : currentDays === 7  ? 'last 7 days'
                      : currentDays        ? 'last ' + currentDays + ' days'
                      : 'all time';
    const groupLabel = currentGroup === 'on_hold' ? 'On Hold'
                     : currentGroup === 'total'   ? 'All Statuses'
                     : 'Delivered';
    if (infoEl) infoEl.textContent = 'Showing top ' + data.products.length + ' products — ' + groupLabel + ' · ' + periodLabel;

    tbody.innerHTML = data.products.map(p => {
      const thumb = p.image_url
        ? '<img src="' + esc(p.image_url) + '" class="prod-img" loading="lazy" onerror="imgErr(this)">'
        : '<div class="prod-img-ph">&#x1F4E6;</div>';
      return '<tr>' +
        '<td class="rank r">' + p.rank + '</td>' +
        '<td class="name"><div class="name-cell">' + thumb + '<span>' + esc(p.name) + '</span></div></td>' +
        '<td class="r qty">' + p.qty.toLocaleString() + '</td>' +
        '<td class="r ord">' + p.orders.toLocaleString() + '</td>' +
        '<td class="r rev">' + Math.round(p.revenue).toLocaleString() + '</td>' +
        '</tr>';
    }).join('');
  } catch(e) {
    tbody.innerHTML = '<tr><td colspan="5" class="state err">Failed to load: ' + e.message + '</td></tr>';
  }
}

function esc(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

load();
</script>
</body>
</html>"""


CUSTOMERS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Winterfell — Customers</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6edf3;font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh}
header{background:#161b22;border-bottom:1px solid #30363d;padding:16px 24px;
       display:flex;align-items:center;justify-content:space-between}
header h1{font-size:1.2rem;font-weight:700;color:#f0f6fc}
.header-right{display:flex;align-items:center;gap:16px}
.top-nav{display:flex;gap:4px}
.nav-link{color:#8b949e;font-size:.8rem;text-decoration:none;padding:4px 12px;
          border:1px solid transparent;border-radius:6px;transition:.2s}
.nav-link:hover{border-color:#30363d;color:#e6edf3}
.nav-link.active{border-color:#30363d;color:#e6edf3;background:#21262d}
.logout{color:#8b949e;font-size:.8rem;text-decoration:none;padding:4px 10px;
        border:1px solid #30363d;border-radius:6px;transition:.2s}
.logout:hover{border-color:#8b949e;color:#e6edf3}
.container{max-width:1100px;margin:0 auto;padding:24px}
.page-title{font-size:1.05rem;font-weight:700;color:#f0f6fc;margin-bottom:18px}
.group-tabs{display:flex;gap:0;margin-bottom:18px;border-bottom:2px solid #21262d}
.group-tab{background:none;border:none;border-bottom:3px solid transparent;
           color:#8b949e;cursor:pointer;font-size:.9rem;font-weight:500;
           padding:10px 22px;transition:.2s;margin-bottom:-2px}
.group-tab:hover{color:#e6edf3}
.group-tab.active{border-bottom-color:#58a6ff;color:#58a6ff;font-weight:700}
.filters{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
         background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 18px;margin-bottom:16px}
.filter-label{color:#8b949e;font-size:.72rem;text-transform:uppercase;letter-spacing:.06em}
.period-btns{display:flex;gap:6px}
.period-btn{background:#0d1117;border:1px solid #30363d;border-radius:6px;
            color:#8b949e;cursor:pointer;font-size:.8rem;padding:5px 14px;transition:.2s}
.period-btn:hover{border-color:#58a6ff;color:#79c0ff}
.period-btn.active{background:#1c2a3f;border-color:#1f6feb;color:#58a6ff;font-weight:600}
.sep{color:#30363d}
.limit-sel{background:#0d1117;border:1px solid #30363d;border-radius:6px;
           color:#e6edf3;font-size:.8rem;padding:5px 10px;cursor:pointer;outline:none}
.limit-sel:focus{border-color:#388bfd}
.result-info{color:#8b949e;font-size:.78rem;margin-bottom:10px;min-height:1.2em}
.table-wrap{background:#161b22;border:1px solid #30363d;border-radius:10px;overflow:auto}
table{width:100%;border-collapse:collapse}
thead th{background:#1c2030;color:#8b949e;font-size:.7rem;font-weight:600;
         text-transform:uppercase;letter-spacing:.05em;padding:10px 16px;text-align:left;
         border-bottom:1px solid #30363d;white-space:nowrap}
thead th.r{text-align:right}
tbody tr{border-bottom:1px solid #21262d;transition:background .12s}
tbody tr:last-child{border-bottom:none}
tbody tr:hover{background:#1c2030}
td{padding:10px 16px;font-size:.83rem;vertical-align:middle}
td.rank{color:#8b949e;font-weight:600;width:44px}
.cust-cell{display:flex;flex-direction:column;gap:3px;max-width:420px}
.cust-name{color:#f0f6fc;font-weight:500;font-size:.87rem}
.cust-meta{color:#8b949e;font-size:.74rem;display:flex;gap:10px;flex-wrap:wrap}
.cust-phone{color:#79c0ff}
.cust-loc{color:#8b949e;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:260px}
td.r{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.qty{color:#3fb950;font-weight:600}
td.ord{color:#79c0ff}
td.rev{color:#e6edf3}
.state{text-align:center;padding:48px;color:#8b949e;font-size:.85rem}
.state.err{color:#f85149}
</style>
</head>
<body>
<header>
  <h1>⚔️ Winterfell Operations</h1>
  <div class="header-right">
    <nav class="top-nav">
      <a href="/" class="nav-link">Operations</a>
      <a href="/products" class="nav-link">Products</a>
      <a href="/customers" class="nav-link active">Customers</a>
    </nav>
    <a href="/logout" class="logout">Logout</a>
  </div>
</header>
<div class="container">
  <div class="page-title">Top Customers</div>
  <div class="group-tabs">
    <button class="group-tab active" onclick="setGroup('delivered',this)">Delivered</button>
    <button class="group-tab" onclick="setGroup('on_hold',this)">On Hold (Pre-orders)</button>
    <button class="group-tab" onclick="setGroup('total',this)">Total (All Statuses)</button>
  </div>
  <div class="filters">
    <span class="filter-label">Period</span>
    <div class="period-btns">
      <button class="period-btn active" onclick="setPeriod(null,this)">All Time</button>
      <button class="period-btn" onclick="setPeriod(90,this)">3 Months</button>
      <button class="period-btn" onclick="setPeriod(30,this)">1 Month</button>
      <button class="period-btn" onclick="setPeriod(7,this)">7 Days</button>
    </div>
    <span class="sep">|</span>
    <span class="filter-label">Show</span>
    <select class="limit-sel" id="limit-sel" onchange="load()">
      <option value="20">Top 20</option>
      <option value="50" selected>Top 50</option>
      <option value="100">Top 100</option>
      <option value="200">Top 200</option>
    </select>
    <span class="sep">|</span>
    <span class="filter-label">Sort By</span>
    <select class="limit-sel" id="sort-sel" onchange="load()">
      <option value="orders" selected>Order Frequency</option>
      <option value="qty">Qty Bought</option>
      <option value="revenue">Revenue</option>
    </select>
  </div>
  <div class="result-info" id="result-info">&nbsp;</div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Customer</th>
          <th class="r">Qty Bought</th>
          <th class="r">Orders</th>
          <th class="r">Revenue (৳)</th>
        </tr>
      </thead>
      <tbody id="tbody">
        <tr><td colspan="5" class="state">Loading...</td></tr>
      </tbody>
    </table>
  </div>
</div>
<script>
let currentDays = null;
let currentGroup = 'delivered';

function setGroup(group, btn) {
  currentGroup = group;
  document.querySelectorAll('.group-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  load();
}

function setPeriod(days, btn) {
  currentDays = days;
  document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  load();
}

async function load() {
  const tbody = document.getElementById('tbody');
  const infoEl = document.getElementById('result-info');
  const limitEl = document.getElementById('limit-sel');
  if (!tbody || !limitEl) return;

  const limit = limitEl.value;
  const sortEl = document.getElementById('sort-sel');
  const sort = sortEl ? sortEl.value : 'orders';
  const params = new URLSearchParams({limit, group: currentGroup, sort});
  if (currentDays) params.set('days', currentDays);

  tbody.innerHTML = '<tr><td colspan="5" class="state">Loading...</td></tr>';
  if (infoEl) infoEl.innerHTML = '&nbsp;';

  try {
    const r = await fetch('/api/customers?' + params);
    if (!r.ok) throw new Error(r.statusText);
    const data = await r.json();

    if (!data.customers || !data.customers.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="state">No data for this period.</td></tr>';
      return;
    }

    const periodLabel = currentDays === 90 ? 'last 3 months'
                      : currentDays === 30 ? 'last month'
                      : currentDays === 7  ? 'last 7 days'
                      : currentDays        ? 'last ' + currentDays + ' days'
                      : 'all time';
    const groupLabel = currentGroup === 'on_hold' ? 'On Hold'
                     : currentGroup === 'total'   ? 'All Statuses'
                     : 'Delivered';
    const sortLabel = sort === 'qty' ? 'by qty' : sort === 'revenue' ? 'by revenue' : 'by order frequency';
    if (infoEl) infoEl.textContent = 'Showing top ' + data.customers.length + ' customers — ' + groupLabel + ' · ' + periodLabel + ' · ' + sortLabel;

    tbody.innerHTML = data.customers.map(c => {
      const loc = c.location ? (c.location.length > 50 ? c.location.slice(0,50) + '...' : c.location) : '';
      const meta = c.phone + (loc ? ' · ' + loc : '');
      return '<tr>' +
        '<td class="rank r">' + c.rank + '</td>' +
        '<td><div class="cust-cell">' +
          '<div class="cust-name">' + esc(c.name) + '</div>' +
          '<div class="cust-meta">' +
            '<span class="cust-phone">' + esc(c.phone) + '</span>' +
            (loc ? '<span class="cust-loc" title="' + esc(c.location) + '">' + esc(loc) + '</span>' : '') +
          '</div>' +
        '</div></td>' +
        '<td class="r qty">' + c.qty.toLocaleString() + '</td>' +
        '<td class="r ord">' + c.orders.toLocaleString() + '</td>' +
        '<td class="r rev">' + Math.round(c.revenue).toLocaleString() + '</td>' +
        '</tr>';
    }).join('');
  } catch(e) {
    tbody.innerHTML = '<tr><td colspan="5" class="state err">Failed to load: ' + e.message + '</td></tr>';
  }
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

load();
</script>
</body>
</html>"""


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
