# -*- coding: utf-8 -*-
"""בדיקות אבטחה ל-API הערות מדריך."""
import asyncio
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from routes import guide as guide_routes


class _FakeRequest:
    def __init__(self, user=None, body=None):
        self.state = SimpleNamespace(current_user=user)
        self._body = body or {}

    async def json(self):
        return self._body


class _FakeCursor:
    def __init__(self, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class _FakeConnection:
    def __init__(self, fetch_rows=None, fetch_one=None):
        self.fetch_rows = fetch_rows or []
        self.fetch_one = fetch_one
        self.executed = []
        self.conn = self
        self.committed = False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        if "SELECT person_id FROM guide_monthly_notes" in sql:
            return _FakeCursor(row=self.fetch_one)
        return _FakeCursor(rows=self.fetch_rows)

    def commit(self):
        self.committed = True


class _FakeConnectionManager:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class TestGuideNotesSecurity(unittest.TestCase):
    """הערות מדריך חייבות לכבד scope ולהירשם על המשתמש המחובר."""

    def test_get_notes_validates_guide_access(self):
        calls = []
        fake_conn = _FakeConnection(fetch_rows=[])

        with patch.object(guide_routes, "get_housing_array_filter", return_value=2), \
             patch.object(guide_routes, "_validate_guide_access", side_effect=lambda person_id, hf: calls.append((person_id, hf))), \
             patch.object(guide_routes, "get_conn", return_value=_FakeConnectionManager(fake_conn)):
            response = guide_routes.get_guide_notes(_FakeRequest(), person_id=123, year=2026, month=4)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls, [(123, 2)])

    def test_fetch_all_notes_scopes_by_housing_array(self):
        fake_conn = _FakeConnection(fetch_rows=[])

        guide_routes._fetch_all_notes_for_month(fake_conn, 2026, 4, housing_filter=7)

        sql, params = fake_conn.executed[0]
        self.assertIn("guide.housing_array_id = %s", sql)
        self.assertEqual(params, (2026, 4, 7))

    def test_fetch_all_notes_without_housing_filter_shows_all(self):
        fake_conn = _FakeConnection(fetch_rows=[])

        guide_routes._fetch_all_notes_for_month(fake_conn, 2026, 4, housing_filter=None)

        sql, params = fake_conn.executed[0]
        self.assertNotIn("guide.housing_array_id = %s", sql)
        self.assertEqual(params, (2026, 4))

    def test_notes_management_uses_default_period_when_missing(self):
        fake_conn = _FakeConnection(fetch_rows=[])
        request = _FakeRequest()

        with patch.object(guide_routes, "get_default_period", return_value=(2026, 4)), \
             patch.object(guide_routes, "get_housing_array_filter", return_value=None), \
             patch.object(guide_routes, "get_conn", return_value=_FakeConnectionManager(fake_conn)), \
             patch.object(guide_routes.templates, "TemplateResponse") as template_response:
            guide_routes.guide_notes_management(request)

        template_response.assert_called_once()
        template_name = template_response.call_args.args[0]
        context = template_response.call_args.args[1]
        self.assertEqual(template_name, "guide_notes_management.html")
        self.assertEqual(context["selected_year"], 2026)
        self.assertEqual(context["selected_month"], 4)

    def test_add_note_uses_session_person_id_as_created_by(self):
        fake_conn = _FakeConnection()
        request = _FakeRequest(
            user={"role": "super_admin", "person_id": 77},
            body={"year": 2026, "month": 4, "content": "בדיקה"},
        )

        with patch.object(guide_routes, "get_housing_array_filter", return_value=None), \
             patch.object(guide_routes, "_validate_guide_access"), \
             patch.object(guide_routes, "get_conn", return_value=_FakeConnectionManager(fake_conn)):
            response = asyncio.run(guide_routes.add_guide_note(request, person_id=123))

        payload = json.loads(response.body.decode("utf-8"))
        self.assertTrue(payload["success"])
        self.assertTrue(fake_conn.committed)
        insert_params = fake_conn.executed[0][1]
        self.assertEqual(insert_params, (123, 2026, 4, "בדיקה", 77))

    def test_delete_note_validates_owner_person_before_delete(self):
        calls = []
        fake_conn = _FakeConnection(fetch_one={"person_id": 123})

        with patch.object(guide_routes, "get_housing_array_filter", return_value=2), \
             patch.object(guide_routes, "_validate_guide_access", side_effect=lambda person_id, hf: calls.append((person_id, hf))), \
             patch.object(guide_routes, "get_conn", return_value=_FakeConnectionManager(fake_conn)):
            response = asyncio.run(guide_routes.delete_guide_note(_FakeRequest(), note_id=50))

        payload = json.loads(response.body.decode("utf-8"))
        self.assertTrue(payload["success"])
        self.assertTrue(fake_conn.committed)
        self.assertEqual(calls, [(123, 2)])
        self.assertIn("DELETE FROM guide_monthly_notes", fake_conn.executed[-1][0])

    def test_delete_missing_note_returns_404(self):
        fake_conn = _FakeConnection(fetch_one=None)

        with patch.object(guide_routes, "get_conn", return_value=_FakeConnectionManager(fake_conn)):
            response = asyncio.run(guide_routes.delete_guide_note(_FakeRequest(), note_id=999))

        payload = json.loads(response.body.decode("utf-8"))
        self.assertEqual(response.status_code, 404)
        self.assertFalse(payload["success"])
        self.assertFalse(fake_conn.committed)

    def test_shift_email_setup_hides_internal_error_details(self):
        request = _FakeRequest(body={"email": "guide@example.com"})

        with patch.object(guide_routes, "get_housing_array_filter", side_effect=RuntimeError("secret db host")):
            response = asyncio.run(guide_routes.shifts_report_email(request, person_id=123, year=2026, month=4))

        text = response.body.decode("utf-8")
        self.assertIn(guide_routes.GENERIC_ERROR, text)
        self.assertNotIn("secret db host", text)


if __name__ == "__main__":
    unittest.main()
