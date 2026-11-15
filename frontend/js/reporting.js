/* Shared result filters and authenticated file exports. */

window.AssessBridgeReporting=(()=>{

 const node=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};

  const grade=(value,max)=>value===null||value===undefined?'Not available':`${value} / ${max}`;

 const labels={not_started:'Not started',in_progress:'In progress',submitted:'Submitted',interrupted:'Interrupted',disqualified:'Disqualified',not_ready:'Not ready',awaiting_review:'Awaiting review',reviewed:'Reviewed',keep_score:'Score kept',change_score:'Score changed',disqualify:'Disqualified',pending:'Needs audit',changed:'Changed since audit',needs_attention:'Needs attention',audited:'Audited',published:'Published',unpublished:'Unpublished'};

 async function download(url,scope,target,name,signal){let response;try{response=await fetch(url,{credentials:'same-origin',signal});}catch(error){if(error.name==='AbortError')return false;throw Error('Could not reach AssessBridge. Try again.');}

  if(!response.ok){throw Error(response.status===401?'Your session ended. Sign in again.':response.status===409?'The report changed during export. Refresh and try again.':response.status===422?'No results match these filters, or the filters are invalid.':response.status===403||response.status===404?'This export is unavailable.':'Could not prepare the export. Try again.');}

  const blob=await response.blob();if(!scope.active||signal?.aborted)return false;

  const object=URL.createObjectURL(blob),link=document.createElement('a');link.href=object;link.download=name;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(object),60000);return true;

 }

 function mount(options){const {table,host,feedback,base,onView,count}=options;const scope=feedback.scope('reporting-'+host.id),abort=new AbortController();let active=true,serial=0,page=1,busy=false;

  const reviewer=options.variant==='reviewer';

  host.classList.toggle('reporting-reviewer',reviewer);

  host.replaceChildren();const form=node('form',undefined,'reporting-filters'),notice=node('div'),fields={};form.noValidate=true;

  const primary=node('div',undefined,'reporting-primary-filters'),extra=node('details',undefined,'reporting-more-filters'),extraTitle=node('summary','More filters'),extraGrid=node('div',undefined,'reporting-extra-grid'),chips=node('div',undefined,'reporting-active-filters');extra.append(extraTitle,extraGrid);if(reviewer)form.append(primary);

  const add=(name,label,control)=>{control.name=name;control.id=host.id+'-'+name;fields[name]=control;const wrap=node('label',label);wrap.append(control);wrap.className='reporting-field reporting-field-'+name;(reviewer?(['search','attempt','review'].includes(name)?primary:extraGrid):form).append(wrap);};

  const search=node('input');search.type='search';search.maxLength=120;search.placeholder='Name, email or session reference';add('search',reviewer?'Find a candidate':'Search candidate or session',search);

  const lists={attempt:['Attempt status',['all','All attempts'],...['not_started','in_progress','submitted','interrupted','disqualified'].map(v=>[v,labels[v]])],review:['Review status / outcome',['all','All reviews'],...['not_ready','awaiting_review','reviewed','keep_score','change_score','disqualify'].map(v=>[v,labels[v]])],audit:['Audit status',['all','All audit statuses'],...['not_ready','pending','changed','needs_attention','audited'].map(v=>[v,labels[v]])],publication:['Publication status',['all','All results'],['published','Published'],['unpublished','Unpublished']]};

  for(const [key,[label,...values]]of Object.entries(lists)){

   const select=node('select');let statuses,decisions;

   if(reviewer&&key==='review'){statuses=node('optgroup');statuses.label='Review status';decisions=node('optgroup');decisions.label='Reviewer decision';}

   for(const [value,text]of values){const option=new Option(reviewer&&key==='review'&&value==='not_ready'?'Not ready for review':reviewer&&key==='audit'&&value==='not_ready'?'No attempt':text,value);

    if(statuses&&value!=='all')(['keep_score','change_score','disqualify'].includes(value)?decisions:statuses).append(option);else select.add(option);}

   if(statuses)select.append(statuses,decisions);

   add(key,reviewer?({attempt:'Exam progress',review:'Review',audit:'Audit',publication:'Result publication'}[key]):label,select);

  }

  for(const [key,label]of [['date_from','Ended from (EAT)'],['date_to','Ended to (EAT)']]){const input=node('input');input.type='date';add(key,reviewer?(key==='date_from'?'Attempt ended on or after (EAT)':'Attempt ended on or before (EAT)'):label,input);}

  const apply=node('button','Apply filters','secondary-button'),clear=node('button','Clear filters','secondary-button');apply.type='submit';clear.type='button';if(reviewer){apply.className='primary-button';clear.textContent='Reset';const buttons=node('div',undefined,'reporting-filter-buttons');buttons.append(apply,clear);form.append(extra,buttons);}else form.append(apply,clear);

  const actions=node('div',undefined,'reporting-export-actions'),exportScope=node('select'),exportLabel=node('label',reviewer?'Include in download':'Export scope');exportScope.add(new Option(reviewer?'Matching candidates (all pages)':'Current filtered results','filtered'));exportScope.add(new Option(reviewer?'All candidates in this exam':'All candidates in this assessment','all'));exportLabel.append(exportScope);const exportButton=node('button',reviewer?'Download CSV':'Export results CSV','secondary-button');exportButton.type='button';actions.append(exportLabel,exportButton);if(reviewer){const toolbar=node('div',undefined,'reporting-results-toolbar'),title=node('h3','Candidate results');toolbar.append(title,actions);host.append(form,chips,toolbar,notice);}else host.append(form,actions,notice);

  const pager=node('div',undefined,'reporting-pagination'),state=node('span'),previous=node('button','Previous','secondary-button'),next=node('button','Next','secondary-button');previous.type=next.type='button';pager.append(state,previous,next);options.pagerHost.append(pager);

  let applied=new URLSearchParams();

  function updateChips(){if(!reviewer)return;chips.replaceChildren();const names={search:'Search',attempt:'Exam progress',review:'Review',audit:'Audit',publication:'Publication',date_from:'Ended from',date_to:'Ended to'};for(const [key,value]of applied){const input=fields[key],label=input.tagName==='SELECT'?([...input.options].find(option=>option.value===value)?.textContent||value):value;const chip=node('button',names[key]+': '+label+' ×','reporting-filter-chip');chip.type='button';chip.setAttribute('aria-label','Remove '+names[key]+' filter: '+label);chip.onclick=()=>{input.value=input.tagName==='SELECT'?'all':'';applied.delete(key);page=1;scope.clear('date-range');updateChips();load();};chips.append(chip);}const advanced=['audit','publication','date_from','date_to'].filter(key=>applied.has(key)).length;extraTitle.textContent='More filters'+(advanced?' ('+advanced+' active)':'');}

  function filters(){const params=new URLSearchParams();for(const [key,input]of Object.entries(fields))if(input.value.trim()&&input.value!=='all')params.set(key,input.value.trim());return params;}

  function valid(){scope.clear('date-range');if(fields.date_from.value&&fields.date_to.value&&fields.date_from.value>fields.date_to.value){scope.fieldError({id:'date-range',input:fields.date_to,text:'Choose a To date on or after the From date.'});fields.date_to.focus();return false;}return true;}

  const empty=text=>{table.replaceChildren();const row=node('tr'),cell=node('td',text,'table-empty');cell.colSpan=9;row.append(cell);table.append(row);};

  async function load(){if(!active)return;const token=++serial;previous.disabled=next.disabled=true;empty('Loading results...');scope.show({id:'load',target:notice,text:'Loading results... ',tone:'loading'});

   try{const response=await fetch(base+'/results?'+new URLSearchParams([...applied,['page',page]]),{credentials:'same-origin',signal:abort.signal});if(!response.ok)throw Error(response.status===401?'Your session ended. Sign in again.':'Could not load results. Try again.');const data=await response.json();if(!active||token!==serial)return;scope.clear('load');table.replaceChildren();

    if(count)count.textContent=`${data.total} ${reviewer&&applied.size?'matching ':''}${data.total===1?'candidate':'candidates'}`;state.textContent=reviewer?(data.total?`${(page-1)*data.page_size+1}–${Math.min(page*data.page_size,data.total)} of ${data.total} candidates`:'0 candidates'):`${data.total} results / Page ${page}`;previous.disabled=page<=1;next.disabled=page*data.page_size>=data.total;exportButton.disabled=data.total===0&&exportScope.value==='filtered';

    for(const item of data.rows){const row=node('tr'),who=node('div');who.append(node('strong',item.name),node('small',item.email),node('small',(reviewer?'Session ':'')+item.session_reference));const final=node('div');final.append(node('strong',item.outcome==='disqualified'?'Disqualified':reviewer&&!item.review_action?(item.review_state==='awaiting_review'?'Awaiting review':'Not reviewed'):grade(item.final_score,item.possible)),node('small',reviewer&&(!item.review_action||item.outcome==='disqualified')?'':labels[item.review_action]||labels[item.review_state]));const actions=node('div',undefined,'reporting-row-actions');

     if(item.has_session){const view=node('button',reviewer?'View report':'View case','secondary-button'),pdf=node('button',reviewer?'Download PDF':'Audit PDF','secondary-button');view.type=pdf.type='button';view.onclick=()=>onView(item.invitation_id);pdf.onclick=()=>exportPdf(item.invitation_id,pdf);actions.append(view,pdf);}else actions.textContent='No attempt';

     const badge=(value)=>node('span',labels[value]||'Not recorded','reporting-status reporting-status-'+value);

     const values=[who,reviewer?badge(item.attempt_state):labels[item.attempt_state],item.mcq_possible?grade(item.mcq_earned,item.mcq_possible):'No MCQs',item.written_possible?(item.written_pending?'Awaiting grading':item.written_status==='Not started'?'Not started':grade(item.written_earned,item.written_possible)):'No written questions',item.written_pending?'Awaiting grading':grade(item.calculated_total,item.possible),final,reviewer?badge(item.audit_status):labels[item.audit_status],reviewer?badge(item.publication_status):labels[item.publication_status],actions];const heads=reviewer?['Candidate','Exam progress','Automatic MCQ score','Written score','Total before review','Final reviewed score','Audit','Publication','Report']:['Candidate / session','Attempt status','MCQ grade','Written grade','Calculated total','Final reviewed result','Audit status','Publication','Report'];

     values.forEach((value,index)=>{const td=node('td');td.dataset.label=heads[index];if(value instanceof Node)td.append(value);else td.textContent=value;row.append(td);});table.append(row);

    }if(!data.rows.length)empty('No results match these filters.');

   }catch(error){if(active&&token===serial&&error.name!=='AbortError'){empty('Results unavailable.');scope.show({id:'load',target:notice,text:error.message==='Failed to fetch'?'Could not reach AssessBridge. Try again.':error.message,tone:'error',action:{label:'Retry',onClick:load}});}}

  }

  async function exportPdf(invitation,button){if(busy)return;busy=true;button.disabled=true;scope.clear('export');scope.show({id:'export-progress',target:notice,text:'Preparing audit PDF...',tone:'loading'});try{const url=options.pdfBase(invitation);const saved=await download(url,scope,notice,`assessment-${options.assessment}-session-${invitation}-audit.pdf`,abort.signal);if(saved)scope.notify({id:'download',text:'Audit report prepared.'});}catch(error){if(active)scope.show({id:'export',target:notice,text:error.message,tone:'error'});}finally{scope.clear('export-progress');busy=false;button.disabled=false;}}

  form.onsubmit=event=>{event.preventDefault();if(valid()){applied=filters();page=1;updateChips();scope.clear('export');load();}};clear.onclick=()=>{form.reset();scope.clear('date-range');applied=new URLSearchParams();page=1;updateChips();scope.clear('export');load();};previous.onclick=()=>{page--;load();};next.onclick=()=>{page++;load();};exportScope.onchange=()=>{exportButton.disabled=false;};

  exportButton.onclick=async()=>{if(busy)return;if(!valid())return;const current=filters();if(current.toString()!==applied.toString()&&exportScope.value==='filtered'){scope.show({id:'export',target:notice,text:'Apply the filters before exporting the filtered results.',tone:'error'});return;}busy=true;exportButton.disabled=true;scope.clear('export');scope.show({id:'export-progress',target:notice,text:'Preparing results CSV...',tone:'loading'});try{const params=exportScope.value==='all'?new URLSearchParams():new URLSearchParams(applied);params.set('scope',exportScope.value);if(await download(base+'/exports/results.csv?'+params,scope,notice,`assessment-${options.assessment}-results.csv`,abort.signal))scope.notify({id:'download',text:'Results CSV prepared.'});}catch(error){if(active)scope.show({id:'export',target:notice,text:error.message,tone:'error'});}finally{scope.clear('export-progress');busy=false;exportButton.disabled=false;}};

  load();return{reload:load,destroy(){active=false;++serial;abort.abort();scope.dispose();host.replaceChildren();pager.remove();}};

 }

 async function pdfButton({button,url,name,scope,target}){if(button.disabled)return;button.disabled=true;scope.clear('audit-export-error');scope.show({id:'audit-export-progress',target,text:'Preparing audit PDF...',tone:'loading'});try{if(await download(url,scope,target,name))scope.notify({id:'audit-download',text:'Audit report prepared.'});}catch(error){if(scope.active)scope.show({id:'audit-export-error',target,text:error.message,tone:'error'});}finally{scope.clear('audit-export-progress');button.disabled=false;}}

 return{mount,pdfButton};

})();

