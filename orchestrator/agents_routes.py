"""
Agents blueprint — Telegram webhook + the Operations dashboard's Agents page.

Routes:
  POST /webhook/telegram         Telegram update receiver (product photos, commands, approvals)
  GET  /agents                   Dashboard page — pending approvals + recent agent activity
  GET  /api/agents/pending       JSON list of pending approvals
  GET  /api/agents/recent        JSON list of recently created agent products
  POST /api/agents/confirm/<id>  Approve a staged action from the dashboard
  POST /api/agents/reject/<id>   Reject a staged action from the dashboard
"""
import os
import sys
import threading
from functools import wraps

from flask import Blueprint, request, session, redirect, url_for, jsonify, render_template_string

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from orchestrator import telegram_alert, pending_actions
from orchestrator.agents import product_agent
from orchestrator.agent_tools import brain as agent_brain

agents_bp = Blueprint('agents', __name__)

MAX_IMAGE_BYTES = 20 * 1024 * 1024


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated


# ── Telegram webhook ─────────────────────────────────────────────────────────

@agents_bp.route('/webhook/telegram', methods=['POST'])
def telegram_webhook():
    update = request.get_json(silent=True) or {}
    message = update.get('message')
    if not message:
        print(f"[telegram_webhook] no 'message' in update: {update}", flush=True)
        return jsonify({'ok': True})

    chat_id = message.get('chat', {}).get('id')
    print(f"[telegram_webhook] received message from chat_id={chat_id!r} "
          f"expected={telegram_alert.AGENT_CHAT_ID!r} keys={list(message.keys())}", flush=True)
    if not telegram_alert.is_authorized_chat(chat_id, telegram_alert.AGENT_CHAT_ID):
        print(f"[telegram_webhook] chat_id {chat_id!r} not authorized — ignoring.", flush=True)
        return jsonify({'ok': True})

    document = message.get('document')
    is_image_document = document and (document.get('mime_type') or '').startswith('image/')

    if 'photo' in message:
        print("[telegram_webhook] dispatching _handle_photo", flush=True)
        threading.Thread(target=_handle_photo, args=(message,), daemon=True).start()
    elif is_image_document:
        print("[telegram_webhook] dispatching _handle_document", flush=True)
        threading.Thread(target=_handle_document, args=(message,), daemon=True).start()
    elif document:
        print(f"[telegram_webhook] ignoring non-image document mime_type={document.get('mime_type')!r}", flush=True)
        telegram_alert.send("❌ That file isn't an image — send a photo or an image file (jpg/png/webp).",
                             telegram_alert.AGENT_BOT_TOKEN, telegram_alert.AGENT_CHAT_ID)
    elif 'text' in message:
        print("[telegram_webhook] dispatching _handle_text", flush=True)
        threading.Thread(target=_handle_text, args=(message['text'],), daemon=True).start()

    return jsonify({'ok': True})


SUPPORTED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}


def _process_image(file_id: str, media_type: str, caption: str):
    image_bytes = telegram_alert.download_photo(file_id, telegram_alert.AGENT_BOT_TOKEN)
    print(f"[_process_image] downloaded {len(image_bytes)} bytes, media_type={media_type}", flush=True)
    if len(image_bytes) > MAX_IMAGE_BYTES:
        telegram_alert.send("❌ Image too large (max 20MB). Send a smaller file.",
                             telegram_alert.AGENT_BOT_TOKEN, telegram_alert.AGENT_CHAT_ID)
        return

    import base64
    image_data = {'base64': base64.b64encode(image_bytes).decode(), 'media_type': media_type}
    print("[_process_image] calling product_agent.run_agent...", flush=True)
    product_agent.run_agent(caption, image_data)
    print("[_process_image] run_agent finished", flush=True)


def _handle_photo(message: dict):
    try:
        photo_sizes = message['photo']
        largest = max(photo_sizes, key=lambda p: p.get('file_size', 0) or p.get('width', 0))
        caption = message.get('caption', '')
        print("[_handle_photo] downloading photo...", flush=True)
        _process_image(largest['file_id'], 'image/jpeg', caption)
    except Exception as e:
        print(f"[_handle_photo] EXCEPTION: {e!r}", flush=True)
        telegram_alert.send(f"❌ Could not process photo: {e}",
                             telegram_alert.AGENT_BOT_TOKEN, telegram_alert.AGENT_CHAT_ID)


def _handle_document(message: dict):
    try:
        document = message['document']
        mime_type = document.get('mime_type') or 'image/jpeg'
        if mime_type not in SUPPORTED_IMAGE_TYPES:
            telegram_alert.send(f"❌ Unsupported image type: {mime_type}. Use jpg, png, webp, or gif.",
                                 telegram_alert.AGENT_BOT_TOKEN, telegram_alert.AGENT_CHAT_ID)
            return
        caption = message.get('caption', '')
        print(f"[_handle_document] downloading document, mime_type={mime_type}...", flush=True)
        _process_image(document['file_id'], mime_type, caption)
    except Exception as e:
        print(f"[_handle_document] EXCEPTION: {e!r}", flush=True)
        telegram_alert.send(f"❌ Could not process file: {e}",
                             telegram_alert.AGENT_BOT_TOKEN, telegram_alert.AGENT_CHAT_ID)


def _handle_text(text: str):
    stripped = text.strip()
    first_word = stripped.split(' ', 1)[0].lower() if stripped else ''

    if first_word in ('yes', 'y', 'confirm', 'approve'):
        correction = stripped[len(first_word):].strip().lstrip(',').strip()
        product_agent.confirm_pending_action(correction_text=correction)
        return

    if first_word in ('no', 'n', 'cancel', 'reject'):
        product_agent.reject_pending_action()
        return

    product_agent.run_agent(stripped)


# ── Dashboard API ─────────────────────────────────────────────────────────────

@agents_bp.route('/api/agents/pending')
@login_required
def api_agents_pending():
    rows = pending_actions.list_pending()

    def fmt(r):
        d = dict(r)
        if d.get('created_at'):
            d['created_at'] = d['created_at'].isoformat()
        if d.get('resolved_at'):
            d['resolved_at'] = d['resolved_at'].isoformat()
        return d

    return jsonify([fmt(r) for r in rows])


@agents_bp.route('/api/agents/recent')
@login_required
def api_agents_recent():
    rows = agent_brain.recent_products()

    def fmt(r):
        d = dict(r)
        for k in ('created_at', 'published_at'):
            if d.get(k):
                d[k] = d[k].isoformat()
        return d

    return jsonify([fmt(r) for r in rows])


@agents_bp.route('/api/agents/confirm/<int:action_id>', methods=['POST'])
@login_required
def api_agents_confirm(action_id):
    result = product_agent.confirm_pending_action(action_id=action_id)
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@agents_bp.route('/api/agents/reject/<int:action_id>', methods=['POST'])
@login_required
def api_agents_reject(action_id):
    result = product_agent.reject_pending_action(action_id=action_id)
    if 'error' in result:
        return jsonify(result), 400
    return jsonify(result)


@agents_bp.route('/agents')
@login_required
def agents_page():
    return render_template_string(AGENTS_HTML)


# ── HTML ──────────────────────────────────────────────────────────────────────

AGENTS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Winterfell — Agents</title>
<style>
:root{
  --header-bg:#161b22;--header-border:#30363d;
  --bg-page:#F6F8FA;--bg-card:#FFFFFF;--bg-inner:#F9FAFB;
  --text-primary:#1A1F2E;--text-secondary:#4A5568;--text-tertiary:#718096;
  --border:#E1E7EF;--teal:#1D9E75;--amber:#EF9F27;--red:#E24B4A;--purple:#7F77DD;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg-page);color:var(--text-primary);font-family:'Segoe UI',system-ui,sans-serif;min-height:100vh}
header{background:var(--header-bg);border-bottom:1px solid var(--header-border);
       padding:12px 24px;display:grid;grid-template-columns:1fr auto 1fr;
       grid-template-areas:'brand nav actions';align-items:center;gap:8px;
       position:sticky;top:0;z-index:50}
header h1{font-size:1rem;font-weight:700;color:#f0f6fc;grid-area:brand}
.hdr-actions{grid-area:actions;display:flex;align-items:center;gap:12px;justify-content:flex-end}
.top-nav{grid-area:nav;display:flex;gap:2px}
.nav-link{color:#8b949e;font-size:.8rem;text-decoration:none;padding:5px 10px;
          border:1px solid transparent;border-radius:6px;transition:.2s;white-space:nowrap}
.nav-link:hover{border-color:#30363d;color:#e6edf3}
.nav-link.active{border-color:#30363d;color:#e6edf3;background:#21262d}
.logout{color:#8b949e;font-size:.8rem;text-decoration:none;padding:5px 10px;
        border:1px solid var(--header-border);border-radius:6px;white-space:nowrap}
.container{max-width:1100px;margin:0 auto;padding:24px 20px}
.page-title{font-size:1.05rem;font-weight:700;margin-bottom:6px}
.page-sub{color:var(--text-tertiary);font-size:.82rem;margin-bottom:22px}
.section{margin-bottom:28px}
.section h2{font-size:.9rem;font-weight:600;margin-bottom:12px}
.card{background:var(--bg-card);border:0.5px solid var(--border);border-radius:10px;padding:16px 20px;margin-bottom:10px}
.card-title{font-weight:600;font-size:.9rem;margin-bottom:6px}
.card-meta{color:var(--text-tertiary);font-size:.78rem;margin-bottom:10px}
.card-payload{background:var(--bg-inner);border-radius:6px;padding:8px 10px;font-size:.75rem;
              color:var(--text-secondary);overflow-x:auto;margin-bottom:10px;white-space:pre-wrap}
.btn{padding:6px 14px;border-radius:6px;border:none;cursor:pointer;font-size:.78rem;font-weight:600;margin-right:8px}
.btn-approve{background:#E1F5EE;color:#085041;border:0.5px solid #5DCAA5}
.btn-reject{background:#FCEBEB;color:#791F1F;border:0.5px solid #F5C6C6}
.empty{color:var(--text-tertiary);font-size:.85rem;padding:20px;text-align:center}
.badge{display:inline-block;padding:2px 10px;border-radius:20px;font-size:.72rem;font-weight:600;text-transform:uppercase}
.badge-draft{background:var(--bg-inner);color:var(--text-tertiary)}
.badge-publish{background:#E1F5EE;color:#085041}
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
    <a href="/inventory" class="nav-link">Inventory</a>
    <a href="/supply-chain" class="nav-link">Supply Chain</a>
    <a href="/agents" class="nav-link active">Agents</a>
  </nav>
  <div class="hdr-actions"><a href="/logout" class="logout">Logout</a></div>
</header>
<div class="container">
  <div class="page-title">AI Agents</div>
  <div class="page-sub">Product Agent listens on Telegram for product photos. Approvals required before anything goes live.</div>

  <div class="section">
    <h2>Pending Approval</h2>
    <div id="pending-list"><div class="empty">Loading...</div></div>
  </div>

  <div class="section">
    <h2>Recent Products</h2>
    <div id="recent-list"><div class="empty">Loading...</div></div>
  </div>
</div>
<script>
async function loadPending() {
  const r = await fetch('/api/agents/pending');
  const rows = await r.json();
  const el = document.getElementById('pending-list');
  if (!rows.length) { el.innerHTML = '<div class="empty">Nothing waiting on approval.</div>'; return; }
  el.innerHTML = rows.map(a => `
    <div class="card">
      <div class="card-title">${a.action_type}</div>
      <div class="card-meta">Staged ${new Date(a.created_at).toLocaleString()}</div>
      <div class="card-payload">${JSON.stringify(a.payload, null, 2)}</div>
      <button class="btn btn-approve" onclick="confirmAction(${a.id}, this)">✅ Approve</button>
      <button class="btn btn-reject" onclick="rejectAction(${a.id}, this)">❌ Reject</button>
    </div>`).join('');
}

async function loadRecent() {
  const r = await fetch('/api/agents/recent');
  const rows = await r.json();
  const el = document.getElementById('recent-list');
  if (!rows.length) { el.innerHTML = '<div class="empty">No products yet.</div>'; return; }
  el.innerHTML = rows.map(p => `
    <div class="card">
      <div class="card-title">${p.name} <span class="badge badge-${p.status}">${p.status}</span></div>
      <div class="card-meta">WooCommerce #${p.woo_id} · ${p.category || '—'} · ৳${p.price || 0} · ${new Date(p.created_at).toLocaleString()}</div>
    </div>`).join('');
}

async function confirmAction(id, btn) {
  btn.disabled = true; btn.textContent = '⏳';
  await fetch(`/api/agents/confirm/${id}`, {method:'POST'});
  loadPending(); loadRecent();
}

async function rejectAction(id, btn) {
  btn.disabled = true; btn.textContent = '⏳';
  await fetch(`/api/agents/reject/${id}`, {method:'POST'});
  loadPending();
}

loadPending();
loadRecent();
setInterval(() => { loadPending(); loadRecent(); }, 15000);
</script>
</body>
</html>"""
