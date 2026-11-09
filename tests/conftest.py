"""Test setup: load the plugin folder as a package (the way Hermes does), give every test
a fresh temporary database, and replace Bitrefill with a fake. No network, no real money."""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def _load_plugin():
    if "errand_guard" in sys.modules:
        return sys.modules["errand_guard"]
    spec = importlib.util.spec_from_file_location("errand_guard", PLUGIN_DIR / "__init__.py",
                                                  submodule_search_locations=[str(PLUGIN_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["errand_guard"] = module
    spec.loader.exec_module(module)
    return module


eg = _load_plugin()
from errand_guard import approvals, hooks, settings, shop, store as store_mod  # noqa: E402

CONV = "conv-test-1"


@pytest.fixture(autouse=True)
def fresh_store(tmp_path):
    settings.set_test_values({})
    db = store_mod.open_file_store(tmp_path / "errands.db")
    store_mod.use_store(db)
    yield db
    store_mod.use_store(None)
    settings.set_test_values(None)


@pytest.fixture
def store(fresh_store):
    return fresh_store


def make_errand(store, **overrides):
    """An active (confirmed) errand: Japan eSIM 5 GB / 7 days, max 15 USD, to me@example.com."""
    now = time.time()
    fields = dict(conversation_id=CONV, created_at=now, expires_at=now + 3600, status="active",
                  kind="esim", item="Japan eSIM 5 GB / 7 days", product_id="", keywords=["japan"],
                  data_gb="5", duration_days=7, face_value=None, face_currency="",
                  recipient="me@example.com", recipient_type="email", merchants=["bitrefill"],
                  max_amount="15", currency="USD", quantity=1)
    fields.update(overrides)
    return store.create_errand(fields)


def make_purchase(**overrides):
    purchase = dict(kind="esim", merchant="bitrefill", product_id="bitrefill-esim-japan",
                    product_name="Japan eSIM", package="5GB, 7 Days", data_gb="5", duration_days=7,
                    face_value=None, face_currency="USD", quantity=1, recipient="me@example.com",
                    price="12.00", currency="USD")
    purchase.update(overrides)
    return purchase


def approve_last_ticket(store, choice="once", decided_by=None):
    """Simulate Hermes reporting a person's answer to the newest approval prompt."""
    row = store._one("SELECT ticket FROM approvals ORDER BY created_at DESC LIMIT 1")
    assert row, "no approval was requested"
    hooks.on_post_approval_response(pattern_key=approvals.PATTERN_PREFIX + row["ticket"], choice=choice,
                                    decided_by=decided_by)
    return row["ticket"]


class FakeBitrefill:
    """Stands in for the Bitrefill API with a small product catalogue."""

    PRODUCTS = {
        "bitrefill-esim-japan": {
            "id": "bitrefill-esim-japan", "name": "Japan eSIM", "country_code": "JP", "currency": "USD",
            "recipient_type": "none", "in_stock": True,
            "packages": [{"id": "jp-5", "value": "5GB, 7 Days", "price": 12.0},
                         {"id": "jp-20", "value": "20GB, 30 Days", "price": 35.0}]},
        "amazon-us": {
            "id": "amazon-us", "name": "Amazon.com US", "country_code": "US", "currency": "USD",
            "recipient_type": "none", "in_stock": True,
            "packages": [{"id": "amz-5", "value": "5", "price": 5.1}, {"id": "amz-25", "value": "25", "price": 25.4}]},
        "mtn-nigeria": {
            "id": "mtn-nigeria", "name": "MTN Nigeria", "country_code": "NG", "currency": "NGN",
            "recipient_type": "phone_number", "in_stock": True,
            "packages": [{"id": "mtn-1000", "value": "1000", "price": 1.2}]},
    }

    def __init__(self):
        self.invoices = {}
        self.paid = []
        self.pay_error = None
        self.created = []

    def search(self, query, **kw):
        return [p for p in self.PRODUCTS.values() if query.lower().split()[0] in p["name"].lower()]

    def esim_products(self, country="", **kw):
        return [p for p in self.PRODUCTS.values() if p["id"].startswith("bitrefill-esim")]

    def product(self, product_id, esim=False):
        return dict(self.PRODUCTS.get(product_id, {}))

    def check_phone(self, phone_number, operator=""):
        return {"operator_found": True, "data": self.PRODUCTS["mtn-nigeria"]}

    def create_invoice(self, products, esim=False, email="", send_email=False):
        line = products[0]
        product = self.PRODUCTS[line["product_id"]]
        pkg = next(p for p in product["packages"] if str(p["value"]) == str(line["value"]).rstrip("0").rstrip(".")
                   or str(p["value"]) == str(line["value"]))
        invoice_id = f"inv-{len(self.invoices) + 1}"
        invoice = {"id": invoice_id, "status": "not_delivered",
                   "payment": {"method": "balance", "currency": "USD", "status": "unpaid",
                               "price": round(pkg["price"] * line["quantity"], 2)},
                   "orders": [{"id": f"ord-{invoice_id}", "status": "created",
                               "product": {"id": product["id"], "name": product["name"], "value": str(pkg["value"]),
                                           "currency": product["currency"]}}]}
        self.invoices[invoice_id] = invoice
        self.created.append({"products": products, "esim": esim, "email": email})
        return invoice

    def pay_invoice(self, invoice_id, esim=False):
        if self.pay_error:
            raise self.pay_error
        invoice = self.invoices[invoice_id]
        invoice["payment"]["status"] = "paid"
        self.paid.append(invoice_id)
        return invoice

    def invoice(self, invoice_id, esim=False):
        return self.invoices[invoice_id]

    def order(self, order_id):
        return {"id": order_id, "status": "delivered", "redemption_info": "Your code: ABCD-1234-EFGH-5678",
                "pin": "145G7-NW5BE-RU3TW-IDEMO"}


@pytest.fixture
def fake_bitrefill():
    fake = FakeBitrefill()
    shop.set_client_factory(lambda: fake)
    yield fake
    shop.set_client_factory(shop.Client)
