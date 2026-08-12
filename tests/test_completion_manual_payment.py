# -*- coding: utf-8 -*-
"""בדיקות להשלמות תומך מקצועי המשולמות ידנית ואינן יוצאות לגשר."""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.constants import MANUAL_COMPLETION_COMPONENT_TYPE_IDS, MANUAL_COMPLETION_SYMBOLS
from services import gesher_difference, gesher_exporter, salary_impact


def _professional_support_diff(amount: float = 150.0) -> dict:
    return {
        "internal_key": "professional_support",
        "symbol": "243",
        "employee_code": "1234",
        "employer_code": "001",
        "person_id": 1,
        "person_name": "אבי",
        "amount_diff": amount,
        "quantity_diff": 0.0,
        "rate": 0.0,
    }


def _manual_completion_row(amount: float = 150.0) -> dict:
    return {
        "employer_code": "001",
        "employee_code": "001234",
        "person_id": 1,
        "person_name": "אבי",
        "symbol": "243",
        "rate": amount,
        "amount": amount,
        "quantity": 0.0,
        "display_name": "תומך מקצועי - לתשלום ידני",
        "source_symbols": "243",
    }


def _regular_completion_row(amount: float = 90.0) -> dict:
    return {
        **_manual_completion_row(amount),
        "symbol": "253",
        "display_name": "הפרשי השלמות לא לפנסיה",
        "source_symbols": "371",
    }


class ManualCompletionTargetTests(unittest.TestCase):
    def test_professional_support_maps_to_manual_symbol(self):
        target = gesher_difference._completion_target_for_diff(_professional_support_diff())
        self.assertEqual(target, "243")

    def test_shared_constants_describe_the_manual_component(self):
        self.assertIn("243", MANUAL_COMPLETION_SYMBOLS)
        self.assertIn(13, MANUAL_COMPLETION_COMPONENT_TYPE_IDS)

    def test_mapping_is_by_internal_key_not_by_source_symbol(self):
        """עריכת סמל המקור במסך סמלי שכר לא אמורה לשבור את המיפוי."""
        diff = {**_professional_support_diff(), "symbol": "999"}
        self.assertEqual(gesher_difference._completion_target_for_diff(diff), "243")

    def test_professional_support_produces_a_completion_row(self):
        rows = gesher_difference.build_completion_gesher_rows([_professional_support_diff()])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "243")
        self.assertEqual(rows[0]["amount"], 150.0)

    def test_row_gets_the_manual_display_name(self):
        rows = gesher_difference.finalize_completion_rows(
            gesher_difference.build_completion_gesher_rows([_professional_support_diff()])
        )
        self.assertEqual(rows[0]["display_name"], "תומך מקצועי - לתשלום ידני")


class ManualCompletionIsNotExportedTests(unittest.TestCase):
    def test_manual_row_is_not_written_to_the_gesher_file(self):
        output = io.StringIO()
        written = gesher_exporter._write_completion_rows(
            output, [_manual_completion_row(), _regular_completion_row()]
        )
        self.assertEqual(written, 1)
        self.assertNotIn("243", output.getvalue())

    def test_regular_completion_row_is_still_written(self):
        output = io.StringIO()
        gesher_exporter._write_completion_rows(output, [_regular_completion_row()])
        self.assertIn("253", output.getvalue())

    def test_manual_row_is_not_shown_in_the_export_preview(self):
        preview = [{"person_id": 1, "name": "אבי", "meirav_code": "001234", "lines": []}]
        gesher_exporter.append_completion_rows_to_preview(
            preview, [_manual_completion_row(), _regular_completion_row()]
        )
        symbols = [line["symbol"] for line in preview[0]["lines"]]
        self.assertEqual(symbols, ["253"])

    def test_manual_row_alone_does_not_create_a_preview_card(self):
        preview = []
        gesher_exporter.append_completion_rows_to_preview(preview, [_manual_completion_row()])
        self.assertEqual(preview, [])


class ManualCompletionDoesNotTouchTotalsTests(unittest.TestCase):
    """אילוץ-העל: כל סכום שמוצג במערכת חייב להשתוות לקובץ הגשר."""

    def test_manual_symbol_is_not_registered_as_an_export_code(self):
        self.assertNotIn("243", gesher_exporter.COMPLETION_EXPORT_CODES)

    def test_manual_row_leaves_every_money_total_untouched(self):
        totals = {
            **gesher_exporter.empty_completion_totals(),
            "total_payment": 1000.0,
            "gesher_total": 1000.0,
            "display_total": 1000.0,
            "rounded_total": 1000.0,
        }
        before = dict(totals)
        gesher_exporter.add_completion_row_to_totals(totals, _manual_completion_row(150.0))
        self.assertEqual(totals, before)

    def test_regular_completion_still_updates_the_money_totals(self):
        totals = {**gesher_exporter.empty_completion_totals(), "total_payment": 1000.0}
        gesher_exporter.add_completion_row_to_totals(totals, _regular_completion_row(90.0))
        self.assertEqual(totals["completion_non_pension"], 90.0)
        self.assertEqual(totals["completion_retro_money_total"], 90.0)
        self.assertEqual(totals["total_payment"], 1090.0)


class ManualCompletionBadgeTests(unittest.TestCase):
    def test_manual_badge_is_flagged_so_the_symbol_can_be_hidden(self):
        from routes import completions as completion_routes

        badges = completion_routes._completion_amount_badges([
            {"symbol": "243", "display_name": "תומך מקצועי - לתשלום ידני",
             "amount": 150.0, "quantity": 0.0},
            {"symbol": "253", "display_name": "הפרשי השלמות לא לפנסיה",
             "amount": 90.0, "quantity": 0.0},
        ])
        by_symbol = {badge["symbol"]: badge for badge in badges}
        self.assertTrue(by_symbol["243"]["is_manual"])
        self.assertFalse(by_symbol["253"]["is_manual"])


def _manual_event(component_type_id=13, status="included_in_export") -> dict:
    return {
        "id": 1,
        "source_table": "payment_components",
        "status": status,
        "person_name": "אבי",
        "work_year": 2026,
        "work_month": 5,
        "validation_error": "חסר קוד מירב למדריך",
        "new_data": {"component_type_id": component_type_id},
        "old_data": None,
    }


class ManualCompletionEventTests(unittest.TestCase):
    def test_professional_support_component_event_is_manual(self):
        self.assertTrue(salary_impact.is_manual_completion_event(_manual_event()))

    def test_other_component_event_is_not_manual(self):
        self.assertFalse(salary_impact.is_manual_completion_event(_manual_event(component_type_id=2)))

    def test_shift_event_is_not_manual(self):
        event = {**_manual_event(), "source_table": "time_reports"}
        self.assertFalse(salary_impact.is_manual_completion_event(event))

    def test_deleted_component_is_read_from_the_old_snapshot(self):
        event = {**_manual_event(), "new_data": None, "old_data": {"component_type_id": 13}}
        self.assertTrue(salary_impact.is_manual_completion_event(event))

    def test_missing_component_type_is_not_manual(self):
        event = {**_manual_event(), "new_data": {"component_type_id": None}, "old_data": None}
        self.assertFalse(salary_impact.is_manual_completion_event(event))


class ManualCompletionAuditTests(unittest.TestCase):
    def test_manual_rows_are_dropped_from_the_expected_side(self):
        rows = gesher_difference._drop_manual_completion_rows([
            _manual_completion_row(), _regular_completion_row()
        ])
        self.assertEqual([row["symbol"] for row in rows], ["253"])

    def test_manual_row_is_not_reported_as_missing_from_the_bridge(self):
        entries = gesher_difference.compare_completion_audit_rows(
            gesher_difference._drop_manual_completion_rows([_manual_completion_row()]),
            [],
        )
        self.assertEqual(entries, [])


class _RecordingCursor:
    """קורסור מדומה שאוסף את פקודות ה-SQL שנשלחו אליו."""

    def __init__(self):
        self.statements: list[str] = []

    def execute(self, sql, params=None):
        self.statements.append(sql)


class ManualCompletionAutoApprovalTests(unittest.TestCase):
    """הטריגר מסמן אירוע של רכיב ידני כמאושר לייצוא כבר ביצירה."""

    def _trigger_sql(self) -> str:
        from core import audit

        cursor = _RecordingCursor()
        audit._ensure_salary_impact_capture(cursor)
        function_statements = [
            statement for statement in cursor.statements
            if "capture_salary_impact_from_audit()" in statement
            and "CREATE OR REPLACE FUNCTION" in statement
        ]
        self.assertEqual(len(function_statements), 1)
        return function_statements[0]

    def test_trigger_selects_the_status_by_component_type(self):
        sql = self._trigger_sql()
        self.assertIn("status_value", sql)
        self.assertIn("'included_in_export'", sql)

    def test_trigger_embeds_the_manual_component_ids_from_the_shared_constant(self):
        sql = self._trigger_sql()
        for component_type_id in MANUAL_COMPLETION_COMPONENT_TYPE_IDS:
            self.assertIn(f"IN ({component_type_id})", sql)

    def test_trigger_inserts_the_status_column(self):
        sql = self._trigger_sql()
        self.assertIn("INSERT INTO salary_impact_events (\n                status,", sql)


if __name__ == "__main__":
    unittest.main()
