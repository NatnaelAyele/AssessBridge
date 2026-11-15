from pathlib import Path
from app.db import database


def migrate():
    with database() as db:
        c = db.cursor()
        for table in ('assessment_identity_checks', 'assessment_identity_attempts'):
            c.execute('SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s', (table, 'verification_method'))
            if not c.fetchone()[0]:
                c.execute(f"ALTER TABLE {table} ADD COLUMN verification_method VARCHAR(40) NOT NULL DEFAULT 'card_face'")
        for statement in Path(__file__).with_name('esignet_schema.sql').read_text().split(';'):
            if statement.strip():
                c.execute(statement)
        db.commit()
    print('Demo identity authentication schema is ready.')


if __name__ == '__main__':
    migrate()
