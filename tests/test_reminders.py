"""Unit and integration tests for reminder drafting, sending, and safety limits."""

import os
import tempfile
from unittest.mock import MagicMock, patch
import pytest

from app import create_app
import config
import db
import ai_client
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


def test_draft_reminder_tones_and_fallback():
    """Verify fallback reminder drafting produces polite, professional text across all 3 tones."""
    inv = {
        "id": 1,
        "invoice_number": "INV-100",
        "client_name": "Ravi Sharma",
        "total": 500.0,
        "currency": "USD",
        "due_date": "2026-11-10",
        "demo_due_override": None,
    }

    # Friendly tone
    draft_friendly = ai_client.draft_reminder(inv, tone="friendly")
    assert draft_friendly["tone"] == "friendly"
    assert "Friendly reminder" in draft_friendly["subject"]
    assert "Ravi Sharma" in draft_friendly["note"]
    assert "500.00 USD" in draft_friendly["note"]
    assert len(draft_friendly["note"].split()) < 120

    # Firm tone
    draft_firm = ai_client.draft_reminder(inv, tone="firm")
    assert draft_firm["tone"] == "firm"
    assert "past due" in draft_firm["subject"].lower()
    assert "Ravi Sharma" in draft_firm["note"]
    assert len(draft_firm["note"].split()) < 120

    # Final tone
    draft_final = ai_client.draft_reminder(inv, tone="final")
    assert draft_final["tone"] == "final"
    assert "Final payment reminder" in draft_final["subject"]
    assert "Ravi Sharma" in draft_final["note"]
    assert len(draft_final["note"].split()) < 120


def test_auto_tone_selection_by_days_overdue():
    """Tone automatically defaults by days overdue: 0-7 friendly, 8-20 firm, 21+ final."""
    # 3 days overdue -> friendly
    inv_3 = {"client_name": "Alice", "due_date": "2026-11-09", "total": 100.0}
    with patch("ai_client.date") as mock_date:
        from datetime import date
        mock_date.today.return_value = date(2026, 11, 12)
        mock_date.fromisoformat = date.fromisoformat
        res_3 = ai_client.draft_reminder(inv_3)
        assert res_3["tone"] == "friendly"

    # 12 days overdue -> firm
    inv_12 = {"client_name": "Bob", "due_date": "2026-10-31", "total": 100.0}
    with patch("ai_client.date") as mock_date:
        from datetime import date
        mock_date.today.return_value = date(2026, 11, 12)
        mock_date.fromisoformat = date.fromisoformat
        res_12 = ai_client.draft_reminder(inv_12)
        assert res_12["tone"] == "firm"

    # 25 days overdue -> final
    inv_25 = {"client_name": "Carol", "due_date": "2026-10-18", "total": 100.0}
    with patch("ai_client.date") as mock_date:
        from datetime import date
        mock_date.today.return_value = date(2026, 11, 12)
        mock_date.fromisoformat = date.fromisoformat
        res_25 = ai_client.draft_reminder(inv_25)
        assert res_25["tone"] == "final"


def test_route_reminder_draft_success(client):
    """POST /api/invoices/<id>/reminder-draft returns subject, note, tone, days_overdue, ai_mode."""
    inv_id = db.insert_invoice({
        "invoice_number": "0042",
        "client_name": "Kavita",
        "client_email": "kavita@example.com",
        "currency": "USD",
        "total": 350.0,
        "due_date": "2026-11-20",
        "last_status": "SENT",
    })

    res = client.post(f"/api/invoices/{inv_id}/reminder-draft", json={"tone": "friendly"})
    assert res.status_code == 200
    data = res.get_json()
    assert "subject" in data
    assert "note" in data
    assert data["tone"] == "friendly"
    assert "days_overdue" in data
    assert "ai_mode" in data
    assert "Kavita" in data["note"]


def test_route_reminder_draft_blocked_on_paid_and_cancelled(client):
    """Cannot draft a reminder for a paid or cancelled invoice."""
    paid_id = db.insert_invoice({
        "client_name": "Paid Client",
        "currency": "USD",
        "total": 100.0,
        "last_status": "PAID",
    })
    canc_id = db.insert_invoice({
        "client_name": "Cancelled Client",
        "currency": "USD",
        "total": 200.0,
        "last_status": "CANCELLED",
    })

    res_paid = client.post(f"/api/invoices/{paid_id}/reminder-draft")
    assert res_paid.status_code == 400
    assert "Cannot draft" in res_paid.get_json()["error"]

    res_canc = client.post(f"/api/invoices/{canc_id}/reminder-draft")
    assert res_canc.status_code == 400
    assert "Cannot draft" in res_canc.get_json()["error"]


def test_route_send_reminder_success(client):
    """POST /api/invoices/<id>/reminder-send dispatches via PayPal and records history."""
    inv_id = db.insert_invoice({
        "paypal_invoice_id": "INV2-REMIND-OK",
        "invoice_number": "0043",
        "client_name": "Vikram",
        "client_email": "vikram@example.com",
        "currency": "USD",
        "total": 750.0,
        "due_date": "2026-11-15",
        "last_status": "SENT",
    })

    with patch("paypal_client.remind_invoice", return_value=True) as mock_remind:
        payload = {
            "subject": "Payment Reminder: Invoice 0043",
            "note": "Hi Vikram, please remit payment for invoice 0043.",
            "tone": "friendly",
        }
        res = client.post(f"/api/invoices/{inv_id}/reminder-send", json=payload)
        assert res.status_code == 200
        data = res.get_json()
        assert data["ok"] is True
        assert "sent_at" in data
        assert mock_remind.called

        # Verify recorded in database and retrievable via GET /api/invoices/<id>/reminders
        res_hist = client.get(f"/api/invoices/{inv_id}/reminders")
        assert res_hist.status_code == 200
        rems = res_hist.get_json()["reminders"]
        assert len(rems) == 1
        assert rems[0]["subject"] == "Payment Reminder: Invoice 0043"
        assert rems[0]["status"] == "SENT"


def test_route_send_reminder_blocked_on_paid_and_cancelled(client):
    """Cannot send a reminder for a paid or cancelled invoice."""
    paid_id = db.insert_invoice({
        "client_name": "Paid Client",
        "currency": "USD",
        "total": 100.0,
        "last_status": "PAID",
    })
    res_paid = client.post(f"/api/invoices/{paid_id}/reminder-send", json={
        "subject": "Hello", "note": "Pay me"
    })
    assert res_paid.status_code == 400
    assert "Cannot send" in res_paid.get_json()["error"]


def test_daily_reminder_rate_limit_429(client):
    """Enforces maximum 2 reminders per invoice per day (HTTP 429 on 3rd)."""
    inv_id = db.insert_invoice({
        "paypal_invoice_id": "INV2-RATE-LIMIT",
        "invoice_number": "0044",
        "client_name": "Rate Limited Client",
        "client_email": "ratelimit@example.com",
        "currency": "USD",
        "total": 200.0,
        "last_status": "SENT",
    })

    with patch("paypal_client.remind_invoice", return_value=True):
        # 1st reminder
        r1 = client.post(f"/api/invoices/{inv_id}/reminder-send", json={"subject": "S1", "note": "N1"})
        assert r1.status_code == 200

        # 2nd reminder
        r2 = client.post(f"/api/invoices/{inv_id}/reminder-send", json={"subject": "S2", "note": "N2"})
        assert r2.status_code == 200

        # 3rd reminder on same day -> 429
        r3 = client.post(f"/api/invoices/{inv_id}/reminder-send", json={"subject": "S3", "note": "N3"})
        assert r3.status_code == 429
        assert "Daily reminder limit reached" in r3.get_json()["error"]


def test_route_remind_alias_with_auto_draft(client):
    """POST /api/invoices/<id>/remind auto-drafts and sends without requiring body."""
    inv_id = db.insert_invoice({
        "paypal_invoice_id": "INV2-ALIAS-REMIND",
        "invoice_number": "0045",
        "client_name": "Suresh",
        "client_email": "suresh@example.com",
        "currency": "USD",
        "total": 600.0,
        "due_date": "2026-11-10",
        "last_status": "SENT",
    })

    with patch("paypal_client.remind_invoice", return_value=True) as mock_remind:
        res = client.post(f"/api/invoices/{inv_id}/remind")
        assert res.status_code == 200
        assert res.get_json()["ok"] is True
        assert mock_remind.called
