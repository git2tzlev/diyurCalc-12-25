#!/usr/bin/env python3
"""בדיקת תשלומי payment_components של אטיאס אסתר לחודש 02/2026,
ובפרט נסיעות ותומך מקצועי."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.config  # noqa: F401
from core.database import get_conn


def main() -> None:
    name_like = "%אטיאס אסתר%"
    year, month = 2026, 2

    with get_conn() as conn:
        people = conn.execute(
            "SELECT id, name, id_number FROM people WHERE name LIKE %s ORDER BY id",
            (name_like,),
        ).fetchall()
        if not people:
            print("לא נמצא אדם בשם אטיאס אסתר")
            return

        out_lines = [f"נמצאו {len(people)} רשומות people עבור 'אטיאס אסתר':"]
        for p in people:
            out_lines.append(f"  id={p['id']}  name={p['name']!r}  id_number={p['id_number']}")
        out_lines.append("")

        type_rows = conn.execute(
            """
            SELECT id, name, COALESCE(for_pension, FALSE) AS for_pension
            FROM payment_component_types
            ORDER BY id
            """
        ).fetchall()
        out_lines.append("== כל סוגי ה-payment_components במערכת ==")
        for t in type_rows:
            out_lines.append(f"  id={t['id']:>3}  for_pension={t['for_pension']}  name={t['name']}")
        out_lines.append("")

        for p in people:
            out_lines.append("=" * 80)
            out_lines.append(f"== payment_components ל-{p['name']!r} (id={p['id']}) "
                             f"בחודש {month:02d}/{year} ==")
            comps = conn.execute(
                """
                SELECT pc.id, pc.date, pc.quantity, pc.rate, pc.description,
                       pc.component_type_id,
                       COALESCE(pct.name, 'unknown') AS type_name,
                       COALESCE(pct.for_pension, FALSE) AS for_pension,
                       ap.id AS apartment_id,
                       ap.name AS apartment_name,
                       ha.id  AS housing_array_id,
                       ha.name AS housing_array_name
                FROM payment_components pc
                LEFT JOIN payment_component_types pct ON pct.id = pc.component_type_id
                LEFT JOIN apartments ap ON ap.id = pc.apartment_id
                LEFT JOIN housing_arrays ha ON ha.id = ap.housing_array_id
                WHERE pc.person_id = %s
                  AND EXTRACT(YEAR  FROM pc.date) = %s
                  AND EXTRACT(MONTH FROM pc.date) = %s
                ORDER BY pc.date, pc.id
                """,
                (p["id"], year, month),
            ).fetchall()

            if not comps:
                out_lines.append("  (אין רשומות payment_components בחודש זה)")
                continue

            total_travel = 0.0
            total_support = 0.0
            for c in comps:
                amount = (c["quantity"] or 0) * (c["rate"] or 0) / 100.0
                is_travel = "נסיעות" in (c["type_name"] or "") or "נסיעה" in (c["type_name"] or "")
                is_support = "תומך מקצועי" in (c["type_name"] or "")
                tag = ""
                if is_travel:
                    tag = "  <נסיעות>"
                    total_travel += amount
                elif is_support:
                    tag = "  <תומך מקצועי>"
                    total_support += amount
                out_lines.append(
                    f"  {c['date']}  type_id={c['component_type_id']}  "
                    f"type={c['type_name']!r}  q={c['quantity']}  rate={c['rate']}  "
                    f"amount={amount:.2f}  "
                    f"apt={c['apartment_name']!r} (id={c['apartment_id']})  "
                    f"arr={c['housing_array_name']!r}  desc={c['description']!r}{tag}"
                )

            out_lines.append("")
            out_lines.append(f"  סיכום נסיעות:      {total_travel:.2f} ש\"ח")
            out_lines.append(f"  סיכום תומך מקצועי: {total_support:.2f} ש\"ח")
            out_lines.append("")

            recurring_check = conn.execute(
                """
                SELECT EXTRACT(YEAR FROM pc.date)::int  AS y,
                       EXTRACT(MONTH FROM pc.date)::int AS m,
                       pct.name AS type_name,
                       SUM(pc.quantity * pc.rate)::float / 100 AS total
                FROM payment_components pc
                LEFT JOIN payment_component_types pct ON pct.id = pc.component_type_id
                WHERE pc.person_id = %s
                  AND (pct.name LIKE %s
                       OR pct.name LIKE %s
                       OR pct.name LIKE %s)
                GROUP BY 1, 2, 3
                ORDER BY 1, 2
                """,
                (p["id"], "%נסיעות%", "%נסיעה%", "%תומך מקצועי%"),
            ).fetchall()
            if recurring_check:
                out_lines.append("  היסטוריה חודשית (נסיעות / תומך מקצועי):")
                for row in recurring_check:
                    out_lines.append(
                        f"    {int(row['y'])}-{int(row['m']):02d}  {row['type_name']!r}  "
                        f"סכום חודשי={row['total']:.2f}"
                    )

    out_path = Path(__file__).parent / "esther_feb2026_payments.txt"
    out_path.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
