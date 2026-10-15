// 场景预览页全景图 360° 查看器(2026-09-21):2:1 等距柱状全景贴到内翻球面,拖动转头、滚轮/双指缩放视角、双击复位、Esc/点 × 关闭;
// 「🖼 平面原图」切回整张展开图(核对接缝/列位用)。依赖页面 importmap:three(见 preview_scenes.html)。
// 「💾 背景图」(2026-09-26):openPano360(src, meta) 带锚点信息(project/sid/anchor_id/scheme/position/yaw_deg)时显示,
// 把当前视窗画面按 16:9(1920×1080,不随项目画幅)离屏渲染一张,连同换算出的白模机位(position/target/fov)POST 到 scenes/<sid>/plates/manual 存为新背景图;
// 方向换算:全景图中心列 = 锚点 yaw(与 modules/scene_panos.py 投影约定、world-viewer 的 yaw 同一约定 fwd=(-sin yaw,0,-cos yaw)),
// 视窗 lon 与贴图 u 的关系 u=lon/360(LON0=180 正对中心列)→ 世界 yaw = yaw0 - (lon - 180)。虚线框 = 将保存的画幅范围。
import * as THREE from 'three';

const t=s=>window.I18N?.t?window.I18N.t(s):s;
const FOV0=75,FOV_MIN=25,FOV_MAX=110,LON0=180;   // LON0:内翻球上贴图正中(u=0.5)落在 -X,开场正对图片中心
let ui=null,cur=null,meta=null;

function build(){
  const css=document.createElement('style');
  css.textContent=`#pano360{position:fixed;inset:0;background:#000;display:none;z-index:99;touch-action:none;user-select:none;-webkit-user-select:none}
#pano360 canvas{width:100%;height:100%;display:block;cursor:grab}
#pano360.dragging canvas{cursor:grabbing}
#pano360 .p360-flat{position:absolute;inset:0;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.92);cursor:zoom-out}
#pano360 .p360-flat img{max-width:96vw;max-height:96vh;border-radius:6px}
#pano360.flat .p360-flat{display:flex}
#pano360 .p360-bar{position:absolute;top:10px;inset-inline-end:12px;display:flex;gap:6px;z-index:2}
#pano360 .p360-bar button{background:rgba(22,26,35,.8);color:#fff;border:1px solid rgba(255,255,255,.25);border-radius:6px;padding:5px 10px;font-size:13px;cursor:pointer}
#pano360 .p360-hint{position:absolute;bottom:12px;left:0;right:0;text-align:center;color:rgba(255,255,255,.7);font-size:12px;pointer-events:none;text-shadow:0 1px 2px #000}
#pano360 .p360-msg{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#fff;font-size:14px;pointer-events:none}
#pano360 .p360-guide{position:absolute;border:1px dashed rgba(255,255,255,.55);box-shadow:0 0 0 9999px rgba(0,0,0,.35);pointer-events:none;display:none}
#pano360.cap .p360-guide{display:block}
#pano360 .p360-bar button.p360-save{background:rgba(40,120,70,.85)}
#pano360 .p360-bar button:disabled{opacity:.5;cursor:default}`;
  document.head.appendChild(css);
  const root=document.createElement('div');root.id='pano360';
  root.innerHTML=`<canvas></canvas><div class="p360-guide"></div><div class="p360-msg"></div><div class="p360-hint"></div><div class="p360-flat"><img alt=""></div>`
    +`<div class="p360-bar"><button type="button" class="p360-save" hidden></button><button type="button" class="p360-toggle"></button><button type="button" class="p360-reset">⟲</button><button type="button" class="p360-close">✕</button></div>`;
  document.body.appendChild(root);
  const canvas=root.querySelector('canvas');
  const renderer=new THREE.WebGLRenderer({canvas,antialias:true});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio||1,2));
  const scene=new THREE.Scene();
  const camera=new THREE.PerspectiveCamera(FOV0,1,.1,100);
  const geo=new THREE.SphereGeometry(50,96,48);geo.scale(-1,1,1);   // 内翻:从球心看贴图不镜像
  const mat=new THREE.MeshBasicMaterial({color:0xffffff});
  scene.add(new THREE.Mesh(geo,mat));
  ui={root,canvas,renderer,scene,camera,mat,msg:root.querySelector('.p360-msg'),hint:root.querySelector('.p360-hint'),
    flatImg:root.querySelector('.p360-flat img'),toggle:root.querySelector('.p360-toggle'),save:root.querySelector('.p360-save'),
    guide:root.querySelector('.p360-guide'),lon:0,lat:0,raf:0};

  const render=()=>{ui.raf=0;
    const w=root.clientWidth,h=root.clientHeight;
    if(canvas.width!==Math.round(w*renderer.getPixelRatio())||canvas.height!==Math.round(h*renderer.getPixelRatio())){renderer.setSize(w,h,false);camera.aspect=w/h;}
    camera.updateProjectionMatrix();
    ui.lat=Math.max(-89,Math.min(89,ui.lat));
    const phi=THREE.MathUtils.degToRad(90-ui.lat),th=THREE.MathUtils.degToRad(ui.lon);
    camera.lookAt(Math.sin(phi)*Math.cos(th),Math.cos(phi),Math.sin(phi)*Math.sin(th));
    renderer.render(scene,camera);
    updateGuide();};
  // 保存画幅:固定 16:9,不随项目画幅(2026-10-08);屏幕比 S ≥ A 时同垂直视场、左右裁到 A;S < A 时以屏幕水平视场为准、上下裁到 A(虚线框即所见即所存)
  const capAspect=()=>16/9;
  const capFov=()=>{const S=root.clientWidth/root.clientHeight,A=capAspect();if(S>=A)return camera.fov;
    const th=Math.tan(THREE.MathUtils.degToRad(camera.fov)/2)*S;return 2*THREE.MathUtils.radToDeg(Math.atan(th/A));};
  const updateGuide=()=>{if(!meta)return;const W=root.clientWidth,H=root.clientHeight,S=W/H,A=capAspect();
    let gw,gh;if(S>=A){gh=H;gw=H*A;}else{gw=W;gh=W/A;}
    Object.assign(ui.guide.style,{width:gw+'px',height:gh+'px',left:((W-gw)/2)+'px',top:((H-gh)/2)+'px'});};
  ui.draw=()=>{if(!ui.raf)ui.raf=requestAnimationFrame(render);};
  ui.reset=()=>{ui.lon=LON0;ui.lat=0;camera.fov=FOV0;ui.draw();};

  // 拖动转头:按当前视角折算角速度(拖过一屏高 ≈ 转过一个 fov),缩放后手感一致
  const pts=new Map();let pinch=0;
  canvas.addEventListener('pointerdown',e=>{if(e.button>0)return;canvas.setPointerCapture(e.pointerId);pts.set(e.pointerId,{x:e.clientX,y:e.clientY});root.classList.add('dragging');pinch=0;});
  canvas.addEventListener('pointermove',e=>{const p=pts.get(e.pointerId);if(!p)return;
    if(pts.size===1){const k=camera.fov/root.clientHeight;ui.lon-=(e.clientX-p.x)*k;ui.lat+=(e.clientY-p.y)*k;}
    p.x=e.clientX;p.y=e.clientY;
    if(pts.size===2){const [a,b]=[...pts.values()],d=Math.hypot(a.x-b.x,a.y-b.y);if(pinch)camera.fov=Math.max(FOV_MIN,Math.min(FOV_MAX,camera.fov*pinch/d));pinch=d;}
    ui.draw();});
  const up=e=>{pts.delete(e.pointerId);pinch=0;if(!pts.size)root.classList.remove('dragging');};
  canvas.addEventListener('pointerup',up);canvas.addEventListener('pointercancel',up);
  canvas.addEventListener('wheel',e=>{e.preventDefault();camera.fov=Math.max(FOV_MIN,Math.min(FOV_MAX,camera.fov*Math.exp(e.deltaY*(e.ctrlKey?.01:.0015))));ui.draw();},{passive:false});
  canvas.addEventListener('dblclick',ui.reset);
  root.querySelector('.p360-reset').onclick=ui.reset;
  root.querySelector('.p360-close').onclick=closePano360;
  ui.save.onclick=async()=>{
    if(!meta||!cur||ui.save.disabled)return;
    const W=1920,H=1080;
    // 离屏按画幅渲一帧(同一任务内 toDataURL,无需 preserveDrawingBuffer),再恢复视窗
    const fov0=camera.fov,asp0=camera.aspect,pr=renderer.getPixelRatio();
    camera.fov=capFov();camera.aspect=W/H;camera.updateProjectionMatrix();
    renderer.setPixelRatio(1);renderer.setSize(W,H,false);renderer.render(scene,camera);
    const dataUrl=canvas.toDataURL('image/jpeg',.92);
    const fovCap=camera.fov;
    camera.fov=fov0;camera.aspect=asp0;renderer.setPixelRatio(pr);renderer.setSize(root.clientWidth,root.clientHeight,false);ui.draw();
    // 视窗 lon/lat → 白模坐标方向:yaw = yaw0 - (lon - 180),fwd=(-sin yaw, 0, -cos yaw),lat 为仰角
    const yaw=THREE.MathUtils.degToRad((meta.yaw_deg||0)-(ui.lon-180)),lat=THREE.MathUtils.degToRad(ui.lat);
    const dir=[-Math.sin(yaw)*Math.cos(lat),Math.sin(lat),-Math.cos(yaw)*Math.cos(lat)];
    const pos=meta.position||[0,1.6,0],D=5;
    const body={source:'pano',image:dataUrl,anchor_id:meta.anchor_id||'',scheme:meta.scheme||'',time_of_day:meta.time_of_day||null,source_file:meta.source_file||'',
      camera:{position:pos,target:[pos[0]+dir[0]*D,pos[1]+dir[1]*D,pos[2]+dir[2]*D],fov_v_deg:fovCap},
      view:{lon:((ui.lon%360)+360)%360,lat:ui.lat,fov_v_deg:fovCap,yaw_deg:((360-THREE.MathUtils.radToDeg(yaw))%360+360)%360,pitch_deg:ui.lat}};   // yaw_deg 记罗盘(北 0 · 东 90),与 world-viewer 一致
    ui.save.disabled=true;ui.save.textContent=t('保存中…');ui.msg.textContent='';
    try{
      const r=await fetch(`/api/v1/projects/${encodeURIComponent(meta.project)}/scenes/${encodeURIComponent(meta.sid)}/plates/manual`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const d=await r.json().catch(()=>({}));
      if(!r.ok)throw new Error(d.detail||(r.status===404?t('服务未重启:保存背景图接口不可用'):String(r.status)));
      ui.hint.textContent=(window.I18N?.f?I18N.f('已保存背景图 {key}',{key:d.key}):'saved '+d.key)+` · ${d.camera?.facing||''} h=${d.camera?.height_m}m ${d.camera?.lens_mm_equiv}mm`;
      if(typeof meta.onSaved==='function')meta.onSaved(d);
    }catch(e){ui.hint.textContent=t('保存失败:')+(e.message||e);}
    finally{ui.save.disabled=false;ui.save.textContent='💾 '+t('背景图');}
  };
  const setFlat=on=>{root.classList.toggle('flat',on);ui.toggle.textContent=on?'🌐 '+t('360° 预览'):'🖼 '+t('平面原图');};
  ui.setFlat=setFlat;
  ui.toggle.onclick=()=>setFlat(!root.classList.contains('flat'));
  root.querySelector('.p360-flat').onclick=()=>setFlat(false);
  window.addEventListener('resize',()=>{if(cur)ui.draw();});
  document.addEventListener('keydown',e=>{if(!cur)return;
    if(e.key==='Escape'){e.stopPropagation();closePano360();return;}
    const step={ArrowLeft:[-10,0],ArrowRight:[10,0],ArrowUp:[0,10],ArrowDown:[0,-10]}[e.key];
    if(step){e.preventDefault();ui.lon+=step[0];ui.lat+=step[1];ui.draw();}},true);
}

export function openPano360(src,info){
  if(!ui){
    try{build();}catch(e){ui=null;document.getElementById('pano360')?.remove();if(window.zoom)window.zoom(src);return;}   // 无 WebGL:退回平面灯箱
  }
  cur=src;meta=(info&&info.project&&info.sid&&Array.isArray(info.position))?info:null;
  ui.root.style.display='block';ui.setFlat(false);ui.flatImg.src=src;
  ui.root.classList.toggle('cap',!!meta);
  ui.save.hidden=!meta;ui.save.disabled=false;ui.save.textContent='💾 '+t('背景图');
  ui.save.title=t('把当前画面保存为本场景的一张新背景图(记录机位坐标),之后可在分镜预览页「换图」选用');
  ui.hint.textContent=t('拖动环视 360°,滚轮缩放,双击复位,Esc 关闭')+(meta?' · '+t('虚线框 = 将保存的画幅范围'):'');
  ui.msg.textContent=t('全景加载中…');
  ui.mat.map?.dispose();ui.mat.map=null;ui.mat.color.set(0x000000);ui.mat.needsUpdate=true;
  ui.reset();
  new THREE.TextureLoader().load(src,tex=>{
    if(cur!==src){tex.dispose();return;}
    tex.colorSpace=THREE.SRGBColorSpace;tex.anisotropy=ui.renderer.capabilities.getMaxAnisotropy();
    ui.mat.map=tex;ui.mat.color.set(0xffffff);ui.mat.needsUpdate=true;ui.msg.textContent='';ui.draw();
  },undefined,()=>{if(cur===src)ui.msg.textContent=t('全景图加载失败');});
}

export function closePano360(){
  if(!ui)return;
  cur=null;meta=null;ui.root.style.display='none';ui.root.classList.remove('cap');
  ui.mat.map?.dispose();ui.mat.map=null;ui.flatImg.removeAttribute('src');
}
