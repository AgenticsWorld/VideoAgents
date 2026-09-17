/* 对白语音库面板(2026-09-13,输出设置「生成对白语音」output.dialogue_tts)
   分镜预览页(白模样片下方;故事板页 2026-09-17 起不再显示——那时还没有 shot_list,无台词可合成):显示库现状(总句/一致/过期/缺失/未选角)、
   「刷新对白语音」按钮(POST /episodes/<ep>/dialogue-tts/sync,宿主后台跑 code/dialogue_tts.py),运行中自行轮询
   GET /episodes/<ep>/dialogue-tts 并把 #dttsbox 重绘;开关关闭时整块不显示。库文件 assets/audio/voice/<ep>/tts/。 */
(function(){
  const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const tr=(k,p)=>(window.I18N&&I18N.f)?I18N.f(k,p||{}):Object.keys(p||{}).reduce((s,x)=>s.split('{'+x+'}').join(p[x]),k);
  const t=k=>tr(k,{});
  const POLL={};   // proj/ep -> timer
  let CUR={proj:'',ep:'',st:{}};
  function url(proj,ep,tail){return '/api/v1/projects/'+encodeURIComponent(proj)+'/episodes/'+encodeURIComponent(ep)+'/dialogue-tts'+(tail||'')}
  function html(st,proj,ep){
    st=st||{};CUR={proj,ep,st};
    if(!st.enabled&&!st.error)return '';   // 未开启「生成对白语音」不显示面板(2026-09-17)
    let line='';
    if(st.running)line=`<span class="spin">⏳</span> ${esc(t('对白语音同步中…(只补合成台词或音色变了的句子)'))}`;
    else if(st.job_error)line=`<span style="color:var(--red)">❌ ${esc(st.job_error.slice(0,120))}</span>`;
    else if(st.error)line=`<span style="color:var(--red)">❌ ${esc(st.error.slice(0,120))}</span>`;
    else if(!st.has_shot_list)line=`<span style="color:var(--dim)">${esc(t('本集还没有 shot_list.json,定稿分镜后才有台词可合成'))}</span>`;
    else if(st.needs_sync)line=`<span style="color:var(--yellow)">⚠ ${esc(t('对白语音库已过期(台词或音色变了),出样片时会自动更新,也可现在刷新'))}</span>`;
    else if(st.total)line=`<span style="color:var(--green)">✓ ${esc(t('对白语音库与当前台词一致'))}${st.synced_at?' · '+esc(st.synced_at):''}</span>`;
    else line=`<span style="color:var(--dim)">${esc(t('本集没有台词'))}</span>`;
    const counts=st.has_shot_list?tr('{n} 句 · 一致 {f} · 过期 {s} · 缺失 {m} · 未选角 {u}',{n:st.total||0,f:st.fresh||0,s:st.stale||0,m:st.missing||0,u:st.unbound||0})+(st.failed?' · '+tr('失败 {x}',{x:st.failed}):''):'';
    const chan=st.provider?` · ${esc(st.provider)}${st.model?'/'+esc(st.model):''}`:'';
    const reasons=(st.unbound_reasons||[]).length?`<div style="color:var(--yellow);font-size:12px;margin-top:4px">${esc(t('未选角的句子已跳过(WARN,不阻断样片);补齐 casting.json 或把 speaker 改成人物编号后刷新'))}:<br>${st.unbound_reasons.map(r=>'· '+esc(r)).join('<br>')}</div>`:'';
    const log=st.job_log&&!st.running?`<pre style="font-size:11px;color:var(--dim);white-space:pre-wrap;margin:6px 0 0;max-height:120px;overflow:auto">${esc(st.job_log.split('\n').slice(-6).join('\n'))}</pre>`:'';
    return `<div class="doc closed dtts"><h3 onclick="if(!event.target.closest('button'))this.parentNode.classList.toggle('closed')">🗣 ${esc(t('对白语音'))} <span class="meta">${esc(counts)}${chan}</span><span style="margin-left:auto;display:inline-flex;gap:6px;align-items:center;font-size:12px">${line}<button class="editbtn dttsbtn" data-force="0" ${st.running||!st.has_shot_list?'disabled':''} title="${esc(t('按当前台词补合成缺失/过期的句子(按人物嗓音模板,走 TTS 渠道,只合成有变化的句子)'))}">🗣 ${esc(t('刷新对白语音'))}</button><button class="editbtn dttsbtn" data-force="1" ${st.running||!st.has_shot_list?'disabled':''} title="${esc(t('忽略缓存,全部句子重新合成(费用按句计)'))}">↻ ${esc(t('全部重出'))}</button></span></h3><div class="body"><div style="color:var(--dim);font-size:12px">${esc(tr('库文件 assets/audio/voice/{ep}/tts/(逐句 mp3 + tts_manifest.json);用于动态样片、白模样片与后期配音',{ep}))}</div>${reasons}${log}</div></div>`;
  }
  function render(st){
    const box=document.getElementById('dttsbox');if(!box)return;
    const open=!box.querySelector('.doc.closed');box.innerHTML=html(st,CUR.proj,CUR.ep);
    if(open){const d=box.querySelector('.doc');if(d)d.classList.remove('closed')}
  }
  async function refresh(proj,ep){
    try{const r=await fetch(url(proj,ep));if(!r.ok)return null;const st=await r.json();if(CUR.proj===proj&&CUR.ep===ep)render(st);return st}catch(_){return null}
  }
  function poll(proj,ep){
    const k=proj+'/'+ep;if(POLL[k])return;
    POLL[k]=setInterval(async()=>{const st=await refresh(proj,ep);if(!st||!st.running){clearInterval(POLL[k]);delete POLL[k]}},4000);
  }
  document.addEventListener('click',async e=>{
    const b=e.target.closest('.dttsbtn');if(!b||b.disabled||!CUR.ep)return;
    const {proj,ep}=CUR;b.disabled=true;
    try{
      const r=await fetch(url(proj,ep,'/sync'),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({force:b.dataset.force==='1'})});
      const j=await r.json().catch(()=>({}));
      if(!r.ok)throw new Error(j.detail||j.error||r.status);
      render(Object.assign({},CUR.st,{running:true,job_error:''}));poll(proj,ep);
    }catch(err){b.disabled=false;alert(tr('刷新对白语音失败:{e}',{e:err.message}))}
  });
  // SSE:宿主同步结束推 dialogue_tts 事件;页面若已开了 EventSource 也可直接调 DialogueTTS.refresh
  try{
    const es=new EventSource('/api/v1/events');
    es.onmessage=ev=>{try{const d=JSON.parse(ev.data);if(d.type==='dialogue_tts'&&d.project===CUR.proj&&d.ep===CUR.ep)refresh(CUR.proj,CUR.ep)}catch(_){}};
  }catch(_){}
  window.DialogueTTS={html,render,refresh,poll};
})();
