"""Database access and schema initialization for BillPilot.

Uses sqlite3 from the standard library with parameterized queries only.
Initializes invoices, reminders, and clients tables as defined in the data model.
"""

import sqlite3
from typing import Optional
from config import DB_PATH

def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Return a database connection configured with Row factory and FK enforcement."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def init_db(db_path: Optional[str] = None) -> None:
    """Create database tables if they do not already exist."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS invoices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT UNIQUE,
                    paypal_invoice_id TEXT UNIQUE,
                    invoice_number TEXT,
                    client_name TEXT NOT NULL,
                    client_email TEXT,
                    currency TEXT NOT NULL,
                    total REAL NOT NULL,
                    due_date TEXT,
                    demo_due_override TEXT,
                    source_text TEXT,
                    extracted_json TEXT,
                    ai_mode TEXT,
                    recipient_url TEXT,
                    last_status TEXT,
                    last_synced_at TEXT,
                    created_at TEXT NOT NULL
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    invoice_id INTEGER NOT NULL,
                    tone TEXT,
                    subject TEXT NOT NULL,
                    note TEXT NOT NULL,
                    ai_mode TEXT,
                    sent_at TEXT NOT NULL,
                    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
                );
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    email TEXT
                );
            """)
    finally:
        conn.close()
