"""Flask application entry point for BillPilot.

Serves the minimal test UI and REST API endpoints:
  - GET  /api/health
  - POST /api/extract
  - POST /api/invoices
  - GET  /api/invoices
  - GET  /api/invoices/<id>
  - POST /api/demo/<id>/make-overdue & POST /api/invoices/<id>/simulate-overdue
  - GET  /api/summary
"""

import logging
import re
import sqlite3
import time
from datetime import date, timedelta
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

import config
import db
import ai_client
import paypal_client
import services

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("billpilot")


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__, static_folder="static", static_url_path="/static")

    # Initialize local SQLite database
    db.init_db()

    @app.route("/")
    def index():
        """Serve the minimal test UI."""
        return send_from_directory(app.static_folder, "index.html")

    @app.route("/api/health", methods=["GET"])
    def health():
        """Health check endpoint exposing configuration state without secrets."""
        return jsonify({
            "status": "ok",
            "ai_provider": config.AI_PROVIDER,
            "ai_configured": config.is_ai_configured(),
            "paypal_configured": config.is_paypal_configured(),
            "demo_mode": config.DEMO_MODE,
        }), 200

    @app.route("/api/extract", methods=["POST"])
    def extract():
        """Extract structured invoice fields from natural language text."""
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or "text" not in payload:
            return jsonify({"error": "Request body must be JSON containing a 'text' field."}), 400

        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            return jsonify({"error": "Text is required and must be a non-empty string."}), 400

        today_iso = date.today().isoformat()
        extraction = ai_client.extract_invoice(
            text.strip(),
            today=today_iso,
            default_currency=config.DEFAULT_CURRENCY,
        )

        validation_errors = services.validate_extraction(extraction, today=today_iso)

        response_data = {
            "client_name": extraction.get("client_name"),
            "client_email": extraction.get("client_email"),
            "items": extraction.get("items", []),
            "currency": extraction.get("currency", config.DEFAULT_CURRENCY),
            "due_date": extraction.get("due_date"),
            "note": extraction.get("note"),
            "missing_fields": extraction.get("missing_fields", []),
            "confidence": extraction.get("confidence", 0.9),
            "ai_mode": extraction.get("ai_mode", "ai"),
            "errors": validation_errors,
        }

        return jsonify(response_data), 200

    @app.route("/api/invoices", methods=["POST"])
    def create_and_send_invoice():
        """Create and send a PayPal invoice after client-side preview confirmation."""
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"error": "Request body must be a valid JSON object."}), 400

        today_iso = date.today().isoformat()

        # Run extraction validation rules
        errors = services.validate_extraction(data, today=today_iso)

        # Invoicing requires a valid client email
        client_email = data.get("client_email")
        if not client_email or not isinstance(client_email, str) or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", client_email.strip()):
            errors.append("Enter a valid client email.")

        # Invoicing requires total > 0
        items = data.get("items", [])
        total = 0.0
        if isinstance(items, list):
            for it in items:
                if isinstance(it, dict):
                    try:
                        total += int(it.get("quantity", 1)) * float(it.get("unit_price", 0.0))
                    except (ValueError, TypeError):
                        pass

        if total <= 0:
            errors.append("Total invoice amount must be greater than 0.")

        if errors:
            return jsonify({"errors": errors, "error": errors[0]}), 400

        # Double-submit protection via idempotency_key
        idempotency_key = data.get("idempotency_key")
        reserved_id = None
        if idempotency_key:
            existing = db.get_invoice_by_idempotency_key(idempotency_key)
            if existing:
                if existing.get("last_status") == "DRAFT" and existing.get("paypal_invoice_id"):
                    logger.info("Retrying send for prior draft invoice %s (key=%s)", existing["paypal_invoice_id"], idempotency_key)
                    try:
                        payer_url = paypal_client.send_invoice(existing["paypal_invoice_id"])
                        raw_status = "SENT"
                        invoice_number = existing.get("invoice_number")
                        try:
                            details = paypal_client.get_invoice(existing["paypal_invoice_id"])
                            invoice_number = details.get("invoice_number") or invoice_number
                            raw_status = details.get("raw_status") or raw_status
                            payer_url = payer_url or details.get("payer_url")
                        except Exception as get_err:
                            logger.warning("Could not fetch invoice details after retried send: %s", get_err)

                        db.update_invoice_status(
                            existing["id"],
                            raw_status,
                            recipient_url=payer_url,
                            invoice_number=invoice_number,
                        )
                        updated = db.get_invoice(existing["id"])
                        return jsonify(services.format_invoice_response(updated)), 200
                    except paypal_client.PayPalError as send_err:
                        logger.error("Retry send failed for draft %s: %s", existing["paypal_invoice_id"], send_err)
                        return jsonify({"error": f"Prior draft {existing['paypal_invoice_id']} failed to send: {send_err.message}"}), 502
                elif existing.get("last_status") != "CREATING":
                    logger.info("Resending existing invoice for idempotency_key: %s", idempotency_key)
                    return jsonify(services.format_invoice_response(existing)), 200

            # Atomically reserve the idempotency key in DB to prevent concurrent duplicate creation
            try:
                reservation_record = {
                    "idempotency_key": idempotency_key,
                    "paypal_invoice_id": None,
                    "invoice_number": None,
                    "client_name": data["client_name"].strip(),
                    "client_email": data["client_email"].strip(),
                    "currency": data.get("currency", "USD").upper(),
                    "total": total,
                    "due_date": data.get("due_date"),
                    "source_text": data.get("source_text"),
                    "extracted_json": data,
                    "ai_mode": data.get("ai_mode", "ai"),
                    "recipient_url": None,
                    "last_status": "CREATING",
                }
                reserved_id = db.insert_invoice(reservation_record)
            except sqlite3.IntegrityError:
                # Concurrent request reserved or inserted with this key at the exact same moment
                for _ in range(30):
                    time.sleep(0.1)
                    existing = db.get_invoice_by_idempotency_key(idempotency_key)
                    if existing and existing.get("last_status") != "CREATING":
                        return jsonify(services.format_invoice_response(existing)), 200
                existing = db.get_invoice_by_idempotency_key(idempotency_key)
                if existing:
                    return jsonify(services.format_invoice_response(existing)), 200
                return jsonify({"error": "Duplicate submission in progress."}), 409

        # Step 1: Create invoice in PayPal
        try:
            paypal_invoice_id = paypal_client.create_invoice(data)
        except paypal_client.PayPalError as e:
            logger.error("PayPal create_invoice failed: %s", e)
            if reserved_id:
                db.delete_invoice(reserved_id)
            return jsonify({"error": f"Failed to create PayPal invoice: {e.message}"}), 502

        # Step 2: Send invoice via PayPal
        payer_url = None
        try:
            payer_url = paypal_client.send_invoice(paypal_invoice_id)
            raw_status = "SENT"
        except paypal_client.PayPalError as send_err:
            logger.error("PayPal send_invoice failed after draft created: %s", send_err)
            if reserved_id:
                db.update_invoice_paypal_info(
                    reserved_id,
                    paypal_invoice_id=paypal_invoice_id,
                    raw_status="DRAFT",
                    recipient_url=None,
                    invoice_number=None,
                )
            else:
                draft_record = {
                    "idempotency_key": idempotency_key,
                    "paypal_invoice_id": paypal_invoice_id,
                    "invoice_number": None,
                    "client_name": data["client_name"].strip(),
                    "client_email": data["client_email"].strip(),
                    "currency": data.get("currency", "USD").upper(),
                    "total": total,
                    "due_date": data.get("due_date"),
                    "source_text": data.get("source_text"),
                    "extracted_json": data,
                    "ai_mode": data.get("ai_mode", "ai"),
                    "recipient_url": None,
                    "last_status": "DRAFT",
                }
                db.insert_invoice(draft_record)
            db.upsert_client(data["client_name"], data["client_email"])
            return jsonify({"error": f"PayPal draft {paypal_invoice_id} created, but failed to send: {send_err.message}"}), 502

        # Step 3: Fetch updated invoice details for invoice_number & payer_url
        invoice_number = None
        try:
            details = paypal_client.get_invoice(paypal_invoice_id)
            invoice_number = details.get("invoice_number")
            raw_status = details.get("raw_status") or raw_status
            payer_url = payer_url or details.get("payer_url")
        except Exception as get_err:
            logger.warning("Could not fetch invoice details immediately after send: %s", get_err)

        # Step 4: Persist or update locally in SQLite
        if reserved_id:
            db.update_invoice_paypal_info(
                reserved_id,
                paypal_invoice_id=paypal_invoice_id,
                raw_status=raw_status,
                recipient_url=payer_url,
                invoice_number=invoice_number,
            )
            local_id = reserved_id
        else:
            saved_record = {
                "idempotency_key": idempotency_key,
                "paypal_invoice_id": paypal_invoice_id,
                "invoice_number": invoice_number,
                "client_name": data["client_name"].strip(),
                "client_email": data["client_email"].strip(),
                "currency": data.get("currency", "USD").upper(),
                "total": total,
                "due_date": data.get("due_date"),
                "source_text": data.get("source_text"),
                "extracted_json": data,
                "ai_mode": data.get("ai_mode", "ai"),
                "recipient_url": payer_url,
                "last_status": raw_status,
            }
            local_id = db.insert_invoice(saved_record)

        db.upsert_client(data["client_name"], data["client_email"])

        created_invoice = db.get_invoice(local_id)
        return jsonify(services.format_invoice_response(created_invoice)), 201

    @app.route("/api/invoices", methods=["GET"])
    def get_invoices():
        """List all invoices, refreshing live statuses from PayPal for open invoices."""
        rows = db.list_invoices()
        formatted = []

        for row in rows:
            stale = False
            raw_status = row.get("last_status", "")
            paypal_id = row.get("paypal_invoice_id")

            # Refresh live status from PayPal for open invoices
            if paypal_id and raw_status not in ("PAID", "MARKED_AS_PAID", "CANCELLED", "CANCELED"):
                try:
                    live_data = paypal_client.get_invoice(paypal_id)
                    new_raw_status = live_data.get("raw_status")
                    new_payer_url = live_data.get("payer_url")
                    new_inv_num = live_data.get("invoice_number")

                    if new_raw_status:
                        db.update_invoice_status(
                            row["id"],
                            new_raw_status,
                            recipient_url=new_payer_url,
                            invoice_number=new_inv_num,
                        )
                        row["last_status"] = new_raw_status
                        if new_payer_url:
                            row["recipient_url"] = new_payer_url
                        if new_inv_num:
                            row["invoice_number"] = new_inv_num
                except Exception as e:
                    logger.warning("Could not refresh live status for invoice %s (%s): %s", row["id"], paypal_id, e)
                    stale = True

            formatted.append(services.format_invoice_response(row, stale=stale))

        return jsonify({"invoices": formatted}), 200

    @app.route("/api/invoices/<int:invoice_id>", methods=["GET"])
    def get_invoice_by_id(invoice_id: int):
        """Retrieve a single invoice with fresh status from PayPal."""
        row = db.get_invoice(invoice_id)
        if not row:
            return jsonify({"error": "Invoice not found."}), 404

        stale = False
        paypal_id = row.get("paypal_invoice_id")
        raw_status = row.get("last_status", "")

        if paypal_id and raw_status not in ("PAID", "MARKED_AS_PAID", "CANCELLED", "CANCELED"):
            try:
                live_data = paypal_client.get_invoice(paypal_id)
                new_raw = live_data.get("raw_status")
                new_url = live_data.get("payer_url")
                new_num = live_data.get("invoice_number")
                if new_raw:
                    db.update_invoice_status(row["id"], new_raw, recipient_url=new_url, invoice_number=new_num)
                    row["last_status"] = new_raw
                    if new_url:
                        row["recipient_url"] = new_url
                    if new_num:
                        row["invoice_number"] = new_num
            except Exception as e:
                logger.warning("Failed live refresh for invoice %s: %s", invoice_id, e)
                stale = True

        return jsonify(services.format_invoice_response(row, stale=stale)), 200

    def _handle_make_overdue(invoice_id: int):
        """Common logic for simulating overdue status in demo mode."""
        if not config.DEMO_MODE:
            return jsonify({"error": "Demo mode is disabled."}), 403

        row = db.get_invoice(invoice_id)
        if not row:
            return jsonify({"error": "Invoice not found."}), 404

        override_date = (date.today() - timedelta(days=12)).isoformat()
        db.set_demo_due_override(invoice_id, override_date)

        updated = db.get_invoice(invoice_id)
        resp = services.format_invoice_response(updated)
        resp["demo_only"] = True
        resp["demo_due_override"] = override_date
        return jsonify(resp), 200

    @app.route("/api/demo/<int:invoice_id>/make-overdue", methods=["POST"])
    def demo_make_overdue(invoice_id: int):
        """Simulate overdue status (called by minimal test UI app.js)."""
        return _handle_make_overdue(invoice_id)

    @app.route("/api/invoices/<int:invoice_id>/simulate-overdue", methods=["POST"])
    def invoices_simulate_overdue(invoice_id: int):
        """Simulate overdue status (endpoint alias)."""
        return _handle_make_overdue(invoice_id)

    @app.route("/api/invoices/<int:invoice_id>/reminder-draft", methods=["POST"])
    def reminder_draft(invoice_id: int):
        """Generate a contextual payment reminder draft for human approval."""
        row = db.get_invoice(invoice_id)
        if not row:
            return jsonify({"error": "Invoice not found."}), 404

        raw_status = (row.get("last_status") or "").strip().upper()
        if raw_status in ("PAID", "MARKED_AS_PAID", "CANCELLED", "CANCELED"):
            return jsonify({"error": "Cannot draft a reminder for a paid or cancelled invoice."}), 400

        data = request.get_json(silent=True) or {}
        requested_tone = data.get("tone")

        draft = ai_client.draft_reminder(row, tone=requested_tone)
        return jsonify(draft), 200

    def _handle_send_reminder(invoice_id: int):
        """Shared logic for sending an approved payment reminder via PayPal."""
        row = db.get_invoice(invoice_id)
        if not row:
            return jsonify({"error": "Invoice not found."}), 404

        raw_status = (row.get("last_status") or "").strip().upper()
        if raw_status in ("PAID", "MARKED_AS_PAID", "CANCELLED", "CANCELED"):
            return jsonify({"error": "Cannot send a reminder for a paid or cancelled invoice."}), 400

        # Enforce rate limit of 2 reminders per invoice per day
        sent_today = db.count_daily_reminders(invoice_id)
        if sent_today >= 2:
            return jsonify({"error": "Daily reminder limit reached (maximum 2 reminders per day per invoice)."}), 429

        data = request.get_json(silent=True) or {}
        subject = data.get("subject", "").strip()
        note = data.get("note", "").strip()
        tone = data.get("tone")
        ai_mode = data.get("ai_mode") or "ai"

        # Auto-draft if subject or note was not provided
        if not subject or not note:
            auto_draft = ai_client.draft_reminder(row, tone=tone)
            subject = subject or auto_draft["subject"]
            note = note or auto_draft["note"]
            tone = tone or auto_draft["tone"]
            ai_mode = auto_draft.get("ai_mode") or "ai"

        paypal_id = row.get("paypal_invoice_id")
        if paypal_id:
            try:
                paypal_client.remind_invoice(paypal_id, subject, note)
            except paypal_client.PayPalError as e:
                logger.error("PayPal remind failed for invoice %s (%s): %s", invoice_id, paypal_id, e)
                db.insert_reminder(
                    invoice_id=invoice_id,
                    subject=subject,
                    note=note,
                    tone=tone,
                    ai_mode=ai_mode,
                    paypal_sent=False,
                    status="FAILED",
                )
                return jsonify({"error": f"Failed to send reminder via PayPal: {e.message}"}), 502

        rem_id = db.insert_reminder(
            invoice_id=invoice_id,
            subject=subject,
            note=note,
            tone=tone,
            ai_mode=ai_mode,
            paypal_sent=True,
            status="SENT",
        )

        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()

        return jsonify({
            "ok": True,
            "id": rem_id,
            "invoice_id": invoice_id,
            "subject": subject,
            "note": note,
            "tone": tone,
            "ai_mode": ai_mode,
            "sent_at": now_iso,
        }), 200

    @app.route("/api/invoices/<int:invoice_id>/reminder-send", methods=["POST"])
    def invoice_reminder_send(invoice_id: int):
        """Send an approved reminder (called by minimal test UI app.js)."""
        return _handle_send_reminder(invoice_id)

    @app.route("/api/invoices/<int:invoice_id>/remind", methods=["POST"])
    def invoice_remind(invoice_id: int):
        """Send a reminder (endpoint alias with auto-drafting fallback)."""
        return _handle_send_reminder(invoice_id)

    @app.route("/api/invoices/<int:invoice_id>/reminders", methods=["GET"])
    def get_invoice_reminders(invoice_id: int):
        """Retrieve sent reminder history for an invoice."""
        reminders = db.list_reminders(invoice_id=invoice_id)
        return jsonify({"reminders": reminders}), 200

    @app.route("/api/reminders", methods=["GET"])
    def get_all_reminders():
        """Retrieve all reminder history across all invoices."""
        reminders = db.list_reminders()
        return jsonify({"reminders": reminders}), 200

    @app.route("/api/summary", methods=["GET"])
    def get_summary():
        """Return flat financial summary cards from local invoices."""
        rows = db.list_invoices()
        summary = services.compute_summary(rows)
        return jsonify(summary), 200

    @app.errorhandler(HTTPException)
    def handle_http_exception(e: HTTPException):
        """Return HTTP errors as clean JSON."""
        return jsonify({"error": e.description or e.name}), e.code

    @app.errorhandler(Exception)
    def handle_generic_exception(e: Exception):
        """Catch unhandled exceptions and return safe JSON without stack traces."""
        logger.exception("Unhandled server error: %s", e)
        return jsonify({"error": "An internal server error occurred."}), 500

    return app


app = create_app()

if __name__ == "__main__":
    logger.info("Starting BillPilot server on port %s (debug=%s)", config.PORT, config.FLASK_DEBUG)
    app.run(host="127.0.0.1", port=config.PORT, debug=config.FLASK_DEBUG)
