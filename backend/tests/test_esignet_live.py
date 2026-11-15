"""Opt-in protocol acceptance test against the real local Compose services.

RUN_ESIGNET_LIVE=1 python -m unittest test_esignet_live -v
Uses disposable AssessBridge accounts/exams and the documented fictional IDs.
"""
from http.cookiejar import CookieJar
import json
import os
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs,urlsplit
from urllib.request import Request,build_opener,HTTPCookieProcessor,HTTPRedirectHandler

from app import esignet,config
from app.db import database
import test_exam_session_integration as fixtures


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args):
        return None


@unittest.skipUnless(os.getenv('RUN_ESIGNET_LIVE')=='1','Start the demo services and set RUN_ESIGNET_LIVE=1')
class EsignetLiveTests(unittest.TestCase):
    def test_all_seeded_identities_otp_consent_and_signed_return(self):
        seeds=json.loads((config.ROOT_DIR/'infra/esignet/identities.json').read_text())
        for seed in seeds:
            with self.subTest(name=seed['fullName']):
                self.authenticate(seed)

    def authenticate(self,seed):
        fixture=fixtures.ExamSessionIntegrationTests();fixture.setUp()
        try:
            with fixture.client() as client:
                with database() as db:
                    c=db.cursor();c.execute('UPDATE assessments SET identity_policy=%s WHERE id=%s',(esignet.POLICY,fixture.assessment_id))
                    c.execute('UPDATE assessment_invitations SET candidate_name=%s WHERE id=%s',(seed['fullName'],fixture.invitation_id))
                    c.execute('DELETE FROM assessment_identity_checks WHERE invitation_id=%s',(fixture.invitation_id,));db.commit()
                response=client.post('/api/candidate/identity/esignet/start')
                self.assertEqual(response.status_code,200,response.text)
                opener=build_opener(NoRedirect(),HTTPCookieProcessor(CookieJar()))
                try:
                    opener.open(response.json()['authorization_url'],timeout=15)
                    self.fail('The provider did not redirect to authentication')
                except HTTPError as error:
                    try:
                        self.assertIn(error.code,(302,303,307))
                        params=parse_qs(urlsplit(error.headers['Location']).query)
                    finally:
                        error.close()
                self.assertNotIn('error',params)
                execution=params['executionId'][0]
                def post(path,data):
                    with opener.open(Request(config.ESIGNET_ISSUER+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'}),timeout=20) as result:
                        return json.load(result)
                flow=post('/flow/execute',{'executionId':execution})
                seen=set()
                for _ in range(8):
                    self.assertNotEqual(flow.get('flowStatus'),'ERROR',flow.get('error'))
                    if flow.get('flowStatus')=='COMPLETE':
                        break
                    values={}
                    for field in flow.get('data',{}).get('inputs',[]):
                        identifier=field['identifier'];seen.add(identifier)
                        if identifier=='username':values[identifier]=seed['individualId']
                        elif identifier=='otp':values[identifier]='111111'
                        elif identifier=='consent_decisions':
                            values[identifier]=json.dumps({'approved':True,'purposes':[{'approved':True,'elements':[{'approved':True,'name':'name'}],'purposeName':'attributes:'+config.ESIGNET_CLIENT_ID}]})
                    if values:values['captcha_token']='assessbridge-fictional-identity-demo'
                    flow=post('/flow/execute',{'executionId':execution,'challengeToken':flow['challengeToken'],'action':flow['data']['actions'][0]['ref'],'inputs':values})
                self.assertTrue({'username','otp','consent_decisions'}<=seen)
                self.assertEqual(flow['flowStatus'],'COMPLETE')
                callback=post('/oauth2/auth/callback',{'authId':params['authId'][0],'assertion':flow['assertion']})
                returned=parse_qs(urlsplit(callback.get('redirect_uri') or callback.get('redirectUri')).query)
                self.assertNotIn('error',returned)
                self.assertIn('code',returned)
                response=client.get('/identity/esignet/callback',params={key:value[0] for key,value in returned.items()},follow_redirects=False)
                self.assertEqual(response.status_code,303,response.text)
                state=client.get('/api/candidate/identity').json()
                self.assertTrue(state['demo']['authenticated'],state['demo'].get('message'))
                self.assertEqual(state['demo']['name'],seed['fullName'])
                self.assertEqual(state['status'],'required')  # Session photo remains mandatory.
                self.assertEqual(client.post('/api/candidate/exam/start').status_code,403)
        finally:
            fixture.tearDown()
