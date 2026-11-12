"""Integration and route tests for invoices API and database persistence."""

import os
import tempfile
from unittest.mock import MagicMock, patch
import pytest

from app import create_app
import config
import db
import paypal_client


@pytest.fixture
def test_app():
    # Use temporary SQLite database for test isolation
    fd, temp_db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    old_db_path = config.DB_PATH
    config.DB_PATH = temp_db_path

    app = create_app()
    app.config["TESTING"] = True

    with app.app_context():
        db.init_db(temp_db_path)
        yield app

    # Cleanup temp db
    config.DB_PATH = old_db_path
    if os.path.exists(temp_db_path):
        try:
            os.remove(temp_db_path)
        except Exception:
            pass


@pytest.fixture
def client(test_app):
    return test_app.test_client()


def test_invalid_payload_rejected_before_paypal(client):
    """Invalid payloads are rejected with 400 without contacting PayPal."""
    with patch("paypal_client.create_invoice") as mock_create:
        # Missing client email
        res1 = client.post("/api/invoices", json={
            "client_name": "Ravi",
            "items": [{"name": "Logo", "quantity": 1, "unit_price": 500.0}],
            "currency": "USD",
        })
        assert res1.status_code == 400
        assert "errors" in res1.get_json()
        assert not mock_create.called

        # Zero unit price
        res2 = client.post("/api/invoices", json={
            "client_name": "Ravi",
            "client_email": "ravi@example.com",
            "items": [{"name": "Logo", "quantity": 1, "unit_price": 0.0}],
            "currency": "USD",
        })
        assert res2.status_code == 400
        assert not mock_create.called

        # Empty items
        res3 = client.post("/api/invoices", json={
            "client_name": "Ravi",
            "client_email": "ravi@example.com",
            "items": [],
            "currency": "USD",
        })
        assert res3.status_code == 400
        assert not mock_create.called


def test_create_and_send_success(client):
    """Valid payload creates, sends, saves in SQLite, and returns 201."""
    with patch("paypal_client.create_invoice", return_value="INV2-TEST-0001") as mock_create, \
         patch("paypal_client.send_invoice", return_value="https://payer-link-test") as mock_send, \
         patch("paypal_client.get_invoice", return_value={
             "id": "INV2-TEST-0001",
             "invoice_number": "0001",
             "raw_status": "SENT",
             "total": 500.0,
             "currency": "USD",
             "payer_url": "https://payer-link-test"
         }):

        payload = {
            "client_name": "Ravi",
            "client_email": "ravi@example.com",
            "items": [{"name": "Logo design", "quantity": 1, "unit_price": 500.0}],
            "currency": "USD",
            "due_date": "2026-11-20",
            "source_text": "Invoice Ravi $500 for logo design",
            "ai_mode": "fallback",
            "idempotency_key": "unique-key-123"
        }

        res = client.post("/api/invoices", json=payload)
        assert res.status_code == 201
        data = res.get_json()

        assert data["id"] is not None
        assert data["paypal_invoice_id"] == "INV2-TEST-0001"
        assert data["invoice_number"] == "0001"
        assert data["client_name"] == "Ravi"
        assert data["client_email"] == "ravi@example.com"
        assert data["total"] == 500.0
        assert data["status"] == "Unpaid"
        assert data["recipient_url"] == "https://payer-link-test"
        assert data["stale"] is False

        assert mock_create.called
        assert mock_send.called

        # Check persistence in database
        saved = db.get_invoice(data["id"])
        assert saved is not None
        assert saved["paypal_invoice_id"] == "INV2-TEST-0001"
        assert saved["last_status"] == "SENT"


def test_idempotency_prevents_duplicate_creation(client):
    """Resending with the same idempotency key returns 200 without creating a new invoice."""
    with patch("paypal_client.create_invoice", return_value="INV2-IDEM-0002") as mock_create, \
         patch("paypal_client.send_invoice", return_value="https://payer-link") as mock_send, \
         patch("paypal_client.get_invoice", return_value={
             "id": "INV2-IDEM-0002",
             "invoice_number": "0002",
             "raw_status": "SENT",
             "total": 100.0,
             "currency": "USD",
             "payer_url": "https://payer-link"
         }):

        payload = {
            "client_name": "Bob",
            "client_email": "bob@example.com",
            "items": [{"name": "Consulting", "quantity": 1, "unit_price": 100.0}],
            "currency": "USD",
            "idempotency_key": "fixed-idempotency-key-abc"
        }

        # First request
        res1 = client.post("/api/invoices", json=payload)
        assert res1.status_code == 201
        inv1 = res1.get_json()

        # Second request with identical key
        res2 = client.post("/api/invoices", json=payload)
        assert res2.status_code == 200
        inv2 = res2.get_json()

        assert inv1["id"] == inv2["id"]
        assert inv1["paypal_invoice_id"] == inv2["paypal_invoice_id"]
        # PayPal create_invoice called only once
        assert mock_create.call_count == 1


def test_send_failure_saves_draft_and_returns_502(client):
    """If send fails after creation, draft is preserved in DB and 502 returned."""
    with patch("paypal_client.create_invoice", return_value="INV2-DRAFT-ONLY") as mock_create, \
         patch("paypal_client.send_invoice", side_effect=paypal_client.PayPalError("Network failure", status_code=503)):

        payload = {
            "client_name": "Carol",
            "client_email": "carol@example.com",
            "items": [{"name": "Writing", "quantity": 1, "unit_price": 200.0}],
            "currency": "USD",
            "idempotency_key": "draft-key-xyz"
        }

        res = client.post("/api/invoices", json=payload)
        assert res.status_code == 502
        assert "failed to send" in res.get_json()["error"]

        # Verify draft was preserved locally
        invs = db.list_invoices()
        assert len(invs) == 1
        assert invs[0]["paypal_invoice_id"] == "INV2-DRAFT-ONLY"
        assert invs[0]["last_status"] == "DRAFT"


def test_list_invoices_refreshes_open_status(client):
    """GET /api/invoices refreshes open status from PayPal and updates local DB."""
    # Seed an unpaid invoice locally
    inv_id = db.insert_invoice({
        "paypal_invoice_id": "INV2-REFRESH-ME",
        "invoice_number": "0005",
        "client_name": "Dave",
        "client_email": "dave@example.com",
        "currency": "USD",
        "total": 300.0,
        "due_date": "2026-11-20",
        "last_status": "SENT",
    })

    # Mock PayPal returning PAID
    with patch("paypal_client.get_invoice", return_value={
        "id": "INV2-REFRESH-ME",
        "invoice_number": "0005",
        "raw_status": "PAID",
        "total": 300.0,
        "currency": "USD",
        "payer_url": "https://payer-view"
    }):
        res = client.get("/api/invoices")
        assert res.status_code == 200
        invoices = res.get_json()["invoices"]
        assert len(invoices) == 1
        assert invoices[0]["status"] == "Paid"
        assert invoices[0]["stale"] is False

        # Verify DB updated
        row = db.get_invoice(inv_id)
        assert row["last_status"] == "PAID"


def test_simulate_overdue_flips_status(client):
    """Simulate overdue flips status to Overdue and includes demo_only flag."""
    inv_id = db.insert_invoice({
        "paypal_invoice_id": "INV2-OVERDUE-TEST",
        "invoice_number": "0006",
        "client_name": "Eve",
        "client_email": "eve@example.com",
        "currency": "USD",
        "total": 150.0,
        "due_date": "2026-11-20",
        "last_status": "SENT",
    })

    # Test via /api/demo/<id>/make-overdue (called by app.js)
    res = client.post(f"/api/demo/{inv_id}/make-overdue")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "Overdue"
    assert data["demo_only"] is True
    assert data["demo_due_override"] is not None

    # Test alias /api/invoices/<id>/simulate-overdue
    res_alias = client.post(f"/api/invoices/{inv_id}/simulate-overdue")
    assert res_alias.status_code == 200
    assert res_alias.get_json()["status"] == "Overdue"


def test_simulate_overdue_blocked_when_demo_mode_false(client):
    """Simulate overdue returns 403 when DEMO_MODE is False."""
    inv_id = db.insert_invoice({
        "paypal_invoice_id": "INV2-DEMO-OFF",
        "invoice_number": "0007",
        "client_name": "Frank",
        "client_email": "frank@example.com",
        "currency": "USD",
        "total": 100.0,
        "due_date": "2026-11-20",
        "last_status": "SENT",
    })

    old_demo = config.DEMO_MODE
    try:
        config.DEMO_MODE = False
        res = client.post(f"/api/demo/{inv_id}/make-overdue")
        assert res.status_code == 403
        assert "Demo mode is disabled" in res.get_json()["error"]
    finally:
        config.DEMO_MODE = old_demo


def test_summary_totals_exclude_cancelled(client):
    """GET /api/summary calculates totals and excludes Cancelled invoices."""
    # Insert 1 Paid ($100), 1 Unpaid ($200), 1 Cancelled ($300)
    db.insert_invoice({
        "client_name": "Paid Client",
        "currency": "USD",
        "total": 100.0,
        "last_status": "PAID",
    })
    db.insert_invoice({
        "client_name": "Unpaid Client",
        "currency": "USD",
        "total": 200.0,
        "last_status": "SENT",
        "due_date": "2026-11-30",
    })
    db.insert_invoice({
        "client_name": "Cancelled Client",
        "currency": "USD",
        "total": 300.0,
        "last_status": "CANCELLED",
    })

    res = client.get("/api/summary")
    assert res.status_code == 200
    summary = res.get_json()

    assert summary["invoice_count"] == 2
    assert summary["total_billed"] == 300.0  # 100 + 200
    assert summary["total_collected"] == 100.0
    assert summary["total_outstanding"] == 200.0
    assert summary["overdue_count"] == 0
