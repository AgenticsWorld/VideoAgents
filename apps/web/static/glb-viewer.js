/* GLB 3D 模型预览(2026-11-25;参考文件页「🧊 3D 模型」板块与「生成 3D 模型」窗口共用)。
 * ES module,页面须先声明 importmap:{"three":"/static/vendor/three/three.module.js","three/addons/":"/static/vendor/three/addons/"}
 * (OrbitControls 用相对路径 import ./three.module.js,与 importmap 指向同一 URL,保证只有一份 three)。
 * 加载完挂 window.GlbViewer 并派发 window 事件 'glbviewer-ready'(普通 <script> 里用 GlbViewer.ready() 前先等这个事件):
 *   GlbViewer.thumb(url)                 → Promise<dataURL>  卡片缩略图:共用一个离屏渲染器按队列逐个渲染(浏览器 WebGL 上下文有上限)
 *   GlbViewer.mount(el, url, {onStats})  → 控制器 {dispose(), reset(), ready: Promise}  在 el 里放可旋转查看的 3D 视图(带工具条)
 *   GlbViewer.open(url, {title, download}) 全屏弹窗查看(Esc / ✕ 关闭)
 * 解码:Draco(glTF 版 wasm)与 meshopt 压缩的 GLB 都能看;KTX2 贴图不支持(报错原样显示)。
 */
import * as THREE from 'three';
import { OrbitControls } from '/static/vendor/three/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';

const T = s => (window.t || (x => x))(s);
const F = (s, p) => (window.I18N && I18N.f) ? I18N.f(s, p) : String(s).replace(/\{(\w+)\}/g, (m, k) => k in p ? p[k] : m);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const BG = 0x1a1f2b;
const THUMB_W = 384, THUMB_H = 288;

const css = document.createElement('style');
css.textContent = [
  '.gv-wrap{position:relative;width:100%;height:100%;min-height:200px;background:#1a1f2b;border-radius:10px;overflow:hidden}',
  '.gv-wrap canvas{display:block;width:100%;height:100%;outline:none;cursor:grab}',
  '.gv-wrap canvas:active{cursor:grabbing}',
  '.gv-msg{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--dim,#8b93a7);font-size:13px;text-align:center;padding:16px;pointer-events:none}',
  '.gv-msg.err{color:var(--red,#f87171)}',
  '.gv-bar{position:absolute;top:8px;right:8px;display:flex;gap:5px;flex-wrap:wrap;justify-content:flex-end}',
  '.gv-bar button{font-size:11.5px;padding:2px 8px;background:rgba(22,26,35,.82);color:var(--text,#dde3ee);border:1px solid var(--border,#2a3040);border-radius:5px;cursor:pointer}',
  '.gv-bar button:hover{border-color:var(--accent,#6c9ef8)}',
  '.gv-bar button.on{background:var(--accent,#6c9ef8);border-color:var(--accent,#6c9ef8);color:#0b1020}',
  '.gv-stats{position:absolute;left:10px;bottom:8px;font-size:11px;color:var(--dim,#8b93a7);background:rgba(22,26,35,.7);border-radius:5px;padding:1px 7px;pointer-events:none}',
  '.gv-modal{position:fixed;inset:0;z-index:99;background:rgba(0,0,0,.82);display:flex;align-items:center;justify-content:center;padding:24px}',
  '.gv-mbox{position:relative;width:min(1200px,100%);height:min(860px,100%);display:flex;flex-direction:column;background:var(--panel,#161a23);border:1px solid var(--border,#2a3040);border-radius:12px;overflow:hidden}',
  '.gv-mhead{display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid var(--border,#2a3040)}',
  '.gv-mhead h3{font-size:14px;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-family:ui-monospace,Menlo,monospace}',
  '.gv-mhead a,.gv-mhead button{font-size:12.5px}',
  '.gv-mhead .gv-hint{color:var(--dim,#8b93a7);font-size:12px}',
  '.gv-mbody{flex:1;min-height:0;padding:10px}',
].join('\n');
document.head.appendChild(css);

/* ---------- 加载 ---------- */
let LOADER = null;
function loader() {
  if (LOADER) return LOADER;
  const draco = new DRACOLoader();
  draco.setDecoderPath('/static/vendor/three/addons/libs/draco/gltf/');
  draco.setDecoderConfig({ type: 'wasm' });
  LOADER = new GLTFLoader();
  LOADER.setDRACOLoader(draco);
  LOADER.setMeshoptDecoder(MeshoptDecoder);
  return LOADER;
}
/* 没有材质的模型(Hunyuan3D 等只出白模时,trimesh 导出的 GLB 既无材质也无法线):GLTFLoader 给的 glTF 默认材质
   是全金属 + 平直着色,在环境光下一片白、看不出形体 → 换成哑光浅灰并补平滑法线 */
let MATTE = null;
function normalizeUntextured(g) {
  if ((g.parser?.json?.materials || []).length) return;
  if (!MATTE) MATTE = new THREE.MeshStandardMaterial({ color: 0xbfc4cc, roughness: 0.75, metalness: 0 });
  g.scene.traverse(o => {
    if (!o.isMesh) return;
    if (!o.geometry.attributes.normal) o.geometry.computeVertexNormals();
    o.material = MATTE;
  });
}
function load(url) {
  return new Promise((resolve, reject) => loader().load(url, g => { normalizeUntextured(g); resolve(g); }, undefined,
    e => reject(e instanceof Error ? e : new Error(String(e && (e.message || e.target?.statusText) || e)))));
}
function disposeTree(obj) {
  obj.traverse(o => {
    if (o.geometry) o.geometry.dispose();
    const mats = o.material ? (Array.isArray(o.material) ? o.material : [o.material]) : [];
    mats.forEach(m => {
      if (m === MATTE) return;                     // 共用的哑光材质,别随某个模型释放
      Object.values(m).forEach(v => { if (v && v.isTexture) v.dispose(); });
      m.dispose();
    });
  });
}
function statsOf(root) {
  let tris = 0, verts = 0, meshes = 0;
  const mats = new Set(), texs = new Set();
  root.traverse(o => {
    if (!o.isMesh) return;
    meshes++;
    const g = o.geometry, pos = g.attributes.position;
    if (pos) verts += pos.count;
    tris += g.index ? g.index.count / 3 : (pos ? pos.count / 3 : 0);
    (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => {
      if (!m) return;
      mats.add(m);
      Object.values(m).forEach(v => { if (v && v.isTexture) texs.add(v); });
    });
  });
  const size = new THREE.Box3().setFromObject(root).getSize(new THREE.Vector3());
  return { tris: Math.round(tris), verts, meshes, materials: mats.size, textures: texs.size, size: [size.x, size.y, size.z] };
}
function fmtCount(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + 'K';
  return String(n);
}
function statsText(s) {
  return F('{tris} 三角面 · {tex} 张贴图 · 尺寸 {w} × {h} × {d}', {
    tris: fmtCount(s.tris), tex: s.textures, w: s.size[0].toFixed(2), h: s.size[1].toFixed(2), d: s.size[2].toFixed(2)
  });
}

/* 场景:中性室内环境光(PBR 材质靠它出质感)+ 一盏补光;模型居中、摆到网格上 */
function makeScene(renderer) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(BG);
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = 0.7;                // 满强度时浅色 / 白模表面发白、看不出起伏
  pmrem.dispose();
  const key = new THREE.DirectionalLight(0xffffff, 1.0);
  key.position.set(3, 5, 4);
  scene.add(key, new THREE.HemisphereLight(0xffffff, 0x404858, 0.3));
  return scene;
}
function frame(root, camera, controls) {
  const box = new THREE.Box3().setFromObject(root);
  const size = box.getSize(new THREE.Vector3()), center = box.getCenter(new THREE.Vector3());
  root.position.sub(center);                       // 模型中心挪到原点,底面落在 y = -size.y/2
  const radius = Math.max(size.length() / 2, 1e-3);
  const dist = radius / Math.sin(THREE.MathUtils.degToRad(camera.fov / 2)) * 1.05;
  const dir = new THREE.Vector3(0.55, 0.32, 1).normalize();   // 斜前上方 3/4 视角
  camera.position.copy(dir.multiplyScalar(dist));
  camera.near = dist / 100; camera.far = dist * 100;
  camera.lookAt(0, 0, 0);                          // 缩略图没有 OrbitControls 帮着对准,这里自己对
  camera.updateProjectionMatrix();
  if (controls) { controls.target.set(0, 0, 0); controls.minDistance = radius * 0.2; controls.maxDistance = dist * 6; controls.update(); }
  return { size, radius, dist };
}
function newRenderer(opts) {
  const r = new THREE.WebGLRenderer({ antialias: true, ...opts });
  r.outputColorSpace = THREE.SRGBColorSpace;
  r.toneMapping = THREE.NeutralToneMapping;
  r.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  return r;
}

/* ---------- 缩略图:一个离屏渲染器,队列逐个渲染 ---------- */
let TR = null;
const THUMBS = new Map();        // url → Promise<dataURL>
let QUEUE = Promise.resolve();
function thumb(url) {
  if (THUMBS.has(url)) return THUMBS.get(url);
  const p = QUEUE.then(async () => {
    if (!TR) {
      TR = { renderer: newRenderer({ preserveDrawingBuffer: true }) };
      TR.renderer.setPixelRatio(1);
      TR.renderer.setSize(THUMB_W, THUMB_H, false);
      TR.scene = makeScene(TR.renderer);
      TR.camera = new THREE.PerspectiveCamera(35, THUMB_W / THUMB_H, 0.01, 1000);
    }
    const g = await load(url);
    const root = g.scene;
    TR.scene.add(root);
    try {
      frame(root, TR.camera, null);
      TR.renderer.render(TR.scene, TR.camera);
      return TR.renderer.domElement.toDataURL('image/webp', 0.85);
    } finally {
      TR.scene.remove(root);
      disposeTree(root);
    }
  });
  QUEUE = p.catch(() => {});
  THUMBS.set(url, p);
  p.catch(() => THUMBS.delete(url));
  return p;
}

/* ---------- 可旋转查看的视图 ---------- */
function mount(el, url, opts = {}) {
  el.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'gv-wrap';
  wrap.innerHTML = `<div class="gv-msg">${esc(T('加载 3D 模型…'))}</div>`;
  el.appendChild(wrap);
  const msg = wrap.querySelector('.gv-msg');
  const renderer = newRenderer();
  const canvas = renderer.domElement;
  canvas.tabIndex = 0;
  wrap.prepend(canvas);
  const scene = makeScene(renderer);
  const camera = new THREE.PerspectiveCamera(35, 1, 0.01, 1000);
  const controls = new OrbitControls(camera, canvas);
  controls.enableDamping = true;
  controls.autoRotateSpeed = 1.6;
  let disposed = false, root = null, grid = null, home = null, clay = null, orig = null;

  const size = () => {
    const w = wrap.clientWidth || 300, h = wrap.clientHeight || 200;
    renderer.setSize(w, h, false);
    camera.aspect = w / h; camera.updateProjectionMatrix();
  };
  const ro = new ResizeObserver(size);
  ro.observe(wrap);
  size();
  renderer.setAnimationLoop(() => { if (!disposed) { controls.update(); renderer.render(scene, camera); } });

  const bar = document.createElement('div');
  bar.className = 'gv-bar';
  const btn = (label, title, on) => {
    const b = document.createElement('button');
    b.type = 'button'; b.textContent = T(label); b.title = T(title); b.onclick = on;
    bar.appendChild(b); return b;
  };
  const toggle = (b, v) => { b.classList.toggle('on', v); return v; };
  const bRot = btn('自动旋转', '让模型自己转起来', () => { controls.autoRotate = toggle(bRot, !controls.autoRotate); });
  const bWire = btn('线框', '显示网格线框,看布线与面数分布', () => {
    const v = toggle(bWire, !bWire.classList.contains('on'));
    root && root.traverse(o => { if (o.isMesh) (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => { m.wireframe = v; }); });
  });
  const bClay = btn('灰模', '去掉贴图只看形体(灰色材质)', () => {
    const v = toggle(bClay, !bClay.classList.contains('on'));
    if (!root) return;
    if (!clay) clay = new THREE.MeshStandardMaterial({ color: 0xbfc4cc, roughness: 0.75, metalness: 0 });
    if (!orig) { orig = new Map(); root.traverse(o => { if (o.isMesh) orig.set(o, o.material); }); }
    root.traverse(o => { if (o.isMesh) o.material = v ? clay : orig.get(o); });
    if (bWire.classList.contains('on')) clay.wireframe = true;
  });
  const bGrid = btn('地面网格', '显示 / 隐藏地面网格', () => { if (grid) grid.visible = toggle(bGrid, !grid.visible); });
  btn('重置视角', '回到初始角度(也可双击画面)', () => reset());
  canvas.addEventListener('dblclick', () => reset());
  wrap.appendChild(bar);
  const stats = document.createElement('div');
  stats.className = 'gv-stats';
  stats.hidden = true;
  wrap.appendChild(stats);

  function reset() {
    if (!home) return;
    camera.position.copy(home.pos); controls.target.set(0, 0, 0); controls.update();
  }
  const ready = load(url).then(g => {
    if (disposed) { disposeTree(g.scene); return null; }
    root = g.scene;
    scene.add(root);
    const f = frame(root, camera, controls);
    home = { pos: camera.position.clone() };
    grid = new THREE.GridHelper(f.radius * 4, 20, 0x3a4256, 0x2a3040);
    grid.position.y = -f.size.y / 2;
    scene.add(grid);
    toggle(bGrid, true);
    msg.remove();
    const s = statsOf(root);
    stats.textContent = statsText(s); stats.hidden = false;
    if (opts.onStats) try { opts.onStats(s); } catch (_) { /* 回调出错不影响显示 */ }
    if (opts.autoRotate) controls.autoRotate = toggle(bRot, true);
    return s;
  }).catch(e => {
    if (disposed) return null;
    msg.classList.add('err');
    msg.textContent = T('无法预览该模型:') + (e && e.message || e);
    throw e;
  });
  ready.catch(() => {});

  function dispose() {
    if (disposed) return;
    disposed = true;
    renderer.setAnimationLoop(null);
    ro.disconnect();
    controls.dispose();
    if (root) disposeTree(root);
    if (clay) clay.dispose();
    if (scene.environment) scene.environment.dispose();
    renderer.dispose();
    renderer.forceContextLoss?.();
    el.innerHTML = '';
  }
  return { dispose, reset, ready };
}

/* ---------- 全屏弹窗 ---------- */
function open(url, opts = {}) {
  const m = document.createElement('div');
  m.className = 'gv-modal';
  m.innerHTML = `<div class="gv-mbox" role="dialog" aria-modal="true"><div class="gv-mhead"><h3 data-no-i18n></h3>`
    + `<span class="gv-hint">${esc(T('拖动旋转 · 滚轮缩放 · 右键平移'))}</span>`
    + (opts.download === false ? '' : `<a class="gv-dl" target="_blank" rel="noopener" download>⬇ ${esc(T('下载'))}</a>`)
    + `<button type="button" class="gv-x" title="${esc(T('关闭'))}">✕</button></div><div class="gv-mbody"></div></div>`;
  m.querySelector('h3').textContent = '🧊 ' + (opts.title || url.split('?')[0].split('/').pop());
  const dl = m.querySelector('.gv-dl');
  if (dl) dl.href = url;
  document.body.appendChild(m);
  const view = mount(m.querySelector('.gv-mbody'), url, { autoRotate: false });
  const close = () => { view.dispose(); m.remove(); document.removeEventListener('keydown', onKey, true); };
  const onKey = e => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
  document.addEventListener('keydown', onKey, true);
  m.querySelector('.gv-x').onclick = close;
  m.addEventListener('mousedown', e => { if (e.target === m) close(); });
  return { close, view };
}

window.GlbViewer = { thumb, mount, open, statsText };
window.dispatchEvent(new Event('glbviewer-ready'));
