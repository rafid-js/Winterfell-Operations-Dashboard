require('dotenv').config();
const express = require('express');
const fetch = require('node-fetch');
const multer = require('multer');
const { parse } = require('csv-parse/sync');
const path = require('path');

const app = express();
const upload = multer({ storage: multer.memoryStorage() });

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

// ── Pages ────────────────────────────────────────────────────
app.get('/', (req, res) => res.sendFile(path.join(__dirname, 'public', 'index.html')));

// ── API: bKash Advance ───────────────────────────────────────
app.post('/api/bkash/record', async (req, res) => {
  try {
    const { orderId, amount, date, customerName } = req.body;
    if (!orderId || !amount) return res.status(400).json({ error: 'Order ID and amount required' });

    // Find invoice
    const search = await zohoGet(`/invoices?invoice_number=${encodeURIComponent(orderId)}`);
    if (!search.invoices?.length) return res.status(404).json({ error: `Invoice ${orderId} not found in Zoho Books` });

    const inv = search.invoices[0];
    if (parseFloat(inv.balance) <= 0) return res.status(400).json({ error: `Invoice ${orderId} is already fully paid` });
    if (parseFloat(amount) > parseFloat(inv.balance)) {
      return res.status(400).json({ error: `Amount ৳${amount} exceeds invoice balance ৳${inv.balance}` });
    }

    const payResp = await zohoPost('/customerpayments', {
      account_id: process.env.ZOHO_BANK_ACCOUNT_ID,
      customer_id: inv.customer_id,
      invoice_id: inv.invoice_id,
      invoices: [{ invoice_id: inv.invoice_id, amount_applied: parseFloat(amount) }],
      amount: parseFloat(amount),
      amount_applied: parseFloat(amount),
      bank_charges: 0,
      date: date || new Date().toISOString().split('T')[0],
      payment_mode: 'others',
      reference_number: `bKash advance — ${customerName || inv.customer_name} ${orderId}`
    });

    if (payResp.code !== 0) return res.status(400).json({ error: payResp.message });

    res.json({
      success: true,
      message: `✅ ৳${amount} bKash advance recorded for ${orderId} (${inv.customer_name})`,
      newBalance: parseFloat(inv.balance) - parseFloat(amount),
      paymentId: payResp.payment?.payment_id
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── API: Look up invoice ─────────────────────────────────────
app.get('/api/invoice/:orderId', async (req, res) => {
  try {
    const data = await zohoGet(`/invoices?invoice_number=${encodeURIComponent(req.params.orderId)}`);
    if (!data.invoices?.length) return res.status(404).json({ error: 'Invoice not found' });
    const inv = data.invoices[0];
    res.json({ invoiceNumber: inv.invoice_number, customerName: inv.customer_name, total: inv.total, balance: inv.balance, status: inv.status });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── API: Nuport order fetch ──────────────────────────────────
app.get('/api/nuport/:orderId', async (req, res) => {
  try {
    const data = await nuportGet(req.params.orderId);
    if (!data.salesOrderItems) return res.status(404).json({ error: data.message || 'Order not found' });
    res.json(data);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── API: Partial order fix ───────────────────────────────────
app.post('/api/partial/fix', async (req, res) => {
  try {
    const { orderId, deliveredSkus, collected, bankCharges, paymentDate, reference, assocOrder, advanceAmount, isExchange } = req.body;

    const search = await zohoGet(`/invoices?invoice_number=${encodeURIComponent(orderId)}`);
    if (!search.invoices?.length) return res.status(404).json({ error: `Invoice ${orderId} not found` });
    const inv = search.invoices[0];
    const invoiceId = inv.invoice_id;
    const customerId = inv.customer_id;

    const fullInv = await zohoGet(`/invoices/${invoiceId}`);
    const lineItems = fullInv.invoice.line_items;
    const balance = parseFloat(fullInv.invoice.balance);

    if (balance <= 0) return res.json({ success: true, message: `${orderId} already paid` });

    const results = [];

    if (isExchange && assocOrder && advanceAmount > 0) {
      // Record advance on associated order
      const assocSearch = await zohoGet(`/invoices?invoice_number=${encodeURIComponent(assocOrder)}`);
      if (assocSearch.invoices?.length) {
        const assocInv = assocSearch.invoices[0];
        if (parseFloat(assocInv.balance) > 0) {
          await zohoPost('/customerpayments', {
            account_id: process.env.ZOHO_BANK_ACCOUNT_ID,
            customer_id: assocInv.customer_id,
            invoice_id: assocInv.invoice_id,
            invoices: [{ invoice_id: assocInv.invoice_id, amount_applied: advanceAmount }],
            amount: advanceAmount, amount_applied: advanceAmount, bank_charges: 0,
            date: paymentDate, payment_mode: 'cash',
            reference_number: `Advance payment — exchange order ${orderId}`
          });
          results.push(`✅ ৳${advanceAmount} advance recorded on ${assocOrder}`);
        }
      }
      // Apply exchange adjustment
      const productTotal = lineItems.filter(li => deliveredSkus.includes(li.sku))
        .reduce((s, li) => s + li.rate * li.quantity, 0);
      await zohoPut(`/invoices/${invoiceId}`, {
        customer_id: customerId,
        line_items: lineItems.filter(li => deliveredSkus.includes(li.sku))
          .map(li => ({ item_id: li.item_id, line_item_id: li.line_item_id, quantity: li.quantity, rate: li.rate })),
        adjustment: -(productTotal),
        adjustment_description: `Exchange credit — covered by ${assocOrder} advance`,
        shipping_charge: collected > 0 ? String(collected) : '0',
        reason: `Exchange order — product cost offset by ${assocOrder}`
      });
      results.push(`✅ Invoice ${orderId} updated for exchange`);
    } else {
      // Partial delivery — keep delivered items only
      const keptItems = lineItems.filter(li => deliveredSkus.includes(li.sku));
      const keptTotal = keptItems.reduce((s, li) => s + li.rate * li.quantity, 0);
      const shipping = Math.max(0, Math.round((collected - keptTotal) * 100) / 100);
      await zohoPut(`/invoices/${invoiceId}`, {
        customer_id: customerId,
        line_items: keptItems.map(li => ({ item_id: li.item_id, line_item_id: li.line_item_id, quantity: li.quantity, rate: li.rate })),
        shipping_charge: String(shipping),
        reason: `Partial delivery — ${lineItems.length - keptItems.length} item(s) returned`
      });
      results.push(`✅ Invoice updated — ${lineItems.length - keptItems.length} item(s) removed`);
    }

    // Record payment
    const updatedInv = await zohoGet(`/invoices/${invoiceId}`);
    const newBalance = parseFloat(updatedInv.invoice.balance);
    if (newBalance > 0 && collected > 0) {
      const payResp = await zohoPost('/customerpayments', {
        account_id: process.env.ZOHO_BANK_ACCOUNT_ID,
        customer_id: customerId, invoice_id: invoiceId,
        invoices: [{ invoice_id: invoiceId, amount_applied: Math.min(collected, newBalance) }],
        amount: Math.min(collected, newBalance),
        amount_applied: Math.min(collected, newBalance),
        bank_charges: bankCharges || 0,
        date: paymentDate, payment_mode: 'banktransfer', reference_number: reference
      });
      if (payResp.code === 0) results.push(`✅ Payment ৳${collected} recorded`);
      else results.push(`⚠️ Payment failed: ${payResp.message}`);
    }

    res.json({ success: true, results });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── API: Batch payments ──────────────────────────────────────
app.post('/api/batch/process', upload.single('csv'), async (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');

  const send = (data) => res.write(`data: ${JSON.stringify(data)}\n\n`);

  try {
    const csvText = req.file.buffer.toString('utf8').replace(/^\uFEFF/, '');
    const rows = parse(csvText, { columns: true, skip_empty_lines: true });
    send({ type: 'total', count: rows.length });

    let paid = 0, fixed = 0, skipped = 0, errors = 0;

    for (const row of rows) {
      const invoiceNum = row['Invoice Number'];
      const csvAmount = parseFloat(row['Amount Received']);
      const bankCharges = parseFloat(row['Bank Charges']);
      const payDate = row['Payment Date'];
      const reference = row['Reference Number'];

      try {
        const search = await zohoGet(`/invoices?invoice_number=${encodeURIComponent(invoiceNum)}`);
        if (!search.invoices?.length) {
          send({ type: 'row', invoice: invoiceNum, status: 'error', message: 'Not found in Zoho' });
          errors++; continue;
        }

        const inv = search.invoices[0];
        const balance = parseFloat(inv.balance);

        if (balance <= 0) {
          send({ type: 'row', invoice: invoiceNum, status: 'skipped', message: 'Already paid' });
          skipped++; continue;
        }

        let amountToApply = csvAmount;
        let shippingAdded = 0;
        const diff = Math.round((csvAmount - balance) * 100) / 100;

        if (diff > 0.5) {
          // Need shipping fix
          const fullInv = await zohoGet(`/invoices/${inv.invoice_id}`);
          const lineItems = fullInv.invoice.line_items.map(li => ({
            item_id: li.item_id, line_item_id: li.line_item_id,
            quantity: li.quantity, rate: li.rate
          }));
          const updateResp = await zohoPut(`/invoices/${inv.invoice_id}`, {
            customer_id: inv.customer_id, line_items: lineItems,
            shipping_charge: diff.toFixed(2), reason: 'Adding Pathao shipping charge'
          });
          if (updateResp.code !== 0) throw new Error('Update failed: ' + updateResp.message);
          shippingAdded = diff;
          fixed++;
        } else {
          amountToApply = balance;
        }

        const payResp = await zohoPost('/customerpayments', {
          account_id: process.env.ZOHO_BANK_ACCOUNT_ID,
          customer_id: inv.customer_id, invoice_id: inv.invoice_id,
          invoices: [{ invoice_id: inv.invoice_id, amount_applied: amountToApply }],
          amount: amountToApply, amount_applied: amountToApply,
          bank_charges: bankCharges, date: payDate,
          payment_mode: 'banktransfer', reference_number: reference
        });

        if (payResp.code !== 0) throw new Error(payResp.message);

        paid++;
        send({ type: 'row', invoice: invoiceNum, status: 'paid', amount: amountToApply, shippingAdded });
      } catch (e) {
        errors++;
        send({ type: 'row', invoice: invoiceNum, status: 'error', message: e.message });
      }

      await new Promise(r => setTimeout(r, 350));
    }

    send({ type: 'done', paid, fixed, skipped, errors });
    res.end();
  } catch (e) {
    send({ type: 'error', message: e.message });
    res.end();
  }
});

// ── Start ────────────────────────────────────────────────────
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Winterfell Ops running on port ${PORT}`));
