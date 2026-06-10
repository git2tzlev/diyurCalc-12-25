#!/usr/bin/env python3
"""מאתר תשלומים חשודים ב-payment_components (סכומים חריגים).

הלוגיקה: לכל סוג רכיב תשלום (component_type), מחשבים את הסכומים השכיחים
לכל מדריך בכל חודש, ומסמנים שורות שסכומן חריג ביחס לרוב המדריכים באותו סוג.
"""
import sys
from pathlib import Path
from statistics import median

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import core.config  # noqa: F401
from core.database import get_conn


YEAR_FROM = 2025
MONTH_FROM = 11
YEAR_TO = 2026
MONTH_TO = 5


def main() -> None:
    out = []
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT
                p.id  AS person_id,
                p.name AS person_name,
                EXTRACT(YEAR  FROM pc.date)::int AS y,
                EXTRACT(MONTH FROM pc.date)::int AS m,
                pc.component_type_id,
                COALESCE(pct.name, 'אחר') AS type_name,
                ha.name AS housing_array_name,
                SUM(pc.quantity * pc.rate)::float / 100 AS total_amount,
                COUNT(*) AS rows_count,
                MIN(pc.date) AS first_date,
                MAX(pc.date) AS last_date,
                array_agg(DISTINCT pc.description) AS descriptions
            FROM payment_components pc
            LEFT JOIN payment_component_types pct ON pct.id = pc.component_type_id
            LEFT JOIN apartments ap ON ap.id = pc.apartment_id
            LEFT JOIN housing_arrays ha ON ha.id = ap.housing_array_id
            JOIN people p ON p.id = pc.person_id
            WHERE pc.date >= make_date(%s, %s, 1)
              AND pc.date <  (make_date(%s, %s, 1) + INTERVAL '1 month')
            GROUP BY p.id, p.name, y, m, pc.component_type_id, pct.name, ha.name
            """,
            (YEAR_FROM, MONTH_FROM, YEAR_TO, MONTH_TO),
        ).fetchall()

        zeros = [r for r in rows if r["total_amount"] == 0]
        negatives = [r for r in rows if r["total_amount"] < 0]

        by_type: dict[int, list] = {}
        for r in rows:
            by_type.setdefault(r["component_type_id"], []).append(r)

        outliers = []
        for type_id, group in by_type.items():
            amounts = [g["total_amount"] for g in group if g["total_amount"] > 0]
            if len(amounts) < 3:
                continue
            med = median(amounts)
            from statistics import quantiles
            q = quantiles(amounts, n=4)
            iqr = q[2] - q[0]
            high_limit = q[2] + 3 * iqr if iqr > 0 else med * 3
            low_limit = q[0] - 3 * iqr if iqr > 0 else med / 3

            for g in group:
                amt = g["total_amount"]
                if amt > 0 and (amt > high_limit or amt < low_limit):
                    outliers.append((g, med, low_limit, high_limit))

        out.append(f"בדיקת תשלומים חשודים בטווח {MONTH_FROM:02d}/{YEAR_FROM} עד {MONTH_TO:02d}/{YEAR_TO}")
        out.append("=" * 90)
        out.append(f"סה\"כ רשומות (sum לפי person+month+type): {len(rows)}")
        out.append("")

        out.append(f"== תשלומים בסכום אפס ({len(zeros)}) ==")
        for r in zeros:
            descs = ", ".join(d for d in (r['descriptions'] or []) if d)
            out.append(
                f"  {r['y']}-{r['m']:02d}  {r['person_name']:<24} "
                f"type='{r['type_name']}'  amount=0.00  rows={r['rows_count']}  "
                f"arr='{r['housing_array_name']}'  desc='{descs}'"
            )

        out.append("")
        out.append(f"== תשלומים שליליים ({len(negatives)}) ==")
        for r in negatives:
            descs = ", ".join(d for d in (r['descriptions'] or []) if d)
            out.append(
                f"  {r['y']}-{r['m']:02d}  {r['person_name']:<24} "
                f"type='{r['type_name']}'  amount={r['total_amount']:.2f}  "
                f"rows={r['rows_count']}  arr='{r['housing_array_name']}'  desc='{descs}'"
            )

        out.append("")
        out.append(f"== חריגים סטטיסטיים ({len(outliers)}) ==")
        out.append("  (סכום חודשי לאותו סוג רכיב שחורג ב-3 IQR מהחציון)")
        outliers.sort(key=lambda x: (x[0]["type_name"], -x[0]["total_amount"]))
        for r, med, lo, hi in outliers:
            descs = ", ".join(d for d in (r['descriptions'] or []) if d)
            out.append(
                f"  {r['y']}-{r['m']:02d}  {r['person_name']:<24} "
                f"type='{r['type_name']}'  amount={r['total_amount']:>9.2f}  "
                f"(חציון לסוג={med:.2f}, גבול תקין={lo:.2f}..{hi:.2f})  "
                f"rows={r['rows_count']}  arr='{r['housing_array_name']}'  desc='{descs}'"
            )

        out.append("")
        out.append("== סקירת סכומים נפוצים לכל סוג (רף עליון לתסמין חריג) ==")
        for type_id, group in sorted(by_type.items()):
            positives = [g["total_amount"] for g in group if g["total_amount"] > 0]
            if not positives:
                continue
            type_name = group[0]["type_name"]
            cnt = len(positives)
            mn = min(positives)
            mx = max(positives)
            med = median(positives)
            out.append(
                f"  type_id={type_id:<3} '{type_name:<25}'  "
                f"חודשים={cnt}  min={mn:.2f}  median={med:.2f}  max={mx:.2f}"
            )

    out_path = Path(__file__).parent / "suspicious_payments.txt"
    out_path.write_text("\n".join(out), encoding="utf-8")
    print(f"Results written to {out_path}")


if __name__ == "__main__":
    main()
