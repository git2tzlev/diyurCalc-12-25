# -*- coding: utf-8 -*-
"""בדיקות יצירת טבלת לוגי מייל."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.email_service import ensure_email_logs_table


class _WrapperConnection:
    def __init__(self):
        self.executed_sql = None
        self.committed = False

    def execute(self, sql):
        self.executed_sql = sql

    def commit(self):
        self.committed = True


class _RawCursor:
    def __init__(self):
        self.executed_sql = None
        self.closed = False

    def execute(self, sql):
        self.executed_sql = sql

    def close(self):
        self.closed = True


class _RawConnection:
    def __init__(self):
        self.cursor_obj = _RawCursor()
        self.committed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True


class TestEmailLogSchema(unittest.TestCase):
    def test_ensure_email_logs_table_supports_wrapper_connection(self):
        conn = _WrapperConnection()

        ensure_email_logs_table(conn)

        self.assertIn("CREATE TABLE IF NOT EXISTS email_logs", conn.executed_sql)
        self.assertTrue(conn.committed)

    def test_ensure_email_logs_table_supports_raw_psycopg_connection(self):
        conn = _RawConnection()

        ensure_email_logs_table(conn)

        self.assertIn("CREATE TABLE IF NOT EXISTS email_logs", conn.cursor_obj.executed_sql)
        self.assertTrue(conn.cursor_obj.closed)
        self.assertTrue(conn.committed)


if __name__ == "__main__":
    unittest.main()
