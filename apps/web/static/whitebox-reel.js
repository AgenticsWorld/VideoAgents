/* 白模样片(整集摄影机视角白模视频 + 对白/旁白字幕,2026-09-11;原名白模合辑)
   分镜预览页使用(成片发布页 2026-09-13 起不再展示白模样片板块):派单文案、运行中状态(sessionStorage 跨页保留)、运行轮询、状态行 HTML。
   文件由白模调度 Agent 用宿主 CLI code/concat_whitebox.py 生成;按钮把指令派给该 Agent(POST /api/v1/runs)。
   2026-10-10:H3W 签字前也能出——CLI 自动补预览版组视频(不算正式导出),状态行标「预览版」;白模在样片之后改过则提示重出。 */
(function(){
  const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const key=(proj,ep)=>'wb-reel-run:'+proj+'/'+ep;
  const tr=(k,p)=>(window.I18N&&I18N.f)?I18N.f(k,p||{}):Object.keys(p||{}).reduce((s,x)=>s.split('{'+x+'}').join(p[x]),k);
  const AGENT='07-directing/whitebox-staging';
  function runId(proj,ep){try{return sessionStorage.getItem(key(proj,ep))||null}catch(_){return null}}
  function setRun(proj,ep,id){try{if(id)sessionStorage.setItem(key(proj,ep),id);else sessionStorage.removeItem(key(proj,ep))}catch(_){}}
  // 指令用中文写给 Agent:CLI 先补出缺失/过期组的预览版组视频(H3W 签字前也能跑,不算正式导出),再合并为整集视频并烧入对白/旁白字幕;回执报组数/时长/字幕/预览版组/缺组
  function message(proj,ep,w){
    return [`请重新生成 ${ep} 的白模样片(整集摄影机视角白模视频,烧入对白/旁白字幕)。`,
      `执行宿主 CLI:python code/concat_whitebox.py --project ${proj} --ep ${ep}`,
      `CLI 会先按当前白模补出缺失或已过期组的预览版摄影机视角视频(本机渲染到 directing/${ep}/whitebox/preview/<grp>/,不写 assets/whitebox 的正式 manifest、不接进 prompt、没有生成费用;H3W 签字前后都可以跑,不算正式导出),再把各组视频按 shot_list 组序合并为一份整集视频 assets/whitebox/${ep}/${ep}-camera.mp4(方便连续查看);字幕由 CLI 按 shot_list 对白与 narration.md 旁白自动烧入,不要另写字幕;项目开启「生成对白语音」时 CLI 会自动同步对白语音库并挂对白轨,不要另行合成语音。`,
      `不要为了出样片去跑 render_whitebox.py 做正式导出(那是 H3W 签字后导出工单的事),也不要自己改白模计划。回执 preview_videos.compile_errors 里有组编译报错补不出时如实上报;确实无法补出的组才用 --allow-missing 跳过并在回执说明。`,
      '回执报告:样片路径、包含组数、总时长、字幕条数(对白/旁白)、其中预览版组数、缺失/跳过的组。'].join('\n');
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
  // 状态行:运行中 / 错误 / 过期(组视频变了 | 白模改了 | 字幕文本变了 | 对白语音变了) / 一致;另加「预览版」说明与尚无视频的组
  function statusHtml(w,running){
    w=w||{};let st='';
    const prev=(w.preview_groups||[]).length,miss=w.groups_missing||[];
    if(running)st=`<span class="warn">⏳ ${esc(tr('已派单给白模调度 Agent,运行中…(run {id})',{id:running}))}</span>`;
    else if(w.error)st=`<span class="err">${esc(w.error)}</span>`;
    else if(w.stale&&w.stale_reason==='whitebox')st=`<span class="warn">⚠ ${esc(tr('白模已改({n} 组),样片需重出',{n:(w.whitebox_changed||[]).length}))}</span>`;
    else if(w.stale&&w.stale_reason==='subtitles')st=`<span class="warn">⚠ ${esc(tr('对白/旁白字幕已变,样片需重出'))}</span>`;
    else if(w.stale&&w.stale_reason==='audio')st=`<span class="warn">⚠ ${esc(tr('对白语音已变(台词或音色更新),样片需重出'))}</span>`;
    else if(w.stale)st=`<span class="warn">⚠ ${esc(tr('分镜组白模视频已更新,样片可能过期'))}</span>`;
    else if(w.exists)st=`<span class="ok">✓ ${esc(tr('样片与各组白模视频一致'))}</span>`;
    // 预览版:H3W 签字前渲染的组视频,未正式导出、未接进提示词(签字后导出时指纹没变会直接转正)
    if(prev)st+=` <span class="warn" title="${esc(tr('预览版组视频只供审看:未正式导出、未接入视频提示词;H3W 签字后导出时,白模没改过的组直接转为正式版'))}">${esc(tr('预览版 · {n} 组未正式导出',{n:prev}))}</span>`;
    if(miss.length)st+=` <span class="warn">${esc(tr('{n} 组尚无白模视频(生成样片时自动补出):{list}',{n:miss.length,list:miss.slice(0,6).join(', ')+(miss.length>6?'…':'')}))}</span>`;
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
