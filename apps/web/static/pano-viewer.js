// 场景预览页全景图 360° 查看器(2026-09-21):2:1 等距柱状全景贴到内翻球面,拖动转头、滚轮/双指缩放视角、双击复位、Esc/点 × 关闭;
// 「🖼 平面原图」切回整张展开图(核对接缝/列位用)。依赖页面 importmap:three(见 preview_scenes.html)。
import * as THREE from 'three';

const t=s=>window.I18N?.t?window.I18N.t(s):s;
const FOV0=75,FOV_MIN=25,FOV_MAX=110,LON0=180;   // LON0:内翻球上贴图正中(u=0.5)落在 -X,开场正对图片中心
let ui=null,cur=null;

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
#pano360 .p360-msg{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#fff;font-size:14px;pointer-events:none}`;
  document.head.appendChild(css);
  const root=document.createElement('div');root.id='pano360';
  root.innerHTML=`<canvas></canvas><div class="p360-msg"></div><div class="p360-hint"></div><div class="p360-flat"><img alt=""></div>`
    +`<div class="p360-bar"><button type="button" class="p360-toggle"></button><button type="button" class="p360-reset">⟲</button><button type="button" class="p360-close">✕</button></div>`;
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
    flatImg:root.querySelector('.p360-flat img'),toggle:root.querySelector('.p360-toggle'),lon:0,lat:0,raf:0};

  const render=()=>{ui.raf=0;
    const w=root.clientWidth,h=root.clientHeight;
    if(canvas.width!==Math.round(w*renderer.getPixelRatio())||canvas.height!==Math.round(h*renderer.getPixelRatio())){renderer.setSize(w,h,false);camera.aspect=w/h;}
    camera.updateProjectionMatrix();
    ui.lat=Math.max(-89,Math.min(89,ui.lat));
    const phi=THREE.MathUtils.degToRad(90-ui.lat),th=THREE.MathUtils.degToRad(ui.lon);
    camera.lookAt(Math.sin(phi)*Math.cos(th),Math.cos(phi),Math.sin(phi)*Math.sin(th));
    renderer.render(scene,camera);};
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

export function openPano360(src){
  if(!ui){
    try{build();}catch(e){ui=null;document.getElementById('pano360')?.remove();if(window.zoom)window.zoom(src);return;}   // 无 WebGL:退回平面灯箱
  }
  cur=src;
  ui.root.style.display='block';ui.setFlat(false);ui.flatImg.src=src;
  ui.hint.textContent=t('拖动环视 360°,滚轮缩放,双击复位,Esc 关闭');
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
  cur=null;ui.root.style.display='none';
  ui.mat.map?.dispose();ui.mat.map=null;ui.flatImg.removeAttribute('src');
}
