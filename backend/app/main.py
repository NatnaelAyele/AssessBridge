import asyncio
import base64
import binascii
import json
import logging
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Literal
import re

import mysql.connector
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from .page_errors import page_http_error
from pydantic import BaseModel, Field, field_validator

from .config import COOKIE_NAME, COOKIE_SECURE, EXAM_COOKIE_NAME, FRONTEND_DIR, ROOT_DIR, SESSION_MINUTES, SEB_REQUIRED, SECURITY_AUDIT_RETENTION_DAYS
from .candidate_assessments import candidate_assessments
from .db import database
from .exam_sessions import disqualify_exam_attempt, heartbeat_exam_attempt, navigate_exam, read_exam_state, save_exam_answer, start_exam_attempt, submit_exam_attempt, submit_expired_attempts
from .invitations import EMAIL_PATTERN, SendInvitationsRequest, assessment_detail, invitation_status, list_assessments, send_all_invitations
from .identity import clear_prestart_identity_access, identity_state, purge_expired_identity_evidence
from .registration import register_candidate, resend_verification, verify_candidate_email
from .recordings import (MAX_CHUNK_BYTES, employer_recording, encryption_key, finalize_stale_recordings,
                         finish_part, finish_recording, open_part, purge_expired_recordings,
                         recording_state, recording_exit_state, store_chunk, stream_part)
from .security import exam_entry_fingerprint, hash_password, new_exam_access, new_session, read_exam_access, read_session, verify_password
from .seb import require_seb
from .reviews import (ReviewRequest, FindingRequest, ConnectivityFindingRequest, candidate_cards, session_report,
                      save_decision, save_finding, save_connectivity_finding, reference_photo)
from . import recovery
from . import auditing
from . import publications
from . import companies
from . import casework
from . import reporting
from . import readiness
from . import retention
from . import esignet


class Role(StrEnum):
    company_admin = "company_admin"
    candidate = "candidate"
    employer_personnel = "employer_personnel"
    auditor = "auditor"


class LoginCredentials(BaseModel):
    username: str = Field(min_length=1, max_length=254)
    password: str = Field(min_length=1)


class CandidateRegistration(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12)

    @field_validator("username")
    @classmethod
    def valid_username(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", value):
            raise ValueError("Use letters, numbers, dots, underscores, or hyphens")
        return value

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("Enter a valid email address")
        return value

    @field_validator("password")
    @classmethod
    def valid_password(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("Password must be at most 72 UTF-8 bytes")
        return value


class VerificationToken(BaseModel):
    token: str = Field(min_length=32, max_length=128)


class UserInfo(BaseModel):
    id: int
    username: str
    role: Role


class ExamCredentials(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1)


class ConsentDecision(BaseModel):
    agreed: bool


class AnswerUpdate(BaseModel):
    question_index: int = Field(ge=0)
    answer: str | int | None


class NavigationRequest(BaseModel):
    direction: Literal["previous", "next"]


class RecordingPartRequest(BaseModel):
    mime_type: str = Field(min_length=1, max_length=120)


class HeartbeatRequest(BaseModel):
    ready: bool = True
    offline_seconds: int = Field(default=0, ge=0, le=86400)


class LiveRecoveryRequest(BaseModel):
    selfie: str = Field(min_length=1, max_length=4200000)


class RecordingPartFinish(BaseModel):
    final_sequence: int = Field(ge=-1, le=100000)
    interrupted: bool = False


POLICY_VERSION = "recording-monitoring-5"


async def expire_exam_attempts() -> None:
    tick = 0
    while True:
        try:
            await asyncio.to_thread(submit_expired_attempts)
            await asyncio.to_thread(finalize_stale_recordings)
            if tick % 12 == 0:
                from .monitoring.repository import purge_reference_images
                for cleanup in (purge_expired_recordings,purge_expired_identity_evidence,purge_reference_images,retention.cleanup_cases,retention.cleanup_security_logs,esignet.purge):
                    try:
                        await asyncio.to_thread(cleanup)
                    except Exception:
                        logging.getLogger(__name__).exception("Retention cleanup failed: %s",cleanup.__name__)
            tick += 1
        except Exception:
            logging.getLogger(__name__).exception("Could not process expired exams or recordings")
        await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    from .esignet_runtime import start_local_runtime, stop_local_runtime
    identity_runtime = start_local_runtime()
    sweeper = asyncio.create_task(expire_exam_attempts())
    from .monitoring.service import supervise
    monitoring = asyncio.create_task(supervise())
    try:
        yield
    finally:
        sweeper.cancel()
        with suppress(asyncio.CancelledError):
            await sweeper
        monitoring.cancel()
        with suppress(asyncio.CancelledError):
            await monitoring
        await asyncio.to_thread(stop_local_runtime, identity_runtime)


app = FastAPI(title="AssessBridge API", lifespan=lifespan)
app.add_exception_handler(StarletteHTTPException, page_http_error)
app.mount("/css", StaticFiles(directory=FRONTEND_DIR / "css"), name="css")
app.mount("/js", StaticFiles(directory=FRONTEND_DIR / "js"), name="js")
app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")


@app.get('/favicon.ico', include_in_schema=False)
def favicon():
    return FileResponse(FRONTEND_DIR/'assets'/'branding'/'mark-blue.svg', media_type='image/svg+xml')


@app.middleware("http")
async def disable_asset_cache_during_development(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/css/", "/js/")):
        response.headers["Cache-Control"] = "no-store"
    return response

_DUMMY_HASH = hash_password("invalid password placeholder")


def unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Service temporarily unavailable")


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Please sign in",
    )


def audit(cursor, event_type: str, request: Request, user_id: int | None = None, username: str | None = None) -> None:
    cursor.execute(
        "INSERT INTO auth_audit (user_id, username_attempt, event_type, source_ip, expires_at) VALUES (%s, %s, %s, %s, %s)",
        (user_id, username, event_type, request.client.host if request.client else None,datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(days=SECURITY_AUDIT_RETENTION_DAYS) if SECURITY_AUDIT_RETENTION_DAYS else None),
    )


def require_role(role: Role, request: Request, user: UserInfo) -> None:
    if user.role == role:
        if role != Role.candidate:
            companies.authorize_resource(user.id, request.path_params.get('assessment_id'), request.path_params.get('publication_id'))
        return
    try:
        with database() as connection:
            cursor = connection.cursor()
            audit(cursor, "authorization_denied", request, user.id)
            connection.commit()
            cursor.close()
    except mysql.connector.Error as exc:
        raise unavailable() from exc
    raise HTTPException(status_code=403, detail="Access denied")


def current_user(request: Request) -> UserInfo:
    token = request.cookies.get(COOKIE_NAME)
    parsed = read_session(token) if token else None
    if parsed is None:
        raise unauthorized()

    user_id, token_id = parsed
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT u.id,
                          CASE WHEN u.role = 'candidate' AND u.email IS NOT NULL THEN u.email ELSE COALESCE(NULLIF(u.full_name,''),u.email,u.username) END AS username,
                          u.role
                   FROM auth_sessions s
                   JOIN users u ON u.id = s.user_id
                   WHERE s.token_id = %s AND u.id = %s
                     AND s.expires_at > UTC_TIMESTAMP() AND u.is_active = TRUE""",
                (token_id, user_id),
            )
            row = cursor.fetchone()
            cursor.close()
    except mysql.connector.Error as exc:
        raise unavailable() from exc

    if row is None:
        raise unauthorized()
    if row["role"] != "candidate": companies.membership(row["id"])
    return UserInfo.model_validate(row)


@app.get("/", include_in_schema=False)
def login_page() -> FileResponse:
    return FileResponse(Path(FRONTEND_DIR) / "index.html")


@app.get("/employer", include_in_schema=False)
def employer_page(request: Request, user: UserInfo = Depends(current_user)) -> FileResponse:
    require_role(Role.employer_personnel, request, user)
    return FileResponse(Path(FRONTEND_DIR) / "employer.html", headers={"Cache-Control": "no-store"})


@app.get("/auditor", include_in_schema=False)
def auditor_page(request: Request, user: UserInfo = Depends(current_user)) -> FileResponse:
    require_role(Role.auditor, request, user)
    return FileResponse(Path(FRONTEND_DIR) / "auditor.html", headers={"Cache-Control": "no-store"})


@app.get("/candidate", include_in_schema=False)
def candidate_page(request: Request, user: UserInfo = Depends(current_user)) -> FileResponse:
    require_role(Role.candidate, request, user)
    return FileResponse(Path(FRONTEND_DIR) / "candidate.html", headers={"Cache-Control": "no-store"})


@app.get("/api/candidate/assessments")
def candidate_dashboard(request: Request, user: UserInfo = Depends(current_user)) -> dict:
    require_role(Role.candidate, request, user)
    return candidate_assessments(user.id)


@app.get("/exam-entry", include_in_schema=False)
def exam_entry_page() -> FileResponse:
    return FileResponse(Path(FRONTEND_DIR) / "exam-entry.html", headers={"Cache-Control": "no-store"})


@app.get("/seb/open/{exam_username}", include_in_schema=False)
def seb_open_page(exam_username: str) -> FileResponse:
    if not re.fullmatch(r"(?:abx_[0-9a-f]{20}|[A-HJ-NP-Z2-9]{5})", exam_username):
        raise HTTPException(status_code=404, detail="Exam link not found")
    return FileResponse(Path(FRONTEND_DIR) / "seb-open.html", headers={"Cache-Control": "no-store"})


@app.get("/seb/config", include_in_schema=False)
def seb_configuration() -> FileResponse:
    config_file = ROOT_DIR / "backend" / "seb" / "AssessBridge.seb"
    if not config_file.is_file():
        raise HTTPException(status_code=503, detail="SEB configuration is not installed")
    return FileResponse(config_file, filename="AssessBridge.seb", media_type="application/octet-stream", headers={"Cache-Control": "no-store"})


@app.get("/seb/launch/{exam_username}", include_in_schema=False)
def exam_seb_configuration(exam_username: str) -> Response:
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                "SELECT i.seb_config_blob FROM assessment_invitations i JOIN assessments a ON a.id=i.assessment_id WHERE i.exam_username=%s AND i.delivery_status='sent' AND "+retention.live_sql(),
                (exam_username,),
            )
            row = cursor.fetchone()
            cursor.close()
    except mysql.connector.Error as exc:
        raise unavailable() from exc
    if not row or not row["seb_config_blob"]:
        raise HTTPException(status_code=404, detail="Exam configuration unavailable")
    return Response(
        content=bytes(row["seb_config_blob"]), media_type="application/octet-stream",
        headers={"Content-Disposition": 'attachment; filename="AssessBridge-Exam.seb"', "Cache-Control": "no-store"},
    )


@app.get("/register", include_in_schema=False)
def register_page() -> FileResponse:
    return FileResponse(Path(FRONTEND_DIR) / "register.html", headers={"Cache-Control": "no-store"})


@app.get("/verify-email", include_in_schema=False)
def verify_email_page() -> FileResponse:
    return FileResponse(Path(FRONTEND_DIR) / "verify-email.html", headers={"Cache-Control": "no-store"})


@app.post("/api/auth/register-candidate", status_code=201)
def register_candidate_account(payload: CandidateRegistration) -> dict:
    return register_candidate(payload.username, payload.email, payload.password)


@app.post("/api/auth/verify-email")
def verify_candidate_account_email(payload: VerificationToken) -> dict:
    return verify_candidate_email(payload.token)


@app.post("/api/auth/resend-verification")
def resend_candidate_verification(payload: LoginCredentials) -> dict:
    return resend_verification(payload.username.strip(), payload.password)


@app.post("/api/candidate/exam-entry")
def enter_exam(credentials: ExamCredentials, request: Request, response: Response, user: UserInfo = Depends(current_user)) -> dict:
    require_role(Role.candidate, request, user)
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT email FROM users WHERE id = %s AND role = 'candidate' AND is_active = TRUE", (user.id,))
            candidate = cursor.fetchone()
            if not candidate or not candidate["email"]:
                raise HTTPException(status_code=403, detail="Your candidate account needs a registered email address")
            cursor.execute(
                """SELECT a.id, a.title, a.duration_minutes, a.starts_at, a.ends_at,
                          i.id AS invitation_id, i.candidate_user_id, i.candidate_email,
                          i.password_hash, i.delivery_status, i.seb_config_key, i.exam_username,
                          s.auth_session_token_id
                   FROM assessment_invitations i
                   JOIN assessments a ON a.id = i.assessment_id
                   LEFT JOIN assessment_exam_sessions s ON s.invitation_id = i.id
                   WHERE i.exam_username = %s FOR UPDATE""",
                (credentials.username.strip(),),
            )
            invitation = cursor.fetchone()
            password_hash = invitation["password_hash"] if invitation else _DUMMY_HASH
            if not verify_password(credentials.password, password_hash) or not invitation or invitation["delivery_status"] != "sent":
                raise HTTPException(status_code=401, detail="Invalid exam username or password")
            if invitation["candidate_email"].casefold() != candidate["email"].casefold():
                raise HTTPException(status_code=403, detail="This invitation is addressed to a different candidate email")
            if invitation["candidate_user_id"] is not None and invitation["candidate_user_id"] != user.id:
                raise HTTPException(status_code=403, detail="This invitation belongs to another candidate account")
            retention.require_live(cursor,invitation["id"])
            if invitation["seb_config_key"]:
                require_seb(request, f"/exam-entry?exam={invitation['exam_username']}", invitation["seb_config_key"])
            else:
                require_seb(request, ("/exam-entry", "/candidate"))
            if invitation["candidate_user_id"] is None:
                cursor.execute(
                    "UPDATE assessment_invitations SET candidate_user_id = %s WHERE id = %s AND candidate_user_id IS NULL",
                    (user.id, invitation["invitation_id"]),
                )
                connection.commit()
            cursor.close()
    except mysql.connector.Error as exc:
        raise unavailable() from exc

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    window = "upcoming" if now < invitation["starts_at"] else "closed" if now >= invitation["ends_at"] else "open"
    state = read_exam_state(invitation["invitation_id"], user.id)
    if state["state"] in {"submitted", "disqualified", "interrupted"}:
        window = state["state"]
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=SESSION_MINUTES)
    response.set_cookie(
        key=EXAM_COOKIE_NAME,
        value=new_exam_access(user.id, invitation["invitation_id"], expires_at, seb_verified=SEB_REQUIRED),
        max_age=max(1, int((expires_at - datetime.now(timezone.utc)).total_seconds())),
        httponly=True, secure=COOKIE_SECURE, samesite="lax", path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        "assessment_id": invitation["id"], "title": invitation["title"],
        "duration_minutes": invitation["duration_minutes"],
        "starts_at": invitation["starts_at"].isoformat() + "Z",
        "ends_at": invitation["ends_at"].isoformat() + "Z",
        "window": window,
    }


def authorized_invitation(request: Request, user: UserInfo) -> dict:
    require_role(Role.candidate, request, user)
    token = request.cookies.get(EXAM_COOKIE_NAME)
    parsed = read_exam_access(token) if token else None
    if not parsed or parsed[0] != user.id or (SEB_REQUIRED and not parsed[2]):
        raise HTTPException(status_code=403, detail="Enter your exam credentials first")
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT i.id AS invitation_id, a.id AS assessment_id, a.title,
                          i.seb_config_key,
                          s.auth_session_token_id, s.submitted_at, s.disqualified_at,
                          a.duration_minutes, a.starts_at, a.ends_at, a.identity_policy
                   FROM assessment_invitations i
                   JOIN assessments a ON a.id = i.assessment_id
                   LEFT JOIN assessment_exam_sessions s ON s.invitation_id = i.id
                   WHERE i.id = %s AND i.candidate_user_id = %s AND i.delivery_status = 'sent'""",
                (parsed[1], user.id),
            )
            exam = cursor.fetchone()
            cursor.close()
    except mysql.connector.Error as exc:
        raise unavailable() from exc
    if not exam:
        raise HTTPException(status_code=403, detail="Exam access is unavailable")
    with database() as connection:
        retention.require_live(connection.cursor(),exam["assessment_id"])
    return exam


def authorized_exam(request: Request, user: UserInfo) -> dict:
    exam = authorized_invitation(request, user)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if not exam["starts_at"] <= now < exam["ends_at"]:
        state = read_exam_state(exam["invitation_id"], user.id)
        if state["state"] == "not_started":
            raise HTTPException(status_code=403, detail="The exam access window is closed")
    return exam


def current_exam_fingerprint(request: Request) -> str:
    parsed = read_session(request.cookies[COOKIE_NAME])
    return exam_entry_fingerprint(parsed[1], request.cookies[EXAM_COOKIE_NAME])


@app.get("/exam-room", include_in_schema=False)
def exam_room_page(request: Request, user: UserInfo = Depends(current_user)) -> FileResponse:
    authorized_exam(request, user)
    return FileResponse(Path(FRONTEND_DIR) / "exam-room.html", headers={"Cache-Control": "no-store"})


@app.get("/api/candidate/exam-room")
def exam_room_state(request: Request, user: UserInfo = Depends(current_user)) -> dict:
    exam = authorized_exam(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    try:
        with database() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """SELECT 1 FROM assessment_consents
                   WHERE invitation_id = %s AND candidate_user_id = %s AND policy_version = %s""",
                (exam["invitation_id"], user.id, POLICY_VERSION),
            )
            agreed = cursor.fetchone() is not None
            cursor.close()
    except mysql.connector.Error as exc:
        raise unavailable() from exc
    return {"title": exam["title"], "duration_minutes": exam["duration_minutes"], "consented": agreed,
            "identity_policy": esignet.POLICY, "retention":retention.notice(exam["invitation_id"])}


@app.post("/api/candidate/exam-room/consent")
def accept_exam_consent(decision: ConsentDecision, request: Request, user: UserInfo = Depends(current_user)) -> dict:
    exam = authorized_exam(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if not exam["starts_at"] <= now < exam["ends_at"]:
        raise HTTPException(status_code=403, detail="The exam access window is closed")
    if not decision.agreed:
        raise HTTPException(status_code=400, detail="Agreement is required to continue")
    try:
        with database() as connection:
            cursor = connection.cursor()
            cursor.execute(
                """INSERT INTO assessment_consents (invitation_id, candidate_user_id, policy_version)
                   VALUES (%s, %s, %s)
                   ON DUPLICATE KEY UPDATE id = id""",
                (exam["invitation_id"], user.id, POLICY_VERSION),
            )
            connection.commit()
            cursor.close()
    except mysql.connector.Error as exc:
        raise unavailable() from exc
    return {"consented": True}


@app.get("/api/candidate/identity")
def get_candidate_identity(request: Request, response: Response, user: UserInfo = Depends(current_user)) -> dict:
    exam = authorized_exam(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    response.headers["Cache-Control"] = "no-store"
    return identity_state(exam["invitation_id"], user.id, current_exam_fingerprint(request))


@app.get('/api/identity-options')
def identity_options(user: UserInfo = Depends(current_user)):
    return {'esignet_demo':esignet.enabled()}


@app.post('/api/candidate/identity/esignet/start')
def start_demo_authentication(request:Request,response:Response,user:UserInfo=Depends(current_user)):
    exam=authorized_exam(request,user)
    require_seb(request,'/exam-room',exam['seb_config_key'])
    response.headers['Cache-Control']='no-store'
    return esignet.begin(exam['invitation_id'],user.id,current_exam_fingerprint(request),POLICY_VERSION)


@app.get('/identity/esignet/callback',include_in_schema=False)
def demo_authentication_callback(request:Request,state:str=Query(min_length=20,max_length=128),
                                 code:str|None=Query(default=None,max_length=8192),error:str|None=Query(default=None,max_length=200),
                                 user:UserInfo=Depends(current_user)):
    # This redirect cannot supply SEB request headers. The one-use state is bound
    # to the current account and exam-entry cookies; completion requires SEB again.
    exam=authorized_exam(request,user)
    try:
        esignet.finish(state,code,error,exam['invitation_id'],user.id,current_exam_fingerprint(request),POLICY_VERSION)
    except HTTPException as exc:
        if exc.status_code not in (403,409,503):
            raise
        # A recorded flow failure is shown beside the retry action in the room.
    return RedirectResponse('/exam-room',status_code=303,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})


@app.post('/api/candidate/identity/esignet/complete')
async def complete_demo_authentication(request:Request,response:Response,user:UserInfo=Depends(current_user)):
    exam=authorized_exam(request,user)
    require_seb(request,'/exam-room',exam['seb_config_key'])
    raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>4201000:
            raise HTTPException(413,'The camera image is too large')
    try:
        payload=json.loads(raw)
        if not isinstance(payload,dict) or set(payload)!={'selfie'} or not isinstance(payload['selfie'],str):
            raise ValueError('Invalid image fields')
        image=base64.b64decode(payload['selfie'],validate=True)
    except (ValueError,TypeError,binascii.Error) as exc:
        raise HTTPException(422,'Send a camera image as base64 JPEG or PNG') from exc
    response.headers['Cache-Control']='no-store'
    return await asyncio.to_thread(esignet.complete,exam['invitation_id'],user.id,current_exam_fingerprint(request),POLICY_VERSION,image)


def ensure_exam_client(request, user, exam):
    client = recovery.client_id(request.headers.get("X-Exam-Client"))
    recovery.check_client(exam["invitation_id"], user.id, client, read_session(request.cookies[COOKIE_NAME])[1])
    return client


@app.post("/api/candidate/exam/prepare")
def prepare_exam_client(request: Request, user: UserInfo = Depends(current_user)):
    exam = authorized_invitation(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    return recovery.prepare(exam["invitation_id"], user.id, recovery.client_id(request.headers.get("X-Exam-Client")), read_session(request.cookies[COOKIE_NAME])[1])


@app.post("/api/candidate/exam/network-check")
def begin_network_check(request: Request, user: UserInfo = Depends(current_user)):
    exam = authorized_invitation(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    client = ensure_exam_client(request, user, exam)
    recovery.begin_probe(exam["invitation_id"], client)
    return {"upload_bytes": 131072}


@app.put("/api/candidate/exam/network-check")
async def finish_network_check(request: Request, user: UserInfo = Depends(current_user)):
    exam = authorized_invitation(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    client = ensure_exam_client(request, user, exam)
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 131072:
            raise HTTPException(413, "Network check payload is too large")
    return recovery.complete_probe(exam["invitation_id"], client, size)


@app.post("/api/candidate/exam/recovery/identity")
def check_recovery_identity(payload: LiveRecoveryRequest, request: Request, user: UserInfo = Depends(current_user)):
    exam = authorized_invitation(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    client = ensure_exam_client(request, user, exam)
    try:
        selfie = base64.b64decode(payload.selfie, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(422, "Capture your face again")
    return recovery.verify_live(exam["invitation_id"], user.id, client, selfie)


@app.post("/api/candidate/exam/recovery/resume")
def resume_exam_client(request: Request, user: UserInfo = Depends(current_user)):
    exam = authorized_invitation(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    client = ensure_exam_client(request, user, exam)
    return recovery.resume(exam["invitation_id"], user.id, client)


@app.get("/api/candidate/exam/session")
def get_exam_session(request: Request, user: UserInfo = Depends(current_user)) -> dict:
    exam = authorized_invitation(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    client = ensure_exam_client(request, user, exam)
    state = read_exam_state(exam["invitation_id"], user.id, client_id=client)
    return state


@app.post("/api/candidate/exam/heartbeat")
def exam_heartbeat(request: Request, payload: HeartbeatRequest, user: UserInfo = Depends(current_user)) -> dict:
    exam = authorized_invitation(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    client = ensure_exam_client(request, user, exam)
    return heartbeat_exam_attempt(exam["invitation_id"], user.id, payload.ready, payload.offline_seconds, client_id=client)


@app.post("/api/candidate/exam/start")
def start_exam(request: Request, response: Response, user: UserInfo = Depends(current_user)) -> dict:
    exam = authorized_invitation(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    encryption_key()
    client = ensure_exam_client(request, user, exam)
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        recovery.require_readiness(cursor, exam["invitation_id"], client)
    token_id = read_session(request.cookies[COOKIE_NAME])[1]
    state = start_exam_attempt(exam["invitation_id"], user.id, POLICY_VERSION, token_id,
                               current_exam_fingerprint(request), client)
    if state["state"] == "active":
        deadline = datetime.fromisoformat(state["deadline_at"].replace("Z", "+00:00"))
        remaining_minutes = max(SESSION_MINUTES, int((deadline - datetime.now(timezone.utc)).total_seconds() / 60) + 61)
        token, token_id, expires_at = new_session(user.id, minutes=remaining_minutes)
        try:
            with database() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    "INSERT INTO auth_sessions (token_id, user_id, expires_at) VALUES (%s, %s, %s)",
                    (token_id, user.id, expires_at.replace(tzinfo=None)),
                )
                cursor.execute(
                    "UPDATE assessment_exam_sessions SET auth_session_token_id = %s WHERE invitation_id = %s AND candidate_user_id = %s AND disqualified_at IS NULL AND submitted_at IS NULL",
                    (token_id, exam["invitation_id"], user.id),
                )
                connection.commit()
                cursor.close()
        except mysql.connector.Error as exc:
            raise unavailable() from exc
        response.set_cookie(COOKIE_NAME, token, max_age=remaining_minutes * 60, httponly=True, secure=COOKIE_SECURE, samesite="lax", path="/")
        response.set_cookie(EXAM_COOKIE_NAME, new_exam_access(user.id, exam["invitation_id"], expires_at, seb_verified=SEB_REQUIRED), max_age=remaining_minutes * 60, httponly=True, secure=COOKIE_SECURE, samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"
    return state


@app.get("/api/candidate/exam/recording")
def get_recording_status(request: Request, user: UserInfo = Depends(current_user)) -> dict:
    exam = authorized_invitation(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    return recording_state(exam["invitation_id"], user.id)


@app.post("/api/candidate/exam/recording/parts", status_code=201)
def start_recording_part(payload: RecordingPartRequest, request: Request, user: UserInfo = Depends(current_user)) -> dict:
    exam = authorized_invitation(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    ensure_exam_client(request, user, exam)
    return open_part(exam["invitation_id"], user.id, payload.mime_type)


@app.post("/api/candidate/exam/recording/parts/{part_id}/chunks/{sequence}")
async def upload_recording_chunk(part_id: int, sequence: int, request: Request,
                                 user: UserInfo = Depends(current_user)) -> dict:
    exam = authorized_invitation(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    ensure_exam_client(request, user, exam)
    if not 0 <= sequence <= 100000:
        raise HTTPException(status_code=422, detail="Invalid recording sequence")
    pieces = []
    size = 0
    async for piece in request.stream():
        size += len(piece)
        if size > MAX_CHUNK_BYTES:
            raise HTTPException(status_code=413, detail="Recording chunk is too large")
        pieces.append(piece)
    return await asyncio.to_thread(store_chunk, exam["invitation_id"], user.id, part_id, sequence, b"".join(pieces))


@app.post("/api/candidate/exam/recording/parts/{part_id}/finish")
def end_recording_part(part_id: int, payload: RecordingPartFinish, request: Request,
                       user: UserInfo = Depends(current_user)) -> dict:
    exam = authorized_invitation(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    ensure_exam_client(request, user, exam)
    return finish_part(exam["invitation_id"], user.id, part_id, payload.final_sequence, payload.interrupted)


@app.post("/api/candidate/exam/recording/finish")
def end_recording(request: Request, user: UserInfo = Depends(current_user)) -> dict:
    exam = authorized_invitation(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    ensure_exam_client(request, user, exam)
    return finish_recording(exam["invitation_id"], user.id)


@app.post("/api/candidate/exam/answer")
def save_answer(payload: AnswerUpdate, request: Request, user: UserInfo = Depends(current_user)) -> dict:
    exam = authorized_invitation(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    client = ensure_exam_client(request, user, exam)
    return save_exam_answer(exam["invitation_id"], user.id, payload.question_index, payload.answer, client_id=client)


@app.post("/api/candidate/exam/navigate")
def move_question(payload: NavigationRequest, request: Request, user: UserInfo = Depends(current_user)) -> dict:
    exam = authorized_invitation(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    client = ensure_exam_client(request, user, exam)
    return navigate_exam(exam["invitation_id"], user.id, payload.direction, client_id=client)


@app.post("/api/candidate/exam/submit")
def submit_exam(request: Request, user: UserInfo = Depends(current_user)) -> dict:
    exam = authorized_invitation(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    client = ensure_exam_client(request, user, exam)
    return submit_exam_attempt(exam["invitation_id"], user.id, client_id=client)


@app.post("/api/candidate/exam/leave")
def leave_exam(request: Request, user: UserInfo = Depends(current_user)) -> dict:
    exam = authorized_invitation(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    state = disqualify_exam_attempt(exam["invitation_id"], user.id)
    return {"state": state["state"], "exit_url": "/seb/exit"}


@app.get("/api/candidate/exam/exit-ready")
def exam_exit_ready(request: Request, user: UserInfo = Depends(current_user)) -> dict:
    exam = authorized_invitation(request, user)
    require_seb(request, "/exam-room", exam["seb_config_key"])
    ensure_exam_client(request, user, exam)
    return recording_exit_state(exam["invitation_id"], user.id)


@app.get("/seb/exit", include_in_schema=False)
def exit_exam(request: Request) -> FileResponse:
    # SEB's configured Quit URL must work even before the candidate has signed in.
    try:
        user = current_user(request)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        user = None
    if user and user.role == Role.candidate and request.cookies.get(EXAM_COOKIE_NAME):
        try:
            exam = authorized_invitation(request, user)
        except HTTPException as exc:
            if exc.status_code != 403:
                raise
        else:
            state = disqualify_exam_attempt(exam["invitation_id"], user.id)
            if state["state"] == "not_started":
                clear_prestart_identity_access(exam["invitation_id"], user.id, current_exam_fingerprint(request))
    return FileResponse(Path(FRONTEND_DIR) / "exam-exit.html", headers={"Cache-Control": "no-store"})


@app.get("/policies/privacy", include_in_schema=False)
def privacy_policy_page() -> FileResponse:
    return FileResponse(Path(FRONTEND_DIR) / "privacy-policy.html", headers={"Cache-Control": "no-store"})


@app.get("/policies/data-control", include_in_schema=False)
def data_control_policy_page() -> FileResponse:
    return FileResponse(Path(FRONTEND_DIR) / "data-control-policy.html", headers={"Cache-Control": "no-store"})


@app.post("/api/employer/invitations")
def create_invitations(payload: SendInvitationsRequest, request: Request, user: UserInfo = Depends(current_user)) -> dict:
    require_role(Role.employer_personnel, request, user)
    return send_all_invitations(payload, user.id)


@app.get("/api/employer/assessments")
def get_assessments(request: Request, page: int = Query(default=1, ge=1),
                    search: str = Query(default="", max_length=120),
                    status: Literal["all", "scheduled", "open", "closed"] = "all",
                    candidates_min: int | None = Query(default=None, ge=0, le=200),
                    candidates_max: int | None = Query(default=None, ge=0, le=200),
                    questions_min: int | None = Query(default=None, ge=0, le=200),
                    questions_max: int | None = Query(default=None, ge=0, le=200),
                    user: UserInfo = Depends(current_user)) -> dict:
    require_role(Role.employer_personnel, request, user)
    return list_assessments(user.id, page, search.strip(), status,
                            candidates_min, candidates_max, questions_min, questions_max)


@app.get("/api/employer/assessments/{assessment_id}")
def get_assessment(assessment_id: int, request: Request, response: Response,
                   user: UserInfo = Depends(current_user)) -> dict:
    require_role(Role.employer_personnel, request, user)
    response.headers["Cache-Control"] = "no-store"
    return assessment_detail(assessment_id, user.id)


@app.get("/api/employer/assessments/{assessment_id}/invitations/{invitation_id}/recording")
def get_employer_recording(assessment_id: int, invitation_id: int, request: Request,
                           user: UserInfo = Depends(current_user)) -> dict:
    require_role(Role.employer_personnel, request, user)
    result = employer_recording(assessment_id, invitation_id, user.id, request.client.host if request.client else None)
    return {"status": result["status"], "parts": result["parts"]}


@app.get("/api/employer/assessments/{assessment_id}/reviews")
def get_review_cards(assessment_id: int, request: Request, response: Response, user: UserInfo = Depends(current_user)):
    require_role(Role.employer_personnel, request, user)
    response.headers["Cache-Control"] = "no-store"
    return candidate_cards(assessment_id, user.id)


@app.get("/api/employer/assessments/{assessment_id}/invitations/{invitation_id}/review")
def get_session_review(assessment_id: int, invitation_id: int, request: Request, response: Response, user: UserInfo = Depends(current_user)):
    require_role(Role.employer_personnel, request, user)
    response.headers["Cache-Control"] = "no-store"
    return session_report(assessment_id, invitation_id, user.id, request.client.host if request.client else None)


@app.post("/api/employer/assessments/{assessment_id}/invitations/{invitation_id}/review/decisions")
def post_review_decision(assessment_id: int, invitation_id: int, payload: ReviewRequest, request: Request, user: UserInfo = Depends(current_user)):
    require_role(Role.employer_personnel, request, user)
    return save_decision(assessment_id, invitation_id, user.id, payload)


@app.post("/api/employer/assessments/{assessment_id}/invitations/{invitation_id}/review/events/{event_key}/findings")
def post_review_finding(assessment_id: int, invitation_id: int, event_key: str, payload: FindingRequest, request: Request, user: UserInfo = Depends(current_user)):
    require_role(Role.employer_personnel, request, user)
    return save_finding(assessment_id, invitation_id, user.id, event_key, payload)


@app.post("/api/employer/assessments/{assessment_id}/invitations/{invitation_id}/review/interruptions/{incident_id}/findings")
def post_connectivity_finding(assessment_id: int, invitation_id: int, incident_id: int, payload: ConnectivityFindingRequest,
                              request: Request, user: UserInfo = Depends(current_user)):
    require_role(Role.employer_personnel, request, user)
    return save_connectivity_finding(assessment_id, invitation_id, user.id, incident_id, payload)


@app.get("/api/employer/assessments/{assessment_id}/invitations/{invitation_id}/review/reference")
def get_review_reference(assessment_id: int, invitation_id: int, request: Request, user: UserInfo = Depends(current_user)):
    require_role(Role.employer_personnel, request, user)
    return Response(reference_photo(assessment_id, invitation_id, user.id, request.client.host if request.client else None),
                    media_type="image/jpeg", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.get("/api/employer/assessments/{assessment_id}/invitations/{invitation_id}/monitoring")
def get_employer_monitoring(assessment_id: int, invitation_id: int, request: Request, response: Response,
                           after_id: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500),
                           user: UserInfo = Depends(current_user)) -> dict:
    require_role(Role.employer_personnel, request, user)
    response.headers["Cache-Control"] = "no-store"
    from .monitoring.repository import report
    return report(assessment_id, invitation_id, user.id, after_id, limit)


@app.get("/api/employer/assessments/{assessment_id}/invitations/{invitation_id}/recording/parts/{part_id}")
def play_employer_recording(assessment_id: int, invitation_id: int, part_id: int,
                            request: Request, user: UserInfo = Depends(current_user)) -> StreamingResponse:
    require_role(Role.employer_personnel, request, user)
    result = employer_recording(assessment_id, invitation_id, user.id, request.client.host if request.client else None)
    return stream_part(result, part_id, request.headers.get("range"))


@app.get("/api/employer/assessments/{assessment_id}/invitations")
def get_invitations(assessment_id: int, request: Request, user: UserInfo = Depends(current_user)) -> dict:
    require_role(Role.employer_personnel, request, user)
    return invitation_status(assessment_id, user.id)


@app.get('/api/auditor/overview')
def auditor_overview(request:Request,response:Response,user:UserInfo=Depends(current_user)):
    require_role(Role.auditor,request,user)
    response.headers['Cache-Control']='no-store'
    return auditing.overview(companies.membership(user.id)['company_id'])


@app.get("/api/auditor/assessments")
def auditor_assessments(request: Request, response: Response,
                        page: int = Query(1, ge=1), search: str = Query("", max_length=120),
                        audit: Literal["all", "pending", "audited"] = "all",
                        user: UserInfo = Depends(current_user)):
    require_role(Role.auditor, request, user)
    response.headers["Cache-Control"] = "no-store"
    return auditing.assessments(page, search.strip(), audit, companies.membership(user.id)["company_id"])


@app.get("/api/auditor/assessments/{assessment_id}/candidates")
def auditor_candidates(assessment_id: int, request: Request, response: Response, user: UserInfo = Depends(current_user)):
    require_role(Role.auditor, request, user)
    response.headers["Cache-Control"] = "no-store"
    return auditing.candidates(assessment_id)


@app.get("/api/auditor/assessments/{assessment_id}/candidates/{invitation_id}/report")
def auditor_report(assessment_id: int, invitation_id: int, request: Request, response: Response,
                   user: UserInfo = Depends(current_user)):
    require_role(Role.auditor, request, user)
    response.headers["Cache-Control"] = "no-store"
    return auditing.case_report(assessment_id, invitation_id, user.id, request.client.host if request.client else None)


@app.get("/api/auditor/publications")
def auditor_publication_history(request: Request, response: Response, page: int = Query(1, ge=1),
                                search: str = Query("", max_length=120),
                                period: Literal["all", "today", "last7", "last30", "custom"] = "all",
                                date_from: date | None = None, date_to: date | None = None,
                                user: UserInfo = Depends(current_user)):
    require_role(Role.auditor, request, user)
    response.headers["Cache-Control"] = "no-store"
    return publications.publication_history(page, search.strip(), period, date_from, date_to, companies.membership(user.id)["company_id"])


@app.get("/api/auditor/publications/{publication_id}")
def auditor_published_results(publication_id: int, request: Request, response: Response,
                              user: UserInfo = Depends(current_user)):
    require_role(Role.auditor, request, user)
    response.headers["Cache-Control"] = "no-store"
    return publications.published_results(publication_id)


@app.post("/api/auditor/assessments/{assessment_id}/publish")
def publish_results(assessment_id: int, payload: publications.PublishResults,
                    request: Request, response: Response, user: UserInfo = Depends(current_user)):
    require_role(Role.auditor, request, user)
    response.headers["Cache-Control"] = "no-store"
    return publications.publish(assessment_id, user.id, payload)


@app.post("/api/auditor/assessments/{assessment_id}/candidates/{invitation_id}/audit")
def mark_audited(assessment_id: int, invitation_id: int, payload: auditing.AuditCompletion,
                 request: Request, response: Response, user: UserInfo = Depends(current_user)):
    require_role(Role.auditor, request, user)
    response.headers["Cache-Control"] = "no-store"
    return auditing.complete(assessment_id, invitation_id, user.id, payload.expected_version,
                             request.client.host if request.client else None,payload.resolution_reason,payload.expected_outcome)


@app.get("/api/auditor/assessments/{assessment_id}/candidates/{invitation_id}/identity/{kind}")
def auditor_identity(assessment_id: int, invitation_id: int, kind: Literal["card", "selfie"],
                     request: Request, user: UserInfo = Depends(current_user)):
    require_role(Role.auditor, request, user)
    return Response(auditing.identity_image(assessment_id, invitation_id, kind, user.id,
                    request.client.host if request.client else None), media_type="image/jpeg",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.get("/api/auditor/assessments/{assessment_id}/candidates/{invitation_id}/recording/parts/{part_id}")
def auditor_recording(assessment_id: int, invitation_id: int, part_id: int,
                      request: Request, user: UserInfo = Depends(current_user)):
    require_role(Role.auditor, request, user)
    media = employer_recording(assessment_id, invitation_id, auditing.owner(assessment_id),
                               request.client.host if request.client else None, actor_id=user.id)
    return stream_part(media, part_id, request.headers.get("range"))


@app.post("/api/auth/login", response_model=UserInfo)
def login(credentials: LoginCredentials, request: Request, response: Response) -> UserInfo:
    username = credentials.username.strip()

    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            if "@" in username:
                cursor.execute(
                    "SELECT id, username, email, full_name, password_hash, role, is_active FROM users WHERE email = %s",
                    (username.lower(),),
                )
            else:
                cursor.execute(
                    "SELECT id, username, email, full_name, password_hash, role, is_active FROM users WHERE username = %s",
                    (username,),
                )
            user = cursor.fetchone()
            password_hash = user["password_hash"] if user else _DUMMY_HASH
            valid_password = verify_password(credentials.password, password_hash)
            if not user or not valid_password or not user["is_active"]:
                audit(cursor, "login_failure", request, user["id"] if user else None, username[:100])
                connection.commit()
                cursor.close()
                raise HTTPException(status_code=401, detail="Invalid username or password")

            if user["role"] != "candidate": companies.membership(user["id"])
            token, token_id, expires_at = new_session(user["id"])
            cursor.execute(
                "INSERT INTO auth_sessions (token_id, user_id, expires_at) VALUES (%s, %s, %s)",
                (token_id, user["id"], expires_at.replace(tzinfo=None)),
            )
            audit(cursor, "login_success", request, user["id"])
            connection.commit()
            cursor.close()
    except mysql.connector.Error as exc:
        raise unavailable() from exc

    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_MINUTES * 60,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    response.headers["Cache-Control"] = "no-store"
    display_name = user["email"] if user["role"] == "candidate" and user["email"] else (user.get("full_name") or user["email"] or user["username"])
    return UserInfo(id=user["id"], username=display_name, role=user["role"])


@app.get("/api/auth/me", response_model=UserInfo)
def me(user: UserInfo = Depends(current_user)) -> UserInfo:
    return user


@app.get("/api/auth/authorize/{role}")
def authorize(role: Role, request: Request, user: UserInfo = Depends(current_user)) -> dict[str, bool]:
    require_role(role, request, user)
    return {"authorized": True}


@app.post("/api/auth/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(COOKIE_NAME)
    parsed = read_session(token) if token else None
    if parsed:
        user_id, token_id = parsed
        try:
            with database() as connection:
                cursor = connection.cursor()
                cursor.execute("DELETE FROM auth_sessions WHERE token_id = %s", (token_id,))
                if cursor.rowcount:
                    audit(cursor, "logout", request, user_id)
                connection.commit()
                cursor.close()
        except mysql.connector.Error as exc:
            raise unavailable() from exc
    response.delete_cookie(COOKIE_NAME, path="/")
    response.delete_cookie(EXAM_COOKIE_NAME, path="/")
    response.headers["Cache-Control"] = "no-store"


@app.get('/company-register', include_in_schema=False)
@app.get('/company-access', include_in_schema=False)
def company_access_page():
    return FileResponse(Path(FRONTEND_DIR)/'company-access.html',headers={'Cache-Control':'no-store'})

@app.get('/company-admin', include_in_schema=False)
def company_admin_page(request:Request,user:UserInfo=Depends(current_user)):
    require_role(Role.company_admin,request,user)
    return FileResponse(Path(FRONTEND_DIR)/'company-admin.html',headers={'Cache-Control':'no-store'})

@app.post('/api/company/register')
def company_register(payload:companies.Registration):return companies.register(payload)
@app.post('/api/company/verify')
def company_verify(payload:companies.Token):return companies.verify(payload.token)
@app.post('/api/company/invitation')
def company_invitation(payload:companies.Token):return companies.invitation(payload.token)
@app.post('/api/company/accept')
def company_accept(payload:companies.Accept):return companies.accept(payload)
@app.get('/api/company/staff')
def company_staff(request:Request,response:Response,user:UserInfo=Depends(current_user)):
    require_role(Role.company_admin,request,user);response.headers['Cache-Control']='no-store'
    return companies.staff_list(user.id)
@app.post('/api/company/staff/invite')
def company_invite(payload:companies.Invite,request:Request,user:UserInfo=Depends(current_user)):
    require_role(Role.company_admin,request,user);return companies.invite(user.id,payload)
@app.post('/api/company/staff/{target}/deactivate')
def company_deactivate(target:int,request:Request,user:UserInfo=Depends(current_user)):
    require_role(Role.company_admin,request,user);return companies.deactivate(user.id,target)
@app.post('/api/company/invitations/{ident}/revoke')
def company_revoke(ident:int,request:Request,user:UserInfo=Depends(current_user)):
    require_role(Role.company_admin,request,user);return companies.revoke(user.id,ident)


@app.post('/api/company/details')
def company_details(payload:companies.CompanyDetails,request:Request,user:UserInfo=Depends(current_user)):
    require_role(Role.company_admin,request,user);return companies.update_company(user.id,payload)


@app.get('/password-reset',include_in_schema=False)
def password_reset_page():
    return FileResponse(FRONTEND_DIR/'company-access.html',headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})


@app.post('/api/auth/password/request')
@app.post('/api/company/password/request')
def company_reset_request(payload:companies.ResetRequest):return companies.request_reset(payload)
@app.post('/api/auth/password/reset')
@app.post('/api/company/password/reset')
def company_reset_password(payload:companies.Accept):return companies.reset_password(payload)


@app.post('/api/employer/assessments/{assessment_id}/invitations/{invitation_id}/remove')
def remove_assigned_candidate(assessment_id:int,invitation_id:int,request:Request,response:Response,user:UserInfo=Depends(current_user)):
    require_role(Role.employer_personnel,request,user)
    response.headers['Cache-Control']='no-store'
    from .invitations import remove_candidate_assignment
    return remove_candidate_assignment(assessment_id,invitation_id,user.id)


@app.get('/api/employer/assessments/{assessment_id}/invitations/{invitation_id}/review/notes')
@app.get('/api/auditor/assessments/{assessment_id}/candidates/{invitation_id}/notes')
def read_case_notes(assessment_id:int,invitation_id:int,request:Request,response:Response,user:UserInfo=Depends(current_user)):
    require_role(Role.auditor if request.url.path.startswith('/api/auditor/') else Role.employer_personnel,request,user)
    response.headers['Cache-Control']='no-store'
    return casework.read_notes(assessment_id,invitation_id,user.id)

@app.post('/api/employer/assessments/{assessment_id}/invitations/{invitation_id}/review/notes')
def create_case_note(assessment_id:int,invitation_id:int,payload:casework.Note,request:Request,user:UserInfo=Depends(current_user)):
    require_role(Role.employer_personnel,request,user)
    return casework.add_note(assessment_id,invitation_id,user.id,payload)

@app.get('/api/employer/assessments/{assessment_id}/invitations/{invitation_id}/review/history')
@app.get('/api/auditor/assessments/{assessment_id}/candidates/{invitation_id}/history')
def read_case_history(assessment_id:int,invitation_id:int,request:Request,response:Response,category:Literal['all','identity','assessment','connectivity','monitoring','review','audit']='all',order:Literal['asc','desc']='desc',page:int=Query(1,ge=1),user:UserInfo=Depends(current_user)):
    require_role(Role.auditor if request.url.path.startswith('/api/auditor/') else Role.employer_personnel,request,user)
    response.headers['Cache-Control']='no-store'
    return casework.history(assessment_id,invitation_id,user.id,category,order,page)

@app.post('/api/auditor/assessments/{assessment_id}/candidates/{invitation_id}/concern')
def record_audit_concern(assessment_id:int,invitation_id:int,payload:casework.Concern,request:Request,user:UserInfo=Depends(current_user)):
    require_role(Role.auditor,request,user)
    return casework.concern(assessment_id,invitation_id,user.id,payload)

@app.post('/api/auditor/assessments/{assessment_id}/hold')
def record_publication_hold(assessment_id:int,payload:casework.Hold,request:Request,user:UserInfo=Depends(current_user)):
    require_role(Role.auditor,request,user)
    return casework.change_hold(assessment_id,user.id,payload)


@app.get('/api/employer/assessments/{assessment_id}/results')
@app.get('/api/auditor/assessments/{assessment_id}/results')
def reporting_results(assessment_id:int,request:Request,response:Response,filters:reporting.Filters=Depends(reporting.query_filters),page:int=Query(1,ge=1),user:UserInfo=Depends(current_user)):
    require_role(Role.auditor if request.url.path.startswith('/api/auditor/') else Role.employer_personnel,request,user)
    data=reporting.result_data(assessment_id,user.id,filters)
    data['total']=len(data['rows']);data['rows']=data['rows'][(page-1)*50:page*50];data['page']=page;data['page_size']=50
    response.headers['Cache-Control']='no-store'
    return data

@app.get('/api/employer/assessments/{assessment_id}/exports/results.csv')
@app.get('/api/auditor/assessments/{assessment_id}/exports/results.csv')
def export_result_csv(assessment_id:int,request:Request,scope:Literal['all','filtered']='filtered',filters:reporting.Filters=Depends(reporting.query_filters),user:UserInfo=Depends(current_user)):
    require_role(Role.auditor if request.url.path.startswith('/api/auditor/') else Role.employer_personnel,request,user)
    content=reporting.csv_export(assessment_id,user.id,filters,scope)
    return Response(content,media_type='text/csv; charset=utf-8',headers={'Content-Disposition':f'attachment; filename="assessment-{assessment_id}-results.csv"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

@app.get('/api/employer/assessments/{assessment_id}/invitations/{invitation_id}/exports/audit.pdf')
@app.get('/api/auditor/assessments/{assessment_id}/candidates/{invitation_id}/exports/audit.pdf')
def export_audit_pdf(assessment_id:int,invitation_id:int,request:Request,user:UserInfo=Depends(current_user)):
    require_role(Role.auditor if request.url.path.startswith('/api/auditor/') else Role.employer_personnel,request,user)
    content=reporting.pdf_export(assessment_id,invitation_id,user.id)
    return Response(content,media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="assessment-{assessment_id}-session-{invitation_id}-audit.pdf"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})


@app.get('/api/candidate/exam/readiness')
def candidate_readiness(request:Request,response:Response,user:UserInfo=Depends(current_user)):
    exam=authorized_invitation(request,user)
    method=require_seb(request,'/exam-room',exam['seb_config_key'])
    client=ensure_exam_client(request,user,exam)
    response.headers['Cache-Control']='no-store'
    return readiness.requirements(exam['invitation_id'],user.id,client,current_exam_fingerprint(request),POLICY_VERSION,method)

@app.post('/api/candidate/exam/readiness')
def confirm_candidate_readiness(payload:readiness.Confirmation,request:Request,response:Response,user:UserInfo=Depends(current_user)):
    exam=authorized_invitation(request,user)
    method=require_seb(request,'/exam-room',exam['seb_config_key'])
    client=ensure_exam_client(request,user,exam)
    response.headers['Cache-Control']='no-store'
    return readiness.confirm(exam['invitation_id'],user.id,client,current_exam_fingerprint(request),POLICY_VERSION,method,payload)

@app.post('/api/candidate/exam/environment-events')
def candidate_environment_events(payload:readiness.EventBatch,request:Request,response:Response,user:UserInfo=Depends(current_user)):
    exam=authorized_invitation(request,user)
    require_seb(request,'/exam-room',exam['seb_config_key'])
    client=ensure_exam_client(request,user,exam)
    response.headers['Cache-Control']='no-store'
    return readiness.record_events(exam['invitation_id'],user.id,client,payload)


@app.get('/api/company/retention')
def get_company_retention(request:Request,response:Response,user:UserInfo=Depends(current_user)):
    require_role(Role.company_admin,request,user)
    response.headers['Cache-Control']='no-store'
    return retention.read(user.id)

@app.post('/api/company/retention')
def update_company_retention(payload:retention.Policy,request:Request,response:Response,user:UserInfo=Depends(current_user)):
    require_role(Role.company_admin,request,user)
    response.headers['Cache-Control']='no-store'
    return retention.save(user.id,payload)


@app.get('/api/company/retention/archives/{assessment_id}')
def retained_audit_metadata(assessment_id:int,request:Request,response:Response,user:UserInfo=Depends(current_user)):
    # Archived assessments no longer have a live assessment row; scope by archive company.
    if user.role!=Role.company_admin:raise HTTPException(403,'Access denied')
    response.headers['Cache-Control']='no-store'
    return retention.archive(user.id,assessment_id)
