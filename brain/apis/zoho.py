"""
Zoho Books API client.
- Loads credentials once from brain/.env
- Caches the access token to disk (.zoho_token) so it survives between script runs
- Auto-refreshes only when the token is expired (every ~55 min)
- Import anywhere: from apis.zoho import zoho
"""
import os
import json
import time
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

_CACHE = Path(__file__).parent.parent / '.zoho_token'


class ZohoClient:
    BOOKS_URL = "https://www.zohoapis.com/books/v3"
    TOKEN_URL = "https://accounts.zoho.com/oauth/v2/token"

    def __init__(self):
        self.client_id     = os.getenv('ZOHO_CLIENT_ID')
        self.client_secret = os.getenv('ZOHO_CLIENT_SECRET')
        self.refresh_token = os.getenv('ZOHO_REFRESH_TOKEN')
        self.org_id        = os.getenv('ZOHO_ORG_ID')
        self._token        = None
        self._expires_at   = 0
        self._load_cache()

    def _load_cache(self):
        if _CACHE.exists():
            try:
                d = json.loads(_CACHE.read_text())
                self._token      = d.get('access_token')
                self._expires_at = d.get('expires_at', 0)
            except Exception:
                pass

    def _save_cache(self):
        try:
            _CACHE.write_text(json.dumps({
                'access_token': self._token,
                'expires_at':   self._expires_at,
            }))
        except Exception:
            pass

    def token(self):
        if self._token and time.time() < self._expires_at:
            return self._token

        r = requests.post(self.TOKEN_URL, data={
            'refresh_token': self.refresh_token,
            'client_id':     self.client_id,
            'client_secret': self.client_secret,
            'grant_type':    'refresh_token',
        }, timeout=15)
        d = r.json()

        if 'access_token' not in d:
            raise RuntimeError(f"Zoho token refresh failed: {d}")

        self._token      = d['access_token']
        self._expires_at = time.time() + d.get('expires_in', 3600) - 60
        self._save_cache()
        print(f"  ↻ Zoho token refreshed (valid {d.get('expires_in', 3600) // 60} min)")
        return self._token

    def _headers(self):
        return {'Authorization': f'Zoho-oauthtoken {self.token()}'}

    def _params(self, extra=None):
        p = {'organization_id': self.org_id}
        if extra:
            p.update(extra)
        return p

    def get(self, path, params=None):
        r = requests.get(
            f"{self.BOOKS_URL}{path}",
            headers=self._headers(),
            params=self._params(params),
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def post(self, path, body):
        r = requests.post(
            f"{self.BOOKS_URL}{path}",
            headers=self._headers(),
            params=self._params(),
            json=body,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def put(self, path, body):
        r = requests.put(
            f"{self.BOOKS_URL}{path}",
            headers=self._headers(),
            params=self._params(),
            json=body,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()


zoho = ZohoClient()
