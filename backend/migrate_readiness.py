from pathlib import Path
from app.db import database

def migrate():
    with database() as db:
        cursor=db.cursor()
        for sql in (Path(__file__).parent/'readiness_schema.sql').read_text().split(';'):
            if sql.strip():cursor.execute(sql)
        db.commit()
if __name__=='__main__':
    migrate()
    print('Readiness schema ready.')
