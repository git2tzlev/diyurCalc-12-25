"""Runtime database defaults that must exist before the app serves traffic."""
from __future__ import annotations

import logging

from core.database import get_conn, set_demo_mode
from core.holiday_payment import (
    ensure_holiday_payment_assignments_table,
    ensure_special_days_holiday_payment_column,
)
from core.logic import (
    ensure_holiday_payment_code,
    ensure_professional_support_code,
    ensure_sick_payment_code,
)
from core.time_reports_audit import ensure_time_reports_audit_columns
from core.payment_period import ensure_payment_period_columns
from services.email_service import ensure_email_logs_table
from services.gesher_archive import ensure_gesher_export_files_table

logger = logging.getLogger(__name__)


def ensure_runtime_defaults_for_current_database() -> None:
    """Ensure required rows and additive schema defaults in the active DB."""
    with get_conn() as conn:
        ensure_sick_payment_code(conn.conn)
        ensure_professional_support_code(conn.conn)
        ensure_holiday_payment_code(conn.conn)
        ensure_holiday_payment_assignments_table(conn.conn)
        ensure_special_days_holiday_payment_column(conn.conn)
        ensure_email_logs_table(conn.conn)
        ensure_gesher_export_files_table(conn.conn)
        ensure_time_reports_audit_columns(conn.conn)
        ensure_payment_period_columns(conn.conn)


def ensure_runtime_defaults(include_demo: bool = True) -> None:
    """Ensure runtime defaults in production, and optionally demo, databases."""
    try:
        ensure_runtime_defaults_for_current_database()
    except Exception:
        logger.warning("Could not ensure runtime defaults on startup", exc_info=True)

    if not include_demo:
        return

    try:
        set_demo_mode(True)
        ensure_runtime_defaults_for_current_database()
    except Exception:
        logger.debug("Could not ensure runtime defaults in demo DB", exc_info=True)
    finally:
        set_demo_mode(False)
