#!/usr/bin/env python3
"""בדיקת השעות הגולמיות של יפרח תהילה בתאריכים הבעייתיים."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from core.database import get_conn


def main() -> None:
    target_dates = ('2026-03-13', '2026-03-14', '2026-03-27', '2026-03-28')
    out_path = Path(__file__).parent / "debug_yifrach_dates_result.txt"
    f = open(out_path, "w", encoding="utf-8")
    print = f.write  # noqa
    def w(s):
        f.write(s + "\n")

    with get_conn() as conn:
        person = conn.execute(
            "SELECT id, name FROM people WHERE name LIKE %s",
            ("%יפרח%תהיל%",),
        ).fetchone()
        person_id = person["id"]

        rows = conn.execute("""
            SELECT tr.date, tr.start_time, tr.end_time, tr.shift_type_id,
                   st.name AS shift_name,
                   tr.apartment_id, ap.name AS apartment_name,
                   tr.description
            FROM time_reports tr
            LEFT JOIN shift_types st ON st.id = tr.shift_type_id
            LEFT JOIN apartments ap ON ap.id = tr.apartment_id
            WHERE tr.person_id = %s
              AND tr.date IN %s
            ORDER BY tr.date, tr.start_time
        """, (person_id, target_dates)).fetchall()

        w(f"=== {person['name']} - דיווחים גולמיים ===\n")
        for r in rows:
            w(f"  {r['date']}  {r['start_time']} -> {r['end_time']}  "
              f"shift={r['shift_type_id']} ({r['shift_name']})  "
              f"דירה={r['apartment_name']}")
            if r['description']:
                w(f"      הערה: {r['description']}")

        w("\n=== הגדרת סגמנטים לתגבור 108/109 ===")
        segs = conn.execute("""
            SELECT shift_type_id, segment_type, start_time, end_time, order_index
            FROM shift_time_segments
            WHERE shift_type_id IN (108, 109)
            ORDER BY shift_type_id, order_index
        """).fetchall()
        for s in segs:
            w(f"  shift={s['shift_type_id']}  "
              f"{s['start_time']}-{s['end_time']}  "
              f"type={s['segment_type']}  order={s['order_index']}")

        w("\n=== zmanim שבת בתאריכי המטרה ===")
        # שלוף את כל העמודות
        sh = conn.execute("""
            SELECT *
            FROM shabbat_times
            WHERE shabbat_date IN %s
            ORDER BY shabbat_date
        """, (target_dates,)).fetchall()
        for s in sh:
            w(f"  {dict(s)}")

        w("\n=== zmanim שבת לימים סמוכים ===")
        nearby = conn.execute("""
            SELECT *
            FROM shabbat_times
            WHERE shabbat_date BETWEEN '2026-03-12' AND '2026-03-15'
               OR shabbat_date BETWEEN '2026-03-26' AND '2026-03-29'
            ORDER BY shabbat_date
        """).fetchall()
        for s in nearby:
            w(f"  {dict(s)}")
    f.close()
    import sys as _sys
    _sys.stdout.write(f"Results: {out_path}\n")


if __name__ == "__main__":
    main()
