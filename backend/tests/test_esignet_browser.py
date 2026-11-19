import unittest
import test_candidate_feedback_browser as candidate


class EsignetBrowserTests(unittest.TestCase):
    open_page=candidate.CandidateFeedbackBrowserTests.open_page
    check=candidate.CandidateFeedbackBrowserTests.check
    open=candidate.CandidateFeedbackBrowserTests.open

    @classmethod
    def setUpClass(cls):
        candidate.CandidateFeedbackBrowserTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        candidate.CandidateFeedbackBrowserTests.tearDownClass.__func__(cls)

    def test_demo_photo_flow_and_local_errors_at_narrow_scrolled_position(self):
        self.open()
        self.browser.command('Emulation.setDeviceMetricsOverride',{'width':390,'height':844,'deviceScaleFactor':1,'mobile':False})
        self.check('''
configureIdentity();showPanel('identity-panel');
assert(!el('identity-card-capture'),'card capture was removed');
assert(el('identity-auth-heading').textContent==='eSignet authentication (Fayda national Id simulation)','identity method labelled');
assert(el('identity-photo-content').hidden,'session photo stays locked before authentication');
assert(el('capture-selfie').disabled,'authenticate before session photo');
routes['/api/candidate/identity/esignet/start']={status:503,body:{detail:'The demo identity service is unavailable. Try again.'}};
el('authenticate-demo').scrollIntoView();el('authenticate-demo').click();
await until(()=>el('demo-authentication-error').textContent.includes('Identity authentication is unavailable'));
assert(el('demo-authentication-error').querySelector('.ab-feedback'),'shared local error');
assert(!el('authenticate-demo').disabled,'retry available');
assert(document.documentElement.scrollWidth<=innerWidth+2,'no narrow overflow');
routes['/api/candidate/exam/session']={status:200,body:{state:'not_started'}};
routes['/api/candidate/exam-room']={status:200,body:{consented:true,identity_policy:'Fayda demo (eSignet)'}};
routes['/api/candidate/identity']={status:200,body:{status:'required',identity_policy:'Fayda demo (eSignet)',demo:{authenticated:true,name:'Daniel Bekele'}}};
await prepareIdentity();assert(!el('capture-selfie').disabled,'authenticated tester can capture photo');
assert(el('authenticate-demo').hidden,'successful authentication is not offered again');
assert(!el('identity-photo-content').hidden&&el('identity-auth-status').textContent==='Authenticated','session photo unlocked after authentication');
assert(el('demo-authentication-state').textContent.includes('Daniel Bekele'),'authenticated name shown');
capturedSelfie='TEST_PHOTO';el('verify-identity').disabled=false;
routes['/api/candidate/identity/esignet/complete']={status:200,body:{status:'passed',verification_method:'esignet_demo'}};
let advanced=false;window.examFlow.openAfterDevices=async()=>{advanced=true};
el('verify-identity').click();await until(()=>advanced);
const sent=requests.find(r=>r.path.endsWith('/esignet/complete'));
assert(JSON.stringify(JSON.parse(sent.body))===JSON.stringify({selfie:'TEST_PHOTO'}),'only photo submitted, no fake card');
assert(!el('identity-card-capture'),'no alternate card-verification flow');
''')

    def test_authenticated_return_restores_devices_and_opens_photo_without_another_otp(self):
        self.open()
        self.check('''
routes['/api/candidate/exam-room']={status:200,body:{consented:true,identity_policy:'Fayda demo (eSignet)'}};
routes['/api/candidate/exam/session']={status:200,body:{state:'not_started'}};
routes['/api/candidate/identity']={status:200,body:{status:'required',identity_policy:'Fayda demo (eSignet)',demo:{authenticated:true,name:'Daniel Bekele'}}};
window.examRecovery.readiness=async()=>true;
let live=true;const track=()=>({readyState:'live',get muted(){return !live},addEventListener(){},stop(){}});
const video=track(),audio=track();const stream={getVideoTracks:()=>[video],getAudioTracks:()=>[audio],getTracks:()=>[video,audio]};
mediaReady=()=>!!mediaStream&&live;
beginMicrophoneMeter=()=>{};
navigator.mediaDevices.getUserMedia=async()=>stream;
// Supply controllable track state to exercise continuation and device loss.
grantDevices=async()=>{mediaStream=stream;syncMediaState()};
showPanel('device-panel');await restoreAuthenticatedIdentity({identity_policy:'Fayda demo (eSignet)'});
await until(()=>!el('identity-panel').hidden&&!deviceContinuationInProgress);
assert(!el('identity-photo-content').hidden,'return advances directly to session photo');
assert(el('authenticate-demo').hidden,'no repeated OTP action');
assert(!el('capture-selfie').disabled,'camera capture is the next action');
assert(!resumeAuthenticatedIdentity,'automatic continuation is consumed once');
assert(!requests.some(item=>item.path.endsWith('/esignet/start')),'no second authentication request');
live=false;syncMediaState();assert(!el('device-panel').hidden,'genuine device loss still blocks capture');
live=true;syncMediaState();assert(!el('device-panel').hidden,'ordinary device recovery requires continue');
el('continue-devices').click();await until(()=>!el('identity-panel').hidden);
assert(el('authenticate-demo').hidden,'device recovery retains verified authentication');
''')
