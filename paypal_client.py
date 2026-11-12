"""PayPal Invoicing API v2 client for Sandbox environment.

Handles OAuth2 client_credentials token caching, invoice creation, sending,
fetching, listing, and payment reminder notifications.
Never uses live URLs or exposes secrets in exceptions or logs.
"""

import time
import logging
from typing import Any, Dict, List, Optional
import requests
from requests.auth import HTTPBasicAuth

import config

logger = logging.getLogger("billpilot.paypal")

# Enforce sandbox base URL
SANDBOX_BASE_URL = "https://api-m.sandbox.paypal.com"
REQUEST_TIMEOUT = 15.0  # seconds

# In-memory token cache
_cached_token: Optional[str] = None
_token_expires_at: float = 0.0


class PayPalError(Exception):
    """Exception raised for errors during PayPal API interactions.
    
    Contains a sanitized, human-readable message, the HTTP status code,
    and PayPal's error detail list (without secrets or auth headers).
    """

    def __init__(self, message: str, status_code: Optional[int] = None, details: Optional[Any] = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or []

    def __str__(self) -> str:
        if self.status_code:
            return f"[HTTP {self.status_code}] {self.message}"
        return self.message


def _parse_error_response(response: requests.Response) -> PayPalError:
    """Extract a user-readable error message and details from a PayPal error response."""
    status_code = response.status_code
    try:
        data = response.json()
    except Exception:
        return PayPalError(f"PayPal request failed with status {status_code}", status_code=status_code)

    message = data.get("message") or data.get("error_description") or data.get("name") or "PayPal API error"
    details = data.get("details", [])
    
    # If detailed validation errors exist, append summary for clarity
    if isinstance(details, list) and len(details) > 0:
        first_detail = details[0]
        if isinstance(first_detail, dict):
            issue = first_detail.get("issue") or first_detail.get("description")
            field = first_detail.get("field")
            if issue and field:
                message = f"{message}: {field} - {issue}"
            elif issue:
                message = f"{message}: {issue}"

    return PayPalError(message, status_code=status_code, details=details)


def get_access_token(force_refresh: bool = False) -> str:
    """Fetch an OAuth2 access token using client credentials.
    
    Caches the token in memory until 60 seconds before expiration.
    """
    global _cached_token, _token_expires_at

    client_id = config.PAYPAL_CLIENT_ID.strip()
    client_secret = config.PAYPAL_CLIENT_SECRET.strip()

    if not client_id or not client_secret:
        raise PayPalError("PayPal client credentials are not configured in .env", status_code=401)

    now = time.time()
    if not force_refresh and _cached_token and now < (_token_expires_at - 60):
        return _cached_token

    url = f"{SANDBOX_BASE_URL}/v1/oauth2/token"
    headers = {"Accept": "application/json", "Accept-Language": "en_US"}
    data = {"grant_type": "client_credentials"}

    try:
        response = requests.post(
            url,
            auth=HTTPBasicAuth(client_id, client_secret),
            headers=headers,
            data=data,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.error("Failed to connect to PayPal token endpoint: %s", str(e))
        raise PayPalError(f"Unable to connect to PayPal sandbox: {e}", status_code=503)

    if response.status_code != 200:
        raise _parse_error_response(response)

    token_data = response.json()
    _cached_token = token_data.get("access_token")
    expires_in = int(token_data.get("expires_in", 3600))
    _token_expires_at = now + expires_in

    return _cached_token


def _get_auth_headers() -> Dict[str, str]:
    """Return common headers for authenticated PayPal API calls."""
    token = get_access_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _extract_payer_url_from_links(links: List[Dict[str, Any]]) -> Optional[str]:
    """Find the payer-view link from a list of HATEOAS links."""
    if not isinstance(links, list):
        return None
    for link in links:
        if isinstance(link, dict) and link.get("rel") in ("payer-view", "recipient-view"):
            return link.get("href")
    return None


def create_invoice(data: Dict[str, Any]) -> str:
    """Create a draft invoice in PayPal Invoicing API v2.
    
    Accepts validated dictionary:
      - client_name: str
      - client_email: str
      - items: list of dicts (name, quantity, unit_price)
      - currency: str (e.g. USD)
      - due_date: ISO date str (YYYY-MM-DD) or None
      - note: str or None
      - invoice_number: optional str (if omitted, PayPal auto-generates)
      
    Returns the created PayPal invoice ID (e.g., 'INV2-XXXX-XXXX-XXXX-XXXX').
    """
    currency = data.get("currency", "USD").upper()
    due_date = data.get("due_date")
    note = data.get("note")
    invoice_number = data.get("invoice_number")

    # Build detail block
    detail: Dict[str, Any] = {
        "currency_code": currency,
        "invoice_date": time.strftime("%Y-%m-%d"),
    }
    if invoice_number:
        detail["invoice_number"] = invoice_number
    if due_date:
        detail["payment_term"] = {
            "term_type": "DUE_ON_DATE_SPECIFIED",
            "due_date": due_date,
        }
    else:
        detail["payment_term"] = {"term_type": "DUE_ON_RECEIPT"}
    if note:
        detail["note"] = note

    # Build primary recipients
    client_name = data.get("client_name", "Valued Client")
    name_parts = client_name.strip().split(maxsplit=1)
    given_name = name_parts[0] if name_parts else "Valued"
    surname = name_parts[1] if len(name_parts) > 1 else ""

    recipient_info: Dict[str, Any] = {
        "name": {"given_name": given_name}
    }
    if surname:
        recipient_info["name"]["surname"] = surname
    if data.get("client_email"):
        recipient_info["email_address"] = data["client_email"].strip()

    primary_recipients = [{"billing_info": recipient_info}]

    # Build line items
    items_input = data.get("items", [])
    if not items_input:
        raise PayPalError("At least one item is required to create an invoice.", status_code=400)

    items = []
    for it in items_input:
        qty = it.get("quantity", 1)
        price = it.get("unit_price", 0.0)
        items.append({
            "name": it.get("name", "Item"),
            "quantity": str(qty),
            "unit_amount": {
                "currency_code": currency,
                "value": f"{float(price):.2f}",
            },
        })

    payload = {
        "detail": detail,
        "primary_recipients": primary_recipients,
        "items": items,
    }

    url = f"{SANDBOX_BASE_URL}/v2/invoicing/invoices"
    try:
        response = requests.post(
            url,
            headers=_get_auth_headers(),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.error("Failed to connect to PayPal create invoice: %s", str(e))
        raise PayPalError(f"Network error while creating invoice: {e}", status_code=503)

    if response.status_code not in (200, 201):
        raise _parse_error_response(response)

    res_data = response.json()
    invoice_id = res_data.get("id")
    if not invoice_id:
        href = res_data.get("href", "")
        if "/invoices/" in href:
            invoice_id = href.split("/invoices/")[-1]

    if not invoice_id:
        raise PayPalError("PayPal created invoice but returned no invoice ID.", status_code=500)

    return invoice_id


def send_invoice(invoice_id: str) -> Optional[str]:
    """Send a draft invoice to the client via PayPal.
    
    Returns the payer view URL if available.
    """
    url = f"{SANDBOX_BASE_URL}/v2/invoicing/invoices/{invoice_id}/send"
    payload = {
        "send_to_recipient": True,
        "send_to_invoicer": False,
    }

    try:
        response = requests.post(
            url,
            headers=_get_auth_headers(),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.error("Failed to connect to PayPal send invoice: %s", str(e))
        raise PayPalError(f"Network error while sending invoice: {e}", status_code=503)

    if response.status_code not in (200, 202, 204):
        raise _parse_error_response(response)

    payer_url: Optional[str] = None
    if response.status_code != 204:
        try:
            res_data = response.json()
            if isinstance(res_data, dict):
                if res_data.get("rel") in ("payer-view", "recipient-view"):
                    payer_url = res_data.get("href")
                elif "links" in res_data:
                    payer_url = _extract_payer_url_from_links(res_data.get("links", []))
        except Exception:
            pass

    # If send response did not provide the payer URL, read the invoice details
    if not payer_url:
        try:
            inv = get_invoice(invoice_id)
            payer_url = inv.get("payer_url")
        except Exception:
            pass

    return payer_url


def get_invoice(invoice_id: str) -> Dict[str, Any]:
    """Retrieve an invoice by ID and return a simplified dictionary.
    
    Returns:
      {
        "id": "INV2-...",
        "invoice_number": "...",
        "raw_status": "SENT",
        "due_date": "YYYY-MM-DD",
        "total": 500.0,
        "currency": "USD",
        "payer_url": "https://..."
      }
    """
    url = f"{SANDBOX_BASE_URL}/v2/invoicing/invoices/{invoice_id}"
    try:
        response = requests.get(
            url,
            headers=_get_auth_headers(),
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.error("Failed to connect to PayPal get invoice: %s", str(e))
        raise PayPalError(f"Network error while retrieving invoice: {e}", status_code=503)

    if response.status_code != 200:
        raise _parse_error_response(response)

    data = response.json()
    detail = data.get("detail", {})
    amount = data.get("amount", {})
    metadata = detail.get("metadata", {})

    try:
        total = float(amount.get("value", 0.0))
    except (ValueError, TypeError):
        total = 0.0

    payer_url = metadata.get("recipient_view_url") or _extract_payer_url_from_links(data.get("links", []))
    due_date = detail.get("payment_term", {}).get("due_date")

    return {
        "id": data.get("id", invoice_id),
        "invoice_number": detail.get("invoice_number", ""),
        "raw_status": data.get("status", ""),
        "due_date": due_date,
        "total": total,
        "currency": amount.get("currency_code") or detail.get("currency_code", "USD"),
        "payer_url": payer_url,
    }


def list_invoices(page: int = 1, page_size: int = 20) -> List[Dict[str, Any]]:
    """List or search invoices for the merchant via the search endpoint."""
    url = f"{SANDBOX_BASE_URL}/v2/invoicing/search-invoices?page={page}&page_size={page_size}&total_required=true"
    try:
        response = requests.post(
            url,
            headers=_get_auth_headers(),
            json={},
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.error("Failed to connect to PayPal search invoices: %s", str(e))
        raise PayPalError(f"Network error while searching invoices: {e}", status_code=503)

    if response.status_code != 200:
        raise _parse_error_response(response)

    data = response.json()
    items = data.get("items", [])
    simplified_items = []

    for item in items:
        detail = item.get("detail", {})
        amount = item.get("amount", {})
        metadata = detail.get("metadata", {})

        try:
            total = float(amount.get("value", 0.0))
        except (ValueError, TypeError):
            total = 0.0

        payer_url = metadata.get("recipient_view_url") or _extract_payer_url_from_links(item.get("links", []))
        due_date = detail.get("payment_term", {}).get("due_date")

        simplified_items.append({
            "id": item.get("id"),
            "invoice_number": detail.get("invoice_number", ""),
            "raw_status": item.get("status", ""),
            "due_date": due_date,
            "total": total,
            "currency": amount.get("currency_code") or detail.get("currency_code", "USD"),
            "payer_url": payer_url,
        })

    return simplified_items


def remind_invoice(invoice_id: str, subject: str, note: str) -> bool:
    """Send an invoice reminder through PayPal.
    
    Rate limit on PayPal side: up to 2 reminders per day per invoice.
    """
    url = f"{SANDBOX_BASE_URL}/v2/invoicing/invoices/{invoice_id}/remind"
    payload = {
        "subject": subject,
        "note": note,
        "send_to_recipient": True,
        "send_to_invoicer": False,
    }

    try:
        response = requests.post(
            url,
            headers=_get_auth_headers(),
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException as e:
        logger.error("Failed to connect to PayPal remind invoice: %s", str(e))
        raise PayPalError(f"Network error while sending reminder: {e}", status_code=503)

    if response.status_code not in (200, 202, 204):
        raise _parse_error_response(response)

    return True
