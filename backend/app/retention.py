"""Company policies frozen per assessment; scheduled, auditable expiry."""
import hashlib
import json
import logging
from datetime import datetime,timedelta,timezone
from uuid import UUID
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field,StrictInt,model_validator,field_validator
from .db import database
from .audit_times import iso,utc
from .config import IDENTITY_RETENTION_DAYS,RECORDING_RETENTION_DAYS
from .companies import membership

KEYS=('identity_days','monitoring_days','review_days','audit_days')
def now():return datetime.now(timezone.utc).replace(tzinfo=None)
def unpack(value):return json.loads(value) if isinstance(value,(str,bytes)) else value
def defaults():return dict(revision=0,identity_days=IDENTITY_RETENTION_DAYS,monitoring_days=RECORDING_RETENTION_DAYS,review_days=None,audit_days=None)
class Policy(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_revision:StrictInt=Field(ge=0)
    request_key:UUID
    identity_days:StrictInt=Field(ge=1,le=3650)
    monitoring_days:StrictInt=Field(ge=1,le=3650)
    review_days:StrictInt|None=Field(default=None,ge=1,le=3650)
    audit_days:StrictInt|None=Field(default=None,ge=1,le=3650)
    reason:str=Field(min_length=5,max_length=1000)
    @field_validator('reason')
    @classmethod
    def reason_text(cls,value):
        if len(value.strip())<5:raise ValueError('Enter a reason of at least 5 characters')
        return value.strip()
    @model_validator(mode='after')
    def ordered(self):
        if self.review_days is not None and self.review_days<max(self.identity_days,self.monitoring_days):raise ValueError('Case retention must cover the image and recording periods')
        if self.audit_days is not None and (self.review_days is None or self.audit_days<self.review_days):raise ValueError('Audit retention must cover the case period; set a case period first')
        return self

def admin(user):
    member=membership(user)
    if member['role']!='company_admin':raise HTTPException(403,'Company administrator access required')
    return member['company_id']

def company_policy(cursor,company):
    cursor.execute('SELECT revision,identity_days,monitoring_days,review_days,audit_days FROM company_retention_policies WHERE company_id=%s',(company,))
    return cursor.fetchone() or defaults()

def snapshot(cursor,assessment,new=False):
    cursor.execute('SELECT * FROM assessment_retention_policies WHERE assessment_id=%s',(assessment,));row=cursor.fetchone()
    if row:return row
    cursor.execute('SELECT company_id FROM assessments WHERE id=%s',(assessment,));exam=cursor.fetchone()
    if not exam:raise HTTPException(404,'Assessment not found')
    policy=company_policy(cursor,exam['company_id']) if new and exam['company_id'] else defaults()
    cursor.execute('INSERT INTO assessment_retention_policies(assessment_id,revision,identity_days,monitoring_days,review_days,audit_days,legacy) VALUES(%s,%s,%s,%s,%s,%s,%s)',(assessment,policy['revision'],*[policy[key] for key in KEYS],not new))
    return {'assessment_id':assessment,**policy,'legacy':not new}

def for_invitation(cursor,invitation):
    cursor.execute('SELECT assessment_id FROM assessment_invitations WHERE id=%s',(invitation,));row=cursor.fetchone()
    if not row:raise HTTPException(404,'Exam invitation not found')
    return snapshot(cursor,row['assessment_id'])

def read(user):
    company=admin(user)
    with database() as db:
        c=db.cursor(dictionary=True);policy=company_policy(c,company)
        c.execute('SELECT revision,actor_name,reason,before_json,after_json,created_at FROM company_retention_changes WHERE company_id=%s AND (expires_at IS NULL OR expires_at>UTC_TIMESTAMP()) ORDER BY id DESC LIMIT 20',(company,));history=c.fetchall()
        for row in history:row['before']=unpack(row.pop('before_json'));row['after']=unpack(row.pop('after_json'));row['created_at']=iso(row['created_at'])
        c.execute('SELECT assessment_id,closed_at,case_removed_at,expires_at,JSON_LENGTH(events_json) AS event_count FROM retained_assessment_audits WHERE company_id=%s AND (expires_at IS NULL OR expires_at>UTC_TIMESTAMP()) ORDER BY case_removed_at DESC LIMIT 20',(company,));archives=c.fetchall()
        for row in archives:
            for key in ('closed_at','case_removed_at','expires_at'):row[key]=iso(row[key])
    return {'policy':policy,'history':history,'archives':archives,'applies_to':'new_assessments'}

def save(user,payload):
    company=admin(user);digest=hashlib.sha256(payload.model_dump_json(exclude={'request_key'}).encode()).hexdigest()
    with database() as db:
        c=db.cursor(dictionary=True)
        # Serialize first-time setup and concurrent administrators using the company row.
        c.execute('SELECT id FROM companies WHERE id=%s FOR UPDATE',(company,));c.fetchone()
        c.execute('SELECT payload_hash FROM company_retention_changes WHERE company_id=%s AND request_key=%s',(company,str(payload.request_key)));old=c.fetchone()
        if old:
            if old['payload_hash']!=digest:raise HTTPException(409,'This save request has already been used')
            return {'saved':True,'duplicate':True}
        before=company_policy(c,company)
        if before['revision']!=payload.expected_revision:raise HTTPException(409,'Retention settings changed. Reload before saving.')
        after={key:getattr(payload,key) for key in KEYS}
        if all(before[key]==after[key] for key in KEYS):return {'saved':True,'unchanged':True}
        after['revision']=before['revision']+1
        c.execute('INSERT INTO company_retention_policies(company_id,revision,identity_days,monitoring_days,review_days,audit_days) VALUES(%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE revision=VALUES(revision),identity_days=VALUES(identity_days),monitoring_days=VALUES(monitoring_days),review_days=VALUES(review_days),audit_days=VALUES(audit_days)',(company,after['revision'],*[after[key] for key in KEYS]))
        c.execute("SELECT COALESCE(NULLIF(full_name,''),email,username) AS name FROM users WHERE id=%s",(user,));name=c.fetchone()['name']
        c.execute('INSERT INTO company_retention_changes(company_id,revision,request_key,payload_hash,actor_id,actor_name,reason,before_json,after_json,created_at,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',(company,after['revision'],str(payload.request_key),digest,user,name,payload.reason,json.dumps(before),json.dumps(after),now(),now()+timedelta(days=after['audit_days']) if after['audit_days'] else None));db.commit()
    return {'saved':True,'revision':after['revision']}

def _audit_metadata(cursor,assessment):
    # Keep action/actor/time/reference only. No names, reasons, scores, images,
    # IP addresses, credentials or document text survive case expiry.
    queries=[
      ("SELECT event_type,created_at AS at,actor_user_id AS actor,invitation_id AS subject FROM assessment_activity WHERE assessment_id=%s",False),
      ("SELECT 'publication' AS event_type,created_at AS at,publisher_user_id AS actor,NULL AS subject FROM assessment_result_publications WHERE assessment_id=%s",False),
      ("SELECT CONCAT('export_',format) AS event_type,generated_at AS at,actor_id AS actor,invitation_id AS subject FROM assessment_export_audit WHERE assessment_id=%s",False),
      ("SELECT CONCAT('publication_',kind) AS event_type,created_at AS at,actor_id AS actor,NULL AS subject FROM assessment_publication_holds WHERE assessment_id=%s",False),
    ]
    session_tables=[('assessment_review_decisions',"CONCAT('decision_',x.action)",'x.created_at','x.reviewer_user_id'),('assessment_review_findings',"CONCAT('finding_',x.finding)",'x.created_at','x.reviewer_user_id'),('assessment_connectivity_findings',"CONCAT('interruption_finding_',x.finding)",'x.created_at','x.reviewer_user_id'),('assessment_audit_completions',"'audit_completed'",'x.created_at','x.auditor_user_id'),('assessment_case_notes',"'case_note_recorded'",'x.created_at','x.actor_id'),('assessment_audit_outcomes',"CONCAT('audit_',x.kind)",'x.created_at','x.actor_id'),('assessment_review_access_audit',"'case_accessed'",'x.accessed_at','x.actor_user_id'),('assessment_connectivity_events',"x.event_type",'x.occurred_at','NULL')]
    for table,kind,at,actor in session_tables:queries.append((f'SELECT {kind} AS event_type,{at} AS at,{actor} AS actor,s.invitation_id AS subject FROM {table} x JOIN assessment_exam_sessions s ON s.id=x.session_id JOIN assessment_invitations i ON i.id=s.invitation_id WHERE i.assessment_id=%s',False))
    for table,kind,at,actor in [('assessment_identity_attempts',"'identity_check_performed'",'x.checked_at','x.candidate_user_id'),('assessment_consents',"'monitoring_notice_accepted'",'x.accepted_at','x.candidate_user_id'),('assessment_readiness_records',"'readiness_recorded'",'x.created_at','x.candidate_user_id'),('assessment_environment_events','x.event_type','x.received_at','x.candidate_user_id')]:
        queries.append((f'SELECT {kind} AS event_type,{at} AS at,{actor} AS actor,i.id AS subject FROM {table} x JOIN assessment_invitations i ON i.id=x.invitation_id WHERE i.assessment_id=%s',False))
    queries.append(("SELECT 'recording_accessed' AS event_type,x.accessed_at AS at,x.actor_user_id AS actor,s.invitation_id AS subject FROM assessment_recording_access_audit x JOIN assessment_recordings r ON r.id=x.recording_id JOIN assessment_exam_sessions s ON s.id=r.session_id JOIN assessment_invitations i ON i.id=s.invitation_id WHERE i.assessment_id=%s",False))
    queries.append(("SELECT 'monitoring_observation_recorded' AS event_type,x.created_at AS at,NULL AS actor,s.invitation_id AS subject FROM assessment_monitoring_events x JOIN assessment_exam_sessions s ON s.id=x.session_id JOIN assessment_invitations i ON i.id=s.invitation_id WHERE i.assessment_id=%s",False))
    events=[]
    for query,_ in queries:
        cursor.execute(query,(assessment,));events.extend(cursor.fetchall())
    for event in events:event['at']=iso(event['at'])
    events.sort(key=lambda item:utc(item['at']))
    return events

def cleanup_cases(limit=25):
    from .recordings import delete_storage
    count=0;failed=0
    with database() as db:
        c=db.cursor(dictionary=True)
        c.execute('SELECT a.id FROM assessments a JOIN assessment_retention_policies p ON p.assessment_id=a.id WHERE p.review_days IS NOT NULL AND a.company_id IS NOT NULL AND DATE_ADD(a.ends_at,INTERVAL p.review_days DAY)<=UTC_TIMESTAMP() ORDER BY p.cleanup_attempt_at,a.id LIMIT %s',(limit,));ids=[row['id'] for row in c.fetchall()]
        db.commit()
        for assessment in ids:
            try:
                c.execute('SELECT a.id,a.company_id,a.ends_at,p.review_days,p.audit_days FROM assessments a JOIN assessment_retention_policies p ON p.assessment_id=a.id WHERE a.id=%s AND p.review_days IS NOT NULL AND DATE_ADD(a.ends_at,INTERVAL p.review_days DAY)<=UTC_TIMESTAMP() FOR UPDATE',(assessment,));exam=c.fetchone()
                if not exam:db.rollback();continue
                c.execute('SELECT id FROM assessment_exam_sessions WHERE invitation_id IN (SELECT id FROM assessment_invitations WHERE assessment_id=%s) AND submitted_at IS NULL AND disqualified_at IS NULL AND interrupted_at IS NULL',(assessment,))
                if c.fetchall():db.rollback();continue
                policy=snapshot(c,assessment);events=_audit_metadata(c,assessment)
                c.execute('SELECT r.storage_token FROM assessment_recordings r JOIN assessment_exam_sessions s ON s.id=r.session_id JOIN assessment_invitations i ON i.id=s.invitation_id WHERE i.assessment_id=%s FOR UPDATE',(assessment,));tokens=[row['storage_token'] for row in c.fetchall()]
                for token in tokens:delete_storage(token)
                c.execute('INSERT INTO retained_assessment_audits(assessment_id,company_id,closed_at,case_removed_at,expires_at,policy_json,events_json) VALUES(%s,%s,%s,%s,%s,%s,%s)',(assessment,exam['company_id'],exam['ends_at'],now(),exam['ends_at']+timedelta(days=exam['audit_days']) if exam['audit_days'] else None,json.dumps({key:policy[key] for key in KEYS}),json.dumps(events)))
                c.execute('DELETE FROM assessments WHERE id=%s',(assessment,));db.commit();count+=1
            except Exception:
                db.rollback();failed+=1
                c.execute('UPDATE assessment_retention_policies SET cleanup_attempt_at=UTC_TIMESTAMP() WHERE assessment_id=%s',(assessment,));db.commit()
                logging.getLogger(__name__).exception('Case retention cleanup failed for assessment %s',assessment)
        c.execute('DELETE FROM retained_assessment_audits WHERE expires_at<=UTC_TIMESTAMP()');audit_count=c.rowcount
        c.execute('DELETE FROM company_retention_changes WHERE expires_at<=UTC_TIMESTAMP()');db.commit()
    return {'cases_removed':count,'cases_failed':failed,'audit_archives_removed':audit_count}


def live_sql(alias='a'):
    return f"NOT EXISTS (SELECT 1 FROM assessment_retention_policies rp WHERE rp.assessment_id={alias}.id AND rp.review_days IS NOT NULL AND DATE_ADD({alias}.ends_at,INTERVAL rp.review_days DAY)<=UTC_TIMESTAMP())"

def require_live(cursor,assessment):
    cursor.execute('SELECT a.id FROM assessments a WHERE a.id=%s AND '+live_sql(),(assessment,))
    if not cursor.fetchone():raise HTTPException(404,'Assessment records are unavailable or their retention period has ended')

def notice(invitation):
    with database() as db:
        c=db.cursor(dictionary=True);policy=for_invitation(c,invitation);db.commit()
    return {key:policy[key] for key in KEYS}

def archive(user,assessment):
    company=admin(user)
    with database() as db:
        c=db.cursor(dictionary=True);c.execute('SELECT assessment_id,closed_at,case_removed_at,expires_at,events_json FROM retained_assessment_audits WHERE assessment_id=%s AND company_id=%s AND (expires_at IS NULL OR expires_at>UTC_TIMESTAMP())',(assessment,company));row=c.fetchone()
    if not row:raise HTTPException(404,'Retained audit metadata not found')
    row['events']=unpack(row.pop('events_json'))
    for key in ('closed_at','case_removed_at','expires_at'):row[key]=iso(row[key])
    return row


def cleanup_security_logs():
    counts={}
    with database() as db:
        c=db.cursor()
        for table in ('auth_audit','company_account_events'):
            c.execute(f'DELETE FROM {table} WHERE expires_at<=UTC_TIMESTAMP()')
            counts[table]=c.rowcount
        db.commit()
    return counts
