#!/usr/bin/env python3
"""שאילתה: מדריכים עם משמרת תגבור בדירה טיפולית בחודש נתון."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn
from core.constants import (
    TAGBUR_FRIDAY_SHIFT_ID,
    TAGBUR_SHABBAT_SHIFT_ID,
    FRIDAY_SHIFT_ID,
    SHABBAT_SHIFT_ID,
    THERAPEUTIC_APT_TYPE,
    REGULAR_APT_TYPE,
)


def main() -> None:
    year, month = 2026, 2  # 02/2026
    with get_conn() as conn:
        # תגבור מפורש (108, 109) או תגבור משתמע (105, 106 בדירה טיפולית עם תעריף דירה רגילה)
        # דירה טיפולית = apartment_type_id = 2
        rows = conn.execute("""
            SELECT DISTINCT p.name, st.name as shift_name, tr.date, a.name as apt_name,
                   COALESCE(tr.rate_apartment_type_id, a.apartment_type_id) as rate_apt
            FROM time_reports tr
            JOIN people p ON p.id = tr.person_id
            JOIN shift_types st ON st.id = tr.shift_type_id
            JOIN apartments a ON a.id = tr.apartment_id
            WHERE EXTRACT(YEAR FROM tr.date) = %s
              AND EXTRACT(MONTH FROM tr.date) = %s
              AND a.apartment_type_id = %s
              AND (
                  tr.shift_type_id IN (%s, %s)
                  OR (tr.shift_type_id IN (%s, %s)
                      AND (tr.rate_apartment_type_id IS NULL OR tr.rate_apartment_type_id = %s))
              )
            ORDER BY p.name, tr.date
        """, (
            year, month, THERAPEUTIC_APT_TYPE,
            TAGBUR_FRIDAY_SHIFT_ID, TAGBUR_SHABBAT_SHIFT_ID,
            FRIDAY_SHIFT_ID, SHABBAT_SHIFT_ID, REGULAR_APT_TYPE
        )).fetchall()

    guides = sorted(set(r["name"] for r in rows))
    out_path = Path(__file__).parent / "tagbur_therapeutic_result.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"מדריכים עם משמרת תגבור בדירה טיפולית ב-{month:02d}/{year}:\n\n")
        for g in guides:
            f.write(f"{g}\n")
        f.write(f"\nסה\"כ: {len(guides)} מדריכים\n")
    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
