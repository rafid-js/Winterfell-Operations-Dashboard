"""
Nuport OMS API client.
- Loads NUPORT_API_KEY once from brain/.env
- Import anywhere: from apis.nuport import nuport
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

    def get_order(self, order_id: str) -> dict:
        r = requests.get(
            f"{self.BASE_URL}/orders/{order_id}",
            headers=self._headers,
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def list_orders(self, page: int = 1, limit: int = 100, **filters) -> dict:
        params = {'page': page, 'limit': limit, **filters}
        r = requests.get(
            f"{self.BASE_URL}/orders",
            headers=self._headers,
            params=params,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def iter_all_orders(self, delay: float = 0.3, **filters):
        """Paginate through all orders, yielding one order dict at a time."""
        page = 1
        while True:
            data = self.list_orders(page=page, **filters)

            # Nuport may return a list directly or wrap it
            if isinstance(data, list):
                orders = data
            else:
                orders = (
                    data.get('data')
                    or data.get('orders')
                    or data.get('results')
                    or []
                )

            if not orders:
                break

            yield from orders

            # Stop if we got fewer than a full page
            if len(orders) < 100:
                break

            page += 1
            time.sleep(delay)


nuport = NuportClient()
