/* 预览页之间的场次/分镜互跳(2026-09-12)
 * 链接:href="/preview/<page>?project=<p>&ep=<ep>#scene=S01" | "#shot=sh020"(分镜页) | "#shot=S01-03"(故事板镜行) | "#group=grp011"
 *      | "/preview/scenes?project=<p>&id=<SCN-ID>#plate=<key>"(场景预览页分镜背景图,分镜页「起点 · 复用库图」文字链,2026-09-15)
 * 目标页:三页 load() 都会 replaceState 把 URL 改写成 ?project&ep(hash 随之丢失),所以本脚本在 <head> 里
 * 解析时就把 hash 读走存起来;页面渲染 + 滚动位置恢复之后调 JumpAnchor.go({find, scroll}) 定位并高亮,
 * 只消费一次(之后刷新回到 localStorage 记忆的位置)。找不到目标时页顶提示,停在原位。
 * 只显示图标不显示文字(用户 2026-09-12 定);来源页只对目标真实存在的场次/镜出链接,存在性由端点给
 * (script: board_scenes/shot_scenes;board: script_scenes/shot_scenes/shots[].final;storyboard: script_scenes/board_scene_nos/shots[].board_key)。 */
window.JumpAnchor=(function(){
  var want=null;
  (function take(){
    var m=/^#(scene|shot|group|plate)=(.+)$/.exec(location.hash||'');   // plate=<库 key>:场景预览页分镜背景图(2026-09-15)
    if(m){try{want={kind:m[1],id:decodeURIComponent(m[2])}}catch(_){want={kind:m[1],id:m[2]}}}
  })();
  var PAGES={script:['/preview/script','📜'],board:['/preview/board','📋'],storyboard:['/preview/storyboard','🎦']};
  var TIPS={
    'script:scene':'在剧本预览中查看该场次',
    'board:scene':'在故事板中查看该场次',
    'storyboard:scene':'在分镜预览中查看该场次',
    'board:shot':'在故事板中查看该镜',
    'storyboard:shot':'在分镜预览中查看该镜',
    'storyboard:group':'在分镜预览中查看该组'};
  function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
  function tr(s){return (window.t?window.t(s):s)}
  /* 一条图标链接;page ∈ script|board|storyboard,kind ∈ scene|shot|group */
  function link(page,project,ep,kind,id){
    var pg=PAGES[page];if(!pg||!id)return '';
    var href=pg[0]+'?project='+encodeURIComponent(project||'demo')+(ep?'&ep='+encodeURIComponent(ep):'')+'#'+kind+'='+encodeURIComponent(id);
    var tip=tr(TIPS[page+':'+kind]||'')+' · '+id;
    return '<a class="jumplink" href="'+esc(href)+'" title="'+esc(tip)+'" data-no-i18n>'+pg[1]+'</a>';
  }
  /* 一组链接的容器(靠右);items 为 link() 串,空串自动略过 */
  function group(items){
    var h=(items||[]).filter(Boolean).join('');
    return h?'<span class="jumps">'+h+'</span>':'';
  }
  function toast(msg){
    var el=document.getElementById('jumptoast');
    if(!el){el=document.createElement('div');el.id='jumptoast';document.body.appendChild(el)}
    el.textContent=msg;el.classList.add('on');
    clearTimeout(toast._t);toast._t=setTimeout(function(){el.classList.remove('on')},4000);
  }
  /* 默认定位:按滚动容器算 scrollTop(留出置顶表头/置顶栏 offset),不用 smooth */
  function defaultScroll(el,box,offset){
    box=box||el.closest('#content')||document.scrollingElement;
    var y=el.getBoundingClientRect().top-box.getBoundingClientRect().top+box.scrollTop-(offset||0);
    box.scrollTop=Math.max(0,y);
  }
  /* opts.find(kind,id) → 元素或 null;opts.scroll(el) 可选(分镜页传自己的 jumpTo);opts.box / opts.offset 供默认定位 */
  function go(opts){
    if(!want)return false;
    var w=want;want=null;
    var el=opts&&opts.find?opts.find(w.kind,w.id):document.getElementById(w.id);
    if(!el){toast((window.I18N&&I18N.f?I18N.f('本页没有找到 {id},可能尚未产出或编号已变',{id:w.id}):'本页没有找到 '+w.id));return false}
    requestAnimationFrame(function(){
      (opts&&opts.scroll?opts.scroll:function(e){defaultScroll(e,opts&&opts.box,opts&&opts.offset)})(el);
      el.classList.add('jump-hit');
      setTimeout(function(){el.classList.remove('jump-hit')},2400);
    });
    return true;
  }
  var st=document.createElement('style');
  st.textContent=[
    '.jumps{display:inline-flex;gap:2px;align-items:center;margin-left:auto;flex:none}',
    '.jumplink{display:inline-flex;align-items:center;justify-content:center;width:22px;height:22px;border-radius:6px;border:1px solid var(--border,#2a3244);background:var(--panel2,#1c2233);text-decoration:none;font-size:13px;line-height:1;opacity:.75;cursor:pointer}',
    '.jumplink:hover{opacity:1;border-color:var(--accent,#6c9ef8)}',
    '.jump-hit{animation:jumpflash 2.4s ease-out}',
    'tr.jump-hit>td{animation:jumpflash 2.4s ease-out}',
    '@keyframes jumpflash{0%,40%{box-shadow:inset 0 0 0 2px var(--accent,#6c9ef8);background-color:rgba(108,158,248,.18)}100%{box-shadow:none}}',
    '#jumptoast{position:fixed;top:14px;left:50%;transform:translate(-50%,-30px);opacity:0;z-index:9999;background:var(--panel,#161b27);color:var(--text,#e6e9f0);border:1px solid var(--yellow,#fbbf24);border-radius:8px;padding:8px 14px;font-size:13px;box-shadow:0 6px 24px rgba(0,0,0,.4);transition:all .25s;pointer-events:none}',
    '#jumptoast.on{opacity:1;transform:translate(-50%,0)}'].join('\n');
  document.head.appendChild(st);
  return {pending:function(){return want},link:link,group:group,go:go,toast:toast};
})();
