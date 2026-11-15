"""Versioned candidate acknowledgements and bounded, source-labelled page events."""
import hashlib
import json
from datetime import datetime,timezone,timedelta
from typing import Literal
from uuid import UUID
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field,StrictBool,AwareDatetime
from .db import database
from .exam_sessions import load_exam,load_session,utc_now,iso_utc
from .recovery import require_readiness

VERSION='readiness-1'
class Checks(BaseModel):
    model_config=ConfigDict(extra='forbid')
    secure_context:StrictBool
    media_recorder:StrictBool
    encrypted_storage:StrictBool
    camera:StrictBool
    microphone:StrictBool
    web_audio:StrictBool
class Acknowledgements(BaseModel):
    model_config=ConfigDict(extra='forbid')
    instructions:StrictBool
    room:StrictBool
    interruptions:StrictBool
class Confirmation(BaseModel):
    model_config=ConfigDict(extra='forbid')
    instructions_version:str=Field(pattern=r'^[0-9a-f]{64}$')
    checks:Checks
    acknowledgements:Acknowledgements
    platform:Literal['Windows','macOS','unsupported']
    client_version:str=Field(default='',max_length=180,pattern=r'^[^\x00-\x1f]*$')
class PageEvent(BaseModel):
    model_config=ConfigDict(extra='forbid')
    request_key:UUID
    event_type:Literal['visibility_hidden','visibility_visible','window_blurred','window_focused','fullscreen_left','fullscreen_entered','camera_unavailable','camera_ready','microphone_unavailable','microphone_ready','page_hidden','page_restored']
    occurred_at:AwareDatetime
class EventBatch(BaseModel):
    model_config=ConfigDict(extra='forbid')
    events:list[PageEvent]=Field(min_length=1,max_length=30)

def instructions_version(exam):
    fields={key:exam.get(key) for key in ('title','description','rules_text','identity_policy','duration_minutes','starts_at','ends_at','questions_json')}
    if isinstance(fields['questions_json'],str):fields['questions_json']=json.loads(fields['questions_json'])
    fields['acknowledgements']=VERSION
    return hashlib.sha256(json.dumps(fields,sort_keys=True,default=str).encode()).hexdigest()

def requirements(invitation,candidate,client,fingerprint,policy,method):
    with database() as db:
        c=db.cursor(dictionary=True);exam=load_exam(c,invitation)
        c.execute('SELECT 1 FROM assessment_consents WHERE invitation_id=%s AND candidate_user_id=%s AND policy_version=%s',(invitation,candidate,policy));consent=bool(c.fetchone())
        from . import esignet
        method_expected=esignet.METHOD
        c.execute("SELECT 1 FROM assessment_identity_checks WHERE invitation_id=%s AND candidate_user_id=%s AND status='passed' AND access_fingerprint=%s AND evidence_expires_at>(statement_timestamp() AT TIME ZONE 'UTC') AND verification_method=%s",(invitation,candidate,fingerprint,method_expected));identity=bool(c.fetchone()) and esignet.enabled()
    return {'instructions_version':instructions_version(exam),'consent_passed':consent,'identity_passed':identity,'seb_method':method,'seb_required':method!='not_required'}

def confirm(invitation,candidate,client,fingerprint,policy,method,payload):
    state=requirements(invitation,candidate,client,fingerprint,policy,method)
    if state['instructions_version']!=payload.instructions_version:raise HTTPException(409,'The exam instructions changed. Reload and read them again.')
    with database() as db:
        c=db.cursor(dictionary=True);exam=load_exam(c,invitation)
        if instructions_version(exam)!=payload.instructions_version:raise HTTPException(409,'The exam instructions changed. Reload and read them again.')
        if load_session(c,invitation):raise HTTPException(409,'Readiness is already recorded for this attempt')
        network=True
        try:require_readiness(c,invitation,client)
        except HTTPException:network=False
        checks={**payload.checks.model_dump(),'supported_platform':payload.platform!='unsupported','network':network,'monitoring_consent':state['consent_passed'],'identity':state['identity_passed']}
        acknowledgements=payload.acknowledgements.model_dump()
        passed=all(checks.values()) and all(acknowledgements.values())
        c.execute("INSERT INTO assessment_readiness_records(invitation_id,candidate_user_id,client_instance_id,access_fingerprint,instructions_version,policy_version,checks_json,acknowledgements_json,seb_method,client_version,platform,passed,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,(statement_timestamp() AT TIME ZONE 'UTC')) RETURNING id",(invitation,candidate,client,fingerprint,payload.instructions_version,policy,json.dumps(checks),json.dumps(acknowledgements),method,payload.client_version or None,payload.platform,passed));db.commit()
    return {'passed':passed,'checks':checks,'acknowledged':all(acknowledgements.values()),'valid_for_seconds':300}

def require_complete(cursor,exam,candidate,client,fingerprint,policy):
    require_readiness(cursor,exam['invitation_id'],client)
    cursor.execute("SELECT passed,instructions_version,policy_version FROM assessment_readiness_records WHERE invitation_id=%s AND candidate_user_id=%s AND client_instance_id=%s AND access_fingerprint=%s AND created_at>=(statement_timestamp() AT TIME ZONE 'UTC')-(5 * INTERVAL '1 minute') ORDER BY id DESC LIMIT 1",(exam['invitation_id'],candidate,client,fingerprint))
    row=cursor.fetchone()
    if not row or not row['passed'] or row['instructions_version']!=instructions_version(exam) or row['policy_version']!=policy:raise HTTPException(428,'Complete the readiness checks and acknowledge the exam instructions before starting')

def record_events(invitation,candidate,client,payload):
    # This endpoint never accepts a claimed native quit, OS event or violation.
    with database() as db:
        c=db.cursor(dictionary=True);exam=load_exam(c,invitation);session=load_session(c,invitation)
        if not session or session['candidate_user_id']!=candidate or session['client_instance_id']!=client:raise HTTPException(403,'No matching exam session')
        c.execute('SELECT p.id FROM assessment_result_publications p JOIN assessment_invitations i ON i.assessment_id=p.assessment_id WHERE i.id=%s AND p.is_legacy=FALSE',(invitation,))
        if c.fetchone():raise HTTPException(409,'Published reports cannot be changed')
        end=session['submitted_at'] or session['disqualified_at'] or session.get('interrupted_at')
        if end and utc_now()>end+timedelta(minutes=15):raise HTTPException(409,'The event upload window has ended')
        c.execute('SELECT COUNT(*) AS total FROM assessment_environment_events WHERE invitation_id=%s',(invitation,));total=c.fetchone()['total']
        for item in payload.events:
            at=item.occurred_at.astimezone(timezone.utc).replace(tzinfo=None)
            # Device clocks may be wrong. Receipt time is the authoritative server time.
            if at.year<1000:raise HTTPException(422,'Invalid event time')
            c.execute('SELECT event_type,client_occurred_at,client_instance_id FROM assessment_environment_events WHERE invitation_id=%s AND request_key=%s',(invitation,str(item.request_key)));old=c.fetchone()
            # The readiness record stores milliseconds; compare at the same precision on retries.
            at=at.replace(microsecond=at.microsecond//1000*1000)
            if old:
                if old['event_type']!=item.event_type or old['client_occurred_at']!=at or old['client_instance_id']!=client:raise HTTPException(409,'Event identifier already used')
                continue
            if total>=10000:raise HTTPException(429,'Event limit reached for this attempt')
            total+=1
            c.execute("INSERT INTO assessment_environment_events(invitation_id,candidate_user_id,client_instance_id,request_key,event_type,client_occurred_at,received_at) VALUES(%s,%s,%s,%s,%s,%s,(statement_timestamp() AT TIME ZONE 'UTC')) RETURNING id",(invitation,candidate,client,str(item.request_key),item.event_type,at))
        db.commit()
    return {'recorded':len(payload.events),'source':'browser_page','automatic_disqualification':False}

LABELS={'visibility_hidden':'Exam page became hidden','visibility_visible':'Exam page became visible','window_blurred':'Exam window lost focus','window_focused':'Exam window regained focus','fullscreen_left':'Page fullscreen ended','fullscreen_entered':'Page fullscreen started','camera_unavailable':'Camera track became unavailable','camera_ready':'Camera track restored','microphone_unavailable':'Microphone track became unavailable','microphone_ready':'Microphone track restored','page_hidden':'Exam page closed or navigated away','page_restored':'Exam page restored'}

def report(cursor,invitation):
    cursor.execute('SELECT id,checks_json,acknowledgements_json,seb_method,client_version,platform,passed,created_at FROM assessment_readiness_records WHERE invitation_id=%s ORDER BY id',(invitation,))
    checks=cursor.fetchall()
    for row in checks:
        for key in ('checks_json','acknowledgements_json'):row[key.removesuffix('_json')]=json.loads(row.pop(key))
        row['created_at']=iso_utc(row['created_at'])
    cursor.execute('SELECT id,event_type,client_occurred_at,received_at FROM assessment_environment_events WHERE invitation_id=%s ORDER BY id',(invitation,));events=cursor.fetchall()
    for row in events:
        row['label']=LABELS[row['event_type']];row['source']='Browser page';row['client_occurred_at']=iso_utc(row['client_occurred_at']);row['received_at']=iso_utc(row['received_at'])
    return {'checks':checks,'events':events,'native_event_feed':'unavailable'}
