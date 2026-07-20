"""Import old-system recovery-pay FTE percentages for 06/2026."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
load_dotenv()

from core.database import get_conn
from core.recovery_pay import import_legacy_recovery_fte, parse_legacy_recovery_xlsx


def main() -> None:
    parser = argparse.ArgumentParser(description="Import recovery-pay FTE rows from old-system XLSX")
    parser.add_argument("xlsx_path", type=Path)
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--month", type=int, default=6)
    parser.add_argument("--source-months", type=int, default=4)
    args = parser.parse_args()

    rows = parse_legacy_recovery_xlsx(args.xlsx_path)
    with get_conn() as conn:
        count = import_legacy_recovery_fte(
            conn.conn,
            rows,
            payment_year=args.year,
            payment_month=args.month,
            source_months=args.source_months,
        )
    print(f"Imported {count} recovery-pay FTE rows for {args.month:02d}/{args.year}")


if __name__ == "__main__":
    main()
