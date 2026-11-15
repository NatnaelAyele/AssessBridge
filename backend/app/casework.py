"""Append-only notes, auditor concerns, publication holds and session history."""
import hashlib
import json
from datetime import datetime,timedelta
from typing import Literal
from uuid import UUID
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field,field_validator
from .db import database
from .audit_times import utc
from .reviews import _load,_advance,_version,_audit,iso,now,unpack
from .companies import membership,authorize_resource

class Reason(BaseModel):
 model_config=ConfigDict(extra='forbid')
 request_key:UUID
 reason:str=Field(min_length=5,max_length=5000)
 @field_validator('reason')
 @classmethod
 def clean(cls,v):
  if len(v.strip())<5:raise ValueError('Enter at least 5 characters')
  return v.strip()
class Note(Reason):
 expected_revision:int=Field(ge=0)
 reference_kind:Literal['note','observation','interruption']|None=None
 reference_key:str|None=Field(default=None,max_length=100)
class Concern(Reason):
 expected_version:str=Field(pattern=r'^[0-9a-f]{64}$')
 expected_outcome:int=Field(ge=0)
class Hold(Reason):
 expected_version:str=Field(pattern=r'^[0-9a-f]{64}$')
 kind:Literal['held','resolved']

def actor(cursor,user):
 cursor.execute("SELECT COALESCE(NULLIF(full_name,''),email,username) AS name FROM users WHERE id=%s",(user,))
 return cursor.fetchone()['name']
def staff(user,assessment,roles):
 member=authorize_resource(user,assessment)
 if member['role'] not in roles:raise HTTPException(403,'Access denied')
 if member['role']=='auditor':
  from .auditing import assessment_for_auditor
  with database() as db:
   c=db.cursor(dictionary=True);assessment_for_auditor(c,assessment)
 return member

def latest_outcome(cursor,session):
 cursor.execute('SELECT * FROM assessment_audit_outcomes WHERE session_id=%s ORDER BY id DESC LIMIT 1',(session,))
 return cursor.fetchone()
def latest_hold(cursor,assessment):
 cursor.execute('SELECT * FROM assessment_publication_holds WHERE assessment_id=%s ORDER BY id DESC LIMIT 1',(assessment,))
 return cursor.fetchone()
def public(row):
 if row is None:return None
 return {k:(iso(v) if k=='created_at' else v) for k,v in row.items() if k not in ('payload_hash','request_key')}
def duplicate(cursor,table,field,ident,payload,user):
 digest=hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
 cursor.execute(f'SELECT payload_hash,actor_id FROM {table} WHERE {field}=%s AND request_key=%s',(ident,str(payload.request_key)))
 existing=cursor.fetchone()
 if existing and (existing['payload_hash']!=digest or existing['actor_id']!=user):raise HTTPException(409,'This request was already used for different information.')
 return bool(existing),digest

def add_note(assessment,invitation,user,payload):
 staff(user,assessment,('employer_personnel',))
 with database() as db:
  c=db.cursor(dictionary=True);s=_load(c,assessment,invitation,user)
  if s['published']:raise HTTPException(409,'Results are published. This report is read only.')
  exists,digest=duplicate(c,'assessment_case_notes','session_id',s['id'],payload,user)
  if exists:return {'saved':True,'duplicate':True,'revision':_version(c,s['id'])}
  if bool(payload.reference_kind)!=bool(payload.reference_key):raise HTTPException(422,'Choose a valid note reference.')
  if payload.reference_kind:
   sql={'note':'SELECT id FROM assessment_case_notes WHERE session_id=%s AND id=%s','observation':'SELECT id FROM assessment_monitoring_events WHERE session_id=%s AND event_key=%s','interruption':'SELECT id FROM assessment_connectivity_events WHERE session_id=%s AND id=%s'}[payload.reference_kind]
   c.execute(sql,(s['id'],payload.reference_key))
   if not c.fetchone():raise HTTPException(422,'This reference does not belong to this session.')
  revision=_advance(c,s['id'],payload.expected_revision)
  c.execute('INSERT INTO assessment_case_notes(session_id,revision,request_key,payload_hash,body,reference_kind,reference_key,actor_id,actor_name,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',(s['id'],revision,str(payload.request_key),digest,payload.reason,payload.reference_kind,payload.reference_key,user,actor(c,user),now()))
  _audit(c,s['id'],user,'case_note',None)
  db.commit()
 return {'saved':True,'revision':revision}

def concern(assessment,invitation,user,payload):
 from .auditing import _session,_status
 staff(user,assessment,('auditor',))
 with database() as db:
  c=db.cursor(dictionary=True);s=_session(c,assessment,invitation,lock=True)
  c.execute('SELECT id FROM assessment_result_publications WHERE assessment_id=%s AND is_legacy=FALSE',(assessment,))
  if c.fetchone():raise HTTPException(409,'Results are published. Audits cannot be changed.')
  exists,digest=duplicate(c,'assessment_audit_outcomes','session_id',s['id'],payload,user)
  if exists:return {'saved':True,'duplicate':True}
  status,_=_status(c,s['id'])
  if status['version']!=payload.expected_version or status['outcome_revision']!=payload.expected_outcome:raise HTTPException(409,'The report or audit changed. Reload the report.')
  c.execute('INSERT INTO assessment_audit_outcomes(session_id,kind,reason,evidence_version,actor_id,actor_name,request_key,payload_hash,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',(s['id'],'needs_attention',payload.reason,payload.expected_version,user,actor(c,user),str(payload.request_key),digest,now()))
  db.commit()
 return {'saved':True}

def change_hold(assessment,user,payload):
 from .publications import _state
 staff(user,assessment,('auditor',))
 with database() as db:
  c=db.cursor(dictionary=True);state,_,_=_state(c,assessment,lock=True)
  if state['status']=='published':raise HTTPException(409,'Results are published. Publication holds cannot be changed.')
  exists,digest=duplicate(c,'assessment_publication_holds','assessment_id',assessment,payload,user)
  if exists:return {'saved':True,'duplicate':True}
  if state['version']!=payload.expected_version:raise HTTPException(409,'The assessment or audits changed. Refresh the candidates.')
  latest=latest_hold(c,assessment)
  if payload.kind=='resolved' and (not latest or latest['kind']!='held'):raise HTTPException(409,'There is no active publication hold.')
  c.execute('INSERT INTO assessment_publication_holds(assessment_id,kind,reason,actor_id,actor_name,request_key,payload_hash,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id',(assessment,payload.kind,payload.reason,user,actor(c,user),str(payload.request_key),digest,now()))
  db.commit()
 return {'saved':True}

def read_notes(assessment,invitation,user):
 staff(user,assessment,('employer_personnel','auditor'))
 with database() as db:
  c=db.cursor(dictionary=True);s=_load(c,assessment,invitation,user)
  c.execute('SELECT id,body,reference_kind,reference_key,actor_name,created_at FROM assessment_case_notes WHERE session_id=%s ORDER BY id DESC',(s['id'],));notes=[public(r) for r in c.fetchall()]
  c.execute('SELECT event_key,event_type FROM assessment_monitoring_events WHERE session_id=%s ORDER BY id',(s['id'],));observations=c.fetchall()
  c.execute('SELECT id,event_type,occurred_at FROM assessment_connectivity_events WHERE session_id=%s ORDER BY occurred_at,id',(s['id'],));interruptions=c.fetchall()
  c.execute('SELECT * FROM assessment_audit_outcomes WHERE session_id=%s ORDER BY id DESC',(s['id'],));outcomes=[public(r) for r in c.fetchall()]
  _audit(c,s['id'],user,'case_notes',None);db.commit()
  return {'notes':notes,'revision':_version(c,s['id']),'published':bool(s['published']),'observations':observations,'interruptions':[{'id':r['id'],'event_type':r['event_type'],'at':iso(r['occurred_at'])} for r in interruptions],'outcomes':outcomes}

def history(assessment,invitation,user,category='all',order='desc',page=1):
 staff(user,assessment,('employer_personnel','auditor'));items=[]
 def add(key,at,kind,title,details='',who='System',reference=None):
  if at:items.append({'id':key,'at':iso(at),'category':kind,'title':title,'details':details,'actor':who,'reference':reference})
 with database() as db:
  c=db.cursor(dictionary=True);s=_load(c,assessment,invitation,user)
  c.execute('SELECT * FROM assessment_activity WHERE assessment_id=%s AND (invitation_id=%s OR invitation_id IS NULL) ORDER BY id',(assessment,invitation))
  for r in c.fetchall():add('assignment:'+str(r['id']),r['created_at'],'assessment',{'assessment_created':'Assessment created','invitation_sent':'Invitation sent','invitation_failed':'Invitation delivery failed','candidate_removed':'Candidate removed'}.get(r['event_type'],r['event_type'].replace('_',' ')),who=actor(c,r['actor_user_id']))
  c.execute('SELECT * FROM assessment_consents WHERE invitation_id=%s',(invitation,))
  for r in c.fetchall():add('consent:'+str(r['id']),r['accepted_at'],'identity','Monitoring notice accepted',r['policy_version'],'Candidate')
  c.execute('SELECT * FROM assessment_identity_attempts WHERE invitation_id=%s ORDER BY id',(invitation,))
  for r in c.fetchall():add('identity:'+str(r['id']),r['checked_at'],'identity',('Demo identity authenticated' if r['status']=='passed' else 'Demo identity authentication failed') if r.get('verification_method')=='esignet_demo' else 'Identity check '+r['status'],(r['failure_reason'] or '').replace('_',' ').capitalize(), 'System')
  from .readiness import report as readiness_report
  readiness=readiness_report(c,invitation)
  for r in readiness['checks']:add('readiness:'+str(r['id']),datetime.fromisoformat(r['created_at'].removesuffix('Z')),'assessment','Readiness confirmed' if r['passed'] else 'Readiness check recorded','Acknowledgements complete' if all(r['acknowledgements'].values()) else 'Acknowledgements incomplete','Candidate')
  for r in readiness['events']:add('environment:'+str(r['id']),datetime.fromisoformat(r['received_at'].removesuffix('Z')),'assessment',r['label'],'Source: Browser page. Reported time: '+r['client_occurred_at'],'Candidate')
  add('start',s['started_at'],'assessment','Assessment started',who='Candidate')
  for field,title in [('submitted_at','Assessment submitted'),('disqualified_at','Attempt disqualified'),('interrupted_at','Attempt interrupted')]:add(field,s.get(field),'assessment',title,s.get('submission_reason') or s.get('interruption_reason') or s.get('disqualification_reason') or '')
  c.execute('SELECT * FROM assessment_connectivity_events WHERE session_id=%s',(s['id'],))
  titles={'interruption_started':'Exam contact interrupted','connection_restored':'Connection restored','session_resumed':'Assessment resumed','recovery_checks_required':'Recovery checks required','recovery_identity_check':'Recovery face check','confirmed_exit':'Candidate confirmed exit','deadline_reached':'Time limit reached','attempt_interrupted':'Recovery limit reached','platform_restored':'Platform connection restored'}
  for r in c.fetchall():
   detail=unpack(r['details_json']) or {};text=''
   if r['event_type']=='recovery_identity_check':text='Passed' if detail.get('passed') else 'Could not check' if detail.get('status')=='unusable' else 'Not matched'
   elif 'duration_seconds' in detail:text='Interruption duration: '+str(detail['duration_seconds'])+' seconds'
   add('connection:'+str(r['id']),r['occurred_at'],'connectivity',titles.get(r['event_type'],r['event_type'].replace('_',' ').capitalize()),text,reference={'kind':'interruption','key':str(r['id'])})
  c.execute('SELECT e.*,p.started_at AS part_started FROM assessment_monitoring_events e LEFT JOIN assessment_recording_parts p ON p.id=e.part_id WHERE e.session_id=%s',(s['id'],))
  for r in c.fetchall():add('monitoring:'+str(r['id']),(r['part_started']+timedelta(milliseconds=r['start_ms'])) if r['part_started'] else r['created_at'],'monitoring','Observation: '+r['event_type'].replace('_',' ').capitalize(),'Recording time estimate' if r['part_started'] else 'Detection recorded',reference={'kind':'observation','key':r['event_key']})
  for table,title in [('assessment_review_findings','Finding recorded'),('assessment_connectivity_findings','Interruption finding recorded'),('assessment_review_decisions','Review decision recorded')]:
   c.execute(f'SELECT * FROM {table} WHERE session_id=%s',(s['id'],))
   for r in c.fetchall():
    details=r['reason']
    if 'finding' in r:
     labels={'supports_violation':'Supports a rule violation','dismissed':'Dismissed','inconclusive':'Inconclusive','explained':'Explained','unresolved':'Unresolved','needs_attention':'Needs attention'}
     details=labels.get(r['finding'],r['finding'].replace('_',' '))+': '+details
    if table=='assessment_review_decisions':
     previous=unpack(r['previous_json'])
     details=r['action'].replace('_',' ')+': '+details+' / Previous score: '+str(previous.get('score'))+' / Final score: '+str(r['final_score'])
     for index,grade in unpack(r['grades_json']).items():details+=' / Written question '+str(int(index)+1)+': '+str(grade['marks'])+' / '+grade['reason']
    add(table+':'+str(r['id']),r['created_at'],'review',title,details,r['reviewer_username'])
  c.execute('SELECT * FROM assessment_case_notes WHERE session_id=%s',(s['id'],))
  for r in c.fetchall():add('note:'+str(r['id']),r['created_at'],'review','Case note added',r['body'],r['actor_name'],{'kind':'note','key':str(r['id'])})
  c.execute("SELECT a.* FROM assessment_audit_completions a WHERE a.session_id=%s AND NOT EXISTS(SELECT 1 FROM assessment_audit_outcomes o WHERE o.session_id=a.session_id AND o.kind='audited' AND o.evidence_version=a.evidence_version AND trunc(extract(epoch from (o.created_at - a.created_at)))=0)",(s['id'],))
  for r in c.fetchall():add('legacy-audit:'+str(r['id']),r['created_at'],'audit','Audit completed',who=r['auditor_username'])
  c.execute('SELECT * FROM assessment_audit_outcomes WHERE session_id=%s',(s['id'],))
  for r in c.fetchall():add('audit:'+str(r['id']),r['created_at'],'audit','Audit needs attention' if r['kind']=='needs_attention' else 'Audit confirmed',r['reason'],r['actor_name'])
  c.execute('SELECT * FROM assessment_publication_holds WHERE assessment_id=%s',(assessment,))
  for r in c.fetchall():add('hold:'+str(r['id']),r['created_at'],'audit','Publication on hold' if r['kind']=='held' else 'Publication hold resolved',r['reason'],r['actor_name'])
  c.execute('SELECT * FROM assessment_result_publications WHERE assessment_id=%s AND is_legacy=FALSE',(assessment,))
  for r in c.fetchall():add('publication:'+str(r['id']),r['created_at'],'audit','Results published',r['remark'],r['publisher_username'])
  _audit(c,s['id'],user,'session_history',None);db.commit()
 filtered=[r for r in items if category=='all' or r['category']==category]
 filtered.sort(key=lambda r:(utc(r['at']),r['id']),reverse=order=='desc')
 return {'items':filtered[(page-1)*30:page*30],'total':len(filtered),'page':page,'page_size':30}
