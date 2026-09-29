/* 白模样片(整集摄影机视角白模视频 + 对白/旁白字幕,2026-09-11;原名白模合辑)
   分镜预览页使用(成片发布页 2026-09-13 起不再展示白模样片板块):派单文案、运行中状态(sessionStorage 跨页保留)、运行轮询、状态行 HTML。
   文件由白模调度 Agent 用宿主 CLI code/concat_whitebox.py 生成;按钮把指令派给该 Agent(POST /api/v1/runs)。 */
(function(){
  const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const key=(proj,ep)=>'wb-reel-run:'+proj+'/'+ep;
  const tr=(k,p)=>(window.I18N&&I18N.f)?I18N.f(k,p||{}):Object.keys(p||{}).reduce((s,x)=>s.split('{'+x+'}').join(p[x]),k);
  const AGENT='07-directing/whitebox-staging';
  function runId(proj,ep){try{return sessionStorage.getItem(key(proj,ep))||null}catch(_){return null}}
  function setRun(proj,ep,id){try{if(id)sessionStorage.setItem(key(proj,ep),id);else sessionStorage.removeItem(key(proj,ep))}catch(_){}}
  // 指令用中文写给 Agent:合并本集全部分镜组 camera.mp4 为整集视频并烧入对白/旁白字幕,缺组先补出;回执报组数/时长/字幕/缺组
  function message(proj,ep,w){
    w=w||{};
    return [`请重新生成 ${ep} 的白模样片(整集摄影机视角白模视频,烧入对白/旁白字幕)。`,
      `执行宿主 CLI:python code/concat_whitebox.py --project ${proj} --ep ${ep}`,
      `把本集全部分镜组的 assets/whitebox/${ep}/<grp>/camera.mp4 按 shot_list 组序合并为一份整集视频 assets/whitebox/${ep}/${ep}-camera.mp4(方便连续查看);字幕由 CLI 按 shot_list 对白与 narration.md 旁白自动烧入,不要另写字幕;项目开启「生成对白语音」时 CLI 会自动同步对白语音库并挂对白轨,不要另行合成语音。`,
      (w.groups_missing||[]).length?`当前缺少 ${w.groups_missing.length} 组 camera.mp4(${w.groups_missing.join(', ')}),请先按规约用 python code/render_whitebox.py --project ${proj} --ep ${ep} 补出这些组再合并;确实无法补出的组才用 --allow-missing 跳过并在回执说明。`
        :'各组 camera.mp4 已齐,直接合并即可;若合并前发现有组视频过期,先用 render_whitebox.py 重出该组。',
      '回执报告:样片路径、包含组数、总时长、字幕条数(对白/旁白)、缺失/跳过的组。'].join('\n');
  }
  async function dispatch(proj,ep,w){
    const r=await fetch('/api/v1/runs',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({agent:(w&&w.agent)||AGENT,message:message(proj,ep,w),project:proj,source:'user'})});
    const j=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(j.detail||j.error||r.status);
    const id=j.run_id||'?';setRun(proj,ep,id);return id;
  }
  // 轮询运行状态直到结束;结束后清跨页状态并回调(页面据此重拉数据刷新板块)
  async function poll(proj,ep,id,onDone){
    let misses=0;   // 运行记录已不存在(404)或连续取不到状态 → 视为结束,避免跨页残留的 run id 永远显示运行中
    while(runId(proj,ep)===id){
      await new Promise(r=>setTimeout(r,4000));
      let st='';
      try{const r=await fetch('/api/v1/runs/'+encodeURIComponent(id));if(r.ok){st=((await r.json()).status||'');misses=0}else if(r.status===404)misses=99;else misses++}catch(_){misses++}
      if((st&&st!=='queued'&&st!=='running')||misses>=8){setRun(proj,ep,null);if(onDone)onDone(st||'unknown');return}
    }
  }
  // 状态行:运行中 / 错误 / 过期(组视频变了 | 字幕文本变了) / 一致;缺组另加一段
  function statusHtml(w,running){
    w=w||{};let st='';
    if(running)st=`<span class="warn">⏳ ${esc(tr('已派单给白模调度 Agent,运行中…(run {id})',{id:running}))}</span>`;
    else if(w.error)st=`<span class="err">${esc(w.error)}</span>`;
    else if(w.stale&&w.stale_reason==='subtitles')st=`<span class="warn">⚠ ${esc(tr('对白/旁白字幕已变,样片需重出'))}</span>`;
    else if(w.stale&&w.stale_reason==='audio')st=`<span class="warn">⚠ ${esc(tr('对白语音已变(台词或音色更新),样片需重出'))}</span>`;
    else if(w.stale)st=`<span class="warn">⚠ ${esc(tr('分镜组白模视频已更新,样片可能过期'))}</span>`;
    else if(w.exists)st=`<span class="ok">✓ ${esc(tr('样片与各组白模视频一致'))}</span>`;
    if((w.groups_missing||[]).length)st+=` <span class="warn">${esc(tr('缺 {n} 组 camera.mp4:{list}',{n:w.groups_missing.length,list:w.groups_missing.slice(0,6).join(', ')+(w.groups_missing.length>6?'…':'')}))}</span>`;
    return st;
  }
  // 播放卡下的规格串:MB · 时长 · 组数 · 字幕条数 · 宽高 · fps
  function metaList(w){
    w=w||{};const sub=w.subtitles||{};
    return [w.size_mb!=null?w.size_mb+' MB':'',w.duration_s?Math.round(w.duration_s)+'s':'',w.groups?tr('{n} 组',{n:w.groups}):'',
      sub.cues?tr('字幕 {n} 条(对白 {d} · 旁白 {r})',{n:sub.cues,d:sub.dialogue||0,r:sub.narration||0}):tr('无字幕'),
      w.audio&&w.audio.kind==='dialogue_tts'&&w.audio.lines?tr('对白语音 {n} 句',{n:w.audio.lines||0})+(w.audio.overflow&&w.audio.overflow.length?' ⚠'+tr('{k} 句超出镜长',{k:w.audio.overflow.length}):''):tr('无声'),
      w.width&&w.height?w.width+'×'+w.height:'',w.fps?w.fps+'fps':''].filter(Boolean);
  }
  window.WhiteboxReel={AGENT,runId,setRun,message,dispatch,poll,statusHtml,metaList};
})();
