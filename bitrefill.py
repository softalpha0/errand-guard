"""Minimal Bitrefill API v2 client (standard library only, no extra dependencies).

Auth: Personal API key as a Bearer token, read through Hermes's secret helper.
Docs: https://docs.bitrefill.com (API reference). Endpoints used:
  GET  /products/search?q=     GET /products/{id}      GET /products/esims?country=
  GET  /products/esims/{id}    GET /check_phone_number?phone_number=
  POST /invoices               POST /invoices/{id}/pay  GET /invoices/{id}
  POST /esims                  POST /esims/invoice/{id}/pay  GET /esims/invoice/{id}
  GET  /orders/{id}            GET /esims/{id}          GET /ping
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

from . import settings

TIMEOUT_SECONDS = 25
USER_AGENT = "errand-guard-hermes-plugin/0.1"


class BitrefillError(Exception):
    def __init__(self, message: str, *, status: Optional[int] = None, code: str = "",
                 uncertain: bool = False):
        super().__init__(message)
        self.status = status
        self.code = code
        # True when we cannot know whether the request took effect (timeout, connection drop).
        self.uncertain = uncertain


class Client:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None, opener=None):
        self.api_key = api_key if api_key is not None else settings.bitrefill_api_key()
        self.base_url = (base_url or settings.bitrefill_base_url()).rstrip("/")
        self._open = opener or urllib.request.urlopen
        if not self.base_url.startswith("https://"):
            raise BitrefillError("BITREFILL_BASE_URL must start with https://")
        if not self.api_key:
            raise BitrefillError("BITREFILL_API_KEY is not set. Add it to the .env file in your Hermes home folder (see .env.example).")

    # ---- transport ------------------------------------------------------------
    def _request(self, method: str, path: str, *, query: Optional[dict] = None,
                 body: Optional[dict] = None) -> dict:
        url = self.base_url + path
        if query:
            clean = {k: ("true" if v is True else "false" if v is False else v)
                     for k, v in query.items() if v not in (None, "")}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self.api_key}", "Accept": "application/json",
            "Content-Type": "application/json", "User-Agent": USER_AGENT})
        try:
            with self._open(request, timeout=TIMEOUT_SECONDS) as response:
                payload = response.read().decode("utf-8") or "{}"
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", "replace") if hasattr(exc, "read") else ""
            message, code = _error_text(text, exc.code)
            raise BitrefillError(message, status=exc.code, code=code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # A write (POST) that timed out may or may not have happened.
            raise BitrefillError(f"Could not reach Bitrefill: {exc}", uncertain=method != "GET") from None
        try:
            parsed = json.loads(payload)
        except ValueError:
            raise BitrefillError("Bitrefill returned something that is not JSON",
                                 uncertain=method != "GET") from None
        return parsed if isinstance(parsed, dict) else {"data": parsed}

    # ---- endpoints ------------------------------------------------------------
    def ping(self) -> dict:
        return self._request("GET", "/ping")

    def balance(self) -> dict:
        return self._request("GET", "/accounts/balance")

    def search(self, query: str, *, limit: int = 10, include_test_products: bool = False) -> list[dict]:
        out = self._request("GET", "/products/search",
                            query={"q": query[:100], "limit": limit, "include_test_products": include_test_products})
        return _as_list(out.get("data"))

    def esim_products(self, country: str = "", *, limit: int = 20, include_test_products: bool = False) -> list[dict]:
        out = self._request("GET", "/products/esims", query={
            "country": country, "limit": limit, "include_test_products": include_test_products})
        return _as_list(out.get("data"))

    def product(self, product_id: str, *, esim: bool = False) -> dict:
        path = ("/products/esims/" if esim else "/products/") + urllib.parse.quote(product_id, safe="")
        return self._request("GET", path).get("data") or {}

    def check_phone(self, phone_number: str, operator: str = "") -> dict:
        return self._request("GET", "/check_phone_number",
                             query={"phone_number": phone_number, "operator": operator})

    def create_invoice(self, products: list[dict], *, esim: bool = False, email: str = "",
                       send_email: bool = False) -> dict:
        body: dict[str, Any] = {"products": products, "payment_method": "balance", "auto_pay": False}
        if email:
            body["email"] = email
            body["send_email"] = bool(send_email)
        return self._request("POST", "/esims" if esim else "/invoices", body=body).get("data") or {}

    def pay_invoice(self, invoice_id: str, *, esim: bool = False) -> dict:
        iid = urllib.parse.quote(invoice_id, safe="")
        path = f"/esims/invoice/{iid}/pay" if esim else f"/invoices/{iid}/pay"
        return self._request("POST", path, body={}).get("data") or {}

    def invoice(self, invoice_id: str, *, esim: bool = False) -> dict:
        iid = urllib.parse.quote(invoice_id, safe="")
        return self._request("GET", f"/esims/invoice/{iid}" if esim else f"/invoices/{iid}").get("data") or {}

    def order(self, order_id: str) -> dict:
        return self._request("GET", "/orders/" + urllib.parse.quote(order_id, safe="")).get("data") or {}

    def esim(self, esim_id: str) -> dict:
        return self._request("GET", "/esims/" + urllib.parse.quote(str(esim_id), safe="")).get("data") or {}


def _as_list(value: Any) -> list[dict]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict):
        return [value]
    return []


def _error_text(text: str, status: int) -> tuple[str, str]:
    try:
        body = json.loads(text)
    except ValueError:
        body = {}
    if isinstance(body, dict):
        message = body.get("message") or body.get("error") or ""
        code = str(body.get("error_code") or "")
    else:
        message, code = "", ""
    friendly = {401: "Bitrefill rejected the API key (401). Check BITREFILL_API_KEY.",
                403: "Bitrefill refused (403): often not enough balance.",
                404: "Bitrefill could not find that (404).",
                429: "Bitrefill rate limit hit (429). Wait a minute and retry."}.get(status, "")
    return (f"{friendly} {message}".strip() if friendly else f"Bitrefill error {status}: {message or text[:200]}"), code


def packages(product: dict) -> list[dict]:
    """A product's packages as a list of {id, value, price} (the API shows one or a list)."""
    raw = product.get("packages")
    if isinstance(raw, dict):
        raw = [raw]
    return [p for p in raw or [] if isinstance(p, dict)]
