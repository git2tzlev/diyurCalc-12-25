#!/usr/bin/env python3
"""שאילתה: תגבור לא בדירה טיפולית + דירה רגילה משולמת כטיפולית."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn
from core.constants import (
    TAGBUR_FRIDAY_SHIFT_ID,
    TAGBUR_SHABBAT_SHIFT_ID,
    THERAPEUTIC_APT_TYPE,
    REGULAR_APT_TYPE,
)


def main() -> None:
    year, month = 2026, 2  # 02/2026
    with get_conn() as conn:
        # 1. תגבור מפורש (108, 109) בדירה שאינה טיפולית
        tagbur_not_therapeutic = conn.execute("""
            SELECT DISTINCT p.name, st.name as shift_name, tr.date, a.name as apt_name,
                   a.apartment_type_id, tr.rate_apartment_type_id
            FROM time_reports tr
            JOIN people p ON p.id = tr.person_id
            JOIN shift_types st ON st.id = tr.shift_type_id
            JOIN apartments a ON a.id = tr.apartment_id
            WHERE EXTRACT(YEAR FROM tr.date) = %s
              AND EXTRACT(MONTH FROM tr.date) = %s
              AND tr.shift_type_id IN (%s, %s)
              AND a.apartment_type_id != %s
            ORDER BY p.name, tr.date
        """, (year, month, TAGBUR_FRIDAY_SHIFT_ID, TAGBUR_SHABBAT_SHIFT_ID, THERAPEUTIC_APT_TYPE)).fetchall()

        # 2. תגבור (108/109) בדירה רגילה אך משולם כטיפולית (rate_apartment_type_id=2)
        tagbur_regular_paid_therapeutic = conn.execute("""
            SELECT DISTINCT p.name, st.name as shift_name, tr.date, a.name as apt_name
            FROM time_reports tr
            JOIN people p ON p.id = tr.person_id
            JOIN shift_types st ON st.id = tr.shift_type_id
            JOIN apartments a ON a.id = tr.apartment_id
            WHERE EXTRACT(YEAR FROM tr.date) = %s
              AND EXTRACT(MONTH FROM tr.date) = %s
              AND tr.shift_type_id IN (%s, %s)
              AND a.apartment_type_id = %s
              AND tr.rate_apartment_type_id = %s
            ORDER BY p.name, tr.date
        """, (year, month, TAGBUR_FRIDAY_SHIFT_ID, TAGBUR_SHABBAT_SHIFT_ID, REGULAR_APT_TYPE, THERAPEUTIC_APT_TYPE)).fetchall()

        # 3. דירה רגילה (כל משמרת) משולמת כטיפולית
        rate_override_therapeutic = conn.execute("""
            SELECT DISTINCT p.name, st.name as shift_name, tr.date, a.name as apt_name,
                   a.apartment_type_id as actual_apt, tr.rate_apartment_type_id as rate_apt
            FROM time_reports tr
            JOIN people p ON p.id = tr.person_id
            JOIN shift_types st ON st.id = tr.shift_type_id
            JOIN apartments a ON a.id = tr.apartment_id
            WHERE EXTRACT(YEAR FROM tr.date) = %s
              AND EXTRACT(MONTH FROM tr.date) = %s
              AND a.apartment_type_id = %s
              AND tr.rate_apartment_type_id = %s
            ORDER BY p.name, tr.date
        """, (year, month, REGULAR_APT_TYPE, THERAPEUTIC_APT_TYPE)).fetchall()

    # פלט
    out_path = Path(__file__).parent / "tagbur_not_therapeutic_result.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"=== 1. תגבור (108/109) לא בדירה טיפולית - {month:02d}/{year} ===\n\n")
        if tagbur_not_therapeutic:
            guides1 = sorted(set(r["name"] for r in tagbur_not_therapeutic))
            for g in guides1:
                f.write(f"{g}\n")
            f.write(f"\nסה\"כ: {len(guides1)} מדריכים\n")
        else:
            f.write("(אין)\n")

        f.write(f"\n\n=== 2. תגבור בדירה רגילה אך משולם כטיפולית - {month:02d}/{year} ===\n\n")
        if tagbur_regular_paid_therapeutic:
            guides2a = sorted(set(r["name"] for r in tagbur_regular_paid_therapeutic))
            for g in guides2a:
                f.write(f"{g}\n")
            f.write(f"\nסה\"כ: {len(guides2a)} מדריכים\n")
        else:
            f.write("(אין)\n")

        f.write(f"\n\n=== 3. דירה רגילה משולמת כטיפולית (כל משמרת) - {month:02d}/{year} ===\n\n")
        if rate_override_therapeutic:
            guides3 = sorted(set(r["name"] for r in rate_override_therapeutic))
            for g in guides3:
                f.write(f"{g}\n")
            f.write(f"\nסה\"כ: {len(guides3)} מדריכים\n")
            f.write("\nפירוט (דוגמאות):\n")
            for r in rate_override_therapeutic[:10]:
                f.write(f"  {r['name']} | {r['date']} | {r['apt_name']} | {r['shift_name']}\n")
        else:
            f.write("(אין)\n")

    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
