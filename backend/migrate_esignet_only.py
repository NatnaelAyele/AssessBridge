"""Retire card verification for unstarted assessments without rewriting case history."""
from app.db import database
from app import esignet
from app.config import SEB_BASE_URL,SEB_REQUIRED
from app.seb_config import build_encrypted_configuration,config_password_for,quit_password_for


def migrate():
    with database() as db:
        c=db.cursor(dictionary=True)
        # Assessments with attempts/publications retain their historical policy.
        c.execute('''SELECT a.id FROM assessments a WHERE COALESCE(a.identity_policy,'')<>%s
                     AND NOT EXISTS(SELECT 1 FROM assessment_exam_sessions s JOIN assessment_invitations i ON i.id=s.invitation_id WHERE i.assessment_id=a.id)
                     AND NOT EXISTS(SELECT 1 FROM assessment_result_publications p WHERE p.assessment_id=a.id) FOR UPDATE''',(esignet.POLICY,))
        ids=[row['id'] for row in c.fetchall()]
        for ident in ids:
            c.execute('UPDATE assessments SET identity_policy=%s WHERE id=%s',(esignet.POLICY,ident))
        count=0
        if SEB_REQUIRED and esignet.enabled():
            c.execute('''SELECT i.id,i.exam_username,i.seb_config_key FROM assessment_invitations i JOIN assessments a ON a.id=i.assessment_id
                         WHERE a.ends_at>UTC_TIMESTAMP() AND i.removed_at IS NULL
                         AND NOT EXISTS(SELECT 1 FROM assessment_exam_sessions s WHERE s.invitation_id=i.id)
                         AND NOT EXISTS(SELECT 1 FROM assessment_result_publications p WHERE p.assessment_id=a.id) FOR UPDATE''')
            for row in c.fetchall():
                username=row['exam_username']
                key,blob=build_encrypted_configuration(SEB_BASE_URL,f'{SEB_BASE_URL}/exam-entry?exam={username}',config_password_for(username),quit_password_for(username),esignet.POLICY)
                if key!=row['seb_config_key']:
                    c.execute('UPDATE assessment_invitations SET seb_config_key=%s,seb_config_blob=%s WHERE id=%s',(key,blob,row['id']));count+=1
        db.commit()
    print(f'eSignet-only identity workflow ready. Updated {len(ids)} unstarted assessments and {count} SEB configurations.')


if __name__=='__main__':
    migrate()
