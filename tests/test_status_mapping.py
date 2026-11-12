"""Unit tests for status mapping and overdue derivation."""

import pytest
from services import map_status, compute_effective_due_date


def test_paid_statuses():
    today = "2026-11-12"
    past_due = "2026-11-01"

    # Paid should always map to Paid, even if due date was in the past
    assert map_status("PAID", past_due, today=today) == "Paid"
    assert map_status("paid", past_due, today=today) == "Paid"
    assert map_status("MARKED_AS_PAID", past_due, today=today) == "Paid"


def test_cancelled_statuses():
    today = "2026-11-12"
    assert map_status("CANCELLED", "2026-11-10", today=today) == "Cancelled"
    assert map_status("CANCELED", "2026-11-10", today=today) == "Cancelled"
    assert map_status("cancelled", today=today) == "Cancelled"


def test_draft_status():
    today = "2026-11-12"
    assert map_status("DRAFT", "2026-11-10", today=today) == "Draft"
    assert map_status("draft", today=today) == "Draft"


def test_due_today_is_not_overdue():
    today = "2026-11-12"
    due_today = "2026-11-12"

    for st in ("SENT", "UNPAID", "PARTIALLY_PAID", "PAYMENT_PENDING", "SCHEDULED"):
        assert map_status(st, due_today, today=today) == "Unpaid"


def test_due_future_is_not_overdue():
    today = "2026-11-12"
    future_due = "2026-11-13"

    for st in ("SENT", "UNPAID", "PARTIALLY_PAID", "PAYMENT_PENDING", "SCHEDULED"):
        assert map_status(st, future_due, today=today) == "Unpaid"


def test_due_past_is_overdue():
    today = "2026-11-12"
    past_due = "2026-11-11"  # yesterday

    for st in ("SENT", "UNPAID", "PARTIALLY_PAID", "PAYMENT_PENDING", "SCHEDULED"):
        assert map_status(st, past_due, today=today) == "Overdue"


def test_no_due_date_defaults_to_unpaid():
    today = "2026-11-12"
    assert map_status("SENT", None, today=today) == "Unpaid"
    assert map_status("UNPAID", "", today=today) == "Unpaid"


def test_unknown_status_passes_through():
    assert map_status("REFUNDED", "2026-11-01", today="2026-11-12") == "REFUNDED"
    assert map_status("CUSTOM_STATUS", "2026-11-01", today="2026-11-12") == "CUSTOM_STATUS"


def test_effective_due_date_override():
    original_due = "2026-11-20"
    demo_override = "2026-10-31"

    effective = compute_effective_due_date(original_due, demo_override)
    assert effective == "2026-10-31"

    # With override, status becomes overdue
    today = "2026-11-12"
    assert map_status("SENT", effective, today=today) == "Overdue"

    # Without override, status remains unpaid
    effective_normal = compute_effective_due_date(original_due, None)
    assert effective_normal == "2026-11-20"
    assert map_status("SENT", effective_normal, today=today) == "Unpaid"
