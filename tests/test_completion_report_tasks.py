# -*- coding: utf-8 -*-
"""בדיקות לרשימת דוחות ההשלמות לשליחה ולסינון לפי בחירת המשתמש."""
import asyncio
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from routes import completions as completion_routes


def _event(person_id: int, name: str, work_month: int, status: str, email: str = "a@b.c") -> dict:
    return {
        "person_id": person_id,
        "person_name": name,
        "person_email": email,
        "work_year": 2026,
        "work_month": work_month,
        "status": status,
    }


class _FakeConnection:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class CompletionReportTasksTests(unittest.TestCase):
    def test_task_is_paid_only_when_all_events_exported(self):
        tasks = completion_routes._completion_report_tasks([
            _event(1, "אבי", 5, "exported"),
            _event(1, "אבי", 5, "exported"),
            _event(2, "בת", 5, "exported"),
            _event(2, "בת", 5, "open"),
        ])
        by_id = {task["id"]: task for task in tasks}
        self.assertTrue(by_id[1]["is_paid"])
        self.assertEqual(by_id[1]["status_label"], "שולם")
        self.assertFalse(by_id[2]["is_paid"])
        self.assertEqual(by_id[2]["status_label"], "מעורב")

    def test_single_status_task_gets_its_own_label(self):
        tasks = completion_routes._completion_report_tasks([
            _event(1, "אבי", 5, "included_in_export"),
            _event(2, "בת", 5, "open"),
        ])
        labels = {task["id"]: task["status_label"] for task in tasks}
        self.assertEqual(labels[1], "מאושר לייצוא")
        self.assertEqual(labels[2], "ממתין לאישור")

    def test_tasks_are_grouped_per_guide_and_work_month(self):
        tasks = completion_routes._completion_report_tasks([
            _event(1, "אבי", 5, "exported"),
            _event(1, "אבי", 6, "exported"),
            _event(1, "אבי", 6, "exported"),
        ])
        self.assertEqual([task["task_id"] for task in tasks], ["1-2026-05", "1-2026-06"])
        self.assertEqual([task["items_count"] for task in tasks], [1, 2])

    def test_event_without_person_or_period_is_ignored(self):
        tasks = completion_routes._completion_report_tasks([
            {"person_id": None, "work_year": 2026, "work_month": 5, "status": "exported"},
            {"person_id": 1, "work_year": None, "work_month": 5, "status": "exported"},
        ])
        self.assertEqual(tasks, [])


class CompletionGuidesPaidTests(unittest.TestCase):
    """מדריכים שהשלמותיהם שולמו ממשיכים להופיע בדף ההשלמות."""

    def test_paid_events_are_shown_with_their_own_counts_and_badges(self):
        events = [
            _event(1, "אבי", 5, "exported"),
            _event(1, "אבי", 5, "open"),
        ]
        group_rows = {
            "open": {(1, 2026, 5): [{"symbol": "253", "amount": 10.0, "quantity": 0}]},
            "included_in_export": {},
            "exported": {(1, 2026, 5): [{"symbol": "253", "amount": 90.0, "quantity": 0}]},
        }
        guides = completion_routes._build_completion_guides(events, [], group_rows, {})
        guide = guides[0]
        self.assertEqual(guide["paid_count"], 1)
        self.assertEqual(guide["open_count"], 1)
        self.assertEqual(guide["approved_count"], 0)
        self.assertEqual(guide["paid_badges"][0]["amount"], 90.0)
        self.assertEqual(guide["open_badges"][0]["amount"], 10.0)
        self.assertEqual(guide["months"][0]["paid_badges"][0]["amount"], 90.0)

    def test_page_loads_paid_events_too(self):
        self.assertIn("exported", completion_routes.COMPLETION_PAGE_STATUSES)


class CompletionBulkSendSelectionTests(unittest.TestCase):
    """השליחה המרוכזת שולחת רק את המשימות שסומנו בדיאלוג."""

    def _run_stream(self, task_ids: str) -> tuple[list[str], str]:
        events = [
            _event(1, "אבי", 5, "exported"),
            _event(2, "בת", 5, "exported"),
            _event(3, "גד", 5, "open"),
        ]
        sent_task_ids: list[str] = []
        requested_statuses: list = []

        def fake_process(guide, work_year, work_month, *args):
            sent_task_ids.append(f"{guide['id']}-{work_year}-{work_month:02d}")
            return {"status": "sent"}

        def fake_events(conn, year, month, **kwargs):
            requested_statuses.append(kwargs.get("statuses"))
            return events

        async def collect():
            with patch.object(completion_routes, "validate_action_token", return_value=True), \
                 patch.object(completion_routes, "get_conn", return_value=_FakeConnection()), \
                 patch.object(completion_routes, "get_housing_array_filter", return_value=None), \
                 patch.object(completion_routes, "is_demo_mode", return_value=False), \
                 patch.object(completion_routes, "get_email_settings", return_value={"host": "x"}), \
                 patch.object(completion_routes, "get_salary_impact_events", side_effect=fake_events), \
                 patch.object(completion_routes, "generate_batch_id", return_value="batch-1"), \
                 patch.object(completion_routes, "process_guide_for_bulk", side_effect=fake_process):
                request = _FakeRequest()
                response = await completion_routes.completion_reports_bulk_send_stream(
                    request, 2026, 7, token="ok", task_ids=task_ids
                )
                body = ""
                async for chunk in response.body_iterator:
                    body += chunk
                return body

        body = asyncio.run(collect())
        self.requested_statuses = requested_statuses
        return sent_task_ids, body

    def test_only_selected_tasks_are_sent(self):
        sent_task_ids, _ = self._run_stream("1-2026-05,3-2026-05")
        self.assertEqual(sorted(sent_task_ids), ["1-2026-05", "3-2026-05"])

    def test_paid_tasks_are_loaded_so_they_can_be_sent(self):
        sent_task_ids, _ = self._run_stream("2-2026-05")
        self.assertEqual(sent_task_ids, ["2-2026-05"])
        self.assertIn("exported", self.requested_statuses[0])

    def test_unknown_task_id_is_filtered_out(self):
        sent_task_ids, _ = self._run_stream("1-2026-05,99-2026-05")
        self.assertEqual(sent_task_ids, ["1-2026-05"])

    def test_empty_selection_returns_error(self):
        sent_task_ids, body = self._run_stream("")
        self.assertEqual(sent_task_ids, [])
        self.assertIn("לא נבחרו דוחות השלמות לשליחה", body)


class _FakeRequest:
    def __init__(self):
        self.state = type("State", (), {"current_user": None})()

    async def is_disconnected(self) -> bool:
        return False


if __name__ == "__main__":
    unittest.main()
