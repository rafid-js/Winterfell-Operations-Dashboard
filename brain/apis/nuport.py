"""
Nuport OMS API client — complete endpoint coverage based on official API docs.
Import anywhere: from apis.nuport import nuport
"""
import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()


class NuportClient:
    BASE_URL = "https://api.nuport.io/integration"

    def __init__(self):
        key = os.getenv('NUPORT_API_KEY')
        if not key:
            raise RuntimeError("NUPORT_API_KEY not set in brain/.env")
        self._headers = {'Authorization': key}

    def _get(self, path: str, params: dict = None) -> dict | list:
        r = requests.get(
            f"{self.BASE_URL}{path}",
            headers=self._headers,
            params=params or {},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    # ── Orders ────────────────────────────────────────────────────────────────
    # NOTE: Nuport has NO list-orders endpoint.
    # Orders are fetched one at a time by their internal SO number (e.g. SO-0036).

    def get_order(self, so_number: str) -> dict:
        """Fetch a single order by its SO number (internalId)."""
        return self._get(f"/orders/{so_number}")

    # ── Products ──────────────────────────────────────────────────────────────
    # Paginated. page starts at 0. pageSize default 20.

    def list_products(self, page: int = 0, page_size: int = 50,
                      search_term: str = None) -> dict:
        params = {'page': page, 'pageSize': page_size}
        if search_term:
            params['searchTerm'] = search_term
        return self._get('/products', params)

    def iter_all_products(self, delay: float = 0.2):
        """Yield every product, auto-paginating."""
        page = 0
        while True:
            data = self.list_products(page=page, page_size=50)
            results = data.get('results', [])
            if not results:
                break
            yield from results
            page_size = data.get('pageSize', 50)
            total = data.get('count', 0)
            if (page + 1) * page_size >= total:
                break
            page += 1
            time.sleep(delay)

    # ── Inventory ─────────────────────────────────────────────────────────────
    # page=0 is first page; page=-1 returns ALL records at once.
    # updatedFrom / updatedTo are ISO 8601 strings.

    def list_inventory(self, page: int = 0, page_size: int = 50,
                       updated_from: str = None, updated_to: str = None,
                       search_term: str = None, location_id: str = None) -> dict:
        params = {'page': page, 'pageSize': page_size}
        if updated_from:
            params['updatedFrom'] = updated_from
        if updated_to:
            params['updatedTo'] = updated_to
        if search_term:
            params['searchTerm'] = search_term
        if location_id:
            params['locationId'] = location_id
        return self._get('/inventory', params)

    def get_all_inventory(self, updated_from: str = None) -> list:
        """Return all inventory records in one call using page=-1."""
        params = {'page': -1}
        if updated_from:
            params['updatedFrom'] = updated_from
        data = self._get('/inventory', params)
        return data.get('results', [])

    def iter_all_inventory(self, delay: float = 0.2, updated_from: str = None):
        """Yield every inventory item, auto-paginating."""
        page = 0
        while True:
            data = self.list_inventory(page=page, page_size=50,
                                       updated_from=updated_from)
            results = data.get('results', [])
            if not results:
                break
            yield from results
            page_size = data.get('pageSize', 50)
            total = data.get('count', 0)
            if (page + 1) * page_size >= total:
                break
            page += 1
            time.sleep(delay)

    # ── Reference data ────────────────────────────────────────────────────────

    def get_order_sources(self) -> list:
        return self._get('/order-sources')

    def get_users(self) -> list:
        return self._get('/users')

    def list_pickup_locations(self, page: int = 0) -> dict:
        return self._get('/pickup-locations', {'page': page})

    def list_delivery_partners(self, page: int = 0) -> dict:
        return self._get('/delivery-partners', {'page': page})


nuport = NuportClient()
