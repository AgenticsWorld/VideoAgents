import { projectRenderFormat } from './whitebox-format.js';
import { wbText as t, wbMessage } from './whitebox-i18n.js';
let rendererModule;
const loadRenderer=()=>rendererModule ||= import('./whitebox-renderer.js?v=20260908-camera-audit');
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api=project=>'/api/v1/projects/'+encodeURIComponent(project)+'/whitebox';
const panels=new Map();
async function json(url,options){const r=await fetch(url,options);const d=await r.json();if(!r.ok){const e=Error(d.detail||r.statusText);e.status=r.status;throw e;}return d;}
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
  host.innerHTML=`<div class="wb-toolbar"><div class="wb-heading"><strong>${esc(t('3D 白模'))} · ${esc(scene.scene_id)}${group?' / '+esc(group.group_id):''}</strong>${group?`<button class="editbtn wb-edit" data-loc="${esc(editLocation)}" title="${esc(t('对这组3D白模提修改意见，发消息给总制片'))}">${esc(t('✏️ 编辑'))}</button>`:''}</div><div class="wb-statuswrap"><button type="button" class="wb-statusbtn" aria-expanded="false" title="${esc(t('查看场景模型 / 调度计划 / 预览文件 / 参考视频的文件状态'))}">${esc(t('状态'))}</button><div class="wb-statuspop" role="status">${statusItems.map(item=>`<span>${esc(item)}</span>`).join('')}${!scene.artifact_status||(group&&!group.artifact_status)?`<span class="wb-statusnote">${esc(t('文件状态未知，请重启服务后刷新。'))}</span>`:''}</div></div><select aria-label="${esc(t('空间视角'))}"><option value="top" selected>${esc(t('俯视图'))}</option><option value="overview">${esc(t('旋转视角'))}</option></select></div>
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
    space=new WhiteboxRenderer(host.querySelector('.wb-space'),format);space.load(scene,group);
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
  if(!episodeCache||episodeCache.key!==key)episodeCache={key,promise:previewData(`${api(project)}/${encodeURIComponent(ep)}`,`/api/v1/projects/${encodeURIComponent(project)}/artifacts/directing/${encodeURIComponent(ep)}/whitebox/episode.json`,3)};
  return episodeCache.promise;
};
const projectSettings=project=>{
  if(!settingsCache.has(project))settingsCache.set(project,json(`/api/v1/projects/${encodeURIComponent(project)}/config`).catch(e=>{settingsCache.delete(project);throw e;}));
  return settingsCache.get(project);
};

// ---- 分镜预览:每个生成组的镜卡列在左半,右半常驻该组的白模面板
// (人物色标 → 播放 → 摄像机视角 → 俯视图 → 建模依据)。全页共用一个 WebGL 上下文,
// 面板只在滚入视口附近时建 3D 场景、滚出即释放;单个 rAF 循环驱动全部面板,静止且无交互时不重绘 ----
const groupPanels=new Map();   // host -> state
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
    st.rr.render(st.els.view.value,st.els.space);
    st.els.time.textContent=`${st.time.toFixed(2)} / ${st.end.toFixed(2)} s · ${st.rr.shotId}`;
    st.els.range.value=st.time;
  }
  groupFrame=requestAnimationFrame(groupLoop);
}
const kick=()=>{if(!groupFrame)groupFrame=requestAnimationFrame(groupLoop);};
function releaseGroup(st){st.rr?.dispose();st.rr=null;st.playing=false;}
async function acquireGroup(st){
  if(st.rr||st.loading||!st.ready)return;
  st.loading=true;
  try{
    const {WhiteboxRenderer,sharedRenderer}=await loadRenderer();
    if(!st.host.isConnected||st.rr||!groupPanels.has(st.host))return;
    st.rr=new WhiteboxRenderer(st.els.space,{...st.format,renderer:sharedRenderer()});
    st.rr.load(st.scene,st.group);
    st.rr.controls?.addEventListener('change',()=>{st.dirty=true;kick();});
    st.dirty=true;st.previous=performance.now();kick();
  }catch(e){st.els.result.textContent=t('3D 加载失败：{error}',{error:wbMessage(e.message)});releaseGroup(st);}
  finally{st.loading=false;}
}
async function buildGroup(host,project,ep){
  const gid=host.dataset.grp;
  const st={host,rr:null,loading:false,ready:false,playing:false,dirty:false,time:0,end:0,previous:0,els:{}};
  groupPanels.set(host,st);
  let data,settings;
  try{[data,settings]=await Promise.all([episodeData(project,ep),projectSettings(project)]);}
  catch(e){episodeCache=null;if(host.isConnected)host.textContent=t('白模加载失败：{error}',{error:wbMessage(e.message)});return st;}
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
  const warnings=group.warnings||scene.warnings||[];
  host.className='wb-panel wb-host';host.style.minHeight='';
  host.style.setProperty('--wb-aspect',`${format.width} / ${format.height}`);
  host.innerHTML=`<div class="wb-toolbar"><div class="wb-heading"><strong>${esc(t('3D 白模'))} · ${esc(scene.scene_id)} / ${esc(gid)}</strong><button class="editbtn wb-edit" data-loc="${esc(editLocation)}" title="${esc(t('对这组3D白模提修改意见，发消息给总制片'))}">${esc(t('✏️ 编辑'))}</button></div><div class="wb-statuswrap"><button type="button" class="wb-statusbtn" aria-expanded="false" title="${esc(t('查看场景模型 / 调度计划 / 预览文件 / 参考视频的文件状态'))}">${esc(t('状态'))}</button><div class="wb-statuspop" role="status">${statusItems.map(item=>`<span>${esc(item)}</span>`).join('')}${!scene.artifact_status||!group.artifact_status?`<span class="wb-statusnote">${esc(t('文件状态未知，请重启服务后刷新。'))}</span>`:''}</div></div></div>
    <div class="wb-cast" data-no-i18n>${[...group.actors,...(group.extras||[])].map(a=>`<span><i class="wb-color" style="background:${esc(a.color)}"></i>${esc(a.label)} (${esc(a.id)}) · ${a.size_m[1]} m</span>`).join('')}</div>
    <div class="wb-transport"><button class="wb-play">${esc(t('播放'))}</button><input type="range" aria-label="${esc(t('白模时间'))}" min="0" max="${group.duration_s}" step="0.01" value="0"><span class="wb-time"></span></div>
    <figure><canvas class="wb-camera" aria-label="${esc(t('摄像机白模'))}"></canvas><figcaption>${esc(t('摄像机视角'))}</figcaption></figure>
    <figure><canvas class="wb-space" aria-label="${esc(t('场景白模'))}"></canvas><figcaption><span>${esc(t('空间与摄像机位置'))}</span><select aria-label="${esc(t('空间视角'))}"><option value="top" selected>${esc(t('俯视图'))}</option><option value="overview">${esc(t('旋转视角'))}</option></select></figcaption></figure>
    <details class="wb-warnings"><summary>${esc(t('建模依据与检查 ({count})',{count:warnings.length}))}</summary><div class="wb-status">${esc(t('{width} × {depth} m · 高 {height} m · 网格 1 m · 画幅 {aspect}',{width:scene.dimensions_m[0],depth:scene.dimensions_m[2],height:scene.dimensions_m[1],aspect:format.aspect_ratio}))} · ${esc(t(scene.inferred?'推断尺寸':'已标定尺寸'))} · ${esc(t('切换旋转视角后可拖动旋转、滚轮缩放'))}</div><div data-no-i18n>${esc(scene.scale_basis)}</div>${warnings.map(w=>`<div data-no-i18n>${esc(wbMessage(w))}</div>`).join('')}</details><div class="wb-status wb-result" role="status"></div>`;
  const q=x=>host.querySelector(x);
  Object.assign(st,{ready:true,scene,group,format,end:group.duration_s,
    els:{camera:q('.wb-camera'),space:q('.wb-space'),view:q('select'),play:q('.wb-play'),range:q('input'),time:q('.wb-time'),result:q('.wb-result')}});
  for(const c of [st.els.camera,st.els.space]){c.width=format.width;c.height=format.height;}
  const statusBtn=q('.wb-statusbtn'), statusPop=q('.wb-statuspop');
  statusBtn.onclick=e=>{e.stopPropagation();const open=!statusPop.classList.contains('open');document.querySelectorAll('.wb-statuspop.open').forEach(x=>x.classList.remove('open'));statusPop.classList.toggle('open',open);statusBtn.setAttribute('aria-expanded',String(open));};
  st.els.view.onchange=()=>{st.dirty=true;kick();};
  st.els.play.onclick=()=>{if(!st.rr)return;if(st.time>=st.end)st.time=0;st.playing=!st.playing;st.previous=performance.now();st.els.play.textContent=t(st.playing?'暂停':'播放');kick();};
  st.els.range.oninput=e=>{st.time=Number(e.target.value);st.playing=false;st.els.play.textContent=t('播放');st.dirty=true;kick();};
  return st;
}
export function mountGroups(root,project,ep){
  groupObserver?.disconnect();
  const hosts=[...root.querySelectorAll('.wb-host')];
  if(!hosts.length)return;
  // 未建面板先按项目画幅预留高度(两块画面 + 工具条/色标/播放条/说明约 190px),
  // 减少懒建时上方内容撑高把页面推走的位移(导航跳转靠 preview_storyboard.html jumpTo 再校正)
  projectSettings(project).then(settings=>{
    const f=projectRenderFormat(settings);
    for(const h of hosts)if(h.isConnected&&!h.classList.contains('wb-panel')){const w=Math.max(0,h.clientWidth-24);h.style.minHeight=`${Math.round(2*w*f.height/f.width+190)}px`;}
  }).catch(()=>{});
  const pending=new Map();   // host -> build promise
  groupObserver=new IntersectionObserver(entries=>{
    for(const {target,isIntersecting} of entries){
      if(!isIntersecting){const st=groupPanels.get(target);if(st)releaseGroup(st);continue;}
      if(!pending.has(target))pending.set(target,buildGroup(target,project,ep));
      pending.get(target).then(st=>{if(groupPanels.get(target)===st&&target.isConnected)acquireGroup(st);});
    }
  },{root,rootMargin:'400px 0px'});
  hosts.forEach(h=>groupObserver.observe(h));
}
export function resetWhitebox(){episodeCache=null;for(const dispose of [...panels.values()])dispose();groupObserver?.disconnect();groupObserver=null;for(const st of groupPanels.values())releaseGroup(st);groupPanels.clear();}
