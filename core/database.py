"""
Database connection and utilities for DiyurCalc application.
Provides PostgreSQL connection wrapper and database utilities.
Uses connection pooling for better performance.
Supports switching between production and demo databases.
"""
from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from typing import Any, Optional

import psycopg2
import psycopg2.extras
from psycopg2 import pool

logger = logging.getLogger(__name__)

# Connection pools - initialized lazily
_prod_pool: Optional[pool.ThreadedConnectionPool] = None
_demo_pool: Optional[pool.ThreadedConnectionPool] = None

# Context variable to track demo mode per request
_demo_mode: ContextVar[bool] = ContextVar('demo_mode', default=False)

# Context variable to track housing array filter per request
_housing_array_filter: ContextVar[Optional[int]] = ContextVar('housing_array_filter', default=None)

# Context variable to track the authenticated user for DB audit triggers.
_db_actor_person_id: ContextVar[Optional[int]] = ContextVar('db_actor_person_id', default=None)


def is_demo_mode() -> bool:
    """Check if currently in demo mode."""
    return _demo_mode.get()


def set_demo_mode(enabled: bool) -> None:
    """Set demo mode for current context."""
    _demo_mode.set(enabled)


def get_housing_array_filter() -> Optional[int]:
    """מחזיר את מזהה מערך הדיור לסינון (None = כל המערכים)."""
    return _housing_array_filter.get()


def set_housing_array_filter(housing_array_id: Optional[int]) -> None:
    """מגדיר את מערך הדיור לסינון."""
    _housing_array_filter.set(housing_array_id)


def get_db_actor_person_id() -> Optional[int]:
    """מחזיר את מזהה המשתמש המחובר לצורך תיעוד DB."""
    return _db_actor_person_id.get()


def set_db_actor_person_id(person_id: Optional[int]) -> None:
    """מגדיר את מזהה המשתמש המחובר לצורך טריגרים ב-DB."""
    _db_actor_person_id.set(person_id)


def _apply_db_actor(conn) -> None:
    """Expose current request user to PostgreSQL triggers via app.current_user_id."""
    actor_id = get_db_actor_person_id()
    value = str(actor_id) if actor_id else ""
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT set_config('app.current_user_id', %s, false)", (value,))
    except Exception:
        logger.debug("Could not set DB actor context", exc_info=True)


def get_housing_array_from_cookie(request) -> Optional[int]:
    """מחלץ את מזהה מערך הדיור מעוגיית הבקשה."""
    cookie_value = request.cookies.get("housing_array_id", "")
    if cookie_value and cookie_value.isdigit():
        return int(cookie_value)
    return None


def get_demo_mode_from_cookie(request) -> bool:
    """Get demo mode setting from request cookie."""
    cookie_value = request.cookies.get("demo_mode", "false")
    return cookie_value.lower() == "true"


def get_selected_period_from_cookie(request) -> tuple[Optional[int], Optional[int]]:
    """
    מחלץ את החודש והשנה האחרונים שנבחרו מעוגיית הבקשה.

    Returns:
        (year, month) או (None, None) אם אין עוגייה
    """
    cookie_value = request.cookies.get("selected_period", "")
    if cookie_value:
        parts = cookie_value.split("-")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return int(parts[0]), int(parts[1])
    return None, None


def get_default_period(request) -> tuple[int, int]:
    """
    מחזיר את החודש והשנה לברירת מחדל.
    עדיפות: 1) מה שנשמר בעוגייה 2) החודש שלפני הנוכחי

    Returns:
        (year, month)
    """
    from datetime import datetime
    from core.config import config

    # נסה לקרוא מהעוגייה
    cookie_year, cookie_month = get_selected_period_from_cookie(request)
    if cookie_year and cookie_month:
        return cookie_year, cookie_month

    # ברירת מחדל: חודש קודם
    now = datetime.now(config.LOCAL_TZ)
    if now.month == 1:
        return now.year - 1, 12
    return now.year, now.month - 1


def _pool_kwargs(dsn: str) -> dict:
    """פרמטרים משותפים ליצירת pool עם TCP keepalive."""
    return {
        "dsn": dsn,
        "keepalives": 1,
        "keepalives_idle": 120,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    }


def _get_prod_pool() -> pool.ThreadedConnectionPool:
    """Get or create the production connection pool."""
    global _prod_pool
    if _prod_pool is None:
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            raise RuntimeError("DATABASE_URL environment variable is required")
        _prod_pool = pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            **_pool_kwargs(db_url)
        )
        logger.info("Production database connection pool created")
    return _prod_pool


def _get_demo_pool() -> pool.ThreadedConnectionPool:
    """Get or create the demo connection pool."""
    global _demo_pool
    if _demo_pool is None:
        db_url = os.getenv("DEMO_DATABASE_URL")
        if not db_url:
            raise RuntimeError("DEMO_DATABASE_URL environment variable is required for demo mode")
        _demo_pool = pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=5,
            **_pool_kwargs(db_url)
        )
        logger.info("Demo database connection pool created")
    return _demo_pool


def _get_pool() -> pool.ThreadedConnectionPool:
    """Get the appropriate connection pool based on demo mode."""
    if is_demo_mode():
        return _get_demo_pool()
    return _get_prod_pool()


def _is_conn_alive(conn) -> bool:
    """בדיקה מהירה אם חיבור DB עדיין פעיל."""
    try:
        if conn.closed:
            return False
        conn.cursor().execute("SELECT 1")
        return True
    except Exception:
        return False


def get_pooled_connection():
    """מחזיר חיבור תקין מה-pool. מחליף חיבור מת בחדש."""
    current_pool = _get_pool()
    conn = current_pool.getconn()

    if _is_conn_alive(conn):
        return conn

    # חיבור מת - סוגר ומבקש חדש
    logger.warning("Stale DB connection detected, replacing")
    try:
        current_pool.putconn(conn, close=True)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass

    conn = current_pool.getconn()
    return conn


def return_connection(conn, is_demo: bool = None):
    """Return a connection to the appropriate pool."""
    if is_demo is None:
        is_demo = is_demo_mode()

    if is_demo and _demo_pool is not None:
        _demo_pool.putconn(conn)
    elif not is_demo and _prod_pool is not None:
        _prod_pool.putconn(conn)


class PostgresConnection:
    """Wrapper for PostgreSQL connection to provide SQLite-like interface.
    Uses connection pooling for better performance."""

    def __init__(self, conn, use_pool: bool = True, is_demo: bool = False):
        self.conn = conn
        self._in_transaction = False
        self._use_pool = use_pool
        self._is_demo = is_demo

    def execute(self, query: str, params: tuple = ()) -> Any:
        """Execute a query and return a cursor-like object."""
        # Convert SQLite placeholders (?) to PostgreSQL (%s)
        query = query.replace("?", "%s")
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(query, params)
        return cursor

    def cursor(self, *args, **kwargs):
        """Allow raw access to cursors if needed (e.g. by logic.py functions)."""
        return self.conn.cursor(*args, **kwargs)

    def commit(self):
        if not self.conn.closed:
            self.conn.commit()

    def rollback(self):
        if not self.conn.closed:
            self.conn.rollback()

    def close(self):
        if self.conn.closed:
            return
        if self._use_pool:
            return_connection(self.conn, self._is_demo)
        else:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn.closed:
            return
        try:
            if exc_type is not None:
                self.rollback()
            else:
                self.commit()
        finally:
            self.close()


def get_conn() -> PostgresConnection:
    """Create and return a PostgreSQL database connection wrapped with SQLite-like interface.
    Uses connection pooling for better performance."""
    is_demo = is_demo_mode()
    pg_conn = get_pooled_connection()
    _apply_db_actor(pg_conn)
    return PostgresConnection(pg_conn, use_pool=True, is_demo=is_demo)


def get_current_db_name() -> str:
    """Get the name of the current database (for display purposes)."""
    if is_demo_mode():
        return "פיתוח"
    return "עבודה"


def get_multi_housing_guides(conn: "PostgresConnection", start_date, end_date) -> dict[int, list[str]]:
    """מחזיר מדריכים שעובדים ביותר ממערך דיור אחד בתקופה נתונה.

    מזהה לפי מספר עובד במירב (meirav_code) באותו מפעל, וגם לפי צירוף
    תעודת זהות + מספר עובד. כך אותו מספר עובד במפעלים שונים לא ייחסם,
    אבל אותו מספר עובד באותו מפעל או אותה תעודת זהות עם אותו מספר עובד
    בכמה רשומות כן יזוהו כאותו עובד.

    Returns:
        dict מ-person_id לרשימת שמות מערכי דיור
    """
    rows = conn.execute(
        """
        WITH report_scope AS (
            SELECT p.id,
                   p.id_number,
                   regexp_replace(COALESCE(p.meirav_code, ''), '\\D', '', 'g') AS employee_code,
                   COALESCE(e.code, '') AS employer_code,
                   ap.housing_array_id,
                   ha.name AS housing_array_name
            FROM time_reports tr
            JOIN people p ON p.id = tr.person_id
            LEFT JOIN employers e ON e.id = p.employer_id
            JOIN apartments ap ON ap.id = tr.apartment_id
            JOIN housing_arrays ha ON ha.id = ap.housing_array_id
            WHERE tr.date >= %s AND tr.date < %s
              AND regexp_replace(COALESCE(p.meirav_code, ''), '\\D', '', 'g') != ''
        ),
        duplicate_keys AS (
            SELECT 'employee_employer' AS key_type,
                   employee_code AS key_employee_code,
                   employer_code AS key_employer_code,
                   NULL::text AS key_id_number
            FROM report_scope
            GROUP BY employee_code, employer_code
            HAVING COUNT(DISTINCT housing_array_id) > 1

            UNION

            SELECT 'id_employee' AS key_type,
                   employee_code AS key_employee_code,
                   NULL::text AS key_employer_code,
                   id_number AS key_id_number
            FROM report_scope
            WHERE id_number IS NOT NULL AND id_number != ''
            GROUP BY id_number, employee_code
            HAVING COUNT(DISTINCT housing_array_id) > 1
        )
        SELECT array_agg(DISTINCT rs.id) AS person_ids,
               array_agg(DISTINCT rs.housing_array_name ORDER BY rs.housing_array_name) AS arrays
        FROM duplicate_keys dk
        JOIN report_scope rs
          ON rs.employee_code = dk.key_employee_code
         AND (
             (dk.key_type = 'employee_employer' AND rs.employer_code = dk.key_employer_code)
             OR
             (dk.key_type = 'id_employee' AND rs.id_number = dk.key_id_number)
         )
        GROUP BY dk.key_type, dk.key_employee_code, dk.key_employer_code, dk.key_id_number
        """,
        (start_date, end_date),
    ).fetchall()
    result: dict[int, list[str]] = {}
    for row in rows:
        for pid in row["person_ids"]:
            result[pid] = row["arrays"]
    return result


def close_all_pools():
    """Close all database connection pools. Used for graceful shutdown."""
    global _prod_pool, _demo_pool
    
    if _prod_pool:
        try:
            _prod_pool.closeall()
            logger.info("Production database pool closed")
        except Exception as e:
            logger.error(f"Error closing production pool: {e}")
        finally:
            _prod_pool = None
    
    if _demo_pool:
        try:
            _demo_pool.closeall()
            logger.info("Demo database pool closed")
        except Exception as e:
            logger.error(f"Error closing demo pool: {e}")
        finally:
            _demo_pool = None
