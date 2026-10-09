/* 预览页「✏️ 编辑/修改」浮窗(所有预览页共用)
 * 用法:<script src="/static/edit-popup.js"></script>(放 i18n.js 之后),
 *      EditPopup.open({project, compose, agent, dispatcher, target, onSent, hint})
 *   project = 项目 slug;compose = 预填进输入框的定位文本(如「修改 <对象>\n修改意见:」);
 *   agent   = 目标 Agent id;缺省或不存在时落**修改师**(00-orchestration/reviser,一人把相关改动全做完,
 *             无状态可并发、不排队在总制片后面);修改师不存在(旧服务端)时再落总制片;
 *   dispatcher = true 强制发给总制片(工作流页「调整 dag.json」等只有调度层能做的事);
 *   target  = 结构化定位 {kind,id,ep,files[],agents[],label},随 POST /api/v1/runs 的 target 字段发出,
 *             服务端据此给修改师拼「[修改单]」头并附代行工位 SOUL(见 core.py REVISION_KIND_AGENTS);
 *             非修改师目标时该字段被服务端忽略;
 *   onSent  = 可选回调 (runJson) => void,派单成功后拿到 /api/v1/runs 的返回(含 run_id),
 *             供页面就地标记「处理中」并轮询(故事板页草图重绘用)。
 *   hint    = 可选,替换底部提示文字(缺省「发出后到控制台可看 Agent 回复」;如场景预览背景图「修改」提示可用「+」附参考图),每次 open 重置。
 * 行为:在当前页右下角弹出非模态浮窗(无遮罩,不抢页面其它区域的点击/选择/复制),
 *      标头显示目标 Agent,输入框预填定位文本、光标落尾,用户接着写修改意见,
 *      目标为修改师时多一行「顺带重跑受影响的下游任务」勾选(默认不勾:只改用户指的这一处,
 *      下游是否重跑由总制片按变更记录决定;勾上则修改师完成后宿主立即把变更记录投递总制片),
 *      并查一次 /api/v1/runs:与本对象相关的任务正在跑时显示冲突提示(不拦发送);
 *      Shift+Enter 或「发送」按钮 POST /api/v1/runs(引擎/模型不传,由服务端回退顶栏全局设置);
 *      输入框右上角「+」按钮(file-attach.js,语音按钮开启时居右下角)可附本机文件:不上传,只把绝对路径拼在正文末尾;
 *      发出后浮窗折叠成一条「✅ 已发送给 …」小提示,数秒后自动消失;失败则保留原文并显示错误。
 * 控制台 index.html 仍保留 ?project=&compose=&agent= 预填通道(飞书等外部入口沿用),本文件不替代它。
 */
(function(){
  'use strict';
  var T=function(s){return (window.t||function(x){return x})(s)};
  var F=function(s,p){return window.I18N&&I18N.f?I18N.f(s,p):String(s).replace(/\{(\w+)\}/g,function(m,k){return k in p?p[k]:m})};
  var esc=function(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})};
  var HIDE_MS=4000;          // 已发送提示停留时长
  var REVISER='00-orchestration/reviser';
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
    '#edit-popup .ep-opt{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--dim,#8b93a7);cursor:pointer;user-select:none}',
    '#edit-popup .ep-opt[hidden]{display:none}',
    '#edit-popup .ep-opt input{margin:0}',
    '#edit-popup .ep-warn{color:var(--yellow,#fbbf24);font-size:12px;white-space:pre-wrap}',
    '#edit-popup .ep-warn:empty{display:none}',
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

  // 语音输入按钮(设置「语音输入」开启时才显示):共用 /static/voice-input.js,未随页面加载时按需拉取
  function attachVoice(ta){
    if(window.VoiceInput){VoiceInput.attach(ta);return}
    if(document.querySelector('script[data-voice-input]'))return;
    var s=document.createElement('script');s.src='/static/voice-input.js?v=20260911';s.setAttribute('data-voice-input','1');
    s.onload=function(){if(window.VoiceInput)VoiceInput.attach(ta)};
    document.head.appendChild(s);
  }
  // 「+」附件按钮:共用 /static/file-attach.js,未随页面加载时按需拉取;就位前发送不带附件(按钮尚未出现,用户也选不了)
  var attach=null;
  function attachFiles(ta){
    var go=function(){if(window.FileAttach&&!attach)attach=FileAttach.attach(ta)};
    if(window.FileAttach){go();return}
    if(document.querySelector('script[data-file-attach]'))return;
    var s=document.createElement('script');s.src='/static/file-attach.js?v=20260927a';s.setAttribute('data-file-attach','1');
    s.onload=go;
    document.head.appendChild(s);
  }
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
      +'<label class="ep-opt" hidden><input type="checkbox" class="ep-rerun"><span>'+esc(T('顺带重跑受影响的下游任务(交总制片标脏重派)'))+'</span></label>'
      +'<div class="ep-warn" role="status"></div>'
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
    attachVoice(ta);
    attachFiles(ta);
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
  // 目标解析:指定 id 存在则用之;dispatcher=true 落总制片;否则落修改师,修改师不存在时落总制片;
  // 列表拿不到时按 id 原样发送(无 id 时按修改师发,服务端 404 会回显到浮窗)
  function resolve(agents,id,dispatcher){
    var hit=id&&agents.find(function(a){return a.id===id});
    if(hit)return hit;
    var disp=agents.find(function(a){return a.dispatcher});
    if(dispatcher)return disp||(id?{id:id,name:id}:null);
    var rev=agents.find(function(a){return a.id===REVISER});
    return rev||disp||{id:id||REVISER,name:id||REVISER};
  }
  function cleanTarget(t){
    if(!t||typeof t!=='object')return null;
    var files=Array.isArray(t.files)?t.files.filter(Boolean).map(String):[];
    var agents=Array.isArray(t.agents)?t.agents.filter(Boolean).map(String):[];
    return {kind:String(t.kind||''),id:String(t.id||''),ep:String(t.ep||''),label:String(t.label||''),files:files,agents:agents};
  }
  // 冲突提示:同项目正在跑/排队的任务里,消息含本对象 id / 集号 / 文件名的都算相关(启发式,不拦发送)
  function checkConflicts(mine){
    var el=root.querySelector('.ep-warn');el.textContent='';
    var t=mine.target;if(!t)return;
    var keys=[t.id,t.ep].concat((t.files||[]).map(function(f){return String(f).split('/').pop()})).filter(function(k){return k&&k.length>=3});
    if(!keys.length)return;
    fetch('/api/v1/runs').then(function(r){return r.ok?r.json():[]}).then(function(runs){
      if(cur!==mine)return;
      var hits=(runs||[]).filter(function(r){
        if(r.project!==mine.project||(r.status!=='running'&&r.status!=='queued'))return false;
        var m=String(r.message||'');
        return keys.some(function(k){return m.indexOf(k)>=0});
      });
      if(!hits.length)return;
      var names={};hits.forEach(function(r){names[r.agent_name||r.agent]=1});
      el.textContent=F('⚠ 相关任务正在运行:{list},修改可能与之冲突',{list:Object.keys(names).join('、')});
    }).catch(function(){});
  }

  var cur=null;   // {project, agent:{id,name}, text, target, onSent}
  function open(opt){
    opt=opt||{};
    var el=ensure();
    clearTimeout(hideTimer);hideTimer=null;
    el.classList.remove('ep-sent');
    el.querySelector('.ep-err').textContent='';
    el.querySelector('.ep-warn').textContent='';
    el.querySelector('.ep-send').disabled=false;
    var optRow=el.querySelector('.ep-opt');optRow.hidden=true;el.querySelector('.ep-rerun').checked=false;
    var b=el.querySelector('.ep-agent b');b.textContent='…';
    var hint=el.querySelector('.ep-hint');hint.textContent=opt.hint||T('发出后到控制台可看 Agent 回复');hint.title=opt.hint||'';
    cur={project:opt.project||'demo',agent:{id:opt.agent||'',name:opt.agent||''},text:opt.compose||'',
         target:cleanTarget(opt.target),dispatcher:!!opt.dispatcher,
         onSent:typeof opt.onSent==='function'?opt.onSent:null};
    var ta=el.querySelector('textarea');
    ta.value=cur.text;
    if(attach)attach.clear();
    el.hidden=false;
    ta.focus({preventScroll:true});ta.setSelectionRange(ta.value.length,ta.value.length);
    var mine=cur;
    loadAgents().then(function(agents){
      if(cur!==mine)return;
      var a=resolve(agents,opt.agent,opt.dispatcher);
      if(!a){b.textContent=T('总制片');return}
      cur.agent={id:a.id,name:displayName(a)};
      b.textContent=cur.agent.name;b.title=a.id;
      if(a.id===REVISER){optRow.hidden=false;checkConflicts(mine);}
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
    if(attach)text=attach.compose(text);   // 附件:本机文件绝对路径拼在正文末尾,文件不上传
    var target=cur.agent, mine=cur;
    err.textContent='';btn.disabled=true;btn.textContent=T('发送中…');
    var go=function(){
      var body={agent:target.id,message:text,project:cur.project};
      if(target.id===REVISER){
        var t=cur.target||cleanTarget({});
        t.rerun_downstream=!!root.querySelector('.ep-rerun').checked;
        body.target=t;
      }
      return fetch('/api/v1/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    };
    // Agent 列表尚未返回时先等它,确保发给解析后的目标(而非空 id)
    var ready=target.id?Promise.resolve():loadAgents().then(function(agents){
      var a=resolve(agents,'',mine.dispatcher);if(a)target={id:a.id,name:displayName(a)};
    });
    ready.then(go).then(function(r){
      if(!r.ok)return r.json().catch(function(){return {}}).then(function(d){throw new Error(d.detail||String(r.status))});
      return r.json().catch(function(){return {}});
    }).then(function(j){
      if(cur!==mine)return;
      root.querySelector('.ep-done').textContent=F('✅ 已发送给 {agent}',{agent:target.name||target.id});
      root.classList.add('ep-sent');
      if(attach)attach.clear();
      hideTimer=setTimeout(close,HIDE_MS);
      if(mine.onSent){try{mine.onSent(j||{})}catch(_){}}
    }).catch(function(e){
      if(cur!==mine)return;
      err.textContent=T('发送失败:')+(e&&e.message||e);   // 失败保留原文,用户可改后重发
    }).then(function(){btn.disabled=false;btn.textContent=T('发送')});
  }

  window.EditPopup={open:open,close:close,REVISER:REVISER};
})();
