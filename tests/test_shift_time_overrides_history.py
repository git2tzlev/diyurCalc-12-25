from app_utils import _fetch_weekday_overrides_for_month


class _FakeCursor:
    def __init__(self, result_sets):
        self.result_sets = list(result_sets)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchall(self):
        return self.result_sets.pop(0)

    def close(self):
        pass


class _FakeConn:
    def __init__(self, result_sets):
        self.cursor_obj = _FakeCursor(result_sets)

    def cursor(self, cursor_factory=None):
        return self.cursor_obj


def test_weekday_overrides_for_month_uses_future_history_value():
    conn = _FakeConn([
        [
            {
                "original_override_id": 1,
                "shift_type_id": 103,
                "apartment_id": 10,
                "housing_array_id": None,
                "start_time": "17:00",
                "end_time": "08:30",
                "is_active": True,
            },
        ],
        [
            {
                "original_override_id": 1,
                "shift_type_id": 103,
                "apartment_id": 10,
                "housing_array_id": None,
                "start_time": "15:00",
                "end_time": "08:00",
                "is_active": True,
            },
        ],
    ])

    apt_overrides, ha_defaults = _fetch_weekday_overrides_for_month(conn, 2026, 7)

    assert apt_overrides == {10: ("15:00", "08:00")}
    assert ha_defaults == {}


def test_weekday_overrides_for_month_uses_current_when_no_future_history():
    conn = _FakeConn([
        [
            {
                "original_override_id": 1,
                "shift_type_id": 103,
                "apartment_id": None,
                "housing_array_id": 2,
                "start_time": "17:00",
                "end_time": "08:30",
                "is_active": True,
            },
        ],
        [],
    ])

    apt_overrides, ha_defaults = _fetch_weekday_overrides_for_month(conn, 2026, 8)

    assert apt_overrides == {}
    assert ha_defaults == {2: ("17:00", "08:30")}


def test_weekday_overrides_for_month_inactive_history_removes_future_override():
    conn = _FakeConn([
        [
            {
                "original_override_id": 1,
                "shift_type_id": 103,
                "apartment_id": 10,
                "housing_array_id": None,
                "start_time": "17:00",
                "end_time": "08:30",
                "is_active": True,
            },
        ],
        [
            {
                "original_override_id": 1,
                "shift_type_id": 103,
                "apartment_id": 10,
                "housing_array_id": None,
                "start_time": None,
                "end_time": None,
                "is_active": False,
            },
        ],
    ])

    apt_overrides, ha_defaults = _fetch_weekday_overrides_for_month(conn, 2026, 4)

    assert apt_overrides == {}
    assert ha_defaults == {}
