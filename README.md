# Winterfell Operations Dashboard

A self-hosted web app for Winterfell's finance and order operations team.

## Tools Included
- 💸 **bKash Advance** — Customer support records advance payments
- ⚡ **Batch Payments** — Push Pathao settlement CSVs to Zoho Books
- 🔧 **Partial Order Fixer** — Fix invoices with partial/exchange deliveries
- 🔄 **Reconciliation** — Process Pathao settlement files

## Deploy to Railway (Recommended)

### 1. Get a Zoho Refresh Token (one-time setup)

You need a **refresh token** (not an access token) so the server can auto-renew:

1. Go to https://accounts.zoho.com/developerconsole
2. Click your **Winterfell** app → **Client Secret** tab → copy Client ID and Client Secret
3. Click **⋮ → Self Client** → enter scope:
   ```
   ZohoBooks.invoices.READ,ZohoBooks.invoices.UPDATE,ZohoBooks.customerpayments.CREATE
   ```
4. Duration: **10 minutes** → Create → copy the **code**
5. Exchange for refresh token via curl:
   ```bash
   curl -X POST https://accounts.zoho.com/oauth/v2/token \
     -d "code=YOUR_CODE" \
     -d "client_id=YOUR_CLIENT_ID" \
     -d "client_secret=YOUR_CLIENT_SECRET" \
     -d "redirect_uri=https://localhost" \
     -d "grant_type=authorization_code"
   ```
6. Copy the `refresh_token` from the response — this never expires

### 2. Deploy to Railway

1. Create account at https://railway.app
2. Click **New Project → Deploy from GitHub**
3. Upload this folder to a GitHub repo, or use **Deploy from local** 
4. Add environment variables in Railway dashboard:
   ```
   ZOHO_CLIENT_ID=your_client_id
   ZOHO_CLIENT_SECRET=your_client_secret
   ZOHO_REFRESH_TOKEN=your_refresh_token
   ZOHO_ORG_ID=878028611
   ZOHO_BANK_ACCOUNT_ID=6038376000000095011
   NUPORT_API_KEY=dYaAxa5pemfxDYd3Oop1y4suYvd4MFDW
   APP_PASSWORD=choose_a_strong_password
   ```
5. Railway auto-detects Node.js and deploys — you get a URL like `https://winterfell-ops.railway.app`

### 3. Share with team
- Give staff the URL + APP_PASSWORD
- They log in once per session
- No Zoho access needed — all API calls happen server-side

## Local Development
```bash
npm install
cp .env.example .env
# Fill in .env values
npm run dev
# Open http://localhost:3000
```

## Adding More Tools
Each tool is a page in `public/index.html` + an API route in `server.js`.
Pattern: add nav item → add page div → add `/api/toolname` route.
