#!/usr/bin/env python3
"""
זיהוי מדריכים שמושפעים מבאג is_variable_rate + hourly_wage_supplement.

מדריכים שעובדים בדירות עם hourly_wage_supplement > 0
ויש להם דיווחי שעות - הם המושפעים מהשינוי.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn


def main() -> None:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT p.id, p.name, at.name AS apt_type_name,
                   at.hourly_wage_supplement
            FROM time_reports tr
            JOIN people p ON p.id = tr.person_id
            JOIN apartments ap ON ap.id = tr.apartment_id
            JOIN apartment_types at ON at.id = ap.apartment_type_id
            WHERE at.hourly_wage_supplement > 0
              AND p.is_active::integer = 1
            ORDER BY p.name
        """).fetchall()

    out_path = Path(__file__).parent / "affected_guides_result.txt"
    seen_names = set()
    unique_rows = []
    for r in rows:
        if r["name"] not in seen_names:
            seen_names.add(r["name"])
            unique_rows.append(r)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("מדריכים מושפעים מבאג is_variable_rate (hourly_wage_supplement > 0):\n")
        f.write("=" * 70 + "\n\n")
        for r in unique_rows:
            supplement = r["hourly_wage_supplement"]
            f.write(f"  {r['name']}  (סוג דירה: {r['apt_type_name']}, תוספת: {supplement} אגורות)\n")
        f.write(f"\n\nסה\"כ מדריכים מושפעים: {len(unique_rows)}\n")

    print(f"Results written to {out_path}")
    for r in unique_rows:
        print(f"  {r['name']}  (תוספת: {r['hourly_wage_supplement']} אגורות)")
    print(f"\nTotal: {len(unique_rows)}")


if __name__ == "__main__":
    main()
