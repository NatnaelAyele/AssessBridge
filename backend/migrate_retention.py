from pathlib import Path
from app.db import database
from app.config import IDENTITY_RETENTION_DAYS,RECORDING_RETENTION_DAYS

def migrate():
    with database() as db:
        c=db.cursor()
        for sql in (Path(__file__).parent/'retention_schema.sql').read_text().split(';'):
            if sql.strip():c.execute(sql)
        for table in ('auth_audit','company_account_events'):
            c.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME='expires_at'",(table,))
            if not c.fetchone()[0]:c.execute(f'ALTER TABLE {table} ADD COLUMN expires_at DATETIME NULL, ADD INDEX idx_security_expiry(expires_at)')
        c.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='assessment_retention_policies' AND COLUMN_NAME='cleanup_attempt_at'")
        if not c.fetchone()[0]:c.execute('ALTER TABLE assessment_retention_policies ADD COLUMN cleanup_attempt_at DATETIME NULL')
        # Freeze legacy defaults. Never apply a new company policy retroactively.
        c.execute("INSERT IGNORE INTO assessment_retention_policies(assessment_id,revision,identity_days,monitoring_days,legacy) SELECT id,0,%s,%s,TRUE FROM assessments",(IDENTITY_RETENTION_DAYS,RECORDING_RETENTION_DAYS))
        db.commit()
if __name__=='__main__':
    migrate()
    print('Retention schema ready.')
