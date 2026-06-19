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

# ── Supply Chain blueprint ──────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from supply_chain import sc_bp
app.register_blueprint(sc_bp)

# ── Inventory blueprint ─────────────────────────────────────────────────────
from inventory import inv_bp
app.register_blueprint(inv_bp)

# ── Agents blueprint ─────────────────────────────────────────────────────────
from agents_routes import agents_bp
app.register_blueprint(agents_bp)

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
    'return':    "o.nuport_status ILIKE '%return%' OR o.nuport_status ILIKE '%refund%'",
    'exchange':  "o.nuport_status ILIKE '%exchange%' OR o.nuport_status ILIKE '%replacement%'",
    'flagged':   "(o.nuport_status ILIKE '%flag%' OR o.nuport_status ILIKE '%return%' OR o.nuport_status ILIKE '%refund%' OR o.nuport_status ILIKE '%exchange%')",
    'total':     "o.nuport_status IS NOT NULL",
}

_ORDER_TAB_FILTERS = {
    'all':           "1=1",
    'pending':       "UPPER(o.nuport_status) IN ('PENDING', 'REQUESTED')",
    'on_hold':       "o.nuport_status ILIKE 'on%hold'",
    'approved':      "o.nuport_status ILIKE 'approv%'",
    'processing':    "o.nuport_status ILIKE 'process%'",
    'ready_to_ship': "o.nuport_status ILIKE 'ready%'",
    'in_transit':    "o.nuport_status ILIKE '%transit%' OR o.nuport_status ILIKE 'in%transit'",
    'delivered':     "UPPER(o.nuport_status) IN ('DELIVERED', 'COMPLETED')",
    'flagged':       "o.nuport_status ILIKE '%flag%'",
    'cancelled':     "o.nuport_status ILIKE '%cancel%'",
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
        search   = request.args.get('search',   '').strip()[:100]
        category = request.args.get('category', '').strip()[:200]
        channel  = request.args.get('channel',  '').strip()[:200]
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid parameters'}), 400

    status_filter = _PRODUCT_STATUS_FILTERS[group]
    date_filter   = f"AND COALESCE(o.order_date, o.delivered_date, o.shipped_date) >= NOW() - INTERVAL '{days} days'" if days else ""
    sql_params    = {}

    search_filter = ""
    if search:
        search_filter = "AND COALESCE(s.product_name, oi.product_name) ILIKE :search_pat"
        sql_params['search_pat'] = f'%{search}%'

    category_filter = ""
    if category:
        kws = [k.strip() for k in category.split(',') if k.strip()]
        if kws:
            conds = ' OR '.join(f"COALESCE(s.product_name, oi.product_name) ILIKE :pcat{i}" for i in range(len(kws)))
            category_filter = f"AND ({conds})"
            for i, kw in enumerate(kws):
                sql_params[f'pcat{i}'] = f'%{kw}%'

    channel_filter = ""
    if channel:
        chs = [c.strip() for c in channel.split(',') if c.strip()]
        if chs:
            conds = ' OR '.join(f"o.source_channel ILIKE :pch{i}" for i in range(len(chs)))
            channel_filter = f"AND ({conds})"
            for i, ch in enumerate(chs):
                sql_params[f'pch{i}'] = f'%{ch}%'

    with get_connection() as conn:
        rows = conn.execute(text(f"""
            SELECT
                TRIM(regexp_replace(
                    COALESCE(s.product_name, oi.product_name),
                    '\\s*-\\s*(XS|S|M|L|XL|2XL|XXL|3XL|XXXL|4XL|5XL|[2-5][0-9])(\\s*\\([^)]*\\))?\\s*$',
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
              {search_filter}
              {category_filter}
              {channel_filter}
              AND oi.product_name IS NOT NULL
              AND oi.product_name !~ '^[0-9][0-9.,\\s]*$'
            GROUP BY base_name
            ORDER BY qty_sold DESC
            LIMIT {limit}
        """), sql_params).fetchall()

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
        search     = request.args.get('search', '').strip()[:100]
        has_email  = request.args.get('has_email', '') == '1'
        category   = request.args.get('category', '').strip()[:200]
        channel    = request.args.get('channel', '').strip()[:200]
        min_orders = max(1, int(request.args.get('min_orders', 1)))
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid parameters'}), 400

    status_filter = _PRODUCT_STATUS_FILTERS[group]
    date_filter = f"AND COALESCE(o.order_date, o.delivered_date, o.shipped_date) >= NOW() - INTERVAL '{days} days'" if days else ""
    sort_col = {'qty': 'qty_bought', 'orders': 'total_orders', 'revenue': 'revenue'}[sort]
    sql_params = {}

    search_filter = ""
    if search:
        search_filter = """AND (
            COALESCE(c.name, o.customer_name) ILIKE :search_pat OR
            o.customer_phone ILIKE :search_pat OR
            c.address ILIKE :search_pat OR
            c.email ILIKE :search_pat
        )"""
        sql_params['search_pat'] = f'%{search}%'

    has_email_filter = "AND c.email IS NOT NULL AND c.email <> ''" if has_email else ""

    category_filter = ""
    if category:
        kws = [k.strip() for k in category.split(',') if k.strip()]
        if kws:
            conds = ' OR '.join(f"oi_cat.product_name ILIKE :cat{i}" for i in range(len(kws)))
            category_filter = f"""AND EXISTS (
                SELECT 1 FROM order_items oi_cat
                WHERE oi_cat.so_number = o.so_number AND ({conds})
            )"""
            for i, kw in enumerate(kws):
                sql_params[f'cat{i}'] = f'%{kw}%'

    channel_filter = ""
    if channel:
        chs = [c.strip() for c in channel.split(',') if c.strip()]
        if chs:
            conds = ' OR '.join(f"o.source_channel ILIKE :ch{i}" for i in range(len(chs)))
            channel_filter = f"AND ({conds})"
            for i, ch in enumerate(chs):
                sql_params[f'ch{i}'] = f'%{ch}%'

    having_clause = f"HAVING COUNT(DISTINCT o.so_number) >= {min_orders}" if min_orders > 1 else ""

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
                MAX(c.email)                               AS email,
                COALESCE(SUM(it.qty), 0)                   AS qty_bought,
                COUNT(DISTINCT o.so_number)                AS total_orders,
                COALESCE(SUM(o.product_total), 0)          AS revenue
            FROM orders o
            LEFT JOIN customers c ON o.customer_phone = c.phone
            LEFT JOIN item_totals it ON o.so_number = it.so_number
            WHERE {status_filter}
              {date_filter}
              {search_filter}
              {has_email_filter}
              {category_filter}
              {channel_filter}
              AND o.customer_phone IS NOT NULL
              AND LENGTH(o.customer_phone) >= 10
              AND o.customer_phone ~ '^[+0-9]'
              AND o.so_number ~ '^(SO|WIN)-[0-9]+$'
            GROUP BY o.customer_phone
            {having_clause}
            ORDER BY {sort_col} DESC
            LIMIT {limit}
        """), sql_params).fetchall()

    return jsonify({
        'customers': [
            {
                'rank':     i + 1,
                'name':     r[1] or '—',
                'phone':    r[0] or '—',
                'location': r[2] or '',
                'email':    r[3] or '',
                'qty':      int(r[4]) if r[4] else 0,
                'orders':   int(r[5]) if r[5] else 0,
                'revenue':  float(r[6]),
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

COMMON_CSS = """
:root{
  --header-bg:#161b22;--header-border:#30363d;
  --bg-page:#F6F8FA;--bg-card:#FFFFFF;--bg-inner:#F9FAFB;
  --text-primary:#1A1F2E;--text-secondary:#4A5568;--text-tertiary:#718096;
  --border:#E1E7EF;--track:#E1E7EF;
  --teal:#1D9E75;--amber:#EF9F27;--red:#E24B4A;--purple:#7F77DD;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg-page);color:var(--text-primary);font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh}
header{background:var(--header-bg);border-bottom:1px solid var(--header-border);
       padding:12px 24px;display:grid;grid-template-columns:1fr auto 1fr;
       grid-template-areas:'brand nav actions';align-items:center;gap:8px;
       position:sticky;top:0;z-index:50}
header h1,.brand{font-size:1rem;font-weight:700;color:#f0f6fc;text-decoration:none;grid-area:brand}
.hdr-actions{grid-area:actions;display:flex;align-items:center;gap:12px;justify-content:flex-end}
.top-nav{grid-area:nav;display:flex;gap:2px}
.nav-link{color:#8b949e;font-size:.8rem;text-decoration:none;padding:5px 10px;
          border:1px solid transparent;border-radius:6px;transition:.2s;white-space:nowrap}
.nav-link:hover{border-color:#30363d;color:#e6edf3}
.nav-link.active{border-color:#30363d;color:#e6edf3;background:#21262d}
.logout{color:#8b949e;font-size:.8rem;text-decoration:none;padding:5px 10px;
        border:1px solid var(--header-border);border-radius:6px;transition:.2s;white-space:nowrap}
.logout:hover{border-color:#8b949e;color:#e6edf3}
.container{max-width:1280px;margin:0 auto;padding:24px 20px}
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
  main{padding:14px 12px!important}
  .tbl-wrap,.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
}
"""

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
<style>""" + COMMON_CSS + """
#server-time{color:#8b949e;font-size:.8rem}
.summary{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:28px}
.stat{background:var(--bg-card);border:0.5px solid var(--border);border-radius:10px;padding:16px 20px;text-align:center}
.stat .num{font-size:2rem;font-weight:700;color:var(--text-primary)}
.stat .lbl{color:var(--text-tertiary);font-size:.75rem;margin-top:4px;text-transform:uppercase;letter-spacing:.05em}
.green{color:#1D9E75}.red{color:var(--red)}.yellow{color:var(--amber)}.gray{color:var(--text-tertiary)}
.scripts{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
.card{background:var(--bg-card);border:0.5px solid var(--border);border-radius:10px;padding:20px}
.card-header{display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:12px}
.card-title{font-weight:600;font-size:.95rem;color:var(--text-primary)}
.card-sched{color:var(--text-tertiary);font-size:.75rem;margin-top:3px}
.badge{display:inline-block;padding:2px 10px;border-radius:20px;font-size:.72rem;font-weight:600;text-transform:uppercase}
.badge-SUCCESS{background:#E1F5EE;color:#085041;border:0.5px solid #5DCAA5}
.badge-FAILED{background:#FCEBEB;color:#791F1F;border:0.5px solid #F5C6C6}
.badge-RUNNING{background:#E6F1FB;color:#0C447C;border:0.5px solid #A8C8ED}
.badge-NEVER,.badge-DISABLED{background:var(--bg-inner);color:var(--text-tertiary);border:0.5px solid var(--border)}
.card-meta{display:flex;gap:16px;font-size:.78rem;color:var(--text-tertiary);margin-top:10px}
.card-error{margin-top:10px;font-size:.75rem;color:var(--red);background:#FCEBEB;
            border:0.5px solid #F5C6C6;border-radius:6px;padding:8px 10px;
            white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer}
.card-actions{display:flex;gap:8px;margin-top:14px}
.btn{padding:6px 14px;border-radius:6px;border:none;cursor:pointer;font-size:.78rem;font-weight:600;transition:.2s}
.btn-run{background:#E1F5EE;color:#085041;border:0.5px solid #5DCAA5}
.btn-run:hover{background:#C7EDE0}
.btn-run:disabled{opacity:.4;cursor:not-allowed}
.btn-logs{background:#E6F1FB;color:#0C447C;border:0.5px solid #A8C8ED}
.btn-logs:hover{background:#CCE0F5}
.btn-toggle{background:var(--bg-inner);color:var(--text-secondary);border:0.5px solid var(--border)}
.btn-toggle:hover{background:var(--border)}
.health{background:var(--bg-card);border:0.5px solid var(--border);border-radius:10px;padding:20px;margin-top:28px}
.health h2{font-size:.9rem;font-weight:600;margin-bottom:14px;color:var(--text-primary)}
.health-grid{display:flex;flex-wrap:wrap;gap:12px}
.health-item{background:var(--bg-inner);border:0.5px solid var(--border);border-radius:8px;
             padding:10px 16px;min-width:130px;text-align:center}
.health-item .tbl{font-size:.7rem;color:var(--text-tertiary);text-transform:uppercase;letter-spacing:.05em}
.health-item .cnt{font-size:1.3rem;font-weight:700;color:var(--purple);margin-top:2px}
.modal-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:100;
          align-items:center;justify-content:center}
.modal-bg.open{display:flex}
.modal{background:var(--bg-card);border:0.5px solid var(--border);border-radius:12px;
       width:min(700px,95vw);max-height:80vh;display:flex;flex-direction:column}
.modal-header{padding:16px 20px;border-bottom:0.5px solid var(--border);
              display:flex;align-items:center;justify-content:space-between}
.modal-header h3{font-size:.95rem;font-weight:600;color:var(--text-primary)}
.modal-close{background:none;border:none;color:var(--text-tertiary);font-size:1.4rem;cursor:pointer;line-height:1}
.modal-body{overflow-y:auto;padding:16px 20px;flex:1}
.log-entry{margin-bottom:10px;font-size:.78rem;border-left:3px solid var(--border);padding-left:10px}
.log-entry.SUCCESS{border-color:#1D9E75}
.log-entry.ERROR{border-color:var(--red)}
.log-entry.INFO{border-color:var(--purple)}
.log-ts{color:var(--text-tertiary);margin-bottom:2px}
.log-title{font-weight:600;color:var(--text-primary);margin-bottom:2px}
.log-msg{color:var(--text-secondary);white-space:pre-wrap;word-break:break-word;max-height:120px;overflow-y:auto}
</style>
</head>
<body>
<header>
  <h1>&#9876;&#65039; Winterfell Operations</h1>
  <nav class="top-nav">
    <a href="/" class="nav-link active">Operations</a>
    <a href="/products" class="nav-link">Products</a>
    <a href="/customers" class="nav-link">Customers</a>
    <a href="/orders" class="nav-link">Orders</a>
    <a href="/inventory" class="nav-link">Inventory</a>
    <a href="/supply-chain" class="nav-link">Supply Chain</a>
    <a href="/agents" class="nav-link">Agents</a>
  </nav>
  <div class="hdr-actions">
    <a href="/logout" class="logout">Logout</a>
    <button class="ham" onclick="document.getElementById('mnav1').classList.toggle('open')" aria-label="Menu">
      <span></span><span></span><span></span>
    </button>
  </div>
  <nav class="mob-nav" id="mnav1">
    <a href="/" class="active">Operations</a>
    <a href="/products">Products</a>
    <a href="/customers">Customers</a>
    <a href="/orders">Orders</a>
    <a href="/inventory">Inventory</a>
    <a href="/supply-chain">Supply Chain</a>
    <a href="/agents">Agents</a>
    <a href="/logout">Logout</a>
  </nav>
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
    const stEl = document.getElementById('server-time');
    if(stEl) stEl.textContent = new Date(server_time).toLocaleTimeString();

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
<style>""" + COMMON_CSS + """
.page-title{font-size:1.05rem;font-weight:700;color:var(--text-primary);margin-bottom:18px}
.filters{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
         background:var(--bg-card);border:0.5px solid var(--border);border-radius:10px;padding:14px 18px;margin-bottom:16px}
.filter-label{color:var(--text-tertiary);font-size:.72rem;text-transform:uppercase;letter-spacing:.06em}
.period-btns{display:flex;gap:6px}
.period-btn{background:var(--bg-inner);border:0.5px solid var(--border);border-radius:6px;
            color:var(--text-secondary);cursor:pointer;font-size:.8rem;padding:5px 14px;transition:.2s}
.period-btn:hover{border-color:var(--purple);color:var(--purple)}
.period-btn.active{background:#EEEDFE;border-color:var(--purple);color:var(--purple);font-weight:600}
.sep{color:var(--border)}
.limit-sel{background:var(--bg-inner);border:0.5px solid var(--border);border-radius:6px;
           color:var(--text-primary);font-size:.8rem;padding:5px 10px;cursor:pointer;outline:none}
.group-tabs{display:flex;gap:0;margin-bottom:0;border-bottom:1px solid var(--border)}
.group-tab{background:none;border:none;border-bottom:3px solid transparent;
           color:var(--text-secondary);cursor:pointer;font-size:.9rem;font-weight:500;
           padding:10px 22px;transition:.2s;margin-bottom:-1px}
.group-tab:hover{color:var(--text-primary)}
.group-tab.active{border-bottom-color:var(--purple);color:var(--purple);font-weight:700}
.group-tab.flagged-tab.active{border-bottom-color:var(--red);color:var(--red)}
.sub-tab-row{display:flex;gap:6px;padding:12px 0 12px;border-bottom:0.5px solid var(--border);margin-bottom:14px}
.sub-tab{background:var(--bg-inner);border:0.5px solid var(--border);border-radius:20px;
         color:var(--text-secondary);cursor:pointer;font-size:.8rem;font-weight:500;padding:5px 16px;transition:.2s}
.sub-tab:hover{border-color:var(--red);color:var(--red)}
.sub-tab.active{background:#FCEBEB;border-color:var(--red);color:var(--red);font-weight:600}
.group-tabs-spacer{margin-bottom:14px}
.filter-toggle{background:var(--bg-card);border:0.5px solid var(--border);border-radius:8px;
               color:var(--text-secondary);cursor:pointer;font-size:.8rem;padding:8px 14px;
               display:flex;align-items:center;gap:6px;transition:.2s;white-space:nowrap}
.filter-toggle:hover{border-color:var(--purple);color:var(--purple)}
.filter-toggle.on{border-color:var(--purple);color:var(--purple);background:#EEEDFE}
.fbadge{background:var(--purple);color:#fff;border-radius:10px;font-size:.68rem;font-weight:700;
        padding:1px 5px;display:none;line-height:1.4}
.pf-panel{background:var(--bg-card);border:0.5px solid var(--border);border-radius:10px;
          padding:16px 18px;margin-bottom:12px;display:none;flex-direction:column;gap:14px}
.fp-section{}
.fp-sec-label{color:var(--text-tertiary);font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px}
.fp-chips{display:flex;flex-wrap:wrap;gap:6px}
.fp-chip{background:var(--bg-inner);border:0.5px solid var(--border);border-radius:20px;
         color:var(--text-secondary);cursor:pointer;font-size:.78rem;padding:4px 12px;transition:.2s}
.fp-chip:hover{border-color:var(--purple);color:var(--purple)}
.fp-chip.on{background:#EEEDFE;border-color:var(--purple);color:var(--purple);font-weight:600}
.result-info{color:var(--text-secondary);font-size:.78rem;margin-bottom:10px;min-height:1.2em}
.table-wrap{background:var(--bg-card);border:0.5px solid var(--border);border-radius:10px;overflow-x:auto}
table{width:100%;border-collapse:collapse}
thead th{background:var(--bg-inner);color:var(--text-tertiary);font-size:.7rem;font-weight:600;
         text-transform:uppercase;letter-spacing:.05em;padding:10px 16px;text-align:left;
         border-bottom:0.5px solid var(--border);white-space:nowrap}
thead th.r{text-align:right}
tbody tr{border-bottom:0.5px solid var(--border);transition:background .12s}
tbody tr:last-child{border-bottom:none}
tbody tr:hover{background:var(--bg-inner)}
td{padding:10px 16px;font-size:.83rem;color:var(--text-primary)}
td.rank{color:var(--text-tertiary);font-weight:600;width:44px}
td.name{color:var(--text-primary);max-width:380px}
.name-cell{display:flex;align-items:center;gap:10px}
.prod-img{width:64px;height:64px;object-fit:cover;border-radius:8px;
          border:0.5px solid var(--border);flex-shrink:0;background:var(--bg-inner)}
.prod-img-ph{width:64px;height:64px;border-radius:8px;border:0.5px solid var(--border);
             flex-shrink:0;background:var(--bg-inner);display:flex;align-items:center;
             justify-content:center;color:var(--border);font-size:1.5rem}
td.r{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.qty{color:#1D9E75;font-weight:600}
td.ord{color:var(--purple)}
td.rev{color:var(--text-primary);font-weight:500}
.state{text-align:center;padding:48px;color:var(--text-tertiary);font-size:.85rem}
.state.err{color:var(--red)}
.toolbar{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.search-wrap{position:relative;flex:1;max-width:420px}
.search-input{width:100%;background:var(--bg-card);border:0.5px solid var(--border);border-radius:8px;
              color:var(--text-primary);font-size:.85rem;padding:8px 30px 8px 34px;outline:none;transition:border-color .2s}
.search-input:focus{border-color:var(--purple)}
.search-input::placeholder{color:var(--text-tertiary)}
.search-icon{position:absolute;left:11px;top:50%;transform:translateY(-50%);
             color:var(--text-tertiary);font-size:.8rem;pointer-events:none}
.search-clear{position:absolute;right:8px;top:50%;transform:translateY(-50%);
              background:none;border:none;color:var(--text-tertiary);cursor:pointer;font-size:.9rem;
              padding:2px 4px;line-height:1;display:none}
.search-clear:hover{color:var(--text-primary)}
.export-wrap{position:relative;margin-left:auto}
.export-btn{background:var(--bg-card);border:0.5px solid var(--border);border-radius:8px;
            color:var(--text-secondary);cursor:pointer;font-size:.8rem;padding:8px 14px;
            display:flex;align-items:center;gap:5px;transition:.2s;white-space:nowrap}
.export-btn:hover{border-color:#1D9E75;color:#1D9E75}
.export-menu{position:absolute;right:0;top:calc(100% + 4px);background:var(--bg-card);
             border:0.5px solid var(--border);border-radius:8px;padding:6px;z-index:100;
             flex-direction:column;gap:2px;min-width:148px;box-shadow:0 4px 16px rgba(0,0,0,.1)}
.export-opt{background:none;border:none;color:var(--text-primary);cursor:pointer;font-size:.82rem;
            padding:8px 12px;text-align:left;border-radius:6px;transition:.15s;width:100%}
.export-opt:hover{background:var(--bg-inner);color:#1D9E75}
</style>
</head>
<body>
<header>
  <h1>&#9876;&#65039; Winterfell Operations</h1>
  <nav class="top-nav">
    <a href="/" class="nav-link">Operations</a>
    <a href="/products" class="nav-link active">Products</a>
    <a href="/customers" class="nav-link">Customers</a>
    <a href="/orders" class="nav-link">Orders</a>
    <a href="/inventory" class="nav-link">Inventory</a>
    <a href="/supply-chain" class="nav-link">Supply Chain</a>
    <a href="/agents" class="nav-link">Agents</a>
  </nav>
  <div class="hdr-actions">
    <a href="/logout" class="logout">Logout</a>
    <button class="ham" onclick="document.getElementById('mnav2').classList.toggle('open')" aria-label="Menu">
      <span></span><span></span><span></span>
    </button>
  </div>
  <nav class="mob-nav" id="mnav2">
    <a href="/">Operations</a>
    <a href="/products" class="active">Products</a>
    <a href="/customers">Customers</a>
    <a href="/orders">Orders</a>
    <a href="/inventory">Inventory</a>
    <a href="/supply-chain">Supply Chain</a>
    <a href="/agents">Agents</a>
    <a href="/logout">Logout</a>
  </nav>
</header>
<div class="container">
  <div class="page-title">Top Products</div>
  <div class="group-tabs">
    <button class="group-tab active" onclick="setGroup('delivered',this)">Delivered</button>
    <button class="group-tab" onclick="setGroup('on_hold',this)">On Hold (Pre-orders)</button>
    <button class="group-tab flagged-tab" onclick="setFlaggedMain(this)">Flagged</button>
    <button class="group-tab" onclick="setGroup('total',this)">Total (All Statuses)</button>
  </div>
  <div class="sub-tab-row" id="flagged-sub-row" style="display:none">
    <button class="sub-tab active" data-group="flagged" onclick="setSubGroup('flagged',this)">All Flagged</button>
    <button class="sub-tab" data-group="return" onclick="setSubGroup('return',this)">&#8617; Return</button>
    <button class="sub-tab" data-group="exchange" onclick="setSubGroup('exchange',this)">&#8644; Exchange</button>
  </div>
  <div class="group-tabs-spacer" id="tabs-spacer" style="display:none"></div>
  <div class="toolbar">
    <div class="search-wrap">
      <span class="search-icon">&#128269;</span>
      <input type="text" id="search-input" class="search-input"
             placeholder="Search products..." oninput="onSearchInput()" onkeydown="if(event.key==='Escape')clearSearch()">
      <button class="search-clear" id="search-clear" onclick="clearSearch()">&#215;</button>
    </div>
    <button class="filter-toggle" id="filter-btn" onclick="toggleProdFilter()">
      &#9881; Filters <span class="fbadge" id="fbadge"></span>
    </button>
    <div class="export-wrap">
      <button class="export-btn" onclick="toggleExport(event)">&#8659; Export</button>
      <div class="export-menu" id="export-menu" style="display:none">
        <button class="export-opt" onclick="exportCSV()">&#128196; Export CSV</button>
        <button class="export-opt" onclick="exportPDF()">&#128424; PDF / Print</button>
      </div>
    </div>
  </div>
  <div class="pf-panel" id="pf-panel">
    <div class="fp-section">
      <div class="fp-sec-label">Product Category</div>
      <div class="fp-chips" id="pcat-chips">
        <button class="fp-chip" data-kw="t-shirt,tee" onclick="toggleChip(this)">T-Shirt / Tee</button>
        <button class="fp-chip" data-kw="polo" onclick="toggleChip(this)">Polo</button>
        <button class="fp-chip" data-kw="drop shoulder" onclick="toggleChip(this)">Drop Shoulder</button>
        <button class="fp-chip" data-kw="pant,jean,trouser,cargo" onclick="toggleChip(this)">Pants / Jeans</button>
        <button class="fp-chip" data-kw="jacket" onclick="toggleChip(this)">Jacket</button>
        <button class="fp-chip" data-kw="shirt" onclick="toggleChip(this)">Shirt</button>
        <button class="fp-chip" data-kw="hoodie,sweatshirt" onclick="toggleChip(this)">Hoodie</button>
        <button class="fp-chip" data-kw="waffle" onclick="toggleChip(this)">Waffle Knit</button>
        <button class="fp-chip" data-kw="corduroy" onclick="toggleChip(this)">Corduroy</button>
      </div>
    </div>
    <div class="fp-section">
      <div class="fp-sec-label">Order Channel</div>
      <div class="fp-chips" id="pch-chips">
        <button class="fp-chip" data-kw="whatsapp" onclick="toggleChip(this)">WhatsApp</button>
        <button class="fp-chip" data-kw="facebook" onclick="toggleChip(this)">Facebook</button>
        <button class="fp-chip" data-kw="instagram" onclick="toggleChip(this)">Instagram</button>
        <button class="fp-chip" data-kw="website,woocommerce" onclick="toggleChip(this)">Website</button>
        <button class="fp-chip" data-kw="tiktok" onclick="toggleChip(this)">TikTok</button>
        <button class="fp-chip" data-kw="phone,call" onclick="toggleChip(this)">Phone / Call</button>
        <button class="fp-chip" data-kw="messenger" onclick="toggleChip(this)">Messenger</button>
      </div>
    </div>
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
      <option value="500">Top 500</option>
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
let lastData = [];
let searchTimer;

function setGroup(group, btn) {
  currentGroup = group;
  document.querySelectorAll('.group-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const subRow = document.getElementById('flagged-sub-row');
  const spacer = document.getElementById('tabs-spacer');
  if (subRow) subRow.style.display = 'none';
  if (spacer) spacer.style.display = 'none';
  load();
}

function setFlaggedMain(btn) {
  document.querySelectorAll('.group-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  const subRow = document.getElementById('flagged-sub-row');
  const spacer = document.getElementById('tabs-spacer');
  if (subRow) subRow.style.display = 'flex';
  if (spacer) spacer.style.display = 'block';
  const activeSub = subRow ? subRow.querySelector('.sub-tab.active') : null;
  currentGroup = activeSub ? activeSub.dataset.group : 'flagged';
  load();
}

function setSubGroup(group, btn) {
  currentGroup = group;
  document.querySelectorAll('.sub-tab').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  load();
}

function toggleProdFilter() {
  const panel = document.getElementById('pf-panel');
  const btn   = document.getElementById('filter-btn');
  const showing = panel.style.display === 'flex';
  panel.style.display = showing ? 'none' : 'flex';
  btn.classList.toggle('on', !showing || document.querySelectorAll('.fp-chip.on').length > 0);
  updateProdBadge();
}

function toggleChip(el) {
  el.classList.toggle('on');
  updateProdBadge();
  load();
}

function updateProdBadge() {
  const cats  = document.querySelectorAll('#pcat-chips .fp-chip.on').length;
  const chs   = document.querySelectorAll('#pch-chips .fp-chip.on').length;
  const count = (cats > 0 ? 1 : 0) + (chs > 0 ? 1 : 0);
  const badge = document.getElementById('fbadge');
  const btn   = document.getElementById('filter-btn');
  if (badge) { badge.textContent = count || ''; badge.style.display = count ? 'inline' : 'none'; }
  if (btn)   btn.classList.toggle('on', count > 0 || document.getElementById('pf-panel').style.display === 'flex');
}

function getChipKws(containerId) {
  return Array.from(document.querySelectorAll('#' + containerId + ' .fp-chip.on'))
    .map(el => el.dataset.kw).join(',');
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

function onSearchInput() {
  const val = document.getElementById('search-input').value;
  const clr = document.getElementById('search-clear');
  if (clr) clr.style.display = val ? 'block' : 'none';
  clearTimeout(searchTimer);
  searchTimer = setTimeout(load, 400);
}

function clearSearch() {
  const el = document.getElementById('search-input');
  const clr = document.getElementById('search-clear');
  if (el) { el.value = ''; el.focus(); }
  if (clr) clr.style.display = 'none';
  load();
}

function toggleExport(e) {
  e.stopPropagation();
  const menu = document.getElementById('export-menu');
  menu.style.display = menu.style.display === 'none' ? 'flex' : 'none';
}
document.addEventListener('click', () => {
  const m = document.getElementById('export-menu');
  if (m) m.style.display = 'none';
});

function exportCSV() {
  const m = document.getElementById('export-menu'); if (m) m.style.display = 'none';
  if (!lastData.length) return;
  const rows = [['#','Product','Qty Sold','Orders','Revenue (BDT)']].concat(
    lastData.map(p => [p.rank, p.name, p.qty, p.orders, Math.round(p.revenue)])
  );
  const csv = rows.map(r => r.map(v => '"' + String(v).replace(/"/g,'""') + '"').join(',')).join('\\n');
  const a = Object.assign(document.createElement('a'), {
    href: URL.createObjectURL(new Blob([csv], {type:'text/csv;charset=utf-8;'})),
    download: 'products-' + currentGroup + '.csv'
  });
  a.click();
}

function exportPDF() {
  const m = document.getElementById('export-menu'); if (m) m.style.display = 'none';
  if (!lastData.length) return;
  const groupLabel = currentGroup === 'on_hold' ? 'On Hold' : currentGroup === 'total' ? 'All Statuses' : 'Delivered';
  const w = window.open('', '_blank');
  w.document.write('<!DOCTYPE html><html><head><title>Top Products</title><style>'
    + 'body{font-family:Arial,sans-serif;font-size:12px;padding:20px;color:#111}'
    + 'h2{font-size:16px;margin-bottom:4px}p{color:#666;font-size:11px;margin-bottom:12px}'
    + 'table{width:100%;border-collapse:collapse}'
    + 'th{background:#f0f0f0;border:1px solid #ccc;padding:6px 10px;text-align:left;font-size:11px}'
    + 'td{border:1px solid #eee;padding:5px 10px;font-size:11px}.r{text-align:right}'
    + 'tr:nth-child(even){background:#f9f9f9}'
    + '</style></head><body>'
    + '<h2>Top Products — ' + groupLabel + '</h2>'
    + '<p>Generated ' + new Date().toLocaleString() + ' · ' + lastData.length + ' products</p>'
    + '<table><thead><tr><th>#</th><th>Product</th><th class="r">Qty Sold</th><th class="r">Orders</th><th class="r">Revenue</th></tr></thead><tbody>'
    + lastData.map(p => '<tr><td>' + p.rank + '</td><td>' + esc(p.name) + '</td><td class="r">' + p.qty.toLocaleString() + '</td><td class="r">' + p.orders.toLocaleString() + '</td><td class="r">' + Math.round(p.revenue).toLocaleString() + '</td></tr>').join('')
    + '</tbody></table></body></html>');
  w.document.close();
  setTimeout(() => w.print(), 500);
}

async function load() {
  const tbody = document.getElementById('tbody');
  const infoEl = document.getElementById('result-info');
  const limitEl = document.getElementById('limit-sel');
  if (!tbody || !limitEl) return;

  const limit = limitEl.value;
  const params = new URLSearchParams({limit, group: currentGroup});
  if (currentDays) params.set('days', currentDays);
  const searchEl = document.getElementById('search-input');
  const search = searchEl ? searchEl.value.trim() : '';
  const category = getChipKws('pcat-chips');
  const channel  = getChipKws('pch-chips');
  if (search)   params.set('search', search);
  if (category) params.set('category', category);
  if (channel)  params.set('channel', channel);

  tbody.innerHTML = '<tr><td colspan="5" class="state">Loading...</td></tr>';
  if (infoEl) infoEl.innerHTML = '&nbsp;';

  try {
    const r = await fetch('/api/products?' + params);
    if (!r.ok) throw new Error(r.statusText);
    const data = await r.json();
    lastData = data.products || [];

    if (!data.products || !data.products.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="state">No data found.</td></tr>';
      return;
    }

    const periodLabel = currentDays === 90 ? 'last 3 months'
                      : currentDays === 30 ? 'last month'
                      : currentDays === 7  ? 'last 7 days'
                      : currentDays        ? 'last ' + currentDays + ' days'
                      : 'all time';
    const groupLabel = currentGroup === 'on_hold' ? 'On Hold'
                     : currentGroup === 'return'  ? 'Flagged — Return'
                     : currentGroup === 'exchange'? 'Flagged — Exchange'
                     : currentGroup === 'flagged' ? 'Flagged'
                     : currentGroup === 'total'   ? 'All Statuses'
                     : 'Delivered';
    if (infoEl) infoEl.textContent = 'Showing top ' + data.products.length + ' products — ' + groupLabel + ' · ' + periodLabel + (search ? ' · "' + search + '"' : '');

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
<style>""" + COMMON_CSS + """
.page-title{font-size:1.05rem;font-weight:700;color:var(--text-primary);margin-bottom:18px}
.group-tabs{display:flex;gap:0;margin-bottom:18px;border-bottom:1px solid var(--border)}
.group-tab{background:none;border:none;border-bottom:3px solid transparent;
           color:var(--text-secondary);cursor:pointer;font-size:.9rem;font-weight:500;
           padding:10px 22px;transition:.2s;margin-bottom:-1px}
.group-tab:hover{color:var(--text-primary)}
.group-tab.active{border-bottom-color:var(--purple);color:var(--purple);font-weight:700}
.table-wrap{background:var(--bg-card);border:0.5px solid var(--border);border-radius:10px;overflow-x:auto}
table{width:100%;border-collapse:collapse}
thead th{background:var(--bg-inner);color:var(--text-tertiary);font-size:.7rem;font-weight:600;
         text-transform:uppercase;letter-spacing:.05em;padding:10px 16px;text-align:left;
         border-bottom:0.5px solid var(--border);white-space:nowrap}
thead th.r{text-align:right}
tbody tr{border-bottom:0.5px solid var(--border);transition:background .12s}
tbody tr:last-child{border-bottom:none}
tbody tr:hover{background:var(--bg-inner)}
td{padding:10px 16px;font-size:.83rem;vertical-align:middle;color:var(--text-primary)}
td.rank{color:var(--text-tertiary);font-weight:600;width:44px}
.cust-cell{display:flex;flex-direction:column;gap:3px;max-width:420px}
.cust-name{color:var(--text-primary);font-weight:500;font-size:.87rem}
.cust-meta{color:var(--text-secondary);font-size:.74rem;display:flex;gap:10px;flex-wrap:wrap}
.cust-phone{color:var(--purple)}
.cust-loc{color:var(--text-tertiary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:260px}
td.r{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
td.qty{color:#1D9E75;font-weight:600}
td.ord{color:var(--purple)}
td.rev{color:var(--text-primary);font-weight:500}
.state{text-align:center;padding:48px;color:var(--text-tertiary);font-size:.85rem}
.state.err{color:var(--red)}
.toolbar{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.search-wrap{position:relative;flex:1;max-width:420px}
.search-input{width:100%;background:var(--bg-card);border:0.5px solid var(--border);border-radius:8px;
              color:var(--text-primary);font-size:.85rem;padding:8px 30px 8px 34px;outline:none;transition:border-color .2s}
.search-input:focus{border-color:var(--purple)}
.search-input::placeholder{color:var(--text-tertiary)}
.search-icon{position:absolute;left:11px;top:50%;transform:translateY(-50%);
             color:var(--text-tertiary);font-size:.8rem;pointer-events:none}
.search-clear{position:absolute;right:8px;top:50%;transform:translateY(-50%);
              background:none;border:none;color:var(--text-tertiary);cursor:pointer;font-size:.9rem;
              padding:2px 4px;line-height:1;display:none}
.search-clear:hover{color:var(--text-primary)}
.export-wrap{position:relative;margin-left:auto}
.export-btn{background:var(--bg-card);border:0.5px solid var(--border);border-radius:8px;
            color:var(--text-secondary);cursor:pointer;font-size:.8rem;padding:8px 14px;
            display:flex;align-items:center;gap:5px;transition:.2s;white-space:nowrap}
.export-btn:hover{border-color:#1D9E75;color:#1D9E75}
.export-menu{position:absolute;right:0;top:calc(100% + 4px);background:var(--bg-card);
             border:0.5px solid var(--border);border-radius:8px;padding:6px;z-index:100;
             flex-direction:column;gap:2px;min-width:148px;box-shadow:0 4px 16px rgba(0,0,0,.1)}
.export-opt{background:none;border:none;color:var(--text-primary);cursor:pointer;font-size:.82rem;
            padding:8px 12px;text-align:left;border-radius:6px;transition:.15s;width:100%}
.export-opt:hover{background:var(--bg-inner);color:#1D9E75}
.filter-toggle{background:var(--bg-card);border:0.5px solid var(--border);border-radius:8px;
               color:var(--text-secondary);cursor:pointer;font-size:.8rem;padding:8px 14px;
               display:flex;align-items:center;gap:6px;transition:.2s;white-space:nowrap}
.filter-toggle:hover{border-color:var(--purple);color:var(--purple)}
.filter-toggle.on{border-color:var(--purple);color:var(--purple);background:#EEEDFE}
.fbadge{background:var(--purple);color:#fff;border-radius:10px;font-size:.68rem;font-weight:700;
        padding:1px 5px;display:none;line-height:1.4}
.filter-panel{background:var(--bg-card);border:0.5px solid var(--border);border-radius:10px;
              padding:16px 18px;margin-bottom:12px;display:none;flex-direction:column;gap:14px}
.fp-section{}
.fp-sec-label{color:var(--text-tertiary);font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px}
.fp-chips{display:flex;flex-wrap:wrap;gap:6px}
.fp-chip{background:var(--bg-inner);border:0.5px solid var(--border);border-radius:20px;
         color:var(--text-secondary);cursor:pointer;font-size:.78rem;padding:4px 12px;transition:.2s}
.fp-chip:hover{border-color:var(--purple);color:var(--purple)}
.fp-chip.on{background:#EEEDFE;border-color:var(--purple);color:var(--purple);font-weight:600}
.fp-row{display:flex;align-items:center;gap:20px;flex-wrap:wrap;padding-top:4px;border-top:0.5px solid var(--border)}
.fcheck{display:flex;align-items:center;gap:7px;cursor:pointer;font-size:.82rem;color:var(--text-primary)}
.fcheck input{width:14px;height:14px;cursor:pointer;accent-color:var(--purple)}
.fp-minorders{display:flex;align-items:center;gap:8px;font-size:.82rem;color:var(--text-primary)}
.limit-sel{background:var(--bg-inner);border:0.5px solid var(--border);border-radius:6px;
           color:var(--text-primary);font-size:.8rem;padding:5px 10px;cursor:pointer;outline:none}
.cust-email-row{font-size:.72rem;color:var(--text-tertiary);margin-top:1px}
.cust-email{color:var(--purple);opacity:.85}
</style>
</head>
<body>
<header>
  <h1>&#9876;&#65039; Winterfell Operations</h1>
  <nav class="top-nav">
    <a href="/" class="nav-link">Operations</a>
    <a href="/products" class="nav-link">Products</a>
    <a href="/customers" class="nav-link active">Customers</a>
    <a href="/orders" class="nav-link">Orders</a>
    <a href="/inventory" class="nav-link">Inventory</a>
    <a href="/supply-chain" class="nav-link">Supply Chain</a>
    <a href="/agents" class="nav-link">Agents</a>
  </nav>
  <div class="hdr-actions">
    <a href="/logout" class="logout">Logout</a>
    <button class="ham" onclick="document.getElementById('mnav3').classList.toggle('open')" aria-label="Menu">
      <span></span><span></span><span></span>
    </button>
  </div>
  <nav class="mob-nav" id="mnav3">
    <a href="/">Operations</a>
    <a href="/products">Products</a>
    <a href="/customers" class="active">Customers</a>
    <a href="/orders">Orders</a>
    <a href="/inventory">Inventory</a>
    <a href="/supply-chain">Supply Chain</a>
    <a href="/agents">Agents</a>
    <a href="/logout">Logout</a>
  </nav>
</header>
<div class="container">
  <div class="page-title">Top Customers</div>
  <div class="group-tabs">
    <button class="group-tab active" onclick="setGroup('delivered',this)">Delivered</button>
    <button class="group-tab" onclick="setGroup('on_hold',this)">On Hold (Pre-orders)</button>
    <button class="group-tab" onclick="setGroup('total',this)">Total (All Statuses)</button>
  </div>
  <div class="toolbar">
    <div class="search-wrap">
      <span class="search-icon">&#128269;</span>
      <input type="text" id="search-input" class="search-input"
             placeholder="Search by name, phone, email..." oninput="onSearchInput()" onkeydown="if(event.key==='Escape')clearSearch()">
      <button class="search-clear" id="search-clear" onclick="clearSearch()">&#215;</button>
    </div>
    <button class="filter-toggle" id="filter-btn" onclick="toggleFilter()">
      &#9881; Filters <span class="fbadge" id="fbadge"></span>
    </button>
    <div class="export-wrap">
      <button class="export-btn" onclick="toggleExport(event)">&#8659; Export</button>
      <div class="export-menu" id="export-menu" style="display:none">
        <button class="export-opt" onclick="exportCSV()">&#128196; Export CSV</button>
        <button class="export-opt" onclick="exportPDF()">&#128424; PDF / Print</button>
      </div>
    </div>
  </div>
  <div class="filter-panel" id="filter-panel">
    <div class="fp-section">
      <div class="fp-sec-label">Product Category (bought at least one)</div>
      <div class="fp-chips" id="cat-chips">
        <button class="fp-chip" data-kw="t-shirt,tee" onclick="toggleChip(this)">T-Shirt / Tee</button>
        <button class="fp-chip" data-kw="polo" onclick="toggleChip(this)">Polo</button>
        <button class="fp-chip" data-kw="drop shoulder" onclick="toggleChip(this)">Drop Shoulder</button>
        <button class="fp-chip" data-kw="pant,jean,trouser,cargo" onclick="toggleChip(this)">Pants / Jeans</button>
        <button class="fp-chip" data-kw="jacket" onclick="toggleChip(this)">Jacket</button>
        <button class="fp-chip" data-kw="shirt" onclick="toggleChip(this)">Shirt</button>
        <button class="fp-chip" data-kw="hoodie,sweatshirt" onclick="toggleChip(this)">Hoodie</button>
        <button class="fp-chip" data-kw="waffle" onclick="toggleChip(this)">Waffle Knit</button>
        <button class="fp-chip" data-kw="corduroy" onclick="toggleChip(this)">Corduroy</button>
      </div>
    </div>
    <div class="fp-section">
      <div class="fp-sec-label">Order Channel</div>
      <div class="fp-chips" id="ch-chips">
        <button class="fp-chip" data-kw="whatsapp" onclick="toggleChip(this)">WhatsApp</button>
        <button class="fp-chip" data-kw="facebook" onclick="toggleChip(this)">Facebook</button>
        <button class="fp-chip" data-kw="instagram" onclick="toggleChip(this)">Instagram</button>
        <button class="fp-chip" data-kw="website,woocommerce" onclick="toggleChip(this)">Website</button>
        <button class="fp-chip" data-kw="tiktok" onclick="toggleChip(this)">TikTok</button>
        <button class="fp-chip" data-kw="phone,call" onclick="toggleChip(this)">Phone / Call</button>
        <button class="fp-chip" data-kw="messenger" onclick="toggleChip(this)">Messenger</button>
      </div>
    </div>
    <div class="fp-row">
      <label class="fcheck"><input type="checkbox" id="has-email" onchange="onFilterChange()"> Has Email</label>
      <div class="fp-minorders">
        <span>Min Orders</span>
        <select class="limit-sel" id="min-orders" onchange="onFilterChange()">
          <option value="1">Any</option>
          <option value="2">2+</option>
          <option value="3">3+</option>
          <option value="5">5+</option>
          <option value="10">10+</option>
          <option value="20">20+</option>
        </select>
      </div>
    </div>
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
      <option value="500">Top 500</option>
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
let lastData = [];
let searchTimer;

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

function onSearchInput() {
  const val = document.getElementById('search-input').value;
  const clr = document.getElementById('search-clear');
  if (clr) clr.style.display = val ? 'block' : 'none';
  clearTimeout(searchTimer);
  searchTimer = setTimeout(load, 400);
}

function clearSearch() {
  const el = document.getElementById('search-input');
  const clr = document.getElementById('search-clear');
  if (el) { el.value = ''; el.focus(); }
  if (clr) clr.style.display = 'none';
  load();
}

function toggleFilter() {
  const panel = document.getElementById('filter-panel');
  const btn = document.getElementById('filter-btn');
  const showing = panel.style.display === 'flex';
  panel.style.display = showing ? 'none' : 'flex';
  btn.classList.toggle('on', !showing);
}

function toggleChip(el) {
  el.classList.toggle('on');
  updateFilterBadge();
  load();
}

function onFilterChange() {
  updateFilterBadge();
  load();
}

function updateFilterBadge() {
  const activeCats = document.querySelectorAll('#cat-chips .fp-chip.on').length;
  const activeChs  = document.querySelectorAll('#ch-chips .fp-chip.on').length;
  const hasEmail   = document.getElementById('has-email').checked;
  const minOrders  = parseInt(document.getElementById('min-orders').value || '1') > 1;
  const count = (activeCats > 0 ? 1 : 0) + (activeChs > 0 ? 1 : 0) + (hasEmail ? 1 : 0) + (minOrders ? 1 : 0);
  const badge = document.getElementById('fbadge');
  const btn   = document.getElementById('filter-btn');
  if (badge) { badge.textContent = count || ''; badge.style.display = count ? 'inline' : 'none'; }
  if (btn)   btn.classList.toggle('on', count > 0 || document.getElementById('filter-panel').style.display === 'flex');
}

function getActiveChipKws(containerId) {
  return Array.from(document.querySelectorAll('#' + containerId + ' .fp-chip.on'))
    .map(el => el.dataset.kw).join(',');
}

function toggleExport(e) {
  e.stopPropagation();
  const menu = document.getElementById('export-menu');
  menu.style.display = menu.style.display === 'none' ? 'flex' : 'none';
}
document.addEventListener('click', () => {
  const m = document.getElementById('export-menu');
  if (m) m.style.display = 'none';
});

function exportCSV() {
  const m = document.getElementById('export-menu'); if (m) m.style.display = 'none';
  if (!lastData.length) return;
  const rows = [['#','Name','Phone','Location','Email','Qty Bought','Orders','Revenue (BDT)']].concat(
    lastData.map(c => [c.rank, c.name, c.phone, c.location, c.email, c.qty, c.orders, Math.round(c.revenue)])
  );
  const csv = rows.map(r => r.map(v => '"' + String(v).replace(/"/g,'""') + '"').join(',')).join('\\n');
  const a = Object.assign(document.createElement('a'), {
    href: URL.createObjectURL(new Blob([csv], {type:'text/csv;charset=utf-8;'})),
    download: 'customers-' + currentGroup + '.csv'
  });
  a.click();
}

function exportPDF() {
  const m = document.getElementById('export-menu'); if (m) m.style.display = 'none';
  if (!lastData.length) return;
  const groupLabel = currentGroup === 'on_hold' ? 'On Hold' : currentGroup === 'total' ? 'All Statuses' : 'Delivered';
  const w = window.open('', '_blank');
  w.document.write('<!DOCTYPE html><html><head><title>Top Customers</title><style>'
    + 'body{font-family:Arial,sans-serif;font-size:12px;padding:20px;color:#111}'
    + 'h2{font-size:16px;margin-bottom:4px}p{color:#666;font-size:11px;margin-bottom:12px}'
    + 'table{width:100%;border-collapse:collapse}'
    + 'th{background:#f0f0f0;border:1px solid #ccc;padding:6px 10px;text-align:left;font-size:11px}'
    + 'td{border:1px solid #eee;padding:5px 10px;font-size:11px}.r{text-align:right}'
    + 'tr:nth-child(even){background:#f9f9f9}'
    + '</style></head><body>'
    + '<h2>Top Customers — ' + groupLabel + '</h2>'
    + '<p>Generated ' + new Date().toLocaleString() + ' · ' + lastData.length + ' customers</p>'
    + '<table><thead><tr><th>#</th><th>Name</th><th>Phone</th><th>Email</th><th>Location</th><th class="r">Qty</th><th class="r">Orders</th><th class="r">Revenue</th></tr></thead><tbody>'
    + lastData.map(c => '<tr><td>' + c.rank + '</td><td>' + esc(c.name) + '</td><td>' + esc(c.phone) + '</td><td>' + esc(c.email) + '</td><td>' + esc(c.location) + '</td><td class="r">' + c.qty.toLocaleString() + '</td><td class="r">' + c.orders.toLocaleString() + '</td><td class="r">' + Math.round(c.revenue).toLocaleString() + '</td></tr>').join('')
    + '</tbody></table></body></html>');
  w.document.close();
  setTimeout(() => w.print(), 500);
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
  const searchEl = document.getElementById('search-input');
  const search = searchEl ? searchEl.value.trim() : '';
  const hasEmail = document.getElementById('has-email')?.checked;
  const minOrders = document.getElementById('min-orders')?.value || '1';
  const category = getActiveChipKws('cat-chips');
  const channel  = getActiveChipKws('ch-chips');
  if (search)              params.set('search', search);
  if (hasEmail)            params.set('has_email', '1');
  if (category)            params.set('category', category);
  if (channel)             params.set('channel', channel);
  if (parseInt(minOrders) > 1) params.set('min_orders', minOrders);

  tbody.innerHTML = '<tr><td colspan="5" class="state">Loading...</td></tr>';
  if (infoEl) infoEl.innerHTML = '&nbsp;';

  try {
    const r = await fetch('/api/customers?' + params);
    if (!r.ok) throw new Error(r.statusText);
    const data = await r.json();
    lastData = data.customers || [];

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
    if (infoEl) infoEl.textContent = 'Showing top ' + data.customers.length + ' customers — ' + groupLabel + ' · ' + periodLabel + ' · ' + sortLabel + (search ? ' · "' + search + '"' : '');

    tbody.innerHTML = data.customers.map(c => {
      const loc = c.location ? (c.location.length > 50 ? c.location.slice(0,50) + '...' : c.location) : '';
      const email = c.email ? '<div class="cust-email-row"><span class="cust-email">' + esc(c.email) + '</span></div>' : '';
      return '<tr>' +
        '<td class="rank r">' + c.rank + '</td>' +
        '<td><div class="cust-cell">' +
          '<div class="cust-name">' + esc(c.name) + '</div>' +
          '<div class="cust-meta">' +
            '<span class="cust-phone">' + esc(c.phone) + '</span>' +
            (loc ? '<span class="cust-loc" title="' + esc(c.location) + '">' + esc(loc) + '</span>' : '') +
          '</div>' +
          email +
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


ORDERS_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Orders \xb7 Winterfell</title>
<style>""" + COMMON_CSS + """
main{padding:24px 20px;max-width:1400px;margin:0 auto}
h1{font-size:1.2rem;font-weight:700;margin-bottom:20px;color:var(--text-primary)}
.tab-row{display:flex;gap:0;border-bottom:1px solid var(--border);margin-bottom:20px;overflow-x:auto;scrollbar-width:none}
.tab-row::-webkit-scrollbar{display:none}
.tab-btn{background:none;border:none;color:var(--text-secondary);padding:10px 16px;cursor:pointer;font-size:.82rem;white-space:nowrap;border-bottom:2px solid transparent;display:flex;align-items:center;gap:6px}
.tab-btn:hover{color:var(--text-primary)}
.tab-btn.active{color:var(--text-primary);border-bottom-color:var(--text-primary)}
.tab-count{background:var(--bg-inner);color:var(--text-tertiary);font-size:.7rem;padding:1px 6px;border-radius:10px;min-width:18px;text-align:center}
.tab-btn.active .tab-count{background:var(--border);color:var(--text-primary)}
.toolbar{display:flex;gap:10px;align-items:center;margin-bottom:16px;flex-wrap:wrap}
.search-wrap{position:relative;flex:1;min-width:200px;max-width:420px}
.search-wrap input{width:100%;background:var(--bg-card);border:0.5px solid var(--border);border-radius:6px;padding:7px 12px 7px 32px;color:var(--text-primary);font-size:.83rem;outline:none}
.search-wrap input::placeholder{color:var(--text-tertiary)}
.search-wrap input:focus{border-color:var(--purple)}
.search-icon{position:absolute;left:10px;top:50%;transform:translateY(-50%);color:var(--text-tertiary);font-size:.78rem}
.period-btns{display:flex;gap:4px}
.period-btn{background:var(--bg-inner);border:0.5px solid var(--border);color:var(--text-secondary);padding:6px 12px;border-radius:6px;cursor:pointer;font-size:.78rem}
.period-btn.active{background:#EEEDFE;border-color:var(--purple);color:var(--purple)}
.period-btn:hover{border-color:var(--purple);color:var(--purple)}
.tbl-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:.82rem}
thead th{background:var(--bg-inner);color:var(--text-tertiary);font-weight:500;padding:8px 12px;text-align:left;border-bottom:0.5px solid var(--border);white-space:nowrap}
thead th.r{text-align:right}
tbody tr{border-bottom:0.5px solid var(--border)}
tbody tr:hover{background:var(--bg-inner)}
tbody td{padding:10px 12px;vertical-align:top;color:var(--text-primary)}
tbody td.r{text-align:right;white-space:nowrap}
.state{color:var(--text-tertiary);text-align:center;padding:40px;font-size:.85rem}
.state.err{color:var(--red)}
.so-num{color:var(--purple);font-weight:600;font-size:.83rem}
.so-badges{display:flex;gap:4px;margin-top:4px;flex-wrap:wrap}
.src-badge{font-size:.65rem;padding:1px 6px;border-radius:4px;font-weight:500}
.src-web{background:#EEEDFE;color:#3C3489;border:0.5px solid #B0ABF0}
.src-fb{background:#E6F1FB;color:#0C447C;border:0.5px solid #A8C8ED}
.src-wa{background:#E1F5EE;color:#085041;border:0.5px solid #5DCAA5}
.src-ig{background:#FDE9F5;color:#6E1048;border:0.5px solid #EDA8D0}
.src-other{background:var(--bg-inner);color:var(--text-tertiary);border:0.5px solid var(--border)}
.st-badge{display:inline-block;font-size:.65rem;padding:2px 8px;border-radius:10px;font-weight:600;white-space:nowrap}
.st-pending{background:#FFF8E0;color:#7A5C00;border:0.5px solid #E6C84A}
.st-on-hold{background:#FFF0DC;color:#7A3A00;border:0.5px solid #E8A65A}
.st-approved{background:#E1F5EE;color:#085041;border:0.5px solid #5DCAA5}
.st-processing{background:#E6F1FB;color:#0C447C;border:0.5px solid #A8C8ED}
.st-ready{background:#EEEDFE;color:#3C3489;border:0.5px solid #B0ABF0}
.st-transit{background:#E6F9EC;color:#1A5C2A;border:0.5px solid #82D49A}
.st-delivered{background:#E1F5EE;color:#085041;border:0.5px solid #5DCAA5}
.st-flagged{background:#FCEBEB;color:#791F1F;border:0.5px solid #F5C6C6}
.st-cancelled{background:var(--bg-inner);color:var(--text-tertiary);border:0.5px solid var(--border)}
.st-other{background:var(--bg-inner);color:var(--text-tertiary);border:0.5px solid var(--border)}
.cust-name{color:var(--purple);font-weight:500}
.cust-phone{color:var(--text-secondary);font-size:.77rem;margin-top:2px}
.cust-region{color:var(--text-tertiary);font-size:.73rem;margin-top:1px}
.date-main{color:var(--text-primary);font-size:.8rem}
.date-sub{color:var(--text-tertiary);font-size:.73rem;margin-top:2px}
.amt-bold{font-weight:600;color:var(--text-primary)}
.pagination{display:flex;align-items:center;gap:6px;justify-content:center;margin-top:20px;flex-wrap:wrap}
.pg-btn{background:var(--bg-inner);border:0.5px solid var(--border);color:var(--text-secondary);padding:5px 10px;border-radius:6px;cursor:pointer;font-size:.78rem;min-width:32px;text-align:center}
.pg-btn:hover{border-color:var(--purple);color:var(--purple)}
.pg-btn.active{background:#EEEDFE;border-color:var(--purple);color:var(--purple)}
.pg-btn:disabled{opacity:.4;cursor:default;pointer-events:none}
.pg-info{color:var(--text-tertiary);font-size:.78rem;padding:0 4px}
</style>
</head>
<body>
<header>
  <h1>&#9876;&#65039; Winterfell Operations</h1>
  <nav class="top-nav">
    <a href="/" class="nav-link">Operations</a>
    <a href="/products" class="nav-link">Products</a>
    <a href="/customers" class="nav-link">Customers</a>
    <a href="/orders" class="nav-link active">Orders</a>
    <a href="/inventory" class="nav-link">Inventory</a>
    <a href="/supply-chain" class="nav-link">Supply Chain</a>
    <a href="/agents" class="nav-link">Agents</a>
  </nav>
  <div class="hdr-actions">
    <a href="/logout" class="logout">Logout</a>
    <button class="ham" onclick="document.getElementById(\'mnav4\').classList.toggle(\'open\')" aria-label="Menu">
      <span></span><span></span><span></span>
    </button>
  </div>
  <nav class="mob-nav" id="mnav4">
    <a href="/">Operations</a>
    <a href="/products">Products</a>
    <a href="/customers">Customers</a>
    <a href="/orders" class="active">Orders</a>
    <a href="/inventory">Inventory</a>
    <a href="/supply-chain">Supply Chain</a>
    <a href="/agents">Agents</a>
    <a href="/logout">Logout</a>
  </nav>
</header>
<main>
<h1>Orders</h1>
<div class="tab-row">
  <button class="tab-btn active" data-tab="all"           onclick="setTab('all')">All Orders <span class="tab-count" id="cnt-all">—</span></button>
  <button class="tab-btn"        data-tab="pending"       onclick="setTab('pending')">Pending <span class="tab-count" id="cnt-pending">—</span></button>
  <button class="tab-btn"        data-tab="on_hold"       onclick="setTab('on_hold')">On Hold <span class="tab-count" id="cnt-on_hold">—</span></button>
  <button class="tab-btn"        data-tab="approved"      onclick="setTab('approved')">Approved <span class="tab-count" id="cnt-approved">—</span></button>
  <button class="tab-btn"        data-tab="processing"    onclick="setTab('processing')">Processing <span class="tab-count" id="cnt-processing">—</span></button>
  <button class="tab-btn"        data-tab="ready_to_ship" onclick="setTab('ready_to_ship')">Ready To Ship <span class="tab-count" id="cnt-ready_to_ship">—</span></button>
  <button class="tab-btn"        data-tab="in_transit"    onclick="setTab('in_transit')">In-Transit <span class="tab-count" id="cnt-in_transit">—</span></button>
  <button class="tab-btn"        data-tab="delivered"     onclick="setTab('delivered')">Delivered <span class="tab-count" id="cnt-delivered">—</span></button>
  <button class="tab-btn"        data-tab="flagged"       onclick="setTab('flagged')">Flagged <span class="tab-count" id="cnt-flagged">—</span></button>
  <button class="tab-btn"        data-tab="cancelled"     onclick="setTab('cancelled')">Cancelled <span class="tab-count" id="cnt-cancelled">—</span></button>
</div>
<div class="toolbar">
  <div class="search-wrap">
    <span class="search-icon">&#128269;</span>
    <input type="text" id="search" placeholder="Search SO#, customer name, phone, waybill..." oninput="onSearch()">
  </div>
  <div class="period-btns">
    <button class="period-btn active" data-days="" onclick="setPeriod(this,'')">All Time</button>
    <button class="period-btn" data-days="90" onclick="setPeriod(this,'90')">3 Months</button>
    <button class="period-btn" data-days="30" onclick="setPeriod(this,'30')">1 Month</button>
    <button class="period-btn" data-days="7"  onclick="setPeriod(this,'7')">7 Days</button>
  </div>
</div>
<p id="info" style="color:#6e7681;font-size:.78rem;margin-bottom:12px"></p>
<div class="tbl-wrap">
<table>
  <thead>
    <tr>
      <th>Invoice</th>
      <th>Date</th>
      <th>Customer</th>
      <th>Status</th>
      <th class="r">Items</th>
      <th class="r">Receivable</th>
      <th class="r">Paid</th>
      <th class="r">Due</th>
      <th class="r">Del. Fee</th>
    </tr>
  </thead>
  <tbody id="tbody"><tr><td colspan="9" class="state">Loading...</td></tr></tbody>
</table>
</div>
<div class="pagination" id="pagination"></div>
</main>
<script>
var currentTab='all', currentDays='', currentPage=1, searchTimer=null;

function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}
function fmt(n){return Math.round(Number(n||0)).toLocaleString();}

function statusClass(s){
  if(!s)return 'st-other';
  var u=s.toUpperCase();
  if(u==='PENDING'||u==='REQUESTED')return 'st-pending';
  if(u.indexOf('ON')>=0&&u.indexOf('HOLD')>=0)return 'st-on-hold';
  if(u.indexOf('APPROV')>=0)return 'st-approved';
  if(u.indexOf('PROCESS')>=0)return 'st-processing';
  if(u.indexOf('READY')>=0)return 'st-ready';
  if(u.indexOf('TRANSIT')>=0)return 'st-transit';
  if(u==='DELIVERED'||u==='COMPLETED')return 'st-delivered';
  if(u.indexOf('FLAG')>=0)return 'st-flagged';
  if(u.indexOf('CANCEL')>=0)return 'st-cancelled';
  return 'st-other';
}

function sourceClass(s){
  if(!s)return 'src-other';
  var l=s.toLowerCase();
  if(l.indexOf('woo')>=0||l.indexOf('web')>=0||l.indexOf('site')>=0)return 'src-web';
  if(l.indexOf('face')>=0||l==='fb')return 'src-fb';
  if(l.indexOf('whats')>=0||l==='wa')return 'src-wa';
  if(l.indexOf('insta')>=0||l==='ig')return 'src-ig';
  return 'src-other';
}

function sourceLabel(s){
  if(!s)return '';
  var l=s.toLowerCase();
  if(l.indexOf('woo')>=0)return 'WOO';
  if(l.indexOf('web')>=0||l.indexOf('site')>=0)return 'Web';
  if(l.indexOf('face')>=0)return 'FB';
  if(l.indexOf('whats')>=0)return 'WA';
  if(l.indexOf('insta')>=0)return 'IG';
  return s.slice(0,8);
}

async function loadCounts(){
  try{
    var r=await fetch('/api/orders/counts');
    var d=await r.json();
    ['all','pending','on_hold','approved','processing','ready_to_ship','in_transit','delivered','flagged','cancelled'].forEach(function(k){
      var el=document.getElementById('cnt-'+k);
      if(el)el.textContent=(d[k]||0).toLocaleString();
    });
  }catch(e){}
}

function setTab(tab){
  currentTab=tab;currentPage=1;
  document.querySelectorAll('.tab-btn').forEach(function(b){b.classList.toggle('active',b.dataset.tab===tab);});
  load();
}

function setPeriod(btn,days){
  currentDays=days;currentPage=1;
  document.querySelectorAll('.period-btn').forEach(function(b){b.classList.remove('active');});
  btn.classList.add('active');
  load();
}

function onSearch(){clearTimeout(searchTimer);searchTimer=setTimeout(function(){currentPage=1;load();},350);}

function goPage(p){currentPage=p;load();window.scrollTo(0,0);}

function renderPagination(page,pages){
  var el=document.getElementById('pagination');
  if(pages<=1){el.innerHTML='';return;}
  var html='';
  html+='<button class="pg-btn" onclick="goPage('+(page-1)+')" '+(page<=1?'disabled':'')+'>&#8249; Prev</button>';
  var start=Math.max(1,page-2),end=Math.min(pages,page+2);
  if(start>1){html+='<button class="pg-btn" onclick="goPage(1)">1</button>';if(start>2)html+='<span class="pg-info">&#8230;</span>';}
  for(var i=start;i<=end;i++){html+='<button class="pg-btn'+(i===page?' active':'')+'" onclick="goPage('+i+')">'+i+'</button>';}
  if(end<pages){if(end<pages-1)html+='<span class="pg-info">&#8230;</span>';html+='<button class="pg-btn" onclick="goPage('+pages+')">'+pages+'</button>';}
  html+='<button class="pg-btn" onclick="goPage('+(page+1)+')" '+(page>=pages?'disabled':'')+'>Next &#8250;</button>';
  html+='<span class="pg-info">Page '+page+' of '+pages+'</span>';
  el.innerHTML=html;
}

async function load(){
  var tbody=document.getElementById('tbody');
  tbody.innerHTML='<tr><td colspan="9" class="state">Loading&#8230;</td></tr>';
  var search=document.getElementById('search').value.trim();
  var params=new URLSearchParams({tab:currentTab,page:currentPage});
  if(currentDays)params.set('days',currentDays);
  if(search)params.set('search',search);
  try{
    var r=await fetch('/api/orders?'+params);
    var d=await r.json();
    renderPagination(currentPage,d.pages||1);
    var tabLabels={all:'All Orders',pending:'Pending',on_hold:'On Hold',approved:'Approved',processing:'Processing',ready_to_ship:'Ready To Ship',in_transit:'In-Transit',delivered:'Delivered',flagged:'Flagged',cancelled:'Cancelled'};
    var periodLabel=currentDays==='90'?'last 3 months':currentDays==='30'?'last month':currentDays==='7'?'last 7 days':'all time';
    document.getElementById('info').textContent='Showing '+(d.orders||[]).length+' of '+(d.total||0).toLocaleString()+' orders — '+(tabLabels[currentTab]||currentTab)+' · '+periodLabel+(search?' · "'+search+'"':'');
    if(!d.orders||!d.orders.length){tbody.innerHTML='<tr><td colspan="9" class="state">No orders found.</td></tr>';return;}
    tbody.innerHTML=d.orders.map(function(o){
      var src=o.source?'<span class="src-badge '+sourceClass(o.source)+'">'+esc(sourceLabel(o.source))+'</span>':'';
      var shipped=o.shipped_date?'<div class="date-sub">Shipped: '+esc(o.shipped_date)+'</div>':'';
      var waybill=o.waybill?'<div class="date-sub">'+esc(o.waybill)+'</div>':'';
      var paidCell=o.collected>0?'<span style="color:#3fb950">৳'+fmt(o.collected)+'</span>':'<span style="color:#6e7681">—</span>';
      var dueCell=o.due>0?'<span style="color:#f85149">৳'+fmt(o.due)+'</span>':'<span style="color:#3fb950">✓</span>';
      return '<tr>'+
        '<td><div class="so-num">'+esc(o.so_number)+'</div><div class="so-badges">'+src+'</div></td>'+
        '<td><div class="date-main">'+esc(o.order_date)+'</div>'+shipped+'</td>'+
        '<td><div class="cust-name">'+esc(o.customer)+'</div><div class="cust-phone">'+esc(o.phone)+'</div>'+(o.region?'<div class="cust-region">'+esc(o.region)+'</div>':'')+'</td>'+
        '<td><span class="st-badge '+statusClass(o.status)+'">'+esc(o.status)+'</span></td>'+
        '<td class="r">'+fmt(o.items)+'</td>'+
        '<td class="r"><span class="amt-bold">৳'+fmt(o.receivable)+'</span></td>'+
        '<td class="r">'+paidCell+'</td>'+
        '<td class="r">'+dueCell+'</td>'+
        '<td class="r">৳'+fmt(o.delivery_fee)+waybill+'</td>'+
        '</tr>';
    }).join('');
  }catch(e){
    tbody.innerHTML='<tr><td colspan="9" class="state err">Failed to load: '+e.message+'</td></tr>';
  }
}

loadCounts();
load();
</script>
</body>
</html>"""


@app.route('/orders')
@login_required
def orders_page():
    return render_template_string(ORDERS_HTML)


@app.route('/api/orders/counts')
@login_required
def api_orders_counts():
    with get_connection() as conn:
        rows = conn.execute(text("""
            SELECT UPPER(nuport_status) AS s, COUNT(*) AS cnt
            FROM orders
            WHERE nuport_status IS NOT NULL
            GROUP BY UPPER(nuport_status)
        """)).fetchall()

    counts = {k: 0 for k in ('all','pending','on_hold','approved','processing',
                               'ready_to_ship','in_transit','delivered','flagged','cancelled')}
    for s, cnt in rows:
        counts['all'] += cnt
        s2 = s.replace(' ', '_').replace('-', '_')
        if   s2 in ('PENDING', 'REQUESTED'): counts['pending']       += cnt
        elif s2 == 'ON_HOLD':              counts['on_hold']       += cnt
        elif s2.startswith('APPROV'):      counts['approved']      += cnt
        elif s2.startswith('PROCESS'):     counts['processing']    += cnt
        elif 'READY' in s2:                counts['ready_to_ship'] += cnt
        elif 'TRANSIT' in s2:              counts['in_transit']    += cnt
        elif s2 in ('DELIVERED','COMPLETED'): counts['delivered']  += cnt
        elif 'FLAG'   in s2:               counts['flagged']       += cnt
        elif 'CANCEL' in s2:               counts['cancelled']     += cnt
    return jsonify(counts)


@app.route('/api/orders')
@login_required
def api_orders():
    tab    = request.args.get('tab', 'all')
    try:
        page = max(1, int(request.args.get('page', 1) or 1))
    except (ValueError, TypeError):
        page = 1
    days   = request.args.get('days', '').strip()
    search = request.args.get('search', '').strip()[:100]

    status_filter = _ORDER_TAB_FILTERS.get(tab, '1=1')

    date_filter = ''
    if days.isdigit():
        date_filter = f"AND o.order_date >= NOW() - INTERVAL '{int(days)} days'"

    search_filter = ''
    sql_params: dict = {}
    if search:
        search_filter = """AND (
            o.so_number         ILIKE :srch
            OR o.customer_name  ILIKE :srch
            OR o.customer_phone ILIKE :srch
            OR o.pathao_waybill ILIKE :srch
        )"""
        sql_params['srch'] = f'%{search}%'

    limit  = 50
    offset = (page - 1) * limit
    sql_params['limit']  = limit
    sql_params['offset'] = offset

    with get_connection() as conn:
        rows = conn.execute(text(f"""
            SELECT
                o.so_number,
                o.nuport_status,
                o.source_channel,
                o.order_date,
                o.shipped_date,
                o.customer_name,
                o.customer_phone,
                COALESCE(c.district, c.city, '')    AS region,
                COALESCE(o.product_total, 0)        AS product_total,
                COALESCE(o.delivery_fee, 0)         AS delivery_fee,
                COALESCE(o.total_receivable, 0)     AS total_receivable,
                COALESCE(o.collected_amount, 0)     AS collected_amount,
                o.pathao_waybill,
                COUNT(oi.id)                        AS item_count
            FROM orders o
            LEFT JOIN customers c    ON o.customer_phone = c.phone
            LEFT JOIN order_items oi ON o.so_number = oi.so_number
            WHERE {status_filter}
              {date_filter}
              {search_filter}
            GROUP BY o.so_number, o.nuport_status, o.source_channel,
                     o.order_date, o.shipped_date, o.customer_name, o.customer_phone,
                     c.district, c.city, o.product_total, o.delivery_fee,
                     o.total_receivable, o.collected_amount, o.pathao_waybill
            ORDER BY o.order_date DESC NULLS LAST
            LIMIT :limit OFFSET :offset
        """), sql_params).fetchall()

        count_params = {k: v for k, v in sql_params.items() if k not in ('limit', 'offset')}
        total = conn.execute(text(f"""
            SELECT COUNT(DISTINCT o.so_number)
            FROM orders o
            WHERE {status_filter}
              {date_filter}
              {search_filter}
        """), count_params).scalar() or 0

    orders = []
    for r in rows:
        recv = float(r[10])
        coll = float(r[11])
        orders.append({
            'so_number':    r[0],
            'status':       r[1] or '',
            'source':       r[2] or '',
            'order_date':   r[3].strftime('%d %b %Y, %I:%M %p') if r[3] else '',
            'shipped_date': r[4].strftime('%d %b %Y') if r[4] else '',
            'customer':     r[5] or '',
            'phone':        r[6] or '',
            'region':       r[7] or '',
            'product_total': float(r[8]),
            'delivery_fee':  float(r[9]),
            'receivable':    recv,
            'collected':     coll,
            'due':           round(max(0.0, recv - coll), 2),
            'waybill':       r[12] or '',
            'items':         int(r[13] or 0),
        })

    return jsonify({
        'orders': orders,
        'total':  total,
        'page':   page,
        'pages':  max(1, (total + limit - 1) // limit),
        'limit':  limit,
    })


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
