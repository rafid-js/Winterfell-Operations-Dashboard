require('dotenv').config();
const express = require('express');
const fetch = require('node-fetch');
const path = require('path');

const app = express();

app.use(express.json());
app.use(express.urlencoded({ extended: true }));
app.use(express.static(path.join(__dirname, 'public')));

// ── Zoho token management ────────────────────────────────────
let cachedToken = null;
let tokenExpiry = 0;

async function getZohoToken() {
  if (cachedToken && Date.now() < tokenExpiry) return cachedToken;
  const res = await fetch('https://accounts.zoho.com/oauth/v2/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      refresh_token: process.env.ZOHO_REFRESH_TOKEN,
      client_id: process.env.ZOHO_CLIENT_ID,
      client_secret: process.env.ZOHO_CLIENT_SECRET,
      grant_type: 'refresh_token'
    })
  });
  const data = await res.json();
  cachedToken = data.access_token;
  tokenExpiry = Date.now() + (data.expires_in - 60) * 1000;
  return cachedToken;
}

async function zohoGet(path) {
  const token = await getZohoToken();
  const res = await fetch(`https://www.zohoapis.com/books/v3${path}?organization_id=${process.env.ZOHO_ORG_ID}`, {
    headers: { Authorization: `Zoho-oauthtoken ${token}` }
  });
  return res.json();
}

async function zohoPost(path, body) {
  const token = await getZohoToken();
  const res = await fetch(`https://www.zohoapis.com/books/v3${path}?organization_id=${process.env.ZOHO_ORG_ID}`, {
    method: 'POST',
    headers: { Authorization: `Zoho-oauthtoken ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  return res.json();
}

async function zohoPut(path, body) {
  const token = await getZohoToken();
  const res = await fetch(`https://www.zohoapis.com/books/v3${path}?organization_id=${process.env.ZOHO_ORG_ID}`, {
    method: 'PUT',
    headers: { Authorization: `Zoho-oauthtoken ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  });
  return res.json();
}

async function nuportGet(orderId) {
  const res = await fetch(`https://api.nuport.io/integration/orders/${orderId}`, {
    headers: { Authorization: process.env.NUPORT_API_KEY }
  });
  return res.json();
}

// ── Auth middleware ──────────────────────────────────────────
app.use('/api', (req, res, next) => {
  const auth = req.headers['x-app-password'];
  if (auth !== process.env.APP_PASSWORD) {
    return res.status(401).json({ error: 'Unauthorized' });
  }
  next();
});

app.get('/api/ping', (req, res) => {
  const auth = req.headers['x-app-password'];
  if (auth !== process.env.APP_PASSWORD) return res.status(401).json({ error: 'Unauthorized' });
  res.json({ ok: true });
});

// ── Pages ────────────────────────────────────────────────────
app.get('/', (req, res) => res.sendFile(path.join(__dirname, 'public', 'index.html')));

// ── Start ────────────────────────────────────────────────────
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Winterfell Ops running on port ${PORT}`));
