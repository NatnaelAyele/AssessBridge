from pathlib import Path
from app.db import database

def migrate():
 with database() as db:
  c=db.cursor()
  for sql in (Path(__file__).parent/'reporting_schema.sql').read_text().split(';'):
   if sql.strip():c.execute(sql)
  db.commit()
if __name__=='__main__':
 migrate()
 print('Reporting schema ready.')
