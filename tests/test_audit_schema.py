# -*- coding: utf-8 -*-
"""Tests for salary-sensitive audit schema setup."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import audit


class _FakeCursor:
    def __init__(self, existing_tables=None):
        self.existing_tables = set(existing_tables or audit.AUDITED_TABLES)
        self.executed = []
        self._last_regclass = None
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "to_regclass" in sql:
            table_name = (params[0] or "").split(".")[-1]
            self._last_regclass = table_name if table_name in self.existing_tables else None

    def fetchone(self):
        return (self._last_regclass,)

    def close(self):
        self.closed = True


class _FakeConnection:
    def __init__(self, existing_tables=None):
        self.cursor_obj = _FakeCursor(existing_tables)
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class TestAuditSchema(unittest.TestCase):
    def test_salary_audit_schema_covers_salary_affecting_tables(self):
        conn = _FakeConnection()

        audit.ensure_salary_audit_schema(conn)

        sql_text = "\n".join(sql for sql, _params in conn.cursor_obj.executed)
        for table_name in audit.AUDITED_TABLES:
            self.assertIn(f"ALTER TABLE {table_name}", sql_text)
            self.assertIn(f"DROP TRIGGER IF EXISTS trg_audit_{table_name}", sql_text)
            self.assertIn(f"CREATE TRIGGER trg_audit_{table_name}", sql_text)

        self.assertTrue(conn.committed)
        self.assertFalse(conn.rolled_back)
        self.assertTrue(conn.cursor_obj.closed)

    def test_salary_audit_schema_skips_missing_optional_tables(self):
        existing = set(audit.AUDITED_TABLES) - {"special_days"}
        conn = _FakeConnection(existing)

        audit.ensure_salary_audit_schema(conn)

        sql_text = "\n".join(sql for sql, _params in conn.cursor_obj.executed)
        self.assertNotIn("DROP TRIGGER IF EXISTS trg_audit_special_days", sql_text)
        self.assertNotIn("CREATE TRIGGER trg_audit_special_days", sql_text)
        self.assertTrue(conn.committed)


if __name__ == "__main__":
    unittest.main()
