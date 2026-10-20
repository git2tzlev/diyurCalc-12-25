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

    async def is_disconnected(self):
        return False


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
        self.assertEqual(args[6], 7)

    def test_retry_failed_uses_validated_backup_ids_when_batch_logs_are_missing(self):
        request = _FakeRequest(
            user={"role": "framework_manager", "housing_array_id": 7, "person_id": 10},
            body={
                "batch_id": "batch-1",
                "year": 2026,
                "month": 8,
                "token": "signed",
                "failed_guide_ids": [20, 21],
            },
        )
        guides = [
            {"id": 20, "name": "א", "email": "a@example.com"},
            {"id": 21, "name": "ב", "email": "b@example.com"},
        ]
        conn = _FakeConnection(rows=guides)

        with (
            patch.object(email_routes, "validate_action_token", return_value=True),
            patch.object(email_routes, "get_conn", return_value=_FakeConnectionManager(conn)),
            patch.object(email_routes, "get_email_settings", return_value={"smtp_host": "smtp"}),
            patch.object(email_routes, "get_email_logs", return_value=[]),
            patch.object(
                email_routes,
                "process_guide_for_bulk",
                return_value={"status": "sent", "name": "מדריך"},
            ) as mock_process,
        ):
            response = asyncio.run(email_routes.retry_failed_route(request))

        self.assertEqual(response.status_code, 200)
        sql, params = conn.calls[-1]
        self.assertIn("p.housing_array_id = %s", sql)
        self.assertEqual(params, (20, 21, 7))
        self.assertEqual(mock_process.call_count, 2)
        self.assertTrue(all(call.args[6] == 7 for call in mock_process.call_args_list))

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

    def test_bulk_stream_uses_super_admin_selected_housing_array(self):
        request = _FakeRequest(
            user={"role": "super_admin", "housing_array_id": None, "person_id": 10}
        )
        conn = _FakeConnection(rows=[
            {"id": 20, "name": "מדריך ASD", "email": "asd@example.com"},
        ])

        with (
            patch.object(email_routes, "validate_action_token", return_value=True),
            patch.object(email_routes, "get_conn", return_value=_FakeConnectionManager(conn)),
            patch.object(email_routes, "get_email_settings", return_value={"smtp_host": "smtp"}),
            patch.object(
                email_routes,
                "_guides_with_reports_for_period",
                return_value=[{"id": 20, "name": "מדריך ASD", "email": "asd@example.com"}],
                create=True,
            ) as mock_guides,
        ):
            response = asyncio.run(email_routes.send_bulk_stream(
                request,
                2026,
                5,
                token="signed",
                housing_array_id=2,
            ))

        self.assertEqual(response.status_code, 200)
        mock_guides.assert_called_once_with(
            year=2026,
            month=5,
            housing_array_id=2,
        )

    def test_retry_failed_stream_tracks_each_retry_and_keeps_original_skipped(self):
        request = _FakeRequest(
            user={"role": "framework_manager", "housing_array_id": 7, "person_id": 10}
        )
        original_logs = [
            {
                "recipient_id": 20,
                "recipient_name": "נכשל א",
                "recipient_email": "a@example.com",
                "status": "failed",
                "error_message": "שגיאה ביצירת PDF",
                "year": 2026,
                "month": 9,
            },
            {
                "recipient_id": 21,
                "recipient_name": "נכשל ב",
                "recipient_email": "b@example.com",
                "status": "failed",
                "error_message": "שגיאה ביצירת PDF",
                "year": 2026,
                "month": 9,
            },
            {
                "recipient_id": 30,
                "recipient_name": "ללא מייל",
                "recipient_email": "",
                "status": "skipped",
                "error_message": "אין מייל תקין",
                "year": 2026,
                "month": 9,
            },
            {
                "recipient_id": 40,
                "recipient_name": "כבר נשלח",
                "recipient_email": "sent@example.com",
                "status": "sent",
                "error_message": None,
                "year": 2026,
                "month": 9,
            },
        ]
        retry_results = [
            {"id": 20, "name": "נכשל א", "status": "sent", "reason": None},
            {"id": 21, "name": "נכשל ב", "status": "failed", "reason": "שגיאה בשליחה"},
        ]

        with (
            patch.object(email_routes, "validate_action_token", return_value=True),
            patch.object(email_routes, "get_conn", return_value=_FakeConnectionManager(_FakeConnection())),
            patch.object(email_routes, "get_email_settings", return_value={"smtp_host": "smtp"}),
            patch.object(email_routes, "get_email_logs", return_value=original_logs) as mock_get_logs,
            patch.object(email_routes, "process_guide_for_bulk", side_effect=retry_results) as mock_process,
        ):
            response = asyncio.run(email_routes.retry_failed_stream(
                request,
                batch_id="batch-1",
                year=2026,
                month=9,
                token="signed",
            ))
            text = asyncio.run(_stream_text(response))

        self.assertEqual(response.status_code, 200)
        self.assertIn("event: start", text)
        self.assertEqual(text.count("event: sending"), 2)
        self.assertEqual(text.count("event: progress"), 2)
        self.assertIn("event: complete", text)
        self.assertIn('"recipientName": "ללא מייל"', text)
        self.assertIn('"reason": "אין מייל תקין"', text)
        self.assertIn('"failedIds": [21]', text)
        self.assertEqual(mock_process.call_count, 2)
        self.assertEqual({call.args[0]["id"] for call in mock_process.call_args_list}, {20, 21})
        self.assertEqual(mock_get_logs.call_args.kwargs["housing_array_id"], 7)
        self.assertEqual(mock_get_logs.call_args.kwargs["limit"], 1000)

    def test_retry_failed_stream_rejects_invalid_token_before_database_access(self):
        request = _FakeRequest(
            user={"role": "framework_manager", "housing_array_id": 7, "person_id": 10}
        )

        with patch.object(email_routes, "get_conn") as mock_get_conn:
            response = asyncio.run(email_routes.retry_failed_stream(
                request,
                batch_id="batch-1",
                year=2026,
                month=9,
                token="bad",
            ))
            text = asyncio.run(_stream_text(response))

        mock_get_conn.assert_not_called()
        self.assertIn("אין הרשאה", text)

    def test_retry_failed_stream_scopes_validated_backup_ids_when_logs_are_missing(self):
        request = _FakeRequest(
            user={"role": "super_admin", "housing_array_id": None, "person_id": 10}
        )
        conn = _FakeConnection(rows=[
            {"id": 20, "name": "א", "email": "a@example.com"},
            {"id": 21, "name": "ב", "email": "b@example.com"},
        ])

        def sent_result(guide, *_args):
            return {
                "id": guide["id"],
                "name": guide["name"],
                "status": "sent",
                "reason": None,
            }

        with (
            patch.object(email_routes, "validate_action_token", return_value=True),
            patch.object(email_routes, "get_conn", return_value=_FakeConnectionManager(conn)),
            patch.object(email_routes, "get_email_settings", return_value={"smtp_host": "smtp"}),
            patch.object(email_routes, "get_email_logs", return_value=[]),
            patch.object(email_routes, "process_guide_for_bulk", side_effect=sent_result) as mock_process,
        ):
            response = asyncio.run(email_routes.retry_failed_stream(
                request,
                batch_id="batch-1",
                year=2026,
                month=9,
                token="signed",
                failed_guide_ids="20,21,20",
                housing_array_id=2,
            ))
            text = asyncio.run(_stream_text(response))

        sql, params = conn.calls[-1]
        self.assertIn("p.housing_array_id = %s", sql)
        self.assertEqual(params, (20, 21, 2))
        self.assertEqual(mock_process.call_count, 2)
        self.assertIn('"failedIds": []', text)

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
