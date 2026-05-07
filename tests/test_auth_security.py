# -*- coding: utf-8 -*-
"""בדיקות אבטחה לשער הכניסה למערכת."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

import app as app_module
from core import auth


class _FakeCursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _FakeConnection:
    def __init__(self, row):
        self.row = row
        self.logged = False

    def execute(self, sql, params=None):
        if "INSERT INTO login_logs" in sql:
            self.logged = True
            return _FakeCursor(None)
        return _FakeCursor(self.row)


class _FakeConnectionManager:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


def _person_row(role_name, is_active=True, housing_array_id=2):
    return {
        "id": 10,
        "name": "משתמש בדיקה",
        "password": "secret",
        "is_active": is_active,
        "housing_array_id": housing_array_id,
        "role_name": role_name,
    }


def _request_with_accept(accept="application/json", cookie="session=test-token"):
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [
            (b"accept", accept.encode("utf-8")),
            (b"cookie", cookie.encode("utf-8")),
        ],
    })


class TestLoginRoleSecurity(unittest.TestCase):
    """רק מנהל-על ומנהל מסגרת מורשים להיכנס למערכת."""

    def test_can_login_allows_only_super_admin_and_framework_manager(self):
        self.assertTrue(auth.can_login("super_admin"))
        self.assertTrue(auth.can_login("framework_manager"))

        for role in ["guide", "employee", "substitute", "viewer", "", None]:
            self.assertFalse(auth.can_login(role or ""))

    @patch.object(auth, "verify_password", return_value=True)
    def test_authenticate_user_rejects_non_manager_role(self, _mock_verify):
        fake_conn = _FakeConnection(_person_row("guide"))

        with patch.object(auth, "get_conn", return_value=_FakeConnectionManager(fake_conn)):
            success, user_data, error = auth.authenticate_user("123456789", "secret")

        self.assertFalse(success)
        self.assertIsNone(user_data)
        self.assertIn("אין לך הרשאה", error)
        self.assertFalse(fake_conn.logged)

    @patch.object(auth, "verify_password", return_value=True)
    def test_authenticate_user_allows_framework_manager(self, _mock_verify):
        fake_conn = _FakeConnection(_person_row("framework_manager"))

        with patch.object(auth, "get_conn", return_value=_FakeConnectionManager(fake_conn)):
            success, user_data, error = auth.authenticate_user("123456789", "secret")

        self.assertTrue(success)
        self.assertEqual(error, "")
        self.assertEqual(user_data["role"], "framework_manager")
        self.assertTrue(fake_conn.logged)

    def test_authenticate_user_hides_internal_database_error(self):
        class _BrokenConnectionManager:
            def __enter__(self):
                raise RuntimeError("secret database host")

            def __exit__(self, exc_type, exc, tb):
                return False

        with patch.object(auth, "get_conn", return_value=_BrokenConnectionManager()):
            success, user_data, error = auth.authenticate_user("123456789", "secret")

        self.assertFalse(success)
        self.assertIsNone(user_data)
        self.assertEqual(error, "שגיאת מערכת. נסי שוב מאוחר יותר")

    def test_refresh_session_user_reads_current_role_and_housing_array(self):
        fake_conn = _FakeConnection(_person_row("framework_manager", housing_array_id=7))

        with patch.object(auth, "get_conn", return_value=_FakeConnectionManager(fake_conn)):
            user = auth.refresh_session_user({"person_id": 10, "role": "super_admin", "housing_array_id": 1})

        self.assertEqual(user["role"], "framework_manager")
        self.assertEqual(user["housing_array_id"], 7)

    def test_refresh_session_user_rejects_inactive_user(self):
        fake_conn = _FakeConnection(_person_row("super_admin", is_active=False))

        with patch.object(auth, "get_conn", return_value=_FakeConnectionManager(fake_conn)):
            user = auth.refresh_session_user({"person_id": 10, "role": "super_admin"})

        self.assertIsNone(user)

    def test_refresh_session_user_rejects_revoked_role(self):
        fake_conn = _FakeConnection(_person_row("guide"))

        with patch.object(auth, "get_conn", return_value=_FakeConnectionManager(fake_conn)):
            user = auth.refresh_session_user({"person_id": 10, "role": "super_admin"})

        self.assertIsNone(user)


class TestActionTokenSecurity(unittest.TestCase):
    """פעולות GET/SSE רגישות דורשות token חתום לפי משתמש ופעולה."""

    def test_action_token_matches_current_user_and_action(self):
        request = SimpleNamespace(state=SimpleNamespace(current_user={"person_id": 10}))

        token = auth.create_action_token(request, "bulk_send")

        self.assertTrue(auth.validate_action_token(request, token, "bulk_send"))
        self.assertFalse(auth.validate_action_token(request, token, "demo_sync"))

    def test_action_token_rejects_different_user(self):
        request = SimpleNamespace(state=SimpleNamespace(current_user={"person_id": 10}))
        other_request = SimpleNamespace(state=SimpleNamespace(current_user={"person_id": 11}))

        token = auth.create_action_token(request, "bulk_send")

        self.assertFalse(auth.validate_action_token(other_request, token, "bulk_send"))


class TestAuthMiddlewareRoleSecurity(unittest.IsolatedAsyncioTestCase):
    """גם session חתום עם role לא מורשה לא מקבל כניסה למערכת."""

    async def test_middleware_rejects_signed_session_with_non_authorized_role(self):
        middleware = app_module.AuthMiddleware(app=SimpleNamespace())
        request = _request_with_accept()

        with (
            patch.object(app_module, "validate_session_token", return_value={"role": "guide", "person_id": 10}),
            patch.object(app_module, "refresh_session_user", return_value=None),
        ):
            response = await middleware.dispatch(request, lambda _request: Response("ok"))

        self.assertIsInstance(response, JSONResponse)
        self.assertEqual(response.status_code, 401)
        self.assertIn("לא מחובר", response.body.decode("utf-8"))

    async def test_middleware_allows_authorized_role(self):
        middleware = app_module.AuthMiddleware(app=SimpleNamespace())
        request = _request_with_accept()

        async def call_next(_request):
            return Response("ok")

        with (
            patch.object(app_module, "validate_session_token", return_value={"role": "super_admin", "person_id": 1}),
            patch.object(app_module, "refresh_session_user", return_value={"role": "super_admin", "person_id": 1}),
        ):
            response = await middleware.dispatch(request, call_next)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body, b"ok")


if __name__ == "__main__":
    unittest.main()
