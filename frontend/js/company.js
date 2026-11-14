(() => {
 const el=id=>document.getElementById(id), manager=AssessBridgeFeedback.create({notificationHost:'#notifications'}), scope=manager.scope('company');
 window.assessbridgeCompanyFeedback=manager;
 const roles={company_admin:'Company administrator',employer_personnel:'Employer personnel',auditor:'Auditor'};
 async function api(path,body){let response;try{response=await fetch(path,{method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:body===undefined?undefined:JSON.stringify(body)})}catch{throw Error('Could not reach AssessBridge. Try again.')}
 const data=await response.json().catch(()=>({}));if(!response.ok)throw Error(response.status>=500?'Could not complete the request. Try again.':typeof data.detail==='string'?data.detail:'Check the information and try again.');return data;}
 function error(e){scope.show({id:'error',target:'#message',text:e.message,tone:'error'});}
 function success(text,persistent=false){scope.clear('error');if(persistent)scope.show({id:'outcome',target:'#message',text,tone:'success'});else scope.notify({id:'saved',text});}
 function bind(id,action){const form=el(id);if(!form)return;form.noValidate=true;form.addEventListener('submit',async e=>{e.preventDefault();if(form.dataset.busy)return;scope.clear('error');if(id==='details')scope.clear('details-error');let first;
 for(const input of form.querySelectorAll('input,select')){scope.clear('field-'+input.name);let text='';if(!input.validity.valid)text=input.type==='email'?'Enter a valid email address.':input.type==='password'?'Use a password of at least 12 characters.':'Complete this field.';if(id==='details'&&input.name==='name'&&!input.value.trim())text='Enter the company name.';if(input.name==='confirmation'&&input.value!==form.elements.password.value)text='Passwords do not match.';if(input.type==='password'&&new TextEncoder().encode(input.value).length>72)text='Use a password of at most 72 bytes.';if(text){scope.fieldError({id:'field-'+input.name,input,text});first ||= input;}}
 if(first){first.focus();return;}form.dataset.busy='1';const button=form.querySelector('button'),label=button.textContent;button.disabled=true;button.textContent='Please wait...';try{await action(Object.fromEntries(new FormData(form)),form)}catch(e){if(id==='details')scope.show({id:'details-error',target:'#company-details-feedback',text:e.message,tone:'error'});else error(e)}finally{delete form.dataset.busy;button.disabled=false;button.textContent=label;}});}
 function confirm(title,text,callback){scope.confirm({id:'confirm',title,text,confirmLabel:'Confirm',onConfirm:callback,errorMessage:e=>e.message});}
 if(document.body.dataset.page==='access'){
  const descriptions={'Register your company':['COMPANY REGISTRATION','Set up your company and its first administrator.'],'Reset staff password':['ACCOUNT RECOVERY','Enter your work email to receive a password reset link.'],'Choose a new password':['ACCOUNT RECOVERY','Choose a new password for your staff account.'],'Verify your company':['EMAIL VERIFICATION','Activate your company account to continue.'],'Join your company':['STAFF INVITATION','Create your password to join the company below.']};
  const updateHeading=()=>{const copy=descriptions[el('title').textContent];if(copy){el('access-kicker').textContent=copy[0];el('access-description').textContent=copy[1];}};
  new MutationObserver(updateHeading).observe(el('title'),{childList:true,characterData:true,subtree:true});

  const hash=new URLSearchParams(location.hash.slice(1)),token=hash.get('verify')||hash.get('accept')||hash.get('reset');const resetRequest=new URLSearchParams(location.search).has('reset');history.replaceState(null,'',location.pathname);
  bind('registration',async data=>{const result=await api('/api/company/register',data);el('registration').hidden=true;success(result.message,true)});
  bind('accept',async data=>{const result=await api(hash.has('reset')?'/api/company/password/reset':'/api/company/accept',{token,password:data.password});el('accept').hidden=true;success(result.message,true)});
  bind('reset-request',async data=>{const result=await api('/api/company/password/request',data);success(result.message,true);});
  if(resetRequest){el('registration').hidden=true;el('reset-request').hidden=false;el('title').textContent='Reset staff password';}
  if(hash.has('reset')){el('registration').hidden=true;el('accept').hidden=false;el('title').textContent='Choose a new password';el('accept').querySelector('button').textContent='Save password';return;}
  if(token){el('registration').hidden=true;if(hash.has('verify')){el('title').textContent='Verify your company';const verify=()=>api('/api/company/verify',{token}).then(r=>success(r.message,true)).catch(e=>scope.show({id:'error',target:'#message',text:e.message,tone:'error',action:{label:'Retry',onClick:verify}}));verify();}else{el('title').textContent='Join your company';api('/api/company/invitation',{token}).then(r=>{el('invitation-details').textContent=`${r.company} / ${roles[r.role]} / ${r.email}`;el('accept').hidden=false}).catch(error);}}
  return;
 }
 function button(text,action){const b=document.createElement('button');b.type='button';b.textContent=text;b.onclick=action;return b;}
 function row(host,values){const tr=document.createElement('tr');for(const [index,value] of values.entries()){const td=document.createElement('td');td.dataset.label=['Name','Email','Role','Status'][index];if(['Active','Inactive','Pending','Accepted','Revoked','Expired'].includes(value)){const badge=document.createElement('span');badge.className='status-pill';badge.dataset.status=value;badge.textContent=value;td.append(badge);}else td.textContent=value??'Not provided';tr.append(td)}host.append(tr);const td=document.createElement('td');td.dataset.label='Actions';tr.append(td);return td;}
 let serial=0,companyName='Company administration';
 function updatePageHeading(){const settings=!document.querySelector('[data-pane="settings"]').hidden;el('title').textContent=settings?'Company settings':companyName;document.querySelector('.page-heading .eyebrow').textContent=settings?companyName:'YOUR COMPANY';document.querySelector('.stats-row').hidden=settings;el('refresh').hidden=settings;}
 function filterStaff(){
  const search=el('staff-search').value.trim().toLowerCase(),role=el('staff-role').value;
  let visible=0;
  for(const row of el('staff').rows){const matches=(!search||[row.cells[0].textContent,row.cells[1].textContent].some(v=>v.toLowerCase().includes(search)))&&(!role||row.cells[2].textContent===roles[role]);row.hidden=!matches;if(matches)visible++;}
  el('staff-empty').hidden=visible>0;
 }
 el('staff-search').addEventListener('input',filterStaff);el('staff-role').addEventListener('change',filterStaff);
 for(const button of document.querySelectorAll('[data-view]'))button.addEventListener('click',()=>{
  for(const item of document.querySelectorAll('[data-view]')){const selected=item===button;item.classList.toggle('is-active',selected);if(selected)item.setAttribute('aria-current','page');else item.removeAttribute('aria-current');}
  for(const pane of document.querySelectorAll('[data-pane]'))pane.hidden=pane.dataset.pane!==button.dataset.view;
  updatePageHeading();
 });
 async function load(){const request=++serial;try{const data=await api('/api/company/staff');if(request!==serial)return;scope.clear('error');companyName=data.company.name;updatePageHeading();if(document.activeElement!==el('details').elements.name)el('details').elements.name.value=data.company.name;el('staff').replaceChildren();el('invitations').replaceChildren();
 el('active-count').textContent=data.users.filter(u=>u.is_active).length;
 const pending=data.invitations.filter(i=>!i.accepted_at&&!i.revoked_at&&!i.expired).length;
 el('pending-count').textContent=pending;el('pending-nav').textContent=pending;
 el('admin-count').textContent=data.users.filter(u=>u.is_active&&u.role==='company_admin').length;
 el('invitations-empty').hidden=data.invitations.length>0;
 for(const user of data.users){const actions=row(el('staff'),[user.full_name||user.email,user.email,roles[user.role],user.is_active?'Active':'Inactive']);if(user.is_active)actions.append(button('Deactivate',()=>confirm('Deactivate account?',`${user.email||user.full_name} will lose company access.`,async()=>{await api(`/api/company/staff/${user.id}/deactivate`,{});success('Account deactivated.');await load();})));}
 for(const i of data.invitations){const status=i.accepted_at?'Accepted':i.revoked_at?'Revoked':i.expired?'Expired':'Pending';const actions=row(el('invitations'),[i.full_name,i.email,roles[i.role],status]);if(status==='Pending')actions.append(button('Revoke',()=>confirm('Revoke invitation?',i.email,async()=>{await api(`/api/company/invitations/${i.id}/revoke`,{});success('Invitation revoked.');await load();})));if(['Pending','Expired'].includes(status))actions.append(button('Resend',()=>confirm('Resend invitation?',`Send a new invitation to ${i.email}. The previous link will expire.`,async()=>{await api('/api/company/staff/invite',{name:i.full_name,email:i.email,role:i.role});success('Invitation sent.');await load();})));}
 filterStaff();
 }catch(e){if(request===serial)error(e)}}
 bind('details',async data=>{await api('/api/company/details',data);success('Company details saved.');await load();});
 bind('invite',async(data,form)=>{if(data.role==='company_admin'){confirm('Invite an administrator?',`${data.email} will be able to manage your company staff.`,async()=>{await api('/api/company/staff/invite',data);form.reset();success('Invitation sent.');await load()});}else{await api('/api/company/staff/invite',data);form.reset();success('Invitation sent.');await load();}});
 el('refresh').onclick=load;el('logout').onclick=async()=>{try{await api('/api/auth/logout',{});location.assign('/')}catch(e){error(e)}};load();
})();
