# -*- coding: utf-8 -*-
"""בדיקות בקרה לשמירת ניהול תשלום חג."""
import asyncio
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from routes import guide as guide_routes


class _FakeRequest:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


class _FakeConnection:
    conn = object()


class _FakeConnectionManager:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        return False


class TestHolidayPaymentSetupSecurity(unittest.TestCase):
    """ניהול תשלום חג משנה שכר ולכן צריך לכבד נעילת חודש."""

    def test_save_holiday_payment_setup_blocked_when_month_locked(self):
        request = _FakeRequest({"year": 2026, "month": 4, "rows": []})

        with patch.object(guide_routes, "get_conn", return_value=_FakeConnectionManager(_FakeConnection())), \
             patch.object(guide_routes, "get_housing_array_filter", return_value=None), \
             patch("core.history.is_month_locked", return_value=True), \
             patch.object(guide_routes, "save_holiday_payment_setup") as mock_save:
            response = asyncio.run(guide_routes.save_holiday_payment_setup_api(request))

        payload = json.loads(response.body.decode("utf-8"))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(payload["success"])
        self.assertIn("נעול", payload["error"])
        mock_save.assert_not_called()

    def test_save_holiday_payment_setup_allowed_when_month_open(self):
        request = _FakeRequest({"year": 2026, "month": 4, "rows": []})

        with patch.object(guide_routes, "get_conn", return_value=_FakeConnectionManager(_FakeConnection())), \
             patch.object(guide_routes, "get_housing_array_filter", return_value=2), \
             patch("core.history.is_month_locked", return_value=False), \
             patch.object(guide_routes, "save_holiday_payment_setup") as mock_save:
            response = asyncio.run(guide_routes.save_holiday_payment_setup_api(request))

        payload = json.loads(response.body.decode("utf-8"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(payload["success"])
        mock_save.assert_called_once()
        self.assertEqual(mock_save.call_args.args[1:], (2026, 4, [], 2))


if __name__ == "__main__":
    unittest.main()
