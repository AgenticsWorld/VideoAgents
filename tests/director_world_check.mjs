// GPU-free regression tests for world alignment, layered rendering and resource lifetime.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import * as THREE from '../apps/web/static/vendor/three/three.module.js';
const pending=[];
class SparkStub extends THREE.Mesh {
  constructor(options){super(new THREE.BufferGeometry(),new THREE.MeshBasicMaterial());this.onDirty=options.onDirty;}
  dispose(){this.released=true;}
}
class SplatStub extends THREE.Group {
  constructor(){super();this.initialized=new Promise((resolve,reject)=>pending.push({resolve,reject,mesh:this}));this.numSplats=500000;this.releases=0;}
  dispose(){this.releases++;}
}
globalThis.worldTestSpark={SparkRenderer:SparkStub,SplatMesh:SplatStub};
globalThis.document=new EventTarget();document.hidden=false;
const path=new URL('../apps/web/static/director-world.js',import.meta.url);
let source=await readFile(path,'utf8');
source=source.replace("import { SparkRenderer, SplatMesh } from './vendor/spark/spark.module.js';",'const {SparkRenderer,SplatMesh}=globalThis.worldTestSpark;');
source=source.replace("'./vendor/three/three.module.js'",JSON.stringify(new URL('../apps/web/static/vendor/three/three.module.js',import.meta.url).href));
const {DirectorWorld,alignWorld}=await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const outer=new THREE.Group(),mesh=new THREE.Group();outer.add(mesh);
alignWorld(outer,mesh,{camera:{position:[1,1.6,.5]},metric_scale_factor:2,ground_plane_offset:1.5,scale_fix:.8,yaw_deg:90,yaw_fix_deg:0});
outer.updateMatrixWorld(true);
assert.ok(mesh.localToWorld(new THREE.Vector3()).distanceTo(new THREE.Vector3(1,1.6,.5))<1e-8);
assert.ok(mesh.localToWorld(new THREE.Vector3(0,1,0)).distanceTo(new THREE.Vector3(1,0,.5))<1e-8);
const calls=[],renderer={autoClear:true,render(scene,camera){calls.push({mask:camera.layers.mask,background:scene.background,clear:this.autoClear});},clearDepth(){calls.push('clearDepth');}};
const rr={width:960,height:540,renderer,scene:new THREE.Scene(),solids:[new THREE.Mesh()],realPlane:new THREE.Mesh(),marker:new THREE.Group(),ray:new THREE.Line(),group:{}};
const background=rr.scene.background=new THREE.Color('white');rr.scene.add(...rr.solids);
const pad=new EventTarget();pad.focus=()=>{};pad.setPointerCapture=()=>{};
let invalidations=0;const reports=[];
const metadata={alignment:{camera:{position:[1,1.6,.5]}},files:{splats:{'500k':'500.spz','100k':'100.spz'}}};
const world=new DirectorWorld(rr,pad,metadata,'/assets/',()=>invalidations++,(...r)=>reports.push(r));world.setEnabled(true);
const first=world.load('500k');const second=world.load('100k');
pending[1].resolve();await second;pending[0].resolve();await first;
assert.equal(world.resolution,'100k');assert.equal(world.ready,true);assert.ok(pending[0].mesh.releases>=1);
assert.equal(world.splat.layers.mask,8);assert.equal(world.spark.layers.mask,8);
assert.deepEqual(reports.filter(r=>r[0]==='ready').map(r=>r[1]),['100k']);
world.render(true);
assert.equal(calls[0].mask,8);assert.equal(calls[1],'clearDepth');assert.equal(calls[2].mask,3);
assert.equal(calls[2].clear,false);assert.equal(calls[2].background,null);
assert.equal(renderer.autoClear,true);assert.equal(rr.scene.background,background);assert.equal(rr.solids[0].visible,false);
assert.equal(world.camera.layers.mask,11);assert.equal(rr.marker.visible,true);
calls.length=0;world.render(false);assert.equal(calls.length,1);assert.equal(calls[0].mask,11);
const key=key=>Object.assign(new Event('keydown'),{key});pad.dispatchEvent(key('w'));
assert.equal(world.tick(.05),true);assert.ok(world.camera.position.z<.5);
pad.dispatchEvent(new Event('blur'));assert.equal(world.tick(.05),false);
world.home();assert.deepEqual(world.camera.position.toArray(),[1,1.6,.5]);
world.detach();assert.equal(world.outer.parent,null);rr.scene=new THREE.Scene();world.attach();assert.equal(world.outer.parent,rr.scene);
const last=world.load('500k');world.dispose();pending[2].resolve();await last;
assert.equal(world.spark.released,true);assert.equal(world.outer.parent,null);assert.ok(pending[2].mesh.releases>=1);
const before=invalidations;pad.dispatchEvent(key('w'));assert.equal(invalidations,before);
console.log('Director world: alignment, off-camera layers, object overlay, async switching, roaming, reuse and disposal passed.');
