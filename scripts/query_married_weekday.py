#!/usr/bin/env python3
"""שאילתה: מדריך נשוי עם משמרת חול שהתחילה הכי מוקדם ב-02/2026."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn
from core.constants import WEEKDAY_SHIFT_TYPE_ID
from core.history import get_all_person_statuses_for_month


def main() -> None:
    with get_conn() as conn:
        # People with weekday shift in Feb 2026 - one row per person with earliest start
        rows_simple = conn.execute("""
            SELECT p.id, p.name, MIN(tr.start_time) as min_start
            FROM time_reports tr
            JOIN people p ON p.id = tr.person_id
            WHERE tr.shift_type_id = %s
              AND EXTRACT(YEAR FROM tr.date) = 2026
              AND EXTRACT(MONTH FROM tr.date) = 2
            GROUP BY p.id, p.name
        """, (WEEKDAY_SHIFT_TYPE_ID,)).fetchall()

        person_ids = [r["id"] for r in rows_simple]
        statuses = get_all_person_statuses_for_month(conn.conn, person_ids, 2026, 2)

        # Filter married only (using historical status for Feb 2026)
        married = [
            r for r in rows_simple
            if statuses.get(r["id"], {}).get("is_married")
        ]
        if married:
            earliest = min(married, key=lambda r: r["min_start"] or "99:99")
            winner = earliest["name"]
            win_time = earliest["min_start"]
        else:
            winner = None
            win_time = None

    out_path = Path(__file__).parent / "married_weekday_result.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        if winner:
            f.write(f"מדריך נשוי עם משמרת חול שהתחילה הכי מוקדם ב-02/2026:\n")
            f.write(f"{winner} - שעת התחלה: {win_time}\n")
        else:
            f.write("אין מדריכים נשואים עם משמרת חול בפברואר 2026\n")
    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
