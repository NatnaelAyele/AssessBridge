from pathlib import Path
from app.db import database

def migrate():
 with database() as db:
  cursor=db.cursor()
  for statement in (Path(__file__).parent/'casework_schema.sql').read_text().split(';'):
   if statement.strip():cursor.execute(statement)
  db.commit()
if __name__=='__main__':
 migrate()
 print('Casework schema ready.')
