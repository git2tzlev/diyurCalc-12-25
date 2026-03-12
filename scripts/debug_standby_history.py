#!/usr/bin/env python3
"""Debug standby rate history."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn


def main():
    with get_conn() as conn:
        # Check if standby_rates_history exists
        print("=== Check for history tables ===")
        rows = conn.execute("""
            SELECT table_name FROM information_schema.tables 
            WHERE table_name LIKE '%%standby%%' OR table_name LIKE '%%history%%'
            ORDER BY table_name
        """).fetchall()
        for r in rows:
            print(f"  {r['table_name']}")

        # Check history module
        from core.history import get_standby_rate_for_month
        
        # Test historical rates for 11/2025
        print("\n=== Historical rates for 11/2025 ===")
        
        # Kramer: seg=10 (Friday), apt=5, married
        rate = get_standby_rate_for_month(conn, 10, 5, 'married', 2025, 11)
        print(f"  Kramer Friday (seg=10, apt=5, married): {rate}")
        
        rate = get_standby_rate_for_month(conn, 8, 5, 'married', 2025, 11)
        print(f"  Kramer Shabbat (seg=8, apt=5, married): {rate}")

        # Default rates for Kramer
        rate = get_standby_rate_for_month(conn, 10, None, 'married', 2025, 11)
        print(f"  Kramer Friday default (seg=10, None, married): {rate}")
        
        rate = get_standby_rate_for_month(conn, 8, None, 'married', 2025, 11)
        print(f"  Kramer Shabbat default (seg=8, None, married): {rate}")

        # Shaliach: seg=2 (weekday), apt=1, single
        rate = get_standby_rate_for_month(conn, 2, 1, 'single', 2025, 11)
        print(f"  Shaliach Weekday (seg=2, apt=1, single): {rate}")

        rate = get_standby_rate_for_month(conn, 13, 1, 'single', 2025, 11)
        print(f"  Shaliach Night (seg=13, apt=1, single): {rate}")

        # Check rate_for_month for existing and different dates
        print("\n=== Compare rates at different dates ===")
        for seg, apt, marital in [(10, 5, 'married'), (8, 5, 'married'), (2, 1, 'single')]:
            for y, m in [(2025, 11), (2025, 12), (2026, 1), (2026, 2)]:
                rate = get_standby_rate_for_month(conn, seg, apt, marital, y, m)
                print(f"  seg={seg} apt={apt} {marital} {y}/{m:02d}: {rate}")

        # Payslip for Kramer says 287.50 per standby. Check where that could come from
        print("\n=== Looking for rates that match 287.50 (28750) ===")
        rows = conn.execute("""
            SELECT * FROM standby_rates WHERE amount = 28750
        """).fetchall()
        for r in rows:
            print(f"  {dict(r)}")
        if not rows:
            print("  No rate of 28750 in current table")

        # Check historical table directly
        print("\n=== standby_rates_history data ===")
        try:
            rows = conn.execute("""
                SELECT * FROM standby_rates_history 
                ORDER BY segment_id, effective_from DESC
                LIMIT 50
            """).fetchall()
            for r in rows:
                print(f"  {dict(r)}")
        except Exception as e:
            print(f"  Error: {e}")

        # Payslip for Shaliach says 80.71 per standby. Check
        print("\n=== Looking for rates that match 80.71 (8071) ===")
        rows = conn.execute("""
            SELECT * FROM standby_rates WHERE amount = 8071
        """).fetchall()
        for r in rows:
            print(f"  {dict(r)}")
        if not rows:
            print("  No rate of 8071 in current table")


if __name__ == "__main__":
    main()
