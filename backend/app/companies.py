"""Company membership and administrative account lifecycle."""
import hashlib
import secrets
from email.message import EmailMessage
from urllib.parse import urljoin
from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from .db import database
from .security import hash_password, verify_password
from .mail import MailSettings, _send_message, EXAM_ENTRY_URL

STAFF = ('company_admin', 'employer_personnel', 'auditor')
class Registration(BaseModel):
    company: str = Field(min_length=1,max_length=160)
    name: str = Field(min_length=1,max_length=160)
    email: str = Field(min_length=3,max_length=254)
    password: str = Field(min_length=12)
    @field_validator('company','name')
    @classmethod
    def name_valid(cls,v):
        if not v.strip():raise ValueError('Enter a name')
        return v.strip()
    @field_validator('email')
    @classmethod
    def email_valid(cls,v):
        from .invitations import EMAIL_PATTERN
        v=v.strip().lower()
        if not EMAIL_PATTERN.fullmatch(v): raise ValueError('Enter a valid email')
        return v
    @field_validator('password')
    @classmethod
    def password_valid(cls,v):
        if len(v.encode())>72: raise ValueError('Password is too long')
        return v
class Invite(BaseModel):
    _name = field_validator('name')(Registration.name_valid.__func__)
    name: str = Field(min_length=1,max_length=160)
    email: str = Field(min_length=3,max_length=254)
    role: str
    _email = field_validator('email')(Registration.email_valid.__func__)
    @field_validator('role')
    @classmethod
    def role_valid(cls,v):
        if v not in STAFF: raise ValueError('Choose a staff role')
        return v
class Accept(BaseModel):
    token: str = Field(min_length=32,max_length=128)
    password: str = Field(min_length=12)
    _password = field_validator('password')(Registration.password_valid.__func__)
class Token(BaseModel):
    token: str = Field(min_length=32,max_length=128)

def digest(token): return hashlib.sha256(token.encode()).hexdigest()
def mail(email,token,kind,company):
    try:
        settings=MailSettings.from_environment()
        message=EmailMessage(); message['From']=settings.sender;message['To']=email
        message['Subject']='AssessBridge: '+('Verify company registration' if kind=='verify' else 'Reset your password' if kind=='reset' else 'Join '+company)
        link=urljoin(EXAM_ENTRY_URL,'/company-access')+'#'+kind+'='+token
        message.set_content('Open this link to continue: '+link+'\n\nThis link expires in 48 hours. If you did not expect this email, ignore it.')
        _send_message(settings,message)
    except Exception as exc: raise HTTPException(503,'Could not send the email. Try again.') from exc

def event(cur,company,actor,kind,target=None,invitation=None):
    cur.execute('INSERT INTO company_account_events(company_id,actor_user_id,event_type,target_user_id,invitation_id) VALUES(%s,%s,%s,%s,%s)',(company,actor,kind,target,invitation))

def membership(user_id):
    with database() as db:
        c=db.cursor(dictionary=True)
        c.execute('SELECT u.company_id,u.role FROM users u JOIN companies co ON co.id=u.company_id WHERE u.id=%s AND u.is_active=TRUE AND co.is_active=TRUE',(user_id,))
        row=c.fetchone()
    if not row: raise HTTPException(403,'Company access unavailable')
    return row

def authorize_resource(user_id,assessment=None,publication=None):
    member=membership(user_id)
    with database() as db:
        c=db.cursor()
        if assessment is not None:
            c.execute('SELECT id FROM assessments WHERE id=%s AND company_id=%s',(assessment,member['company_id']))
        elif publication is not None:
            c.execute('SELECT p.id FROM assessment_result_publications p JOIN assessments a ON a.id=p.assessment_id WHERE p.id=%s AND a.company_id=%s',(publication,member['company_id']))
        else:return member
        if not c.fetchone():raise HTTPException(404,'Not found')
    return member

def register(payload):
    token=secrets.token_urlsafe(32)
    with database() as db:
        c=db.cursor()
        c.execute("SELECT u.id,u.password_hash,v.company_id FROM users u LEFT JOIN company_registration_verifications v ON v.user_id=u.id WHERE u.email=%s",(payload.email,))
        existing=c.fetchone()
        if existing:
            if existing[2] and verify_password(payload.password,existing[1]):
                c.execute('UPDATE company_registration_verifications SET token_hash=%s,expires_at=UTC_TIMESTAMP()+INTERVAL 48 HOUR,sent_at=UTC_TIMESTAMP() WHERE user_id=%s',(digest(token),existing[0]))
                mail(payload.email,token,'verify',payload.company);db.commit()
                return {'message':'Check your email to activate your company.'}
            raise HTTPException(409,'This email is already registered. Sign in with that account or use another email.')
        c.execute('INSERT INTO companies(name) VALUES(%s)',(payload.company.strip(),));company=c.lastrowid
        c.execute("INSERT INTO users(username,email,password_hash,role,is_active,company_id,full_name) VALUES(%s,%s,%s,'company_admin',FALSE,%s,%s)",('staff_'+secrets.token_hex(12),payload.email,hash_password(payload.password),company,payload.name.strip()));user=c.lastrowid
        c.execute('INSERT INTO company_registration_verifications(user_id,company_id,token_hash,expires_at,sent_at) VALUES(%s,%s,%s,UTC_TIMESTAMP()+INTERVAL 48 HOUR,UTC_TIMESTAMP())',(user,company,digest(token)))
        mail(payload.email,token,'verify',payload.company)
        event(c,company,user,'registration');db.commit()
    return {'message':'Check your email to activate your company.'}

def verify(token):
    with database() as db:
        c=db.cursor(dictionary=True)
        c.execute('SELECT * FROM company_registration_verifications WHERE token_hash=%s AND expires_at>UTC_TIMESTAMP() FOR UPDATE',(digest(token),));r=c.fetchone()
        if not r:raise HTTPException(400,'This link is invalid or expired.')
        c.execute('UPDATE companies SET is_active=TRUE WHERE id=%s',(r['company_id'],))
        c.execute('UPDATE users SET is_active=TRUE WHERE id=%s',(r['user_id'],))
        c.execute('DELETE FROM company_registration_verifications WHERE user_id=%s',(r['user_id'],))
        event(c,r['company_id'],r['user_id'],'company_activated');db.commit()
    return {'message':'Company activated. Sign in to continue.'}

def staff_list(user_id):
    m=membership(user_id)
    with database() as db:
        c=db.cursor(dictionary=True)
        c.execute('SELECT id,name FROM companies WHERE id=%s',(m['company_id'],));co=c.fetchone()
        c.execute('SELECT id,full_name,email,role,is_active FROM users WHERE company_id=%s ORDER BY id',(m['company_id'],));users=c.fetchall()
        c.execute("SELECT id,full_name,email,role,accepted_at,revoked_at,expires_at,expires_at<=UTC_TIMESTAMP() AS expired FROM company_staff_invitations WHERE company_id=%s ORDER BY id DESC",(m['company_id'],));invitations=c.fetchall()
    return {'company':co,'users':users,'invitations':invitations}

def invite(user_id,payload):
    m=membership(user_id);token=secrets.token_urlsafe(32)
    with database() as db:
        c=db.cursor(dictionary=True)
        c.execute('SELECT name FROM companies WHERE id=%s FOR UPDATE',(m['company_id'],));co=c.fetchone()
        c.execute('SELECT id FROM users WHERE email=%s',(payload.email,))
        if c.fetchone():raise HTTPException(409,'This email cannot be invited. Use another work email.')
        c.execute('UPDATE company_staff_invitations SET revoked_at=UTC_TIMESTAMP() WHERE company_id=%s AND email=%s AND accepted_at IS NULL AND revoked_at IS NULL',(m['company_id'],payload.email))
        c.execute('INSERT INTO company_staff_invitations(company_id,email,full_name,role,token_hash,expires_at,invited_by,sent_at) VALUES(%s,%s,%s,%s,%s,UTC_TIMESTAMP()+INTERVAL 48 HOUR,%s,UTC_TIMESTAMP())',(m['company_id'],payload.email,payload.name.strip(),payload.role,digest(token),user_id));ident=c.lastrowid
        mail(payload.email,token,'accept',co['name']);event(c,m['company_id'],user_id,'staff_invited',invitation=ident);db.commit()
    return {'message':'Invitation sent.'}

def invitation(token):
    with database() as db:
        c=db.cursor(dictionary=True)
        c.execute('SELECT i.email,i.full_name,i.role,co.name AS company FROM company_staff_invitations i JOIN companies co ON co.id=i.company_id WHERE token_hash=%s AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at>UTC_TIMESTAMP() AND co.is_active=TRUE',(digest(token),));r=c.fetchone()
    if not r:raise HTTPException(400,'This invitation is invalid or expired. Ask your administrator for a new invitation.')
    return r

def accept(payload):
    with database() as db:
        c=db.cursor(dictionary=True)
        c.execute('SELECT i.* FROM company_staff_invitations i JOIN companies co ON co.id=i.company_id WHERE token_hash=%s AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at>UTC_TIMESTAMP() AND co.is_active=TRUE FOR UPDATE',(digest(payload.token),));r=c.fetchone()
        if not r:raise HTTPException(400,'This invitation is invalid or expired.')
        c.execute('SELECT id FROM users WHERE email=%s',(r['email'],))
        if c.fetchone():raise HTTPException(409,'This email is already registered.')
        c.execute('INSERT INTO users(username,email,password_hash,role,is_active,company_id,full_name) VALUES(%s,%s,%s,%s,TRUE,%s,%s)',('staff_'+secrets.token_hex(12),r['email'],hash_password(payload.password),r['role'],r['company_id'],r['full_name']));uid=c.lastrowid
        c.execute('UPDATE company_staff_invitations SET accepted_at=UTC_TIMESTAMP(),accepted_by=%s WHERE id=%s',(uid,r['id']))
        event(c,r['company_id'],uid,'invitation_accepted',uid,r['id']);db.commit()
    return {'message':'Account created. Sign in to continue.'}

def deactivate(actor,target):
    m=membership(actor)
    with database() as db:
        c=db.cursor(dictionary=True)
        c.execute('SELECT id FROM companies WHERE id=%s FOR UPDATE',(m['company_id'],));c.fetchone()
        c.execute('SELECT role,is_active FROM users WHERE id=%s AND company_id=%s FOR UPDATE',(target,m['company_id']));r=c.fetchone()
        if not r:raise HTTPException(404,'Account not found')
        if r['role']=='company_admin' and r['is_active']:
            c.execute("SELECT COUNT(*) AS n FROM users WHERE company_id=%s AND role='company_admin' AND is_active=TRUE",(m['company_id'],))
            if c.fetchone()['n']<=1:raise HTTPException(409,'The last active administrator cannot be deactivated.')
        c.execute('UPDATE users SET is_active=FALSE WHERE id=%s',(target,))
        c.execute('DELETE FROM auth_sessions WHERE user_id=%s',(target,));event(c,m['company_id'],actor,'account_deactivated',target);db.commit()
    return {'message':'Account deactivated.'}

def revoke(actor,ident):
    m=membership(actor)
    with database() as db:
        c=db.cursor();c.execute('UPDATE company_staff_invitations SET revoked_at=UTC_TIMESTAMP() WHERE id=%s AND company_id=%s AND accepted_at IS NULL AND revoked_at IS NULL',(ident,m['company_id']))
        if not c.rowcount:raise HTTPException(404,'Pending invitation not found')
        event(c,m['company_id'],actor,'invitation_revoked',invitation=ident);db.commit()
    return {'message':'Invitation revoked.'}


class CompanyDetails(BaseModel):
    name: str = Field(min_length=1,max_length=160)
    @field_validator('name')
    @classmethod
    def clean(cls,v):
        if not v.strip():raise ValueError('Enter a company name')
        return v.strip()

def update_company(actor,payload):
    member=membership(actor)
    with database() as db:
        c=db.cursor();c.execute('UPDATE companies SET name=%s WHERE id=%s',(payload.name,member['company_id']))
        event(c,member['company_id'],actor,'company_details_updated');db.commit()
    return {'message':'Company details saved.'}


class ResetRequest(BaseModel):
    email: str = Field(min_length=3,max_length=254)
    _email = field_validator('email')(Registration.email_valid.__func__)

def request_reset(payload):
    token=secrets.token_urlsafe(32)
    with database() as db:
        c=db.cursor(dictionary=True)
        c.execute("SELECT u.id FROM users u JOIN companies co ON co.id=u.company_id WHERE u.email=%s AND u.is_active=TRUE AND co.is_active=TRUE",(payload.email,));r=c.fetchone()
        if r:
            c.execute('INSERT INTO company_password_resets(user_id,token_hash,expires_at) VALUES(%s,%s,UTC_TIMESTAMP()+INTERVAL 48 HOUR) ON DUPLICATE KEY UPDATE token_hash=VALUES(token_hash),expires_at=VALUES(expires_at)',(r['id'],digest(token)))
            mail(payload.email,token,'reset','');db.commit()
    return {'message':'If an active staff account uses this email, a reset link has been sent.'}

def reset_password(payload):
    with database() as db:
        c=db.cursor(dictionary=True)
        c.execute('SELECT r.user_id,u.company_id FROM company_password_resets r JOIN users u ON u.id=r.user_id JOIN companies co ON co.id=u.company_id WHERE r.token_hash=%s AND r.expires_at>UTC_TIMESTAMP() AND u.is_active=TRUE AND co.is_active=TRUE FOR UPDATE',(digest(payload.token),));r=c.fetchone()
        if not r:raise HTTPException(400,'This reset link is invalid or expired.')
        c.execute('UPDATE users SET password_hash=%s WHERE id=%s',(hash_password(payload.password),r['user_id']))
        c.execute('DELETE FROM auth_sessions WHERE user_id=%s',(r['user_id'],));c.execute('DELETE FROM company_password_resets WHERE user_id=%s',(r['user_id'],))
        event(c,r['company_id'],r['user_id'],'password_reset',r['user_id']);db.commit()
    return {'message':'Password updated. Sign in with your new password.'}
