"""OIDC signature, replay, account/exam binding, and live reference integration."""
import base64
from datetime import datetime, timedelta, timezone
import json
from urllib.parse import parse_qs, urlsplit
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import jwt

from app import config, esignet
from app.db import database
from app.identity import _identity_key
from app.main import POLICY_VERSION
import test_exam_session_integration as fixtures
from setup_esignet import public_jwk


class EsignetIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signer = rsa.generate_private_key(public_exponent=65537,key_size=2048)
        cls.pem = cls.signer.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
        cls.key = public_jwk(cls.signer)

    def setUp(self):
        self.feature = patch.object(config,'ESIGNET_DEMO_ENABLED',True)
        self.feature.start();self.addCleanup(self.feature.stop)
        temporary=tempfile.TemporaryDirectory(prefix='esignet-key-');self.addCleanup(temporary.cleanup)
        private_path=Path(temporary.name)/'client.pem';private_path.write_bytes(self.pem)
        private_patch=patch.object(config,'ESIGNET_PRIVATE_KEY_FILE',private_path)
        private_patch.start();self.addCleanup(private_patch.stop)
        self.f = fixtures.ExamSessionIntegrationTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        self.client = self.f.client();self.addCleanup(self.client.close)
        with database() as db:
            c=db.cursor();c.execute('DELETE FROM assessment_identity_checks WHERE invitation_id=%s',(self.f.invitation_id,))
            c.execute('UPDATE assessments SET identity_policy=%s WHERE id=%s',(esignet.POLICY,self.f.assessment_id))
            c.execute("UPDATE assessment_invitations SET candidate_name='Daniel Bekele' WHERE id=%s",(self.f.invitation_id,));db.commit()
        self.metadata = {'issuer':config.ESIGNET_ISSUER,**{key:config.ESIGNET_ISSUER+path for key,path in {
            'authorization_endpoint':'/oauth2/authorize','token_endpoint':'/oauth2/token','userinfo_endpoint':'/oauth2/userinfo','jwks_uri':'/oauth2/jwks'}.items()}}

    def begin(self):
        with patch.object(esignet,'remote',return_value=self.metadata):
            response=self.client.post('/api/candidate/identity/esignet/start')
        self.assertEqual(response.status_code,200,response.text)
        params=parse_qs(urlsplit(response.json()['authorization_url']).query)
        self.assertEqual(params['code_challenge_method'],['S256'])
        with database() as db:
            c=db.cursor(dictionary=True);c.execute('SELECT * FROM assessment_identity_auth_flows WHERE invitation_id=%s ORDER BY id DESC LIMIT 1',(self.f.invitation_id,));flow=c.fetchone()
        blob=bytes(flow['secret_ciphertext'])
        secret=json.loads(AESGCM(_identity_key()).decrypt(blob[:12],blob[12:],f'identity:{self.f.invitation_id}:oidc-flow'.encode()))
        self.assertEqual(secret['nonce'],params['nonce'][0])
        return params, flow

    def callback(self, params, name='Daniel Bekele', changes=None, key=None):
        timestamp=int(datetime.now(timezone.utc).timestamp())
        claims={'iss':config.ESIGNET_ISSUER,'aud':config.ESIGNET_CLIENT_ID,'sub':'fictional-subject',
                'nonce':params['nonce'][0],'iat':timestamp,'exp':timestamp+120,**(changes or {})}
        signed=jwt.encode(claims,key or self.pem,algorithm='PS256',headers={'kid':self.key['kid']})
        responses=[self.metadata,{'id_token':signed,'access_token':'TEST_ACCESS','token_type':'Bearer'},
                   {'keys':[self.key]},{'sub':'fictional-subject','name':name}]
        with patch.object(esignet,'remote',side_effect=responses) as remote:
            response=self.client.get('/identity/esignet/callback',params={'state':params['state'][0],'code':'TEST_CODE'},follow_redirects=False)
        assertion=remote.call_args_list[1].args[1]['client_assertion']
        client_claims=jwt.decode(assertion,self.signer.public_key(),algorithms=['PS256'],audience=config.ESIGNET_ISSUER,issuer=config.ESIGNET_CLIENT_ID)
        self.assertEqual(client_claims['sub'],config.ESIGNET_CLIENT_ID)
        self.assertEqual(jwt.get_unverified_header(assertion)['kid'],self.key['kid'])
        return response

    def state(self):
        response=self.client.get('/api/candidate/identity')
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def test_authentication_camera_reference_start_and_review(self):
        self.assertFalse(self.state()['demo']['authenticated'])
        self.assertEqual(self.client.post('/api/candidate/exam/start').status_code,403)
        params,flow=self.begin()
        self.assertEqual(self.callback(params).status_code,303)
        self.assertTrue(self.state()['demo']['authenticated'])
        self.assertEqual(self.state()['status'],'required')  # A session photo is still mandatory.
        with patch('app.identity._decode_image',return_value=(np.zeros((480,640,3),np.uint8),np.frombuffer(b'TEST_SESSION_PHOTO',dtype=np.uint8))), \
             patch('app.identity._face_models',return_value=(object(),object())), \
             patch('app.identity._detect_one',return_value=(object(),None)):
            response=self.client.post('/api/candidate/identity/esignet/complete',json={'selfie':base64.b64encode(b'test').decode()})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.state()['verification_method'],'esignet_demo')
        self.f.confirm_readiness(self.client)
        started=self.client.post('/api/candidate/exam/start');self.assertEqual(started.status_code,200,started.text)
        with database() as db:
            c=db.cursor(dictionary=True);c.execute('SELECT card_ciphertext,selfie_ciphertext,name_similarity,face_similarity FROM assessment_identity_checks WHERE invitation_id=%s',(self.f.invitation_id,));check=c.fetchone()
            self.assertIsNone(check['card_ciphertext']);self.assertIsNone(check['name_similarity']);self.assertIsNone(check['face_similarity'])
            c.execute('SELECT reference_ciphertext FROM assessment_monitoring_runs WHERE session_id=%s',(started.json()['session_id'],));reference=c.fetchone()['reference_ciphertext']
            self.assertEqual(reference,check['selfie_ciphertext'])
        for _ in range(2):
            self.assertEqual(self.client.post('/api/candidate/exam/navigate',json={'direction':'next'}).status_code,200)
        self.assertEqual(self.client.post('/api/candidate/exam/submit').status_code,200)
        self.assertEqual(self.client.post('/api/candidate/exam/recording/finish').status_code,200)
        login=self.client.post('/api/auth/login',json={'username':self.f.employer_username,'password':self.f.password});self.assertEqual(login.status_code,200)
        report=self.client.get(f'/api/employer/assessments/{self.f.assessment_id}/invitations/{self.f.invitation_id}/review')
        self.assertEqual(report.status_code,200,report.text)
        self.assertEqual(report.json()['identity']['verification_method'],'esignet_demo')

    def test_wrong_name_invalid_nonce_signature_and_audience_fail(self):
        bad_key=rsa.generate_private_key(public_exponent=65537,key_size=2048).private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
        for kwargs in ({'name':'Samuel Mensah'},{'changes':{'nonce':'wrong'}},{'changes':{'aud':'another-client'}},{'key':bad_key},
                       {'changes':{'exp':int((datetime.now(timezone.utc)-timedelta(minutes=2)).timestamp())}}):
            params,_=self.begin();self.callback(params,**kwargs)
            self.assertFalse(self.state()['demo']['authenticated'])
            self.assertNotEqual(self.state()['status'],'passed')

    def test_replay_cancellation_expiry_missing_seb_and_feature_disabled(self):
        params,_=self.begin();self.assertEqual(self.callback(params).status_code,303)
        with patch.object(esignet,'remote') as remote:
            self.client.get('/identity/esignet/callback',params={'state':params['state'][0],'code':'REPLAY'},follow_redirects=False)
            remote.assert_not_called()
        params,_=self.begin()
        response=self.client.get('/identity/esignet/callback',params={'state':params['state'][0],'error':'access_denied'},follow_redirects=False)
        self.assertEqual(response.status_code,303);self.assertFalse(self.state()['demo']['authenticated'])
        params,_=self.begin()
        with database() as db:
            c=db.cursor();c.execute('UPDATE assessment_identity_auth_flows SET expires_at=UTC_TIMESTAMP()-INTERVAL 1 SECOND WHERE invitation_id=%s',(self.f.invitation_id,));db.commit()
        with patch.object(esignet,'remote') as remote:
            self.client.get('/identity/esignet/callback',params={'state':params['state'][0],'code':'EXPIRED'},follow_redirects=False);remote.assert_not_called()
        headers=self.client.headers.pop('X-AssessBridge-SEB-ConfigKeyHash')
        self.assertEqual(self.client.post('/api/candidate/identity/esignet/start').status_code,403)
        self.client.headers['X-AssessBridge-SEB-ConfigKeyHash']=headers
        with patch.object(config,'ESIGNET_DEMO_ENABLED',False):
            self.assertEqual(self.client.post('/api/candidate/identity/esignet/start').status_code,503)

    def test_removed_card_route_and_unverified_photo_cannot_bypass_authentication(self):
        self.assertEqual(self.client.post('/api/candidate/identity/verify',json={'card':'YQ==','selfie':'YQ=='}).status_code,404)
        with patch('app.identity._decode_image',return_value=(np.zeros((480,640,3),np.uint8),np.frombuffer(b'test',dtype=np.uint8))), \
             patch('app.identity._face_models',return_value=(object(),object())), \
             patch('app.identity._detect_one',return_value=(object(),None)):
            self.assertEqual(self.client.post('/api/candidate/identity/esignet/complete',json={'selfie':'YQ=='}).status_code,403)
        with patch.object(esignet,'remote',return_value={**self.metadata,'token_endpoint':'http://other.invalid/token'}):
            self.assertEqual(self.client.post('/api/candidate/identity/esignet/start').status_code,503)

    def test_flow_is_bound_to_account_and_exam_entry(self):
        params,_=self.begin()
        with database() as db:
            c=db.cursor();c.execute("UPDATE assessment_identity_auth_flows SET access_fingerprint=%s WHERE invitation_id=%s",('0'*64,self.f.invitation_id));db.commit()
        with patch.object(esignet,'remote') as remote:
            self.callback_without_remote(params)
            remote.assert_not_called()
        self.assertFalse(self.state()['demo']['authenticated'])

    def test_signed_userinfo_without_expiry_is_valid_but_id_token_requires_expiry(self):
        timestamp=int(datetime.now(timezone.utc).timestamp())
        claims={'iss':config.ESIGNET_ISSUER,'aud':config.ESIGNET_CLIENT_ID,'sub':'fictional-subject','name':'Daniel Bekele'}
        userinfo_key={**self.key,'alg':'RS256'}
        signed=jwt.encode(claims,self.pem,algorithm='RS256',headers={'kid':self.key['kid']})
        self.assertEqual(esignet.decode_token(signed,{'keys':[userinfo_key]},userinfo=True)['name'],'Daniel Bekele')
        with self.assertRaises(Exception):
            esignet.decode_token(signed,{'keys':[userinfo_key]})
        missing=jwt.encode({**claims,'iat':timestamp},self.pem,algorithm='PS256',headers={'kid':self.key['kid']})
        with self.assertRaises(Exception):
            esignet.decode_token(missing,{'keys':[self.key]})

    def callback_without_remote(self,params):
        return self.client.get('/identity/esignet/callback',params={'state':params['state'][0],'code':'WRONG_ENTRY'},follow_redirects=False)
