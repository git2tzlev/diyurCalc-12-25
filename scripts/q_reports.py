import sys; sys.path.insert(0,".")
from core.database import get_conn
conn = get_conn()
for pid, name in [(211,"Kramer"),(221,"Shaliach")]:
    rows = conn.execute("SELECT id,date,start_time,end_time,shift_type_id,created_at FROM time_reports WHERE person_id=%s AND date>=%s AND date<%s ORDER BY date,start_time", (pid, "2025-11-01", "2025-12-01")).fetchall()
    print(f"{name} ({pid}):")
    for r in rows: print(f"  id={r['id']} {r['date']} {r['start_time']}-{r['end_time']} shift={r['shift_type_id']} created={r['created_at']}")
conn.close()
