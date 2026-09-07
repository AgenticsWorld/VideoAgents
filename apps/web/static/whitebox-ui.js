import { projectRenderFormat } from './whitebox-format.js';
let rendererModule;
const loadRenderer=()=>rendererModule ||= import('./whitebox-renderer.js');
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

async function mount(host,project,scene,group=null,ep='') {
  const settings=await json(`/api/v1/projects/${encodeURIComponent(project)}/config`);
  if(!host.isConnected)return;
  const format=projectRenderFormat(settings);
  host.className='wb-panel '+(group?'wb-group':'wb-scene');
  host.style.setProperty('--wb-aspect',`${format.width} / ${format.height}`);
  host.style.setProperty('--wb-preview-width',`${(group?315:460)*format.width/format.height}px`);
  const editLocation=group?`3D 白模 [whitebox:${project}/${ep}/${group.group_id}]（项目 ${project}；集 ${ep}；分镜组 ${group.group_id}；场景 ${scene.scene_id}；镜头 ${group.cameras.map(c=>c.shot_id).join('、')}；白模调度计划 directing/${ep}/whitebox_plans/${group.group_id}.json；场景模型 bible/scenes/${scene.scene_id}/whitebox.json；编译预览 directing/${ep}/whitebox/episode.json）`:'';
  host.innerHTML=`<div class="wb-toolbar"><div class="wb-heading"><strong>3D 白模 · ${esc(scene.scene_id)}${group?' / '+esc(group.group_id):''}</strong>${group?`<button class="editbtn wb-edit" data-loc="${esc(editLocation)}" title="对这组3D白模提修改意见，发消息给总制片">✏️ 编辑</button>`:''}</div><select aria-label="空间视角"><option value="top" selected>俯视图</option><option value="overview">旋转视角</option></select></div>
    <div class="wb-status">${scene.dimensions_m[0]} × ${scene.dimensions_m[2]} m · 高 ${scene.dimensions_m[1]} m · 网格 1 m · 画幅 ${esc(format.aspect_ratio)} · ${scene.inferred?'推断尺寸':'已标定尺寸'} · 切换旋转视角后可拖动旋转、滚轮缩放</div>
    <div class="wb-views"><figure><canvas class="wb-space" aria-label="场景白模"></canvas><figcaption>空间与摄像机位置</figcaption></figure>${group?'<figure><canvas class="wb-camera" aria-label="摄像机白模"></canvas><figcaption>摄像机视角</figcaption></figure>':''}</div>
    ${group?`<div class="wb-transport"><button class="wb-play">播放</button><input type="range" aria-label="白模时间" min="0" max="${group.duration_s}" step="0.01" value="0"><span class="wb-time"></span></div><div class="wb-cast">${[...group.actors,...(group.extras||[])].map(a=>`<span><i class="wb-color" style="background:${esc(a.color)}"></i>${esc(a.label)} (${esc(a.id)}) · ${a.size_m[1]} m</span>`).join('')}</div>`:''}
    <details class="wb-warnings"><summary>建模依据与检查 (${(group?.warnings||scene.warnings).length})</summary><div>${esc(scene.scale_basis)}</div>${(group?.warnings||scene.warnings).map(w=>`<div>${esc(w)}</div>`).join('')}</details><div class="wb-status wb-result" role="status"></div>`;
  let space, camera, frame, disposed=false, playing=false, time=0, previous=0;
  const dispose=()=>{if(disposed)return;disposed=true;cancelAnimationFrame(frame);space?.dispose();camera?.dispose();panels.delete(host);};
  panels.set(host,dispose);
  try {
    const {WhiteboxRenderer}=await loadRenderer();if(disposed||!host.isConnected)return dispose();
    space=new WhiteboxRenderer(host.querySelector('.wb-space'),format);space.load(scene,group);
    if(group){camera=new WhiteboxRenderer(host.querySelector('.wb-camera'),{...format,controls:false});camera.load(scene,group);}
  } catch(e){host.querySelector('.wb-result').textContent='3D 加载失败：'+e.message;dispose();return;}
  const render=()=>{
    space.setTime(time);space.render(host.querySelector('select').value);
    if(camera){camera.setTime(time);camera.render('camera');host.querySelector('.wb-time').textContent=`${time.toFixed(2)} / ${group.duration_s.toFixed(2)} s · ${camera.shotId}`;host.querySelector('input').value=time;}
  };
  const loop=now=>{if(disposed)return;if(!host.isConnected)return dispose();
    if(playing){time=Math.min(group.duration_s,time+(now-previous)/1000);if(time>=group.duration_s){playing=false;host.querySelector('.wb-play').textContent='重播';}}
    previous=now;render();frame=requestAnimationFrame(loop);
  };frame=requestAnimationFrame(loop);
  if(group){
    host.querySelector('.wb-play').onclick=()=>{if(time>=group.duration_s)time=0;playing=!playing;previous=performance.now();host.querySelector('.wb-play').textContent=playing?'暂停':'播放';};
    host.querySelector('input').oninput=e=>{time=Number(e.target.value);playing=false;host.querySelector('.wb-play').textContent='播放';render();};
  }
}

export async function mountScene(host,project,sid){
  host.className='wb-panel';host.textContent='正在加载场景白模…';
  try{const scene=await previewData(`${api(project)}/scenes/${encodeURIComponent(sid)}`,`/api/v1/projects/${encodeURIComponent(project)}/artifacts/assets/concepts/scenes/${encodeURIComponent(sid)}/whitebox.scene.json`);if(host.isConnected)await mount(host,project,scene);}
  catch(e){host.textContent='场景白模尚不可用：'+e.message;}
}

let episodeCache=null;
export async function toggleGroup(button,project,ep,gid){
  const parent=button.closest('.grp');let host=parent.querySelector('.wb-panel');
  if(host){panels.get(host)?.();host.remove();return;}
  // Keep GPU use bounded even for episodes with hundreds of groups.
  for(const [node,dispose] of panels){dispose();node.remove();}
  host=document.createElement('section');host.className='wb-panel';host.textContent='正在加载分镜组白模…';
  parent.querySelector('.gshots').before(host);
  panels.set(host,()=>{host.remove();panels.delete(host);});
  try{
    const key=project+'/'+ep;
    if(!episodeCache||episodeCache.key!==key)episodeCache={key,promise:previewData(`${api(project)}/${encodeURIComponent(ep)}`,`/api/v1/projects/${encodeURIComponent(project)}/artifacts/directing/${encodeURIComponent(ep)}/whitebox/episode.json`,3)};
    const data=await episodeCache.promise;
    const group=data.groups.find(g=>g.group_id===gid);
    if(!group)throw Error(data.errors.find(e=>e.group_id===gid)?.error||'没有本组白模数据');
    if(host.isConnected)await mount(host,project,data.scenes[group.scene_id],{...group,requires_service_restart:data.requires_service_restart},ep);
  }catch(e){episodeCache=null;host.textContent='白模加载失败：'+e.message;}
}
export function resetWhitebox(){episodeCache=null;for(const dispose of [...panels.values()])dispose();}
