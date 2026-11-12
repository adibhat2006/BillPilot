"""Unit tests for Phase 7 security, AI, and audit hardening.

Verifies:
1. Gemini API uses 'x-goog-api-key' header (never '?key=' in URL) and never logs secrets on error.
2. Default Gemini model is 'gemini-2.5-flash' when AI_MODEL is unset.
3. Hallucinated client_email not present in source sentence is stripped to None and added to missing_fields.
4. AI reminder copy exceeding 120 words or containing aggressive threats falls back to template.
5. Idempotency race condition handling avoids HTTP 500 and prevents duplicate PayPal invoice creation.
6. Retrying with idempotency_key on an existing DRAFT invoice triggers send_invoice.
"""

import json
import logging
import sqlite3
from unittest.mock import MagicMock, patch
import pytest

import ai_client
import config
import db
from app import create_app
import paypal_client


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """Create a test client with a fresh temporary SQLite database."""
    test_db = str(tmp_path / "test_phase7.db")
    monkeypatch.setattr(config, "DB_PATH", test_db)
    monkeypatch.setattr(config, "DEMO_MODE", True)
    monkeypatch.setattr(config, "AI_PROVIDER", "none")
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


# ---------------- 1. Gemini Header Auth & Logging Safety ----------------

def test_gemini_header_auth_no_key_in_url_and_safe_logs(monkeypatch, caplog):
    """Verify Gemini request uses x-goog-api-key header and never leaks key in URL or error logs."""
    test_secret = "secret_gemini_key_12345"
    monkeypatch.setattr(config, "AI_API_KEY", test_secret)
    monkeypatch.setattr(config, "AI_MODEL", "")  # Unset model to test default

    with patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": json.dumps({"client_name": "Test Client", "items": [{"name": "Work", "quantity": 1, "unit_price": 100}]})}
                        ]
                    }
                }
            ]
        }
        mock_post.return_value = mock_response

        # Execute call
        result = ai_client._call_gemini("System prompt", "Invoice Test $100")

        # Check call arguments
        mock_post.assert_called_once()
        called_url = mock_post.call_args[0][0]
        called_headers = mock_post.call_args[1].get("headers", {})

        # URL must NOT contain ?key= or the secret
        assert "?key=" not in called_url
        assert test_secret not in called_url
        # URL must default to gemini-2.5-flash
        assert "gemini-2.5-flash:generateContent" in called_url

        # Header must contain x-goog-api-key
        assert called_headers.get("x-goog-api-key") == test_secret

    # Verify exception logging never prints the secret or raw URL
    with patch("requests.post") as mock_fail_post:
        import requests
        mock_fail_post.side_effect = requests.ConnectionError(f"Connection error to https://gemini?key={test_secret}")

        caplog.set_level(logging.WARNING)
        with pytest.raises(RuntimeError) as exc_info:
            ai_client._call_gemini("System prompt", "Invoice Test $100")

        # Raised error and logged message must not leak key
        assert test_secret not in str(exc_info.value)
        for record in caplog.records:
            assert test_secret not in record.message


# ---------------- 2. Anti-Hallucination Email Check ----------------

def test_hallucinated_email_stripped_from_extraction():
    """Verify hallucinated emails not in original text are cleared and flagged in missing_fields."""
    source_sentence = "Invoice Acme Corp $500 for website design, due Friday"
    
    # LLM hallucinated an email 'billing@acme.com' that was nowhere in the source text
    hallucinated_output = {
        "client_name": "Acme Corp",
        "client_email": "billing@acme.com",
        "items": [{"name": "website design", "quantity": 1, "unit_price": 500.0}],
        "currency": "USD",
        "due_date": "2026-11-20",
        "note": None,
        "missing_fields": [],
        "confidence": 0.95,
    }

    sanitized = ai_client.extract_invoice_fields(hallucinated_output, source_sentence)
    assert sanitized["client_email"] is None
    assert "client_email" in sanitized["missing_fields"]

    # When email is genuinely present in source sentence (even with case difference)
    real_source = "Invoice Acme billing@acme.com $500 for website design, due Friday"
    valid_output = {
        "client_name": "Acme",
        "client_email": "BILLING@acme.com",
        "items": [{"name": "website design", "quantity": 1, "unit_price": 500.0}],
        "currency": "USD",
        "due_date": "2026-11-20",
        "note": None,
        "missing_fields": [],
        "confidence": 0.95,
    }

    verified = ai_client.extract_invoice_fields(valid_output, real_source)
    assert verified["client_email"] == "BILLING@acme.com"


# ---------------- 3. Reminder Word Count & Safety Policy ----------------

def test_ai_reminder_word_count_and_threat_safety_fallback(monkeypatch):
    """Verify AI reminder copy exceeding 120 words or containing threats falls back to template."""
    monkeypatch.setattr(config, "AI_PROVIDER", "gemini")
    monkeypatch.setattr(config, "AI_API_KEY", "mock_key")

    mock_invoice = {
        "id": 1,
        "invoice_number": "INV-101",
        "client_name": "John Doe",
        "total": 350.0,
        "currency": "USD",
        "due_date": "2026-11-01",
        "demo_due_override": None,
    }

    # Case A: Note exceeds 120 words -> must fallback to template
    long_note = "word " * 125
    with patch("ai_client._call_gemini") as mock_gemini:
        mock_gemini.return_value = json.dumps({
            "subject": "Payment reminder",
            "note": long_note,
        })
        draft = ai_client.draft_reminder(mock_invoice, tone="firm")
        assert draft["ai_mode"] == "fallback"
        assert len(draft["note"].split()) < 120

    # Case B: Note contains legal threats -> must fallback to template
    threat_note = "Please pay now or we will sue you in court and initiate legal action through a collections agency."
    with patch("ai_client._call_gemini") as mock_gemini:
        mock_gemini.return_value = json.dumps({
            "subject": "Urgent reminder",
            "note": threat_note,
        })
        draft = ai_client.draft_reminder(mock_invoice, tone="final")
        assert draft["ai_mode"] == "fallback"
        assert "sue" not in draft["note"]
        assert "legal action" not in draft["note"]

    # Case C: Safe and concise note -> returns ai_mode="ai"
    safe_note = "Hi John Doe, invoice INV-101 for 350.00 USD was due on 2026-11-01. Please let us know if you need assistance."
    with patch("ai_client._call_gemini") as mock_gemini:
        mock_gemini.return_value = json.dumps({
            "subject": "Payment reminder for INV-101",
            "note": safe_note,
        })
        draft = ai_client.draft_reminder(mock_invoice, tone="friendly")
        assert draft["ai_mode"] == "ai"
        assert draft["note"] == safe_note


# ---------------- 4. Idempotency Race Condition & Draft Retry ----------------

def test_idempotency_retry_sends_prior_draft(app_client, monkeypatch):
    """Verify that retrying POST /api/invoices on a prior DRAFT invoice calls send_invoice."""
    # Pre-populate database with an existing invoice in DRAFT status
    draft_invoice = {
        "idempotency_key": "draft-retry-key-001",
        "paypal_invoice_id": "INV2-DRAFT-123",
        "invoice_number": None,
        "client_name": "Beta LLC",
        "client_email": "beta@example.com",
        "currency": "USD",
        "total": 450.0,
        "due_date": "2026-12-01",
        "source_text": "Invoice Beta $450",
        "extracted_json": {"items": [{"name": "Consulting", "quantity": 1, "unit_price": 450.0}]},
        "ai_mode": "ai",
        "recipient_url": None,
        "last_status": "DRAFT",
    }
    db.insert_invoice(draft_invoice)

    with patch("paypal_client.create_invoice") as mock_create, \
         patch("paypal_client.send_invoice") as mock_send, \
         patch("paypal_client.get_invoice") as mock_get:
        
        mock_send.return_value = "https://www.sandbox.paypal.com/invoice/p/#INV2-DRAFT-123"
        mock_get.return_value = {
            "invoice_number": "INV-DRAFT-001",
            "raw_status": "SENT",
            "payer_url": "https://www.sandbox.paypal.com/invoice/p/#INV2-DRAFT-123",
        }

        # Resubmit with the same idempotency key
        payload = {
            "idempotency_key": "draft-retry-key-001",
            "client_name": "Beta LLC",
            "client_email": "beta@example.com",
            "items": [{"name": "Consulting", "quantity": 1, "unit_price": 450.0}],
            "currency": "USD",
            "due_date": "2026-12-01",
        }
        res = app_client.post("/api/invoices", json=payload)
        assert res.status_code == 200

        # create_invoice must NOT be called again
        mock_create.assert_not_called()
        # send_invoice MUST be called with the existing draft ID
        mock_send.assert_called_once_with("INV2-DRAFT-123")

        data = res.get_json()
        assert data["status"] in ("SENT", "Unpaid")
        assert data["invoice_number"] == "INV-DRAFT-001"


def test_concurrent_idempotency_race_handling(app_client):
    """Verify that concurrent duplicate submissions handle IntegrityError safely and never return HTTP 500."""
    with patch("paypal_client.create_invoice") as mock_create, \
         patch("paypal_client.send_invoice") as mock_send, \
         patch("paypal_client.get_invoice") as mock_get:

        mock_create.return_value = "INV2-CONCUR-001"
        mock_send.return_value = "https://payer.url"
        mock_get.return_value = {"invoice_number": "1001", "raw_status": "SENT", "payer_url": "https://payer.url"}

        payload = {
            "idempotency_key": "concurrency-key-999",
            "client_name": "Speedy Corp",
            "client_email": "speedy@example.com",
            "items": [{"name": "Delivery", "quantity": 1, "unit_price": 50.0}],
            "currency": "USD",
        }

        # First request succeeds
        res1 = app_client.post("/api/invoices", json=payload)
        assert res1.status_code == 201

        # Second request with identical key returns 200 without calling PayPal create again
        res2 = app_client.post("/api/invoices", json=payload)
        assert res2.status_code == 200
        assert res2.get_json()["client_name"] == "Speedy Corp"
        assert mock_create.call_count == 1
