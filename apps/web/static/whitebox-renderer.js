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
    for(const k of ['position','target','left_hand','right_hand','scale']) if(a[k]) out[k]=a[k].map((v,j)=>v+(b[k][j]-v)*u);
    for(const k of ['fov','yaw']) if(a[k]!==undefined) {
      let delta=b[k]-a[k];
      if(k==='yaw') delta=((delta+Math.PI)%(2*Math.PI)+2*Math.PI)%(2*Math.PI)-Math.PI;
      out[k]=a[k]+delta*u;
    }
    for(const k of ['bend','pitch','roll','head_pitch','head_yaw','torso_yaw','body_roll','neck_extension','expression','morph'])if(a[k]!==undefined||b[k]!==undefined)out[k]=(a[k]||0)+((b[k]||0)-(a[k]||0))*u;
    return out;
  }
  return {...keys.at(-1)};
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
    const floor=new THREE.Mesh(new THREE.BoxGeometry(w,.05,d),new THREE.MeshStandardMaterial({color:0xd6ddd3,roughness:1}));
    floor.position.y=-.05;scene.add(floor);
    const grid=new THREE.GridHelper(Math.ceil(Math.max(w,d)),Math.ceil(Math.max(w,d)),0xa8b5a5,0xc0cbbd);
    grid.position.y=.005;scene.add(grid);
    const propIds=new Set((group?.props||[]).map(p=>p.id));
    // A group can animate an existing scene prop without leaving a duplicate.
    for(const obj of [...sceneData.objects.filter(o=>!propIds.has(o.id)),...(group?.props||[])]) {
      let geo; const [x,y,z]=obj.size_m;
      if(obj.shape==='sphere') {geo=new THREE.SphereGeometry(.5,16,12);geo.scale(x,y,z);}
      else if(obj.shape==='cylinder') {geo=new THREE.CylinderGeometry(.5,.5,1,16);geo.scale(x,y,z);}
      else geo=new THREE.BoxGeometry(x,y,z);
      const material=new THREE.MeshStandardMaterial({color:obj.color||0xf3f0e8,roughness:1});
      const mesh=new THREE.Mesh(geo,material);mesh.name=obj.id;mesh.position.fromArray(obj.position);mesh.rotation.set(obj.pitch||0,obj.yaw||0,obj.roll||0);
      scene.add(mesh);
      if(group?.props?.includes(obj))this.props.push({data:obj,mesh});
      const edges=new THREE.LineSegments(new THREE.EdgesGeometry(geo),new THREE.LineBasicMaterial({color:0x8d968d}));mesh.add(edges);
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
      if(actor.kind!=='creature')for(const [side,key] of [[-1,'left_hand'],[1,'right_hand']]){
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
      const h=a.data.size_m[1];
      // Bend hips/knees for sitting; keep dimensions and ground anchors in meters.
      // Roll a lying performer about their longitudinal axis before lying/yaw.
      a.body.rotation.set(k.pose==='lie'?-Math.PI/2:0,k.pose==='lie'?(k.body_roll||0):0,0,'XYZ');
      a.upper.rotation.y=k.torso_yaw||0;
      a.body.position.y=k.pose==='lie'?h*.16:0;
      if(a.torso){
        const seated=k.pose==='sit';
        a.torso.position.y=h*(seated?.4:.575);a.head.position.y=h*(seated?.725:.9)+(k.neck_extension||0);
        for(const {thigh,shin} of a.legs){
          thigh.rotation.x=seated?Math.PI/2:0;thigh.position.y=h*(seated?.175:.2625);thigh.position.z=seated?h*.0875:0;
          shin.position.z=seated?h*.175:0;
        }
        // Lean the upper body about the hips without turning a standing
        // performer into a horizontal, bed-anchored lying performer.
        const bend=k.pose==='stand'?(k.bend||0):0,hip=h*.35;
        a.torso.rotation.x=bend;
        a.torso.position.z=(a.torso.position.y-hip)*Math.sin(bend);
        a.torso.position.y=hip+(a.torso.position.y-hip)*Math.cos(bend);
        a.head.rotation.set(bend+(k.head_pitch||0),k.head_yaw||0,0,'YXZ');
        a.head.position.z=(a.head.position.y-hip)*Math.sin(bend);
        a.head.position.y=hip+(a.head.position.y-hip)*Math.cos(bend);
        if(a.neck){
          const extension=k.neck_extension||0,cy=h*(seated?.625:.8)+extension/2;
          a.neck.visible=extension>0;a.neck.scale.y=Math.max(.001,extension+.01);
          a.neck.rotation.x=bend;a.neck.position.set(0,hip+(cy-hip)*Math.cos(bend),(cy-hip)*Math.sin(bend));
        }
      }
      for(const arm of a.arms){
        const shoulder=new THREE.Vector3(arm.side*a.data.size_m[0]*.52,h*(k.pose==='sit'?.58:.77),0);
        const hand=new THREE.Vector3(...k[arm.key]);
        // Two equal arm segments: elbow bends outward, with a stable pole.
        const delta=hand.clone().sub(shoulder),distance=delta.length(),axis=delta.clone().normalize();
        const pole=new THREE.Vector3(arm.side,-.35,0).addScaledVector(axis,-new THREE.Vector3(arm.side,-.35,0).dot(axis)).normalize();
        const elbow=shoulder.clone().addScaledVector(delta,.5).addScaledVector(pole,Math.sqrt(Math.max(0,(h*.21)**2-(distance/2)**2)));
        for(const [mesh,start,end] of [[arm.upper,shoulder,elbow],[arm.lower,elbow,hand]]){
          const d=end.clone().sub(start);mesh.position.copy(start).add(end).multiplyScalar(.5);mesh.scale.y=d.length();
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
    const shot=this.group.cameras.find(c=>t<c.start+c.duration_s)||this.group.cameras.at(-1);
    // Off-screen cast stays in spatial/top views without obscuring this shot.
    for(const a of this.actors){
      const layer=shot.visible_actor_ids&&!shot.visible_actor_ids.includes(a.data.id)?1:0;
      a.root.traverse(o=>o.layers.set(layer));
    }
    for(const {data,mesh} of this.props){
      const k=data.keyframes?sample(data.keyframes,t):data;
      mesh.position.fromArray(k.position);mesh.rotation.set(k.pitch||0,k.yaw||0,k.roll||0);
      mesh.scale.fromArray(k.scale||[1,1,1]);
      mesh.visible=k.visible!==false&&(!data.shot_ids||data.shot_ids.includes(shot.shot_id));
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
    if(this.controls)this.controls.enabled=view==='overview';
    this.marker.visible=!!this.group;this.ray.visible=!!this.group;
    const gl=this.renderer.domElement;
    if(this.shared&&(gl.width!==this.width||gl.height!==this.height))this.renderer.setSize(this.width,this.height,false);
    this.renderer.render(this.scene,view==='camera'?this.camera:view==='top'?this.top:this.overview);
    const out=target||(this.shared?this.canvas:null);
    if(!out||out===gl)return;
    if(out.width!==this.width||out.height!==this.height){out.width=this.width;out.height=this.height;}
    out.getContext('2d').drawImage(gl,0,0);
  }
  dispose(){this.controls?.dispose();this.disposeScene();if(!this.shared){this.renderer.dispose();this.renderer.forceContextLoss();}}
}
