"""PayPal Invoicing API v2 smoke test script.

Validates sandbox connectivity, OAuth2 token fetching, invoice creation,
sending, reading, listing, and experiments with INR currency and past due dates.
Never prints secrets or raw tokens.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import paypal_client
from services import map_status


def run_smoke_test():
    print("=" * 65)
    print("BillPilot PayPal Sandbox Smoke Test (Phase 1)")
    print("=" * 65)

    # 0. Check credentials
    if not config.is_paypal_configured():
        print("[FAIL] PayPal credentials not found in .env.")
        print("Please configure PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env.")
        sys.exit(1)

    print("[INFO] Base URL:", paypal_client.SANDBOX_BASE_URL)
    configured_email = config.SANDBOX_PAYER_EMAIL.strip()
    # Default to an international test buyer if none provided
    payer_email = configured_email or "us-buyer@example.com"
    print(f"[INFO] Primary recipient email: {payer_email}")

    # 1. Fetch OAuth token
    print("\n--- Step 1: OAuth2 Token Retrieval ---")
    try:
        token = paypal_client.get_access_token()
        token_preview = f"{token[:4]}...{token[-4:]}" if len(token) > 8 else "***"
        print(f"[PASS] Successfully retrieved access token ({token_preview})")
    except paypal_client.PayPalError as e:
        print(f"[FAIL] Token retrieval failed: {e}")
        if e.details:
            print(f"       Details: {e.details}")
        sys.exit(1)

    # 2. Create standard USD invoice
    print("\n--- Step 2: Create Standard USD Draft Invoice ---")
    today = date.today()
    due_date = (today + timedelta(days=7)).isoformat()
    invoice_payload = {
        "client_name": "Smoke Test Client",
        "client_email": payer_email,
        "items": [
            {"name": "Consulting Services (Smoke Test)", "quantity": 1, "unit_price": 50.00}
        ],
        "currency": "USD",
        "due_date": due_date,
        "note": "Automated smoke test invoice from BillPilot.",
    }

    try:
        inv_id = paypal_client.create_invoice(invoice_payload)
        print(f"[PASS] Invoice created successfully! PayPal ID: {inv_id}")
    except paypal_client.PayPalError as e:
        print(f"[FAIL] Invoice creation failed: {e}")
        if e.details:
            print(f"       Details: {e.details}")
        sys.exit(1)

    # 3. Send the invoice
    print(f"\n--- Step 3: Send Invoice ({inv_id}) ---")
    payer_url = None
    try:
        payer_url = paypal_client.send_invoice(inv_id)
        print(f"[PASS] Invoice sent successfully!")
        if payer_url:
            print(f"       Payer URL: {payer_url}")
    except paypal_client.PayPalError as e:
        # Check if failed due to Indian account domestic limitation
        if "INR_FOREIGN_CURRENCY_BLOCKED" in str(e):
            print(f"[WARN] Recipient {payer_email} triggered INR_FOREIGN_CURRENCY_BLOCKED.")
            print("       PayPal sandbox merchant account is registered in India, which prohibits")
            print("       invoicing India-registered personal accounts due to RBI regulations.")
            print("       Retrying with an international (US) buyer address...")
            
            # Create & send to an international sandbox recipient
            intl_payload = dict(invoice_payload)
            intl_payload["client_name"] = "US Sandbox Buyer"
            intl_payload["client_email"] = "us-sandbox-buyer@example.com"
            inv_id = paypal_client.create_invoice(intl_payload)
            print(f"       Created new international draft: {inv_id}")
            payer_url = paypal_client.send_invoice(inv_id)
            print(f"[PASS] Invoice sent successfully to international recipient!")
            if payer_url:
                print(f"       Payer URL: {payer_url}")
        else:
            print(f"[FAIL] Sending invoice failed: {e}")
            if e.details:
                print(f"       Details: {e.details}")
            sys.exit(1)

    # 4. Fetch invoice details
    print(f"\n--- Step 4: Fetch Invoice Details ({inv_id}) ---")
    try:
        inv_details = paypal_client.get_invoice(inv_id)
        status_raw = inv_details.get("raw_status")
        mapped = map_status(status_raw, inv_details.get("due_date"), today=today.isoformat())
        print(f"[PASS] Retrieved invoice details:")
        print(f"       Invoice Number : {inv_details.get('invoice_number')}")
        print(f"       Total Amount   : {inv_details.get('total')} {inv_details.get('currency')}")
        print(f"       Due Date       : {inv_details.get('due_date')}")
        print(f"       Raw Status     : {status_raw}")
        print(f"       Mapped Status  : {mapped}")
        print(f"       Payer URL      : {inv_details.get('payer_url')}")
    except paypal_client.PayPalError as e:
        print(f"[FAIL] Fetching invoice details failed: {e}")

    # 5. List invoices via search
    print("\n--- Step 5: List Invoices (Search Endpoint) ---")
    try:
        invoices_list = paypal_client.list_invoices(page=1, page_size=5)
        print(f"[PASS] Successfully queried invoice search. Found {len(invoices_list)} invoice(s) on page 1:")
        for idx, item in enumerate(invoices_list[:3], 1):
            print(f"       {idx}. ID: {item.get('id')} | Number: {item.get('invoice_number')} | Status: {item.get('raw_status')} | Total: {item.get('total')} {item.get('currency')}")
    except paypal_client.PayPalError as e:
        print(f"[FAIL] Listing invoices failed: {e}")

    # 6. Experiments (INR and Past Due Date)
    print("\n" + "=" * 65)
    print("Optional Behavioural Experiments")
    print("=" * 65)

    # Experiment A: INR Currency
    print("\n--- Experiment A: Draft Invoice in INR Currency ---")
    inr_payload = {
        "client_name": "Smoke Test India",
        "client_email": "us-buyer@example.com",
        "items": [
            {"name": "Local Consultation", "quantity": 1, "unit_price": 2500.00}
        ],
        "currency": "INR",
        "due_date": (today + timedelta(days=5)).isoformat(),
        "note": "Testing INR currency support in PayPal Sandbox Invoicing.",
    }
    try:
        inr_id = paypal_client.create_invoice(inr_payload)
        print(f"[RESULT - INR ACCEPTED] PayPal accepted INR currency! Invoice ID: {inr_id}")
    except paypal_client.PayPalError as e:
        print(f"[RESULT - INR REJECTED] PayPal rejected INR currency:")
        print(f"                        {e}")

    # Experiment B: Past Due Date
    print("\n--- Experiment B: Draft Invoice with Past Due Date ---")
    past_due_date = "2026-10-15"
    past_payload = {
        "client_name": "Smoke Test Past Due",
        "client_email": "us-buyer@example.com",
        "items": [
            {"name": "Backdated Item", "quantity": 1, "unit_price": 100.00}
        ],
        "currency": "USD",
        "due_date": past_due_date,
        "note": "Testing past due date acceptance in PayPal Sandbox.",
    }
    try:
        past_id = paypal_client.create_invoice(past_payload)
        print(f"[RESULT - PAST DUE ACCEPTED] PayPal accepted past due date ({past_due_date})! Invoice ID: {past_id}")
        past_payer_url = paypal_client.send_invoice(past_id)
        inv_past = paypal_client.get_invoice(past_id)
        mapped_past = map_status(inv_past.get("raw_status"), inv_past.get("due_date"), today=today.isoformat())
        print(f"                             Raw Status: {inv_past.get('raw_status')} | Mapped Status: {mapped_past}")
        print(f"                             Payer URL: {past_payer_url or inv_past.get('payer_url')}")
    except paypal_client.PayPalError as e:
        print(f"[RESULT - PAST DUE REJECTED] PayPal rejected past due date ({past_due_date}):")
        print(f"                             {e}")

    print("\n" + "=" * 65)
    print("Smoke Test Execution Finished")
    print("=" * 65)


if __name__ == "__main__":
    run_smoke_test()
