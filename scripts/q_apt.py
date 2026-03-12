import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.database import get_conn
c=get_conn()
r=c.execute("SELECT * FROM apartment_status_history WHERE apartment_id IN (8,12) ORDER BY 1").fetchall()
print(len(r),"rows")
for x in r: print(dict(x))
c.close()
