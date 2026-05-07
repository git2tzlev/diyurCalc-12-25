# -*- coding: utf-8 -*-
"""בדיקות אבטחה לסינון לוגי מייל לפי מערך דיור."""
import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from routes import email as email_routes
from services import email_service


class _FakeCursor:
    def __init__(self, rows=None):
        self.rows = rows or []

    def fetchall(self):
        return self.rows


class _FakeConnection:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return _FakeCursor(self.rows)


class _FakeConnectionManager:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeRequest:
    def __init__(self, user=None, body=None):
        self.state = SimpleNamespace(current_user=user)
        self._body = body or {}

    async def json(self):
        return self._body


async def _stream_text(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
    return b"".join(chunks).decode("utf-8")


class TestEmailLogHousingScope(unittest.TestCase):
    def test_get_email_logs_filters_by_housing_array(self):
        conn = _FakeConnection()

        email_service.get_email_logs(conn, year=2026, month=4, housing_array_id=7)

        sql, params = conn.calls[0]
        self.assertIn("EXISTS", sql)
        self.assertIn("p.housing_array_id = %s", sql)
        self.assertEqual(params, (4, 2026, 7, 100))

    def test_get_batch_summary_filters_by_housing_array(self):
        conn = _FakeConnection(rows=[{"status": "failed", "count": 2}])

        summary = email_service.get_batch_summary(conn, "batch-1", housing_array_id=7)

        sql, params = conn.calls[0]
        self.assertIn("EXISTS", sql)
        self.assertIn("p.housing_array_id = %s", sql)
        self.assertEqual(params, ("batch-1", 7))
        self.assertEqual(summary["failed"], 2)

    def test_email_logs_route_passes_framework_manager_scope(self):
        request = _FakeRequest(user={"role": "framework_manager", "housing_array_id": 7})

        with (
            patch.object(email_routes, "get_conn", return_value=_FakeConnectionManager(_FakeConnection())),
            patch.object(email_routes, "get_email_logs", return_value=[]) as mock_get_logs,
        ):
            response = asyncio.run(email_routes.email_logs_route(request, 2026, 4))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_get_logs.call_args.kwargs["housing_array_id"], 7)

    def test_retry_failed_route_passes_framework_manager_scope(self):
        request = _FakeRequest(
            user={"role": "framework_manager", "housing_array_id": 7, "person_id": 10},
            body={"batch_id": "batch-1", "year": 2026, "month": 4, "token": "signed"},
        )

        with (
            patch.object(email_routes, "validate_action_token", return_value=True),
            patch.object(email_routes, "get_conn", return_value=_FakeConnectionManager(_FakeConnection())),
            patch.object(email_routes, "get_email_settings", return_value={"smtp_host": "smtp"}),
            patch.object(email_routes, "get_email_logs", return_value=[]) as mock_get_logs,
        ):
            response = asyncio.run(email_routes.retry_failed_route(request))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_get_logs.call_args.kwargs["housing_array_id"], 7)

    def test_retry_failed_uses_original_log_period(self):
        request = _FakeRequest(
            user={"role": "framework_manager", "housing_array_id": 7, "person_id": 10},
            body={"batch_id": "batch-1", "year": 2025, "month": 1, "token": "signed"},
        )
        failed_logs = [{
            "recipient_id": 20,
            "recipient_name": "מדריך",
            "recipient_email": "guide@example.com",
            "year": 2026,
            "month": 4,
        }]

        with (
            patch.object(email_routes, "validate_action_token", return_value=True),
            patch.object(email_routes, "get_conn", return_value=_FakeConnectionManager(_FakeConnection())),
            patch.object(email_routes, "get_email_settings", return_value={"smtp_host": "smtp"}),
            patch.object(email_routes, "get_email_logs", return_value=failed_logs),
            patch.object(email_routes, "process_guide_for_bulk", return_value={"status": "sent", "name": "מדריך"}) as mock_process,
        ):
            response = asyncio.run(email_routes.retry_failed_route(request))

        self.assertEqual(response.status_code, 200)
        args = mock_process.call_args.args
        self.assertEqual(args[1], 2026)
        self.assertEqual(args[2], 4)

    def test_retry_failed_requires_signed_action_token(self):
        request = _FakeRequest(
            user={"role": "framework_manager", "housing_array_id": 7, "person_id": 10},
            body={"batch_id": "batch-1", "year": 2026, "month": 4, "token": "bad"},
        )

        with patch.object(email_routes, "get_conn") as mock_get_conn:
            response = asyncio.run(email_routes.retry_failed_route(request))

        self.assertEqual(response.status_code, 403)
        mock_get_conn.assert_not_called()

    def test_email_logs_route_hides_internal_error_details(self):
        request = _FakeRequest(user={"role": "framework_manager", "housing_array_id": 7})

        with patch.object(email_routes, "get_conn", side_effect=RuntimeError("secret smtp host")):
            response = asyncio.run(email_routes.email_logs_route(request, 2026, 4))

        text = response.body.decode("utf-8")
        self.assertIn(email_routes.GENERIC_ERROR, text)
        self.assertNotIn("secret smtp host", text)

    def test_bulk_stream_requires_signed_action_token(self):
        request = _FakeRequest(user={"role": "framework_manager", "housing_array_id": 7, "person_id": 10})

        with patch.object(email_routes, "get_conn") as mock_get_conn:
            response = asyncio.run(email_routes.send_bulk_stream(request, 2026, 4, token="bad-token"))
            text = asyncio.run(_stream_text(response))

        mock_get_conn.assert_not_called()
        self.assertIn("אין הרשאה", text)

    def test_email_service_hides_unexpected_send_error_details(self):
        with patch.object(email_service.smtplib, "SMTP", side_effect=RuntimeError("secret smtp host")):
            result = email_service.send_email_with_pdf(
                settings={
                    "smtp_host": "smtp",
                    "smtp_port": 587,
                    "smtp_user": "user",
                    "smtp_password": "password",
                    "from_email": "from@example.com",
                },
                to_email="to@example.com",
                to_name="מדריך",
                subject="בדיקה",
                body="body",
                pdf_bytes=b"%PDF",
                pdf_filename="report.pdf",
            )

        self.assertFalse(result["success"])
        self.assertEqual(result["error"], email_service.GENERIC_ERROR)


if __name__ == "__main__":
    unittest.main()
