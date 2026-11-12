"""Database access, schema initialization, and repository functions for BillPilot.

Uses sqlite3 with parameterized queries only.
Provides repository helpers for invoices, clients, and reminders.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import config


def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Return a database connection configured with Row factory and FK enforcement."""
    path = db_path or config.DB_PATH
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
                    channel TEXT DEFAULT 'paypal_email',
                    tone TEXT,
                    subject TEXT NOT NULL,
                    note TEXT NOT NULL,
                    ai_mode TEXT DEFAULT 'ai',
                    paypal_sent INTEGER DEFAULT 1,
                    status TEXT DEFAULT 'SENT',
                    sent_at TEXT NOT NULL,
                    FOREIGN KEY (invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
                );
            """)

            # Ensure any newly added columns exist in older database files
            columns = [row["name"] for row in conn.execute("PRAGMA table_info(reminders)").fetchall()]
            if "channel" not in columns:
                conn.execute("ALTER TABLE reminders ADD COLUMN channel TEXT DEFAULT 'paypal_email';")
            if "paypal_sent" not in columns:
                conn.execute("ALTER TABLE reminders ADD COLUMN paypal_sent INTEGER DEFAULT 1;")
            if "status" not in columns:
                conn.execute("ALTER TABLE reminders ADD COLUMN status TEXT DEFAULT 'SENT';")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS clients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    email TEXT
                );
            """)
    finally:
        conn.close()


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    """Convert an sqlite3.Row to a standard python dictionary."""
    if row is None:
        return None
    return dict(row)


# ---------------- Invoices Repository ----------------

def insert_invoice(data: Dict[str, Any], db_path: Optional[str] = None) -> int:
    """Insert a new invoice record and return its local ID."""
    conn = get_connection(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO invoices (
                    idempotency_key, paypal_invoice_id, invoice_number,
                    client_name, client_email, currency, total,
                    due_date, demo_due_override, source_text,
                    extracted_json, ai_mode, recipient_url,
                    last_status, last_synced_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data.get("idempotency_key"),
                    data.get("paypal_invoice_id"),
                    data.get("invoice_number"),
                    data["client_name"],
                    data.get("client_email"),
                    data.get("currency", "USD"),
                    float(data.get("total", 0.0)),
                    data.get("due_date"),
                    data.get("demo_due_override"),
                    data.get("source_text"),
                    json.dumps(data.get("extracted_json")) if data.get("extracted_json") else None,
                    data.get("ai_mode", "ai"),
                    data.get("recipient_url"),
                    data.get("last_status", "DRAFT"),
                    now_iso,
                    now_iso,
                ),
            )
            return cursor.lastrowid
    finally:
        conn.close()


def get_invoice(invoice_id: int, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetch an invoice by its local primary key."""
    conn = get_connection(db_path)
    try:
        cursor = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,))
        return row_to_dict(cursor.fetchone())
    finally:
        conn.close()


def get_invoice_by_idempotency_key(key: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetch an existing invoice by idempotency key to prevent double submits."""
    if not key:
        return None
    conn = get_connection(db_path)
    try:
        cursor = conn.execute("SELECT * FROM invoices WHERE idempotency_key = ?", (key,))
        return row_to_dict(cursor.fetchone())
    finally:
        conn.close()


def list_invoices(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieve all invoices ordered newest first."""
    conn = get_connection(db_path)
    try:
        cursor = conn.execute("SELECT * FROM invoices ORDER BY id DESC")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def update_invoice_status(
    invoice_id: int,
    raw_status: str,
    recipient_url: Optional[str] = None,
    invoice_number: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    """Update PayPal status, synced timestamp, and optional recipient URL / number."""
    conn = get_connection(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with conn:
            if recipient_url and invoice_number:
                conn.execute(
                    """
                    UPDATE invoices
                    SET last_status = ?, recipient_url = ?, invoice_number = ?, last_synced_at = ?
                    WHERE id = ?
                    """,
                    (raw_status, recipient_url, invoice_number, now_iso, invoice_id),
                )
            elif recipient_url:
                conn.execute(
                    """
                    UPDATE invoices
                    SET last_status = ?, recipient_url = ?, last_synced_at = ?
                    WHERE id = ?
                    """,
                    (raw_status, recipient_url, now_iso, invoice_id),
                )
            else:
                conn.execute(
                    """
                    UPDATE invoices
                    SET last_status = ?, last_synced_at = ?
                    WHERE id = ?
                    """,
                    (raw_status, now_iso, invoice_id),
                )
    finally:
        conn.close()


def delete_invoice(invoice_id: int, db_path: Optional[str] = None) -> None:
    """Delete an invoice record by local ID (e.g. rolling back an uncompleted reservation)."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
    finally:
        conn.close()


def update_invoice_paypal_info(
    invoice_id: int,
    paypal_invoice_id: Optional[str],
    raw_status: str,
    recipient_url: Optional[str] = None,
    invoice_number: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    """Update PayPal-related identifiers, status, and URL on an invoice record."""
    conn = get_connection(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with conn:
            conn.execute(
                """
                UPDATE invoices
                SET paypal_invoice_id = ?, last_status = ?, recipient_url = ?,
                    invoice_number = ?, last_synced_at = ?
                WHERE id = ?
                """,
                (paypal_invoice_id, raw_status, recipient_url, invoice_number, now_iso, invoice_id),
            )
    finally:
        conn.close()


def set_demo_due_override(
    invoice_id: int,
    override_date: str,
    db_path: Optional[str] = None,
) -> None:
    """Set the demo_due_override date for an invoice."""
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                "UPDATE invoices SET demo_due_override = ? WHERE id = ?",
                (override_date, invoice_id),
            )
    finally:
        conn.close()


# ---------------- Reminders Repository ----------------

def insert_reminder(
    invoice_id: int,
    subject: str,
    note: str,
    tone: Optional[str] = None,
    ai_mode: str = "ai",
    channel: str = "paypal_email",
    paypal_sent: bool = True,
    status: str = "SENT",
    db_path: Optional[str] = None,
) -> int:
    """Insert a reminder record and return its local ID."""
    conn = get_connection(db_path)
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO reminders (
                    invoice_id, channel, tone, subject, note,
                    ai_mode, paypal_sent, status, sent_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    invoice_id,
                    channel,
                    tone,
                    subject,
                    note,
                    ai_mode,
                    1 if paypal_sent else 0,
                    status,
                    now_iso,
                ),
            )
            return cursor.lastrowid
    finally:
        conn.close()


def list_reminders(
    invoice_id: Optional[int] = None,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieve reminder records, optionally filtered by invoice_id, newest first."""
    conn = get_connection(db_path)
    try:
        if invoice_id:
            cursor = conn.execute(
                "SELECT * FROM reminders WHERE invoice_id = ? ORDER BY id DESC",
                (invoice_id,),
            )
        else:
            cursor = conn.execute("SELECT * FROM reminders ORDER BY id DESC")
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def count_daily_reminders(
    invoice_id: int,
    day_str: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """Count how many reminders have been sent today for a given invoice."""
    conn = get_connection(db_path)
    day = day_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        cursor = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM reminders
            WHERE invoice_id = ? AND date(sent_at) = ? AND paypal_sent = 1
            """,
            (invoice_id, day),
        )
        row = cursor.fetchone()
        return int(row["count"]) if row else 0
    finally:
        conn.close()


def upsert_client(name: str, email: Optional[str], db_path: Optional[str] = None) -> None:
    """Insert or update a client by name."""
    if not name or not name.strip():
        return
    conn = get_connection(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO clients (name, email)
                VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET email = COALESCE(excluded.email, clients.email)
                """,
                (name.strip(), email.strip() if email else None),
            )
    finally:
        conn.close()


def find_client_by_name(name: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Look up a client by exact name."""
    if not name or not name.strip():
        return None
    conn = get_connection(db_path)
    try:
        cursor = conn.execute("SELECT * FROM clients WHERE name = ?", (name.strip(),))
        return row_to_dict(cursor.fetchone())
    finally:
        conn.close()
