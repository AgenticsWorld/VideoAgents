/* 预览页「✏️ 编辑/修改」浮窗(所有预览页共用)
 * 用法:<script src="/static/edit-popup.js"></script>(放 i18n.js 之后),
 *      EditPopup.open({project, compose, agent})
 *   project = 项目 slug;compose = 预填进输入框的定位文本(如「修改 <对象>\n修改意见:」);
 *   agent   = 目标 Agent id,缺省或不存在时落总制片(与控制台 ?compose= 跳转同一规则);
 *   onSent  = 可选回调 (runJson) => void,派单成功后拿到 /api/v1/runs 的返回(含 run_id),
 *             供页面就地标记「处理中」并轮询(故事板页草图重绘用)。
 * 行为:在当前页右下角弹出非模态浮窗(无遮罩,不抢页面其它区域的点击/选择/复制),
 *      标头显示目标 Agent,输入框预填定位文本、光标落尾,用户接着写修改意见,
 *      Shift+Enter 或「发送」按钮 POST /api/v1/runs(引擎/模型不传,由服务端回退顶栏全局设置);
 *      发出后浮窗折叠成一条「✅ 已发送给 …」小提示,数秒后自动消失;失败则保留原文并显示错误。
 * 控制台 index.html 仍保留 ?project=&compose=&agent= 预填通道(飞书等外部入口沿用),本文件不替代它。
 */
(function(){
  'use strict';
  var T=function(s){return (window.t||function(x){return x})(s)};
  var F=function(s,p){return window.I18N&&I18N.f?I18N.f(s,p):String(s).replace(/\{(\w+)\}/g,function(m,k){return k in p?p[k]:m})};
  var esc=function(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})};
  var HIDE_MS=4000;          // 已发送提示停留时长
  var agentsP=null;          // /api/v1/agents 缓存(整页生命周期内 Agent 列表不变)
  var root=null, hideTimer=null;

  var CSS=[
    '#edit-popup{position:fixed;right:16px;bottom:16px;z-index:97;width:min(420px,calc(100vw - 32px));',
      'background:var(--panel,#161a23);color:var(--text,#dde3ee);border:1px solid var(--accent,#6c9ef8);border-radius:10px;',
      'box-shadow:0 10px 32px rgba(0,0,0,.45);font:13px/1.55 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif;display:flex;flex-direction:column;gap:8px;padding:10px 12px 12px}',
    '#edit-popup[hidden]{display:none}',
    '#edit-popup .ep-head{display:flex;align-items:center;gap:8px}',
    '#edit-popup .ep-title{font-weight:600;font-size:13px;flex:none}',
    '#edit-popup .ep-agent{flex:1;min-width:0;color:var(--dim,#8b93a7);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '#edit-popup .ep-agent b{color:var(--accent,#6c9ef8);font-weight:600}',
    '#edit-popup .ep-close{flex:none;background:none;border:0;color:var(--dim,#8b93a7);font-size:16px;line-height:1;cursor:pointer;padding:0 2px}',
    '#edit-popup .ep-close:hover{color:var(--text,#dde3ee)}',
    '#edit-popup textarea{width:100%;height:132px;resize:vertical;background:var(--panel2,#1c2130);color:var(--text,#dde3ee);',
      'border:1px solid var(--border,#2a3040);border-radius:8px;padding:8px 10px;font:inherit;line-height:1.5}',
    '#edit-popup textarea:focus{outline:none;border-color:var(--accent,#6c9ef8)}',
    '#edit-popup .ep-foot{display:flex;align-items:center;gap:8px}',
    '#edit-popup .ep-hint{flex:1;min-width:0;color:var(--dim,#8b93a7);font-size:11.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '#edit-popup .ep-err{color:var(--red,#f87171);font-size:12px;white-space:pre-wrap}',
    '#edit-popup .ep-err:empty{display:none}',
    '#edit-popup button.ep-btn{background:var(--panel2,#1c2130);color:var(--text,#dde3ee);border:1px solid var(--border,#2a3040);border-radius:6px;padding:5px 12px;cursor:pointer;font-size:13px;font-family:inherit}',
    '#edit-popup button.ep-btn:hover{border-color:var(--accent,#6c9ef8)}',
    '#edit-popup button.ep-send{background:var(--accent,#6c9ef8);border-color:var(--accent,#6c9ef8);color:#0b1020;font-weight:600}',
    '#edit-popup button.ep-btn:disabled{opacity:.6;cursor:default}',
    /* 折叠态:发送后只剩一条小提示 */
    '#edit-popup.ep-sent{width:auto;max-width:min(420px,calc(100vw - 32px));padding:8px 14px;border-color:var(--green,#4ade80);cursor:default;',
      'animation:ep-fade 4s ease-in forwards}',
    '#edit-popup.ep-sent>*{display:none}',
    '#edit-popup.ep-sent .ep-done{display:block;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '@keyframes ep-fade{0%,75%{opacity:1}100%{opacity:0}}',
    '@media (prefers-reduced-motion:reduce){#edit-popup.ep-sent{animation:none}}',
  ].join('\n');

  function ensure(){
    if(root)return root;
    var st=document.createElement('style');st.textContent=CSS;document.head.appendChild(st);
    root=document.createElement('div');root.id='edit-popup';root.hidden=true;
    root.setAttribute('role','dialog');root.setAttribute('aria-modal','false');
    root.innerHTML=
      '<div class="ep-head"><span class="ep-title">✏️ '+esc(T('修改意见'))+'</span>'
      +'<span class="ep-agent">'+esc(T('发给:'))+'<b data-no-i18n></b></span>'
      +'<button type="button" class="ep-close" title="'+esc(T('关闭'))+'" aria-label="'+esc(T('关闭'))+'">×</button></div>'
      +'<textarea data-no-i18n placeholder="'+esc(T('接着写修改意见… (Shift+Enter 发送,Enter 换行)'))+'"></textarea>'
      +'<div class="ep-err" role="alert"></div>'
      +'<div class="ep-foot"><span class="ep-hint">'+esc(T('发出后到控制台可看 Agent 回复'))+'</span>'
      +'<button type="button" class="ep-btn ep-cancel">'+esc(T('取消'))+'</button>'
      +'<button type="button" class="ep-btn ep-send">'+esc(T('发送'))+'</button></div>'
      +'<div class="ep-done" role="status"></div>';
    document.body.appendChild(root);
    root.querySelector('.ep-close').onclick=close;
    root.querySelector('.ep-cancel').onclick=close;
    root.querySelector('.ep-send').onclick=send;
    var ta=root.querySelector('textarea');
    ta.addEventListener('keydown',function(e){
      if(e.key==='Enter'&&e.shiftKey){e.preventDefault();send();}   // 与控制台输入框同一快捷键
      else if(e.key==='Escape'){e.preventDefault();close();}         // 仅焦点在浮窗内时 Esc 关闭,不截获页面其它 Esc
    });
    return root;
  }

  // Agent 显示名:与控制台 agentName() 同规则(「中文名(English)」中文界面取前段,其它语言取词典或括号内)
  function displayName(a){
    var n=a&&a.name||'';
    var m=/^(.+?)[((]([^()()]+)[))]\s*$/.exec(n);
    if(!m)return T(n)||(a&&a.id)||'';
    if(!window.I18N||I18N.lang==='zh')return m[1];
    var key='agent·'+m[1], tr=T(key);
    return tr!==key?tr:m[2];
  }
  function loadAgents(){
    if(!agentsP)agentsP=fetch('/api/v1/agents').then(function(r){return r.ok?r.json():[]}).catch(function(){agentsP=null;return []});
    return agentsP;
  }
  // 目标解析:指定 id 存在则用之,否则落总制片(dispatcher);列表拿不到时按 id 原样发送
  function resolve(agents,id){
    var hit=id&&agents.find(function(a){return a.id===id});
    return hit||agents.find(function(a){return a.dispatcher})||(id?{id:id,name:id}:null);
  }

  var cur=null;   // {project, agent:{id,name}, text}
  function open(opt){
    opt=opt||{};
    var el=ensure();
    clearTimeout(hideTimer);hideTimer=null;
    el.classList.remove('ep-sent');
    el.querySelector('.ep-err').textContent='';
    el.querySelector('.ep-send').disabled=false;
    var b=el.querySelector('.ep-agent b');b.textContent='…';
    cur={project:opt.project||'demo',agent:{id:opt.agent||'',name:opt.agent||''},text:opt.compose||'',
         onSent:typeof opt.onSent==='function'?opt.onSent:null};
    var ta=el.querySelector('textarea');
    ta.value=cur.text;
    el.hidden=false;
    ta.focus({preventScroll:true});ta.setSelectionRange(ta.value.length,ta.value.length);
    var mine=cur;
    loadAgents().then(function(agents){
      if(cur!==mine)return;
      var a=resolve(agents,opt.agent);
      if(!a){b.textContent=T('总制片');return}
      cur.agent={id:a.id,name:displayName(a)};
      b.textContent=cur.agent.name;b.title=a.id;
    });
  }
  function close(){
    if(!root)return;
    clearTimeout(hideTimer);hideTimer=null;
    root.hidden=true;root.classList.remove('ep-sent');cur=null;
  }
  function send(){
    if(!root||!cur)return;
    var ta=root.querySelector('textarea'), btn=root.querySelector('.ep-send'), err=root.querySelector('.ep-err');
    var text=ta.value.trim();
    if(!text)return;
    var target=cur.agent, mine=cur;
    err.textContent='';btn.disabled=true;btn.textContent=T('发送中…');
    var go=function(){
      return fetch('/api/v1/runs',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({agent:target.id,message:text,project:cur.project})});
    };
    // Agent 列表尚未返回时先等它,确保发给解析后的目标(而非空 id)
    var ready=target.id?Promise.resolve():loadAgents().then(function(agents){
      var a=resolve(agents,'');if(a)target={id:a.id,name:displayName(a)};
    });
    ready.then(go).then(function(r){
      if(!r.ok)return r.json().catch(function(){return {}}).then(function(d){throw new Error(d.detail||String(r.status))});
      return r.json().catch(function(){return {}});
    }).then(function(j){
      if(cur!==mine)return;
      root.querySelector('.ep-done').textContent=F('✅ 已发送给 {agent}',{agent:target.name||target.id});
      root.classList.add('ep-sent');
      hideTimer=setTimeout(close,HIDE_MS);
      if(mine.onSent){try{mine.onSent(j||{})}catch(_){}}
    }).catch(function(e){
      if(cur!==mine)return;
      err.textContent=T('发送失败:')+(e&&e.message||e);   // 失败保留原文,用户可改后重发
    }).then(function(){btn.disabled=false;btn.textContent=T('发送')});
  }

  window.EditPopup={open:open,close:close};
})();
