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
@login_required
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


if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
