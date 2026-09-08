import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {wbText,wbMessage,diagnosticTemplates} from '../apps/web/static/whitebox-i18n.js';

const staticURL=new URL('../apps/web/static/',import.meta.url);
const read=name=>readFileSync(new URL(name,staticURL),'utf8');
const source=read('whitebox-ui.js');
const keys=new Set([...source.matchAll(/\bt\('([^']+)'/g)].map(m=>m[1]));
for(const key of [
  '推断尺寸','已标定尺寸','暂停','播放','🧊白模','场次人物','场次人物，空间位置由白模调度继承',
  '场景建模','场景白模建模 Agent','白模调度与参考视频 Agent',
  '项目画幅超出白模参考视频尺寸范围',...diagnosticTemplates,
])keys.add(key);
const compiler=readFileSync(new URL('../modules/whitebox.py',import.meta.url),'utf8');
for(const m of compiler.matchAll(/warnings\.append\('([^']*[一-鿿][^']*)'\)/g))keys.add(m[1]);
const params={shot:'sh085',actor:'CHAR-0001',group:'grp048',aspect:'9:16',move:'dolly',
  width:9,depth:16,height:3,count:4,error:'GPU unavailable',marker:'[whitebox:dzg6/ep01/grp048]',
  scene:'SCN-0075',shots:'sh085',plan:'directing/ep01/whitebox_plans/grp048.json',
  model:'bible/scenes/SCN-0075/whitebox.json',preview:'directing/ep01/whitebox/episode.json'};
const expand=(s,p=params)=>s.replace(/\{(\w+)\}/g,(m,k)=>p[k]??m);
const placeholders=s=>[...s.matchAll(/\{\w+\}/g)].map(m=>m[0]).sort();
const escape=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function locale(lang){
  // Use the real language runtime, with browser boot deferred. No DOM observer
  // can mask a missing explicit translation in dynamic text/ARIA/data-loc.
  const context=vm.createContext({window:{},localStorage:{getItem:()=>lang},
    document:{write(){},readyState:'loading',addEventListener(){}},location:{pathname:'/preview_scenes.html'}});
  if(lang!=='zh')vm.runInContext(read(`i18n/${lang}.js`),context);
  vm.runInContext(read('i18n/i18n.js'),context);
  globalThis.I18N=context.window.I18N;
  return context.window.I18N_DICT;
}
const frames=new Map();let frameId=0;
globalThis.requestAnimationFrame=fn=>{frames.set(++frameId,fn);return frameId;};
globalThis.cancelAnimationFrame=id=>{frames.delete(id);};
const tick=now=>{const fns=[...frames.values()];frames.clear();for(const fn of fns)fn(now);};
globalThis.TestRenderer=class {load(){} dispose(){} setTime(){} render(){} shotId='sh085';};
// The storyboard page mounts one panel per generation group as it scrolls into
// view; here every observed host intersects immediately.
globalThis.IntersectionObserver=class {constructor(cb){this.cb=cb;} observe(el){this.cb([{target:el,isIntersecting:true}]);} disconnect(){}};
globalThis.document={createElement(){return host();},addEventListener(){},querySelectorAll(){return [];}};
const settle=async()=>{for(let i=0;i<8;i++)await new Promise(r=>setImmediate(r));};
async function mountGroup(ui,gid,ep='ep01'){
  const panel=host();panel.dataset={grp:gid};
  ui.mountGroups({querySelectorAll:()=>[panel]},'dzg6',ep);
  await settle();return panel;
}
// Substitute only the GPU renderer; exercise actual mount/play/seek/error code.
let uiSource=source.replace(/import\('\.\/whitebox-renderer\.js[^']*'\)/,'Promise.resolve({WhiteboxRenderer:globalThis.TestRenderer,sharedRenderer:()=>({})})');
uiSource=uiSource.replace(/from '(\.\/[^']+)'/g,(_,path)=>`from '${new URL(path,staticURL).href}'`);
const ui=await import('data:text/javascript;base64,'+Buffer.from(uiSource).toString('base64'));
function host(){
  const children={};
  return {isConnected:true,innerHTML:'',textContent:'',className:'',dataset:{},style:{setProperty(){}},
    querySelector(sel){return children[sel]||=(sel==='select'?{value:'top'}:{});},
    remove(){this.isConnected=false;}};
}
const scene={scene_id:'SCN-0075',dimensions_m:[9,3,16],inferred:true,objects:[],
  scale_basis:'按门宽推断，项目原文',warnings:['机位与 grp047 不连续。']};
const group={group_id:'grp048',scene_id:scene.scene_id,duration_s:2,
  actors:[{id:'CHAR-0001',label:'王三合',color:'#fff',size_m:[.5,1.7,.4]}],
  cameras:[{shot_id:'sh085'}],warnings:scene.warnings};
const episode={staging_version:3,scenes:{[scene.scene_id]:scene},groups:[group],errors:[]};
const respond=data=>({ok:true,json:async()=>data});
for(const lang of ['zh','en','ja','ko','vi','es','fr','de','id','pt','ru','ar']){
  const dict=locale(lang);
  for(const key of keys){
    if(lang!=='zh'){
      assert.ok(dict[key],`${lang}: missing ${key}`);
      assert.notEqual(dict[key],key,`${lang}: untranslated ${key}`);
      assert.deepEqual(placeholders(dict[key]),placeholders(key),`${lang}: placeholder mismatch ${key}`);
    }
    assert.equal(wbText(key,params),expand(lang==='zh'?key:dict[key]));
  }
  for(const key of diagnosticTemplates)assert.equal(wbMessage(expand(key)),wbText(key,params));
  assert.equal(wbMessage('用户自定义检查：人物面向北'), '用户自定义检查：人物面向北');
  assert.equal(wbText('未知键 {id}',{id:'CHAR-$&'}),'未知键 CHAR-$&');
  globalThis.fetch=async url=>respond(url.endsWith('/config')?{output:{aspect_preset:'douyin'}}:
    url.includes('/scenes/')?scene:episode);
  const panel=await mountGroup(ui,'grp048');
  assert.ok(panel.innerHTML.includes(escape(wbText('3D 白模'))),lang);
  assert.ok(panel.innerHTML.includes(escape(wbText('文件状态未知，请重启服务后刷新。'))),lang);
  assert.ok(panel.innerHTML.includes(`aria-label="${escape(wbText('白模时间'))}"`),lang);
  assert.ok(panel.innerHTML.includes(`aria-label="${escape(wbText('摄像机白模'))}"`),lang);
  assert.ok(panel.innerHTML.includes(`value="top" selected>${escape(wbText('俯视图'))}`),lang);
  assert.ok(panel.innerHTML.includes(escape(wbText('{width} × {depth} m · 高 {height} m · 网格 1 m · 画幅 {aspect}',params))),lang);
  for(const value of ['王三合',scene.scale_basis,params.marker,params.plan,params.model,params.preview])assert.ok(panel.innerHTML.includes(value),`${lang}: lost ${value}`);
  assert.ok(panel.innerHTML.includes(escape(wbMessage(scene.warnings[0]))),lang);
  const play=panel.querySelector('.wb-play');play.onclick();assert.equal(play.textContent,wbText('暂停'));
  play.onclick();assert.equal(play.textContent,wbText('播放'));
  play.onclick();tick(performance.now()+3000);assert.equal(play.textContent,wbText('重播'));
  panel.querySelector('input').oninput({target:{value:'0.5'}});assert.equal(play.textContent,wbText('播放'));
  ui.resetWhitebox();
  const sceneHost=host();await ui.mountScene(sceneHost,'dzg6','SCN-0075');
  assert.ok(sceneHost.innerHTML.includes(escape(wbText('空间与摄像机位置'))),lang);
  assert.ok(!sceneHost.innerHTML.includes('wb-camera'));
  ui.resetWhitebox();
  // Error prefixes and parameterized format diagnostics must both localize.
  globalThis.fetch=async()=>{throw Error('项目画幅无效：9:16');};
  const failed=host();await ui.mountScene(failed,'dzg6','SCN-0075');
  assert.equal(failed.textContent,wbText('场景白模尚不可用：{error}',{error:wbText('项目画幅无效：{aspect}',params)}));
}
delete globalThis.I18N;
// A calibrated scene alone must not mark inferred group staging as authored.
scene.inferred=false;scene.artifact_status={model:true};
group.authored=false;group.artifact_status={plan:false,preview:false,video:false};
globalThis.fetch=async url=>respond(url.endsWith('/config')?{output:{aspect_preset:'douyin'}}:episode);
let statusPanel=await mountGroup(ui,'grp048','ep02');
assert.ok(statusPanel.innerHTML.includes('已标定尺寸'));
assert.ok(statusPanel.innerHTML.includes('场景模型：已有'));
assert.ok(statusPanel.innerHTML.includes('调度计划：未生成'));
assert.ok(statusPanel.innerHTML.includes('参考视频：未生成'));
assert.ok(!statusPanel.innerHTML.includes('文件状态未知'));
ui.resetWhitebox();
group.authored=true;group.artifact_status={plan:true,preview:true,video:true};
statusPanel=await mountGroup(ui,'grp048','ep02');
assert.ok(statusPanel.innerHTML.includes('参考视频：已有'));
ui.resetWhitebox();
scene.inferred=true;
statusPanel=await mountGroup(ui,'grp048','ep02');
assert.ok(statusPanel.innerHTML.includes('推断尺寸'));
assert.ok(statusPanel.innerHTML.includes('参考视频：已有'));
ui.resetWhitebox();
// A group missing from the compiled episode reports the compiler's reason.
episode.groups=[];episode.errors=[{group_id:'grp048',error:'机位与 grp047 不连续。'}];
statusPanel=await mountGroup(ui,'grp048','ep02');
assert.equal(statusPanel.textContent,wbText('白模加载失败：{error}',{error:wbMessage('机位与 grp047 不连续。')}));
ui.resetWhitebox();
assert.equal(wbText('3D 白模'),'3D 白模');
assert.equal(wbText('建模依据与检查 ({count})',{count:0}),'建模依据与检查 (0)');
console.log(`Whitebox i18n: ${keys.size} keys, 12 locales; dynamic UI, playback, diagnostics and identifiers passed.`);
