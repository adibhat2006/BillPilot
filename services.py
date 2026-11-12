"""Business logic, status mapping, validation, and fallback extraction for BillPilot.

Encapsulates:
  - Status translation between PayPal raw statuses and application badges
  - Overdue derivation based on effective due date and current date
  - Extraction validation adhering to financial and format constraints
  - Fallback deterministic regex parser for offline / unconfigured operation
"""

import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import config


def compute_effective_due_date(due_date: Optional[str], demo_due_override: Optional[str] = None) -> Optional[str]:
    """Return the effective due date, preferring demo_due_override if set."""
    if demo_due_override and demo_due_override.strip():
        return demo_due_override.strip()
    return due_date.strip() if due_date else None


def map_status(
    raw_status: Optional[str],
    effective_due_date: Optional[str] = None,
    today: Optional[str] = None,
) -> str:
    """Map raw PayPal status to application status (Paid, Unpaid, Overdue, Draft, Cancelled)."""
    if not raw_status:
        return "Unpaid"

    status_upper = raw_status.strip().upper()

    if status_upper in ("PAID", "MARKED_AS_PAID"):
        return "Paid"

    if status_upper in ("CANCELLED", "CANCELED"):
        return "Cancelled"

    if status_upper == "DRAFT":
        return "Draft"

    unpaid_statuses = (
        "SENT",
        "UNPAID",
        "PARTIALLY_PAID",
        "PAYMENT_PENDING",
        "SCHEDULED",
    )

    if status_upper in unpaid_statuses:
        if effective_due_date:
            today_str = today or date.today().isoformat()
            due_clean = effective_due_date.strip()
            if due_clean < today_str:
                return "Overdue"
        return "Unpaid"

    return raw_status


def resolve_relative_date(date_expr: Optional[str], today: Optional[str] = None) -> Optional[str]:
    """Resolve a relative or natural language date string into an ISO YYYY-MM-DD date."""
    if not date_expr:
        return None

    ref_date = date.fromisoformat(today) if today else date.today()
    clean = date_expr.strip().lower().rstrip(".,")

    if clean in ("today",):
        return ref_date.isoformat()
    if clean in ("tomorrow",):
        return (ref_date + timedelta(days=1)).isoformat()

    # Match 'in N days' or 'N days'
    m_in_days = re.search(r"(?:in\s+)?(\d+)\s+days?", clean)
    if m_in_days:
        num_days = int(m_in_days.group(1))
        return (ref_date + timedelta(days=num_days)).isoformat()

    # Match weekday names
    weekdays = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    for wname, wnum in weekdays.items():
        if wname in clean:
            days_ahead = (wnum - ref_date.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            return (ref_date + timedelta(days=days_ahead)).isoformat()

    # Match 'Month Day' (e.g., 'Nov 30', 'November 30', 'Nov 30th')
    months = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
    }
    m_month = re.search(r"([a-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?", clean)
    if m_month:
        month_name, day_num = m_month.groups()
        for prefix, month_int in months.items():
            if prefix in month_name:
                try:
                    d_parsed = date(ref_date.year, month_int, int(day_num))
                    if d_parsed < ref_date:
                        d_parsed = date(ref_date.year + 1, month_int, int(day_num))
                    return d_parsed.isoformat()
                except ValueError:
                    return None

    # Check direct ISO date format YYYY-MM-DD
    try:
        return date.fromisoformat(clean).isoformat()
    except Exception:
        return None


def parse_fallback_extraction(
    text: str,
    today: Optional[str] = None,
    default_currency: str = "USD",
) -> Dict[str, Any]:
    """Deterministic regex extraction fallback for common freelancer invoicing sentences.
    
    Handles:
      - 'Invoice <Client> $<amount> for <item>, due <date>'
      - 'Bill <Client> <qty> <unit> of <item> at $<price> per hour, due <date>'
      - Multi-item syntax like '... $1200 for website, 2 revisions $50 each, due Nov 30'
      - Sentences with missing due dates or missing emails
    """
    raw = text.strip()
    ref_today = today or date.today().isoformat()

    # 1. Extract due date clause if present at end of sentence
    due_date = None
    m_due = re.search(r",?\s*(?:due|by)\s+([^,]+)$", raw, re.IGNORECASE)
    if m_due:
        due_str = m_due.group(1).strip()
        due_date = resolve_relative_date(due_str, ref_today)
        body = raw[:m_due.start()].strip()
    else:
        body = raw

    # 2. Extract client email if present in text and remove from body for clean name parsing
    client_email = None
    m_email = re.search(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", raw)
    if m_email:
        client_email = m_email.group(0)
        body = re.sub(r"\s*(?:at\s+)?" + re.escape(client_email), "", body).strip()

    # 3. Split multi-item clauses separated by commas
    parts = [p.strip() for p in body.split(",") if p.strip()]
    first_part = parts[0] if parts else ""
    secondary_parts = parts[1:] if len(parts) > 1 else []

    client_name = None
    items: List[Dict[str, Any]] = []

    # Check hourly/quantity rate pattern:
    # "Bill Asha 3 hours of tutoring at $20 per hour"
    m_rate = re.match(
        r"^(?:bill|invoice)\s+([A-Za-z0-9\s]+?)\s+(\d+)\s+([A-Za-z]+)\s+of\s+(.+?)\s+at\s+\$?(\d+(?:\.\d+)?)(?:\s+per\s+[A-Za-z]+)?$",
        first_part,
        re.IGNORECASE,
    )

    if m_rate:
        c_name, qty_str, unit, item_desc, price_str = m_rate.groups()
        client_name = c_name.strip()
        items.append({
            "name": item_desc.strip(),
            "quantity": int(qty_str),
            "unit_price": float(price_str),
        })
    else:
        # Check standard pattern:
        # "Invoice Ravi $500 for logo design" or "Invoice Mehta Traders $1200 for website"
        m_std = re.match(
            r"^(?:invoice|bill)\s+([A-Za-z0-9\s]+?)\s+\$?(\d+(?:\.\d+)?)(?:\s+for\s+(.+))?$",
            first_part,
            re.IGNORECASE,
        )
        if m_std:
            c_name, amt_str, item_desc = m_std.groups()
            client_name = c_name.strip()
            item_name = item_desc.strip() if item_desc else "Service"
            items.append({
                "name": item_name,
                "quantity": 1,
                "unit_price": float(amt_str),
            })
        else:
            # Check simple "Invoice <Name>" pattern
            m_name_only = re.match(r"^(?:invoice|bill)\s+([A-Za-z0-9\s]+)$", first_part, re.IGNORECASE)
            if m_name_only:
                client_name = m_name_only.group(1).strip()

    # Parse secondary item clauses: "2 revisions $50 each"
    for sec in secondary_parts:
        m_sec = re.match(r"^(\d+)\s+(.+?)\s+\$?(\d+(?:\.\d+)?)\s+each$", sec, re.IGNORECASE)
        if m_sec:
            s_qty, s_name, s_price = m_sec.groups()
            items.append({
                "name": s_name.strip(),
                "quantity": int(s_qty),
                "unit_price": float(s_price),
            })

    # Track missing fields
    missing_fields = []
    if not client_email:
        missing_fields.append("client_email")
    if not due_date:
        missing_fields.append("due_date")
    if not items:
        missing_fields.append("items")

    confidence = 0.85 if (client_name and items and due_date) else (0.5 if items else 0.1)

    return {
        "client_name": client_name,
        "client_email": client_email,
        "items": items,
        "currency": default_currency.upper(),
        "due_date": due_date,
        "note": None,
        "missing_fields": missing_fields,
        "confidence": confidence,
        "ai_mode": "fallback",
    }


def validate_extraction(data: Dict[str, Any], today: Optional[str] = None) -> List[str]:
    """Validate extraction dictionary according to business rules.
    
    Returns a list of readable error messages. Never raises an exception.
    """
    errors: List[str] = []
    ref_today = today or date.today().isoformat()

    # 1. Validate Client Name
    client_name = data.get("client_name")
    if not client_name or not isinstance(client_name, str) or not client_name.strip():
        errors.append("Client name is required.")
    elif len(client_name.strip()) > 100:
        errors.append("Client name cannot exceed 100 characters.")

    # 2. Validate Items
    items = data.get("items")
    if not isinstance(items, list) or len(items) == 0:
        errors.append("At least one item is required.")
    else:
        total_amount = 0.0
        for idx, item in enumerate(items, 1):
            if not isinstance(item, dict):
                errors.append(f"Item #{idx} is invalid.")
                continue

            name = item.get("name")
            qty = item.get("quantity")
            price = item.get("unit_price")

            if not name or not isinstance(name, str) or not name.strip():
                errors.append(f"Item #{idx} name cannot be empty.")
            elif len(name.strip()) > 150:
                errors.append(f"Item #{idx} name cannot exceed 150 characters.")

            if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
                errors.append(f"Item #{idx} quantity must be a whole number, 1 or more.")

            try:
                price_float = float(price)
                if price_float <= 0:
                    errors.append(f"Item #{idx} unit price must be greater than 0.")
                elif price_float > config.MAX_AMOUNT:
                    errors.append(f"Item #{idx} unit price exceeds the maximum limit of {config.MAX_AMOUNT:,.2f}.")
                else:
                    if isinstance(qty, int) and qty >= 1:
                        total_amount += qty * price_float
            except (TypeError, ValueError):
                errors.append(f"Item #{idx} unit price must be a valid number.")

        if total_amount > config.MAX_AMOUNT:
            errors.append(f"Total amount ({total_amount:,.2f}) exceeds the maximum limit of {config.MAX_AMOUNT:,.2f}.")

    # 3. Validate Currency
    currency = data.get("currency")
    curr_clean = (currency or "").strip().upper()
    if curr_clean not in config.ALLOWED_CURRENCIES:
        errors.append(f"Currency '{curr_clean}' is not supported. Allowed: {', '.join(config.ALLOWED_CURRENCIES)}.")

    # 4. Validate Due Date
    due_date = data.get("due_date")
    if due_date:
        try:
            d_val = date.fromisoformat(str(due_date).strip())
            d_ref = date.fromisoformat(ref_today)
            if d_val < d_ref:
                errors.append("Due date cannot be in the past.")
        except (ValueError, TypeError):
            errors.append("Due date must be in YYYY-MM-DD format.")

    # 5. Validate Email (if present; missing email is not an error)
    email = data.get("client_email")
    if email:
        email_clean = str(email).strip()
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email_clean):
            errors.append("Enter a valid client email.")
        elif len(email_clean) > 254:
            errors.append("Client email cannot exceed 254 characters.")

    # 6. Validate Note length
    note = data.get("note")
    if note and len(str(note)) > 500:
        errors.append("Note cannot exceed 500 characters.")

    return errors
