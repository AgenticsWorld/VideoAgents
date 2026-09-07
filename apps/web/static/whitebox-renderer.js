import * as THREE from './vendor/three/three.module.js';
import { OrbitControls } from './vendor/three/OrbitControls.js';

export function sample(keys, time) {
  if (time <= keys[0].t) return {...keys[0]};
  for (let i=1; i<keys.length; i++) {
    const a=keys[i-1], b=keys[i];
    if (time>=b.t) continue;
    let u=a.hold?0:(time-a.t)/(b.t-a.t);
    if(a.easing==='smooth') u=u*u*(3-2*u);
    const out={...a};
    for(const k of ['position','target']) if(a[k]) out[k]=a[k].map((v,j)=>v+(b[k][j]-v)*u);
    for(const k of ['fov','yaw']) if(a[k]!==undefined) {
      let delta=b[k]-a[k];
      if(k==='yaw') delta=((delta+Math.PI)%(2*Math.PI)+2*Math.PI)%(2*Math.PI)-Math.PI;
      out[k]=a[k]+delta*u;
    }
    return out;
  }
  return {...keys.at(-1)};
}

export class WhiteboxRenderer {
  constructor(canvas, {width=960,height=540, controls=true}={}) {
    this.canvas=canvas; this.width=width; this.height=height;
    this.renderer=new THREE.WebGLRenderer({canvas,antialias:true,preserveDrawingBuffer:true});
    this.renderer.setPixelRatio(1);
    this.renderer.setSize(width,height,false);
    this.renderer.setClearColor(0xe9ede9);
    this.camera=new THREE.PerspectiveCamera(50,width/height,.025,2000);
    this.overview=new THREE.PerspectiveCamera(45,width/height,.05,3000);
    this.top=new THREE.OrthographicCamera(-10,10,6,-6,.05,3000);
    this.controls=controls?new OrbitControls(this.overview,canvas):null;
    if(this.controls) {this.controls.enableDamping=false;this.controls.maxPolarAngle=Math.PI*.49;}
    this.scene=null; this.group=null; this.actors=[];
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
    this.disposeScene();this.sceneData=sceneData;this.group=group;this.actors=[];
    const scene=this.scene=new THREE.Scene();scene.background=new THREE.Color(0xe9ede9);
    scene.add(new THREE.HemisphereLight(0xffffff,0x8c968d,2.5));
    const sun=new THREE.DirectionalLight(0xffffff,2);sun.position.set(-8,20,10);scene.add(sun);
    const [w,h,d]=sceneData.dimensions_m;
    const floor=new THREE.Mesh(new THREE.BoxGeometry(w,.05,d),new THREE.MeshStandardMaterial({color:0xd6ddd3,roughness:1}));
    floor.position.y=-.05;scene.add(floor);
    const grid=new THREE.GridHelper(Math.ceil(Math.max(w,d)),Math.ceil(Math.max(w,d)),0xa8b5a5,0xc0cbbd);
    grid.position.y=.005;scene.add(grid);
    for(const obj of sceneData.objects) {
      let geo; const [x,y,z]=obj.size_m;
      if(obj.shape==='sphere') {geo=new THREE.SphereGeometry(.5,16,12);geo.scale(x,y,z);}
      else if(obj.shape==='cylinder') {geo=new THREE.CylinderGeometry(.5,.5,1,16);geo.scale(x,y,z);}
      else geo=new THREE.BoxGeometry(x,y,z);
      const material=new THREE.MeshStandardMaterial({color:obj.color||0xf3f0e8,roughness:1});
      const mesh=new THREE.Mesh(geo,material);mesh.position.fromArray(obj.position);mesh.rotation.y=obj.yaw||0;
      scene.add(mesh);
      const edges=new THREE.LineSegments(new THREE.EdgesGeometry(geo),new THREE.LineBasicMaterial({color:0x8d968d}));mesh.add(edges);
    }
    for(const actor of group?.actors||[]) {
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
      for(const side of [-1,1]) {
        const eye=new THREE.Mesh(new THREE.SphereGeometry(radius*.25,12,8),white);
        eye.position.set(side*radius*.38,radius*.2,radius*.88);
        const pupil=new THREE.Mesh(new THREE.SphereGeometry(radius*.13,12,8),dark);
        pupil.position.z=radius*.2;eye.add(pupil);face.add(eye);
      }
      const nose=new THREE.Mesh(new THREE.ConeGeometry(radius*.28,radius*.85,4),white);
      nose.name='face-forward';nose.rotation.x=Math.PI/2;
      nose.position.set(0,-radius*.08,radius*1.18);face.add(nose);head.add(face);
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
      body.add(head);
      scene.add(root);this.actors.push({data:actor,root,body,head,torso,legs});
      if(actor.keyframes.length>1) {
        const geo=new THREE.BufferGeometry().setFromPoints(actor.keyframes.map(k=>new THREE.Vector3(k.position[0],k.position[1]+.08,k.position[2])));
        const path=new THREE.Line(geo,new THREE.LineBasicMaterial({color:actor.color,transparent:true,opacity:.6}));
        path.layers.set(1);scene.add(path);
      }
    }
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
      const h=a.data.size_m[1];
      // Bend hips/knees for sitting; keep dimensions and ground anchors in meters.
      a.body.rotation.x=k.pose==='lie'?-Math.PI/2:0;
      a.body.position.y=k.pose==='lie'?h*.16:0;
      if(a.torso){
        const seated=k.pose==='sit';
        a.torso.position.y=h*(seated?.4:.575);a.head.position.y=h*(seated?.725:.9);
        for(const {thigh,shin} of a.legs){
          thigh.rotation.x=seated?Math.PI/2:0;thigh.position.y=h*(seated?.175:.2625);thigh.position.z=seated?h*.0875:0;
          shin.position.z=seated?h*.175:0;
        }
      }
    }
    const shot=this.group.cameras.find(c=>t<c.start+c.duration_s)||this.group.cameras.at(-1);
    const k=sample(shot.keyframes,t-shot.start);this.shotId=shot.shot_id;
    this.camera.position.fromArray(k.position);this.camera.lookAt(new THREE.Vector3(...k.target));
    this.camera.fov=k.fov;this.camera.updateProjectionMatrix();
    this.marker.position.copy(this.camera.position);this.marker.quaternion.copy(this.camera.quaternion);
    this.ray.geometry.dispose();this.ray.geometry=new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(...k.position),new THREE.Vector3(...k.target)]);
  }
  render(view='overview') {
    if(!this.scene)return;
    if(this.controls)this.controls.enabled=view==='overview';
    this.marker.visible=!!this.group;this.ray.visible=!!this.group;
    this.renderer.render(this.scene,view==='camera'?this.camera:view==='top'?this.top:this.overview);
  }
  dispose(){this.controls?.dispose();this.disposeScene();this.renderer.dispose();this.renderer.forceContextLoss();}
}
