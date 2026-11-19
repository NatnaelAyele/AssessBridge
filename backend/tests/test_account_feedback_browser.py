"""Account-page integration in Chrome with controlled API replies; never sends email."""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import shutil
import tempfile
import threading
import time
import unittest
from urllib.parse import urlsplit

from chrome_probe import ChromeProbe, find_chrome

ROOT = Path(__file__).resolve().parents[2]


class AccountHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        aliases = {'/': '/index.html', '/register': '/register.html',
                   '/company-register': '/company-access.html', '/password-reset': '/company-access.html',
                   '/verify-email': '/verify-email.html', '/exam-entry': '/exam-entry.html'}
        if path in ('/candidate', '/employer', '/auditor'):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'<html><body>Signed in</body></html>')
            return
        self.path = aliases.get(path, path)
        super().do_GET()

    def log_message(self, *_args):
        pass


class AccountFeedbackBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not find_chrome():
            raise unittest.SkipTest('Install Chrome/Chromium or set CHROME_BIN to run browser tests')
        cls.temporary = tempfile.TemporaryDirectory(prefix='assessbridge-accounts-')
        directory = Path(cls.temporary.name)
        public = directory/'public'
        public.mkdir()
        shutil.copytree(ROOT/'frontend/css', public/'css')
        (public/'js').mkdir()
        for script in ('password-fields', 'company', 'ui-messages', 'account-feedback', 'login', 'register', 'verify-email', 'exam-entry', 'seb-client'):
            shutil.copyfile(ROOT/f'frontend/js/{script}.js', public/f'js/{script}.js')
        for page in ('index', 'register', 'verify-email', 'exam-entry', 'company-access'):
            shutil.copyfile(ROOT/f'frontend/{page}.html', public/f'{page}.html')
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(AccountHandler, directory=str(public)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.browser = ChromeProbe(directory/'profile')
        cls.browser.command('Page.enable')
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
        cls.initializer = None
        cls.run_id = 0

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.temporary.cleanup()

    def open(self, path, routes=None):
        if self.initializer:
            self.browser.command('Page.removeScriptToEvaluateOnNewDocument', {'identifier': self.initializer})
        self.run_id += 1
        source = '''
window.testRun=RUN_ID;window.errors=[];window.requests=[];window.routes=ROUTES;
addEventListener('error',e=>errors.push(e.message));
addEventListener('unhandledrejection',e=>errors.push(String(e.reason)));
window.wait=ms=>new Promise(r=>setTimeout(r,ms));
window.assert=(value,label)=>{if(!value)throw Error(label)};
window.el=id=>document.getElementById(id);
window.fill=(id,value)=>{el(id).value=value;el(id).dispatchEvent(new Event('input',{bubbles:true}))};
window.until=async test=>{for(let n=0;n<200;n++){if(test())return;await wait(10)}throw Error('Timed out: '+test)};
window.submit=async id=>{el(id).requestSubmit();await wait(30);await until(()=>!el(id).querySelector('[type="submit"]').disabled)};
window.fetch=async(url,options={})=>{
 const path=new URL(url,location.href).pathname;
 requests.push({path,...options});
 let spec=routes[path]||{status:path==='/api/auth/me'?401:503};
 if(Array.isArray(spec)) spec=spec.length>1?spec.shift():spec[0];
 if(spec.delay)await wait(spec.delay);
 if(spec.network)throw Error('PRIVATE transport exception');
 return new Response(spec.status===204?null:JSON.stringify(spec.body||{}),{status:spec.status||200,headers:{'Content-Type':'application/json'}});
};
'''.replace('RUN_ID', str(self.run_id)).replace('ROUTES', json.dumps(routes or {}))
        self.initializer = self.browser.command('Page.addScriptToEvaluateOnNewDocument', {'source': source})['identifier']
        base, mark, fragment = path.partition('#')
        base += ('&' if '?' in base else '?')+'testRun='+str(self.run_id)
        self.browser.command('Page.navigate', {'url': self.url+base+(mark+fragment if mark else '')})
        self.browser.evaluate(f"new Promise((resolve,reject)=>{{let n=0;const t=setInterval(()=>{{if(window.testRun==={self.run_id}&&document.readyState==='complete'){{clearInterval(t);resolve(true)}}else if(n++>300){{clearInterval(t);reject(Error('Page did not load'))}} }} ,10)}})")
        self.browser.evaluate('wait(30)')

    def check(self, script):
        self.assertTrue(self.browser.evaluate('(async()=>{'+script+';assert(!errors.length,errors.join("; "));return true})()'))

    def test_password_controls_across_account_flows(self):
        for page, count in (('/', 1), ('/register', 3), ('/exam-entry', 2),
                            ('/company-register', 4), ('/password-reset#reset=test-token', 4)):
            self.open(page)
            self.assertEqual(self.browser.evaluate('document.querySelectorAll(".ab-password-toggle").length'), count)
            self.check('''
let submissions=0;
document.addEventListener('submit',e=>{submissions++;e.preventDefault()},true);
for(const input of document.querySelectorAll('[data-password-input]')){
 const button=input.parentElement.querySelector('.ab-password-toggle');
 input.value='Example-password';
 assert(button.type==='button'&&button.getAttribute('aria-controls')===input.id,'safe associated button');
 button.click();
 assert(input.type==='text'&&input.value==='Example-password','revealed without changing value');
 assert(button.getAttribute('aria-label')==='Hide password'&&button.getAttribute('aria-pressed')==='true','accessible visible state');
 button.click();assert(input.type==='password','concealed');
}
assert(submissions===0,'visibility does not submit');
''')
            for width in (390, 1280):
                self.browser.command('Emulation.setDeviceMetricsOverride', {'width': width, 'height': 900, 'deviceScaleFactor': 1, 'mobile': False})
                self.check('''
for(const button of document.querySelectorAll('.ab-password-toggle')){
 if(!button.getClientRects().length)continue;
 const box=button.getBoundingClientRect(),input=button.parentElement.querySelector('input').getBoundingClientRect();
 assert(box.width>=44&&box.height>=44,'touch target');
 assert(box.left>=input.left&&box.right<=input.right+1,'eye stays inside field');
}
assert(document.documentElement.scrollWidth<=innerWidth+1,'no page overflow');
''')
        self.check('''
const resetForm=el('accept'), resetButton=resetForm.querySelector('button:not([type="button"])');
const resetPassword=resetForm.elements.password,resetConfirmation=resetForm.elements.confirmation;
resetPassword.value='x'.repeat(73);resetConfirmation.value=resetPassword.value;
resetPassword.parentElement.querySelector('button').click();
resetForm.requestSubmit();await wait(30);
assert(resetPassword.getAttribute('aria-invalid')==='true','revealing cannot bypass password byte limit');
assert(resetButton.textContent==='Save password','submit button remains intact');
assert(!requests.some(r=>r.path==='/api/auth/password/reset'),'invalid reset not sent');
resetPassword.value='Valid-password';resetConfirmation.value=resetPassword.value;
routes['/api/auth/password/reset']={status:200,body:{message:'Password reset.'}};
resetForm.requestSubmit();await wait(50);
assert(requests.some(r=>r.path==='/api/auth/password/reset'),'reset submits using the real submit button');
assert(resetPassword.parentElement.querySelector('button').querySelector('svg'),'eye icon not replaced by submit progress');
const field=el('accept').elements.password;
field.value='temporary-value';field.parentElement.querySelector('button').click();
el('accept').reset();assert(field.type==='password','reset conceals password');
const extra=document.createElement('input');extra.type='password';document.body.append(extra);
await wait(30);assert(extra.parentElement.querySelector('.ab-password-toggle'),'inserted field enhanced');
extra.disabled=true;await wait(30);assert(extra.parentElement.querySelector('button').disabled,'disabled field respected');
''')

    def test_sign_in_fields_persistent_errors_and_safe_retry(self):
        self.open('/', {'/api/auth/login': {'status': 401}})
        self.check('''
await submit('login-form');
assert(document.activeElement.id==='username','first invalid field focused');
assert(el('username').getAttribute('aria-invalid')==='true','invalid associated');
assert(document.querySelectorAll('.ab-field-error').length===2,'both missing fields');
fill('username','candidate@example.com');fill('password','password-value');
assert(!document.querySelector('.ab-field-error'),'valid fields clear errors');
await submit('login-form');
assert(el('form-message').textContent.includes('incorrect'),'credential error');
assert(el('form-message').textContent.includes('verify your email'),'verification next step');
fill('password','another-password');await wait(4100);
assert(el('form-message').textContent.includes('incorrect'),'editing and timer retain error');
assert(el('username').value==='candidate@example.com'&&el('password').value==='another-password','failed login retains input');
routes['/api/auth/login']={network:true};await submit('login-form');
assert(el('form-message').textContent.includes('Could not reach'),'network message');
assert(!document.body.textContent.includes('PRIVATE'),'no exception exposed');
assert(!document.querySelector('.ab-feedback--temporary'),'no sign-in toast');
''')

    def test_registration_validation_conflict_and_success(self):
        self.open('/register?email=invited%40example.com', {'/api/auth/register-candidate': {'status': 409}})
        self.check('''
assert(el('email').value==='invited@example.com','invitation email prefilled');
await submit('register-form');assert(document.activeElement.id==='password','password focused');
fill('password','abcdefghijkl');fill('confirm-password','different');
await submit('register-form');assert(document.activeElement.id==='confirm-password','confirmation focused');
fill('confirm-password','abcdefghijkl');await submit('register-form');
assert(el('register-message').textContent.includes('already has an account'),'conflict clear');
assert(el('register-message').querySelector('a').getAttribute('href')==='/','sign in action');
fill('password','x'.repeat(73));fill('confirm-password','x'.repeat(73));await submit('register-form');
assert(el('password').getAttribute('aria-invalid')==='true','byte limit enforced');
fill('password','abcdefghijkl');fill('confirm-password','abcdefghijkl');
routes['/api/auth/register-candidate']={status:201};await submit('register-form');
assert(el('register-form').hidden&&!el('registered-panel').hidden,'success panel');
assert(el('registered-panel').querySelector('.ab-feedback[data-tone=success]'),'green persistent outcome');
assert(el('registered-panel').textContent.includes('invited@example.com'),'destination email clear');
assert(el('registered-panel').querySelector('a').getAttribute('href')==='/','next sign-in action');
assert(!el('password').value&&!el('confirm-password').value,'passwords cleared after success');
assert(!el('register-message').textContent,'previous error removed');
''')

    def test_sign_in_redirects_for_all_roles(self):
        for role, destination in (('candidate', '/candidate'), ('employer_personnel', '/employer'), ('auditor', '/auditor')):
            self.open('/', {'/api/auth/login': {'status': 200, 'body': {'role': role, 'username': 'test-user'}}})
            self.browser.evaluate("fill('username','test-user');fill('password','test-password');el('login-form').requestSubmit();true")
            deadline = time.monotonic()+3
            while time.monotonic() < deadline:
                if self.browser.evaluate('location.pathname') == destination:
                    break
                time.sleep(.02)
            self.assertEqual(self.browser.evaluate('location.pathname'), destination)

    def test_resend_validation_rate_limit_and_hidden_scope(self):
        self.open('/register', {'/api/auth/resend-verification': {'status': 429}})
        self.check('''
el('show-resend').click();await submit('resend-form');
assert(document.activeElement.id==='resend-email','resend field focus');
fill('resend-email','wrong');fill('resend-password','password');await submit('resend-form');
assert(el('resend-email').getAttribute('aria-invalid')==='true','email validated');
fill('resend-email','candidate@example.com');await submit('resend-form');
assert(el('resend-message').textContent.includes('one minute'),'rate limit next step');
routes['/api/auth/resend-verification']={status:200};await submit('resend-form');
assert(el('resend-message').querySelector('.ab-feedback[data-tone=success]'),'persistent sent notice');
assert(!el('resend-password').value,'clears sent password');
routes['/api/auth/resend-verification']={status:503,delay:150};fill('resend-password','password');
el('resend-form').requestSubmit();el('show-resend').click();el('show-resend').click();await wait(200);
assert(!el('resend-message').textContent,'retired resend response ignored');
''')

    def test_verification_missing_expired_retry_and_success(self):
        self.open('/verify-email')
        self.check("assert(el('verification-message').textContent.includes('incomplete'),'missing token');assert(!requests.length,'no empty-token request');")
        self.open('/verify-email#token='+'a'*40, {'/api/auth/verify-email': {'status': 400, 'body': {'detail': '<private error>'}}})
        self.check("assert(!location.hash,'token removed from address');assert(el('verification-message').textContent.includes('invalid or expired'),'expired clear');assert(!document.body.textContent.includes('private'),'raw detail hidden');")
        self.open('/verify-email#token='+'b'*40, {'/api/auth/verify-email': [{'status': 503}, {'status': 200}]})
        self.check('''
el('verification-message').querySelector('button').click();await wait(60);
assert(el('verification-message').querySelector('.ab-feedback[data-tone=success]'),'retry success');
assert(el('verification-message').querySelector('a').getAttribute('href')==='/','sign in next');
assert(JSON.parse(requests[1].body).token==='b'.repeat(40),'retry retains original token');
''')

    def test_exam_entry_permissions_binding_and_result(self):
        self.open('/exam-entry?exam=abcde', {
            '/api/auth/login': {'status': 200, 'body': {'role': 'candidate', 'username': 'candidate@example.com'}},
            '/api/candidate/exam-entry': {'status': 403, 'body': {'detail': 'Open this assessment with the approved Safe Exam Browser configuration'}},
        })
        self.check('''
await submit('account-form');assert(document.activeElement.id==='account-username','account focus');
fill('account-username','candidate@example.com');fill('account-password','secret');await submit('account-form');
assert(el('account-form').hidden&&!el('exam-step').hidden,'account advances');
assert(!el('account-password').value&&!el('exam-username').value,'no invitation username autofill');
await submit('exam-form');assert(document.activeElement.id==='exam-username','exam focus');
fill('exam-username','abcde');fill('exam-password','abcdefgh');
window.SafeExamBrowser={security:{configKey:'a'.repeat(64)}};
await submit('exam-form');assert(el('entry-message').textContent.includes('SEB link'),'SEB next action');
const request=requests.find(r=>r.path==='/api/candidate/exam-entry');
assert(request.headers['X-AssessBridge-SEB-ConfigKeyHash']==='a'.repeat(64),'SEB proof retained');
routes['/api/candidate/exam-entry']={status:403,body:{detail:'This invitation is addressed to a different candidate email'}};
await submit('exam-form');assert(el('entry-message').textContent.includes('different email'),'binding failure clear');
assert(el('exam-password').value==='abcdefgh','failed entry retains password');
routes['/api/candidate/exam-entry']={status:200,body:{title:'Example exam',starts_at:'2026-11-12T00:00:00Z',ends_at:'2026-11-12T20:00:00Z',duration_minutes:60,window:'open'}};
await submit('exam-form');assert(el('exam-step').hidden&&!el('exam-result').hidden,'only result shown');
assert(!el('exam-password').value&&!el('entry-message').textContent,'password and error cleared');
assert(el('exam-status').querySelector('.ab-feedback[data-tone=success]'),'ready green');
routes['/api/auth/me']={status:200,body:{role:'candidate',username:'candidate@example.com'}};
el('try-another-exam').click();await wait(60);
assert(!el('exam-step').hidden&&el('exam-result').hidden&&!el('exam-username').value,'return to empty credentials');
assert(!el('exam-status').textContent,'old outcome cleared');
''')

    def test_exam_entry_stale_request_cannot_change_account_step(self):
        self.open('/exam-entry', {
            '/api/auth/me': {'status': 200, 'body': {'role': 'candidate', 'username': 'candidate@example.com'}},
            '/api/auth/logout': {'status': 204},
            '/api/candidate/exam-entry': {'status': 503, 'delay': 150},
        })
        self.check('''
fill('exam-username','abcde');fill('exam-password','abcdefgh');el('exam-form').requestSubmit();
el('switch-account').click();await wait(200);
assert(!el('account-form').hidden&&el('exam-result').hidden,'stays on account form');
assert(!el('entry-message').textContent,'stale exam error ignored');
''')

    def test_account_pages_remain_readable_on_narrow_screens(self):
        for path, form in (('/', 'login-form'), ('/register', 'register-form'), ('/exam-entry', 'account-form'), ('/verify-email', None)):
            self.open(path)
            self.browser.command('Emulation.setDeviceMetricsOverride', {'width': 320, 'height': 800, 'deviceScaleFactor': 1, 'mobile': False})
            self.check(('await submit('+json.dumps(form)+');' if form else '')+'''
assert(document.documentElement.scrollWidth<=innerWidth,'no horizontal overflow');
for(const field of document.querySelectorAll('.ab-field-error')){
 const rect=field.getBoundingClientRect();assert(rect.left>=0&&rect.right<=innerWidth,'field error fits');
 assert(getComputedStyle(field).color==='rgb(162, 29, 56)','field error red');
}
''')
        self.browser.command('Emulation.clearDeviceMetricsOverride')


if __name__ == '__main__':
    unittest.main()
