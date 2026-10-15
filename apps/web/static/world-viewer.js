// 场景预览页「🌍 世界模型」全屏视窗(2026-09-12 World Labs Marble;2026-10-04 由板块内嵌视窗改为全屏,一个场景可有多个世界模型):
// 点世界模型卡片 → openWorld() 盖满窗口,用 Spark 渲染该世界模型的高斯泼溅,按 world.json#alignment 对齐到白模坐标(米,Y 向上);
// 拖动转头、WASD 漫游、Esc / ✕ 关闭。
// 「💾 背景图」:把虚线框里的画面按 16:9(1920×1080,不随项目画幅)离屏渲一帧,连同相机白模坐标(position/target/fov)POST 到
// scenes/<sid>/plates/manual 存为本场景一张新背景图(opts.onSaved 保存后回调)。
// 「⚙ 设置」:精度、白模线框(核对对齐用,默认关)、yaw / 尺度微调及其保存、设为默认世界模型(POST scenes/<sid>/worlds/<key>,
// opts.onChanged 回调);默认世界模型 = 背景图模式 world 的截图与导演台世界背景所用的那个。
// 依赖页面 importmap:three / three/addons/ / @sparkjsdev/spark(见 preview_scenes.html)。
import * as THREE from 'three';
import { SparkRenderer, SplatMesh } from '@sparkjsdev/spark';

const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const t=s=>window.I18N?.t?window.I18N.t(s):s;
const f=(s,p)=>window.I18N?.f?window.I18N.f(s,p):s.replace(/\{(\w+)\}/g,(m,k)=>p[k]??m);
const deg=r=>r*180/Math.PI;
const FOV=60;
let cssDone=false,closeCur=null;

function css(){
  if(cssDone)return;cssDone=true;
  const el=document.createElement('style');
  el.textContent=`#world3d{position:fixed;inset:0;background:#000;z-index:99;touch-action:none;user-select:none;-webkit-user-select:none;color:#fff;font-size:13px}
#world3d canvas{width:100%;height:100%;display:block;cursor:grab;outline:none}
#world3d.dragging canvas{cursor:grabbing}
#world3d .w3-title{position:absolute;top:12px;inset-inline-start:14px;max-width:45vw;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-shadow:0 1px 2px #000;pointer-events:none}
#world3d .w3-title b{font-weight:600}
#world3d .w3-bar{position:absolute;top:10px;inset-inline-end:12px;display:flex;gap:6px;z-index:2}
#world3d button{background:rgba(22,26,35,.8);color:#fff;border:1px solid rgba(255,255,255,.25);border-radius:6px;padding:5px 10px;font-size:13px;cursor:pointer}
#world3d button:disabled{opacity:.5;cursor:default}
#world3d button.w3-save{background:rgba(40,120,70,.85)}
#world3d button.on{border-color:#fff}
#world3d .w3-panel{position:absolute;top:48px;inset-inline-end:12px;z-index:2;width:250px;background:rgba(22,26,35,.92);border:1px solid rgba(255,255,255,.2);border-radius:8px;padding:10px 12px;display:flex;flex-direction:column;gap:8px}
#world3d .w3-panel[hidden]{display:none}
#world3d .w3-panel label{display:flex;align-items:center;justify-content:space-between;gap:8px;color:rgba(255,255,255,.85)}
#world3d .w3-panel label.chk{justify-content:flex-start}
#world3d .w3-panel select,#world3d .w3-panel input[type=number]{background:#0b0e14;color:#fff;border:1px solid rgba(255,255,255,.25);border-radius:5px;padding:3px 6px;width:96px}
#world3d .w3-panel hr{border:0;border-top:1px solid rgba(255,255,255,.15);margin:2px 0;width:100%}
#world3d .w3-panel .w3-note{color:rgba(255,255,255,.6);font-size:12px;line-height:1.5}
#world3d .w3-panel a{color:#8ab4ff}
#world3d .w3-guide{position:absolute;border:1px dashed rgba(255,255,255,.55);box-shadow:0 0 0 9999px rgba(0,0,0,.35);pointer-events:none}
#world3d .w3-foot{position:absolute;bottom:10px;left:0;right:0;text-align:center;color:rgba(255,255,255,.75);font-size:12px;line-height:1.6;pointer-events:none;text-shadow:0 1px 2px #000}
#world3d .w3-foot .ok{color:#7ee2a0}
#world3d .w3-foot .err{color:#ff8a8a}
#world3d .w3-msg{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:14px;pointer-events:none;text-shadow:0 1px 2px #000}`;
  document.head.appendChild(el);
}

function whiteboxWire(scene){
  // 白模几何线框(墙/家具)+ 地面外框,用于核对 world 与白模的对齐;不写深度,透过 splat 可见
  const g=new THREE.Group();g.name='whitebox-wire';
  const mat=new THREE.LineBasicMaterial({color:0x27c3ff,transparent:true,opacity:.85,depthTest:false});
  for(const obj of scene.objects||[]){
    let geo;const [x,y,z]=obj.size_m;
    if(obj.shape==='sphere'){geo=new THREE.SphereGeometry(.5,12,8);geo.scale(x,y,z);}
    else if(obj.shape==='cylinder'){geo=new THREE.CylinderGeometry(.5,.5,1,12);geo.scale(x,y,z);}
    else geo=new THREE.BoxGeometry(x,y,z);
    const l=new THREE.LineSegments(new THREE.EdgesGeometry(geo),mat);l.position.fromArray(obj.position);l.rotation.set(obj.pitch||0,obj.yaw||0,obj.roll||0);l.renderOrder=10;g.add(l);geo.dispose();
  }
  const [w,,d]=scene.dimensions_m;
  const floor=new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.PlaneGeometry(w,d)),new THREE.LineBasicMaterial({color:0xffb020,depthTest:false}));
  floor.rotation.x=-Math.PI/2;floor.renderOrder=10;g.add(floor);
  const grid=new THREE.GridHelper(Math.ceil(Math.max(w,d)),Math.ceil(Math.max(w,d)),0xffb020,0x7a5a20);grid.material.transparent=true;grid.material.opacity=.35;grid.material.depthTest=false;grid.renderOrder=9;g.add(grid);
  return g;
}

async function compiledScene(project,sid){
  // 线框要用编译后的场景(objects 带 position),与 whitebox-ui 同源:先服务接口,404 时回退到产物 whitebox.scene.json
  for(const url of [`/api/v1/projects/${encodeURIComponent(project)}/whitebox/scenes/${encodeURIComponent(sid)}`,
                    `/api/v1/projects/${encodeURIComponent(project)}/artifacts/assets/concepts/scenes/${encodeURIComponent(sid)}/whitebox.scene.json`]){
    try{const r=await fetch(url);if(r.ok)return await r.json();}catch(e){/* 下一个来源 */}
  }
  return null;
}

// world:预览 API 的 scenes[].worlds[i](key/default/files/alignment/base_url/dir/input);
// opts:{sourceLabel:来源全景的显示名, onSaved(d):存了背景图, onChanged(world):设了默认 / 存了对齐}。
export function openWorld(project,sid,world,opts={}){
  closeWorld();css();
  const base=world.base_url||`/api/v1/projects/${encodeURIComponent(project)}/artifacts/assets/concepts/scenes/${encodeURIComponent(sid)}/world/`;
  const al={...(world.alignment||{})};
  const cam0=al.camera?.position||[0,1.6,0];
  const resOptions=Object.keys(world.files?.splats||{});
  const pick=resOptions.includes('500k')?'500k':resOptions[0];
  const api=`/api/v1/projects/${encodeURIComponent(project)}/scenes/${encodeURIComponent(sid)}`;
  let isDefault=!!world.default;

  const root=document.createElement('div');root.id='world3d';
  root.innerHTML=`<canvas tabindex="0"></canvas><div class="w3-guide"></div><div class="w3-msg"></div>
    <div class="w3-title"><b>🌍 ${esc(f('世界模型 {k}',{k:world.key||''}))}</b>${opts.sourceLabel?' · '+esc(opts.sourceLabel):''}${world.model?' · '+esc(world.model):''}</div>
    <div class="w3-bar"><button type="button" class="w3-save" title="${esc(t('把当前画面保存为本场景的一张新背景图(记录机位坐标),之后可在分镜预览页「换图」选用'))}">💾 ${esc(t('背景图'))}</button>
      <button type="button" class="w3-gear">⚙ ${esc(t('设置'))}</button>
      <button type="button" class="w3-home" title="${esc(t('回到全景机位'))}">⟲</button>
      <button type="button" class="w3-close">✕</button></div>
    <div class="w3-panel" hidden>
      <label>${esc(t('精度'))} <select class="w3-res">${resOptions.map(r=>`<option value="${esc(r)}"${r===pick?' selected':''}>${esc(r)}</option>`).join('')}</select></label>
      <label class="chk"><input type="checkbox" class="w3-wire"> ${esc(t('白模线框'))}</label>
      <label class="chk"><input type="checkbox" class="w3-splat" checked> ${esc(t('世界'))}</label>
      <hr>
      <label>${esc(t('yaw 微调°'))} <input class="w3-yaw" type="number" step="1" value="${Number(al.yaw_fix_deg||0)}"></label>
      <label>${esc(t('尺度微调'))} <input class="w3-scale" type="number" step="0.01" value="${Number(al.scale_fix||1)}"></label>
      <button type="button" class="w3-align" disabled>${esc(t('保存对齐微调'))}</button>
      <hr>
      <button type="button" class="w3-default"></button>
      <div class="w3-note">${esc(t('默认世界模型用于背景图模式「世界模型」的截图和导演台的世界背景'))}</div>
      ${world.world_marble_url?`<a href="${esc(world.world_marble_url)}" target="_blank" rel="noopener">${esc(t('在 Marble 中打开'))} ↗</a>`:''}
    </div>
    <div class="w3-foot"><div class="w3-status">${esc(t('加载中…'))}</div><div class="w3-info" data-no-i18n></div>
      <div>${esc(t('拖动=转头 · W/S 前后 · A/D 左右 · Q/E 升降 · Shift 加速 · 滚轮=前进'))} · ${esc(t('虚线框 = 将保存的画幅范围'))} · ${esc(t('Esc 关闭'))}</div></div>`;
  document.body.appendChild(root);
  const $=q=>root.querySelector(q);
  const canvas=$('canvas'),status=$('.w3-status'),info=$('.w3-info'),msg=$('.w3-msg'),guide=$('.w3-guide'),panel=$('.w3-panel');
  const say=(text,cls='')=>{status.className='w3-status '+cls;status.textContent=text;};

  let renderer;
  try{renderer=new THREE.WebGLRenderer({canvas,antialias:false});}
  catch(e){root.remove();throw e;}
  const three=new THREE.Scene();three.background=new THREE.Color(0x101418);
  const camera=new THREE.PerspectiveCamera(FOV,16/9,.02,500);
  const spark=new SparkRenderer({renderer});three.add(spark);
  // 对齐:外层 Group = 绕 Y 转全景 yaw、平移到全景相机位;内层 SplatMesh = ×scale、y 减 ground offset、绕 X 转 180°(OpenCV→three.js)
  const outer=new THREE.Group();three.add(outer);
  let splat=null,wire=null,disposed=false;
  compiledScene(project,sid).then(scene=>{
    if(disposed||!scene?.objects||!scene.dimensions_m)return;
    wire=whiteboxWire(scene);wire.visible=$('.w3-wire').checked;three.add(wire);
  });
  const applyAlignment=()=>{
    const fix=Number($('.w3-scale').value||1);
    const s=(al.metric_scale_factor||1)*fix;
    const off=(al.ground_plane_offset||0)*fix;
    const yaw=THREE.MathUtils.degToRad((al.yaw_deg||0)+Number($('.w3-yaw').value||0));
    outer.rotation.set(0,yaw,0);outer.position.set(cam0[0],cam0[1]-off,cam0[2]);
    if(splat){splat.scale.setScalar(s);splat.quaternion.set(1,0,0,0);splat.position.set(0,off,0);}
  };
  let loadSeq=0;
  const loadSplat=async res=>{
    const seq=++loadSeq;
    if(splat){outer.remove(splat);splat.dispose?.();splat=null;}
    msg.textContent=t('加载世界模型中…')+` (${res})`;say('');
    const m=new SplatMesh({url:base+world.files.splats[res]});outer.add(m);splat=m;applyAlignment();
    try{await m.initialized;if(seq!==loadSeq||disposed)return;msg.textContent='';
      say(`${t('已加载')} ${res} · ${m.numSplats.toLocaleString()} splats · scale ${al.metric_scale_factor??'?'} · ground offset ${al.ground_plane_offset??'?'} m(${t('全景相机离地')} ${cam0[1]} m)`);}
    catch(e){if(seq!==loadSeq||disposed)return;msg.textContent='';say(t('世界模型加载失败:')+(e?.message||e),'err');}
  };

  // 视窗尺寸:铺满窗口;高分屏按总像素封顶(高斯泼溅按像素计费,全屏 2× 会卡)
  const size=()=>{const w=root.clientWidth||1,h=root.clientHeight||1;
    const pr=Math.min(window.devicePixelRatio||1,Math.max(1,Math.sqrt(3e6/(w*h))));
    renderer.setPixelRatio(pr);renderer.setSize(w,h,false);camera.aspect=w/h;camera.updateProjectionMatrix();updateGuide();};
  // 保存画幅:固定 16:9,不随项目画幅(2026-10-08);屏幕比 S ≥ A 时同垂直视场、左右裁到 A;S < A 时以屏幕水平视场为准、上下裁到 A(虚线框即所见即所存)
  const capAspect=()=>16/9;
  const capFov=()=>{const S=root.clientWidth/root.clientHeight,A=capAspect();if(S>=A)return camera.fov;
    return 2*deg(Math.atan(Math.tan(THREE.MathUtils.degToRad(camera.fov)/2)*S/A));};
  const updateGuide=()=>{const W=root.clientWidth,H=root.clientHeight,S=W/H,A=capAspect();
    let gw,gh;if(S>=A){gh=H;gw=H*A;}else{gw=W;gh=W/A;}
    Object.assign(guide.style,{width:gw+'px',height:gh+'px',left:((W-gw)/2)+'px',top:((H-gh)/2)+'px'});};

  // 漫游控制:拖动转头(yaw/pitch),键盘平移(全屏下键盘挂在 document 上,输入框里打字不算)
  const yawPitch={yaw:THREE.MathUtils.degToRad(al.yaw_deg||0),pitch:0};
  const home=()=>{camera.position.fromArray(cam0);yawPitch.yaw=THREE.MathUtils.degToRad(al.yaw_deg||0);yawPitch.pitch=0;};
  home();
  const keys=new Set();let drag=null;
  canvas.addEventListener('pointerdown',e=>{if(e.button>0)return;drag={x:e.clientX,y:e.clientY};canvas.setPointerCapture(e.pointerId);canvas.focus();root.classList.add('dragging');});
  const up=()=>{drag=null;root.classList.remove('dragging');};
  canvas.addEventListener('pointerup',up);canvas.addEventListener('pointercancel',up);
  canvas.addEventListener('pointermove',e=>{if(!drag)return;yawPitch.yaw-=(e.clientX-drag.x)*.004;yawPitch.pitch=Math.max(-1.5,Math.min(1.5,yawPitch.pitch-(e.clientY-drag.y)*.004));drag={x:e.clientX,y:e.clientY};});
  canvas.addEventListener('wheel',e=>{e.preventDefault();const d=new THREE.Vector3();camera.getWorldDirection(d);camera.position.addScaledVector(d,-e.deltaY*.002);},{passive:false});
  const typing=e=>/^(INPUT|SELECT|TEXTAREA)$/.test(e.target?.tagName||'');
  const MOVE=['w','a','s','d','q','e'];
  const onKeyDown=e=>{
    if(e.key==='Escape'){e.stopPropagation();e.preventDefault();closeWorld();return;}
    if(typing(e)||e.metaKey||e.ctrlKey||e.altKey)return;
    const k=e.key.toLowerCase();keys.add(k);
    if(MOVE.includes(k)){e.preventDefault();e.stopPropagation();}
  };
  const onKeyUp=e=>keys.delete(e.key.toLowerCase());
  const onBlur=()=>keys.clear();
  document.addEventListener('keydown',onKeyDown,true);document.addEventListener('keyup',onKeyUp,true);
  window.addEventListener('blur',onBlur);window.addEventListener('resize',size);

  $('.w3-home').onclick=home;
  $('.w3-close').onclick=()=>closeWorld();
  $('.w3-gear').onclick=e=>{panel.hidden=!panel.hidden;e.currentTarget.classList.toggle('on',!panel.hidden);};
  $('.w3-wire').onchange=e=>{if(wire)wire.visible=e.target.checked;};
  $('.w3-splat').onchange=e=>{outer.visible=e.target.checked;};
  $('.w3-res').onchange=e=>loadSplat(e.target.value);

  // 设置写回:POST scenes/<sid>/worlds/<key>
  const post=async body=>{
    const r=await fetch(`${api}/worlds/${encodeURIComponent(world.key||'')}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(d.detail||(r.status===404?t('服务未重启:世界模型设置接口不可用'):String(r.status)));
    if(typeof opts.onChanged==='function')opts.onChanged(d.world);
    return d;
  };
  const alignBtn=$('.w3-align'),defBtn=$('.w3-default');
  const alignDirty=()=>{alignBtn.disabled=Number($('.w3-yaw').value||0)===Number(al.yaw_fix_deg||0)&&Number($('.w3-scale').value||1)===Number(al.scale_fix||1);};
  $('.w3-yaw').oninput=$('.w3-scale').oninput=()=>{applyAlignment();alignDirty();};
  alignBtn.onclick=async()=>{
    const yaw=Number($('.w3-yaw').value||0),scale=Number($('.w3-scale').value||1);
    alignBtn.disabled=true;
    try{await post({yaw_fix_deg:yaw,scale_fix:scale});al.yaw_fix_deg=yaw;al.scale_fix=scale;say(t('对齐微调已保存'),'ok');}
    catch(e){say(t('保存失败:')+(e.message||e),'err');}
    alignDirty();
  };
  const paintDefault=()=>{defBtn.disabled=isDefault;defBtn.textContent=isDefault?'★ '+t('已是默认世界模型'):'☆ '+t('设为默认世界模型');};
  paintDefault();
  defBtn.onclick=async()=>{
    defBtn.disabled=true;
    try{await post({default:true});isDefault=true;say(t('已设为默认世界模型'),'ok');}
    catch(e){say(t('保存失败:')+(e.message||e),'err');}
    paintDefault();
  };

  // 💾 背景图:按 16:9 离屏渲一帧(同一任务内 toDataURL)→ POST plates/manual;相机方向直接取 three 相机(已在白模坐标系)
  const saveBtn=$('.w3-save');
  saveBtn.onclick=async()=>{
    if(!splat||saveBtn.disabled)return;
    const PW=1920,PH=1080;
    const fov0=camera.fov,fovCap=capFov();
    camera.fov=fovCap;camera.aspect=PW/PH;camera.updateProjectionMatrix();
    renderer.setPixelRatio(1);renderer.setSize(PW,PH,false);
    const wireOn=wire?.visible;if(wire)wire.visible=false;   // 线框只是核对用,不进背景图
    renderer.render(three,camera);
    const dataUrl=canvas.toDataURL('image/jpeg',.92);
    if(wire)wire.visible=wireOn;
    camera.fov=fov0;size();
    const dir=new THREE.Vector3();camera.getWorldDirection(dir);const p=camera.position,D=5;
    const body={source:'world',image:dataUrl,anchor_id:world.input?.anchor_id||'',scheme:world.input?.scheme||'',world_key:world.key||'',
      source_file:`${world.dir||`assets/concepts/scenes/${sid}/world`}/world.json`,
      camera:{position:[p.x,p.y,p.z],target:[p.x+dir.x*D,p.y+dir.y*D,p.z+dir.z*D],fov_v_deg:fovCap},
      view:{yaw_deg:((360-deg(yawPitch.yaw))%360+360)%360,pitch_deg:deg(yawPitch.pitch),fov_v_deg:fovCap,res:$('.w3-res').value}};
    saveBtn.disabled=true;const txt=saveBtn.textContent;saveBtn.textContent=t('保存中…');
    try{
      const r=await fetch(`${api}/plates/manual`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const d=await r.json().catch(()=>({}));
      if(!r.ok)throw new Error(d.detail||(r.status===404?t('服务未重启:保存背景图接口不可用'):String(r.status)));
      say(f('已保存背景图 {key}',{key:d.key})+` · ${d.camera?.facing||''} h=${d.camera?.height_m}m ${d.camera?.lens_mm_equiv}mm`,'ok');
      if(typeof opts.onSaved==='function')opts.onSaved(d);
    }catch(e){say(t('保存失败:')+(e.message||e),'err');}
    finally{saveBtn.disabled=false;saveBtn.textContent=txt;}
  };

  let last=performance.now();
  const loop=now=>{
    if(disposed)return;
    const dt=Math.min(.1,(now-last)/1000);last=now;
    const speed=(keys.has('shift')?3:1.2)*dt;
    const fwd=new THREE.Vector3(-Math.sin(yawPitch.yaw),0,-Math.cos(yawPitch.yaw)),right=new THREE.Vector3(fwd.z,0,-fwd.x).negate();
    if(keys.has('w'))camera.position.addScaledVector(fwd,speed);if(keys.has('s'))camera.position.addScaledVector(fwd,-speed);
    if(keys.has('d'))camera.position.addScaledVector(right,speed);if(keys.has('a'))camera.position.addScaledVector(right,-speed);
    if(keys.has('e'))camera.position.y+=speed;if(keys.has('q'))camera.position.y-=speed;
    camera.rotation.set(0,0,0,'YXZ');camera.rotation.y=yawPitch.yaw;camera.rotation.x=yawPitch.pitch;
    renderer.render(three,camera);
    const p=camera.position;
    info.textContent=`${t('机位')} x ${p.x.toFixed(2)} y ${p.y.toFixed(2)} z ${p.z.toFixed(2)} m · ${t('朝向')} ${((360-deg(yawPitch.yaw))%360+360)%360|0}° (${t('罗盘:北 0 · 东 90')}) · ${t('俯仰')} ${deg(yawPitch.pitch).toFixed(0)}° · fov ${capFov().toFixed(0)}°`;
    requestAnimationFrame(loop);
  };

  closeCur=()=>{
    disposed=true;closeCur=null;
    document.removeEventListener('keydown',onKeyDown,true);document.removeEventListener('keyup',onKeyUp,true);
    window.removeEventListener('blur',onBlur);window.removeEventListener('resize',size);
    if(splat){outer.remove(splat);splat.dispose?.();splat=null;}
    try{renderer.dispose();renderer.forceContextLoss();}catch(e){/* 已失效 */}
    root.remove();
  };
  size();canvas.focus();
  requestAnimationFrame(loop);
  if(pick)loadSplat(pick);else say(t('world.json 里没有 splats 文件'),'err');
}

export function closeWorld(){if(closeCur)closeCur();}
