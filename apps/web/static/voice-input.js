/* 语音输入按钮(控制台输入框 + 预览页「修改」浮窗共用)
 * 用法:<script src="/static/voice-input.js"></script>(放 i18n.js 之后;edit-popup.js 会按需自行加载)
 *      VoiceInput.attach(textarea, {insert: fn(text)})   把麦克风按钮挂到 textarea 右侧居中
 *      VoiceInput.setEnabled(bool)                        设置页开关切换后即时显示/隐藏所有按钮
 *      VoiceInput.refresh()                               重新读取 /api/v1/voice-input 的开关
 * 行为:只有「设置 → 高级 → 语音输入」开启时按钮才出现;点一下开始录音(按钮变红并有呼吸光环)、
 *      再点一下结束,录音 blob 原样 POST /api/v1/voice-input/transcribe(服务端 ffmpeg 转码 +
 *      本机 faster-whisper 转写,音频不出本机),结果插到光标处;60s 自动结束。
 *      失败在按钮上方弹一条小提示(权限被拒/模型未下载/识别失败)。
 */
(function(){
  'use strict';
  var T=function(s){return (window.t||function(x){return x})(s)};
  var F=function(s,p){return window.I18N&&I18N.f?I18N.f(s,p):String(s).replace(/\{(\w+)\}/g,function(m,k){return k in p?p[k]:m})};
  var MAX_MS=60000;
  var instances=[], enabled=null, cfgP=null, cssDone=false;

  var CSS=[
    '.vi-wrap{position:relative;flex:1;min-width:0;display:flex}',
    '.vi-wrap>textarea{flex:1;min-width:0;width:100%}',
    '.vi-wrap.vi-on>textarea{padding-right:42px}',
    '.vi-btn{display:none;position:absolute;right:10px;top:50%;transform:translateY(-50%);width:30px;height:30px;border-radius:50%;',
      'border:0;background:transparent;color:var(--dim,#8b93a7);cursor:pointer;padding:0;align-items:center;justify-content:center;z-index:2}',
    '.vi-wrap.vi-on .vi-btn{display:flex}',
    '.vi-btn:hover{color:var(--text,#dde3ee);background:rgba(128,128,128,.12)}',
    '.vi-btn svg{width:22px;height:22px;display:block}',
    '.vi-btn.rec{color:#f87171;animation:vi-pulse 1.4s ease-out infinite}',
    '.vi-btn.busy{color:var(--accent,#6c9ef8);cursor:progress}',
    '.vi-btn.busy svg{animation:vi-spin 1s linear infinite}',
    '.vi-btn:disabled{cursor:default;opacity:.6}',
    '@keyframes vi-pulse{0%{box-shadow:0 0 0 0 rgba(248,113,113,.55)}70%{box-shadow:0 0 0 9px rgba(248,113,113,0)}100%{box-shadow:0 0 0 0 rgba(248,113,113,0)}}',
    '@keyframes vi-spin{to{transform:rotate(360deg)}}',
    '.vi-toast{position:absolute;right:6px;bottom:calc(100% + 6px);max-width:min(320px,90%);background:var(--panel,#161a23);color:var(--text,#dde3ee);',
      'border:1px solid var(--border,#2a3040);border-radius:8px;padding:6px 10px;font-size:12px;line-height:1.45;box-shadow:0 6px 20px rgba(0,0,0,.35);z-index:3;white-space:pre-wrap}',
    '.vi-toast.err{border-color:var(--red,#f87171)}',
    '@media (prefers-reduced-motion:reduce){.vi-btn.rec{animation:none;outline:2px solid rgba(248,113,113,.6)}}',
  ].join('\n');
  var MIC='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    +'<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0"/><path d="M12 18v3"/><path d="M9 21h6"/></svg>';
  var SPIN='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 3a9 9 0 1 0 9 9"/></svg>';

  function ensureCss(){
    if(cssDone)return;cssDone=true;
    var st=document.createElement('style');st.textContent=CSS;document.head.appendChild(st);
  }
  function loadCfg(force){
    if(!cfgP||force)cfgP=fetch('/api/v1/voice-input').then(function(r){return r.ok?r.json():{enabled:false}})
      .then(function(c){enabled=!!c.enabled;return c}).catch(function(){enabled=false;return {enabled:false}});
    return cfgP;
  }
  function applyAll(){instances.forEach(function(it){it.wrap.classList.toggle('vi-on',!!enabled)})}

  function pickMime(){
    var c=['audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus','audio/mp4','audio/aac'];
    if(!window.MediaRecorder)return '';
    for(var i=0;i<c.length;i++)if(MediaRecorder.isTypeSupported(c[i]))return c[i];
    return '';
  }
  // 默认插入:光标处插入并触发 input 事件(控制台草稿保存依赖 input 事件)
  function defaultInsert(ta,text){
    var st=ta.selectionStart==null?ta.value.length:ta.selectionStart, en=ta.selectionEnd==null?st:ta.selectionEnd;
    var before=ta.value.slice(0,st), after=ta.value.slice(en);
    // 前文以中日韩文字/全角符号/冒号逗号括号结尾时不补空格,其余(拉丁文字)补一个空格再接识别结果
    var noSpace=/[\u3000-\u9fff\uf900-\ufaff\uff00-\uffef\s:,(\[「【]$/.test(before);
    var pre=before&&!noSpace?' ':'';
    ta.value=before+pre+text+after;
    var pos=(before+pre+text).length;
    ta.focus();ta.setSelectionRange(pos,pos);
    ta.dispatchEvent(new Event('input',{bubbles:true}));
  }

  function attach(ta,opt){
    opt=opt||{};
    if(!ta||ta.dataset.viAttached)return null;
    ensureCss();
    ta.dataset.viAttached='1';
    var wrap=document.createElement('div');wrap.className='vi-wrap';
    ta.parentNode.insertBefore(wrap,ta);wrap.appendChild(ta);
    var btn=document.createElement('button');btn.type='button';btn.className='vi-btn';btn.innerHTML=MIC;
    btn.setAttribute('data-no-i18n','');
    wrap.appendChild(btn);
    var it={wrap:wrap,btn:btn,ta:ta,insert:opt.insert||function(txt){defaultInsert(ta,txt)},
            rec:null,stream:null,chunks:[],timer:null,toastTimer:null,state:'idle'};
    instances.push(it);
    setTitle(it);
    btn.onclick=function(e){e.preventDefault();e.stopPropagation();toggle(it)};
    if(enabled!=null)applyAll();else loadCfg().then(applyAll);
    return it;
  }
  function setTitle(it){
    it.btn.title=it.state==='rec'?T('录音中,点击结束'):it.state==='busy'?T('识别中…'):T('点击开始录音');
    it.btn.setAttribute('aria-label',it.btn.title);
  }
  function toast(it,msg,isErr){
    clearTimeout(it.toastTimer);
    var old=it.wrap.querySelector('.vi-toast');if(old)old.remove();
    var el=document.createElement('div');el.className='vi-toast'+(isErr?' err':'');el.textContent=msg;el.setAttribute('data-no-i18n','');
    it.wrap.appendChild(el);
    it.toastTimer=setTimeout(function(){el.remove()},isErr?5000:2500);
  }
  function setState(it,s){
    it.state=s;
    it.btn.classList.toggle('rec',s==='rec');it.btn.classList.toggle('busy',s==='busy');
    it.btn.disabled=s==='busy';it.btn.innerHTML=s==='busy'?SPIN:MIC;
    setTitle(it);
  }
  function stopTracks(it){
    if(it.stream){it.stream.getTracks().forEach(function(t){try{t.stop()}catch(e){}});it.stream=null}
  }
  function toggle(it){
    if(it.state==='rec'){stop(it);return}
    if(it.state==='busy')return;
    start(it);
  }
  function permErr(e){
    var n=e&&e.name||'';
    if(n==='NotAllowedError'||n==='SecurityError')return T('麦克风权限被拒绝');
    if(n==='NotFoundError'||n==='OverconstrainedError')return T('没有找到麦克风设备');
    return F('无法打开麦克风:{err}',{err:(e&&e.message)||n||String(e)});
  }
  function start(it){
    if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia||!window.MediaRecorder){
      toast(it,window.isSecureContext===false?T('录音需要 HTTPS 或本机地址'):T('当前浏览器不支持录音'),true);return;
    }
    navigator.mediaDevices.getUserMedia({audio:true}).then(function(stream){
      it.stream=stream;it.chunks=[];
      var mime=pickMime();
      try{it.rec=mime?new MediaRecorder(stream,{mimeType:mime}):new MediaRecorder(stream)}
      catch(e){stopTracks(it);toast(it,permErr(e),true);return}
      it.rec.ondataavailable=function(ev){if(ev.data&&ev.data.size)it.chunks.push(ev.data)};
      it.rec.onerror=function(ev){toast(it,F('录音出错:{err}',{err:(ev.error&&ev.error.message)||''}),true);stop(it)};
      it.rec.onstop=function(){
        stopTracks(it);
        var type=it.rec.mimeType||mime||'audio/webm';
        var blob=new Blob(it.chunks,{type:type});it.chunks=[];
        if(blob.size<1000){setState(it,'idle');toast(it,T('没有识别到语音'),true);return}
        send(it,blob);
      };
      it.rec.start(250);
      setState(it,'rec');
      it.timer=setTimeout(function(){if(it.state==='rec')stop(it)},MAX_MS);
    }).catch(function(e){toast(it,permErr(e),true)});
  }
  function stop(it){
    clearTimeout(it.timer);it.timer=null;
    if(it.rec&&it.rec.state!=='inactive'){setState(it,'busy');try{it.rec.stop()}catch(e){stopTracks(it);setState(it,'idle')}}
    else{stopTracks(it);setState(it,'idle')}
  }
  function send(it,blob){
    setState(it,'busy');
    var lang=(window.I18N&&I18N.lang)||'';
    fetch('/api/v1/voice-input/transcribe'+(lang?'?lang='+encodeURIComponent(lang):''),
      {method:'POST',headers:{'Content-Type':blob.type||'application/octet-stream'},body:blob})
      .then(function(r){return r.json().catch(function(){return {}}).then(function(j){if(!r.ok)throw new Error(j.detail||('HTTP '+r.status));return j})})
      .then(function(j){
        var text=String(j.text||'').trim();
        if(!text){toast(it,T('没有识别到语音'),true);return}
        it.insert(text);
      })
      .catch(function(e){toast(it,F('语音识别失败:{err}',{err:e.message||String(e)}),true)})
      .then(function(){setState(it,'idle')});
  }

  window.VoiceInput={
    attach:attach,
    setEnabled:function(v){enabled=!!v;applyAll()},
    refresh:function(){return loadCfg(true).then(function(c){applyAll();return c})},
    isEnabled:function(){return !!enabled},
  };
})();
