#!/usr/bin/env python3
"""
Seeding של ימי פורים בטבלת special_days ל-20 שנה קדימה.

לוגיקה:
- פורים = י"ד אדר (או אדר ב' בשנה מעוברת)
- שושן פורים (ירושלים) = ט"ו אדר
- תשפ"ו (5786) חריג: ירושלים מקבלת כמו שאר הארץ (י"ד אדר)

- שנה שבה פורים זהה לשאר הארץ ולירושלים → שורה אחת (city_filter=NULL)
- שנה שבה שונה → שתי שורות:
    1. י"ד אדר עבור כל הערים **חוץ מירושלים** (city_exclude=['ירושלים'])
    2. ט"ו אדר עבור ירושלים בלבד (city_filter=['ירושלים'])

התעריף: 150% בטווח 08:00-22:00 באותו יום. כוננות = תעריף שבת.

הרצה:
    py scripts/seed_purim_days.py [--years 20] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from convertdate import hebrew

from core.database import get_pooled_connection, return_connection


PURIM_START_TIME = "08:00"
PURIM_END_TIME = "22:00"
PURIM_RATE_PCT = 150
PURIM_STANDBY_MODE = "shabbat"
JERUSALEM = "ירושלים"


def _get_purim_date_for_year(gregorian_year: int, is_jerusalem: bool) -> date:
    """חישוב תאריך פורים לשנה גרגוריאנית נתונה."""
    hebrew_year, _, _ = hebrew.from_gregorian(gregorian_year, 3, 1)  # מרץ תמיד חופף לאדר
    adar = 13 if hebrew.leap(hebrew_year) else 12
    purim_day = 15 if is_jerusalem and hebrew_year != 5786 else 14
    g_year, g_month, g_day = hebrew.to_gregorian(hebrew_year, adar, purim_day)
    return date(g_year, g_month, g_day)


def _build_purim_rows(gregorian_year: int) -> list[dict]:
    """בניית שורות Purim לשנה גרגוריאנית נתונה."""
    non_jerusalem_date = _get_purim_date_for_year(gregorian_year, is_jerusalem=False)
    jerusalem_date = _get_purim_date_for_year(gregorian_year, is_jerusalem=True)

    hebrew_year, _, _ = hebrew.from_gregorian(gregorian_year, 3, 1)
    name = f"פורים {_hebrew_year_label(hebrew_year)}"

    if non_jerusalem_date == jerusalem_date:
        return [{
            "name": name,
            "purim_date": non_jerusalem_date,
            "city_filter": None,
            "city_exclude": None,
        }]

    return [
        {
            "name": name,
            "purim_date": non_jerusalem_date,
            "city_filter": None,
            "city_exclude": [JERUSALEM],
        },
        {
            "name": f"שושן פורים {_hebrew_year_label(hebrew_year)}",
            "purim_date": jerusalem_date,
            "city_filter": [JERUSALEM],
            "city_exclude": None,
        },
    ]


def _hebrew_year_label(hebrew_year: int) -> str:
    """תווית קצרה לשנה עברית (למשל 'תשפ"ו'). מטפל ב-15 (ט"ו) ו-16 (ט"ז)."""
    letters = {
        1: "א", 2: "ב", 3: "ג", 4: "ד", 5: "ה", 6: "ו", 7: "ז", 8: "ח", 9: "ט",
        10: "י", 20: "כ", 30: "ל", 40: "מ", 50: "נ", 60: "ס", 70: "ע", 80: "פ", 90: "צ",
        100: "ק", 200: "ר", 300: "ש", 400: "ת",
    }
    short = hebrew_year % 1000
    result = ""
    for value in (400, 300, 200, 100):
        while short >= value:
            result += letters[value]
            short -= value
    if short == 15:
        result += "טו"
    elif short == 16:
        result += "טז"
    else:
        for value in (90, 80, 70, 60, 50, 40, 30, 20, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1):
            while short >= value:
                result += letters[value]
                short -= value
    if len(result) >= 2:
        result = result[:-1] + '"' + result[-1]
    return result


def seed_purim_days(years: int, dry_run: bool) -> None:
    """הוספת שורות פורים לטבלת special_days ל-N שנים קדימה."""
    current_year = date.today().year
    target_years = range(current_year, current_year + years)

    all_rows: list[tuple] = []
    for g_year in target_years:
        for row in _build_purim_rows(g_year):
            all_rows.append((
                "purim",
                row["name"],
                row["purim_date"],
                PURIM_START_TIME,
                row["purim_date"],
                PURIM_END_TIME,
                PURIM_RATE_PCT,
                PURIM_STANDBY_MODE,
                row["city_filter"],
                row["city_exclude"],
                "auto_seeded",
            ))

    print(f"נבנו {len(all_rows)} שורות פורים ל-{years} שנים ({current_year}-{current_year + years - 1})")
    for row in all_rows:
        filter_note = ""
        if row[8]:
            filter_note = f" [רק: {', '.join(row[8])}]"
        elif row[9]:
            filter_note = f" [חוץ מ: {', '.join(row[9])}]"
        print(f"  {row[2]} — {row[1]}{filter_note}")

    if dry_run:
        print("\n-- dry-run: לא מוכנס ל-DB --")
        return

    conn = get_pooled_connection()
    try:
        cursor = conn.cursor()
        try:
            cursor.executemany("""
                INSERT INTO special_days
                    (day_type, name, start_date, start_time, end_date, end_time,
                     rate_pct, standby_mode, city_filter, city_exclude, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, all_rows)
            conn.commit()
            print(f"\n✓ הוכנסו {cursor.rowcount} שורות ל-special_days")
        finally:
            cursor.close()
    finally:
        return_connection(conn)


def main() -> None:
    parser = argparse.ArgumentParser(description="Seeding ימי פורים בטבלת special_days")
    parser.add_argument("--years", type=int, default=20, help="מספר שנים קדימה (ברירת מחדל 20)")
    parser.add_argument("--dry-run", action="store_true", help="הצגה בלבד ללא הכנסה ל-DB")
    args = parser.parse_args()
    seed_purim_days(args.years, args.dry_run)


if __name__ == "__main__":
    main()
