from unittest.mock import patch

from core.logic import get_available_months_for_person


class _Cursor:
    def __init__(self):
        self.sql = ""
        self.params = ()

    def execute(self, sql, params):
        self.sql = sql
        self.params = params

    def fetchall(self):
        return [(2026, 8), (2026, 6)]

    def close(self):
        pass


class _Connection:
    def __init__(self):
        self.db_cursor = _Cursor()

    def cursor(self):
        return self.db_cursor


def test_available_months_include_payment_months_without_work_in_that_month():
    conn = _Connection()

    with patch("core.logic.get_housing_array_filter", return_value=None):
        months = get_available_months_for_person(conn, 436)

    assert months == [(2026, 8), (2026, 6)]
    assert "payment_year" in conn.db_cursor.sql
    assert "payment_month" in conn.db_cursor.sql
