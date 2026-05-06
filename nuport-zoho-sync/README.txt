============================================================
  WINTERFELL OPERATIONS
  Nuport OMS → Zoho Books Auto-Sync
  Setup Guide (Plain English, No Jargon)
============================================================

WHAT THIS DOES
--------------
This tool automatically copies your product catalogue from Nuport
into Zoho Books every 6 hours. It keeps product names, SKUs, and
selling prices in sync. You never have to manually update Zoho again.

It will:
  - CREATE new items in Zoho if a Nuport SKU doesn't exist there yet
  - UPDATE existing Zoho items when price or name changes in Nuport
  - NEVER delete anything from Zoho
  - SKIP products with no SKU or zero price (and log a warning)

------------------------------------------------------------
STEP 1 — INSTALL PYTHON (if you don't have it)
------------------------------------------------------------
1. Open your browser and go to python.org/downloads
2. Click the big yellow "Download Python" button
3. Run the installer
4. IMPORTANT: On the first screen, tick the checkbox that says
   "Add Python to PATH" before clicking Install Now
5. Click Install Now
6. When done, open Command Prompt (press Win+R, type cmd, press Enter)
7. Type this and press Enter:
     python --version
8. You should see something like "Python 3.12.0"
   If you see an error, restart your PC and try again.

No other packages needed — this script uses only Python's built-in tools.

------------------------------------------------------------
STEP 2 — DOWNLOAD THIS FOLDER
------------------------------------------------------------
1. Download the nuport-zoho-sync folder from GitHub to your PC
2. Put it somewhere easy to find, for example:
     C:\Winterfell\nuport-zoho-sync\

------------------------------------------------------------
STEP 3 — GET YOUR NUPORT API KEY
------------------------------------------------------------
1. Log in to app.nuport.io
2. Go to Settings (gear icon, usually bottom-left)
3. Look for "API Keys" or "Integrations" or "Developer Settings"
4. Copy your API key (it looks like a long random string)
5. You'll paste this into config.json in Step 5

------------------------------------------------------------
STEP 4 — GET YOUR ZOHO REFRESH TOKEN
------------------------------------------------------------
You need a "refresh token" so the script can log into Zoho
automatically without you typing a password every time.

If you already have one, skip to Step 5.

To get a refresh token:
1. Go to: https://api-console.zoho.com
   (Use the same Zoho account as your Zoho Books)
2. Click "Add Client"
3. Choose "Server-based Applications"
4. Fill in:
     Client Name: Winterfell Sync
     Homepage URL: https://winterfell.com (anything works here)
     Authorized Redirect URIs: https://www.zoho.com/books
5. Click "Create"
6. You'll see a "Client ID" and "Client Secret" — copy both
7. Now open this URL in your browser (replace YOUR_CLIENT_ID):
     https://accounts.zoho.com/oauth/v2/auth?scope=ZohoBooks.fullaccess.all&client_id=YOUR_CLIENT_ID&response_type=code&access_type=offline&redirect_uri=https://www.zoho.com/books
8. Log in and click "Accept"
9. You'll be taken to a page with a "code" in the URL, like:
     https://www.zoho.com/books?code=1000.abcd1234...
   Copy that code (just the part after "code=")
10. Open Command Prompt and run this (replace the placeholders):
     curl -X POST "https://accounts.zoho.com/oauth/v2/token" -d "code=YOUR_CODE&client_id=YOUR_CLIENT_ID&client_secret=YOUR_CLIENT_SECRET&redirect_uri=https://www.zoho.com/books&grant_type=authorization_code"
11. You'll get a JSON response. Copy the "refresh_token" value.

NOTE: If you are in South Asia (India region on Zoho), replace
"zoho.com" with "zoho.in" everywhere above and set
"zoho_region": "in" in config.json.

------------------------------------------------------------
STEP 5 — FILL IN config.json
------------------------------------------------------------
1. In the nuport-zoho-sync folder, find "config.example.json"
2. Make a COPY of it and rename the copy to "config.json"
   (the original stays as a backup template)
3. Open config.json with Notepad
4. Fill in your details between the quotes:

   "nuport_api_key"    — your Nuport API key from Step 3
   "nuport_base_url"   — leave as "https://api.nuport.io"
   "zoho_org_id"       — your Zoho Organization ID
                         (Zoho Books → Settings → Organization Profile)
   "zoho_client_id"    — from Step 4
   "zoho_client_secret"— from Step 4
   "zoho_refresh_token"— from Step 4
   "zoho_region"       — "com" for most users, "in" for India/South Asia

5. Save and close Notepad

IMPORTANT: Never share config.json with anyone. It contains your
passwords. It is already excluded from GitHub uploads (.gitignore).

------------------------------------------------------------
STEP 6 — RUN IT FOR THE FIRST TIME
------------------------------------------------------------
1. Go to the nuport-zoho-sync folder
2. Double-click "run_sync.bat"
3. A black window will open and show progress
4. Wait for it to finish — you'll see a summary like:
     Sync complete: 12 created, 0 updated, 0 skipped, 0 errors
5. Press any key to close the window

That's it! Your Zoho Books items are now synced.

------------------------------------------------------------
STEP 7 — SET UP AUTOMATIC SYNC (runs every 6 hours)
------------------------------------------------------------
1. Right-click "schedule_task.bat"
2. Choose "Run as Administrator"
3. Click Yes on the security prompt
4. You should see "SUCCESS!" in the window
5. Press any key to close

From now on, the sync runs automatically every 6 hours.
You don't need to do anything else.

To verify the task was created:
- Press Win+R, type "taskschd.msc", press Enter
- Look for "Winterfell Nuport-Zoho Sync" in the list

------------------------------------------------------------
STEP 8 — HOW TO CHECK IF IT WORKED
------------------------------------------------------------
1. Open the "logs" folder inside nuport-zoho-sync
2. You'll see a file named like "sync_2026-05-06.log"
3. Open it with Notepad
4. Scroll to the bottom — you'll see the summary line:
     Sync complete: X created, Y updated, Z skipped, W errors
5. You can also check Zoho Books → Items to see the products

------------------------------------------------------------
COMMON ERRORS AND WHAT TO DO
------------------------------------------------------------

ERROR: "python is not recognized as a command"
  → Python is not installed or not added to PATH
  → Reinstall Python and make sure "Add to PATH" is ticked

ERROR: "config.json not found"
  → You forgot to create config.json from config.example.json
  → See Step 5 above

ERROR: "Zoho token request failed — HTTP 400"
  → Your Zoho refresh token is wrong or expired
  → Get a new refresh token following Step 4 again

ERROR: "Zoho token request failed — HTTP 400: invalid_client"
  → Your Client ID or Client Secret is wrong
  → Double-check them in api-console.zoho.com

ERROR: "Nuport fetch failed — HTTP 401"
  → Your Nuport API key is wrong or expired
  → Check Settings in Nuport and update nuport_api_key in config.json

ERROR: "Zoho rejected create: This item name already exists"
  → The product exists in Zoho but without a SKU set
  → Open that item in Zoho Books, add the SKU, and run sync again

ERROR: Sync runs but 0 items created/updated
  → All your Nuport products may be missing SKUs or have price=0
  → Open Nuport and check that your products have SKU and price filled in
  → Check the log file for "SKIP" warning lines

ERROR: Python window closes immediately with no output
  → There is likely a crash — open Command Prompt, navigate to the
    folder (cd C:\Winterfell\nuport-zoho-sync) and type:
    python sync_nuport_zoho.py
  → You'll see the full error message

------------------------------------------------------------
NEED HELP?
------------------------------------------------------------
Send the log file from the logs\ folder to your developer.
The log file contains everything needed to diagnose any problem.

============================================================
