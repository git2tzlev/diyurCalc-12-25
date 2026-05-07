# -*- coding: utf-8 -*-
"""בדיקות לריכוז ברירות המחדל שנוצרות בעליית המערכת."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core import runtime_defaults


class _FakeConnectionWrapper:
    conn = "raw-connection"


class _FakeConnectionManager:
    def __enter__(self):
        return _FakeConnectionWrapper()

    def __exit__(self, exc_type, exc, tb):
        return False


class TestRuntimeDefaults(unittest.TestCase):
    def test_current_database_ensures_payment_codes_schema_and_email_logs(self):
        with (
            patch.object(runtime_defaults, "get_conn", return_value=_FakeConnectionManager()),
            patch.object(runtime_defaults, "ensure_sick_payment_code") as sick,
            patch.object(runtime_defaults, "ensure_professional_support_code") as support,
            patch.object(runtime_defaults, "ensure_holiday_payment_code") as holiday,
            patch.object(runtime_defaults, "ensure_holiday_payment_assignments_table") as assignments,
            patch.object(runtime_defaults, "ensure_special_days_holiday_payment_column") as special_day,
            patch.object(runtime_defaults, "ensure_email_logs_table") as email_logs,
        ):
            runtime_defaults.ensure_runtime_defaults_for_current_database()

        for mock in (sick, support, holiday, assignments, special_day, email_logs):
            mock.assert_called_once_with("raw-connection")

    def test_demo_defaults_temporarily_switch_demo_mode(self):
        with (
            patch.object(runtime_defaults, "ensure_runtime_defaults_for_current_database") as ensure_current,
            patch.object(runtime_defaults, "set_demo_mode") as set_demo_mode,
        ):
            runtime_defaults.ensure_runtime_defaults(include_demo=True)

        self.assertEqual(ensure_current.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in set_demo_mode.call_args_list],
            [True, False],
        )


if __name__ == "__main__":
    unittest.main()
