#!/usr/bin/env python3
"""Check if standby rates were changed."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2.extras
from core.database import get_conn


def main():
    with get_conn() as conn:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Check for rates relevant to our guides
        print("=== Relevant standby rates ===")
        
        # Shaliach: seg=2 (weekday), seg=13 (night), apt=1, single
        print("\nShaliach (apt=1, single):")
        cursor.execute("""
            SELECT id, segment_id, apartment_type_id, amount, marital_status, created_at, updated_at
            FROM standby_rates 
            WHERE (segment_id IN (2, 13) AND apartment_type_id = 1 AND marital_status = 'single')
               OR (segment_id IN (2, 13) AND apartment_type_id IS NULL AND marital_status = 'single')
            ORDER BY segment_id, apartment_type_id NULLS LAST
        """)
        for r in cursor.fetchall():
            changed = r['created_at'] != r['updated_at'] if r['updated_at'] else False
            print(f"  id={r['id']} seg={r['segment_id']} apt={r['apartment_type_id']} "
                  f"amount={r['amount']} ({r['amount']/100:.2f}) "
                  f"created={r['created_at']} updated={r['updated_at']} "
                  f"CHANGED={changed}")

        # Kramer: seg=10 (Friday), seg=8 (Shabbat), apt=5, married  
        print("\nKramer (apt=5, married):")
        cursor.execute("""
            SELECT id, segment_id, apartment_type_id, amount, marital_status, created_at, updated_at
            FROM standby_rates 
            WHERE (segment_id IN (8, 10) AND apartment_type_id = 5 AND marital_status = 'married')
               OR (segment_id IN (8, 10) AND apartment_type_id IS NULL AND marital_status = 'married')
            ORDER BY segment_id, apartment_type_id NULLS LAST
        """)
        for r in cursor.fetchall():
            changed = r['created_at'] != r['updated_at'] if r['updated_at'] else False
            print(f"  id={r['id']} seg={r['segment_id']} apt={r['apartment_type_id']} "
                  f"amount={r['amount']} ({r['amount']/100:.2f}) "
                  f"created={r['created_at']} updated={r['updated_at']} "
                  f"CHANGED={changed}")

        # Check if amount 8071 or 28750 ever existed
        print("\n=== Searching for payslip rates ===")
        
        # 80.71 * 100 = 8071
        cursor.execute("SELECT * FROM standby_rates WHERE amount = 8071")
        rows = cursor.fetchall()
        print(f"  Amount 8071 (80.71): {'FOUND' if rows else 'NOT FOUND'}")
        for r in rows:
            print(f"    {dict(r)}")

        # 28750
        cursor.execute("SELECT * FROM standby_rates WHERE amount = 28750")
        rows = cursor.fetchall()
        print(f"  Amount 28750 (287.50): {'FOUND' if rows else 'NOT FOUND'}")
        for r in rows:
            print(f"    {dict(r)}")

        # Check the cancelled standby logic for Kramer
        # Shabbat rate = 356, cancelled partial = 356 - 70 = 286
        # If the correct rate was 287.50, then it was NOT cancelled (287.50 * 2 = 575)
        print("\n=== Kramer analysis ===")
        print("  Current system: standby1=219 (Friday, seg=10) + standby2=286 (cancelled Shabbat, seg=8)")
        print("  Payslip:        standby1=287.50 + standby2=287.50 = 575")
        print("  If Friday rate should be 287.50 instead of 219, diff would be 68.50")
        print("  If Shabbat was NOT cancelled: 356 instead of 286, diff would be 70")
        print("  Total diff: 505 vs 575 = 70. EXACTLY the cancelled standby deduction!")
        print("  ==> The Shabbat standby SHOULD NOT have been cancelled (rate=356), ")
        print("      and the Friday rate was different (287.50 vs 219)")

        # Check if Friday standby rate was updated
        cursor.execute("""
            SELECT id, segment_id, apartment_type_id, amount, marital_status, created_at, updated_at
            FROM standby_rates 
            WHERE segment_id = 10
            ORDER BY apartment_type_id, marital_status
        """)
        print("\n  All Friday standby rates (seg=10):")
        for r in cursor.fetchall():
            was_changed = str(r['created_at']) != str(r['updated_at']) if r['updated_at'] else False
            print(f"    id={r['id']} apt={r['apartment_type_id']} {r['marital_status']} "
                  f"amount={r['amount']} ({r['amount']/100:.2f}) "
                  f"changed={was_changed}")

        # Check the Shabbat rate was changed
        cursor.execute("""
            SELECT id, segment_id, apartment_type_id, amount, marital_status, created_at, updated_at
            FROM standby_rates 
            WHERE segment_id = 8
            ORDER BY apartment_type_id, marital_status
        """)
        print("\n  All Shabbat standby rates (seg=8):")
        for r in cursor.fetchall():
            was_changed = str(r['created_at']) != str(r['updated_at']) if r['updated_at'] else False
            print(f"    id={r['id']} apt={r['apartment_type_id']} {r['marital_status']} "
                  f"amount={r['amount']} ({r['amount']/100:.2f}) "
                  f"changed={was_changed}")

        # What about the night standby rate for shaliach?
        # Shaliach has 7 standbys at 70 each. Payslip says 80.71 each.
        # 80.71 - 70 = 10.71 per standby
        # 7 * 10.71 = 74.97 ~ 75
        print("\n=== Shaliach analysis ===")
        print("  Current: 7 * 70 = 490")
        print("  Payslip: 7 * 80.71 = 564.97 ~ 565")
        print("  Diff per standby: 80.71 - 70 = 10.71")
        
        # Was the weekday standby rate for apt=1, single changed from 8071 to 7000?
        cursor.execute("""
            SELECT id, amount, created_at, updated_at
            FROM standby_rates 
            WHERE segment_id = 2 AND apartment_type_id = 1 AND marital_status = 'single'
        """)
        row = cursor.fetchone()
        if row:
            print(f"\n  Weekday standby (seg=2, apt=1, single):")
            print(f"    id={row['id']} amount={row['amount']} "
                  f"created={row['created_at']} updated={row['updated_at']}")
            print(f"    Was changed: {str(row['created_at']) != str(row['updated_at'])}")

        cursor.close()


if __name__ == "__main__":
    main()
