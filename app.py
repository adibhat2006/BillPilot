"""Flask application entry point for BillPilot.

Serves the minimal test UI and REST API endpoints.
Enforces JSON responses for errors and exposes GET /api/health and POST /api/extract.
"""

import logging
from datetime import date
from flask import Flask, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

import config
from db import init_db
import ai_client
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
    init_db()

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
