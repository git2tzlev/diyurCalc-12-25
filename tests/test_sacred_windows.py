from datetime import date

from core.premium_windows import (
    _hhmm_to_minutes,
    _load_shabbat_windows,
    build_sacred_windows_from_cache,
    get_window_at,
)
from core.time_utils import SHABBAT_CACHE_KEY, get_shabbat_times_cache
from utils.cache_manager import cache


class _Cursor:
    def __init__(self, rows):
        self._rows = rows
        self.closed = False

    def execute(self, query, params=None):
        self.query = query
        self.params = params

    def fetchall(self):
        return list(self._rows)

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, rows):
        self.cursor_instance = _Cursor(rows)

    def cursor(self, cursor_factory=None):
        return self.cursor_instance


def test_cache_uses_tel_aviv_and_does_not_let_placeholder_override_valid_row():
    """שורת עיר זרה/placeholder לא יכולה לדרוס את זמני תל אביב התקינים."""
    cache.delete(SHABBAT_CACHE_KEY)
    conn = _Connection([
        {
            "city": "תל אביב",
            "shabbat_date": "2025-12-06",
            "candle_lighting": "16:16",
            "havdalah": "17:16",
            "parsha": "וישלח",
            "holiday_name": None,
        },
        {
            "city": "ירושלים",
            "shabbat_date": "2025-12-06",
            "candle_lighting": "00:00",
            "havdalah": "00:00",
            "parsha": "וישלח",
            "holiday_name": None,
        },
    ])

    result = get_shabbat_times_cache(conn)

    assert result["2025-12-06"]["enter"] == "16:16"
    assert result["2025-12-06"]["exit"] == "17:16"


def test_invalid_clock_value_is_rejected():
    assert _hhmm_to_minutes("25:99") is None
    assert _hhmm_to_minutes("19:60") is None


def test_conflicting_valid_rows_use_first_deterministic_row_and_warn(caplog):
    conn = _Connection([
        {
            "city": "תל אביב",
            "shabbat_date": "2026-09-21",
            "candle_lighting": "17:58",
            "havdalah": "19:27",
            "holiday_name": "יום כיפור",
        },
        {
            "city": "תל אביב",
            "shabbat_date": "2026-09-21",
            "candle_lighting": "18:05",
            "havdalah": "19:35",
            "holiday_name": "יום כיפור",
        },
    ])

    windows = _load_shabbat_windows(conn, date(2026, 9, 20), date(2026, 9, 21))

    assert len(windows) == 1
    assert windows[0].start_min == 17 * 60 + 58
    assert "Conflicting shabbat_times rows" in caplog.text


def test_sunday_before_monday_holiday_is_half_open_absolute_window():
    windows = build_sacred_windows_from_cache({
        "2026-09-21": {
            "enter": "17:58",
            "exit": "19:27",
            "holiday": "יום כיפור",
        },
    })

    assert get_window_at(windows, date(2026, 9, 20), 17 * 60 + 57) is None
    assert get_window_at(windows, date(2026, 9, 20), 17 * 60 + 58) is not None
    assert get_window_at(windows, date(2026, 9, 21), 19 * 60 + 26) is not None
    assert get_window_at(windows, date(2026, 9, 21), 19 * 60 + 27) is None


def test_database_window_loader_ignores_non_tel_aviv_placeholders():
    conn = _Connection([
        {
            "city": "תל אביב",
            "shabbat_date": "2025-12-06",
            "candle_lighting": "16:16",
            "havdalah": "17:16",
            "holiday_name": None,
        },
        {
            "city": "ירושלים",
            "shabbat_date": "2025-12-06",
            "candle_lighting": "00:00",
            "havdalah": "00:00",
            "holiday_name": None,
        },
    ])

    windows = _load_shabbat_windows(conn, date(2025, 12, 5), date(2025, 12, 7))

    assert len(windows) == 1
    assert windows[0].start_min == 16 * 60 + 16
    assert windows[0].end_min == 17 * 60 + 16


def test_database_window_loader_treats_midnight_exit_as_continuation():
    conn = _Connection([{
        "city": "תל אביב",
        "shabbat_date": "2026-05-22",
        "candle_lighting": "18:52",
        "havdalah": "00:00",
        "holiday_name": "שבועות",
    }])

    windows = _load_shabbat_windows(conn, date(2026, 5, 21), date(2026, 5, 23))

    assert len(windows) == 1
    assert windows[0].start_date == date(2026, 5, 21)
    assert windows[0].end_date == date(2026, 5, 23)
    assert windows[0].end_min == 0


def test_missing_shabbat_row_is_covered_until_sunday_holiday_entry():
    """גם כששורת השבת חסרה, אין חור במוצאי שבת שלפני חג ביום ראשון."""
    windows = build_sacred_windows_from_cache(
        {
            "2025-04-13": {
                "enter": "19:58",
                "exit": "19:59",
                "holiday": "פסח",
            },
        },
        relevant_dates=[date(2025, 4, 12)],
    )

    assert get_window_at(windows, date(2025, 4, 12), 19 * 60 + 57) is not None
    assert get_window_at(windows, date(2025, 4, 12), 19 * 60 + 58) is not None
    assert get_window_at(windows, date(2025, 4, 13), 19 * 60 + 59) is None


def test_legacy_single_row_rosh_hashanah_covers_both_days():
    """רשומת ב' תשרי יחידה מייצגת חלון רציף של שני ימי ראש השנה."""
    windows = build_sacred_windows_from_cache({
        "2025-09-24": {
            "enter": "19:26",
            "exit": "19:25",
            "holiday": "ראש השנה",
        },
    })

    assert get_window_at(windows, date(2025, 9, 22), 19 * 60 + 25) is None
    assert get_window_at(windows, date(2025, 9, 22), 19 * 60 + 26) is not None
    assert get_window_at(windows, date(2025, 9, 23), 23 * 60) is not None
    assert get_window_at(windows, date(2025, 9, 24), 19 * 60 + 25) is None


def test_split_rosh_hashanah_rows_start_on_first_day_eve():
    windows = build_sacred_windows_from_cache({
        "2025-09-23": {
            "enter": "18:00",
            "exit": None,
            "holiday": "ראש השנה א",
        },
        "2025-09-24": {
            "enter": None,
            "exit": "19:30",
            "holiday": "ראש השנה ב",
        },
    })

    assert get_window_at(windows, date(2025, 9, 22), 17 * 60 + 59) is None
    assert get_window_at(windows, date(2025, 9, 22), 18 * 60) is not None
    assert get_window_at(windows, date(2025, 9, 23), 12 * 60) is not None
    assert get_window_at(windows, date(2025, 9, 24), 19 * 60 + 29) is not None
    assert get_window_at(windows, date(2025, 9, 24), 19 * 60 + 30) is None


def test_cache_loader_preserves_split_rosh_hashanah_rows():
    cache.delete(SHABBAT_CACHE_KEY)
    conn = _Connection([
        {
            "city": "תל אביב",
            "shabbat_date": "2025-09-23",
            "candle_lighting": "18:00",
            "havdalah": None,
            "parsha": None,
            "holiday_name": "ראש השנה א",
        },
        {
            "city": "תל אביב",
            "shabbat_date": "2025-09-24",
            "candle_lighting": None,
            "havdalah": "19:30",
            "parsha": None,
            "holiday_name": "ראש השנה ב",
        },
    ])

    result = get_shabbat_times_cache(conn)

    assert result["2025-09-23"]["enter"] == "18:00"
    assert result["2025-09-23"]["exit"] is None
    assert result["2025-09-24"]["enter"] is None
    assert result["2025-09-24"]["exit"] == "19:30"


def test_database_window_loader_preserves_split_rosh_hashanah_rows():
    conn = _Connection([
        {
            "city": "תל אביב",
            "shabbat_date": "2025-09-23",
            "candle_lighting": "18:00",
            "havdalah": None,
            "holiday_name": "ראש השנה א",
        },
        {
            "city": "תל אביב",
            "shabbat_date": "2025-09-24",
            "candle_lighting": None,
            "havdalah": "19:30",
            "holiday_name": "ראש השנה ב",
        },
    ])

    windows = _load_shabbat_windows(conn, date(2025, 9, 22), date(2025, 9, 24))

    assert get_window_at(windows, date(2025, 9, 22), 18 * 60) is not None
    assert get_window_at(windows, date(2025, 9, 24), 19 * 60 + 30) is None


def test_holiday_on_friday_connects_to_following_shabbat():
    windows = build_sacred_windows_from_cache({
        "2027-06-11": {
            "enter": "19:28",
            "exit": "20:37",
            "holiday": "שבועות",
        },
        "2027-06-12": {
            "enter": "19:29",
            "exit": "20:37",
            "holiday": None,
        },
    })

    assert get_window_at(windows, date(2027, 6, 10), 19 * 60 + 27) is None
    assert get_window_at(windows, date(2027, 6, 10), 19 * 60 + 28) is not None
    assert get_window_at(windows, date(2027, 6, 11), 23 * 60) is not None
    assert get_window_at(windows, date(2027, 6, 12), 20 * 60 + 36) is not None
    assert get_window_at(windows, date(2027, 6, 12), 20 * 60 + 37) is None


def test_direct_two_row_rosh_hashanah_has_no_secular_gap():
    windows = build_sacred_windows_from_cache({
        "2027-10-02": {
            "enter": "18:08",
            "exit": "19:14",
            "holiday": "ראש השנה",
        },
        "2027-10-03": {
            "enter": "19:15",
            "exit": "19:14",
            "holiday": "ראש השנה",
        },
    })

    assert get_window_at(windows, date(2027, 10, 1), 18 * 60 + 7) is None
    assert get_window_at(windows, date(2027, 10, 1), 18 * 60 + 8) is not None
    assert get_window_at(windows, date(2027, 10, 2), 19 * 60 + 14) is not None
    assert get_window_at(windows, date(2027, 10, 3), 19 * 60 + 13) is not None
    assert get_window_at(windows, date(2027, 10, 3), 19 * 60 + 14) is None


def test_future_sunday_eve_yom_kippur_uses_exact_entry_and_exit():
    windows = build_sacred_windows_from_cache({
        "2027-10-11": {
            "enter": "17:56",
            "exit": "19:04",
            "holiday": "יום כיפור",
        },
    })

    assert get_window_at(windows, date(2027, 10, 10), 17 * 60 + 55) is None
    assert get_window_at(windows, date(2027, 10, 10), 17 * 60 + 56) is not None
    assert get_window_at(windows, date(2027, 10, 11), 19 * 60 + 3) is not None
    assert get_window_at(windows, date(2027, 10, 11), 19 * 60 + 4) is None
