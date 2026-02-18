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

from core.config import config

logger = logging.getLogger(__name__)

# Connection pools - initialized lazily
_prod_pool: Optional[pool.ThreadedConnectionPool] = None
_demo_pool: Optional[pool.ThreadedConnectionPool] = None

# Context variable to track demo mode per request
_demo_mode: ContextVar[bool] = ContextVar('demo_mode', default=False)

# Context variable to track housing array filter per request
_housing_array_filter: ContextVar[Optional[int]] = ContextVar('housing_array_filter', default=None)


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
    return PostgresConnection(pg_conn, use_pool=True, is_demo=is_demo)


def get_current_db_name() -> str:
    """Get the name of the current database (for display purposes)."""
    if is_demo_mode():
        return "פיתוח"
    return "עבודה"


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