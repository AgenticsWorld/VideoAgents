// 场景预览页「🌍 世界模型」板块(2026-09-12 World Labs Marble):用 Spark 在浏览器里渲染场景的高斯泼溅 world,
// 按 world.json#alignment 对齐到白模坐标(米,Y 向上),叠加白模线框便于比对;拖动转头、WASD 漫游。
// 依赖页面 importmap:three / three/addons/ / @sparkjsdev/spark(见 preview_scenes.html)。
import * as THREE from 'three';
import { SparkRenderer, SplatMesh } from '@sparkjsdev/spark';

const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const t=s=>window.I18N?.t?window.I18N.t(s):s;
const deg=r=>r*180/Math.PI;

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

// host:板块内的视窗容器;world:预览 API 的 scenes[].world(files/alignment/base_url)。返回 dispose()。
export async function mountWorld(host,project,sid,world){
  const base=world.base_url||`/api/v1/projects/${encodeURIComponent(project)}/artifacts/assets/concepts/scenes/${encodeURIComponent(sid)}/world/`;
  const scene=await compiledScene(project,sid);
  const al=world.alignment||{};
  const cam0=al.camera?.position||[0,1.6,0];
  const resOptions=Object.keys(world.files?.splats||{});
  const pick=resOptions.includes('500k')?'500k':resOptions[0];
  const W=960,H=540;
  host.innerHTML=`<div class="wb-toolbar wv-toolbar">
    <label>${esc(t('精度'))} <select class="wv-res">${resOptions.map(r=>`<option value="${esc(r)}"${r===pick?' selected':''}>${esc(r)}</option>`).join('')}</select></label>
    <label><input type="checkbox" class="wv-wire" checked> ${esc(t('白模线框'))}</label>
    <label><input type="checkbox" class="wv-splat" checked> ${esc(t('世界'))}</label>
    <button type="button" class="wv-home">${esc(t('回到全景机位'))}</button>
    <label>${esc(t('yaw 微调°'))} <input class="wv-yaw" type="number" step="1" value="${Number(al.yaw_fix_deg||0)}" style="width:60px"></label>
    <label>${esc(t('尺度微调'))} <input class="wv-scale" type="number" step="0.01" value="${Number(al.scale_fix||1)}" style="width:64px"></label></div>
    <div class="wb-status wv-status">${esc(t('加载中…'))}</div>
    <div class="wb-views"><figure style="--wb-aspect:16/9"><canvas class="wv-canvas" width="${W}" height="${H}" tabindex="0" style="max-width:100%;outline:none;cursor:grab"></canvas>
      <figcaption>${esc(t('拖动=转头 · W/S 前后 · A/D 左右 · Q/E 升降 · Shift 加速 · 滚轮=前进'))}</figcaption></figure></div>
    <div class="wb-status wv-info" data-no-i18n></div>`;
  const status=host.querySelector('.wv-status'), info=host.querySelector('.wv-info'), canvas=host.querySelector('.wv-canvas');
  const renderer=new THREE.WebGLRenderer({canvas,antialias:false});renderer.setPixelRatio(1);renderer.setSize(W,H,false);
  const three=new THREE.Scene();three.background=new THREE.Color(0x101418);
  const camera=new THREE.PerspectiveCamera(60,W/H,.02,500);
  const spark=new SparkRenderer({renderer});three.add(spark);
  // 对齐:外层 Group = 绕 Y 转全景 yaw、平移到全景相机位;内层 SplatMesh = ×scale、y 减 ground offset、绕 X 转 180°(OpenCV→three.js)
  const outer=new THREE.Group();three.add(outer);
  let splat=null;
  const wire=scene?.objects&&scene.dimensions_m?whiteboxWire(scene):null;if(wire)three.add(wire);
  const marker=new THREE.Mesh(new THREE.SphereGeometry(.06,12,8),new THREE.MeshBasicMaterial({color:0xff4060}));marker.position.fromArray(cam0);three.add(marker);
  const applyAlignment=()=>{
    const fix=Number(host.querySelector('.wv-scale').value||1);
    const s=(al.metric_scale_factor||1)*fix;
    const off=(al.ground_plane_offset||0)*fix;
    const yaw=THREE.MathUtils.degToRad((al.yaw_deg||0)+Number(host.querySelector('.wv-yaw').value||0));
    outer.rotation.set(0,yaw,0);outer.position.set(cam0[0],cam0[1]-off,cam0[2]);
    if(splat){splat.scale.setScalar(s);splat.quaternion.set(1,0,0,0);splat.position.set(0,off,0);}
  };
  const loadSplat=async res=>{
    if(splat){outer.remove(splat);splat.dispose?.();splat=null;}
    status.textContent=t('加载世界模型中…')+` (${res})`;
    const m=new SplatMesh({url:base+world.files.splats[res]});outer.add(m);splat=m;applyAlignment();
    try{await m.initialized;status.textContent=`${t('已加载')} ${res} · ${m.numSplats.toLocaleString()} splats · scale ${al.metric_scale_factor??'?'} · ground offset ${al.ground_plane_offset??'?'} m(${t('全景相机离地')} ${cam0[1]} m)`;}
    catch(e){status.textContent=t('世界模型加载失败:')+(e?.message||e);}
  };
  // 漫游控制:拖动转头(yaw/pitch),键盘平移
  const yawPitch={yaw:THREE.MathUtils.degToRad(al.yaw_deg||0),pitch:0};
  const home=()=>{camera.position.fromArray(cam0);yawPitch.yaw=THREE.MathUtils.degToRad(al.yaw_deg||0);yawPitch.pitch=0;};
  home();
  const keys=new Set();let drag=null;
  canvas.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY};canvas.setPointerCapture(e.pointerId);canvas.focus();canvas.style.cursor='grabbing';});
  canvas.addEventListener('pointerup',()=>{drag=null;canvas.style.cursor='grab';});
  canvas.addEventListener('pointermove',e=>{if(!drag)return;yawPitch.yaw-=(e.clientX-drag.x)*.004;yawPitch.pitch=Math.max(-1.5,Math.min(1.5,yawPitch.pitch-(e.clientY-drag.y)*.004));drag={x:e.clientX,y:e.clientY};});
  canvas.addEventListener('wheel',e=>{e.preventDefault();const f=new THREE.Vector3();camera.getWorldDirection(f);camera.position.addScaledVector(f,-e.deltaY*.002);},{passive:false});
  canvas.addEventListener('keydown',e=>{keys.add(e.key.toLowerCase());if(['w','a','s','d','q','e',' '].includes(e.key.toLowerCase()))e.preventDefault();});
  canvas.addEventListener('keyup',e=>keys.delete(e.key.toLowerCase()));
  canvas.addEventListener('blur',()=>keys.clear());
  host.querySelector('.wv-home').onclick=home;
  host.querySelector('.wv-wire').onchange=e=>{if(wire)wire.visible=e.target.checked;};
  host.querySelector('.wv-splat').onchange=e=>{outer.visible=e.target.checked;};
  host.querySelector('.wv-yaw').oninput=applyAlignment;host.querySelector('.wv-scale').oninput=applyAlignment;
  host.querySelector('.wv-res').onchange=e=>loadSplat(e.target.value);
  let last=performance.now(),disposed=false;
  const loop=now=>{
    if(disposed||!host.isConnected){disposed=true;renderer.dispose();return;}
    const dt=Math.min(.1,(now-last)/1000);last=now;
    const speed=(keys.has('shift')?3:1.2)*dt;
    const fwd=new THREE.Vector3(-Math.sin(yawPitch.yaw),0,-Math.cos(yawPitch.yaw)),right=new THREE.Vector3(fwd.z,0,-fwd.x).negate();
    if(keys.has('w'))camera.position.addScaledVector(fwd,speed);if(keys.has('s'))camera.position.addScaledVector(fwd,-speed);
    if(keys.has('d'))camera.position.addScaledVector(right,speed);if(keys.has('a'))camera.position.addScaledVector(right,-speed);
    if(keys.has('e'))camera.position.y+=speed;if(keys.has('q'))camera.position.y-=speed;
    camera.rotation.set(0,0,0,'YXZ');camera.rotation.y=yawPitch.yaw;camera.rotation.x=yawPitch.pitch;
    renderer.render(three,camera);
    const p=camera.position;
    info.textContent=`${t('机位')} x ${p.x.toFixed(2)} y ${p.y.toFixed(2)} z ${p.z.toFixed(2)} m · ${t('朝向')} ${((360-deg(yawPitch.yaw))%360+360)%360|0}° (${t('罗盘:北 0 · 东 90')}) · ${t('俯仰')} ${deg(yawPitch.pitch).toFixed(0)}° · fov ${camera.fov}°`;
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
  if(pick)await loadSplat(pick);else status.textContent=t('world.json 里没有 splats 文件');
  return ()=>{disposed=true;};
}
