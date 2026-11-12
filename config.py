"""Configuration loader for BillPilot.

Loads environment variables from .env and exposes application settings.
Defaults allow the application to start even when credentials are missing,
enabling health checks and graceful fallback reporting.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env file from project root if present
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# PayPal settings (Sandbox only)
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_BASE_URL = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com").rstrip("/")
SANDBOX_PAYER_EMAIL = os.getenv("SANDBOX_PAYER_EMAIL", "")

# AI settings
AI_PROVIDER = os.getenv("AI_PROVIDER", "none").strip().lower()
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "")

# Business & currency rules
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "USD").strip().upper()
_allowed_currencies_raw = os.getenv("ALLOWED_CURRENCIES", "USD")
ALLOWED_CURRENCIES = [
    c.strip().upper()
    for c in _allowed_currencies_raw.split(",")
    if c.strip()
] or ["USD"]

try:
    MAX_AMOUNT = float(os.getenv("MAX_AMOUNT", "50000.0"))
except ValueError:
    MAX_AMOUNT = 50000.0

# Demo & server configuration
DEMO_MODE = os.getenv("DEMO_MODE", "true").strip().lower() in ("true", "1", "yes")
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "false").strip().lower() in ("true", "1", "yes")
PORT = int(os.getenv("PORT", "5000"))

# Database path
DB_PATH = os.getenv("DB_PATH", str(BASE_DIR / "billpilot.db"))

def is_paypal_configured() -> bool:
    """Check if PayPal sandbox credentials are provided."""
    return bool(PAYPAL_CLIENT_ID.strip() and PAYPAL_CLIENT_SECRET.strip())

def is_ai_configured() -> bool:
    """Check if an active AI provider has credentials configured."""
    if AI_PROVIDER in ("anthropic", "gemini"):
        return bool(AI_API_KEY.strip())
    # If provider is 'none', fallback mode is considered configured
    return AI_PROVIDER == "none"
