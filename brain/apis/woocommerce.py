"""
WooCommerce REST API client (WC/v3).
Reads credentials from brain/.env: WC_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET.
Import anywhere: from apis.woocommerce import wc
"""
import os
import requests
from dotenv import load_dotenv

load_dotenv()


class WooCommerceClient:
    def __init__(self):
        wc_url = os.getenv('WC_URL')
        key    = os.getenv('WC_CONSUMER_KEY')
        secret = os.getenv('WC_CONSUMER_SECRET')
        if not wc_url:
            raise RuntimeError("WC_URL not set in brain/.env")
        if not key or not secret:
            raise RuntimeError("WC_CONSUMER_KEY / WC_CONSUMER_SECRET not set in brain/.env")
        self.BASE_URL = f"{wc_url.rstrip('/')}/wp-json/wc/v3"
        self._auth    = (key, secret)

    def _get(self, path: str, params: dict = None) -> list | dict:
        r = requests.get(
            f"{self.BASE_URL}{path}",
            auth=self._auth,
            params=params or {},
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    # ── Orders ────────────────────────────────────────────────────────────────

    def list_orders(self, page: int = 1, per_page: int = 100,
                    modified_after: str = None) -> list:
        """Return one page of orders (status=any).

        Args:
            page: 1-based page number.
            per_page: Records per page (max 100).
            modified_after: ISO 8601 datetime string; only orders modified
                            after this date are returned.
        """
        params = {'status': 'any', 'page': page, 'per_page': per_page}
        if modified_after:
            params['modified_after'] = modified_after
        return self._get('/orders', params)

    def iter_orders(self, modified_after: str = None, per_page: int = 100):
        """Yield every order, auto-paginating.

        Stops when a page returns fewer records than per_page.
        """
        page = 1
        while True:
            results = self.list_orders(page=page, per_page=per_page,
                                       modified_after=modified_after)
            if not results:
                break
            yield from results
            if len(results) < per_page:
                break
            page += 1

    def get_order(self, order_id: int | str) -> dict:
        """Fetch a single order by its WooCommerce order ID."""
        return self._get(f"/orders/{order_id}")

    # ── Products ──────────────────────────────────────────────────────────────

    def list_products(self, page: int = 1, per_page: int = 100,
                      modified_after: str = None) -> list:
        """Return one page of products.

        Args:
            page: 1-based page number.
            per_page: Records per page (max 100).
            modified_after: ISO 8601 datetime string filter.
        """
        params = {'page': page, 'per_page': per_page}
        if modified_after:
            params['modified_after'] = modified_after
        return self._get('/products', params)

    def get_variations(self, product_id: int | str) -> list:
        """Return all variations for a variable product, auto-paginating."""
        variations = []
        page = 1
        per_page = 100
        while True:
            results = self._get(
                f"/products/{product_id}/variations",
                {'page': page, 'per_page': per_page},
            )
            if not results:
                break
            variations.extend(results)
            if len(results) < per_page:
                break
            page += 1
        return variations

    def iter_products(self, modified_after: str = None, per_page: int = 100):
        """Yield every product, auto-paginating.

        For variable products (type='variable') the variations sub-resource is
        fetched and attached as product['variations_detail'].
        """
        page = 1
        while True:
            results = self.list_products(page=page, per_page=per_page,
                                         modified_after=modified_after)
            if not results:
                break
            for product in results:
                if product.get('type') == 'variable':
                    product['variations_detail'] = self.get_variations(product['id'])
                yield product
            if len(results) < per_page:
                break
            page += 1

    # ── Customers ─────────────────────────────────────────────────────────────

    def list_customers(self, page: int = 1, per_page: int = 100,
                       modified_after: str = None) -> list:
        """Return one page of customers.

        Args:
            page: 1-based page number.
            per_page: Records per page (max 100).
            modified_after: ISO 8601 datetime string filter.
        """
        params = {'page': page, 'per_page': per_page}
        if modified_after:
            params['modified_after'] = modified_after
        return self._get('/customers', params)

    def iter_customers(self, modified_after: str = None, per_page: int = 100):
        """Yield every customer, auto-paginating.

        Stops when a page returns fewer records than per_page.
        """
        page = 1
        while True:
            results = self.list_customers(page=page, per_page=per_page,
                                          modified_after=modified_after)
            if not results:
                break
            yield from results
            if len(results) < per_page:
                break
            page += 1


wc = WooCommerceClient()
