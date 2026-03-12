#!/usr/bin/env python3
"""Debug standby_rates_history table."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2.extras
from core.database import get_conn


def main():
    with get_conn() as conn:
        cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

        # Get table structure
        print("=== standby_rates_history columns ===")
        cursor.execute("""
            SELECT column_name, data_type 
            FROM information_schema.columns 
            WHERE table_name = 'standby_rates_history'
            ORDER BY ordinal_position
        """)
        for r in cursor.fetchall():
            print(f"  {r['column_name']}: {r['data_type']}")

        # Get all history rows
        print("\n=== All standby_rates_history data ===")
        cursor.execute("SELECT * FROM standby_rates_history ORDER BY segment_id, year, month")
        rows = cursor.fetchall()
        for r in rows:
            print(f"  {dict(r)}")
        if not rows:
            print("  (empty table)")

        # Specific query: what does history return for seg=10, apt=5, married, 2025/11?
        print("\n=== History query for Kramer Friday (seg=10, apt=5, married, 2025/11) ===")
        cursor.execute("""
            SELECT amount, year, month, apartment_type_id
            FROM standby_rates_history
            WHERE segment_id = 10
              AND apartment_type_id = 5
              AND marital_status = 'married'
              AND (year > 2025 OR (year = 2025 AND month > 11))
            ORDER BY year ASC, month ASC
            LIMIT 1
        """)
        row = cursor.fetchone()
        print(f"  Specific apt=5: {dict(row) if row else 'None'}")

        # General fallback for seg=10, married, 2025/11
        cursor.execute("""
            SELECT amount, year, month, apartment_type_id
            FROM standby_rates_history
            WHERE segment_id = 10
              AND apartment_type_id IS NULL
              AND marital_status = 'married'
              AND (year > 2025 OR (year = 2025 AND month > 11))
            ORDER BY year ASC, month ASC
            LIMIT 1
        """)
        row = cursor.fetchone()
        print(f"  General (apt=NULL): {dict(row) if row else 'None'}")

        # Now check for seg=8 (Shabbat)
        print("\n=== History query for Kramer Shabbat (seg=8, apt=5, married, 2025/11) ===")
        cursor.execute("""
            SELECT amount, year, month, apartment_type_id
            FROM standby_rates_history
            WHERE segment_id = 8
              AND apartment_type_id = 5
              AND marital_status = 'married'
              AND (year > 2025 OR (year = 2025 AND month > 11))
            ORDER BY year ASC, month ASC
            LIMIT 1
        """)
        row = cursor.fetchone()
        print(f"  Specific apt=5: {dict(row) if row else 'None'}")

        cursor.execute("""
            SELECT amount, year, month, apartment_type_id
            FROM standby_rates_history
            WHERE segment_id = 8
              AND apartment_type_id IS NULL
              AND marital_status = 'married'
              AND (year > 2025 OR (year = 2025 AND month > 11))
            ORDER BY year ASC, month ASC
            LIMIT 1
        """)
        row = cursor.fetchone()
        print(f"  General (apt=NULL): {dict(row) if row else 'None'}")

        # Shaliach: seg=2, apt=1, single
        print("\n=== History for Shaliach Weekday (seg=2, apt=1, single, 2025/11) ===")
        cursor.execute("""
            SELECT amount, year, month, apartment_type_id
            FROM standby_rates_history
            WHERE segment_id = 2
              AND apartment_type_id = 1
              AND marital_status = 'single'
              AND (year > 2025 OR (year = 2025 AND month > 11))
            ORDER BY year ASC, month ASC
            LIMIT 1
        """)
        row = cursor.fetchone()
        print(f"  Specific apt=1: {dict(row) if row else 'None'}")

        cursor.execute("""
            SELECT amount, year, month, apartment_type_id
            FROM standby_rates_history
            WHERE segment_id = 2
              AND apartment_type_id IS NULL
              AND marital_status = 'single'
              AND (year > 2025 OR (year = 2025 AND month > 11))
            ORDER BY year ASC, month ASC
            LIMIT 1
        """)
        row = cursor.fetchone()
        print(f"  General (apt=NULL): {dict(row) if row else 'None'}")

        cursor.close()


if __name__ == "__main__":
    main()
