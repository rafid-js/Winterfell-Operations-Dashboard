"""
Meta Marketing API client (Graph API v19.0).
Reads credentials from brain/.env:
    META_APP_ID, META_APP_SECRET, META_ACCESS_TOKEN, META_AD_ACCOUNT_ID
The ad account ID should be in the form act_123456789.
Import anywhere: from apis.meta import meta
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))

_BASE_URL = "https://graph.facebook.com/v19.0"

_INSIGHT_FIELDS = ','.join([
    'spend',
    'impressions',
    'clicks',
    'ctr',
    'cpm',
    'cpc',
    'campaign_id',
    'campaign_name',
    'adset_id',
    'adset_name',
    'date_start',
    'date_stop',
])


class MetaClient:
    def __init__(self):
        self._access_token  = None
        self._ad_account_id = None
        self._app_id        = None
        self._app_secret    = None

    def _ensure_auth(self):
        if self._access_token is None:
            access_token  = os.getenv('META_ACCESS_TOKEN')
            ad_account_id = os.getenv('META_AD_ACCOUNT_ID')
            if not access_token or not ad_account_id:
                raise RuntimeError("META_ACCESS_TOKEN / META_AD_ACCOUNT_ID not set in environment")
            self._access_token  = access_token
            self._ad_account_id = ad_account_id
            self._app_id        = os.getenv('META_APP_ID')
            self._app_secret    = os.getenv('META_APP_SECRET')

    def _get(self, path: str, params: dict = None) -> dict:
        self._ensure_auth()
        p = {'access_token': self._access_token}
        if params:
            p.update(params)
        r = requests.get(
            f"{_BASE_URL}{path}",
            params=p,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    # ── Insights ──────────────────────────────────────────────────────────────

    def get_insights(self, since: str, until: str,
                     level: str = 'adset') -> list:
        """Fetch ad insights for the account, returning daily rows.

        Args:
            since: Start date in YYYY-MM-DD format.
            until: End date in YYYY-MM-DD format.
            level: Breakdown level — 'ad', 'adset', 'campaign', or 'account'.

        Returns:
            List of insight dicts, one per (level entity, day).
        """
        params = {
            'fields':         _INSIGHT_FIELDS,
            'level':          level,
            'time_increment': 1,
            'time_range':     f'{{"since":"{since}","until":"{until}"}}',
            'limit':          500,
        }
        self._ensure_auth()
        path    = f"/{self._ad_account_id}/insights"
        results = []

        while True:
            data  = self._get(path, params)
            rows  = data.get('data', [])
            results.extend(rows)

            # Follow cursor-based pagination
            next_url = (data.get('paging') or {}).get('next')
            if not next_url:
                break

            # Extract the 'after' cursor from the next URL and use it
            after = _extract_after_cursor(next_url)
            if not after:
                break
            params['after'] = after

        return results

    # ── Account ───────────────────────────────────────────────────────────────

    def get_account_info(self) -> dict:
        """Return basic account info: name and currency."""
        self._ensure_auth()
        return self._get(
            f"/{self._ad_account_id}",
            {'fields': 'name,currency'},
        )


def _extract_after_cursor(next_url: str) -> str | None:
    """Pull the 'after' cursor value out of a Graph API paging.next URL."""
    try:
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(next_url).query)
        cursors = qs.get('after')
        return cursors[0] if cursors else None
    except Exception:
        return None


meta = MetaClient()
