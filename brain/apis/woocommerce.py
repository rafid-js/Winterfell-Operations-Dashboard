"""
WooCommerce REST API client (WC/v3).
Reads credentials from brain/.env: WC_URL, WC_CONSUMER_KEY, WC_CONSUMER_SECRET.
Import anywhere: from apis.woocommerce import wc
"""
import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()


class WooCommerceClient:
    def __init__(self):
        self.BASE_URL = None
        self._auth    = None

    def _ensure_auth(self):
        if self._auth is None:
            wc_url = os.getenv('WC_URL')
            key    = os.getenv('WC_CONSUMER_KEY')
            secret = os.getenv('WC_CONSUMER_SECRET')
            if not wc_url or not key or not secret:
                raise RuntimeError("WC_URL / WC_CONSUMER_KEY / WC_CONSUMER_SECRET not set in environment")
            self.BASE_URL = f"{wc_url.rstrip('/')}/wp-json/wc/v3"
            self._auth    = (key, secret)

    def _get(self, path: str, params: dict = None) -> requests.Response:
        self._ensure_auth()
        r = requests.get(
            f"{self.BASE_URL}{path}",
            auth=self._auth,
            params=params or {},
            timeout=30,
        )
        r.raise_for_status()
        return r

    # ── Orders ────────────────────────────────────────────────────────────────

    # Only fetch the fields we actually use — reduces response size and WC query cost
    _ORDER_FIELDS = (
        'id,number,status,billing,line_items,'
        'date_created,date_modified,'
        'total,shipping_total,discount_total,customer_id'
    )

    def _fetch_orders_page(self, page: int, per_page: int,
                           modified_after: str = None) -> tuple[int, list]:
        params = {
            'status':   'any',
            'page':     page,
            'per_page': per_page,
            'orderby':  'id',
            'order':    'asc',
            '_fields':  self._ORDER_FIELDS,
        }
        if modified_after:
            params['modified_after'] = modified_after
        r = self._get('/orders', params)
        return page, r.json()

    def iter_orders(self, modified_after: str = None, per_page: int = 100,
                    workers: int = 5):
        """Yield every order using parallel page fetching.

        Fetches `workers` pages simultaneously, yields in order.
        Falls back to sequential on modified_after (incremental syncs are small).
        """
        # Page 1 first to discover total pages
        _, first_page = self._fetch_orders_page(1, per_page, modified_after)
        if not first_page:
            return
        yield from first_page
        if len(first_page) < per_page:
            return

        # Parallel fetch for remaining pages
        page = 2
        while True:
            pages_to_fetch = list(range(page, page + workers))
            results = {}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {
                    pool.submit(self._fetch_orders_page, p, per_page, modified_after): p
                    for p in pages_to_fetch
                }
                for future in as_completed(futures):
                    p, data = future.result()
                    results[p] = data

            # Yield in order, stop early if any page is short
            done = False
            for p in pages_to_fetch:
                data = results.get(p, [])
                if not data:
                    done = True
                    break
                yield from data
                if len(data) < per_page:
                    done = True
                    break

            if done:
                break
            page += workers

    def get_order(self, order_id: int | str) -> dict:
        return self._get(f"/orders/{order_id}").json()

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
        return self._get('/products', params).json()

    def get_variations(self, product_id: int | str) -> list:
        """Return all variations for a variable product, auto-paginating."""
        variations = []
        page = 1
        per_page = 100
        while True:
            results = self._get(
                f"/products/{product_id}/variations",
                {'page': page, 'per_page': per_page},
            ).json()
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
        return self._get('/customers', params).json()

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
