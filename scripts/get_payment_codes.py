#!/usr/bin/env python3
"""Get payment code mapping from database."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.database import get_conn

with get_conn() as conn:
    rows = conn.execute(
        "SELECT merav_code, internal_key, display_name "
        "FROM payment_codes "
        "WHERE merav_code IS NOT NULL AND merav_code <> '' "
        "ORDER BY display_order ASC NULLS LAST"
    ).fetchall()
    for r in rows:
        print(f'{r["merav_code"]:>5} | {r["internal_key"]:<30} | {r["display_name"]}')
