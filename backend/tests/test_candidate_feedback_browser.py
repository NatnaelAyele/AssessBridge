"""Candidate feedback with real Chrome, local storage, and controlled exam/upload APIs."""
from pathlib import Path
import shutil
import unittest

import test_account_feedback_browser as accounts


class CandidateFeedbackBrowserTests(unittest.TestCase):
    open_page = accounts.AccountFeedbackBrowserTests.open
    check = accounts.AccountFeedbackBrowserTests.check

    @classmethod
    def setUpClass(cls):
        accounts.AccountFeedbackBrowserTests.setUpClass.__func__(cls)
        public = Path(cls.temporary.name)/'public'
        for page in ('candidate', 'exam-room'):
            shutil.copyfile(accounts.ROOT/f'frontend/{page}.html', public/f'{page}.html')
        for name in ('candidate-feedback', 'candidate', 'exam-room', 'identity', 'identity-crop', 'exam-storage', 'exam-connectivity', 'exam-recording', 'exam-readiness', 'exam-attempt'):
            shutil.copyfile(accounts.ROOT/f'frontend/js/{name}.js', public/f'js/{name}.js')

    @classmethod
    def tearDownClass(cls):
        accounts.AccountFeedbackBrowserTests.tearDownClass.__func__(cls)

    def open(self, routes=None, dashboard=False):
        self.initializer = type(self).initializer
        self.run_id = type(self).run_id
        self.open_page('/candidate.html' if dashboard else '/exam-room.html', {
            '/api/auth/me': {'status': 200, 'body': {'role': 'candidate', 'username': 'candidate@example.com'}},
            '/api/candidate/assessments': {'status': 200, 'body': {'assessments': []}},
            '/api/candidate/exam-room': {'status': 200, 'delay': 30, 'body': {'title': 'Example exam', 'consented': False, 'identity_policy': 'Other ID cards'}},
            '/api/candidate/exam/prepare': {'status': 200, 'body': {'storage_scope': 'feedback-'+str(self.run_id)}},
            '/api/candidate/exam/answer': {'status': 200, 'body': {'answered_count': 1}},
            '/api/candidate/exam/recording/finish': {'status': 200, 'body': {'status': 'complete'}},
            '/api/candidate/exam/exit-ready': {'status': 200, 'body': {'exit_url': '/seb/exit', 'recording_status': 'complete'}},
            **(routes or {}),
        })
        type(self).initializer = self.initializer
        type(self).run_id = self.run_id
        if not dashboard:
            self.check("await until(()=>!el('consent-panel').hidden);")
            self.browser.evaluate('''window.activeState=()=>({state:'active',title:'Example exam',current_index:0,question_count:2,answered_count:0,
 deadline_at:new Date(Date.now()+600000).toISOString(),server_now:new Date().toISOString(),
 recovery_seconds:0,recovery_started_at:null,resume_required:false,question:{type:'written',prompt:'Explain your answer'},answer:null});
window.finishedState=(state='submitted')=>({state,title:'Example exam',answered_count:1,question_count:2,submission_reason:'manual',upload_deadline_at:new Date(Date.now()+60000).toISOString()});
window.prepareExam=async()=>{await examRecovery.prepare();mediaReady=()=>true;assessbridgeRecording.healthy=()=>true;
attemptState=activeState();deadlineTime=Date.parse(attemptState.deadline_at);serverAnchor=Date.now();clientAnchor=performance.now();renderQuestion(attemptState);showPanel('exam-panel');deviceStatusChanged()};true''')

    def test_repeated_saves_update_one_quiet_status_and_preserve_local_answer(self):
        self.open()
        self.check('''
await prepareExam();fill('written-answer','First answer');await ensureSaved();
assert(el('save-status').textContent==='Saved','server acknowledged');
for(let n=0;n<5;n++){fill('written-answer','Answer '+n);await ensureSaved()}
assert(el('save-status').querySelectorAll('.ab-feedback').length===1,'status updated in place');
assert(!document.querySelector('.ab-feedback--temporary'),'no repeated notifications');
examRecovery.lost();fill('written-answer','Offline answer');await ensureSaved();
assert(el('save-status').textContent.includes('Saved on device'),'local save distinct');
assert((await examStorage.get('draft')).answer==='Offline answer','answer really stored');
assert(el('next-question').disabled&&el('previous-question').disabled,'navigation blocked while offline');
assert(el('connection-status').textContent.includes('Connection lost'),'persistent connection warning');
''')

    def test_storage_failure_never_claims_saved_on_device(self):
        self.open()
        self.check('''
await prepareExam();examRecovery.lost();examStorage.put=async()=>{throw Error('PRIVATE quota failure')};
fill('written-answer','Unsaved answer');await ensureSaved();
assert(el('save-status').textContent.includes('Save failed'),'storage failure explicit');
assert(!el('save-status').textContent.includes('Saved on device'),'no false local success');
assert(el('next-question').disabled,'storage failure blocks navigation');
assert(!document.body.textContent.includes('PRIVATE'),'no raw storage error');
''')

    def test_connection_recovery_replaces_warning(self):
        self.open()
        self.check('''
await prepareExam();examRecovery.lost();
routes['/api/candidate/exam/heartbeat']={status:200,body:attemptState};await examRecovery.heartbeat();
assert(el('connection-status').textContent==='Connected','recovered state');
assert(el('exam-device-warning').hidden&&!el('exam-monitoring-warning').textContent,'obsolete device warning removed');
''')

    def test_submission_hides_warnings_and_exit_waits_for_confirmation(self):
        self.open({'/api/candidate/exam/exit-ready': {'status': 200, 'delay': 150, 'body': {'exit_url': '/seb/exit', 'recording_status': 'incomplete'}}})
        self.check('''
await prepareExam();setExamError('Old save error');examRecovery.lost();
assessbridgeRecording.finish=async()=>({status:'incomplete',upload_complete:true});
showSubmitted(finishedState());await wait(40);
assert(!el('submitted-panel').hidden&&el('exit-exam').hidden,'exit gated during confirmation');
assert(!el('connection-status').textContent&&!el('exam-error').textContent,'old warnings removed');
await wait(180);
assert(!el('exit-exam').hidden,'exit after server acknowledgement');
assert(el('recording-final-status').textContent==='Available recording uploaded.','available upload complete');
assert(el('recording-completeness').textContent.includes('incomplete'),'missing footage remains separate');
assert(el('recording-upload-progress').value===100,'completed available transfer');
''')

    def test_upload_failure_retains_progress_and_retry(self):
        self.open({'/api/candidate/exam/exit-ready': {'status': 503, 'body': {'detail': 'PRIVATE'}}})
        self.check('''
assessbridgeRecording.finish=async()=>({status:'complete',upload_complete:true});
el('recording-upload-progress').value=65;el('recording-upload-progress').hidden=false;
showSubmitted(finishedState());await wait(60);
assert(el('exit-exam').hidden&&!el('retry-final-upload').hidden,'failure gates exit and offers retry');
assert(el('recording-final-status').textContent.includes('Upload confirmation failed'),'persistent upload error');
assert(el('recording-upload-progress').value===65,'existing progress preserved');
routes['/api/candidate/exam/exit-ready']={status:200,body:{recording_status:'complete',exit_url:'/seb/exit'}};
el('retry-final-upload').click();await wait(60);
assert(!el('exit-exam').hidden&&el('retry-final-upload').hidden,'retry resolves blocker');
assert(el('recording-completeness').textContent==='Session recording complete.','completeness confirmed');
assert(!document.body.textContent.includes('PRIVATE'),'no raw exception');
''')

    def test_actual_recording_finalizer_shows_acknowledged_progress(self):
        self.open({'/api/candidate/exam/recording/finish': {'status': 200, 'delay': 120, 'body': {'status': 'complete'}}})
        self.check('''
const saved=new Map([['part:000000000001',{id:1,mime:'video/webm',next:1,stopped:true}],['chunk:000000000001:000000000000',new Blob(['sample-footage'])]]);
examStorage.names=async prefix=>[...saved.keys()].filter(k=>k.startsWith(prefix));
examStorage.get=async name=>saved.get(name);examStorage.put=async(name,value)=>saved.set(name,value);
examStorage.remove=async name=>saved.delete(name);examStorage.clear=async()=>saved.clear();
routes['/api/candidate/exam/recording/parts/1/chunks/0']={status:200,delay:60};
routes['/api/candidate/exam/recording/parts/1/finish']={status:200};
showSubmitted(finishedState());await wait(40);
assert(!el('recording-upload-progress').hidden&&!el('recording-upload-detail').hidden,'progress visible while uploading');
assert(el('exit-exam').hidden,'cannot exit during upload');
await wait(220);
assert(!el('exit-exam').hidden&&el('recording-upload-progress').value===100,'recording confirmed');
assert(requests.some(r=>r.path.endsWith('/chunks/0')),'actual chunk acknowledged');
''')

    def test_terminal_outcomes_are_persistent_and_timeout_copy_is_accurate(self):
        self.open()
        self.check('''
assessbridgeRecording.finish=async()=>({status:'complete',upload_complete:true});
showSubmitted({...finishedState(),submission_reason:'timeout'});await wait(30);
assert(el('submitted-message').textContent.includes('before the deadline'),'timeout outcome');
showSubmitted(finishedState('interrupted'));await wait(30);
assert(el('submitted-title').textContent.includes('interrupted')&&el('submitted-message').textContent.includes('kept for review'),'interrupted outcome');
showSubmitted(finishedState('disqualified'));await wait(30);
assert(el('submitted-title').textContent.includes('disqualified'),'disqualified outcome');
assert(!document.querySelector('.ab-feedback--temporary'),'no temporary terminal outcomes');
''')

    def test_identity_device_feedback_and_dashboard_retry(self):
        self.open()
        self.check('''
showPanel('device-panel');setDeviceError('Reconnect the microphone.');
assert(el('device-error').querySelector('[data-tone=error]'),'device error styled');setDeviceError('');assert(el('device-error').hidden,'device error clears');
showPanel('identity-panel');identityMessage('The printed name does not match the invitation.');
assert(el('identity-error').textContent.includes('printed name'),'identity reason retained');
showIdentityResult({status:'failed',checked_at:'2026-11-12T09:00:00Z',stage:'name',attempts:3});
assert(!el('identity-check-result').textContent,'no duplicate attempt exposition');
identityMessage('');showIdentityResult({status:'passed'});assert(el('identity-check-result').textContent==='Identity check passed.','passed status');
''')

    def test_timeout_confirmation_and_mobile_exam_status_layout(self):
        self.open()
        self.browser.command('Emulation.setDeviceMetricsOverride', {'width': 390, 'height': 844, 'deviceScaleFactor': 1, 'mobile': False})
        self.check('''
await prepareExam();setSaveStatus('Saved on device. Server save pending.','saving');
assert(document.documentElement.scrollWidth<=innerWidth,'status chips fit mobile');
const timer=el('exam-timer').getBoundingClientRect();assert(timer.left>=0&&timer.right<=innerWidth,'timer remains visible');
assessbridgeRecording.finish=async()=>({status:'complete',upload_complete:true});
routes['/api/candidate/exam/session']={status:200,body:{...finishedState(),submission_reason:'timeout'}};
attemptState.deadline_at=new Date(Date.now()-1000).toISOString();startCountdown(attemptState);await wait(80);
assert(!el('submitted-panel').hidden&&el('exam-panel').hidden,'timeout enters persistent outcome');
assert(el('submitted-message').textContent.includes('before the deadline'),'only received answers submitted');
assert(el('submitted-progress').textContent==='1 of 2 questions answered.','exact submitted progress');
''')
        self.browser.command('Emulation.clearDeviceMetricsOverride')

    def test_submit_dialog_retains_failure_and_blocks_duplicate_requests(self):
        self.open({'/api/candidate/exam/submit': {'status': 503, 'delay': 120}})
        self.check('''
await prepareExam();attemptState.current_index=1;renderQuestion(attemptState);deviceStatusChanged();
routes['/api/candidate/exam/session']={status:200,body:attemptState};
el('next-question').focus();el('next-question').click();await wait(0);
assert(el('submit-dialog').classList.contains('ab-feedback-dialog'),'shared submission');
el('confirm-submit').click();el('confirm-submit').click();
assert(el('cancel-submit').disabled,'cannot cancel running submission');await wait(170);
assert(requests.filter(r=>r.path.endsWith('/submit')).length===1,'one submission');
assert(el('submit-dialog').open&&el('submit-dialog').textContent.includes('Could not connect'),'submission error stays in modal');
assert(!el('exam-error').textContent,'no error behind modal');
el('cancel-submit').click();await wait(0);assert(!el('submit-dialog'),'cancel closes shared dialog');
''')

    def test_leave_failure_keeps_emergency_exit_available(self):
        self.open({'/api/candidate/exam/leave': {'network': True}})
        self.check('''
await prepareExam();el('report-interruption').click();
const dialog=el('leave-exam-dialog');assert(dialog.classList.contains('ab-feedback-dialog'),'shared voluntary exit');
dialog.querySelector('.ab-feedback-dialog__actions button').click();await wait(0);
assert(!requests.some(r=>r.path.endsWith('/leave')),'cancel sends no exit');
el('report-interruption').click();const second=el('leave-exam-dialog');
second.querySelector('.ab-feedback-dialog__actions button:last-child').click();await wait(60);
assert(second.open&&second.textContent.includes('Could not confirm the exit'),'uncertain exit not reported as disqualified');
assert(second.querySelector('a').getAttribute('href')==='/seb/exit','emergency quit link preserved');
second.querySelector('.ab-feedback-dialog__actions button').click();await wait(0);
''')

    def test_late_dashboard_failure_cannot_replace_newer_success(self):
        self.open(dashboard=True)
        self.check('''
routes['/api/candidate/assessments']=[{status:503,delay:140},{status:200,body:{assessments:[]}}];
const old=loadDashboard();await loadDashboard();await old;
assert(el('dashboard-message').hidden,'old dashboard error suppressed');
assert(el('active-list').textContent.includes('No invitations'),'new data preserved');
''')

    def test_old_upload_confirmation_cannot_overwrite_new_outcome(self):
        self.open()
        self.check('''
let rejectOld;
assessbridgeRecording.finish=()=>new Promise((resolve,reject)=>{rejectOld=reject});
showSubmitted(finishedState());
assessbridgeRecording.finish=async()=>({status:'complete',upload_complete:true});
showSubmitted(finishedState('interrupted'));await wait(50);rejectOld(Error('old upload failure'));await wait(30);
assert(el('recording-final-status').textContent==='Available recording uploaded.','old failure cannot overwrite confirmed upload');
assert(el('retry-final-upload').hidden&&!el('exit-exam').hidden,'latest exit confirmation retained');
''')
        self.open({'/api/candidate/assessments': {'status': 503}}, dashboard=True)
        self.check('''
assert(el('dashboard-message').textContent.includes('Could not load'),'dashboard error');
routes['/api/candidate/assessments']={status:200,body:{assessments:[]}};
el('dashboard-message').querySelector('button').click();await wait(60);
assert(el('dashboard-message').hidden,'dashboard retry clears error');
''')


if __name__ == '__main__':
    unittest.main()
