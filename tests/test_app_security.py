# -*- coding: utf-8 -*-
"""בדיקות אבטחה ל-routes שמוגדרים ב-app.py."""
import asyncio
import json
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app as app_module
import psycopg2
from starlette.responses import Response


class _FakeRequest:
    def __init__(self, user=None, body=None, cookies=None):
        self.state = SimpleNamespace(current_user=user)
        self._body = body or {}
        self.cookies = cookies or {}
        self.query_params = {}
        self.url = SimpleNamespace(path="/api/test")

    async def json(self):
        return self._body


class _FakeCursor:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchall(self):
        return self._rows


class _FakeConnection:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return _FakeCursor(self.rows)


class _FakeConnectionManager:
    def __init__(self, conn=None, error=None):
        self.conn = conn or _FakeConnection()
        self.error = error

    def __enter__(self):
        if self.error:
            raise self.error
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class TestDemoModeSecurity(unittest.TestCase):
    """מעבר דמו/ייצור הוא פעולה רגישה ולכן דורש מנהל-על."""

    def test_toggle_demo_mode_requires_super_admin(self):
        request = _FakeRequest(
            user={"role": "framework_manager", "person_id": 10},
            body={"password": "secret"},
        )

        response = asyncio.run(app_module.toggle_demo_mode(request))

        self.assertEqual(response.status_code, 403)
        self.assertIn("אין הרשאה", response.body.decode("utf-8"))

    @patch.object(app_module.config, "DEMO_MODE_PASSWORD", "secret")
    def test_super_admin_can_toggle_demo_mode_with_password(self):
        request = _FakeRequest(
            user={"role": "super_admin", "person_id": 1},
            body={"password": "secret"},
            cookies={"demo_mode": "false"},
        )

        response = asyncio.run(app_module.toggle_demo_mode(request))
        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        self.assertTrue(payload["demo_mode"])

    def test_demo_cookie_is_ignored_without_super_admin_session(self):
        request = _FakeRequest(cookies={"demo_mode": "true", "session": "signed"})
        middleware = app_module.DemoModeMiddleware(app=object())

        async def call_next(_request):
            return Response("ok")

        with (
            patch.object(app_module, "validate_session_token", return_value={"role": "framework_manager"}),
            patch.object(app_module, "set_demo_mode") as mock_set_demo_mode,
        ):
            response = asyncio.run(middleware.dispatch(request, call_next))

        self.assertEqual(response.status_code, 200)
        mock_set_demo_mode.assert_called_once_with(False)

    def test_demo_cookie_is_allowed_for_super_admin_session(self):
        request = _FakeRequest(cookies={"demo_mode": "true", "session": "signed"})
        middleware = app_module.DemoModeMiddleware(app=object())

        async def call_next(_request):
            return Response("ok")

        with (
            patch.object(app_module, "validate_session_token", return_value={"role": "super_admin"}),
            patch.object(app_module, "set_demo_mode") as mock_set_demo_mode,
        ):
            response = asyncio.run(middleware.dispatch(request, call_next))

        self.assertEqual(response.status_code, 200)
        mock_set_demo_mode.assert_called_once_with(True)

    def test_demo_mode_status_ignores_cookie_for_non_super_admin(self):
        request = _FakeRequest(
            user={"role": "framework_manager", "person_id": 10},
            cookies={"demo_mode": "true"},
        )

        result = app_module.demo_mode_status(request)

        self.assertFalse(result["demo_mode"])


class TestHousingArraySecurity(unittest.TestCase):
    """מנהל מערך מקבל רק את מערך הדיור שלו."""

    def test_framework_manager_gets_only_own_housing_array(self):
        request = _FakeRequest(user={"role": "framework_manager", "housing_array_id": 2})
        fake_conn = _FakeConnection(rows=[{"id": 2, "name": "מערך בדיקה"}])

        with patch.object(app_module, "get_conn", return_value=_FakeConnectionManager(fake_conn)):
            result = app_module.get_housing_arrays(request)

        self.assertEqual(result, [{"id": 2, "name": "מערך בדיקה"}])
        sql, params = fake_conn.calls[0]
        self.assertIn("WHERE id = %s", sql)
        self.assertEqual(params, (2,))

    def test_super_admin_gets_all_housing_arrays(self):
        request = _FakeRequest(user={"role": "super_admin"})
        fake_conn = _FakeConnection(rows=[{"id": 1, "name": "א"}, {"id": 2, "name": "ב"}])

        with patch.object(app_module, "get_conn", return_value=_FakeConnectionManager(fake_conn)):
            result = app_module.get_housing_arrays(request)

        self.assertEqual(len(result), 2)
        sql, params = fake_conn.calls[0]
        self.assertNotIn("WHERE id = %s", sql)
        self.assertIsNone(params)


class TestHealthSecurity(unittest.TestCase):
    """health לא חושף טקסט שגיאה פנימי."""

    def test_health_check_hides_database_error_details(self):
        with patch("core.database.get_conn", return_value=_FakeConnectionManager(error=RuntimeError("secret host"))):
            response = app_module.health_check()
        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(payload["error"], "database unavailable")
        self.assertNotIn("secret host", json.dumps(payload))

    def test_database_exception_handler_hides_connection_details(self):
        request = _FakeRequest()
        error = psycopg2.OperationalError("could not translate host name secret-db.local")

        with patch.object(app_module.config, "DEBUG", False):
            response = asyncio.run(app_module.database_connection_error_handler(request, error))
        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(payload["error_type"], "database_connection_error")
        self.assertNotIn("secret-db.local", json.dumps(payload))

    def test_database_exception_handler_keeps_diagnostics_in_debug_mode(self):
        request = _FakeRequest()
        error = psycopg2.OperationalError("could not translate host name secret-db.local")

        with patch.object(app_module.config, "DEBUG", True):
            response = asyncio.run(app_module.database_connection_error_handler(request, error))
        payload = json.loads(response.body.decode("utf-8"))

        self.assertIn("לא ניתן לפתור", payload["error"])


if __name__ == "__main__":
    unittest.main()
