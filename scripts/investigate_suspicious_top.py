#!/usr/bin/env python3
"""חופר במקרים החשודים ביותר שעלו ב-find_suspicious_payments.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.config  # noqa: F401
from core.database import get_conn


def main() -> None:
    out = []
    with get_conn() as conn:

        out.append("=" * 90)
        out.append("מקרה 1: סיטוטאו ישראל - נסיעות 02/2026 שווה 2.36 ש\"ח (חצי-אחוז מהקבוע 236)")
        out.append("=" * 90)
        rows = conn.execute(
            """
            SELECT pc.id, pc.date, pc.quantity, pc.rate, pc.description,
                   pct.name AS type_name, ap.name AS apt
            FROM payment_components pc
            JOIN people p ON p.id = pc.person_id
            LEFT JOIN payment_component_types pct ON pct.id = pc.component_type_id
            LEFT JOIN apartments ap ON ap.id = pc.apartment_id
            WHERE p.name LIKE %s
              AND EXTRACT(YEAR  FROM pc.date) = 2026
              AND EXTRACT(MONTH FROM pc.date) = 2
            ORDER BY pc.date, pc.id
            """,
            ("%סיטוטאו ישראל%",),
        ).fetchall()
        for r in rows:
            amount = (r["quantity"] or 0) * (r["rate"] or 0) / 100
            out.append(
                f"  id={r['id']} {r['date']} type='{r['type_name']}'  "
                f"quantity={r['quantity']}  rate={r['rate']}  amount={amount:.2f}  "
                f"apt='{r['apt']}'  desc='{r['description']}'"
            )

        out.append("")
        out.append("היסטוריית נסיעות מלאה של סיטוטאו ישראל:")
        rows = conn.execute(
            """
            SELECT pc.date, pc.quantity, pc.rate, pc.description, pct.name AS type_name
            FROM payment_components pc
            JOIN people p ON p.id = pc.person_id
            LEFT JOIN payment_component_types pct ON pct.id = pc.component_type_id
            WHERE p.name LIKE %s AND pct.name LIKE %s
            ORDER BY pc.date
            """,
            ("%סיטוטאו ישראל%", "%נסיעות%"),
        ).fetchall()
        for r in rows:
            amount = (r["quantity"] or 0) * (r["rate"] or 0) / 100
            out.append(f"  {r['date']}  q={r['quantity']}  rate={r['rate']}  "
                       f"amount={amount:.2f}  desc='{r['description']}'")

        out.append("")
        out.append("=" * 90)
        out.append("מקרה 2: רוטמן יצחק שלמה זלמן - 21 רשומות נסיעות ב-05/2026")
        out.append("=" * 90)
        rows = conn.execute(
            """
            SELECT pc.id, pc.date, pc.quantity, pc.rate, pc.description,
                   pct.name AS type_name, ap.name AS apt
            FROM payment_components pc
            JOIN people p ON p.id = pc.person_id
            LEFT JOIN payment_component_types pct ON pct.id = pc.component_type_id
            LEFT JOIN apartments ap ON ap.id = pc.apartment_id
            WHERE p.id = 334
              AND EXTRACT(YEAR  FROM pc.date) = 2026
              AND EXTRACT(MONTH FROM pc.date) = 5
              AND pct.name LIKE %s
            ORDER BY pc.date, pc.id
            """,
            ("%נסיעות%",),
        ).fetchall()
        for r in rows:
            amount = (r["quantity"] or 0) * (r["rate"] or 0) / 100
            out.append(
                f"  id={r['id']} {r['date']} q={r['quantity']} rate={r['rate']} "
                f"amount={amount:.2f}  apt='{r['apt']}'  desc='{r['description']}'"
            )

        out.append("")
        out.append("=" * 90)
        out.append("מקרה 3: יפרח שמואל מאיר - הסעות בסכומים גבוהים מאוד")
        out.append("=" * 90)
        rows = conn.execute(
            """
            SELECT pc.date, pc.quantity, pc.rate, pc.description,
                   pct.name AS type_name, ap.name AS apt
            FROM payment_components pc
            JOIN people p ON p.id = pc.person_id
            LEFT JOIN payment_component_types pct ON pct.id = pc.component_type_id
            LEFT JOIN apartments ap ON ap.id = pc.apartment_id
            WHERE p.name LIKE %s AND pct.name LIKE %s
            ORDER BY pc.date
            """,
            ("%יפרח שמואל מאיר%", "%הסעות%"),
        ).fetchall()
        for r in rows:
            amount = (r["quantity"] or 0) * (r["rate"] or 0) / 100
            out.append(f"  {r['date']}  type='{r['type_name']}'  "
                       f"q={r['quantity']}  rate={r['rate']}  amount={amount:.2f}  "
                       f"apt='{r['apt']}'  desc='{r['description']}'")

    out_path = Path(__file__).parent / "suspicious_top_details.txt"
    out_path.write_text("\n".join(out), encoding="utf-8")
    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
