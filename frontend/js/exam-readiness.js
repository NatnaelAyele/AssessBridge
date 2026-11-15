/* Candidate readiness and browser-observed events. No native OS event claims. */
window.examReadiness = (() => {
  const ids = ['ack-instructions','ack-room','ack-interruptions'];
  let starting = false, expectedVersion = null;
  let requirements = null, checksPassed = false, checkedAt = 0, checking = false, serial = 0;
  let uploading = null, writes = Promise.resolve(), pending = 0;
  const tracks = new Map();
  const rows = {software:'Supported environment',seb:'SEB configuration',camera:'Camera',microphone:'Microphone',storage:'Encrypted local storage',network:'Exam server connection',identity:'Identity check'};
  function result(key, state, text) {
    const row = document.getElementById('readiness-'+key);
    if (!row) return;
    row.dataset.state=state;row.querySelector('strong').textContent=text;
  }
  function acknowledged() { return ids.every(id=>document.getElementById(id).checked); }
  function update() {
    const fresh=checksPassed && performance.now()-checkedAt<300000 && mediaReady();
    document.getElementById('check-readiness').disabled=starting||checking;
    document.getElementById('start-exam').disabled=starting || checking || !fresh || !acknowledged();
  }
  function invalidate() { checksPassed=false;update(); }
  async function check() {
    if(checking)return false;
    checking=true;checksPassed=false;update();const request=++serial;
    const button=document.getElementById('check-readiness');button.disabled=true;
    candidateFeedback.show('readiness-error','');
    try {
      requirements=await examRequest('/api/candidate/exam/readiness');
      if(expectedVersion && requirements.instructions_version!==expectedVersion)throw Error('The exam instructions changed. Reload the page and read them again.');
      expectedVersion ||= requirements.instructions_version;
      const info=window.assessbridgeSeb.info(),stream=window.assessbridgeMedia.stream();
      const live=kind=>stream?.getTracks().some(track=>track.kind===kind&&track.readyState==='live'&&!track.muted)||false;
      const format=['video/webm;codecs=vp8,opus','video/webm','video/mp4'].find(type=>window.MediaRecorder?.isTypeSupported?.(type));
      let recording=false;
      if(format&&stream){try{const probe=new MediaRecorder(stream,{mimeType:format});recording=probe.state==='inactive';}catch{}}
      const checks={secure_context:window.isSecureContext,media_recorder:recording,encrypted_storage:false,camera:live('video'),microphone:live('audio'),web_audio:!!(window.AudioContext||window.webkitAudioContext)};
      result('software',checks.secure_context&&recording&&checks.web_audio&&info.platform!=='unsupported'?'passed':'failed',checks.secure_context&&recording&&checks.web_audio&&info.platform!=='unsupported'?'Ready':'Use SEB on Windows or macOS with recording support');
      result('seb','passed',requirements.seb_required?'Approved configuration':'Not required for this environment');
      result('camera',checks.camera?'passed':'failed',checks.camera?'Connected':'Reconnect the camera');
      result('microphone',checks.microphone?'passed':'failed',checks.microphone?'Connected':'Reconnect the microphone');
      result('identity',requirements.identity_passed?'passed':'failed',requirements.identity_passed?'Passed':'Complete the identity check');
      result('storage','checking','Checking');result('network','checking','Checking');
      let network=false;
      try{await window.examStorage.check();checks.encrypted_storage=true;result('storage','passed','Ready');}
      catch{result('storage','failed','Storage unavailable or less than 64 MB free');}
      if(checks.encrypted_storage){try{await window.examRecovery.readiness();network=true;result('network','passed','Connected');}catch{result('network','failed','Reconnect and retry');}}
      else result('network','failed','Check local storage first');
      const payload={instructions_version:requirements.instructions_version,checks,acknowledgements:{instructions:document.getElementById(ids[0]).checked,room:document.getElementById(ids[1]).checked,interruptions:document.getElementById(ids[2]).checked},platform:info.platform,client_version:info.version};
      const saved=await examRequest('/api/candidate/exam/readiness','POST',payload);
      if(request!==serial)return false;
      checksPassed=Object.values(saved.checks).every(Boolean)&&network;checkedAt=performance.now();
      if(!checksPassed)throw Error('Resolve the failed checks, then select Check again.');
      return saved.passed;
    } catch(error) {if(request===serial)candidateFeedback.show('readiness-error',error.message);return false;}
    finally{checking=false;button.disabled=false;button.textContent='Check again';update();}
  }
  function open(version) {
    expectedVersion=version||null;
    ++serial;requirements=null;checksPassed=false;
    for(const id of ids)document.getElementById(id).checked=false;
    for(const key of Object.keys(rows))result(key,'checking','Not checked');
    update();check();
  }
  async function confirm() {
    if(!acknowledged())throw Error('Confirm the three statements before starting.');
    if(!await check())throw Error('Complete the readiness checks before starting.');
  }
  function active() {return typeof attemptState!=='undefined'&&attemptState?.state==='active';}
  async function flush() {
    if(uploading)return uploading;
    uploading=(async()=>{
      while(true){
        await writes;
        const names=await window.examStorage.names('environment:');
        if(!names.length)return true;
        for(let offset=0;offset<names.length;offset+=30){
          const batch=names.slice(offset,offset+30),events=[];
          for(const name of batch){const event=await window.examStorage.get(name);if(event)events.push(event);}
          if(!events.length){for(const name of batch)await window.examStorage.remove(name);continue;}
          try{await examRequest('/api/candidate/exam/environment-events','POST',{events});}
          catch(error){if([403,409].includes(error.status)){for(const name of batch)await window.examStorage.remove(name);continue;}return false;}
          for(const name of batch)await window.examStorage.remove(name);
        }
      }
    })().catch(()=>false).finally(()=>{uploading=null;});return uploading;
  }
  function record(type) {
    if(!active()||pending>=200)return;
    const event={request_key:crypto.randomUUID(),event_type:type,occurred_at:new Date().toISOString()};pending++;
    writes=writes.then(async()=>{const keys=(await window.examStorage.names('environment:')).filter(name=>name.startsWith('environment:'));if(keys.length<200)await window.examStorage.put('environment:'+event.request_key,event);}).catch(()=>{}).finally(()=>{pending--;});
    writes.then(()=>flush());
  }
  function track(kind, ready) {
    if(tracks.get(kind)===ready)return;tracks.set(kind,ready);
    if(!ready)invalidate();record(kind+(ready?'_ready':'_unavailable'));
  }
  for(const id of ids)document.getElementById(id).addEventListener('change',update);
  document.getElementById('check-readiness').addEventListener('click',check);
  document.addEventListener('visibilitychange',()=>record(document.hidden?'visibility_hidden':'visibility_visible'));
  window.addEventListener('blur',()=>record('window_blurred'));
  window.addEventListener('focus',()=>record('window_focused'));
  document.addEventListener('fullscreenchange',()=>record(document.fullscreenElement?'fullscreen_entered':'fullscreen_left'));
  window.addEventListener('pagehide',()=>record('page_hidden'));
  window.addEventListener('pageshow',event=>{if(event.persisted)record('page_restored');});
  window.addEventListener('online',()=>flush());
  setInterval(()=>{if(active())flush();if(!document.getElementById('instructions-panel').hidden)update();},10000);
  return {open,confirm,flush,record,track,invalidate,update,starting(value){starting=value;update();}};
})();
