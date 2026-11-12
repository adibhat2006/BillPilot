"""Seed script to populate local database with realistic demo invoices for BillPilot."""

import sys
from datetime import date, timedelta
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db

def seed_demo_data():
    print("Initializing database...")
    db.init_db()

    today = date.today()
    invoices = [
        {
            "idempotency_key": "seed-inv-001",
            "paypal_invoice_id": "INV2-SEED-PAID-001",
            "invoice_number": "SEED-001",
            "client_name": "Acme Media Group",
            "client_email": "billing@acmemedia.example.com",
            "currency": "USD",
            "total": 1200.00,
            "due_date": (today - timedelta(days=5)).isoformat(),
            "demo_due_override": None,
            "source_text": "Invoice Acme Media Group $1200 for branding package, due last week",
            "extracted_json": {"items": [{"name": "Branding package", "quantity": 1, "unit_price": 1200.0}]},
            "ai_mode": "fallback",
            "recipient_url": "https://www.sandbox.paypal.com/invoice/p/#INV2-SEED-PAID-001",
            "last_status": "PAID",
        },
        {
            "idempotency_key": "seed-inv-002",
            "paypal_invoice_id": "INV2-SEED-UNPAID-002",
            "invoice_number": "SEED-002",
            "client_name": "DevSolutions LLC",
            "client_email": "finance@devsolutions.example.com",
            "currency": "USD",
            "total": 750.00,
            "due_date": (today + timedelta(days=7)).isoformat(),
            "demo_due_override": None,
            "source_text": "Bill DevSolutions LLC $750 for API integration, due in 7 days",
            "extracted_json": {"items": [{"name": "API integration", "quantity": 1, "unit_price": 750.0}]},
            "ai_mode": "fallback",
            "recipient_url": "https://www.sandbox.paypal.com/invoice/p/#INV2-SEED-UNPAID-002",
            "last_status": "SENT",
        },
        {
            "idempotency_key": "seed-inv-003",
            "paypal_invoice_id": "INV2-SEED-OVERDUE-003",
            "invoice_number": "SEED-003",
            "client_name": "Starlight Studios",
            "client_email": "accounts@starlight.example.com",
            "currency": "USD",
            "total": 450.00,
            "due_date": (today - timedelta(days=2)).isoformat(),
            "demo_due_override": (today - timedelta(days=14)).isoformat(),
            "source_text": "Invoice Starlight Studios $450 for motion graphics, due Nov 1",
            "extracted_json": {"items": [{"name": "Motion graphics", "quantity": 1, "unit_price": 450.0}]},
            "ai_mode": "fallback",
            "recipient_url": "https://www.sandbox.paypal.com/invoice/p/#INV2-SEED-OVERDUE-003",
            "last_status": "SENT",
        },
    ]

    for inv in invoices:
        existing = db.get_invoice_by_idempotency_key(inv["idempotency_key"])
        if not existing:
            inv_id = db.insert_invoice(inv)
            db.upsert_client(inv["client_name"], inv["client_email"])
            print(f"Inserted demo invoice #{inv_id}: {inv['invoice_number']} ({inv['client_name']}) - {inv['total']} {inv['currency']}")
        else:
            print(f"Demo invoice {inv['invoice_number']} already exists (id #{existing['id']})")

    print("\nDatabase demo seed complete!")

if __name__ == "__main__":
    seed_demo_data()
