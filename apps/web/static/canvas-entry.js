/* 「🖌 画板」入口按钮(2026-10-10;画板页 /preview/canvas,docs/image_canvas.md)。
 * 场景预览(场景图 / 分镜背景图)、人物预览(人物图 / 服装图)、生物 / 道具预览、故事板草图、成片发布页封面共用,
 * 放在各处「✏️ 修改」按钮后面。
 * 用法:<script src="/static/canvas-entry.js"></script>(放 i18n.js 之后)
 *      CanvasEntry.btn({url | file, kind, label, oid, project}) → 按钮 HTML
 *        url   = 图片地址(/api/v1/projects/<p>/artifacts/<相对路径>?v=… 或 /projects/<p>/<相对路径>),与 file(项目内相对路径)二选一;
 *        kind  = scene | plate | character | costume | creature | prop | sketch | cover(页面显示与设定归属用;怎么落最终版由服务端按路径判);
 *        label = 对象名(画板标题、发给修改师的修改单里用);oid = 对象编号(服装号等路径里判不出的);
 *        project 缺省取页面的 #project 输入框,再退回 localStorage。
 * 点击在当前窗口跳到画板(桌面端不允许开新窗口),画板页左上角「<」返回原页面。
 */
(function(){
  'use strict';
  var T=function(s){return (window.t||function(x){return x})(s)};
  var esc=function(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})};
  var st=document.createElement('style');
  st.textContent='.cvbtn{font-size:11px;padding:1px 7px;flex:none;background:var(--panel2,#1c2130);border:1px solid var(--border,#2a3040);border-radius:5px;'
    +'color:var(--dim,#8b93a7);cursor:pointer;white-space:nowrap;font-family:inherit;line-height:inherit}'
    +'.cvbtn:hover{color:var(--accent,#6c9ef8);border-color:var(--accent,#6c9ef8)}';
  document.head.appendChild(st);

  function relOf(url){
    var u=String(url||'').split('?')[0];
    var m=/^\/api\/v1\/projects\/[^/]+\/artifacts\/(.+)$/.exec(u)||/^\/projects\/[^/]+\/(.+)$/.exec(u);
    if(!m)return '';
    try{return decodeURIComponent(m[1])}catch(_){return m[1]}
  }
  function project(explicit){
    if(explicit)return explicit;
    var el=document.querySelector('#project');
    if(el&&String(el.value||'').trim())return String(el.value).trim();
    try{return JSON.parse(localStorage.getItem('webui_prefs')||'{}').project||'demo'}catch(_){return 'demo'}
  }
  function href(o){
    var file=o.file||relOf(o.url);
    if(!file)return '';
    var q='project='+encodeURIComponent(project(o.project))+'&file='+encodeURIComponent(file);
    ['kind','label','oid'].forEach(function(k){if(o[k])q+='&'+k+'='+encodeURIComponent(o[k])});
    return '/preview/canvas?'+q;
  }
  function btn(o){
    o=o||{};
    if(!(o.file||relOf(o.url)))return '';
    return '<button type="button" class="cvbtn" data-cv-file="'+esc(o.file||relOf(o.url))+'" data-cv-kind="'+esc(o.kind||'')+'" data-cv-label="'+esc(o.label||'')
      +'" data-cv-oid="'+esc(o.oid||'')+'" data-cv-project="'+esc(o.project||'')+'" title="'
      +esc(T('在画板里打开这张图:圈出位置修改、放大、裁剪翻转,保留全部历史版本,选一张作最终版'))+'">🖌 '+esc(T('画板'))+'</button>';
  }
  // 捕获阶段处理:按钮常嵌在带 onclick 的容器里(文件夹缩略图、可折叠标题),不让点击冒到它们身上
  document.addEventListener('click',function(e){
    var b=e.target&&e.target.closest?e.target.closest('.cvbtn'):null;
    if(!b)return;
    e.preventDefault();e.stopPropagation();
    var d=b.dataset;
    var to=href({file:d.cvFile,kind:d.cvKind,label:d.cvLabel,oid:d.cvOid,project:d.cvProject});
    if(to)location.href=to;
  },true);

  window.CanvasEntry={btn:btn,href:href,relOf:relOf};
})();
