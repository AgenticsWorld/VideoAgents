/* 「+」附件按钮(控制台输入框 + 预览页「修改」浮窗共用)
 * 用法:<script src="/static/file-attach.js"></script>(放 i18n.js 之后;edit-popup.js 会按需自行加载)
 *      var att=FileAttach.attach(textarea, {storageKey:'webui_draft_files'})
 *        att.paths()        → 当前已附加的绝对路径数组
 *        att.compose(text)  → 正文末尾拼上「附件(本机文件绝对路径,请直接读取):\n- /abs/a.png」段落;无附件原样返回
 *        att.clear()        → 发送成功后清空标签;att.setPaths(list) 发送失败时把附件还给用户
 * 行为:按钮固定在输入框右上角(语音按钮开启时在右下角,同一列),点击选择本机文件。**文件不上传**,只把绝对路径附在消息末尾发给 Agent,
 *      Agent 与浏览器同机运行,按路径自行读取。选路径三级来源:
 *        ① 桌面客户端:Electron 原生对话框(window.videoagentsDesktop.pickFiles);
 *        ② 网页版:服务端在本机弹系统「选择文件」对话框(POST /actions/pick-files,macOS osascript /
 *           Windows PowerShell / Linux zenity|kdialog),浏览器与服务同机时可用;
 *        ③ 对话框不可用(无桌面环境、远程服务、桌面客户端连远程后端)时弹一个手填框,粘贴绝对路径,每行一个。
 *      Shift+点击「+」直接打开手填框。已选文件显示为输入框上方的小标签(文件名,悬停看全路径),可逐个 × 移除。
 *      storageKey 传入时附件列表随草稿存 localStorage(控制台整页重载后不丢);浮窗不传。
 */
(function(){
  'use strict';
  var T=function(s){return (window.t||function(x){return x})(s)};
  var F=function(s,p){return window.I18N&&I18N.f?I18N.f(s,p):String(s).replace(/\{(\w+)\}/g,function(m,k){return k in p?p[k]:m})};
  var esc=function(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})};
  var cssDone=false;

  var CSS=[
    '.fa-wrap{display:flex;flex-direction:column;flex:1;min-width:0;gap:6px}',
    '.fa-box{position:relative;display:flex;flex:1;min-width:0}',
    '.fa-box>textarea,.fa-box>.vi-wrap{flex:1;min-width:0}',
    '.fa-btn{position:absolute;right:9px;top:5px;width:26px;height:26px;border-radius:50%;z-index:2;display:flex;align-items:center;justify-content:center;',
      'border:1px solid var(--border,#2a3040);background:var(--panel2,#1c2130);color:var(--dim,#8b93a7);cursor:pointer;padding:0;font:inherit;line-height:1}',
    '.fa-btn:hover{color:var(--text,#dde3ee);border-color:var(--accent,#6c9ef8)}',
    '.fa-btn:disabled{opacity:.6;cursor:progress}',
    '.fa-btn svg{width:16px;height:16px;display:block}',
    /* 语音按钮与「+」同列:「+」右上角、麦克风右下角(控制台 64px 高输入框刚好放两枚 26px 按钮) */
    '.fa-box .vi-btn{top:auto;bottom:5px;right:9px;transform:none;width:26px;height:26px}',
    '.fa-box .vi-btn svg{width:20px;height:20px}',
    '.fa-chips{display:none;flex-wrap:wrap;gap:6px;min-width:0}',
    '.fa-chips.on{display:flex}',
    '.fa-chip{display:inline-flex;align-items:center;gap:4px;max-width:260px;font-size:12px;line-height:1.4;background:var(--panel2,#1c2130);color:var(--text,#dde3ee);',
      'border:1px solid var(--border,#2a3040);border-radius:14px;padding:2px 6px 2px 9px}',
    '.fa-chip .fa-nm{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}',
    '.fa-chip .fa-x{flex:none;border:0;background:none;color:var(--dim,#8b93a7);cursor:pointer;font-size:14px;line-height:1;padding:0 2px;font-family:inherit}',
    '.fa-chip .fa-x:hover{color:var(--red,#f87171)}',
    /* 手填框:贴在按钮上方 */
    '.fa-pop{position:absolute;right:6px;bottom:calc(100% + 6px);z-index:4;width:min(380px,calc(100% - 12px));background:var(--panel,#161a23);color:var(--text,#dde3ee);',
      'border:1px solid var(--accent,#6c9ef8);border-radius:8px;padding:8px 10px;box-shadow:0 8px 24px rgba(0,0,0,.4);display:flex;flex-direction:column;gap:6px;font-size:12px}',
    /* 高度/字体加 !important:浮窗页 #edit-popup textarea 的 id 选择器会盖住这里 */
    '.fa-pop textarea{width:100%;height:56px!important;resize:vertical;background:var(--panel2,#1c2130);color:var(--text,#dde3ee);border:1px solid var(--border,#2a3040);border-radius:6px;',
      'padding:5px 8px!important;font:inherit;font-size:12px!important;line-height:1.45!important;font-family:ui-monospace,Menlo,monospace!important}',
    '.fa-pop textarea:focus{outline:none;border-color:var(--accent,#6c9ef8)}',
    '.fa-pop .fa-hint{color:var(--dim,#8b93a7);white-space:pre-wrap}',
    '.fa-pop .fa-err{color:var(--red,#f87171);white-space:pre-wrap}',
    '.fa-pop .fa-err:empty{display:none}',
    '.fa-pop .fa-row{display:flex;gap:6px;justify-content:flex-end}',
    '.fa-pop button{background:var(--panel2,#1c2130);color:var(--text,#dde3ee);border:1px solid var(--border,#2a3040);border-radius:6px;padding:3px 10px;cursor:pointer;font:inherit;font-size:12px}',
    '.fa-pop button.fa-ok{background:var(--accent,#6c9ef8);border-color:var(--accent,#6c9ef8);color:#0b1020;font-weight:600}',
    '.fa-toast{position:absolute;right:6px;bottom:calc(100% + 6px);max-width:min(360px,90%);z-index:3;background:var(--panel,#161a23);color:var(--text,#dde3ee);',
      'border:1px solid var(--red,#f87171);border-radius:8px;padding:6px 10px;font-size:12px;line-height:1.45;box-shadow:0 6px 20px rgba(0,0,0,.35);white-space:pre-wrap}',
  ].join('\n');
  var PLUS='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14"/><path d="M5 12h14"/></svg>';

  function ensureCss(){
    if(cssDone)return;cssDone=true;
    var st=document.createElement('style');st.textContent=CSS;document.head.appendChild(st);
  }
  function isAbs(p){return /^\//.test(p)||/^[A-Za-z]:[\\/]/.test(p)||/^\\\\/.test(p)}
  function baseName(p){var s=String(p).replace(/[\\/]+$/,'');var i=Math.max(s.lastIndexOf('/'),s.lastIndexOf('\\'));return i>=0?s.slice(i+1):s}
  function desktop(){return window.videoagentsDesktop||null}

  // ① 桌面原生对话框 ② 服务端本机对话框;都不可用时 reject({manual:true, hint}),由调用方转手填
  function nativePick(title){
    var d=desktop();
    if(d&&d.remoteBackend)return Promise.reject({manual:true,hint:T('远程服务:请填写服务所在机器上的文件绝对路径(每行一个):')});
    if(d&&typeof d.pickFiles==='function'){
      return d.pickFiles({title:title}).then(function(r){return Array.isArray(r)?r:(r&&r.paths)||[]})
        .catch(function(e){return Promise.reject({manual:true,hint:T('无法打开系统文件对话框,请粘贴文件的绝对路径(每行一个):')+'\n'+(e&&e.message||'')})});
    }
    return fetch('/actions/pick-files',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:title})})
      .then(function(r){
        if(r.ok)return r.json().then(function(j){return (j&&j.paths)||[]});
        if(r.status===409)return r.json().catch(function(){return {}}).then(function(j){return Promise.reject({busy:true,message:j.detail||''})});
        return r.json().catch(function(){return {}}).then(function(j){
          return Promise.reject({manual:true,hint:T('无法打开系统文件对话框,请粘贴文件的绝对路径(每行一个):')+(j.detail?'\n'+j.detail:'')});
        });
      },function(){return Promise.reject({manual:true,hint:T('无法打开系统文件对话框,请粘贴文件的绝对路径(每行一个):')})});
  }

  function attach(ta,opt){
    opt=opt||{};ensureCss();
    // 语音按钮若已把 textarea 包进 .vi-wrap,连同外壳一起接管;若语音按钮之后再来包,它会包在 .fa-box 内,按钮定位不受影响
    var host=(ta.parentNode&&ta.parentNode.classList&&ta.parentNode.classList.contains('vi-wrap'))?ta.parentNode:ta;
    var wrap=document.createElement('div');wrap.className='fa-wrap';
    var chips=document.createElement('div');chips.className='fa-chips';chips.setAttribute('data-no-i18n','');
    var box=document.createElement('div');box.className='fa-box';
    host.parentNode.insertBefore(wrap,host);
    wrap.appendChild(chips);wrap.appendChild(box);box.appendChild(host);
    var btn=document.createElement('button');btn.type='button';btn.className='fa-btn';btn.innerHTML=PLUS;
    btn.title=T('添加文件:选择本机文件,不上传,只把绝对路径附在消息里发给 Agent(Shift+点击可手填路径)');
    btn.setAttribute('aria-label',T('添加文件'));
    box.appendChild(btn);
    ta.style.paddingRight='42px';   // 右侧留出按钮列(语音按钮开启时也用同一宽度)

    var it={ta:ta,wrap:wrap,box:box,chips:chips,btn:btn,paths:[],key:opt.storageKey||'',pop:null,toast:null};
    if(it.key){try{var saved=JSON.parse(localStorage.getItem(it.key)||'[]');if(Array.isArray(saved))it.paths=saved.filter(function(p){return typeof p==='string'&&isAbs(p)})}catch(_){}}
    render(it);
    btn.addEventListener('click',function(e){
      if(it.pop){closePop(it);return}
      if(e.shiftKey){openPop(it,T('粘贴文件的绝对路径(每行一个):'));return}
      pick(it);
    });
    return {
      paths:function(){return it.paths.slice()},
      setPaths:function(list){it.paths=(list||[]).filter(function(p){return typeof p==='string'&&isAbs(p)});render(it)},
      clear:function(){it.paths=[];closePop(it);render(it)},
      compose:function(text){return compose(it,text)},
    };
  }

  function persist(it){
    if(!it.key)return;
    try{it.paths.length?localStorage.setItem(it.key,JSON.stringify(it.paths)):localStorage.removeItem(it.key)}catch(_){}
  }
  function render(it){
    it.chips.innerHTML='';
    it.paths.forEach(function(p,i){
      var c=document.createElement('span');c.className='fa-chip';c.title=p;
      c.innerHTML='📎 <span class="fa-nm">'+esc(baseName(p))+'</span><button type="button" class="fa-x" aria-label="'+esc(T('移除'))+'" title="'+esc(T('移除'))+'">×</button>';
      c.querySelector('.fa-x').onclick=function(){it.paths.splice(i,1);persist(it);render(it);it.ta.focus({preventScroll:true})};
      it.chips.appendChild(c);
    });
    it.chips.classList.toggle('on',it.paths.length>0);
    persist(it);
  }
  function add(it,list){
    var added=0;
    (list||[]).forEach(function(p){
      p=String(p||'').trim().replace(/^["']|["']$/g,'');
      if(!p||!isAbs(p)||it.paths.indexOf(p)>=0)return;
      it.paths.push(p);added++;
    });
    render(it);return added;
  }
  function compose(it,text){
    text=String(text==null?'':text);
    if(!it.paths.length)return text;
    return text.replace(/\s+$/,'')+'\n\n'+T('附件(本机文件绝对路径,请直接读取):')+'\n'+it.paths.map(function(p){return '- '+p}).join('\n');
  }
  function showToast(it,msg){
    if(it.toast)it.toast.remove();
    var el=document.createElement('div');el.className='fa-toast';el.setAttribute('role','alert');el.textContent=msg;
    it.box.appendChild(el);it.toast=el;
    setTimeout(function(){if(it.toast===el){el.remove();it.toast=null}},4000);
  }
  function pick(it){
    it.btn.disabled=true;
    nativePick(T('选择文件')).then(function(paths){
      add(it,paths);it.ta.focus({preventScroll:true});
    }).catch(function(e){
      if(e&&e.manual){openPop(it,e.hint);return}
      showToast(it,(e&&e.message)||T('选择文件失败'));
    }).then(function(){it.btn.disabled=false});
  }
  // 手填框:粘贴绝对路径,每行一个;非绝对路径逐条报错不入列
  function openPop(it,hint){
    closePop(it);
    var pop=document.createElement('div');pop.className='fa-pop';pop.setAttribute('data-no-i18n','');
    pop.innerHTML='<div class="fa-hint">'+esc(hint||'')+'</div>'
      +'<textarea spellcheck="false" placeholder="'+esc(T('如 /Users/you/Desktop/a.png,每行一个'))+'"></textarea>'
      +'<div class="fa-err" role="alert"></div>'
      +'<div class="fa-row"><button type="button" class="fa-cancel">'+esc(T('取消'))+'</button><button type="button" class="fa-ok">'+esc(T('添加'))+'</button></div>';
    it.box.appendChild(pop);it.pop=pop;
    var ta=pop.querySelector('textarea'), err=pop.querySelector('.fa-err');
    var ok=function(){
      var lines=ta.value.split(/\r?\n/).map(function(s){return s.trim().replace(/^["']|["']$/g,'')}).filter(Boolean);
      var bad=lines.filter(function(p){return !isAbs(p)});
      if(bad.length){err.textContent=F(T('不是绝对路径:{p}'),{p:bad.join(', ')});return}
      add(it,lines);closePop(it);it.ta.focus({preventScroll:true});
    };
    pop.querySelector('.fa-ok').onclick=ok;
    pop.querySelector('.fa-cancel').onclick=function(){closePop(it);it.ta.focus({preventScroll:true})};
    ta.addEventListener('keydown',function(e){
      if(e.key==='Enter'&&(e.shiftKey||e.metaKey||e.ctrlKey)){e.preventDefault();ok()}
      else if(e.key==='Escape'){e.preventDefault();e.stopPropagation();closePop(it);it.ta.focus({preventScroll:true})}
    });
    ta.focus();
  }
  function closePop(it){if(it.pop){it.pop.remove();it.pop=null}}

  window.FileAttach={attach:attach};
})();
