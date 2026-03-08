#!/usr/bin/env python3
"""מדריכים נשואים עם משמרת חול שמתחילה מוקדם - בכל החודשים."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn
from core.constants import WEEKDAY_SHIFT_TYPE_ID
from core.history import get_all_person_statuses_for_month


def main() -> None:
    with get_conn() as conn:
        months = conn.execute("""
            SELECT DISTINCT
                EXTRACT(YEAR FROM date)::int as year,
                EXTRACT(MONTH FROM date)::int as month
            FROM time_reports
            ORDER BY year, month
        """).fetchall()

        results = []
        for row in months:
            year, month = int(row["year"]), int(row["month"])
            rows = conn.execute("""
                SELECT p.id, p.name, MIN(tr.start_time) as min_start
                FROM time_reports tr
                JOIN people p ON p.id = tr.person_id
                WHERE tr.shift_type_id = %s
                  AND EXTRACT(YEAR FROM tr.date) = %s
                  AND EXTRACT(MONTH FROM tr.date) = %s
                GROUP BY p.id, p.name
            """, (WEEKDAY_SHIFT_TYPE_ID, year, month)).fetchall()

            if not rows:
                continue

            person_ids = [r["id"] for r in rows]
            statuses = get_all_person_statuses_for_month(conn.conn, person_ids, year, month)
            married = [r for r in rows if statuses.get(r["id"], {}).get("is_married")]

            if not married:
                continue

            earliest = min(married, key=lambda r: r["min_start"] or "99:99")
            results.append({
                "month": f"{month:02d}/{year}",
                "guide": earliest["name"],
                "start": str(earliest["min_start"]),
            })

    out_path = Path(__file__).parent / "married_weekday_earliest_result.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("מדריכים נשואים עם משמרת חול שמתחילה הכי מוקדם - לפי חודש\n")
        f.write("=" * 50 + "\n\n")
        for r in results:
            f.write(f"{r['month']}: {r['guide']} (התחלה: {r['start']})\n")
        f.write(f"\nסה\"כ {len(results)} חודשים\n")
    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
