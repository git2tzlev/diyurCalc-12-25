#!/usr/bin/env python3
"""חיפוש מדריכים בשם רוטמן/דומה לחיפוש."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.config  # noqa: F401
from core.database import get_conn


def main() -> None:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT p.id, p.name, p.id_number
            FROM people p
            WHERE p.name LIKE %s OR p.name LIKE %s
            ORDER BY p.name
            """,
            ("%רוטמן%", "%שלמה זלמן%"),
        ).fetchall()

        out = ["מועמדים שמכילים 'רוטמן' או 'שלמה זלמן':"]
        for r in rows:
            out.append(f"  id={r['id']} name={r['name']} id_number={r['id_number']}")
            arrays = conn.execute(
                """
                SELECT DISTINCT ha.id, ha.name,
                       MIN(tr.date) AS first_date, MAX(tr.date) AS last_date
                FROM time_reports tr
                JOIN apartments ap ON ap.id = tr.apartment_id
                JOIN housing_arrays ha ON ha.id = ap.housing_array_id
                WHERE tr.person_id = %s
                GROUP BY ha.id, ha.name
                ORDER BY ha.name
                """,
                (r["id"],),
            ).fetchall()
            for a in arrays:
                out.append(f"      array={a['name']} (id={a['id']}) "
                           f"first={a['first_date']} last={a['last_date']}")

    print("\n".join(out))


if __name__ == "__main__":
    main()
