"""Company-scoped reporting and controlled exports. Media and credentials are never exported."""
from psycopg import IsolationLevel
import csv
import hashlib
import io
import json
from datetime import date,datetime,timedelta,timezone
from typing import Literal
from pathlib import Path
from xml.sax.saxutils import escape
from fastapi import HTTPException
from pydantic import BaseModel,Field,model_validator
from .db import database
from .companies import authorize_resource
from .reviews import _answers,_latest,score_details,unpack,iso,now

class Filters(BaseModel):
 search:str=Field(default='',max_length=120)
 attempt:Literal['all','not_started','in_progress','submitted','interrupted','disqualified']='all'
 review:Literal['all','not_ready','awaiting_review','reviewed','keep_score','change_score','disqualify']='all'
 audit:Literal['all','pending','not_ready','changed','needs_attention','audited']='all'
 publication:Literal['all','published','unpublished']='all'
 date_from:date|None=None
 date_to:date|None=None
 @model_validator(mode='after')
 def dates(self):
  if self.date_from and self.date_to and self.date_from>self.date_to:raise ValueError('From date must not be after To date')
  return self

def guard(user,assessment):
 member=authorize_resource(user,assessment)
 if member['role'] not in ('employer_personnel','auditor'):raise HTTPException(403,'Access denied')
 if member['role']=='auditor':
  from .auditing import assessment_for_auditor
  with database() as db:
   c=db.cursor(dictionary=True);assessment_for_auditor(c,assessment)
 return member

def matches(row,filters):
 if filters.search.strip().casefold() not in (' '.join([row['name'],row['email'],row['session_reference']])).casefold():return False
 if filters.attempt!='all' and row['attempt_state']!=filters.attempt:return False
 if filters.review!='all' and filters.review not in (row['review_state'],row['review_action']):return False
 if filters.audit!='all' and row['audit_status']!=filters.audit:return False
 if filters.publication!='all' and row['publication_status']!=filters.publication:return False
 if filters.date_from or filters.date_to:
  if not row['ended_at']:return False
  day=datetime.fromisoformat(row['ended_at'].replace('Z','+00:00')).astimezone(timezone(timedelta(hours=3))).date()
  if filters.date_from and day<filters.date_from:return False
  if filters.date_to and day>filters.date_to:return False
 return True

def result_data(assessment,user,filters):
 from .auditing import _status
 guard(user,assessment)
 with database() as db:
  db.isolation_level = IsolationLevel.REPEATABLE_READ
  c=db.cursor(dictionary=True)
  c.execute('SELECT a.*,co.name AS company_name FROM assessments a JOIN companies co ON co.id=a.company_id WHERE a.id=%s',(assessment,));exam=c.fetchone()
  c.execute('SELECT id,created_at FROM assessment_result_publications WHERE assessment_id=%s AND is_legacy=FALSE',(assessment,));publication=c.fetchone()
  c.execute('SELECT invitation_id,result_json FROM assessment_publication_results WHERE publication_id=%s',((publication or {}).get('id',0),));frozen={r['invitation_id']:unpack(r['result_json']) for r in c.fetchall()}
  c.execute('SELECT i.id AS invitation_id,i.candidate_name,i.candidate_email,s.* FROM assessment_invitations i LEFT JOIN assessment_exam_sessions s ON s.invitation_id=i.id WHERE i.assessment_id=%s AND i.removed_at IS NULL ORDER BY i.candidate_name,i.id',(assessment,));sessions=c.fetchall()
  questions=unpack(exam['questions_json']);rows=[]
  for s in sessions:
   decision=_latest(c,s['id']) if s['id'] else None
   scores=score_details(questions,_answers(c,s['id']),unpack(decision['grades_json']) if decision else {}) if s['id'] else None
   audit=_status(c,s['id'])[0] if s['id'] else {'status':'not_ready'}
   state='disqualified' if s['disqualified_at'] else 'interrupted' if s['interrupted_at'] else 'submitted' if s['submitted_at'] else 'in_progress' if s['id'] else 'not_started'
   written=[q for q in (scores or {}).get('questions',[]) if q['type']=='written']
   ended=s['submitted_at'] or s['disqualified_at'] or s['interrupted_at']
   row={'invitation_id':s['invitation_id'],'name':s['candidate_name'],'email':s['candidate_email'],'session_reference':f"AB-{assessment}-{s['invitation_id']}",'has_session':bool(s['id']),'attempt_state':state,'submitted_at':iso(s['submitted_at']),'ended_at':iso(ended),
    'mcq_earned':scores['objective_earned'] if scores and scores['objective_possible'] else None,'mcq_possible':scores['objective_possible'] if scores else sum(q['type']=='multiple_choice' for q in questions),
    'written_earned':sum(float(q['earned'] or 0) for q in written) if scores and not scores['written_pending'] and written else None,'written_possible':sum(q['type']=='written' for q in questions),'written_pending':scores['written_pending'] if scores else None,
    'written_status':'Not started' if not scores else 'No written questions' if not written else 'Awaiting grading' if scores['written_pending'] else 'Graded',
    'calculated_total':scores['calculated_score'] if scores else None,'possible':len(questions),'review_state':'reviewed' if decision else 'awaiting_review' if ended else 'not_ready','review_action':decision['action'] if decision else '',
    'outcome':'disqualified' if s['disqualified_at'] or (decision and decision['action']=='disqualify') else 'eligible' if decision else 'awaiting_review','final_score':float(decision['final_score']) if decision and decision['final_score'] is not None else None,
    'review_reason':decision['reason'] if decision else '', 'reviewer':decision['reviewer_username'] if decision else '', 'reviewed_at':iso(decision['created_at']) if decision else None,
    'audit_status':audit['status'],'publication_status':'published' if publication else 'unpublished','published_at':iso(publication['created_at']) if publication else None}
   if publication:
    snap=frozen.get(s['invitation_id'])
    if not snap:raise HTTPException(409,'A published result snapshot is unavailable. Contact the administrator.')
    objective=snap['multiple_choice_score'];written_snap=snap['written_score'];review_snap=snap['review']
    row.update(mcq_earned=objective['earned'] if objective else None,mcq_possible=objective['possible'] if objective else 0,written_earned=written_snap['earned'] if written_snap and not written_snap['pending'] else None,written_possible=written_snap['possible'] if written_snap else 0,written_pending=written_snap['pending'] if written_snap else 0,
       written_status='No written questions' if not written_snap else 'Awaiting grading' if written_snap['pending'] else 'Graded',final_score=review_snap['score'],possible=review_snap['possible'],review_action=review_snap['action'],reviewed_at=review_snap['reviewed_at'],outcome=review_snap['outcome'],submitted_at=snap['submitted_at'])
    row['calculated_total']=None if row['written_pending'] else float((objective or {}).get('earned',0))+float((written_snap or {}).get('earned',0))
   if row['outcome']=='disqualified':row['final_score']=None
   if matches(row,filters):rows.append(row)
  c.execute("SELECT COALESCE(NULLIF(full_name,''),email,username) AS name FROM users WHERE id=%s",(user,));exporter=c.fetchone()['name']
 return {'assessment':{'id':assessment,'title':exam['title'],'code':exam['internal_reference'] or '', 'company':exam['company_name']},'publication_id':publication['id'] if publication else None,'rows':rows,'generated_at':iso(now()),'exporter':exporter}

def log_export(assessment,invitation,user,format,source,filters,count,content,publication=None):
 guard(user,assessment)
 with database() as db:
  c=db.cursor(dictionary=True);c.execute("SELECT COALESCE(NULLIF(full_name,''),email,username) AS name FROM users WHERE id=%s",(user,));name=c.fetchone()['name']
  c.execute("INSERT INTO assessment_export_audit(assessment_id,invitation_id,actor_id,actor_name,format,publication_id,source_version,filters_json,record_count,content_sha256,generated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,(statement_timestamp() AT TIME ZONE 'UTC')) RETURNING id",(assessment,invitation,user,name,format,publication,source,json.dumps(filters,default=str),count,hashlib.sha256(content).hexdigest()));db.commit()

def spreadsheet(value):
 if value is None:return ''
 if isinstance(value,(int,float)):return value
 text=str(value)
 if text.lstrip().startswith(('=','+','-','@')) or text.startswith(('\t','\r','\n')):return "'"+text
 return text

def csv_export(assessment,user,filters,scope='filtered'):
 selected=Filters() if scope=='all' else filters
 data=result_data(assessment,user,selected)
 if not data['rows']:raise HTTPException(422,'No results match these filters.')
 columns=[('Company','company'),('Assessment','assessment'),('Exam code','code'),('Candidate','name'),('Email','email'),('Session reference','session_reference'),('Attempt status','attempt_state'),('Submitted at UTC','submitted_at'),('Ended at UTC','ended_at'),('MCQ earned','mcq_earned'),('MCQ maximum','mcq_possible'),('Written earned','written_earned'),('Written maximum','written_possible'),('Written grading status','written_status'),('Calculated total','calculated_total'),('Total maximum','possible'),('Review status','review_state'),('Review outcome','review_action'),('Final reviewed score','final_score'),('Eligibility outcome','outcome'),('Review reason','review_reason'),('Reviewer','reviewer'),('Reviewed at UTC','reviewed_at'),('Audit status','audit_status'),('Publication status','publication_status'),('Published at UTC','published_at'),('Exported by','exporter'),('Exported at UTC','generated_at'),('Export scope','scope'),('Applied filters','filters')]
 buffer=io.StringIO(newline='');writer=csv.writer(buffer);writer.writerow([label for label,key in columns])
 summary='; '.join(f'{key}: {value}' for key,value in selected.model_dump(mode='json').items() if value not in (None,'','all')) or 'None'
 for row in data['rows']:
  values={**row,'company':data['assessment']['company'],'assessment':data['assessment']['title'],'code':data['assessment']['code'],'exporter':data['exporter'],'generated_at':data['generated_at'],'scope':scope,'filters':summary}
  writer.writerow([spreadsheet(values.get(key)) for label,key in columns])
 content=buffer.getvalue().encode('utf-8-sig')
 log_export(assessment,None,user,'csv',hashlib.sha256(content).hexdigest(),{**selected.model_dump(mode='json'),'scope':scope},len(data['rows']),content,data['publication_id'])
 return content


def fingerprint(assessment,invitation):
 from .auditing import _status
 from .casework import latest_hold
 with database() as db:
  c=db.cursor(dictionary=True);c.execute('SELECT id FROM assessment_exam_sessions WHERE invitation_id=%s',(invitation,));session=c.fetchone()
  if not session:raise HTTPException(404,'Candidate session not found')
  status,_=_status(c,session['id']);hold=latest_hold(c,assessment)
  c.execute('SELECT id FROM assessment_result_publications WHERE assessment_id=%s AND is_legacy=FALSE',(assessment,));pub=c.fetchone()
 return ':'.join(map(str,[status['version'],status.get('outcome_revision',0),(hold or {}).get('id',0),(pub or {}).get('id',0)])),status

def pdf_export(assessment,invitation,user):
 from .reviews import session_report
 from .monitoring.repository import report as monitoring_report
 from .recordings import employer_recording
 from .casework import read_notes,history
 guard(user,assessment)
 with database() as db:
  c=db.cursor();c.execute('SELECT id FROM assessment_invitations WHERE id=%s AND assessment_id=%s AND removed_at IS NULL',(invitation,assessment))
  if not c.fetchone():raise HTTPException(404,'Candidate session not found')
 before,audit=fingerprint(assessment,invitation)
 report=session_report(assessment,invitation,user,actor_id=user)
 result=result_data(assessment,user,Filters(search=f'AB-{assessment}-{invitation}'))
 row=next((r for r in result['rows'] if r['invitation_id']==invitation),None)
 if not row:raise HTTPException(404,'Candidate session not found')
 monitoring=monitoring_report(assessment,invitation,user,limit=500);events=list(monitoring['events'])
 while len(monitoring['events'])==500:
  monitoring_page=monitoring_report(assessment,invitation,user,after_id=events[-1]['id'],limit=500)
  events.extend(monitoring_page['events'])
  if len(monitoring_page['events'])<500:break
 try:media=employer_recording(assessment,invitation,user,None,actor_id=user)
 except HTTPException as error:
  if error.status_code!=404:raise
  media={'status':'unavailable','parts':[]}
 notes=read_notes(assessment,invitation,user)
 timeline=[];page=1
 while True:
  batch=history(assessment,invitation,user,order='asc',page=page);timeline.extend(batch['items'])
  if len(timeline)>=batch['total']:break
  page+=1
 with database() as db:
  c=db.cursor(dictionary=True)
  c.execute('SELECT name_similarity,face_similarity FROM assessment_identity_checks WHERE invitation_id=%s',(invitation,));identity=c.fetchone() or {}
  c.execute('SELECT * FROM assessment_publication_holds WHERE assessment_id=%s ORDER BY id',(assessment,));holds=c.fetchall()
  c.execute('SELECT publisher_username,remark,created_at FROM assessment_result_publications WHERE assessment_id=%s AND is_legacy=FALSE',(assessment,));publication=c.fetchone()
 if before!=fingerprint(assessment,invitation)[0]:raise HTTPException(409,'The report changed during export. Retry the export.')
 content=build_pdf(result,report,row,audit,monitoring,events,media,notes,timeline,identity,holds,publication)
 if before!=fingerprint(assessment,invitation)[0]:raise HTTPException(409,'The report changed during export. Retry the export.')
 log_export(assessment,invitation,user,'pdf',hashlib.sha256(before.encode()).hexdigest(),{'scope':'complete_session'},1,content,result['publication_id'])
 return content

def build_pdf(data,report,row,audit,monitoring,events,media,notes,timeline,identity,holds,publication):
 from reportlab.platypus import SimpleDocTemplate,Paragraph
 from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
 from reportlab.lib import colors
 from reportlab.pdfbase import pdfmetrics
 from reportlab.pdfbase.ttfonts import TTFont
 fonts=[Path('C:/Windows/Fonts/nyala.ttf'),Path('/usr/share/fonts/truetype/noto/NotoSansEthiopic-Regular.ttf'),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
 font=next((p for p in fonts if p.is_file()),Path(__import__('reportlab').__file__).parent/'fonts'/'Vera.ttf')
 if 'AssessBridgeReport' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('AssessBridgeReport',str(font)))
 styles=getSampleStyleSheet()
 body=ParagraphStyle('ReportBody',fontName='AssessBridgeReport',fontSize=10,leading=15,textColor=colors.HexColor('#243b5c'),spaceAfter=7,wordWrap='CJK',splitLongWords=True)
 heading=ParagraphStyle('ReportHeading',parent=body,fontSize=15,leading=20,spaceBefore=18,spaceAfter=10,textColor=colors.HexColor('#103f7c'),keepWithNext=True)
 title=ParagraphStyle('ReportTitle',parent=heading,fontSize=23,leading=29)
 story=[]
 def text(value):return escape(str(value if value is not None else 'Not recorded')).replace('\n','<br/>')
 def para(value,style=body):story.append(Paragraph(text(value),style))
 def section(value):para(value,heading)
 def fact(label,value):para(label+': '+str(value if value is not None else 'Not recorded'))
 def grade(earned,possible):return 'Not available' if earned is None else f'{earned} / {possible}'
 def human(value):return str(value or 'Not recorded').replace('_',' ').capitalize()
 def moment(value):
  if not value:return 'Not recorded'
  parsed=datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value.replace(tzinfo=timezone.utc)
  return parsed.astimezone(timezone(timedelta(hours=3))).strftime('%d %b %Y, %H:%M:%S EAT')
 def span(ms):
  seconds=max(0,round(float(ms or 0)/1000));return f'{seconds//60}:{seconds%60:02d}'
 para('AssessBridge',title);para('Candidate audit report',heading)
 para('PUBLISHED RESULTS' if row['publication_status']=='published' else 'UNPUBLISHED - INTERNAL REVIEW',heading)
 section('Report identification')
 for label,value in [('Company',data['assessment']['company']),('Assessment',data['assessment']['title']),('Exam code',data['assessment']['code'] or 'Not set'),('Candidate',row['name']),('Email',row['email']),('Session reference',row['session_reference']),('Exported by',data['exporter']),('Exported at',moment(data['generated_at'])),('Scope','Complete session - no reporting filters applied')]:fact(label,value)
 section('Session summary')
 for label,value in [('Scheduled start',moment(report['starts_at'])),('Scheduled end',moment(report['ends_at'])),('Permitted duration',str(report['duration_minutes'])+' minutes'),('Started',moment(report['started_at'])),('Ended',moment(report['ended_at'])),('Attempt status',human(row['attempt_state'])),('End method',human(report.get('submission_reason') or report.get('interruption_reason') or report.get('disqualification_reason')))]:fact(label,value)
 if report['started_at'] and report['ended_at']:
  used=(datetime.fromisoformat(report['ended_at'].replace('Z','+00:00'))-datetime.fromisoformat(report['started_at'].replace('Z','+00:00'))).total_seconds();fact('Time used',span(used*1000))
 section('Results')
 for label,value in [('Automatic MCQ grade',grade(row['mcq_earned'],row['mcq_possible'])),('Written grade',grade(row['written_earned'],row['written_possible'])),('Written grading status',row['written_status']),('Calculated total before final adjustment',grade(row['calculated_total'],row['possible'])),('Final reviewed result','Disqualified' if row['outcome']=='disqualified' else grade(row['final_score'],row['possible'])),('Review status',human(row['review_state']))]:fact(label,value)
 section('Reviewer decision and history')
 if not report['history']:para('No final reviewer decision recorded.')
 for decision in reversed(report['history']):
  fact('Decision',human(decision['action']));fact('Reason',decision['reason']);fact('Reviewer',decision['reviewer']);fact('Recorded at',moment(decision['created_at']));fact('Previous score',decision.get('previous',{}).get('score'));fact('Recorded final score',decision['score'])
  for index,item in decision['grades'].items():para(f"Written question {int(index)+1}: {item['marks']} / 1. Reason: {item['reason']}")
 section('Identity verification')
 if (report.get('identity') or {}).get('verification_method')=='esignet_demo':
  fact('Outcome','Demo identity authenticated');fact('Method','Fictional eSignet identity and session photo; no card or portrait comparison')
 else:
  fact('Outcome',human((report.get('identity') or {}).get('status')))
  fact('Name similarity',identity.get('name_similarity'));fact('Face similarity (cosine score)',identity.get('face_similarity'))
 section('Readiness and acknowledgements')
 readiness=report.get('readiness',{})
 for check in readiness.get('checks',[]):
  fact('Readiness record',check['id']);fact('Result','Passed' if check['passed'] else 'Incomplete');fact('Recorded at',moment(check['created_at']));fact('SEB configuration check','Not required' if check['seb_method']=='not_required' else 'Verified by server');fact('Platform (client reported)',check['platform']);fact('SEB version (client reported)',check['client_version'])
  for key,value in check['checks'].items():fact(human(key),'Passed' if value else 'Not passed')
  for key,value in check['acknowledgements'].items():fact(human(key)+' acknowledgement','Confirmed' if value else 'Not confirmed')
 section('Page and device events')
 for item in readiness.get('events',[]):fact('Event',item['label']);fact('Source','Browser page');fact('Client-reported time',moment(item['client_occurred_at']));fact('Received by server',moment(item['received_at']))
 section('Monitoring coverage and evidence availability')
 fact('Recording status at export time',human(media['status']));fact('Analysis status',human(monitoring['status']));fact('Session duration',span(monitoring.get('expected_duration_ms')))
 for coverage in monitoring.get('coverage',[]):
  fact(human(coverage['stream'])+' analyzed',span(coverage.get('analysed_ms')))
  if 'identity_checked_ms' in coverage:fact('Identity comparison coverage',span(coverage['identity_checked_ms']))
  if 'usable_face_ms' in coverage:fact('Usable face coverage',span(coverage['usable_face_ms']))
 for part in media.get('parts',[]):
  fact('Recording part',str(part['id']));fact('Part started',moment(part['started_at']));fact('Part finalized',moment(part.get('finalized_at')));fact('Part status',human(part['status']))
 for capability,value in monitoring.get('capabilities',{}).items():
  if value=='unsupported':fact('Unsupported check',human(capability))
 section('Integrity observations and recorded findings')
 if not events:para('No observations recorded.')
 for event in sorted(events,key=lambda e:(e.get('part_id') or 0,e['start_ms'],e['id'])):
  para('Observation: '+human(event['event_type']),heading);fact('Time reference','Recording part '+str(event['part_id']) if event['part_id'] else 'Exam session');fact('Recording range',span(event['start_ms'])+' - '+span(event['end_ms']))
  fact('Detection strength (model score)',None if event['confidence'] is None else f"{event['confidence']:.3f} / 1")
  available=any(p['id']==event['part_id'] and p.get('total_bytes',0)>0 for p in media.get('parts',[]));fact('Supporting recording at export time','Available in platform' if available else 'Unavailable')
  found=[f for f in report['findings'] if f['event_key']==event['event_key']]
  if not found:para('Finding: Not reviewed. Automated observation remains unconfirmed.')
  for finding in reversed(found):fact('Finding',human(finding['assessment']));fact('Finding reason',finding['reason']);fact('Reviewer',finding['reviewer']);fact('Recorded at',moment(finding['created_at']))
 section('Connectivity and interruptions')
 incident_report=report.get('connectivity_report') or {}
 fact('Recovery time used',str(round(incident_report.get('recovery_seconds',0)))+' / 600 seconds')
 for exit in incident_report.get('confirmed_exits',[]):fact('Candidate selected Exit SEB',moment(exit['at']))
 if not incident_report.get('incidents') and not incident_report.get('confirmed_exits'):para('No interruptions recorded.')
 for incident in incident_report.get('incidents',[]):
  for label,value in [('Started',moment(incident['started_at'])),('Ended',moment(incident.get('ended_at'))),('Duration','Ongoing' if incident.get('duration_seconds') is None else str(incident['duration_seconds'])+' seconds'),('Cause','Platform interruption recorded' if incident.get('trigger')=='platform_unavailable' else 'Cause unknown'),('Outcome',human(incident.get('outcome')))]:fact(label,value)
  fact('Detected event',human(incident.get('trigger')))
  for check in incident.get('face_checks',[]):
   fact('Recovery face check','Passed' if check.get('passed') else 'Could not check' if check.get('status')=='unusable' else 'Not matched');fact('Checked at',moment(check.get('at')));fact('Recovery face similarity (cosine score)',check.get('similarity'))
  for gap in incident.get('gaps',[]):
   # Coverage gaps are estimates, never proof of an intentional exit.
   fact('Estimated '+gap['stream']+' recording gap',str(gap['estimated_seconds'])+' seconds')
  for finding in report.get('connectivity_findings',[]):
   if finding['incident_id']==incident.get('id'):fact('Recorded interruption finding',human(finding['finding']));fact('Reason',finding['reason']);fact('Reviewer',finding['reviewer']);fact('Recorded at',moment(finding['created_at']))
 section('Case notes')
 if not notes['notes']:para('No case notes recorded.')
 for note in reversed(notes['notes']):
  fact('Note',note['body']);fact('Author',note['actor_name']);fact('Recorded at',moment(note['created_at']))
  if note.get('reference_kind'):fact('Reference',human(note['reference_kind'])+' '+str(note['reference_key']))
 section('Audit outcomes')
 fact('Current audit status',human(audit['status']))
 if not notes['outcomes']:
  for item in reversed(audit.get('history',[])):fact('Audit completed by',item['auditor_username']);fact('Recorded at',moment(item['created_at']))
 for outcome in reversed(notes['outcomes']):fact('Outcome',human(outcome['kind']));fact('Reason',outcome['reason'] or 'Audit completed.');fact('Auditor',outcome['actor_name']);fact('Recorded at',moment(outcome['created_at']))
 section('Publication')
 fact('Status',human(row['publication_status']));fact('Publication reference',data['publication_id'])
 if publication:fact('Published by',publication['publisher_username']);fact('Published at',moment(publication['created_at']));fact('Publication remark',publication['remark'])
 for hold in holds:fact('Publication hold outcome',human(hold['kind']));fact('Reason',hold['reason']);fact('Auditor',hold['actor_name']);fact('Recorded at',moment(hold['created_at']))
 section('Complete session history')
 for item in timeline:
  para(moment(item['at'])+' - '+item['title']);fact('Recorded by',item['actor'])
  if item['details']:para(item['details'])
 output=io.BytesIO();doc=SimpleDocTemplate(output,pagesize=(595.28,841.89),rightMargin=44,leftMargin=44,topMargin=48,bottomMargin=48,title='AssessBridge candidate audit report',author=data['exporter'])
 def footer(canvas,document):
  canvas.saveState();canvas.setFont('Helvetica',8);canvas.setFillColor(colors.HexColor('#667a94'));canvas.drawString(44,26,'AssessBridge / '+row['session_reference']+' / '+row['publication_status'].upper());canvas.drawRightString(550,26,'Page '+str(document.page));canvas.restoreState()
 doc.build(story,onFirstPage=footer,onLaterPages=footer)
 return output.getvalue()


def query_filters(search:str='',attempt:Literal['all','not_started','in_progress','submitted','interrupted','disqualified']='all',review:Literal['all','not_ready','awaiting_review','reviewed','keep_score','change_score','disqualify']='all',audit:Literal['all','pending','not_ready','changed','needs_attention','audited']='all',publication:Literal['all','published','unpublished']='all',date_from:date|None=None,date_to:date|None=None):
 from pydantic import ValidationError
 try:return Filters(search=search,attempt=attempt,review=review,audit=audit,publication=publication,date_from=date_from,date_to=date_to)
 except ValidationError as error:raise HTTPException(422,'Choose valid reporting filters and date range.') from error
