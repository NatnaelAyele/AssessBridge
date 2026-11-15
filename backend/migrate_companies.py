from pathlib import Path
from app.db import database

def migrate():
    with database() as db:
        c=db.cursor()
        for statement in (Path(__file__).parent/'company_schema.sql').read_text().split(';'):
            if statement.strip():c.execute(statement)
        for table,column,definition in [('users','company_id','BIGINT UNSIGNED NULL'),('users','full_name','VARCHAR(160) NULL'),('assessments','company_id','BIGINT UNSIGNED NULL')]:
            c.execute('SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s',(table,column))
            if not c.fetchone()[0]:c.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')
        c.execute("ALTER TABLE users MODIFY role ENUM('candidate','employer_personnel','auditor','company_admin') NOT NULL")
        c.execute("SELECT COUNT(*) FROM users WHERE role IN ('employer_personnel','auditor') AND company_id IS NULL")
        if c.fetchone()[0]:
            c.execute("INSERT INTO companies(name,is_active) VALUES('AssessBridge Development Company',TRUE)");company=c.lastrowid
            c.execute("UPDATE users SET company_id=%s WHERE role IN ('employer_personnel','auditor') AND company_id IS NULL",(company,))
        c.execute('UPDATE assessments a JOIN users u ON u.id=a.employer_user_id SET a.company_id=u.company_id WHERE a.company_id IS NULL')
        for table in ('users','assessments'):
            constraint='fk_'+table+'_company'
            c.execute('SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME=%s',(constraint,))
            if not c.fetchone()[0]:c.execute(f'ALTER TABLE {table} ADD CONSTRAINT {constraint} FOREIGN KEY(company_id) REFERENCES companies(id)')
        db.commit()
if __name__=='__main__':
    migrate()
    print('Company migration complete.')
