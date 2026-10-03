/* 对白语音库面板(2026-09-13,输出设置「生成对白语音」output.dialogue_tts)
   分镜预览页(白模样片下方;故事板页 2026-09-17 起不再显示——那时还没有 shot_list,无台词可合成):显示库现状(总句/一致/过期/缺失/未选角)、
   「刷新对白语音」按钮(POST /episodes/<ep>/dialogue-tts/sync,宿主后台跑 code/dialogue_tts.py),运行中自行轮询
   GET /episodes/<ep>/dialogue-tts 并把 #dttsbox 重绘;开关关闭时整块不显示。库文件 assets/audio/voice/<ep>/tts/。
   台词演法(2026-10-02,modules/dialogue_direction.py):面板展开时拉 GET /episodes/<ep>/dialogue-direction,逐句列出台词、
   剧本情绪、库里这句的音频,以及可改的演法 / 场景与对象 / 语速档或目标时长;「保存」POST 同一地址(写分镜表
   dialogue_lines[].delivery),保存后该句在库里变过期,点「刷新对白语音」重出。重绘面板时逐句清单的节点原样保留(不丢未保存的输入)。 */
(function(){
  const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const tr=(k,p)=>(window.I18N&&I18N.f)?I18N.f(k,p||{}):Object.keys(p||{}).reduce((s,x)=>s.split('{'+x+'}').join(p[x]),k);
  const t=k=>tr(k,{});
  const POLL={};   // proj/ep -> timer
  let CUR={proj:'',ep:'',st:{}};
  function url(proj,ep,tail){return '/api/v1/projects/'+encodeURIComponent(proj)+'/episodes/'+encodeURIComponent(ep)+'/dialogue-tts'+(tail||'')}
  function html(st,proj,ep){
    st=st||{};CUR={proj,ep,st};
    if(!st.enabled&&!st.error){
      // 未开启「生成对白语音」不显示库面板(2026-09-17);但「声画分离」开着时仍要能逐句改声源(画外 / V.O.),只出逐句清单(2026-10-03)
      if(!(st.sound_split&&st.sound_split!=='off'&&st.has_shot_list))return '';
      return `<div class="doc closed dtts"><h3 onclick="this.parentNode.classList.toggle('closed')">🗣 ${esc(t('对白声源'))} <span class="meta">${esc(t('画内 / 画外 O.S. / V.O.'))}</span></h3><div class="body"><div style="color:var(--dim);font-size:12px">${esc(t('画外句不进组视频,由宿主按人物声线后期合成(code/offscreen_lines.py);「生成对白语音」关闭时此处不显示语音库状态'))}</div><div class="dttslines" data-key="${esc(proj+'/'+ep)}"></div></div></div>`;
    }
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
    return `<div class="doc closed dtts"><h3 onclick="if(!event.target.closest('button'))this.parentNode.classList.toggle('closed')">🗣 ${esc(t('对白语音'))} <span class="meta">${esc(counts)}${chan}</span><span style="margin-left:auto;display:inline-flex;gap:6px;align-items:center;font-size:12px">${line}<button class="editbtn dttsbtn" data-force="0" ${st.running||!st.has_shot_list?'disabled':''} title="${esc(t('按当前台词补合成缺失/过期的句子(按人物嗓音模板,走 TTS 渠道,只合成有变化的句子)'))}">🗣 ${esc(t('刷新对白语音'))}</button><button class="editbtn dttsbtn" data-force="1" ${st.running||!st.has_shot_list?'disabled':''} title="${esc(t('忽略缓存,全部句子重新合成(费用按句计)'))}">↻ ${esc(t('全部重出'))}</button></span></h3><div class="body"><div style="color:var(--dim);font-size:12px">${esc(tr('库文件 assets/audio/voice/{ep}/tts/(逐句 mp3 + tts_manifest.json);用于动态样片、白模样片与后期配音',{ep}))}</div>${reasons}${log}${st.has_shot_list?'<div class="dttslines" data-key="'+esc(proj+'/'+ep)+'"></div>':''}</div></div>`;
  }
  function render(st){
    const box=document.getElementById('dttsbox');if(!box)return;
    const open=!box.querySelector('.doc.closed');
    const keep=box.querySelector('.dttslines');           // 逐句清单节点原样搬过去:轮询重绘不丢正在改的输入
    box.innerHTML=html(st,CUR.proj,CUR.ep);
    const slot=box.querySelector('.dttslines');
    if(keep&&slot&&keep.dataset.key===slot.dataset.key)slot.replaceWith(keep);
    if(open){const d=box.querySelector('.doc');if(d)d.classList.remove('closed');loadLines()}
  }
  /* ---------- 台词演法:逐句清单 ---------- */
  const LINE_ST={ok:'',missing:'还没写演法',stale:'台词改过,演法已作废'};
  const AUDIO_ST={fresh:'库:一致',stale:'库:过期',missing:'库:未合成',unbound:'库:未选角'};
  function durl(){return '/api/v1/projects/'+encodeURIComponent(CUR.proj)+'/episodes/'+encodeURIComponent(CUR.ep)+'/dialogue-direction'}
  function lineHtml(r){
    const inp='background:var(--panel);border:1px solid var(--border);border-radius:5px;color:var(--text);font:12.5px/1.5 inherit;padding:4px 6px';
    const paces=[['','语速'],['fast','快'],['medium','中'],['slow','慢']].map(([v,l])=>`<option value="${v}"${(r.pace||'')===v&&v?' selected':''}>${esc(t(l))}${v?' · '+r.pace_seconds[v]+'s':''}</option>`).join('');
    const warn=LINE_ST[r.status]?`<span style="color:var(--yellow)">${esc(t(LINE_ST[r.status]))}</span>`:'';
    const lib=r.audio_status?t(AUDIO_ST[r.audio_status]||r.audio_status)+(r.duration_s?' '+r.duration_s+'s':''):'';
    // 声画分离(2026-10-03):声源位置 on 画内 / os 画外 O.S. / vo V.O.;画外句后期按人物声线合成,不进组视频
    const pl=PLACES.some(([v])=>v===r.placement)?r.placement:'on';
    const badge=pl==='on'?'':`<span class="dttsbadge" style="color:var(--accent);font-size:12px">${esc(t(pl==='vo'?'👻 V.O.':'👻 画外'))}</span>`;
    const trig=r.placement_reason&&r.placement_reason.trigger?`<span style="color:var(--dim);font-size:11px" title="${esc(r.placement_reason.evidence||'')}">${esc(t('依据'))} ${esc(r.placement_reason.trigger)}</span>`:'';
    const placeOpts=PLACES.map(([v,l])=>`<option value="${v}"${pl===v?' selected':''}>${esc(t(l))}</option>`).join('');
    const fxCur=r.source_fx||(pl==='vo'?'inner':'plain');
    const fxOpts=FXS.map(([v,l])=>`<option value="${v}"${fxCur===v?' selected':''}>${esc(t(l))}</option>`).join('');
    const placeRow=`<div class="dttsplacerow" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:4px;font-size:12px"><span style="color:var(--dim)">${esc(t('声源'))}</span><select class="dttsplace" data-orig="${pl}" style="${inp}">${placeOpts}</select>
        <span class="dttsfxwrap" style="display:${pl==='on'?'none':'inline-flex'};gap:6px;align-items:center"><span style="color:var(--dim)">${esc(t('声源效果'))}</span><select class="dttsfx" style="${inp}">${fxOpts}</select>
        <span style="color:var(--dim)">${esc(t('偏移(s)'))}</span><input class="dttsoffset" type="number" step="0.1" min="0" value="${r.offset_s??''}" placeholder="0.4" style="${inp};width:64px"></span>${trig}</div>`;
    return `<div class="dttsline" data-shot="${esc(r.shot_id)}" data-idx="${r.idx}" data-pace='${esc(JSON.stringify(r.pace_seconds))}' data-limit="${r.limit_s??''}" style="border-top:1px solid var(--border);padding:8px 0">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><b>${esc(r.shot_id)}</b>${badge}<span>${esc(r.speaker_name)}</span>${r.emotion?`<span style="color:var(--yellow);font-size:12px">${esc(r.emotion)}</span>`:''}<span>「${esc(r.text)}」</span></div>
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:4px 0;font-size:12px;color:var(--dim)">${r.audio?`<audio controls preload="none" src="${esc(r.audio)}" style="height:28px;max-width:260px"></audio>`:''}<span>${esc(lib)}</span><span>${esc(tr('镜长 {s}s',{s:r.shot_duration_s??'?'}))}</span>${warn}</div>
      <textarea class="dttsdir" rows="2" placeholder="${esc(t('演法:状态 + 怎么演 + 语速 / 音量 / 停顿'))}" style="${inp};width:100%;resize:vertical">${esc(r.direction)}</textarea>
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-top:4px"><input class="dttsscene" type="text" value="${esc(r.scene)}" placeholder="${esc(t('场景与对象:在哪、对谁说、刚发生了什么'))}" style="${inp};flex:1;min-width:220px">
        <select class="dttspace" style="${inp}">${paces}</select><input class="dttstarget" type="number" step="0.1" min="0.3" value="${r.target_s??''}" title="${esc(t('目标时长(秒)'))}" style="${inp};width:70px"><span style="font-size:12px;color:var(--dim)">${esc(t('秒'))}</span>
        <button class="editbtn dttssave">${esc(t('保存'))}</button><span class="dttsmsg" style="font-size:12px"></span></div>${placeRow}</div>`;
  }
  const PLACES=[['on','画内'],['os','画外 O.S.'],['vo','V.O.']];
  const FXS=[['plain','原声'],['phone','电话'],['door','隔门'],['distance','远处'],['inner','内心'],['memory','回忆']];
  function headHtml(d){
    const hint=d.timed?t('逐句演法与目标时长,对白语音按它合成;改完保存,再点「刷新对白语音」重出这一句')
                      :t('逐句演法;当前 TTS 渠道只把演法当语气指令,场景与目标时长用不上');
    return `<b>${esc(t('台词演法'))}</b> <span style="color:var(--dim);font-size:12px">${esc(tr('{b}/{n} 句已写',{b:d.bound,n:d.total}))} · ${esc(hint)}</span>`;
  }
  function drawLines(box,d){
    box.innerHTML=`<div class="dttshead" style="margin:10px 0 2px;font-size:13px">${headHtml(d)}</div>`
      +(d.lines||[]).filter(r=>r.speaker).map(lineHtml).join('');
    box.dataset.loaded='1';
  }
  async function loadLines(force){
    const box=document.querySelector('#dttsbox .dttslines');
    if(!box||(box.dataset.loaded&&!force))return;
    box.dataset.loaded='1';
    box.innerHTML=`<div style="color:var(--dim);font-size:12px;margin-top:8px">${esc(t('加载中…'))}</div>`;
    try{const r=await fetch(durl());const j=await r.json();if(!r.ok)throw new Error(j.detail||j.error||r.status);drawLines(box,j)}
    catch(err){box.dataset.loaded='';box.innerHTML=`<div style="color:var(--red);font-size:12px;margin-top:8px">${esc(tr('加载失败:{e}',{e:err.message}))}</div>`}
  }
  document.addEventListener('click',e=>{                    // 展开面板时才拉逐句清单
    if(e.target.closest('#dttsbox .doc>h3')&&!e.target.closest('button'))setTimeout(()=>{if(!document.querySelector('#dttsbox .doc.closed'))loadLines()},0);
  });
  document.addEventListener('change',e=>{                   // 声源切到画外/V.O. 才露出声源效果与偏移
    const sel=e.target.closest('.dttsplace');if(!sel)return;
    const w=sel.closest('.dttsline').querySelector('.dttsfxwrap');if(w)w.style.display=sel.value==='on'?'none':'inline-flex';
  });
  document.addEventListener('change',e=>{                   // 选语速档:按字数算出的秒数填进目标时长(不超过本镜可用)
    const sel=e.target.closest('.dttspace');if(!sel||!sel.value)return;
    const row=sel.closest('.dttsline');let v=JSON.parse(row.dataset.pace||'{}')[sel.value];
    const lim=parseFloat(row.dataset.limit);if(v&&lim&&v>lim)v=lim;
    if(v)row.querySelector('.dttstarget').value=v;
  });
  document.addEventListener('click',async e=>{
    const b=e.target.closest('.dttssave');if(!b||b.disabled)return;
    const row=b.closest('.dttsline'),msg=row.querySelector('.dttsmsg');b.disabled=true;msg.textContent='';
    const target=row.querySelector('.dttstarget').value;
    const body={shot_id:row.dataset.shot,idx:+row.dataset.idx,direction:row.querySelector('.dttsdir').value,
        scene:row.querySelector('.dttsscene').value,pace:row.querySelector('.dttspace').value,target_s:target===''?null:+target};
    const ps=row.querySelector('.dttsplace');
    if(ps&&(ps.value!==ps.dataset.orig||ps.value!=='on')){   // 声源字段只在改了或本就是画外时随包发(走 offscreen_lines.set_line)
      body.placement=ps.value;
      if(ps.value!=='on'){body.source_fx=row.querySelector('.dttsfx').value;const off=row.querySelector('.dttsoffset').value;if(off!=='')body.offset_s=+off}
    }
    try{
      const r=await fetch(durl(),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const j=await r.json().catch(()=>({}));
      if(!r.ok)throw new Error(j.detail||j.error||r.status);
      // 只重绘这一句和标题计数:别的句子里没保存的输入不动
      const cur=(j.lines||[]).find(r=>r.shot_id===row.dataset.shot&&r.idx===+row.dataset.idx);
      const head=document.querySelector('#dttsbox .dttshead');if(head)head.innerHTML=headHtml(j);
      let fresh=row;
      if(cur){const tmp=document.createElement('div');tmp.innerHTML=lineHtml(cur);fresh=tmp.firstElementChild;row.replaceWith(fresh)}
      const ok=fresh.querySelector('.dttsmsg');ok.style.color='var(--green)';
      ok.textContent=(j.notes||[]).length?j.notes.join(';'):t('已保存,点「刷新对白语音」重出这一句');
      refresh(CUR.proj,CUR.ep);
    }catch(err){b.disabled=false;msg.style.color='var(--red)';msg.textContent=tr('保存失败:{e}',{e:err.message})}
  });
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
    es.onmessage=ev=>{try{const d=JSON.parse(ev.data);if(d.type==='dialogue_tts'&&d.project===CUR.proj&&d.ep===CUR.ep){
      // 同步结束:库里的音频换了,逐句清单也重拉(演法保存自己已重绘,不再重拉以免冲掉别句未保存的输入)
      refresh(CUR.proj,CUR.ep).then(()=>{if(d.status!=='direction'&&d.status!=='running'&&!document.querySelector('#dttsbox .doc.closed'))loadLines(true)})}}catch(_){}};
  }catch(_){}
  window.DialogueTTS={html,render,refresh,poll};
})();
