import * as THREE from './vendor/three/three.module.js';
import { OrbitControls } from './vendor/three/OrbitControls.js';

// Whitebox shadow proxy: project the actual posed triangles onto a curtain,
// clipping to its physical rectangle. This is a parallel silhouette, not a
// photometric simulation of the final film lighting.
export function projectSilhouette(geometry, matrixWorld, bounds, z) {
  const p=geometry.attributes.position,index=geometry.index,out=[];
  for(let i=0;i<(index?index.count:p.count);i+=3){
    let polygon=[0,1,2].map(j=>new THREE.Vector3().fromBufferAttribute(p,index?index.getX(i+j):i+j).applyMatrix4(matrixWorld));
    for(const [axis,edge,sign] of [['x',bounds.min.x,1],['x',bounds.max.x,-1],['y',bounds.min.y,1],['y',bounds.max.y,-1]]){
      const clipped=[];
      for(let j=0;j<polygon.length;j++){
        const a=polygon[j],b=polygon[(j+1)%polygon.length],da=(a[axis]-edge)*sign,db=(b[axis]-edge)*sign;
        if(da>=0)clipped.push(a);
        if((da>=0)!==(db>=0))clipped.push(a.clone().lerp(b,da/(da-db)));
      }
      polygon=clipped;
    }
    for(let j=1;j+1<polygon.length;j++)for(const v of [polygon[0],polygon[j],polygon[j+1]])out.push(v.x,v.y,z);
  }
  return new THREE.BufferGeometry().setAttribute('position',new THREE.Float32BufferAttribute(out,3));
}

export function sample(keys, time) {
  if (time <= keys[0].t) return {...keys[0]};
  for (let i=1; i<keys.length; i++) {
    const a=keys[i-1], b=keys[i];
    if (time>=b.t) continue;
    let u=a.hold?0:(time-a.t)/(b.t-a.t);
    if(a.easing==='smooth') u=u*u*(3-2*u);
    const out={...a};
    // 后一帧缺某通道时按前一帧保持(2026-11-05;原先 b[k] 缺失得 NaN,yaw 经 ||0 变 0 人物瞬间转向),与 modules/whitebox.py sample 同
    for(const k of ['position','target','left_hand','right_hand','scale']) if(a[k]) out[k]=a[k].map((v,j)=>v+((b[k]||a[k])[j]-v)*u);
    for(const k of ['fov','yaw']) if(a[k]!==undefined) {
      let delta=(b[k]??a[k])-a[k];
      if(k==='yaw') delta=((delta+Math.PI)%(2*Math.PI)+2*Math.PI)%(2*Math.PI)-Math.PI;
      out[k]=a[k]+delta*u;
    }
    for(const k of ['bend','pitch','roll','head_pitch','head_yaw','torso_yaw','body_roll','neck_extension','expression','morph','extent'])if(a[k]!==undefined||b[k]!==undefined){
      // bend 缺省值随姿态:蹲姿默认前俯 0.6(leanBend),插值时缺失端也按它算,不再先弹直再俯身(2026-11-05);软体 extent 缺省 1(全身)
      const da=k==='bend'?bendDefault(a):k==='extent'?1:0,db=k==='bend'?bendDefault(b):k==='extent'?1:0;
      out[k]=(a[k]??da)+((b[k]??db)-(a[k]??da))*u;
    }
    // 软体中心线 path 逐点插值(点数相同时;否则保持前帧);模型铰链 joints 按名插值、缺省 0(2026-11-08,与 modules/whitebox.py sample 同)
    if(Array.isArray(a.path)&&Array.isArray(b.path)&&a.path.length===b.path.length)out.path=a.path.map((p,i)=>p.map((v,j)=>v+(b.path[i][j]-v)*u));
    if(a.joints||b.joints){
      const ja=a.joints||{},jb=b.joints||{};out.joints={};
      for(const n of [...new Set([...Object.keys(ja),...Object.keys(jb)])].sort())out.joints[n]=(ja[n]??0)+((jb[n]??0)-(ja[n]??0))*u;
    }
    return out;
  }
  return {...keys.at(-1)};
}

// Upper-body lean shared with modules/whitebox.py (lean_bend/lean_hip/shoulder_point, #77) — keep in sync.
// stand: bend defaults 0 about a 0.35h hip; crouch: 0.6 rad default about 0.175h; kneel: 0 default about
// 0.175h (knees stay on the ground). Other poses never lean.
export const ARM_SEGMENT=.21;
export const SHOT_EPS=1e-6;
export function leanBend(k){const pose=k.pose||'stand';return pose==='stand'||pose==='kneel'?(k.bend||0):pose==='crouch'?(k.bend??.6):0;}
// 关键帧没写 bend 时的缺省值(插值用):蹲姿 0.6,其余 0
export function bendDefault(k){return (k.pose||'stand')==='crouch'?.6:0;}
export function leanHip(pose){return pose==='crouch'||pose==='kneel'?.175:.35;}
// Hand channels follow the actor's own anatomy (#121): facing local +Z with +Y up, the actor's right
// side is local -X. `side` below is the local X sign of the shoulder (modules/whitebox.py HAND_SIDES).
export const HAND_SIDES=[['left_hand',1],['right_hand',-1]];
// Shoulder (body-local, before torso_yaw/yaw) rotated about the hip by the lean, like torso and head.
export function shoulderPoint(size,k,side){
  const pose=k.pose||'stand',h=size[1],low=pose==='sit'||pose==='kneel'||pose==='crouch';
  const bend=leanBend(k),hip=h*leanHip(pose),y=h*(low?.58:.77)-hip;
  return [side*size[0]*.52,hip+y*Math.cos(bend),y*Math.sin(bend)];
}

// ---------------- 自定义形状(2026-11-08,docs/whitebox.md「自定义形状:连续软体 tube 与模型库 model」) ----------------
// 起因:inlove ep01 白蛇只能用一串各自插值的球拼,参考视频里是一串分开的扁椭球,视频模型照着画成一串圆。
// tube = 一条按半径剖面逐渐变细的连续软体;follow:"head" 时身体沿头部走过的轨迹拖在后面,否则按 path(道具局部中心线)。
// model = 编译时从模型库内联的零件树(model_def.parts),零件可带铰链(关键帧 joints 转动)。
// 中心线算法与 modules/whitebox_shapes.py tube_centerline 同口径(tests/test_whitebox_shapes.py 逐时刻比对),改动须两边同步;
// 平滑(Catmull-Rom 重采样)、波动(wave)与网格只在这里做,Python 侧按未平滑折线 + 半径(+波幅)算包围。
export const TUBE_SEGMENTS=48, TUBE_RADIAL=12;
export function radiusProfile(r){return Array.isArray(r)?(r.length>1?r.map(Number):[+r[0],+r[0]]):[+r,+r];}
export function radiusAt(profile,f){
  f=Math.min(1,Math.max(0,f));const x=f*(profile.length-1),i=Math.min(profile.length-2,Math.floor(x));
  return profile[i]+(profile[i+1]-profile[i])*(x-i);
}
const dist3=(a,b)=>Math.hypot(a[0]-b[0],a[1]-b[1],a[2]-b[2]);
function polyLength(pts){let s=0;for(let i=1;i<pts.length;i++)s+=dist3(pts[i-1],pts[i]);return s;}
// 从起点沿折线走 len 米为止的那一段(末点按比例插值);len ≤ 0 只剩起点
function cutPolyline(pts,len){
  const out=[[...pts[0]]];let left=Math.max(0,len);
  for(let i=1;i<pts.length;i++){
    const a=pts[i-1],b=pts[i],seg=dist3(a,b);
    if(seg<=1e-6)continue;
    if(left<=seg+1e-6){if(left>1e-6){const u=Math.min(1,left/seg);out.push(a.map((v,j)=>v+(b[j]-v)*u));}return out;}
    out.push([...b]);left-=seg;
  }
  return out;
}
// follow 软体在轨迹起点之前的身体朝向(指向头的前方):首帧 yaw/pitch → 道具顶层 yaw/pitch → 第一段运动方向 → +Z
export function tubeHeading(data){
  const keys=data.keyframes||[];
  for(const src of [keys[0]||data,data]){
    if(src.yaw!=null||src.pitch!=null){const y=+(src.yaw||0),p=+(src.pitch||0);return [Math.sin(y),-Math.sin(p)*Math.cos(y),Math.cos(p)*Math.cos(y)];}
  }
  if(keys.length){
    const p0=keys[0].position;
    for(const q of keys.slice(1)){const d=q.position.map((v,j)=>v-p0[j]),n=Math.hypot(...d);if(n>1e-4)return d.map(v=>v/n);}
  }
  return [0,0,1];
}
// 软体 t 时刻的中心线(未平滑折线,头在前):points 为道具局部坐标(follow 相对头部),length 可见长度,full 半径剖面全长,
// travel = follow 时头沿轨迹已走的距离(波动相位用)
export function tubeCenterline(data,k,t){
  const extent=Math.min(1,Math.max(0,+(k.extent??data.extent??1)));
  const profile=radiusProfile(data.radius_m);
  let points,full,travel=0;
  if(data.follow==='head'){
    const head=[...(k.position||data.position||[0,0,0])],trail=[head],keys=data.keyframes||[];
    for(let i=keys.length-1;i>=0;i--){const q=keys[i];if(q.t<t-1e-9&&dist3(q.position,trail.at(-1))>1e-6)trail.push([...q.position]);}
    travel=polyLength(trail);full=+data.length_m;
    const d=tubeHeading(data),last=trail.at(-1);trail.push(last.map((v,j)=>v-d[j]*full));
    points=cutPolyline(trail,full*extent).map(p=>p.map((v,j)=>v-head[j]));
  }else{
    const path=k.path||data.path;full=polyLength(path);points=cutPolyline(path,full*extent);
  }
  let s=0;const radii=points.map((p,i)=>{if(i)s+=dist3(points[i-1],p);return radiusAt(profile,full>1e-6?s/full:0);});
  return {points,radii,length:s,full,travel};
}
function propMaterial(color,ghost,side=THREE.FrontSide){
  return ghost?new THREE.MeshStandardMaterial({color:color||0xbfe6ff,roughness:.25,transparent:true,opacity:.3,depthWrite:false,side})
    :new THREE.MeshStandardMaterial({color:color||0xf3f0e8,roughness:1,side});
}
function primitiveGeometry(shape,[x,y,z]){
  let geo;
  if(shape==='sphere'){geo=new THREE.SphereGeometry(.5,16,12);geo.scale(x,y,z);}
  else if(shape==='cylinder'){geo=new THREE.CylinderGeometry(.5,.5,1,16);geo.scale(x,y,z);}
  // 锥体(2026-11-05,仅计划 props):尖朝局部 +Y,底径 size_m[0]×[2]、高 [1];尖朝下(甜筒)用 pitch=π
  else if(shape==='cone'){geo=new THREE.ConeGeometry(.5,1,16);geo.scale(x,y,z);}
  else geo=new THREE.BoxGeometry(x,y,z);
  return geo;
}
// 软体网格:(TUBE_SEGMENTS+1) 圈 × TUBE_RADIAL 个顶点每帧重填,头尾各一个球冠;head:"eyes" 在头端画一对眼睛标明朝向。
// 不画棱线(曲面棱线会画成一节节的环,视频模型又会照着画成分节)
function makeTube(color,ghost,head){
  const wrap=new THREE.Group(),material=propMaterial(color,ghost),R=TUBE_RADIAL;
  const geo=new THREE.BufferGeometry();
  geo.setAttribute('position',new THREE.Float32BufferAttribute(new Float32Array((TUBE_SEGMENTS+1)*R*3),3));
  const index=[];
  for(let i=0;i<TUBE_SEGMENTS;i++)for(let j=0;j<R;j++){const a=i*R+j,b=i*R+(j+1)%R,c=(i+1)*R+j,d=(i+1)*R+(j+1)%R;index.push(a,b,c,b,d,c);}
  geo.setIndex(index);
  const body=new THREE.Mesh(geo,material);body.name='tube-body';
  const capGeo=new THREE.SphereGeometry(1,16,12);
  const headCap=new THREE.Mesh(capGeo,material),tailCap=new THREE.Mesh(capGeo,material);headCap.name='tube-head';tailCap.name='tube-tail';
  wrap.add(body,headCap,tailCap);
  if(ghost)for(const m of [body,headCap,tailCap])m.renderOrder=2;
  let eyes=null;
  if(head==='eyes'){
    eyes=[];const white=new THREE.MeshStandardMaterial({color:0xffffff,roughness:.9}),dark=new THREE.MeshStandardMaterial({color:0x18232b,roughness:.9});
    const eyeGeo=new THREE.SphereGeometry(1,12,8);
    for(const side of [-1,1]){
      const eye=new THREE.Mesh(eyeGeo,white),pupil=new THREE.Mesh(eyeGeo,dark);pupil.scale.setScalar(.5);eye.add(pupil);
      eye.name='tube-eye';eye.userData.side=side;wrap.add(eye);eyes.push({eye,pupil});
    }
  }
  return {group:wrap,body,headCap,tailCap,eyes,geo};
}
// 原始中心线 → 等弧长重采样的平滑曲线(Catmull-Rom centripetal,不过冲)+ 每点半径;退化(不足两点)时隐藏
function shapeTube(tube,line,profile){
  const pts=[];
  for(const p of line.points){const v=new THREE.Vector3(...p);if(!pts.length||v.distanceTo(pts.at(-1))>1e-4)pts.push(v);}
  const ok=pts.length>=2;
  for(const m of [tube.body,tube.headCap,tube.tailCap,...(tube.eyes||[]).map(e=>e.eye)])m.visible=ok;
  if(!ok)return null;
  const curve=pts.length===2?new THREE.LineCurve3(pts[0],pts[1]):new THREE.CatmullRomCurve3(pts,false,'centripetal');
  const spaced=curve.getSpacedPoints(TUBE_SEGMENTS);
  return {pts:spaced,radii:spaced.map((_,i)=>radiusAt(profile,line.full>1e-6?i/TUBE_SEGMENTS*line.length/line.full:0))};
}
function tangents(pts){
  return pts.map((_,i)=>{const t=new THREE.Vector3().subVectors(pts[Math.min(pts.length-1,i+1)],pts[Math.max(0,i-1)]);return t.lengthSq()<1e-12?t.set(0,0,1):t.normalize();});
}
// follow 软体的蛇形波动:位移固定在世界轨迹上(相位 = 头已走距离 − 身上弧长),身体从中穿过;头部 15% 身长内渐入,头仍在关键帧位置。
// axis "side"(缺省)= 水平左右摆(地上 / 水面游);"up" = 竖直起伏(腾空的龙蛇,侧面机位看得见)
function applyWave(pts,line,amplitude,wavelength,axis){
  const up=new THREE.Vector3(0,1,0),T=tangents(pts),n=pts.length-1;
  for(let i=0;i<=n;i++){
    const s=i/n*line.length,lat=new THREE.Vector3().crossVectors(T[i],up);
    if(lat.lengthSq()<1e-8)lat.set(1,0,0);else lat.normalize();
    if(axis==='up')lat.crossVectors(lat.clone(),T[i]).normalize();
    let env=Math.min(1,s/(.15*line.full));env=env*env*(3-2*env);
    pts[i].addScaledVector(lat,amplitude*env*Math.sin(2*Math.PI*(line.travel-s)/wavelength));
  }
}
function fillTube(tube,pts,radii){
  const pos=tube.geo.attributes.position.array,R=TUBE_RADIAL,n=pts.length-1,T=tangents(pts);
  let N=null;const B=new THREE.Vector3();
  for(let i=0;i<=n;i++){
    // 平行移动标架:首圈取与切线正交的「上」方向,之后逐圈去掉切向分量,截面不随曲率翻转
    if(N)N.addScaledVector(T[i],-T[i].dot(N));
    if(!N||N.lengthSq()<1e-10){const ref=Math.abs(T[i].y)>.99?new THREE.Vector3(1,0,0):new THREE.Vector3(0,1,0);N=ref.addScaledVector(T[i],-T[i].dot(ref));}
    N.normalize();B.crossVectors(T[i],N);
    for(let j=0;j<R;j++){
      const a=j/R*Math.PI*2,c=Math.cos(a)*radii[i],s=Math.sin(a)*radii[i],o=(i*R+j)*3;
      pos[o]=pts[i].x+N.x*c+B.x*s;pos[o+1]=pts[i].y+N.y*c+B.y*s;pos[o+2]=pts[i].z+N.z*c+B.z*s;
    }
  }
  tube.geo.attributes.position.needsUpdate=true;tube.geo.computeVertexNormals();tube.geo.computeBoundingSphere();tube.geo.computeBoundingBox();
  tube.headCap.position.copy(pts[0]);tube.headCap.scale.setScalar(Math.max(radii[0],1e-4));
  tube.tailCap.position.copy(pts[n]);tube.tailCap.scale.setScalar(Math.max(radii[n],1e-4));
  if(tube.eyes){
    const F=new THREE.Vector3().subVectors(pts[0],pts[1]).normalize(),up=Math.abs(F.y)>.99?new THREE.Vector3(1,0,0):new THREE.Vector3(0,1,0);
    const U=up.addScaledVector(F,-F.dot(up)).normalize(),S=new THREE.Vector3().crossVectors(F,U),r=radii[0];
    for(const {eye,pupil} of tube.eyes){
      eye.position.copy(pts[0]).addScaledVector(F,.3*r).addScaledVector(U,.5*r).addScaledVector(S,eye.userData.side*.55*r);
      eye.scale.setScalar(Math.max(.4*r,1e-4));pupil.position.copy(F).multiplyScalar(.62);
    }
  }
}
function updateTube(tube,data,k,t){
  const line=tubeCenterline(data,k,t),shaped=shapeTube(tube,line,radiusProfile(data.radius_m));
  if(!shaped)return;
  if(data.follow==='head'&&data.wave&&+data.wave.amplitude_m>0)applyWave(shaped.pts,line,+data.wave.amplitude_m,+data.wave.wavelength_m,data.wave.axis);
  fillTube(tube,shaped.pts,shaped.radii);
}
// 模型零件树:每个零件 = 枢轴组(position + pitch/yaw/roll,挂在父零件的铰链组下)→ 铰链组(关键帧 joints 转动)→ 几何(偏移 offset)。
// 颜色 / 材质:零件自己的 → 道具的 → 模型的 → 缺省
function buildModel(data){
  const def=data.model_def,root=new THREE.Group(),hinges={},joints={};
  for(const part of def.parts){
    const pivot=new THREE.Group();pivot.position.fromArray(part.position||[0,0,0]);pivot.rotation.set(part.pitch||0,part.yaw||0,part.roll||0);
    const hinge=new THREE.Group();pivot.add(hinge);
    const color=part.color||data.color||def.color,ghost=(part.material||data.material||def.material)==='ghost';
    let node;
    if(part.shape==='tube'){
      const tube=makeTube(color,ghost,part.head),length=polyLength(part.path);
      const shaped=shapeTube(tube,{points:part.path,length,full:length},radiusProfile(part.radius_m));
      if(shaped)fillTube(tube,shaped.pts,shaped.radii);
      node=tube.group;
    }else{
      let geo;
      if(part.shape==='lathe')geo=new THREE.LatheGeometry(part.profile.map(([r,y])=>new THREE.Vector2(r,y)),24);
      else if(part.shape==='extrude'){
        geo=new THREE.ExtrudeGeometry(new THREE.Shape(part.outline.map(([x,y])=>new THREE.Vector2(x,y))),{depth:part.depth_m,bevelEnabled:false});
        geo.translate(0,0,-part.depth_m/2);
      }else geo=primitiveGeometry(part.shape,part.size_m);
      const curved=part.shape==='lathe'||part.shape==='extrude';
      node=new THREE.Mesh(geo,propMaterial(color,ghost,part.shape==='lathe'?THREE.DoubleSide:THREE.FrontSide));
      if(ghost)node.renderOrder=2;
      node.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo,curved?30:1),ghost?new THREE.LineBasicMaterial({color:color||0xbfe6ff,transparent:true,opacity:.6,depthWrite:false})
        :new THREE.LineBasicMaterial({color:0x8d968d})));
    }
    node.name=part.id;node.position.fromArray(part.offset||[0,0,0]);hinge.add(node);
    (part.parent?hinges[part.parent]:root).add(pivot);hinges[part.id]=hinge;
    if(part.hinge)joints[part.id]={node:hinge,axis:part.hinge.axis};
  }
  return {root,joints};
}

// One detached WebGL context shared by every per-shot preview on a page: each
// panel renders into it and copies the frame to its own 2D canvas, so hundreds
// of storyboard shots never approach the browser's WebGL context limit.
let shared=null;
export function sharedRenderer() {
  if(!shared){
    shared=new THREE.WebGLRenderer({canvas:document.createElement('canvas'),antialias:true});
    shared.setPixelRatio(1);shared.setClearColor(0xe9ede9);
  }
  return shared;
}

export class WhiteboxRenderer {
  constructor(canvas, {width=960,height=540, controls=true, renderer=null}={}) {
    this.canvas=canvas; this.width=width; this.height=height; this.shared=!!renderer;
    this.renderer=renderer||new THREE.WebGLRenderer({canvas,antialias:true,preserveDrawingBuffer:true});
    if(!this.shared){
      this.renderer.setPixelRatio(1);
      this.renderer.setSize(width,height,false);
      this.renderer.setClearColor(0xe9ede9);
    }
    this.camera=new THREE.PerspectiveCamera(50,width/height,.025,2000);
    this.overview=new THREE.PerspectiveCamera(45,width/height,.05,3000);
    this.top=new THREE.OrthographicCamera(-10,10,6,-6,.05,3000);
    this.controls=controls?new OrbitControls(this.overview,canvas):null;
    if(this.controls) {this.controls.enableDamping=false;this.controls.maxPolarAngle=Math.PI*.49;}
    this.scene=null; this.group=null; this.actors=[];
    // 实景图视图(2026-09-09):assetBase 指向项目 artifacts 根时,把场景俯视图 layout_top.png 铺在地面,
    // 'real' 视图隐藏灰盒几何只留实景俯视图 + 人物 + 机位,便于比对分镜背景图;导出页不设 assetBase 不加载贴图
    this.assetBase=null; this.onRealPlate=null; this.solids=[]; this.realPlane=null;
    this.fly=null; this.flyRadius=5;   // 旋转视角第一人称漫游(bindFly 后启用);flyRadius 由 load 按取景半径写,决定移动速度
  }
  // 旋转视角漫游(2026-09-26,鼠标与键盘都与世界模型视窗同一套):拖动=转头(原地转视线,不再绕目标点公转)、滚轮=沿视线前进,
  // 画面可聚焦(tabindex),W/S/A/D/Q/E 平移、Shift 加速;绑定后 OrbitControls 停用。只在自带 controls 的视口上生效,导出页/共享上下文不绑定
  bindFly(canvas=this.canvas) {
    if(!this.controls||!canvas||typeof canvas.addEventListener!=='function'||this.fly)return this.fly;
    const fly=this.fly={keys:new Set(),yaw:0,pitch:0,drag:null};
    this.controls.enabled=false;
    this.syncFly();
    const watched=['w','a','s','d','q','e','shift'];
    canvas.tabIndex=0;canvas.style.cursor='grab';
    canvas.addEventListener('pointerdown',e=>{fly.drag={x:e.clientX,y:e.clientY};try{canvas.setPointerCapture(e.pointerId);canvas.focus({preventScroll:true});}catch(err){}canvas.style.cursor='grabbing';});
    const stop=()=>{fly.drag=null;canvas.style.cursor='grab';};
    canvas.addEventListener('pointerup',stop);canvas.addEventListener('pointercancel',stop);
    canvas.addEventListener('pointermove',e=>{if(!fly.drag)return;fly.yaw-=(e.clientX-fly.drag.x)*.004;fly.pitch=Math.max(-1.5,Math.min(1.5,fly.pitch-(e.clientY-fly.drag.y)*.004));fly.drag={x:e.clientX,y:e.clientY};this.applyFly();});
    canvas.addEventListener('wheel',e=>{e.preventDefault();this.overview.position.addScaledVector(this.flyDirection(),-e.deltaY*.002*this.flyScale());},{passive:false});
    canvas.addEventListener('keydown',e=>{const k=e.key.toLowerCase();if(!watched.includes(k))return;fly.keys.add(k);if(k!=='shift')e.preventDefault();});
    canvas.addEventListener('keyup',e=>fly.keys.delete(e.key.toLowerCase()));
    canvas.addEventListener('blur',()=>fly.keys.clear());
    return fly;
  }
  flyScale(){return Math.max(1,this.flyRadius/5);}   // 世界模型视窗按人眼尺度;白模视口按取景半径放大移动量
  flyDirection(){return new THREE.Vector3(0,0,-1).applyQuaternion(this.overview.quaternion);}
  // load 取景(lookAt)后把相机朝向换算成 yaw/pitch,之后拖动只改这两个角
  syncFly(){if(!this.fly)return;const d=this.flyDirection();this.fly.yaw=Math.atan2(-d.x,-d.z);this.fly.pitch=Math.asin(Math.max(-1,Math.min(1,d.y)));this.applyFly();}
  applyFly(){const c=this.overview;c.rotation.set(0,0,0,'YXZ');c.rotation.y=this.fly.yaw;c.rotation.x=this.fly.pitch;}
  // 每帧按当前按键平移:W/S 沿视线水平前后、A/D 左右、Q/E 升降,Shift 加速。返回是否移动了(调用方据此重绘)
  flyKeys(dt=1/60) {
    const fly=this.fly;if(!fly||!fly.keys.size)return false;
    const keys=fly.keys,speed=Math.max(1.5,this.flyRadius*.15)*(keys.has('shift')?3:1)*dt;
    const f=new THREE.Vector3(-Math.sin(fly.yaw),0,-Math.cos(fly.yaw)),r=new THREE.Vector3(-f.z,0,f.x);
    const mv=new THREE.Vector3();
    if(keys.has('w'))mv.addScaledVector(f,speed);if(keys.has('s'))mv.addScaledVector(f,-speed);
    if(keys.has('d'))mv.addScaledVector(r,speed);if(keys.has('a'))mv.addScaledVector(r,-speed);
    if(keys.has('e'))mv.y+=speed;if(keys.has('q'))mv.y-=speed;
    if(mv.lengthSq()===0)return false;
    this.overview.position.add(mv);
    return true;
  }
  disposeScene() {
    if(!this.scene)return;
    this.scene.traverse(o=>{
      o.geometry?.dispose();
      for(const m of (Array.isArray(o.material)?o.material:[o.material])) if(m){m.map?.dispose();m.dispose();}
    });
    this.scene=null;
  }
  load(sceneData, group=null) {
    this.disposeScene();this.sceneData=sceneData;this.group=group;this.actors=[];this.props=[];this.silhouettes=[];
    const scene=this.scene=new THREE.Scene();scene.background=new THREE.Color(0xe9ede9);
    scene.add(new THREE.HemisphereLight(0xffffff,0x8c968d,2.5));
    const sun=new THREE.DirectionalLight(0xffffff,2);sun.position.set(-8,20,10);scene.add(sun);
    const [w,h,d]=sceneData.dimensions_m;
    // 开底/悬空场景(#76):scene.floor==='none' 不生成地面板,scene.grid===false 不生成网格;缺省照旧两者都有
    this.solids=[];this.realPlane=null;
    if(sceneData.floor!=='none'){
      const floor=new THREE.Mesh(new THREE.BoxGeometry(w,.05,d),new THREE.MeshStandardMaterial({color:0xd6ddd3,roughness:1}));
      floor.name='ground-floor';floor.position.y=-.05;scene.add(floor);this.solids.push(floor);
    }
    if(sceneData.grid!==false){
      const grid=new THREE.GridHelper(Math.ceil(Math.max(w,d)),Math.ceil(Math.max(w,d)),0xa8b5a5,0xc0cbbd);
      grid.name='ground-grid';grid.position.y=.005;scene.add(grid);this.solids.push(grid);
    }
    if(this.assetBase&&sceneData.layout_top){
      // 俯视图上北下南左西右东 = 白模 -z 北 / +x 东;PlaneGeometry 绕 X 轴转 -90° 后图片上缘落到 -z
      const plane=new THREE.Mesh(new THREE.PlaneGeometry(w,d),new THREE.MeshBasicMaterial({color:0xffffff}));
      plane.rotation.x=-Math.PI/2;plane.position.y=.02;plane.visible=false;plane.name='real-plate';scene.add(plane);
      this.realPlane=plane;
      new THREE.TextureLoader().load(this.assetBase+sceneData.layout_top,tex=>{
        if(this.scene!==scene)return;tex.colorSpace=THREE.SRGBColorSpace;plane.material.map=tex;plane.material.needsUpdate=true;this.onRealPlate?.();
      });
    }
    const propIds=new Set((group?.props||[]).map(p=>p.id));
    // A group can animate an existing scene prop without leaving a duplicate.
    for(const obj of [...sceneData.objects.filter(o=>!propIds.has(o.id)),...(group?.props||[])]) {
      // material "ghost"(2026-11-05):泡泡/水/烟/光效等不挡人的效果体——半透明、不写深度,人物透过它仍清楚可见
      const ghost=obj.material==='ghost';
      // 自定义形状(2026-11-08,仅计划 props):tube 软体(网格在 setTime 里按中心线重填)、model 模型库零件树
      let mesh,tube=null,joints=null;
      if(obj.shape==='tube'){tube=makeTube(obj.color,ghost,obj.head);mesh=tube.group;}
      else if(obj.model_def){const built=buildModel(obj);mesh=built.root;joints=built.joints;}
      else{
        const geo=primitiveGeometry(obj.shape,obj.size_m);
        mesh=new THREE.Mesh(geo,propMaterial(obj.color,ghost));
        if(ghost)mesh.renderOrder=2;
        const edges=new THREE.LineSegments(new THREE.EdgesGeometry(geo),ghost?new THREE.LineBasicMaterial({color:obj.color||0xbfe6ff,transparent:true,opacity:.6,depthWrite:false})
          :new THREE.LineBasicMaterial({color:0x8d968d}));mesh.add(edges);
      }
      mesh.name=obj.id;mesh.position.fromArray(obj.position||[0,0,0]);mesh.rotation.set(obj.pitch||0,obj.yaw||0,obj.roll||0);
      scene.add(mesh);
      if(group?.props?.includes(obj))this.props.push({data:obj,mesh,tube,joints});else this.solids.push(mesh);
    }
    for(const actor of [...(group?.actors||[]),...(group?.extras||[])]) {
      const root=new THREE.Group();const body=new THREE.Group();root.add(body);
      const [aw,ah,ad]=actor.size_m;
      const mat=new THREE.MeshStandardMaterial({color:actor.color,roughness:.9});
      const box=(sx,sy,sz,x,y,z)=>{const m=new THREE.Mesh(new THREE.BoxGeometry(sx,sy,sz),mat);m.position.set(x,y,z);body.add(m);return m;};
      const head=new THREE.Mesh(new THREE.SphereGeometry(ah*.1,20,14),mat);
      // The face points along local +Z, matching the trajectory's yaw convention.
      // Attach solid features to the head so they follow turns and sitting/lying
      // poses in every view, including exported camera and top-down frames.
      const radius=ah*.1;
      const face=new THREE.Group();face.name='face-direction';
      const white=new THREE.MeshStandardMaterial({color:0xffffff,roughness:.9});
      const dark=new THREE.MeshStandardMaterial({color:0x18232b,roughness:.9});
      const eyes=[],brows=[];
      for(const side of [-1,1]) {
        const eye=new THREE.Mesh(new THREE.SphereGeometry(radius*.25,12,8),white);
        eye.position.set(side*radius*.38,radius*.2,radius*.88);
        const pupil=new THREE.Mesh(new THREE.SphereGeometry(radius*.13,12,8),dark);
        pupil.position.z=radius*.2;eye.add(pupil);face.add(eye);eyes.push(eye);
      }
      const nose=new THREE.Mesh(new THREE.ConeGeometry(radius*.28,radius*.85,4),white);
      nose.name='face-forward';nose.rotation.x=Math.PI/2;
      nose.position.set(0,-radius*.08,radius*1.18);face.add(nose);head.add(face);
      face.visible=actor.faceless!==true;
      // Optional performance channels leave legacy silhouettes unchanged.
      let tongue=null;
      if(actor.keyframes.some(k=>k.expression!==undefined)){
        head.material=mat.clone();
        for(const side of [-1,1]){
          const brow=new THREE.Mesh(new THREE.BoxGeometry(radius*.55,radius*.09,radius*.08),dark);
          brow.position.set(side*radius*.38,radius*.48,radius*.88);brow.userData.side=side;face.add(brow);brows.push(brow);
        }
        tongue=new THREE.Mesh(new THREE.BoxGeometry(radius*.3,radius*.45,radius*.12),new THREE.MeshStandardMaterial({color:0x864b50,roughness:1}));
        tongue.position.set(0,-radius*.6,radius*.92);face.add(tongue);
      }
      let torso,legs=[];
      if(actor.kind==='creature') {
        box(aw,ah*.45,ad*.8,0,ah*.6,0);
        head.position.set(0,ah*.92,ad*.43);
        for(const x of [-aw*.35,aw*.35])for(const z of [-ad*.3,ad*.3]) box(aw*.18,ah*.4,aw*.18,x,ah*.2,z);
      } else {
        torso=box(aw,ah*.45,ad,0,ah*.575,0);head.position.y=ah*.9;
        for(const x of [-aw*.27,aw*.27]) {
          const thigh=box(aw*.38,ah*.175,ad*.8,x,ah*.2625,0);
          const shin=box(aw*.38,ah*.175,ad*.8,x,ah*.0875,0);
          legs.push({thigh,shin});
        }
      }
      let neck=null;
      if(actor.kind!=='creature'&&actor.keyframes.some(k=>k.neck_extension!==undefined)){
        neck=new THREE.Mesh(new THREE.CylinderGeometry(ah*.06,ah*.06,1,12),mat);neck.name='neck';body.add(neck);
      }
      const arms=[];
      if(actor.kind!=='creature')for(const [key,side] of HAND_SIDES){
        if(!actor.keyframes.some(k=>k[key]))continue;
        const upper=box(ah*.055,1,ah*.055,0,0,0),lower=box(ah*.05,1,ah*.05,0,0,0);
        upper.name=key+'-upper-arm';lower.name=key+'-forearm';
        const hand=new THREE.Mesh(new THREE.SphereGeometry(ah*.035,12,8),mat);body.add(hand);
        hand.name=key;arms.push({side,key,upper,lower,hand});
      }
      body.add(head);
      const upper=new THREE.Group();body.add(upper);
      if(torso){upper.add(torso,head);if(neck)upper.add(neck);for(const arm of arms)upper.add(arm.upper,arm.lower,arm.hand);}
      scene.add(root);this.actors.push({data:actor,root,body,head,torso,legs,arms,eyes,brows,tongue,mat,neck,upper});
      if(actor.keyframes.length>1) {
        const geo=new THREE.BufferGeometry().setFromPoints(actor.keyframes.map(k=>new THREE.Vector3(k.position[0],k.position[1]+.08,k.position[2])));
        const path=new THREE.Line(geo,new THREE.LineBasicMaterial({color:actor.color,transparent:true,opacity:.6}));
        path.layers.set(1);scene.add(path);
      }
    }
    for(const screen of this.props.filter(p=>p.data.projection_screen)){
      const shadow=new THREE.Mesh(new THREE.BufferGeometry(),new THREE.MeshBasicMaterial({color:0x202020,side:THREE.DoubleSide,depthWrite:false}));
      shadow.name=screen.data.id+'-silhouette';shadow.layers.set(2);shadow.renderOrder=1;scene.add(shadow);
      this.silhouettes.push({screen,shadow});
    }
    this.camera.layers.enable(2);
    this.marker=new THREE.Group();this.marker.layers.set(1);
    const camBody=new THREE.Mesh(new THREE.BoxGeometry(.35,.25,.5),new THREE.MeshBasicMaterial({color:0x1b3d4b}));
    camBody.layers.set(1);this.marker.add(camBody);scene.add(this.marker);
    this.ray=new THREE.Line(new THREE.BufferGeometry(),new THREE.LineBasicMaterial({color:0x1b3d4b}));
    this.ray.layers.set(1);scene.add(this.ray);
    this.overview.layers.enable(1);this.top.layers.enable(1);
    // Fit the full 3D trajectory using the narrower field of view. Portrait
    // overviews otherwise clip flying subjects even with a correct aspect.
    const bounds=new THREE.Box3(new THREE.Vector3(-w/2,0,-d/2),new THREE.Vector3(w/2,h,d/2));
    for(const a of this.actors)for(const k of a.data.keyframes){
      const p=new THREE.Vector3(...k.position),[aw,ah,ad]=a.data.size_m;
      bounds.expandByPoint(p.clone().add(new THREE.Vector3(-aw/2,0,-ad/2)));
      bounds.expandByPoint(p.clone().add(new THREE.Vector3(aw/2,ah,ad/2)));
    }
    const center=bounds.getCenter(new THREE.Vector3()),size=bounds.getSize(new THREE.Vector3());
    const radius=size.length()/2,aspect=this.width/this.height;
    const halfFov=Math.min(Math.PI/8,Math.atan(Math.tan(Math.PI/8)*aspect));
    const distance=radius/Math.sin(halfFov)*1.08;
    this.overview.position.copy(center).add(new THREE.Vector3(.65,.9,.9).normalize().multiplyScalar(distance));
    this.overview.lookAt(center);
    if(this.controls){this.controls.target.copy(center);this.controls.update();}
    this.flyRadius=radius;if(this.fly)this.syncFly();
    this.overview.far=Math.max(3000,distance+radius*4);this.overview.updateProjectionMatrix();
    this.camera.far=Math.max(2000,bounds.max.y*4);
    this.top.far=Math.max(3000,bounds.max.y*4);
    const extent=Math.max(size.z,size.x/aspect)*1.12;
    this.top.left=-extent*(this.width/this.height)/2;this.top.right=-this.top.left;
    this.top.top=extent/2;this.top.bottom=-extent/2;
    this.top.position.set(center.x,Math.max(w,d,bounds.max.y)*2,center.z);this.top.up.set(0,0,-1);this.top.lookAt(center.x,0,center.z);this.top.updateProjectionMatrix();
    this.setTime(0);
  }
  setTime(time) {
    if(!this.group)return;
    const t=Math.max(0,Math.min(time,this.group.duration_s));
    for(const a of this.actors) {
      const k=sample(a.data.keyframes,t);a.root.position.fromArray(k.position);a.root.rotation.y=k.yaw||0;
      a.root.visible=k.visible!==false;
      const h=a.data.size_m[1],ad=a.data.size_m[2];
      // Poses (2026-09-14, six states shared with storyboard poses / modules/whitebox.POSES):
      // stand, sit, lie (face up), kneel (shins flat on the ground, torso upright),
      // crouch (squat: shins upright, thighs level, torso leaning forward), prone (lie face down).
      const pose=k.pose||'stand',lying=pose==='lie'||pose==='prone',seated=pose==='sit',kneel=pose==='kneel',crouch=pose==='crouch',low=seated||kneel||crouch;
      // Bend hips/knees for sitting; keep dimensions and ground anchors in meters.
      // Roll a lying performer about their longitudinal axis before lying/yaw; prone = lie rolled by pi (face down, same foot anchor and head direction).
      a.body.rotation.set(lying?-Math.PI/2:0,lying?((k.body_roll||0)+(pose==='prone'?Math.PI:0)):0,0,'XYZ');
      a.upper.rotation.y=k.torso_yaw||0;
      a.body.position.y=lying?h*.16:0;
      if(a.torso){
        a.torso.position.y=h*(low?.4:.575);a.head.position.y=h*(low?.725:.9)+(k.neck_extension||0);
        for(const {thigh,shin} of a.legs){
          // sit / crouch: thighs level, shins upright in front; kneel: thighs upright from the ground, shins flat behind the knees.
          thigh.rotation.x=(seated||crouch)?Math.PI/2:0;thigh.position.y=h*(seated||crouch?.175:kneel?.0875:.2625);thigh.position.z=(seated||crouch)?h*.0875:0;
          shin.rotation.x=kneel?Math.PI/2:0;shin.position.y=kneel?ad*.4:h*.0875;shin.position.z=(seated||crouch)?h*.175:kneel?-h*.0875:0;
        }
        // Lean the upper body about the hips without turning a standing
        // performer into a horizontal, bed-anchored lying performer.
        // A crouch leans forward by default (0.6 rad about a low hip) unless the keyframe gives its own bend.
        // kneel (#77): bend leans the upper body about the low hip (0.175h, top of the upright thighs);
        // thighs/shins and so the knee support points stay put. Other poses ignore bend (compiler warns).
        const bend=leanBend(k),hip=h*leanHip(pose);
        a.torso.rotation.x=bend;
        a.torso.position.z=(a.torso.position.y-hip)*Math.sin(bend);
        a.torso.position.y=hip+(a.torso.position.y-hip)*Math.cos(bend);
        a.head.rotation.set(bend+(k.head_pitch||0),k.head_yaw||0,0,'YXZ');
        a.head.position.z=(a.head.position.y-hip)*Math.sin(bend);
        a.head.position.y=hip+(a.head.position.y-hip)*Math.cos(bend);
        if(a.neck){
          const extension=k.neck_extension||0,cy=h*(low?.625:.8)+extension/2;
          a.neck.visible=extension>0;a.neck.scale.y=Math.max(.001,extension+.01);
          a.neck.rotation.x=bend;a.neck.position.set(0,hip+(cy-hip)*Math.cos(bend),(cy-hip)*Math.sin(bend));
        }
      }
      for(const arm of a.arms){
        // Shoulder follows the same hip lean as torso/head (#77, stand/crouch/kneel); see shoulderPoint.
        const shoulder=new THREE.Vector3(...shoulderPoint(a.data.size_m,k,arm.side));
        const seg=h*ARM_SEGMENT;
        let hand=new THREE.Vector3(...k[arm.key]);
        // Two fixed-length arm segments: elbow bends outward, with a stable pole. A target beyond
        // full extension is not reached by stretching the mesh: the hand stops on the straight arm
        // pointing at it (the compiler reports a reach warning).
        let delta=hand.clone().sub(shoulder),distance=delta.length();
        if(distance<1e-9){delta.set(0,-1e-9,0);distance=1e-9;}
        const axis=delta.clone().divideScalar(distance);
        if(distance>2*seg){hand=shoulder.clone().addScaledVector(axis,2*seg);delta=hand.clone().sub(shoulder);distance=2*seg;}
        const ref=new THREE.Vector3(arm.side,-.35,0);
        let pole=ref.clone().addScaledVector(axis,-ref.dot(axis));
        if(pole.lengthSq()<1e-12)pole.set(0,0,1).addScaledVector(axis,-axis.z);
        pole.normalize();
        const elbow=shoulder.clone().addScaledVector(delta,.5).addScaledVector(pole,Math.sqrt(Math.max(0,seg**2-(distance/2)**2)));
        for(const [mesh,start,end] of [[arm.upper,shoulder,elbow],[arm.lower,elbow,hand]]){
          const d=end.clone().sub(start);mesh.position.copy(start).add(end).multiplyScalar(.5);mesh.scale.y=seg;
          mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0,1,0),d.normalize());
        }
        arm.hand.position.copy(hand);
      }
      const morph=k.morph||0,target=a.data.morph_target;
      a.body.scale.set(...a.data.size_m.map((v,i)=>target?1+(target.size_m[i]/v-1)*morph:1));
      // Heads and limb thickness are height-based in both endpoint models.
      // Cancel the body's anisotropic width/depth blend for these meshes so
      // a morph handoff has exactly the same geometry as the target actor.
      const sx=a.body.scale.y/a.body.scale.x,sz=a.body.scale.y/a.body.scale.z;
      a.head.scale.set(sx,1,sz);
      if(a.neck){a.neck.scale.x=sx;a.neck.scale.z=sz;}
      for(const arm of a.arms){
        arm.hand.scale.set(sx,1,sz);
        // Arm segments rotate about their own axes; endpoints are authoritative.
      }
      a.mat.color.set(a.data.color);if(target)a.mat.color.lerp(new THREE.Color(target.color),morph);
      const expression=k.expression||0;
      if(a.tongue){
        a.head.material.color.copy(a.mat.color).lerp(new THREE.Color(0xe4e4dc),expression);
        for(const eye of a.eyes)eye.scale.y=1-.94*expression;
        for(const brow of a.brows){brow.visible=expression>0;brow.rotation.z=brow.userData.side*.65*expression;}
        a.tongue.visible=expression>0;a.tongue.scale.y=Math.max(.001,expression);
      }
    }
    // 切点容差 1e-6 s:镜头起点是浮点累加(1.1+2.2=3.3000000000000003),跳到下一镜 start 时不能落回上一镜(2026-11-05)
    const shot=this.group.cameras.find(c=>t<c.start+c.duration_s-SHOT_EPS)||this.group.cameras.at(-1);
    // Off-screen cast stays in spatial/top views without obscuring this shot.
    for(const a of this.actors){
      const layer=shot.visible_actor_ids&&!shot.visible_actor_ids.includes(a.data.id)?1:0;
      a.root.traverse(o=>o.layers.set(layer));
    }
    // 挂手道具(2026-11-05,prop.attach {actor, hand, offset}):关键帧 attached(默认 true,按帧保持)为真时道具中心 =
    // 该手世界位置 + 上身朝向下的 offset,朝向 = 上身朝向 × 道具自身 pitch/yaw/roll;为假时按关键帧世界坐标。
    // 与 modules/whitebox.py hand_anchor/prop_state 同口径。
    if(this.props.some(p=>p.data.attach))this.scene.updateMatrixWorld(true);
    for(const p of this.props){
      const {data,mesh}=p;
      const k=data.keyframes?sample(data.keyframes,t):data;
      // 关键帧没写朝向 / 缩放时回退到道具顶层值(2026-11-05;原先顶层 yaw 等被静默丢掉,前科 fengshen3 ep07 grp019 shiji-stone)
      const own=new THREE.Euler(k.pitch??data.pitch??0,k.yaw??data.yaw??0,k.roll??data.roll??0);
      let holder=null,arm=null;
      if(data.attach&&k.attached!==false){
        holder=this.actors.find(a=>a.data.id===data.attach.actor);
        arm=holder?.arms.find(x=>x.key===data.attach.hand)||null;
      }
      p.attachedNow=!!arm;
      let layer=0;
      if(arm){
        const q=holder.upper.getWorldQuaternion(new THREE.Quaternion());
        arm.hand.getWorldPosition(mesh.position).add(new THREE.Vector3(...(data.attach.offset||[0,0,0])).applyQuaternion(q));
        mesh.quaternion.copy(q).multiply(new THREE.Quaternion().setFromEuler(own));
        layer=holder.root.layers.mask===1?0:1;   // 挂在被本镜 visible_actor_ids 排除的人手上:随人一起只留在空间/俯视视角
      }else{
        // follow 软体:原点 = 头部关键帧位置,身体按世界轨迹拖在后面,不再整体旋转(yaw/pitch 只定轨迹起点前的身体朝向)
        mesh.position.fromArray(k.position||data.position||[0,0,0]);
        if(data.follow==='head')mesh.rotation.set(0,0,0);else mesh.rotation.copy(own);
      }
      mesh.traverse(o=>o.layers.set(layer));
      mesh.scale.fromArray(k.scale||data.scale||[1,1,1]);
      mesh.visible=k.visible!==false&&(!arm||holder.root.visible)&&(!data.shot_ids||data.shot_ids.includes(shot.shot_id));
      if(p.tube)updateTube(p.tube,data,k,t);
      // 模型铰链(2026-11-08):关键帧 joints {零件 id: 弧度} 绕该零件铰链轴转,缺省 0
      if(p.joints){const jv=k.joints||{};for(const id in p.joints){const j=p.joints[id];j.node.rotation.set(0,0,0);j.node.rotation[j.axis]=+(jv[id]||0);}}
    }
    this.scene.updateMatrixWorld(true);
    for(const {screen,shadow} of this.silhouettes){
      const spec=screen.data.projection_screen;
      shadow.visible=screen.mesh.visible&&(!spec.shot_ids||spec.shot_ids.includes(shot.shot_id));
      if(!shadow.visible)continue;
      const bounds=new THREE.Box3().setFromObject(screen.mesh),positions=[];
      for(const a of this.actors.filter(a=>a.root.visible&&spec.actor_ids.includes(a.data.id))){
        a.root.traverse(o=>{
          if(!o.isMesh)return;
          for(let p=o;p;p=p.parent)if(!p.visible)return;
          const projected=projectSilhouette(o.geometry,o.matrixWorld,bounds,bounds.min.z-.003);
          const values=projected.attributes.position.array;
          for(const v of values)positions.push(v);
          projected.dispose();
        });
      }
      shadow.geometry.dispose();shadow.geometry=new THREE.BufferGeometry().setAttribute('position',new THREE.Float32BufferAttribute(positions,3));
    }
    const k=sample(shot.keyframes,t-shot.start);this.shotId=shot.shot_id;
    this.camera.position.fromArray(k.position);this.camera.lookAt(new THREE.Vector3(...k.target));
    this.camera.fov=k.fov;this.camera.updateProjectionMatrix();
    this.marker.position.copy(this.camera.position);this.marker.quaternion.copy(this.camera.quaternion);
    this.ray.geometry.dispose();this.ray.geometry=new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...k.position),new THREE.Vector3(...k.target)]);
  }
  // target: a 2D canvas to copy the frame into (defaults to this.canvas when the
  // WebGL context is shared); ignored when rendering straight into an own context.
  render(view='overview', target=null) {
    if(!this.scene)return;
    if(this.controls)this.controls.enabled=view==='overview'&&!this.fly;
    this.marker.visible=!!this.group;this.ray.visible=!!this.group;
    const real=view==='real';
    for(const m of this.solids)m.visible=!real;
    if(this.realPlane)this.realPlane.visible=real;
    const gl=this.renderer.domElement;
    if(this.shared&&(gl.width!==this.width||gl.height!==this.height))this.renderer.setSize(this.width,this.height,false);
    this.renderer.render(this.scene,view==='camera'?this.camera:(view==='top'||real)?this.top:this.overview);
    const out=target||(this.shared?this.canvas:null);
    if(!out||out===gl)return;
    if(out.width!==this.width||out.height!==this.height){out.width=this.width;out.height=this.height;}
    out.getContext('2d').drawImage(gl,0,0);
  }
  dispose(){this.controls?.dispose();this.disposeScene();if(!this.shared){this.renderer.dispose();this.renderer.forceContextLoss();}}
}
