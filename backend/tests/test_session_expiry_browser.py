"""Session-expiry UI, including expiry during an idle page and renewed exams."""
import unittest
import time
import test_candidate_feedback_browser as candidate


class SessionExpiryBrowserTests(unittest.TestCase):
    open_page = candidate.CandidateFeedbackBrowserTests.open_page
    check = candidate.CandidateFeedbackBrowserTests.check
    open = candidate.CandidateFeedbackBrowserTests.open

    @classmethod
    def setUpClass(cls):
        candidate.CandidateFeedbackBrowserTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        candidate.CandidateFeedbackBrowserTests.tearDownClass.__func__(cls)

    def test_timeout_redirect_shows_one_red_popup_on_sign_in(self):
        self.open({'/api/auth/me': {'status': 200, 'body': {'role': 'candidate', 'username': 'tester'}}}, dashboard=True)
        initializer = self.browser.command('Page.addScriptToEvaluateOnNewDocument', {'source': "if(location.pathname==='/')routes['/api/auth/me']={status:401,body:{detail:'Please sign in'},headers:{'X-AssessBridge-Session-Ended':'1'}};"})['identifier']
        self.addCleanup(lambda: self.browser.command('Page.removeScriptToEvaluateOnNewDocument', {'identifier': initializer}))
        self.check('''
routes['/api/auth/me']={status:401,body:{detail:'Please sign in'},headers:{'X-AssessBridge-Session-Ended':'1'}};
await fetch('/api/auth/me');
''')
        for _ in range(100):
            try:
                if self.browser.evaluate("location.pathname==='/'&&document.readyState==='complete'"):
                    break
            except RuntimeError:
                pass  # The redirect replaces the old JavaScript execution context.
            time.sleep(.05)
        else:
            self.fail('Session expiry did not return to sign-in')
        self.check('''
await until(()=>document.querySelector('.ab-feedback--temporary[data-tone="error"]'));
const messages=document.querySelectorAll('.ab-feedback--temporary[data-tone="error"]');
assert(messages.length===1&&messages[0].textContent.includes('Your session has ended'),'one clear expiry popup');
assert(!location.search.includes('session='),'expiry indicator consumed');
assert(!el('login-form').hidden,'sign-in form available');
messages[0].querySelector('button').click();assert(!document.querySelector('.ab-feedback--temporary'),'popup dismisses');
''')

    def test_idle_deadline_is_confirmed_by_server_before_sign_out(self):
        self.open(dashboard=True)
        self.check('''
routes['/api/auth/me']={status:200,body:{role:'candidate'},headers:{'X-AssessBridge-Session-Expires-At':new Date(Date.now()+100).toISOString()}};
await fetch('/api/auth/me');
const initial=requests.filter(r=>r.path==='/api/auth/me').length;
routes['/api/auth/me']={status:200,body:{role:'candidate'},headers:{'X-AssessBridge-Session-Expires-At':new Date(Date.now()+60000).toISOString()}};
await until(()=>requests.filter(r=>r.path==='/api/auth/me').length>initial);
assert(location.pathname==='/candidate.html','renewed account remains signed in');
''')

    def test_network_and_exam_credential_failures_do_not_end_the_account(self):
        self.open(dashboard=True)
        self.check('''
routes['/api/auth/me']={network:true};
try{await fetch('/api/auth/me')}catch{}
assert(location.pathname==='/candidate.html','connection failure does not sign out');
routes['/api/candidate/exam-entry']={status:401,body:{detail:'Invalid exam username or password'}};
await fetch('/api/candidate/exam-entry',{method:'POST'});
assert(location.pathname==='/candidate.html','incorrect exam credentials do not sign out');
''')

    def test_anonymous_exam_entry_and_wrong_login_stay_on_the_entry_page(self):
        self.open_page('/exam-entry', {'/api/auth/me': {'status': 401, 'headers': {'X-AssessBridge-Session-Ended': '1'}}})
        self.check('''
await wait(80);assert(location.pathname==='/exam-entry','anonymous entry is available');
routes['/api/auth/login']={status:401,body:{detail:'Invalid username or password'}};
await fetch('/api/auth/login',{method:'POST'});
assert(location.pathname==='/exam-entry','wrong login remains available for correction');
''')
