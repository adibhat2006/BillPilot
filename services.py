"""Business logic, status mapping, and orchestration services for BillPilot.

Encapsulates status translation between PayPal raw statuses and application badges,
as well as overdue derivation based on effective due date and current date.
"""

from datetime import date
from typing import Optional


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
    """Map raw PayPal status to application status (Paid, Unpaid, Overdue, Draft, Cancelled).
    
    Rules:
      - PAID, MARKED_AS_PAID -> Paid
      - CANCELLED, CANCELED -> Cancelled
      - DRAFT -> Draft
      - SENT, UNPAID, PARTIALLY_PAID, PAYMENT_PENDING, SCHEDULED ->
          Overdue if effective_due_date < today
          Unpaid otherwise (due today is NOT overdue)
      - unknown status -> raw status string
    """
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
            # If effective due date is strictly before today, it is Overdue.
            # Due today is NOT overdue.
            if due_clean < today_str:
                return "Overdue"
        return "Unpaid"

    # Return raw status as fallback for unrecognized values
    return raw_status
