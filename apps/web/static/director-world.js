// Director-only world backdrop. Uses the existing renderer and timeline objects.
import * as THREE from './vendor/three/three.module.js';
import { SparkRenderer, SplatMesh } from './vendor/spark/spark.module.js';

export function alignWorld(outer, splat, alignment={}) {
  const a=alignment, p=a.camera?.position||[0,1.6,0], fix=a.scale_fix||1;
  const offset=(a.ground_plane_offset||0)*fix;
  outer.rotation.set(0,THREE.MathUtils.degToRad((a.yaw_deg||0)+(a.yaw_fix_deg||0)),0);
  outer.position.set(p[0],p[1]-offset,p[2]);
  splat.scale.setScalar((a.metric_scale_factor||1)*fix);
  splat.quaternion.set(1,0,0,0);splat.position.set(0,offset,0);
}

// Spark has no request abort on SplatMesh. Keep its buffers alive until decoding
// settles, then release even when the user has already switched scenes.
function releaseMesh(mesh){
  if(!mesh)return;mesh.removeFromParent();
  Promise.resolve(mesh.initialized).then(()=>mesh.dispose(),()=>mesh.dispose());
}

export class DirectorWorld {
  constructor(rr, pad, metadata, base, invalidate, report) {
    this.rr=rr;this.metadata=metadata;this.base=base;this.invalidate=invalidate;this.report=report;
    this.disposed=false;this.enabled=false;this.ready=false;this.sequence=0;this.keys=new Set();
    this.camera=new THREE.PerspectiveCamera(60,rr.width/rr.height,.02,500);
    this.camera.layers.enable(1);this.camera.layers.enable(3);
    this.outer=new THREE.Group();this.spark=new SparkRenderer({renderer:rr.renderer,onDirty:()=>{if(this.enabled&&!this.disposed)invalidate();}});
    this.spark.layers.set(3);this.attach();this.home();
    this.events=new AbortController();const options={signal:this.events.signal};
    pad.tabIndex=0;
    pad.addEventListener('pointerdown',e=>{
      if(!this.enabled||e.button!==0)return;
      this.drag={x:e.clientX,y:e.clientY};pad.focus();pad.setPointerCapture(e.pointerId);
    },options);
    pad.addEventListener('pointermove',e=>{
      if(!this.enabled||!this.drag)return;
      this.yaw-=(e.clientX-this.drag.x)*.004;
      this.pitch=Math.max(-1.5,Math.min(1.5,this.pitch-(e.clientY-this.drag.y)*.004));
      this.drag={x:e.clientX,y:e.clientY};this.orient();invalidate();
    },options);
    for(const event of ['pointerup','pointercancel','lostpointercapture'])pad.addEventListener(event,()=>{this.drag=null;},options);
    pad.addEventListener('wheel',e=>{
      if(!this.enabled)return;e.preventDefault();
      const forward=new THREE.Vector3();this.camera.getWorldDirection(forward);
      this.camera.position.addScaledVector(forward,-e.deltaY*.002);invalidate();
    },{...options,passive:false});
    pad.addEventListener('keydown',e=>{
      const key=e.key.toLowerCase();if(!this.enabled||!['w','a','s','d','q','e','shift'].includes(key))return;
      e.preventDefault();this.keys.add(key);invalidate();
    },options);
    pad.addEventListener('keyup',e=>this.keys.delete(e.key.toLowerCase()),options);
    pad.addEventListener('blur',()=>this.resetInput(),options);
    document.addEventListener('visibilitychange',()=>{if(document.hidden)this.resetInput();else if(this.enabled)invalidate();},options);
  }
  attach(){this.rr.scene.add(this.outer,this.spark);}
  detach(){this.outer.removeFromParent();this.spark.removeFromParent();}
  resetInput(){this.keys.clear();this.drag=null;}
  setEnabled(enabled){this.enabled=enabled;this.outer.visible=enabled;this.spark.visible=enabled;if(!enabled)this.resetInput();}
  orient(){this.camera.rotation.set(this.pitch,this.yaw,0,'YXZ');this.camera.updateMatrixWorld(true);}
  home(){this.camera.position.fromArray(this.metadata.alignment?.camera?.position||[0,1.6,0]);this.yaw=THREE.MathUtils.degToRad(this.metadata.alignment?.yaw_deg||0);this.pitch=0;this.orient();this.invalidate();}
  tick(dt){
    if(!this.enabled||document.hidden)return false;
    const k=this.keys;if(!['w','a','s','d','q','e'].some(key=>k.has(key)))return false;
    const speed=(k.has('shift')?3:1.2)*Math.min(.1,dt);
    const f=new THREE.Vector3(-Math.sin(this.yaw),0,-Math.cos(this.yaw)),r=new THREE.Vector3(Math.cos(this.yaw),0,-Math.sin(this.yaw));
    this.camera.position.addScaledVector(f,((k.has('w')?1:0)-(k.has('s')?1:0))*speed);
    this.camera.position.addScaledVector(r,((k.has('d')?1:0)-(k.has('a')?1:0))*speed);
    this.camera.position.y+=((k.has('e')?1:0)-(k.has('q')?1:0))*speed;return true;
  }
  async load(resolution){
    const file=this.metadata.files.splats[resolution];if(!file||this.disposed)return;
    const sequence=++this.sequence;this.ready=false;this.resolution=resolution;
    releaseMesh(this.splat);
    const mesh=this.splat=new SplatMesh({url:this.base+file});
    mesh.layers.set(3);this.outer.add(mesh);alignWorld(this.outer,mesh,this.metadata.alignment);
    this.report('loading',resolution);this.invalidate();
    try{
      await mesh.initialized;
      if(this.disposed||sequence!==this.sequence){mesh.dispose();return;}
      this.ready=true;this.report('ready',resolution,mesh.numSplats);this.invalidate();
    }catch(e){
      if(this.disposed||sequence!==this.sequence){mesh.dispose();return;}
      mesh.removeFromParent();mesh.dispose();this.splat=null;
      this.report('error',resolution,e.message||String(e));this.invalidate();
    }
  }
  render(showThrough=true){
    const rr=this.rr,scene=rr.scene,camera=this.camera;
    for(const solid of rr.solids)solid.visible=false;
    if(rr.realPlane)rr.realPlane.visible=false;
    rr.marker.visible=!!rr.group;rr.ray.visible=!!rr.group;
    // Include off-shot props; explicit timeline visibility is still respected by the caller.
    const background=scene.background,autoClear=rr.renderer.autoClear;
    try{
      scene.background=new THREE.Color(0x101418);
      if(showThrough){
        camera.layers.set(3);rr.renderer.render(scene,camera);
        rr.renderer.autoClear=false;rr.renderer.clearDepth();scene.background=null;
        camera.layers.set(0);camera.layers.enable(1);rr.renderer.render(scene,camera);
      }else{camera.layers.set(0);camera.layers.enable(1);camera.layers.enable(3);rr.renderer.render(scene,camera);}
    }finally{
      scene.background=background;rr.renderer.autoClear=autoClear;
      camera.layers.set(0);camera.layers.enable(1);camera.layers.enable(3);
    }
  }
  dispose(){
    if(this.disposed)return;this.disposed=true;++this.sequence;this.events.abort();this.resetInput();this.detach();
    releaseMesh(this.splat);this.splat=null;this.spark.autoUpdate=false;clearTimeout(this.spark.updateTimeoutId);clearTimeout(this.spark.sortTimeoutId);this.spark.dispose();this.spark.geometry.dispose();this.spark.material.dispose();
  }
}
