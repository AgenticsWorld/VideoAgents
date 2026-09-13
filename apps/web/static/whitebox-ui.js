import { projectRenderFormat } from './whitebox-format.js?v=20260908-bedfoot-pour';
import { wbText as t, wbMessage } from './whitebox-i18n.js?v=20260908-bedfoot-pour';
let rendererModule;
const loadRenderer=()=>rendererModule ||= import('./whitebox-renderer.js?v=20260909-realplate');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api=project=>'/api/v1/projects/'+encodeURIComponent(project)+'/whitebox';
const artifactBase=project=>'/api/v1/projects/'+encodeURIComponent(project)+'/artifacts/';
const panels=new Map();
// 请求加超时 + 一次重试:同源 HTTP/1.1 连接被 SSE 长连接/媒体请求占满时,fetch 会在浏览器里无限期排队,
// 不加超时面板会永远停在「正在加载…」且没有任何报错;超时/网络错误重试一次,仍失败交给调用方显示可重试的错误
const FETCH_TIMEOUT_MS=15000;
async function json(url,options={}){
  for(let attempt=0;;attempt++){
    const ac=new AbortController();
    const timer=setTimeout(()=>ac.abort(),FETCH_TIMEOUT_MS);
    try{
      const r=await fetch(url,{...options,signal:ac.signal});
      const d=await r.json();
      if(!r.ok){const e=Error(d.detail||r.statusText);e.status=r.status;throw e;}
      return d;
    }catch(e){
      const transient=e.name==='AbortError'||e.name==='TypeError';
      if(transient&&attempt<1)continue;
      if(e.name==='AbortError')throw Error(t('白模数据请求超时（{seconds} 秒）',{seconds:Math.round(FETCH_TIMEOUT_MS/1000)}));
      throw e;
    }finally{clearTimeout(timer);}
  }
}
async function previewData(url,fallback,minStagingVersion=0){
  let current;
  try{current=await json(url);}catch(e){if(e.status!==404)throw e;return {...await json(fallback),requires_service_restart:true};}
  if((current.staging_version||0)<minStagingVersion){
    // A running pre-upgrade service can compile actors but drop extras/props.
    // Prefer the newly compiled project artifact until that service restarts.
    try{const compiled=await json(fallback);if((compiled.staging_version||0)>=minStagingVersion)return {...compiled,requires_service_restart:true};}
    catch(e){if(e.status!==404)throw e;}
  }
  return current;
}

// 「状态」弹出层:点面板外任意处收起
document.addEventListener('click',e=>{if(!e.target.closest('.wb-statuswrap'))document.querySelectorAll('.wb-statuspop.open').forEach(x=>{x.classList.remove('open');x.previousElementSibling?.setAttribute('aria-expanded','false');});});
async function mount(host,project,scene,group=null,ep='') {
  const settings=await json(`/api/v1/projects/${encodeURIComponent(project)}/config`);
  if(!host.isConnected)return;
  const format=projectRenderFormat(settings);
  const fileState=value=>value===true?t('已有'):value===false?t('未生成'):t('未知');
  const statusItems=[t('场景模型：{state}',{state:fileState(scene.artifact_status?.model)})];
  if(group)statusItems.push(
    t('调度计划：{state}',{state:fileState(group.artifact_status?.plan)}),
    t('预览文件：{state}',{state:fileState(group.artifact_status?.preview)}),
    t('参考视频：{state}',{state:fileState(group.artifact_status?.video)}));
  host.className='wb-panel '+(group?'wb-group':'wb-scene');
  host.style.setProperty('--wb-aspect',`${format.width} / ${format.height}`);
  host.style.setProperty('--wb-preview-width',`${(group?315:460)*format.width/format.height}px`);
  const editLocation=group?t('3D 白模 {marker}（场景 {scene}；镜头 {shots}；调度 {plan}；模型 {model}；预览 {preview}）', {
    marker:`[whitebox:${project}/${ep}/${group.group_id}]`,scene:scene.scene_id,
    shots:group.cameras.map(c=>c.shot_id).join(', '),plan:`directing/${ep}/whitebox_plans/${group.group_id}.json`,
    model:`bible/scenes/${scene.scene_id}/whitebox.json`,preview:`directing/${ep}/whitebox/episode.json`,
  }):'';
  host.innerHTML=`<div class="wb-toolbar"><div class="wb-heading"><strong>${esc(t('3D 白模'))} · ${esc(scene.scene_id)}${group?' / '+esc(group.group_id):''}</strong>${group?`<button class="editbtn wb-edit" data-loc="${esc(editLocation)}" data-agent="07-directing/whitebox-staging" title="${esc(t('对这组3D白模提修改意见，直接发消息给白模调度 Agent'))}">${esc(t('✏️ 编辑'))}</button>`:''}</div><div class="wb-statuswrap"><button type="button" class="wb-statusbtn" aria-expanded="false" title="${esc(t('查看场景模型 / 调度计划 / 预览文件 / 参考视频的文件状态'))}">${esc(t('状态'))}</button><div class="wb-statuspop" role="status">${statusItems.map(item=>`<span>${esc(item)}</span>`).join('')}${!scene.artifact_status||(group&&!group.artifact_status)?`<span class="wb-statusnote">${esc(t('文件状态未知，请重启服务后刷新。'))}</span>`:''}</div></div><select aria-label="${esc(t('空间视角'))}"><option value="top" selected>${esc(t('俯视图'))}</option><option value="real">${esc(t('实景图'))}</option><option value="overview">${esc(t('旋转视角'))}</option></select></div>
    <div class="wb-status">${esc(t('{width} × {depth} m · 高 {height} m · 网格 1 m · 画幅 {aspect}',{width:scene.dimensions_m[0],depth:scene.dimensions_m[2],height:scene.dimensions_m[1],aspect:format.aspect_ratio}))} · ${esc(t(scene.inferred?'推断尺寸':'已标定尺寸'))} · ${esc(t('切换旋转视角后可拖动旋转、滚轮缩放'))}</div>
    <div class="wb-views"><figure><canvas class="wb-space" aria-label="${esc(t('场景白模'))}"></canvas><figcaption>${esc(t('空间与摄像机位置'))}</figcaption></figure>${group?`<figure><canvas class="wb-camera" aria-label="${esc(t('摄像机白模'))}"></canvas><figcaption>${esc(t('摄像机视角'))}</figcaption></figure>`:''}</div>
    ${group?`<div class="wb-transport"><button class="wb-play">${esc(t('播放'))}</button><input type="range" aria-label="${esc(t('白模时间'))}" min="0" max="${group.duration_s}" step="0.01" value="0"><span class="wb-time"></span></div><div class="wb-cast" data-no-i18n>${[...group.actors,...(group.extras||[])].map(a=>`<span><i class="wb-color" style="background:${esc(a.color)}"></i>${esc(a.label)} (${esc(a.id)}) · ${a.size_m[1]} m</span>`).join('')}</div>`:''}
    <details class="wb-warnings"><summary>${esc(t('建模依据与检查 ({count})',{count:(group?.warnings||scene.warnings).length}))}</summary><div data-no-i18n>${esc(scene.scale_basis)}</div>${(group?.warnings||scene.warnings).map(w=>`<div data-no-i18n>${esc(wbMessage(w))}</div>`).join('')}</details><div class="wb-status wb-result" role="status"></div>`;
  const statusBtn=host.querySelector('.wb-statusbtn'), statusPop=host.querySelector('.wb-statuspop');
  statusBtn.onclick=e=>{e.stopPropagation();const open=!statusPop.classList.contains('open');document.querySelectorAll('.wb-statuspop.open').forEach(x=>x.classList.remove('open'));statusPop.classList.toggle('open',open);statusBtn.setAttribute('aria-expanded',String(open));};
  let space, camera, frame, disposed=false, playing=false, time=0, previous=0;
  const dispose=()=>{if(disposed)return;disposed=true;cancelAnimationFrame(frame);space?.dispose();camera?.dispose();panels.delete(host);};
  panels.set(host,dispose);
  try {
    const {WhiteboxRenderer}=await loadRenderer();if(disposed||!host.isConnected)return dispose();
    space=new WhiteboxRenderer(host.querySelector('.wb-space'),format);space.assetBase=artifactBase(project);space.load(scene,group);
    if(group){camera=new WhiteboxRenderer(host.querySelector('.wb-camera'),{...format,controls:false});camera.load(scene,group);}
  } catch(e){host.querySelector('.wb-result').textContent=t('3D 加载失败：{error}',{error:wbMessage(e.message)});dispose();return;}
  const render=()=>{
    space.setTime(time);space.render(host.querySelector('select').value);
    if(camera){camera.setTime(time);camera.render('camera');host.querySelector('.wb-time').textContent=`${time.toFixed(2)} / ${group.duration_s.toFixed(2)} s · ${camera.shotId}`;host.querySelector('input').value=time;}
  };
  const loop=now=>{if(disposed)return;if(!host.isConnected)return dispose();
    if(playing){time=Math.min(group.duration_s,time+(now-previous)/1000);if(time>=group.duration_s){playing=false;host.querySelector('.wb-play').textContent=t('重播');}}
    previous=now;render();frame=requestAnimationFrame(loop);
  };frame=requestAnimationFrame(loop);
  if(group){
    host.querySelector('.wb-play').onclick=()=>{if(time>=group.duration_s)time=0;playing=!playing;previous=performance.now();host.querySelector('.wb-play').textContent=t(playing?'暂停':'播放');};
    host.querySelector('input').oninput=e=>{time=Number(e.target.value);playing=false;host.querySelector('.wb-play').textContent=t('播放');render();};
  }
}

export async function mountScene(host,project,sid){
  host.className='wb-panel';host.textContent=t('正在加载场景白模…');
  try{const scene=await previewData(`${api(project)}/scenes/${encodeURIComponent(sid)}`,`/api/v1/projects/${encodeURIComponent(project)}/artifacts/assets/concepts/scenes/${encodeURIComponent(sid)}/whitebox.scene.json`);if(host.isConnected)await mount(host,project,scene);}
  catch(e){host.textContent=t('场景白模尚不可用：{error}',{error:wbMessage(e.message)});}
}

let episodeCache=null;
const settingsCache=new Map();
const episodeData=(project,ep)=>{
  const key=project+'/'+ep;
  if(!episodeCache||episodeCache.key!==key)episodeCache={key,at:Date.now(),promise:previewData(`${api(project)}/${encodeURIComponent(ep)}`,`/api/v1/projects/${encodeURIComponent(project)}/artifacts/directing/${encodeURIComponent(ep)}/whitebox/episode.json`,3)};
  return episodeCache.promise;
};
const projectSettings=project=>{
  if(!settingsCache.has(project))settingsCache.set(project,json(`/api/v1/projects/${encodeURIComponent(project)}/config`).catch(e=>{settingsCache.delete(project);throw e;}));
  return settingsCache.get(project);
};

// ---- 分镜预览:每个生成组的镜卡列在左半,右半常驻该组的白模面板
// (播放 → 摄像机视角 → 空间与摄像机位置[旋转视角] → 人物色标 → 待决项;2026-09-13 起不再有视图选择与「建模依据与检查」)。全页共用一个 WebGL 上下文,
// 面板只在滚入视口附近时建 3D 场景、滚出即释放;单个 rAF 循环驱动全部面板,静止且无交互时不重绘 ----
const groupPanels=new Map();   // host -> state
let pending=new Map();         // host -> build promise(每次 mountGroups 重建)
let groupObserver=null, groupFrame=0;
function groupLoop(now){
  groupFrame=0;
  const active=[...groupPanels.values()].filter(st=>st.rr);
  if(!active.length)return;
  for(const st of active){
    if(!st.host.isConnected){releaseGroup(st);continue;}
    if(st.playing){
      st.time=Math.min(st.end,st.time+(now-st.previous)/1000);
      if(st.time>=st.end){st.playing=false;st.els.play.textContent=t('重播');}
      st.dirty=true;
    }
    st.previous=now;
    if(!st.dirty)continue;
    st.dirty=false;
    st.rr.setTime(st.time);
    st.rr.render('camera',st.els.camera);
    st.rr.render('overview',st.els.space);   // 空间视图固定旋转视角(2026-09-13 起不再提供俯视图/实景图切换),可拖动旋转、滚轮缩放
    st.els.time.textContent=`${st.time.toFixed(2)} / ${st.end.toFixed(2)} s · ${st.rr.shotId}`;
    st.els.range.value=st.time;
  }
  groupFrame=requestAnimationFrame(groupLoop);
}
const kick=()=>{if(!groupFrame)groupFrame=requestAnimationFrame(groupLoop);};
function releaseGroup(st){st.rr?.dispose();st.rr=null;st.playing=false;}
// 数据请求失败(超时/断网/服务端错误)显示错误 + 「重试」按钮;重试把该组恢复成占位并重新交给观察器建面板
function failGroup(host,message){
  host.innerHTML=`<div class="wb-fail">${esc(message)} <button type="button" class="editbtn wb-retry">${esc(t('重试'))}</button></div>`;
  host.querySelector('.wb-retry').onclick=()=>retryGroup(host);
}
function retryGroup(host){
  pending.delete(host);groupPanels.delete(host);
  host.textContent=t('正在加载分镜组白模…')+' '+t('如果长时间未完成加载，点击右上角刷新按钮');
  if(groupObserver){groupObserver.unobserve(host);groupObserver.observe(host);}
}
async function acquireGroup(st){
  if(st.rr||st.loading||!st.ready)return;
  st.loading=true;
  try{
    const {WhiteboxRenderer,sharedRenderer}=await loadRenderer();
    if(!st.host.isConnected||st.rr||!groupPanels.has(st.host))return;
    st.rr=new WhiteboxRenderer(st.els.space,{...st.format,renderer:sharedRenderer()});
    st.rr.assetBase=artifactBase(st.project);st.rr.onRealPlate=()=>{st.dirty=true;kick();};
    st.rr.load(st.scene,st.group);
    st.rr.controls?.addEventListener('change',()=>{st.dirty=true;kick();});
    st.dirty=true;st.previous=performance.now();kick();
  }catch(e){st.els.result.textContent=t('3D 加载失败：{error}',{error:wbMessage(e.message)});releaseGroup(st);}
  finally{st.loading=false;}
}
async function buildGroup(host,project,ep){
  const gid=host.dataset.grp;
  const st={host,project,rr:null,loading:false,ready:false,playing:false,dirty:false,time:0,end:0,previous:0,els:{}};
  groupPanels.set(host,st);
  let data,settings;
  try{[data,settings]=await Promise.all([episodeData(project,ep),projectSettings(project)]);}
  catch(e){episodeCache=null;if(host.isConnected)failGroup(host,t('白模加载失败：{error}',{error:wbMessage(e.message)}));return st;}
  if(!host.isConnected||!groupPanels.has(host))return st;
  const group=data.groups.find(g=>g.group_id===gid);
  if(!group){host.textContent=t('白模加载失败：{error}',{error:wbMessage(data.errors?.find(e=>e.group_id===gid)?.error||t('没有本组白模数据'))});return st;}
  let format;
  try{format=projectRenderFormat(settings);}catch(e){host.textContent=wbMessage(e.message);return st;}
  const scene=data.scenes[group.scene_id];
  const fileState=value=>value===true?t('已有'):value===false?t('未生成'):t('未知');
  const statusItems=[t('场景模型：{state}',{state:fileState(scene.artifact_status?.model)}),
    t('调度计划：{state}',{state:fileState(group.artifact_status?.plan)}),
    t('预览文件：{state}',{state:fileState(group.artifact_status?.preview)}),
    t('参考视频：{state}',{state:fileState(group.artifact_status?.video)})];
  const editLocation=t('3D 白模 {marker}（场景 {scene}；镜头 {shots}；调度 {plan}；模型 {model}；预览 {preview}）',{
    marker:`[whitebox:${project}/${ep}/${gid}]`,scene:scene.scene_id,shots:group.cameras.map(c=>c.shot_id).join(', '),
    plan:`directing/${ep}/whitebox_plans/${gid}.json`,model:`bible/scenes/${scene.scene_id}/whitebox.json`,preview:`directing/${ep}/whitebox/episode.json`});
  host.className='wb-panel wb-host';host.style.minHeight='';
  host.style.setProperty('--wb-aspect',`${format.width} / ${format.height}`);
  host.innerHTML=`<div class="wb-toolbar"><div class="wb-heading"><strong>${esc(t('3D 白模'))} · ${esc(scene.scene_id)} / ${esc(gid)}</strong><button class="editbtn wb-edit" data-loc="${esc(editLocation)}" data-agent="07-directing/whitebox-staging" title="${esc(t('对这组3D白模提修改意见，直接发消息给白模调度 Agent'))}">${esc(t('✏️ 编辑'))}</button><a class="editbtn wb-director" href="/preview/director?project=${encodeURIComponent(project)}&ep=${encodeURIComponent(ep)}&grp=${encodeURIComponent(gid)}" title="${esc(t('到导演台:大视窗看白模,逐对象写修改注释,批量提交并按版本 A|B 比对'))}">${esc(t('📺 导演台'))}</a></div><div class="wb-statuswrap"><button type="button" class="wb-statusbtn" aria-expanded="false" title="${esc(t('查看场景模型 / 调度计划 / 预览文件 / 参考视频的文件状态'))}">${esc(t('状态'))}</button><div class="wb-statuspop" role="status">${statusItems.map(item=>`<span>${esc(item)}</span>`).join('')}${!scene.artifact_status||!group.artifact_status?`<span class="wb-statusnote">${esc(t('文件状态未知，请重启服务后刷新。'))}</span>`:''}</div></div></div>
    <div class="wb-transport"><button class="wb-play">${esc(t('播放'))}</button><input type="range" aria-label="${esc(t('白模时间'))}" min="0" max="${group.duration_s}" step="0.01" value="0"><span class="wb-time"></span></div>
    <figure><canvas class="wb-camera" aria-label="${esc(t('摄像机白模'))}"></canvas><figcaption>${esc(t('摄像机视角'))}</figcaption></figure>
    <figure><canvas class="wb-space" aria-label="${esc(t('场景白模'))}"></canvas><figcaption><span>${esc(t('空间与摄像机位置'))}</span></figcaption></figure>
    <div class="wb-cast" data-no-i18n>${[...group.actors,...(group.extras||[])].map(a=>`<span><i class="wb-color" style="background:${esc(a.color)}"></i>${esc(a.label)} (${esc(a.id)}) · ${a.size_m[1]} m</span>`).join('')}</div>
    <div class="wb-issues"></div>
    <div class="wb-status wb-result" role="status"></div>`;
  const q=x=>host.querySelector(x);
  Object.assign(st,{ready:true,scene,group,format,end:group.duration_s,
    els:{camera:q('.wb-camera'),space:q('.wb-space'),play:q('.wb-play'),range:q('input'),time:q('.wb-time'),result:q('.wb-result')}});
  for(const c of [st.els.camera,st.els.space]){c.width=format.width;c.height=format.height;}
  const statusBtn=q('.wb-statusbtn'), statusPop=q('.wb-statuspop');
  statusBtn.onclick=e=>{e.stopPropagation();const open=!statusPop.classList.contains('open');document.querySelectorAll('.wb-statuspop.open').forEach(x=>x.classList.remove('open'));statusPop.classList.toggle('open',open);statusBtn.setAttribute('aria-expanded',String(open));};
  st.els.play.onclick=()=>{if(!st.rr)return;if(st.time>=st.end)st.time=0;st.playing=!st.playing;st.previous=performance.now();st.els.play.textContent=t(st.playing?'暂停':'播放');kick();};
  st.els.range.oninput=e=>{st.time=Number(e.target.value);st.playing=false;st.els.play.textContent=t('播放');st.dirty=true;kick();};
  st.ep=ep;st.issues=q('.wb-issues');renderIssues(st);
  return st;
}

// ---- 待决项(docs/whitebox.md「待决项与用户裁决」,2026-09-09):白模调度 Agent 拿不准的取舍列在组面板,
// 用户「▶ 看现场」跳到对应时刻后点选项;答复写 directing/<ep>/whitebox/decisions.json,
// 「应用决定并重编译」派单给 whitebox-staging 套用;「自定义…」与面板「✏️ 编辑」走预览页 ✏️ 修改同款编辑浮窗(edit-popup.js)通道,
// 但带 data-agent 直发 07-directing/whitebox-staging(2026-09-09 起不再经总制片) ----
const kindLabel=k=>({facing:t('朝向'),occlusion:t('遮挡'),timing:t('时长'),presence:t('进退场'),source_conflict:t('设定冲突'),model_gap:t('模型缺口'),missing_info:t('缺信息'),continuity:t('连续性')})[k]||t('其他');
function renderIssues(st){
  const box=st.issues;if(!box)return;
  const issues=st.group.issues||[];
  if(!issues.length){box.innerHTML='';box.hidden=true;return;}
  box.hidden=false;
  const pending=issues.filter(i=>i.status==='open'||i.status==='stale');
  const blocking=pending.filter(i=>i.severity==='blocking').length;
  const decided=issues.filter(i=>i.status==='decided');
  const item=i=>{
    const jumpT=i.camera_view?.t??i.t_range_s?.[0];
    const where=[i.shots?.length?i.shots.join(', '):'',i.t_range_s?t('{start}–{end} 秒',{start:i.t_range_s[0].toFixed(1),end:i.t_range_s[1].toFixed(1)}):'',i.actors?.length?t('人物 {actors}',{actors:i.actors.join(', ')}):''].filter(Boolean).join(' · ');
    const chosen=i.decision?.choice;
    const opts=[...i.options.map(o=>`<button type="button" class="wb-opt${chosen===o.id?' on':''}${i.recommended===o.id?' rec':''}" data-issue="${esc(i.issue_id)}" data-choice="${esc(o.id)}" title="${esc([o.consequence,o.cost].filter(Boolean).join(' · '))}">${esc(o.id)}. ${esc(o.label)}${i.recommended===o.id?' <em>'+esc(t('推荐方案'))+'</em>':''}</button>`),
      i.provisional&&!i.options.some(o=>o.id==='provisional')?`<button type="button" class="wb-opt${chosen==='provisional'?' on':''}" data-issue="${esc(i.issue_id)}" data-choice="provisional">${esc(t('默认取舍'))}</button>`:'',
      `<button type="button" class="editbtn wb-custom" data-loc="${esc(t('白模待决项 {marker} {question} 我的决定:',{marker:`[whitebox-issue:${st.project}/${st.ep}/${st.group.group_id}/${i.issue_id}]`,question:i.question}))}" data-agent="07-directing/whitebox-staging" title="${esc(t('对这条白模待决项给出自定义决定,直接发消息给白模调度 Agent'))}">${esc(t('自定义…'))}</button>`].join('');
    let state='';
    if(i.status==='applied')state=`<div class="wb-issue-state ok">✅ ${esc(t('已套用'))}${i.applied?.choice?' · '+esc(i.applied.choice):''}</div>`;
    else if(i.status==='decided')state=`<div class="wb-issue-state ok">☑ ${esc(t('已选「{choice}」· {by}',{choice:i.decision.choice,by:i.decision.by||''}))}${i.decision.note?' · '+esc(i.decision.note):''}</div>`;
    else if(i.status==='stale')state=`<div class="wb-issue-state warn">⚠ ${esc(t('答复已失效(问题已变),请重新选择'))}</div>`;
    return `<div class="wb-issue sev-${esc(i.severity)} st-${esc(i.status)}" data-issue="${esc(i.issue_id)}">
      <div class="wb-issue-head"><span class="wb-sev">${esc(t(i.severity==='blocking'?'阻断':'建议'))}</span><span class="wb-kind">${esc(kindLabel(i.kind))}</span><code data-no-i18n>${esc(i.issue_id)}</code>${where?`<span class="wb-where" data-no-i18n>${esc(where)}</span>`:''}${jumpT!=null?`<button type="button" class="wb-jump" data-t="${jumpT}">${esc(t('▶ 看现场'))}</button>`:''}</div>
      <div class="wb-q" data-no-i18n>${esc(i.question)}</div>
      ${i.provisional?`<div class="wb-prov" data-no-i18n>${esc(t('默认取舍:{text}',{text:i.provisional}))}</div>`:''}
      ${i.status==='applied'?'':`<div class="wb-opts">${opts}</div>`}${state}</div>`;
  };
  box.innerHTML=`<details class="wb-issues-box" open><summary>⚠ ${esc(t('待决项 ({count})',{count:issues.length}))}${pending.length?` · <b class="wb-pending">${esc(t('待处理 {count}',{count:pending.length}))}</b>`:''}${blocking?` · <b class="wb-blocking">${esc(t('阻断 {count}',{count:blocking}))}</b>`:''}</summary>
    ${issues.map(item).join('')}
    ${decided.length?`<div class="wb-apply"><button type="button" class="wb-apply-btn">${esc(t('🔄 应用 {count} 项决定并重编译',{count:decided.length}))}</button><span class="wb-apply-msg" role="status"></span></div>`:''}</details>`;
  box.querySelectorAll('.wb-jump').forEach(b=>b.onclick=()=>{st.time=Math.min(st.end,Number(b.dataset.t));st.playing=false;st.els.play.textContent=t('播放');st.dirty=true;kick();});
  box.querySelectorAll('.wb-opt').forEach(b=>b.onclick=()=>decideIssue(st,b.dataset.issue,b.dataset.choice,b));
  const apply=box.querySelector('.wb-apply-btn');if(apply)apply.onclick=()=>applyDecisions(st,apply);
}
async function decideIssue(st,issueId,choice,button){
  button.disabled=true;
  try{
    const r=await json(`${api(st.project)}/${encodeURIComponent(st.ep)}/issues/${encodeURIComponent(issueId)}/decision`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({choice})});
    const idx=st.group.issues.findIndex(i=>i.issue_id===issueId);
    if(idx>=0)st.group.issues[idx]=r.issue;
    renderIssues(st);
  }catch(e){button.disabled=false;const row=button.closest('.wb-issue');let er=row.querySelector('.wb-issue-state.err');if(!er){er=document.createElement('div');er.className='wb-issue-state err';row.appendChild(er);}er.textContent=t('保存失败:{e}',{e:wbMessage(e.message)});}
}
async function applyDecisions(st,button){
  const rows=(st.group.issues||[]).filter(i=>i.status==='decided');
  if(!rows.length)return;
  const gid=st.group.group_id, proj=st.project, ep=st.ep;
  // 指令用中文写给 Agent(同分镜预览页「重新生成白模样片」):内联已裁决项,按规约套用并回写源文件
  const message=[`请套用 ${ep} ${gid} 已裁决的白模待决项(docs/whitebox.md「待决项与用户裁决」):`,
    ...rows.map(i=>`- ${i.issue_id}:选「${i.decision.choice}」${i.decision.note?'(说明:'+i.decision.note+')':''}${i.decision.choice==='provisional'?'(=接受默认取舍:'+i.provisional+')':''}`),
    `先执行 python code/whitebox_issues.py --project ${proj} --ep ${ep} --pending 核对;逐条按所选方案修改 directing/${ep}/whitebox_plans/${gid}.json 并回写对应源文件(blocking/camera/shot_list/prompt),`,
    `把该条 issues[].status 置 applied 并写 applied:{choice,at,note};然后 python code/render_whitebox.py --project ${proj} --ep ${ep} --compile-only ${gid} 重编译。`,
    '回执逐条报告处理结果与回写的文件;不得只改 status 不改内容。'].join('\n');
  button.disabled=true;
  const msg=button.parentElement.querySelector('.wb-apply-msg');
  try{
    const r=await json('/api/v1/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agent:'07-directing/whitebox-staging',message,project:proj,source:'user'})});
    msg.textContent=t('已派单给白模调度 Agent(运行 {run}),套用后刷新本页',{run:r.run_id||'?'});
    episodeCache=null;
  }catch(e){button.disabled=false;msg.textContent=t('派单失败:{e}',{e:wbMessage(e.message)});}
}
export function mountGroups(root,project,ep){
  groupObserver?.disconnect();
  const hosts=[...root.querySelectorAll('.wb-host')];
  if(!hosts.length)return;
  // 未建面板先按项目画幅预留高度(两块画面 + 工具条/色标/播放条约 160px),
  // 减少懒建时上方内容撑高把页面推走的位移(导航跳转靠 preview_storyboard.html jumpTo 再校正)
  projectSettings(project).then(settings=>{
    const f=projectRenderFormat(settings);
    for(const h of hosts)if(h.isConnected&&!h.classList.contains('wb-panel')){const w=Math.max(0,h.clientWidth-24);h.style.minHeight=`${Math.round(2*w*f.height/f.width+160)}px`;}
  }).catch(()=>{});
  pending=new Map();
  groupObserver=new IntersectionObserver(entries=>{
    for(const {target,isIntersecting} of entries){
      if(!isIntersecting){const st=groupPanels.get(target);if(st)releaseGroup(st);continue;}
      if(!pending.has(target))pending.set(target,buildGroup(target,project,ep));
      pending.get(target).then(st=>{if(groupPanels.get(target)===st&&target.isConnected)acquireGroup(st);});
    }
  },{root,rootMargin:'400px 0px'});
  hosts.forEach(h=>groupObserver.observe(h));
}
// keepData:分镜页页内重绘(SSE 事件触发的 load(true)、虚拟人像标记补打等)沿用已取到的白模/项目设置数据,
// 不再每次重绘都重发请求;手动刷新、切集/切项目(keepData=false)或数据超过 DATA_TTL_MS 仍重新请求
const DATA_TTL_MS=5*60*1000;
export function resetWhitebox({keepData=false}={}){
  if(!keepData||!episodeCache||Date.now()-episodeCache.at>DATA_TTL_MS){episodeCache=null;settingsCache.clear();}
  for(const dispose of [...panels.values()])dispose();
  groupObserver?.disconnect();groupObserver=null;pending=new Map();
  for(const st of groupPanels.values())releaseGroup(st);groupPanels.clear();
}
