"""
Pathao Courier API client.
Import anywhere: from apis.pathao import pathao

Auth: OAuth2 client_credentials — access token cached in memory, refreshed on 401.
Webhook: use PATHAO_WEBHOOK_SECRET to verify incoming webhook signatures.
"""
import os
import hmac
import hashlib
import time
import requests
from dotenv import load_dotenv

load_dotenv()

_BASE   = "https://api-hermes.pathao.com"
_AUTH   = f"{_BASE}/aladdin/api/v1/issue-token"


class PathaoClient:

    def __init__(self):
        self._client_id      = None
        self._client_secret  = None
        self._webhook_secret = ''
        self._access_token   = None
        self._expires_at     = 0

    def _ensure_auth(self):
        if self._client_id is None:
            self._client_id      = os.getenv('PATHAO_CLIENT_ID')
            self._client_secret  = os.getenv('PATHAO_CLIENT_SECRET')
            self._webhook_secret = os.getenv('PATHAO_WEBHOOK_SECRET', '')
            if not self._client_id or not self._client_secret:
                raise RuntimeError("PATHAO_CLIENT_ID / PATHAO_CLIENT_SECRET not set in environment")

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _token(self) -> str:
        self._ensure_auth()
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        r = requests.post(_AUTH, json={
            'client_id':     self._client_id,
            'client_secret': self._client_secret,
            'grant_type':    'client_credentials',
        }, timeout=30)
        r.raise_for_status()
        data = r.json()
        self._access_token = data['access_token']
        self._expires_at   = time.time() + int(data.get('expires_in', 3600))
        return self._access_token

    def _headers(self) -> dict:
        return {
            'Authorization': f'Bearer {self._token()}',
            'Content-Type':  'application/json',
        }

    def _get(self, path: str, params: dict = None) -> dict:
        r = requests.get(f"{_BASE}{path}", headers=self._headers(),
                         params=params or {}, timeout=30)
        if r.status_code == 401:
            self._access_token = None
            r = requests.get(f"{_BASE}{path}", headers=self._headers(),
                             params=params or {}, timeout=30)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        r = requests.post(f"{_BASE}{path}", headers=self._headers(),
                          json=body, timeout=30)
        if r.status_code == 401:
            self._access_token = None
            r = requests.post(f"{_BASE}{path}", headers=self._headers(),
                              json=body, timeout=30)
        r.raise_for_status()
        return r.json()

    # ── Orders ────────────────────────────────────────────────────────────────

    def create_order(self, payload: dict) -> dict:
        """Create a new Pathao delivery order. Returns consignment data."""
        return self._post('/aladdin/api/v1/orders', payload)

    def get_order(self, consignment_id: str) -> dict:
        """Get status of a single consignment."""
        return self._get(f'/aladdin/api/v1/orders/{consignment_id}')

    # ── Tracking ──────────────────────────────────────────────────────────────

    def track(self, consignment_id: str) -> dict:
        """Fetch tracking events for a consignment."""
        return self._get(f'/aladdin/api/v1/orders/{consignment_id}/tracking')

    # ── Stores / Zones ────────────────────────────────────────────────────────

    def list_stores(self, page: int = 1) -> dict:
        return self._get('/aladdin/api/v1/stores', {'page': page})

    def list_cities(self) -> dict:
        return self._get('/aladdin/api/v1/city-list')

    def list_zones(self, city_id: int) -> dict:
        return self._get('/aladdin/api/v1/zones/list', {'city_id': city_id})

    def list_areas(self, zone_id: int) -> dict:
        return self._get('/aladdin/api/v1/areas/list', {'zone_id': zone_id})

    # ── Webhook verification ──────────────────────────────────────────────────

    def verify_webhook(self, raw_body: bytes, signature_header: str) -> bool:
        """
        Verify a Pathao webhook POST using PATHAO_WEBHOOK_SECRET.
        Pass the raw request body (bytes) and the X-Pathao-Signature header value.
        Returns True if signature matches.
        """
        if not self._webhook_secret:
            return False
        expected = hmac.new(
            self._webhook_secret.encode(),
            raw_body,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature_header)

    def parse_webhook(self, payload: dict) -> dict:
        """
        Normalise a Pathao webhook payload into Brain-friendly fields.
        Returns a dict ready to upsert into pathao_waybills.
        """
        order = payload.get('order') or payload
        return {
            'waybill_number': order.get('consignment_id'),
            'current_status': order.get('order_status'),
            'last_location':  order.get('location'),
            'failure_reason': order.get('reason'),
        }


pathao = PathaoClient()
