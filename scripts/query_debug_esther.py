#!/usr/bin/env python3
"""Debug: check apartment types, supplements, and Esther's data."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn


def main() -> None:
    with get_conn() as conn:
        # 1. All apartment types with supplements
        apt_types = conn.execute("""
            SELECT id, name, hourly_wage_supplement
            FROM apartment_types
            ORDER BY id
        """).fetchall()

        # 2. Esther's reports for 11/2025
        esther = conn.execute("""
            SELECT p.id, p.name, p.is_married
            FROM people p
            WHERE p.name LIKE %s OR p.name LIKE %s
            ORDER BY p.name
        """, ('%%אסיאט%%', '%%אסתר%%')).fetchall()

        esther_reports = []
        if esther:
            for e in esther:
                reports = conn.execute("""
                    SELECT tr.date, tr.start_time, tr.end_time,
                           tr.shift_type_id, st.name AS shift_name,
                           tr.apartment_id, ap.name AS apt_name,
                           ap.apartment_type_id, at.name AS apt_type_name,
                           at.hourly_wage_supplement,
                           ap.housing_array_id
                    FROM time_reports tr
                    LEFT JOIN shift_types st ON st.id = tr.shift_type_id
                    LEFT JOIN apartments ap ON ap.id = tr.apartment_id
                    LEFT JOIN apartment_types at ON at.id = ap.apartment_type_id
                    WHERE tr.person_id = %s
                      AND tr.date >= '2025-11-01' AND tr.date < '2025-12-01'
                    ORDER BY tr.date, tr.start_time
                """, (e["id"],)).fetchall()
                esther_reports.append((e, reports))

        # 3. Check shift_type_housing_rates columns and data
        cols = conn.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'shift_type_housing_rates'
            ORDER BY ordinal_position
        """).fetchall()
        col_names = [c["column_name"] for c in cols]

        housing_rates = conn.execute("""
            SELECT * FROM shift_type_housing_rates
            ORDER BY shift_type_id, housing_array_id
        """).fetchall()

    out_path = Path(__file__).parent / "debug_esther_result.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("=== סוגי דירות ותוספות ===\n")
        for at in apt_types:
            supp = at["hourly_wage_supplement"] or 0
            marker = " *** " if supp > 0 else ""
            f.write(f"  ID={at['id']}: {at['name']} | תוספת={supp} אגורות{marker}\n")

        f.write("\n\n=== חיפוש אסיאט/אסתר ===\n")
        for e, reports in esther_reports:
            f.write(f"\n  שם: {e['name']} (ID={e['id']}, נשוי={e['is_married']})\n")
            f.write(f"  דיווחים 11/2025: {len(reports)}\n")
            apt_types_seen = set()
            for r in reports:
                supp = r["hourly_wage_supplement"] or 0
                apt_key = (r["apartment_id"], r["apt_name"], r["apt_type_name"], supp)
                if apt_key not in apt_types_seen:
                    apt_types_seen.add(apt_key)
                f.write(f"    {r['date']} | {r['start_time']}-{r['end_time']} | "
                        f"משמרת: {r['shift_name']} (ID={r['shift_type_id']}) | "
                        f"דירה: {r['apt_name']} (סוג={r['apt_type_name']}, "
                        f"תוספת={supp}, מערך={r['housing_array_id']})\n")

            f.write(f"\n  סוגי דירות ייחודיים:\n")
            for apt_id, apt_name, apt_type, supp in sorted(apt_types_seen):
                f.write(f"    דירה {apt_name} | סוג: {apt_type} | תוספת: {supp}\n")

        f.write(f"\n\n=== תעריפי מערכי דיור - עמודות: {col_names} ===\n")
        for hr in housing_rates:
            f.write(f"  {dict(hr)}\n")

    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
