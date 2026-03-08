#!/usr/bin/env python3
"""שאילתה: מדריכים עם דיווח מחלה/חופשה ב-01/2026 ו-02/2026."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn
from core.constants import SICK_SHIFT_TYPE_ID, VACATION_SHIFT_TYPE_ID


def main() -> None:
    results = []
    with get_conn() as conn:
        for year, month in [(2026, 1), (2026, 2)]:
            rows = conn.execute("""
                SELECT DISTINCT p.id, p.name,
                       st.id as shift_type_id,
                       st.name as shift_name,
                       tr.date
                FROM time_reports tr
                JOIN people p ON p.id = tr.person_id
                JOIN shift_types st ON st.id = tr.shift_type_id
                WHERE tr.shift_type_id IN (%s, %s)
                  AND EXTRACT(YEAR FROM tr.date) = %s
                  AND EXTRACT(MONTH FROM tr.date) = %s
                ORDER BY p.name, st.name, tr.date
            """, (SICK_SHIFT_TYPE_ID, VACATION_SHIFT_TYPE_ID, year, month)).fetchall()

            month_label = f"{month:02d}/{year}"
            sick = []
            vacation = []
            for r in rows:
                if "מחלה" in (r["shift_name"] or ""):
                    sick.append((r["name"], r["date"]))
                elif "חופשה" in (r["shift_name"] or ""):
                    vacation.append((r["name"], r["date"]))

            sick_guides = sorted(set(n for n, _ in sick))
            vacation_guides = sorted(set(n for n, _ in vacation))

            results.append({
                "month": month_label,
                "sick": sick_guides,
                "vacation": vacation_guides,
            })

    out_path = Path(__file__).parent / "sick_vacation_result.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        for r in results:
            f.write(f"\n=== {r['month']} ===\n")
            f.write(f"דיווח מחלה: {', '.join(r['sick']) if r['sick'] else '(אין)'}\n")
            f.write(f"דיווח חופשה: {', '.join(r['vacation']) if r['vacation'] else '(אין)'}\n")
    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
