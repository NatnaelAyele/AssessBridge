"""Retain revoked assignment history while excluding it from the active roster."""
from app.db import database

def migrate():
    with database() as connection:
        cursor=connection.cursor()
        for column,definition in [('removed_at','DATETIME NULL'),('removed_by','BIGINT UNSIGNED NULL')]:
            cursor.execute("SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='assessment_invitations' AND COLUMN_NAME=%s",(column,))
            if not cursor.fetchone()[0]:cursor.execute(f'ALTER TABLE assessment_invitations ADD COLUMN {column} {definition}')
        cursor.execute("ALTER TABLE assessment_invitations MODIFY delivery_status ENUM('pending','sent','failed','revoked') NOT NULL DEFAULT 'pending'")
        cursor.execute("ALTER TABLE assessment_activity MODIFY event_type ENUM('assessment_created','invitation_sent','invitation_failed','candidate_removed') NOT NULL")
        cursor.execute("SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='fk_invitation_removed_by'")
        if not cursor.fetchone()[0]:cursor.execute('ALTER TABLE assessment_invitations ADD CONSTRAINT fk_invitation_removed_by FOREIGN KEY(removed_by) REFERENCES users(id)')
        connection.commit()
if __name__=='__main__':
    migrate()
    print('Roster removal migration complete.')
