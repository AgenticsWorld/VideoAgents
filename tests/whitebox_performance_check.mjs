import assert from 'node:assert/strict';
import * as T from '../apps/web/static/vendor/three/three.module.js';
import {sample,WhiteboxRenderer} from '../apps/web/static/whitebox-renderer.js';
const a={t:0,position:[1,0,2],yaw:Math.PI/2,pose:'stand',left_hand:[.2,1,.3],right_hand:[-.2,1,.3],expression:0,morph:0,neck_extension:0};
const b={...a,t:2,left_hand:[.2,1.7,.1],right_hand:[-.2,1.7,.1],expression:1,morph:1,neck_extension:.06};
assert.deepEqual(sample([a,b],1).left_hand,[.2,1.35,.2]);assert.equal(sample([a,b],1).expression,.5);
assert.equal(sample([{...a,hold:true},b],1).morph,0);
const actor={id:'source',size_m:[.45,1.6,.35],color:'#e63946',kind:'person',morph_target:{id:'target',size_m:[.48,1.65,.38],color:'#2ea043'},keyframes:[a,b]};
const renderer=Object.create(WhiteboxRenderer.prototype);
Object.assign(renderer,{width:960,height:540,scene:null,controls:null,camera:new T.PerspectiveCamera(),overview:new T.PerspectiveCamera(),top:new T.OrthographicCamera()});
renderer.load({dimensions_m:[10,4,8],objects:[]},{duration_s:2,actors:[actor],props:[{id:'ribbon',size_m:[.04,1,.04],position:[0,1,0],keyframes:[{t:0,position:[0,1,0],scale:[1,.1,1]},{t:2,position:[0,1,0],scale:[1,2,1]}]}],cameras:[{shot_id:'s1',start:0,duration_s:2,keyframes:[{t:0,position:[4,2,2],target:[1,1.4,2],fov:40},{t:2,position:[4,2,2],target:[1,1.4,2],fov:40}]}]});
renderer.setTime(1);renderer.scene.updateMatrixWorld(true);const rendered=renderer.actors[0];
assert.equal(rendered.arms.length,2);assert.ok(rendered.neck.visible);assert.ok(rendered.tongue.visible);assert.equal(rendered.eyes[0].scale.y,.53);
const actual=rendered.arms[0].hand.getWorldPosition(new T.Vector3());
const expected=new T.Vector3(.2,1.35,.2).multiply(rendered.body.scale).applyAxisAngle(new T.Vector3(0,1,0),Math.PI/2).add(new T.Vector3(1,0,2));assert.ok(actual.distanceTo(expected)<1e-9);
assert.equal(renderer.props[0].mesh.scale.y,1.05);
renderer.setTime(2);const endColor=rendered.mat.color.getHexString();renderer.setTime(0);assert.notEqual(rendered.mat.color.getHexString(),endColor);assert.equal(rendered.mat.color.getHexString(),'e63946');assert.equal(rendered.tongue.visible,false);assert.equal(rendered.eyes[0].scale.y,1);assert.equal(rendered.neck.visible,false);
// Turning the upper body/head keeps the feet fixed; rolling a lying actor
// changes their facing direction without spinning the bed's longitudinal axis.
const legBefore=rendered.legs[0].shin.getWorldPosition(new T.Vector3());
for(const k of actor.keyframes){k.torso_yaw=.6;k.head_yaw=.4;}
renderer.setTime(0);renderer.scene.updateMatrixWorld(true);
assert.ok(rendered.legs[0].shin.getWorldPosition(new T.Vector3()).distanceTo(legBefore)<1e-9);
const faceDirection=new T.Vector3(0,0,1).transformDirection(rendered.head.matrixWorld);
assert.ok(Math.abs(faceDirection.x-Math.cos(1))<1e-9);
for(const k of actor.keyframes){k.pose='lie';k.body_roll=-Math.PI/2;k.torso_yaw=0;k.head_yaw=0;}
renderer.setTime(0);renderer.scene.updateMatrixWorld(true);
assert.ok(new T.Vector3(0,0,1).transformDirection(rendered.head.matrixWorld).distanceTo(new T.Vector3(0,0,1))<1e-9);
for(const k of actor.keyframes){k.pose='stand';delete k.body_roll;}
renderer.setTime(0);assert.equal(rendered.body.rotation.y,0);
renderer.disposeScene();console.log('performance rendering, interpolation, rewind and prop scale passed');

// A curtain must mask the real actor while its clipped silhouette follows
// performance. Shadow copies only render in the camera layer; rewinding and
// cutting to a non-shadow shot must not leave stale outlines.
const sr=Object.create(WhiteboxRenderer.prototype);
Object.assign(sr,{width:960,height:540,scene:null,controls:null,camera:new T.PerspectiveCamera(),overview:new T.PerspectiveCamera(),top:new T.OrthographicCamera()});
const shadowActor={id:'shadow-actor',kind:'person',size_m:[.48,1.7,.38],color:'#e63946',faceless:true,keyframes:[{t:0,position:[0,0,0],pose:'stand',yaw:0,left_hand:[.3,1,.1]},{t:2,position:[.6,0,0],pose:'stand',yaw:0,left_hand:[.3,1.5,.1]}]};
const camera=(id,start)=>({shot_id:id,start,duration_s:1,keyframes:[{t:0,position:[0,1,-2],target:[0,1,-.5],fov:40},{t:1,position:[0,1,-2],target:[0,1,-.5],fov:40}]});
sr.load({dimensions_m:[4,3,4],objects:[]},{duration_s:2,actors:[shadowActor],props:[{id:'curtain',shape:'box',size_m:[1,1.2,.02],position:[0,1,-.5],projection_screen:{actor_ids:['shadow-actor'],shot_ids:['shadow']}}],cameras:[camera('shadow',0),camera('plain',1)]});
assert.equal(sr.actors[0].head.getObjectByName('face-direction').visible,false);
const shadow=sr.silhouettes[0].shadow;assert.ok(shadow.visible);assert.ok(sr.camera.layers.test(shadow.layers));assert.ok(!sr.top.layers.test(shadow.layers));
const initial=Array.from(shadow.geometry.attributes.position.array);
assert.ok(initial.length>0);
for(let i=0;i<initial.length;i+=3){assert.ok(Math.abs(initial[i])<=.500001);assert.ok(initial[i+1]>=.399999&&initial[i+1]<=1.600001);assert.ok(Math.abs(initial[i+2]+.513)<1e-6);}
sr.setTime(.75);assert.notDeepEqual(Array.from(shadow.geometry.attributes.position.array),initial);
sr.setTime(1.1);assert.equal(shadow.visible,false);assert.equal(sr.actors[0].root.visible,true);
sr.setTime(0);assert.deepEqual(Array.from(shadow.geometry.attributes.position.array),initial);
sr.disposeScene();console.log('curtain projection clipping, camera layer, faceless form and rewind passed');

// #77: kneel leans about the low hip with the knees fixed; shoulders follow the lean (stand/crouch
// too); both arm segments keep their fixed length and an unreachable hand stops on the straight arm.
// #121: hands follow anatomy -- facing +Z, left_hand hangs from the +X shoulder and right_hand from -X.
{
  const kr=Object.create(WhiteboxRenderer.prototype);
  Object.assign(kr,{width:960,height:540,scene:null,controls:null,camera:new T.PerspectiveCamera(),overview:new T.PerspectiveCamera(),top:new T.OrthographicCamera()});
  const h=1.7,far=[.2,.05,1.6];
  const kneeler={id:'k',kind:'person',size_m:[.48,h,.38],color:'#e63946',keyframes:[
    {t:0,position:[0,0,0],yaw:0,pose:'kneel',left_hand:[.25,.5,.2],right_hand:[-.25,.5,.2]},
    {t:1,position:[0,0,0],yaw:0,pose:'kneel',bend:1.2,left_hand:far,right_hand:[-.2,.05,.55]},
    {t:2,position:[0,0,0],yaw:0,pose:'stand',bend:.8,left_hand:[.25,.9,.3],right_hand:[-.25,.9,.3]}]};
  kr.load({dimensions_m:[6,3,6],objects:[],floor:'none',grid:false},{duration_s:2,actors:[kneeler],cameras:[{shot_id:'s',start:0,duration_s:2,keyframes:[{t:0,position:[0,1,4],target:[0,.5,0],fov:45}]}]});
  const a=kr.actors[0];
  const st=t=>{kr.setTime(t);kr.scene.updateMatrixWorld(true);return {head:a.head.getWorldPosition(new T.Vector3()),
    knees:a.legs.map(l=>l.thigh.getWorldPosition(new T.Vector3())),shins:a.legs.map(l=>l.shin.getWorldPosition(new T.Vector3()))};};
  const up=st(0),bent=st(1);
  assert.ok(bent.head.y<up.head.y-.2&&bent.head.z>up.head.z+.3,'kneel bend leans the head forward and down');
  for(let i=0;i<2;i++){assert.ok(bent.knees[i].distanceTo(up.knees[i])<1e-9);assert.ok(bent.shins[i].distanceTo(up.shins[i])<1e-9);}
  // shoulder rotates about the 0.175h hip by the same bend as the torso
  const hip=.175*h,sy=.58*h-hip,expect=new T.Vector3(.48*.52,hip+sy*Math.cos(1.2),sy*Math.sin(1.2));
  const plusX=a.arms.find(x=>x.side===1);   // +X shoulder = the actor's anatomical left (#121)
  assert.equal(plusX.key,'left_hand');assert.equal(a.arms.find(x=>x.key==='right_hand').side,-1);
  const upperEnd=new T.Vector3(0,-.5,0).applyMatrix4(plusX.upper.matrix);   // upper-arm segment starts at the shoulder
  assert.ok(upperEnd.distanceTo(expect)<1e-9,'shoulder follows the kneel lean');
  for(const arm of a.arms)for(const m of [arm.upper,arm.lower])assert.ok(Math.abs(m.scale.y-h*.21)<1e-12,'arm segments never stretch');
  // far target is beyond 0.42h: hand stops at full extension toward it
  const reach=plusX.hand.position.distanceTo(expect);
  assert.ok(Math.abs(reach-.42*h)<1e-9);
  const dir=new T.Vector3(...far).sub(expect).normalize(),got=plusX.hand.position.clone().sub(expect).normalize();
  assert.ok(dir.distanceTo(got)<1e-9);
  // reachable low target is reached exactly
  const minusX=a.arms.find(x=>x.side===-1);assert.equal(minusX.key,'right_hand');assert.ok(minusX.hand.position.distanceTo(new T.Vector3(-.2,.05,.55))<1e-9);
  // stand bend now also carries the shoulders forward
  st(2);const sh=.77*h-.35*h;
  assert.ok(new T.Vector3(0,-.5,0).applyMatrix4(plusX.upper.matrix).distanceTo(new T.Vector3(.48*.52,.35*h+sh*Math.cos(.8),sh*Math.sin(.8)))<1e-9);
  kr.disposeScene();console.log('kneel lean, shoulder follow and fixed-length reach passed');
}
