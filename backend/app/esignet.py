"""An OIDC relying party for the explicitly enabled fictional eSignet provider.

No provider token, identity number, or OTP is accepted from browser assertions.
An exam-bound one-use server flow, signed tokens and PKCE establish the result.
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from fastapi import HTTPException
import jwt
from jwt import PyJWTError

from . import config
from .audit_times import iso
from .db import database
from .retention import for_invitation
from .esignet_keys import public_jwk

POLICY = 'Fayda demo (eSignet)'
METHOD = 'esignet_demo'


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enabled():
    return config.ESIGNET_DEMO_ENABLED


def require_enabled():
    if not enabled():
        raise HTTPException(503, 'Demo identity authentication is disabled')
    for value in (config.ESIGNET_ISSUER, config.ESIGNET_REDIRECT_URI):
        url = urlsplit(value)
        if url.scheme not in ('http','https') or not url.hostname or url.username or url.fragment or url.query:
            raise HTTPException(503, 'Demo identity authentication is not configured')
        if url.scheme == 'http' and url.hostname not in ('127.0.0.1','localhost','::1'):
            raise HTTPException(503, 'Demo identity authentication requires HTTPS')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def remote(url, form=None, token=None):
    # Discovery cannot redirect server requests to unrelated hosts or schemes.
    if urlsplit(url)[:2] != urlsplit(config.ESIGNET_ISSUER)[:2]:
        raise HTTPException(503, 'The identity provider returned an unexpected address')
    headers = {'Accept':'application/json, application/jwt'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if form is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
    request = Request(url, data=urlencode(form).encode() if form is not None else None, headers=headers)
    try:
        with build_opener(NoRedirect()).open(request, timeout=12) as response:
            raw = response.read(1024*1024+1)
            if len(raw) > 1024*1024:
                raise ValueError('Oversized response')
            if response.headers.get_content_type() == 'application/jwt':
                return raw.decode('ascii')
            return json.loads(raw)
    except (HTTPError, URLError, OSError, ValueError, UnicodeError) as exc:
        raise HTTPException(503, 'The demo identity service is unavailable. Try again.') from exc


def discovery():
    metadata = remote(config.ESIGNET_ISSUER + '/.well-known/openid-configuration')
    if not isinstance(metadata, dict) or metadata.get('issuer') != config.ESIGNET_ISSUER:
        raise HTTPException(503, 'The identity provider could not be verified')
    for key in ('authorization_endpoint','token_endpoint','userinfo_endpoint','jwks_uri'):
        value = metadata.get(key)
        if not isinstance(value, str) or urlsplit(value)[:2] != urlsplit(config.ESIGNET_ISSUER)[:2]:
            raise HTTPException(503, 'The identity provider returned an unexpected address')
    return metadata


def _exam(cursor, invitation, candidate, policy):
    cursor.execute('''SELECT i.candidate_name,a.identity_policy,a.starts_at,a.ends_at,
                 EXISTS(SELECT 1 FROM assessment_consents c WHERE c.invitation_id=i.id AND c.candidate_user_id=%s AND c.policy_version=%s) AS consented,
                 EXISTS(SELECT 1 FROM assessment_exam_sessions s WHERE s.invitation_id=i.id) AS started
                 FROM assessment_invitations i JOIN assessments a ON a.id=i.assessment_id
                 WHERE i.id=%s AND i.candidate_user_id=%s AND i.delivery_status='sent' FOR UPDATE''', (candidate,policy,invitation,candidate))
    row = cursor.fetchone()
    if not row or row['identity_policy'] != POLICY:
        raise HTTPException(403, 'This exam does not use demo identity authentication')
    if row['started']:
        raise HTTPException(409, 'The exam has already started')
    if not row['starts_at'] <= now() < row['ends_at']:
        raise HTTPException(403, 'The exam access window is closed')
    if not row['consented']:
        raise HTTPException(403, 'Agreement is required before identity authentication')
    return row


def begin(invitation, candidate, fingerprint, policy):
    require_enabled()
    metadata = discovery()
    state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    from .identity import _encrypt
    secret = _encrypt(json.dumps({'nonce':nonce,'verifier':verifier}).encode(), invitation, 'oidc-flow')
    with database() as db:
        c = db.cursor(dictionary=True)
        _exam(c,invitation,candidate,policy)
        c.execute("UPDATE assessment_identity_auth_flows SET status='failed',failure_reason='replaced' WHERE invitation_id=%s AND candidate_user_id=%s AND status IN ('pending','authenticated')", (invitation,candidate))
        c.execute('''INSERT INTO assessment_identity_auth_flows(invitation_id,candidate_user_id,access_fingerprint,state_hash,secret_ciphertext,created_at,expires_at)
                     VALUES(%s,%s,%s,%s,%s,%s,%s)''', (invitation,candidate,fingerprint,hashlib.sha256(state.encode()).hexdigest(),secret,now(),now()+timedelta(minutes=10)))
        db.commit()
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    params = {'client_id':config.ESIGNET_CLIENT_ID,'response_type':'code','redirect_uri':config.ESIGNET_REDIRECT_URI,
              'scope':'openid profile','state':state,'nonce':nonce,'code_challenge':challenge,'code_challenge_method':'S256',
              'acr_values':'mosip:idp:acr:generated-code','prompt':'login consent',
              'claims':json.dumps({'userinfo':{'name':{'essential':True}}},separators=(',',':'))}
    return {'authorization_url':metadata['authorization_endpoint']+'?'+urlencode(params)}


def decode_token(raw, keys, nonce=None, userinfo=False):
    try:
        header = jwt.get_unverified_header(raw)
        algorithms=['PS256','RS256'] if userinfo else ['PS256']
        if header.get('alg') not in algorithms:
            raise ValueError('Unexpected signature algorithm')
        candidates = [key for key in keys['keys'] if key.get('kty')=='RSA' and key.get('use','sig')=='sig' and (not header.get('kid') or key.get('kid')==header['kid'])]
        if len(candidates) != 1:
            raise ValueError('Unknown signing key')
        # OIDC signed UserInfo requires iss/aud/sub, but not exp/iat. It is
        # obtained only over the back channel using this fresh access token;
        # the ID token must always include and pass its expiry/issued-at checks.
        required=['sub','iss','aud'] if userinfo else ['exp','iat','sub','iss','aud']
        claims = jwt.decode(raw,jwt.PyJWK.from_dict(candidates[0]).key,algorithms=algorithms,audience=config.ESIGNET_CLIENT_ID,issuer=config.ESIGNET_ISSUER,
                            options={'require':required},leeway=15)
        if claims.get('azp',config.ESIGNET_CLIENT_ID) != config.ESIGNET_CLIENT_ID:
            raise ValueError('Wrong authorized client')
        if isinstance(claims.get('aud'),list) and len(claims['aud'])>1 and claims.get('azp')!=config.ESIGNET_CLIENT_ID:
            raise ValueError('Missing authorized client')
        if nonce is not None and (not isinstance(claims.get('nonce'),str) or not secrets.compare_digest(claims['nonce'],nonce)):
            raise ValueError('Wrong authentication nonce')
        if not isinstance(claims.get('sub'),str) or not 1 <= len(claims['sub']) <= 255:
            raise ValueError('Invalid identity subject')
        return claims
    except (PyJWTError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(403, 'The identity authentication response could not be verified. Try again.') from exc


def _name(value):
    return ' '.join(unicodedata.normalize('NFKC',value).casefold().split())


def finish(state, code, provider_error, invitation, candidate, fingerprint, policy):
    require_enabled()
    with database() as db:
        c = db.cursor(dictionary=True)
        exam = _exam(c,invitation,candidate,policy)
        c.execute('SELECT * FROM assessment_identity_auth_flows WHERE state_hash=%s FOR UPDATE',(hashlib.sha256(state.encode()).hexdigest(),))
        flow = c.fetchone()
        if not flow or flow['status']!='pending' or flow['expires_at']<=now() or flow['candidate_user_id']!=candidate or flow['invitation_id']!=invitation or flow['access_fingerprint']!=fingerprint:
            raise HTTPException(403, 'This identity authentication request has expired or was already used. Start again.')
        c.execute("UPDATE assessment_identity_auth_flows SET status='exchanging' WHERE id=%s",(flow['id'],));db.commit()
    try:
        if provider_error or not code:
            raise HTTPException(403,'Identity authentication was cancelled or declined. Try again.')
        from .identity import _identity_key
        blob = bytes(flow['secret_ciphertext'])
        secret = json.loads(AESGCM(_identity_key()).decrypt(blob[:12],blob[12:],f'identity:{invitation}:oidc-flow'.encode()))
        metadata = discovery()
        issued = int(datetime.now(timezone.utc).timestamp())
        try:
            key = config.ESIGNET_PRIVATE_KEY_FILE.read_text()
            private=load_pem_private_key(key.encode(),password=None)
        except (OSError,ValueError,TypeError) as exc:
            raise HTTPException(503,'Demo identity authentication is not configured') from exc
        # eSignet 2.0's FAPI client authentication requires the issuer identifier
        # as audience, rather than the token endpoint URL.
        assertion = jwt.encode({'iss':config.ESIGNET_CLIENT_ID,'sub':config.ESIGNET_CLIENT_ID,'aud':metadata['issuer'],
                                'iat':issued,'exp':issued+60,'jti':secrets.token_urlsafe(24)},private,algorithm='PS256',headers={'kid':public_jwk(private)['kid']})
        tokens = remote(metadata['token_endpoint'],{'grant_type':'authorization_code','code':code,'redirect_uri':config.ESIGNET_REDIRECT_URI,
                        'client_id':config.ESIGNET_CLIENT_ID,'code_verifier':secret['verifier'],
                        'client_assertion_type':'urn:ietf:params:oauth:client-assertion-type:jwt-bearer','client_assertion':assertion})
        if not isinstance(tokens,dict) or not isinstance(tokens.get('id_token'),str) or not isinstance(tokens.get('access_token'),str) or tokens.get('token_type','').lower()!='bearer':
            raise HTTPException(403,'The identity provider did not return a complete authentication result')
        keys = remote(metadata['jwks_uri'])
        claims = decode_token(tokens['id_token'],keys,secret['nonce'])
        if claims.get('at_hash'):
            expected = base64.urlsafe_b64encode(hashlib.sha256(tokens['access_token'].encode('ascii')).digest()[:16]).decode().rstrip('=')
            if not secrets.compare_digest(str(claims['at_hash']),expected):
                raise HTTPException(403,'The identity authentication response could not be verified')
        userinfo = remote(metadata['userinfo_endpoint'],token=tokens['access_token'])
        if isinstance(userinfo,str):
            # The mock registry signs its userinfo with RS256; eSignet publishes
            # that public key alongside its own PS256 signing key.
            userinfo = decode_token(userinfo,keys,userinfo=True)
        if not isinstance(userinfo,dict) or userinfo.get('sub')!=claims['sub']:
            raise HTTPException(403,'The identity authentication response could not be verified')
        name = userinfo.get('name')
        if not isinstance(name,str) or not 1 <= len(name.strip()) <= 160:
            raise HTTPException(403,'The identity provider did not share a name. Allow name sharing and try again.')
        seeds = json.loads((config.ROOT_DIR/'infra/esignet/identities.json').read_text())
        if _name(name) not in {_name(item['fullName']) for item in seeds} or _name(name)!=_name(exam['candidate_name']):
            raise HTTPException(403,'The authenticated test name does not match the invitation. Use the identity named in your invitation.')
        with database() as db:
            c = db.cursor(dictionary=True)
            current = _exam(c,invitation,candidate,policy)
            if _name(name)!=_name(current['candidate_name']):
                raise HTTPException(409,'The invitation changed. Start identity authentication again.')
            c.execute("UPDATE assessment_identity_auth_flows SET status='authenticated',authenticated_subject=%s,authenticated_name=%s,authenticated_at=%s WHERE id=%s AND status='exchanging' AND expires_at>%s",(claims['sub'],name.strip(),now(),flow['id'],now()))
            if c.rowcount!=1:
                raise HTTPException(403,'The identity authentication request expired. Try again.')
            db.commit()
    except Exception as exc:
        with database() as db:
            c=db.cursor();c.execute("UPDATE assessment_identity_auth_flows SET status='failed',failure_reason=%s WHERE id=%s",('name_mismatch' if isinstance(exc,HTTPException) and 'test name' in str(exc.detail) else 'authentication_failed',flow['id']));db.commit()
        raise


def state(invitation,candidate,fingerprint):
    with database() as db:
        c=db.cursor(dictionary=True)
        c.execute('SELECT status,authenticated_name,failure_reason,expires_at FROM assessment_identity_auth_flows WHERE invitation_id=%s AND candidate_user_id=%s AND access_fingerprint=%s ORDER BY id DESC LIMIT 1',(invitation,candidate,fingerprint))
        flow=c.fetchone()
    current = flow and flow['expires_at']>now()
    message = None
    if current and flow['status']=='failed':
        message = 'The authenticated test name does not match the invitation.' if flow['failure_reason']=='name_mismatch' else 'Authentication did not finish. Try again.'
    return {'enabled':enabled(),'authenticated':bool(current and flow['status']=='authenticated'),
            'name':flow['authenticated_name'] if current and flow['status']=='authenticated' else None,'message':message}


def complete(invitation,candidate,fingerprint,policy,selfie):
    require_enabled()
    from .identity import _decode_image,_model_lock,_face_models,_detect_one,_encrypt,_REASONS
    with database() as db:
        c=db.cursor(dictionary=True);_exam(c,invitation,candidate,policy)
    image,jpeg = _decode_image(selfie)
    with _model_lock:
        detector,_ = _face_models()
        _,failure = _detect_one(detector,image,'selfie')
    if failure:
        raise HTTPException(422,_REASONS.get(failure,'Capture a clear image with only your face visible'))
    with database() as db:
        c=db.cursor(dictionary=True);exam=_exam(c,invitation,candidate,policy)
        c.execute("SELECT * FROM assessment_identity_auth_flows WHERE invitation_id=%s AND candidate_user_id=%s AND access_fingerprint=%s ORDER BY id DESC LIMIT 1 FOR UPDATE",(invitation,candidate,fingerprint))
        flow=c.fetchone()
        if not flow or flow['status'] not in ('authenticated','completed') or flow['expires_at']<=now() or _name(flow['authenticated_name'])!=_name(exam['candidate_name']):
            raise HTTPException(403,'Authenticate your test identity before capturing the session photo')
        c.execute('SELECT * FROM assessment_identity_checks WHERE invitation_id=%s FOR UPDATE',(invitation,));previous=c.fetchone()
        if previous and previous['candidate_user_id']!=candidate:
            raise HTTPException(403,'Identity check belongs to another candidate account')
        if flow['status']=='completed':
            if previous and previous['access_fingerprint']==fingerprint and previous['status']=='passed' and previous['evidence_expires_at']>now():
                return {'status':'passed','verification_method':METHOD}
            raise HTTPException(403,'Authenticate your test identity again')
        checked=now().replace(microsecond=0);expires=checked+timedelta(days=for_invitation(c,invitation)['identity_days'])
        attempt=(previous['attempts'] if previous else 0)+1
        encrypted=_encrypt(jpeg.tobytes(),invitation,'selfie')
        c.execute('''INSERT INTO assessment_identity_checks(invitation_id,candidate_user_id,access_fingerprint,status,stage,checked_at,evidence_expires_at,selfie_ciphertext,verification_method)
                     VALUES(%s,%s,%s,'passed','demo_complete',%s,%s,%s,%s)
                     ON DUPLICATE KEY UPDATE access_fingerprint=VALUES(access_fingerprint),status='passed',stage='demo_complete',failure_reason=NULL,
                     card_detection_method=NULL,name_readability=NULL,name_similarity=NULL,face_similarity=NULL,checked_at=VALUES(checked_at),
                     evidence_expires_at=VALUES(evidence_expires_at),card_ciphertext=NULL,selfie_ciphertext=VALUES(selfie_ciphertext),verification_method=VALUES(verification_method),attempts=attempts+1''',
                     (invitation,candidate,fingerprint,checked,expires,encrypted,METHOD))
        c.execute("INSERT INTO assessment_identity_attempts(invitation_id,candidate_user_id,attempt_number,status,stage,checked_at,verification_method) VALUES(%s,%s,%s,'passed','demo_complete',%s,%s)",(invitation,candidate,attempt,checked,METHOD))
        c.execute("UPDATE assessment_identity_auth_flows SET status='completed' WHERE id=%s",(flow['id'],))
        db.commit()
    return {'status':'passed','verification_method':METHOD,'checked_at':iso(checked)}


def purge():
    with database() as db:
        c=db.cursor();c.execute('DELETE FROM assessment_identity_auth_flows WHERE expires_at<=UTC_TIMESTAMP()');db.commit()
