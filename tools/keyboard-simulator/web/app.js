// Copyright 2026 blue_lobster
// SPDX-License-Identifier: Apache-2.0
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const svg = $('keyboard'), ns = 'http://www.w3.org/2000/svg';
  let state = null, selectedAgent = null, keyNodes = new Map(), agentNodes = new Map();
  let geometrySignature = '', queue = Promise.resolve(), pending = 0, polling = false;
  let selectedProfile = '', selectedScenario = '', activeKey = null, lastEventSignature = '', csrfToken = '', runtimeError = false;
  const down = new Map();
  let lastKeyboardModeTap=0;
  const aliases = {ESC:'Esc',ENTER:'Enter',SPACE:'Space',TAB:'Tab',PAUSE:'Pause',INSERT:'Ins',DELETE:'Del',HOME:'Home',END:'End',LIGHTING_KEY:'Light',SCREENSHOT:'PrtSc',SCROLL_LOCK:'ScrLk',PAGE_UP:'PgUp',PAGE_DOWN:'PgDn',LEFT:'←',RIGHT:'→',UP:'↑',DOWN:'↓',LEFT_CONTROL:'Ctrl',RIGHT_CONTROL:'Ctrl',LEFT_SHIFT:'Shift',RIGHT_SHIFT:'Shift',LEFT_MODIFIER_2:'LMod2',LEFT_MODIFIER_3:'LMod3',RIGHT_MODIFIER_1:'RMod1',RIGHT_MODIFIER_2:'RMod2',LEFT_BRACKET:'[',RIGHT_BRACKET:']',BACKSLASH:'\\',SEMICOLON:';',QUOTE:"'",COMMA:',',PERIOD:'.',SLASH:'/',GRAVE:'`',MINUS:'−',EQUAL:'=',CAPS_LOCK:'Caps',BACKSPACE:'Back',KNOB_PRESS:'Knob'};
  const actionNames = {select:'Open agent',cycle_knob:'Switch knob mode',cycle_sort:'Change slot ordering',mode_key:'Agent Mode / navigation',pickup:'Pick up call',mute:'Mute call',close_gap:'Hold to close gap',exit_focus:'Leave task focus',dismiss_keyboard_frame:'Dismiss keyboard guide',confirm_candidate:'Open previewed agent',previous_page:'Knob counterclockwise',next_page:'Knob clockwise'};
  function showError(error){$('error').textContent=error instanceof Error?error.message:String(error);$('error').hidden=false;}
  function clearError(){$('error').hidden=true;$('error').textContent='';}
  async function request(path, body){
    const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json',...(csrfToken?{'X-Abralia-Token':csrfToken}:{})},body:JSON.stringify(body)};
    const response=await fetch(path,options);let value;
    try{value=await response.json();}catch{throw Error('The simulator returned an unreadable response.');}
    if(!response.ok)throw Error(typeof value.error==='string'?value.error:value.message||`Simulator request failed (${response.status}).`);
    return value;
  }
  function snapshot(value){return value.state || value;}
  function mutate(path, body){
    pending++;
    const task=queue.then(async()=>{const result=await request(path,body);clearError();if(snapshot(result).keys)render(snapshot(result));return result;});
    queue=task.catch(showError).finally(()=>{pending--;});
    return task;
  }
  const action=body=>mutate('/api/action',body);
  function formatTime(time){const total=Math.max(0,Number(time)||0);return `${String(Math.floor(total/60)).padStart(2,'0')}:${(total%60).toFixed(1).padStart(4,'0')}`;}
  function node(name,attributes,parent=svg){const result=document.createElementNS(ns,name);for(const [key,value] of Object.entries(attributes))result.setAttribute(key,String(value));parent.appendChild(result);return result;}
  function foreground(hex){const c=hex.replace('#','').match(/../g).map(x=>parseInt(x,16)/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);return c[0]*.2126+c[1]*.7152+c[2]*.0722>.18?'#101010':'#f5f5f5';}
  function led(key){const color=key.led ?? state.colors?.[key.id];return typeof color==='string' && /^#?[a-f\d]{6}$/i.test(color)?color.replace('#','').toUpperCase():null;}
  function boundAction(key){const binding=(state.bindings||[]).find(b=>b.key===key);return binding?actionNames[binding.action]||binding.action.replaceAll('_',' ').replace(':',' · '):'Ordinary input';}
  function inspectKey(key){
    activeKey=key;
    const k=(state.keys||[]).find(k=>k.id===key);
    if(k)$('key-readout').textContent=`${k.id} · ${led(k)?'#'+led(k):'No LED'} · ${boundAction(k.id)}`;
  }
  function keyDown(id,event){
    if(down.has(id))return;
    if(state.profile.interaction_available===false){inspectKey(id);return;}
    down.set(id,performance.now());inspectKey(id);
    if(event?.pointerId!==undefined)event.currentTarget.setPointerCapture(event.pointerId);
    void action({type:'press',key:id}).catch(()=>{});
  }
  function keyUp(id){
    if(!down.has(id))return;
    const duration=(performance.now()-down.get(id))/1000;down.delete(id);
    void action({type:'release_key',key:id,duration}).catch(()=>{});
  }
  function drawGeometry(){
    const occupied=new Set();
    const keys=state.keys.filter(k=>[k.x,k.y,k.w,k.h].every(Number.isFinite)&&k.w>0&&k.h>0).filter(k=>{
      const position=JSON.stringify([k.x,k.y,k.w,k.h]);
      if(!k.rgb && occupied.has(position))return false;
      occupied.add(position);return true;
    });
    if(!keys.length)return;
    const container=$('keyboard').parentElement;
    const width=Math.max(700,Math.floor(container.clientWidth)-36);
    const x0=Math.min(...keys.map(k=>k.x)),y0=Math.min(...keys.map(k=>k.y));
    const extentX=Math.max(...keys.map(k=>k.x+k.w))-x0,extentY=Math.max(...keys.map(k=>k.y+k.h))-y0;
    const scale=Math.min((width-30)/(extentX+.5),400/(extentY+.5));
    const height=Math.ceil((extentY+.5)*scale+28),offsetX=(width-extentX*scale)/2,offsetY=14+.25*scale;
    const signature=JSON.stringify([width,keys.map(k=>[k.id,k.x,k.y,k.w,k.h])]);
    if(signature===geometrySignature)return;
    geometrySignature=signature;svg.replaceChildren();keyNodes=new Map();
    svg.setAttribute('width',width);svg.setAttribute('height',height);svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
    const title=node('title',{});title.textContent=state.profile?.name||'Keyboard';
    node('rect',{class:'plate',x:offsetX-10,y:offsetY-10,width:extentX*scale+20,height:extentY*scale+20,rx:10});
    for(const k of keys){
      const x=offsetX+(k.x-x0)*scale,y=offsetY+(k.y-y0)*scale,w=k.w*scale-4,h=k.h*scale-4;
      const group=node('g',{class:'key',tabindex:'0',role:'button','data-key':k.id,transform:`translate(${x},${y})`});
      const round=k.id==='KNOB_PRESS'?Math.min(w,h)/2:4;
      node('rect',{class:'key-shell',width:w,height:h,rx:round},group);
      const face=node('rect',{class:'led',x:3,y:3,width:Math.max(1,w-6),height:Math.max(1,h-7),rx:Math.max(2,round-3)},group);
      const label=node('text',{class:'key-label',x:w/2,y:h/2+3,'text-anchor':'middle'},group);
      label.textContent=aliases[k.id]||k.label||k.id;
      if(label.textContent.length>7 && k.w<1.5)label.textContent=label.textContent.slice(0,5)+'…';
      group.addEventListener('pointerdown',e=>{if(e.button===0){e.preventDefault();keyDown(k.id,e);}});
      group.addEventListener('pointerup',()=>keyUp(k.id));group.addEventListener('pointercancel',()=>keyUp(k.id));
      group.addEventListener('dblclick',e=>{if(k.id===state.profile.toggle_key){e.preventDefault();void action({type:'input',key:k.id,gesture:'double'}).catch(()=>{});}});
      group.addEventListener('mouseenter',()=>inspectKey(k.id));group.addEventListener('focus',()=>inspectKey(k.id));
      group.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();if(!e.repeat)keyDown(k.id);}});
      group.addEventListener('keyup',e=>{if(['Enter',' '].includes(e.key)){
        e.preventDefault();const started=down.get(k.id),stamp=performance.now();keyUp(k.id);
        if(k.id===state.profile.toggle_key && started!==undefined && stamp-started<400){
          if(lastKeyboardModeTap && stamp-lastKeyboardModeTap<300){lastKeyboardModeTap=0;void action({type:'input',key:k.id,gesture:'double'}).catch(()=>{});}else lastKeyboardModeTap=stamp;
        }
      }});
      const tooltip=node('title',{},group);keyNodes.set(k.id,{group,face,label,tooltip});
    }
  }
  function renderAgents(){
    const agents=state.agents||[],ids=new Set(agents.map(a=>a.id));
    if(!ids.has(selectedAgent))selectedAgent=agents[0]?.id||null;
    for(const [id,row] of agentNodes){if(!ids.has(id)){row.button.remove();agentNodes.delete(id);}}
    for(const agent of agents){
      let row=agentNodes.get(agent.id);
      if(!row){
        const button=document.createElement('button');button.type='button';button.className='agent-row';button.dataset.agent=agent.id;
        const copy=document.createElement('span'),name=document.createElement('span'),meta=document.createElement('span'),location=document.createElement('span');
        name.className='agent-name';meta.className='agent-meta';location.className='agent-location';copy.append(name,meta);button.append(copy,location);
        button.addEventListener('click',()=>{selectedAgent=agent.id;renderAgents();});
        row={button,name,meta,location};agentNodes.set(agent.id,row);$('agent-list').appendChild(button);
      }
      row.name.textContent=agent.label;row.meta.textContent=`${agent.project} · ${agent.state.replaceAll('_',' ')}`;row.location.textContent=`P${agent.page} / ${agent.f_key}`;
      row.button.classList.toggle('selected',selectedAgent===agent.id);row.button.setAttribute('aria-pressed',String(selectedAgent===agent.id));
    }
    $('agent-count').textContent=String(agents.length);$('agent-actions').hidden=!selectedAgent;
    const selected=agents.find(a=>a.id===selectedAgent);
    if(selected && document.activeElement!==$('agent-state'))$('agent-state').value=selected.state;
  }
  function renderEvents(){
    const events=(state.events||[]).slice(-60).reverse(),signature=JSON.stringify(events);
    if(signature===lastEventSignature)return;
    lastEventSignature=signature;$('event-list').replaceChildren(...events.map(event=>{
      const li=document.createElement('li'),time=document.createElement('time'),text=document.createElement('span');
      time.textContent=formatTime(event.time??event.at??state.time);
      text.textContent=event.label||event.message||event.kind||event.type||JSON.stringify(event);
      li.append(time,text);return li;
    }));
    const last=events[0];if(last)$('latest-action').textContent=last.label||last.message||last.kind||last.type||'Controller updated';
  }
  function render(value){
    state=value;
    if(state.error){runtimeError=true;showError(state.error);}else if(runtimeError){runtimeError=false;clearError();}
    drawGeometry();
    for(const k of state.keys){
      const n=keyNodes.get(k.id);if(!n)continue;
      const hex=led(k),fill=hex?'#'+hex:'#292929';n.face.setAttribute('fill',fill);n.face.setAttribute('stroke',hex?'#'+hex:'#444');n.label.setAttribute('fill',hex?foreground(fill):'#bdbdbd');
      n.group.classList.toggle('pressed',!!k.pressed||down.has(k.id));n.group.setAttribute('aria-label',`${k.label||k.id}. ${boundAction(k.id)}. ${hex?'LED '+hex:'No LED'}`);
      n.tooltip.textContent=`${k.id} · ${boundAction(k.id)}${hex?' · #'+hex:''}`;
    }
    $('board-name').textContent=state.profile.name;
    $('board-caption').textContent=state.profile.interaction_available===false?'LED preview · this profile has no interaction controls':`${state.design_name||'Live design'} · profile geometry`;
    $('clock').textContent=formatTime(state.time);
    $('mode-state').textContent=state.navigation_active?'Agent Mode · navigation armed':state.active?'Agent Mode':'Ordinary typing';
    $('page-state').textContent=`Page ${state.page} / ${state.page_count}`;$('order-state').textContent=state.sort_policy==='project'?'Grouped by project':'Arrival order';
    $('effect-state').textContent=state.frame_source && state.frame_source!=='renderer'?'Custom LED '+state.frame_source:String(state.effect_phase||'Steady').replaceAll('_',' ');
    $('play').textContent=state.playing?'Pause':'Play';$('play').setAttribute('aria-label',state.playing?'Pause simulation':'Play simulation');
    $('knob-controls').hidden=!state.profile.has_encoder;$('knob-mode').textContent=`Knob · ${state.knob_mode}`;
    if(document.activeElement!==$('speed'))$('speed').value=String(state.speed||1);
    const bg=state.background_percent??state.config?.background_brightness_percent;
    if(bg!==undefined && document.activeElement!==$('background')){$('background').value=bg;$('background-value').textContent=bg+'%';}
    if(activeKey)inspectKey(activeKey);renderAgents();renderEvents();
  }
  async function loadEditor(){const value=await request('/api/design');$('design-editor').value=JSON.stringify(value.design||value,null,2);}
  async function loadScenario(){
    const profile=$('profile').value,scenario=$('scenario').value;
    await mutate('/api/reset',{profile,scenario});selectedProfile=profile;selectedScenario=scenario;await loadEditor();
  }
  $('help-toggle').addEventListener('click',()=>{const open=$('help').hidden;$('help').hidden=!open;$('help-toggle').setAttribute('aria-expanded',String(open));});
  document.querySelectorAll('[data-tab]').forEach(button=>button.addEventListener('click',()=>{
    document.querySelectorAll('[data-tab]').forEach(tab=>{const active=tab===button;tab.setAttribute('aria-selected',String(active));$(tab.dataset.tab+'-panel').hidden=!active;});
  }));
  $('load-scenario').addEventListener('click',()=>void loadScenario().catch(showError));
  $('profile').addEventListener('change',()=>void loadScenario().catch(showError));
  $('play').addEventListener('click',()=>void action({type:'play',enabled:!state.playing}).catch(()=>{}));
  $('step').addEventListener('click',()=>void action({type:'advance',seconds:.1}).catch(()=>{}));
  $('speed').addEventListener('change',()=>void action({type:'speed',value:Number($('speed').value)}).catch(()=>{}));
  $('reset').addEventListener('click',()=>void mutate('/api/reset',{}).then(loadEditor).catch(showError));
  $('background').addEventListener('input',()=>{$('background-value').textContent=$('background').value+'%';});
  $('background').addEventListener('change',()=>void action({type:'background',percent:Number($('background').value)}).catch(()=>{}));
  $('agent-state').addEventListener('change',()=>void action({type:'set_state',agent_id:selectedAgent,state:$('agent-state').value}).catch(()=>{}));
  $('notify').addEventListener('click',()=>void action({type:'notify',agent_id:selectedAgent,enabled:true}).catch(()=>{}));
  $('withdraw').addEventListener('click',()=>void action({type:'notify',agent_id:selectedAgent,enabled:false}).catch(()=>{}));
  $('release').addEventListener('click',()=>void action({type:'release',agent_id:selectedAgent}).catch(()=>{}));
  $('add-agent').addEventListener('submit',event=>{event.preventDefault();const prior=new Set((state.agents||[]).map(a=>a.id));void action({type:'add_agent',project:$('new-project').value,label:$('new-label').value}).then(result=>{const added=snapshot(result).agents.find(a=>!prior.has(a.id));if(added)selectedAgent=added.id;$('new-label').value='';renderAgents();}).catch(()=>{});});
  document.querySelectorAll('[data-knob]').forEach(button=>button.addEventListener('click',()=>{
    const key=button.dataset.knob==='press'?'KNOB_PRESS':button.dataset.knob==='previous'?'KNOB_CCW':'KNOB_CW';
    void action({type:'input',key,gesture:'tap'}).catch(()=>{});
  }));
  $('import-design').addEventListener('click',()=>$('design-file').click());
  $('design-file').addEventListener('change',async()=>{const file=$('design-file').files[0];if(!file)return;try{if(file.size>1024*1024)throw Error('Choose a JSON design smaller than 1 MB.');const value=JSON.parse(await file.text());$('design-editor').value=JSON.stringify(value,null,2);clearError();}catch(error){showError(error);}finally{$('design-file').value='';}});
  $('apply-design').addEventListener('click',()=>{try{const value=JSON.parse($('design-editor').value);void mutate('/api/design',value).then(loadEditor).catch(showError);}catch(error){showError(error);}});
  $('download-design').addEventListener('click',()=>{try{
    const text=JSON.stringify(JSON.parse($('design-editor').value),null,2);
    if(new TextEncoder().encode(text).length>1024*1024)throw Error('Keep the design smaller than 1 MB.');
    const form=document.createElement('form'), field=document.createElement('textarea');
    form.method='POST';form.action='/api/download';form.hidden=true;
    field.name='design';field.value=text;form.appendChild(field);document.body.appendChild(form);
    form.submit();setTimeout(()=>form.remove(),1000);clearError();
  }catch(error){showError(error);}});
  window.addEventListener('blur',()=>{for(const id of [...down.keys()])keyUp(id);});
  window.addEventListener('pointerup',()=>{for(const id of [...down.keys()])keyUp(id);});
  window.addEventListener('pointercancel',()=>{for(const id of [...down.keys()])keyUp(id);});
  new ResizeObserver(()=>{if(state)drawGeometry();}).observe(svg.parentElement);
  async function poll(){if(polling||pending)return;polling=true;try{render(snapshot(await request('/api/state')));}catch(error){showError(error);}finally{polling=false;}}
  async function start(){
    const boot=await request('/api/bootstrap');csrfToken=boot.csrf_token||'';
    for(const p of boot.profiles){const option=document.createElement('option');option.value=p.id;option.textContent=p.name;$('profile').appendChild(option);}
    for(const p of boot.scenarios){const option=document.createElement('option');option.value=p.id;option.textContent=p.name||p.label;$('scenario').appendChild(option);}
    selectedProfile=boot.profile.id;$('profile').value=selectedProfile;selectedScenario=$('scenario').value;
    render(boot.state);await loadEditor();setInterval(()=>void poll(),80);
  }
  void start().catch(showError);
})();
