/* 「🎞 素材库」截图弹窗(2026-10-26;参考文件页 preview_refs.html,后端 core.api_refs_footage_frame)。
 * 左边列素材库(/footage,data/footage)的项目,选一个后右边播它的原片(高分辨率 source,不是 480p 分析代理),
 * 播放 / 拖动 / 逐帧 / 点下方分镜缩略图跳转找到画面,点「📸 截图加入参考文件」把当前帧存进打开它的板块。
 * 截图在服务端从原片取,不受浏览器解码影响;浏览器播不了原片格式时改放 480p 代理,截出来的仍是原片分辨率。
 * 用法:<script src="/static/footage-frame.js"></script>(放 i18n.js 之后)
 *      FootageFrame.open({project, target, ico, catName, onAdded})
 *        target = 打开它的板块 key;catName = 板块显示名(中文原文,组件里再 t());onAdded(res) 加入成功后回调。
 */
(function(){
  'use strict';
  var T=function(s){return (window.t||function(x){return x})(s)};
  var F=function(s,p){return window.I18N&&I18N.f?I18N.f(s,p):String(s).replace(/\{(\w+)\}/g,function(m,k){return k in p?p[k]:m})};
  var esc=function(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})};
  var API='/api/v1/footage/projects';
  var PREF_KEY='footage_frame_prefs';

  var st=document.createElement('style');
  st.textContent=[
    '.ff-mask{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:90;display:none;align-items:center;justify-content:center;padding:16px}',
    '.ff-mask.on{display:flex}',
    '.ff-box{background:var(--panel,#161a23);border:1px solid var(--border,#2a3040);border-radius:12px;width:min(1280px,100%);height:min(860px,100%);display:flex;flex-direction:column;overflow:hidden;box-shadow:0 10px 40px rgba(0,0,0,.5)}',
    '.ff-head{display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid var(--border,#2a3040)}',
    '.ff-head h3{font-size:15px;white-space:nowrap}.ff-head .ff-hint{color:var(--dim,#8b93a7);font-size:12px;flex:1;min-width:0}',
    '.ff-main{flex:1;display:flex;min-height:0}',
    '.ff-side{width:320px;flex:none;display:flex;flex-direction:column;border-inline-end:1px solid var(--border,#2a3040);min-height:0}',
    '.ff-side .ff-q{margin:10px 12px 6px}',
    '.ff-list{flex:1;overflow-y:auto;padding:0 8px 10px}',
    '.ff-list .none{color:var(--dim,#8b93a7);font-size:12.5px;text-align:center;padding:24px 8px}',
    '.ff-row{display:flex;gap:9px;padding:6px;border-radius:8px;cursor:pointer;border:1px solid transparent}',
    '.ff-row:hover{background:var(--panel2,#1c2130)}.ff-row.on{border-color:var(--accent,#6c9ef8);background:var(--panel2,#1c2130)}',
    '.ff-row.off{opacity:.45;cursor:not-allowed}',
    '.ff-row .th{width:96px;height:54px;flex:none;border-radius:5px;background:#000 center/cover no-repeat;display:flex;align-items:center;justify-content:center;font-size:20px}',
    '.ff-row .tx{min-width:0;flex:1;display:flex;flex-direction:column;gap:2px}',
    '.ff-row .tt{font-size:12.5px;line-height:1.35;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;word-break:break-all}',
    '.ff-row .mt{font-size:11px;color:var(--dim,#8b93a7);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.ff-play{flex:1;min-width:0;display:flex;flex-direction:column;padding:10px 14px;gap:8px}',
    '.ff-ptitle{display:flex;align-items:center;gap:10px;font-size:13px;font-weight:600;min-height:20px}',
    '.ff-ptitle span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.ff-ptitle a{font-weight:400;font-size:12px;white-space:nowrap;margin-inline-start:auto}',
    '.ff-vwrap{flex:1;min-height:0;background:#000;border-radius:8px;display:flex;align-items:center;justify-content:center;overflow:hidden;color:var(--dim,#8b93a7);font-size:13px}',
    '.ff-vwrap video{max-width:100%;max-height:100%;display:block}',
    '.ff-ctl{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:12.5px}.ff-ctl[hidden],.ff-proxy[hidden]{display:none}',
    '.ff-ctl .ff-time{font-family:ui-monospace,Menlo,monospace;color:var(--text,#dde3ee)}.ff-ctl .ff-shot{margin-inline-start:auto}',
    '.ff-ctl .ff-proxy{color:var(--yellow,#fbbf24);font-size:12px}',
    '.ff-status{font-size:12px;color:var(--dim,#8b93a7);min-height:18px;word-break:break-word}.ff-status.err{color:var(--red,#f87171)}',
    '.ff-clips{display:flex;gap:6px;overflow-x:auto;padding-bottom:4px;flex:none}.ff-clips:empty{display:none}',
    '.ff-clip{flex:none;width:112px;cursor:pointer;border:2px solid transparent;border-radius:6px;overflow:hidden;background:#000;position:relative}',
    '.ff-clip:hover{border-color:var(--accent,#6c9ef8)}.ff-clip.on{border-color:var(--green,#4ade80)}',
    '.ff-clip img{width:100%;height:63px;object-fit:cover;display:block}',
    '.ff-clip span{position:absolute;left:3px;bottom:2px;font-size:10px;color:#fff;background:rgba(0,0,0,.6);border-radius:3px;padding:0 4px;font-family:ui-monospace,Menlo,monospace}',
    '@media (max-width:760px){.ff-main{flex-direction:column;overflow-y:auto}.ff-side{width:auto;max-height:40vh;border-inline-end:0;border-bottom:1px solid var(--border,#2a3040)}.ff-play{min-height:420px}}'
  ].join('\n');
  document.head.appendChild(st);

  var S={opts:null,list:[],cur:null,proj:null,fps:0,proxy:false,busy:false};
  var root=null;
  function $(s){return root.querySelector(s)}
  function api(url,body){
    return fetch(url,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
      .then(function(r){return r.json().catch(function(){return {}}).then(function(d){if(!r.ok)throw new Error(d.detail||('HTTP '+r.status));return d})});
  }
  function fileUrl(name,rel){return API+'/'+encodeURIComponent(name)+'/files/'+rel.split('/').map(encodeURIComponent).join('/')}
  function status(msg,err){var el=$('.ff-status');el.textContent=msg||'';el.classList.toggle('err',!!err)}
  function catName(){return T(S.opts.catName||S.opts.target||'')}
  function title(p){var s=p.source||{};return p.title||s.title||s.filename||p.name}
  function fmt(t){t=Math.max(0,t||0);var m=Math.floor(t/60),s=t-m*60;return (m<10?'0':'')+m+':'+(s<10?'0':'')+s.toFixed(3)}
  function fmtDur(t){t=Math.round(t||0);var m=Math.floor(t/60),s=t%60;return m+':'+(s<10?'0':'')+s}
  function prefs(){try{return JSON.parse(localStorage.getItem(PREF_KEY)||'{}')||{}}catch(_){return {}}}
  function savePrefs(){try{localStorage.setItem(PREF_KEY,JSON.stringify({name:S.cur||''}))}catch(_){}}
  function frameOf(t){return S.fps>0?Math.floor(t*S.fps+1e-3):-1}

  function build(){
    root=document.createElement('div');
    root.className='ff-mask';
    root.innerHTML='<div class="ff-box" role="dialog" aria-modal="true">'
      +'<div class="ff-head"><h3 class="ff-title"></h3><span class="ff-hint">'+esc(T('选一个素材项目,播放、拖动找到需要的画面,截图加入参考文件(按原片分辨率截取)'))+'</span>'
      +'<button type="button" class="ff-x" title="'+esc(T('关闭'))+'">✕</button></div>'
      +'<div class="ff-main"><div class="ff-side"><input type="text" class="ff-q" placeholder="'+esc(T('搜索素材项目…'))+'"><div class="ff-list"></div></div>'
      +'<div class="ff-play"><div class="ff-ptitle"></div><div class="ff-vwrap">'+esc(T('先在左侧选一个素材项目'))+'</div>'
      +'<div class="ff-ctl" hidden><button type="button" class="ff-prev" title="'+esc(T('上一帧'))+'">◀ '+esc(T('上一帧'))+'</button>'
      +'<button type="button" class="ff-next" title="'+esc(T('下一帧'))+'">'+esc(T('下一帧'))+' ▶</button><span class="ff-time" data-no-i18n></span>'
      +'<span class="ff-proxy" hidden>'+esc(T('浏览器播放不了原片格式,已改放 480p 代理;截图仍按原片截取'))+'</span>'
      +'<button type="button" class="ff-shot primary">'+esc(T('📸 截图加入参考文件'))+'</button></div>'
      +'<div class="ff-status"></div><div class="ff-clips" title="'+esc(T('点分镜跳到它的开头'))+'"></div></div></div></div>';
    document.body.appendChild(root);

    $('.ff-x').onclick=close;
    root.addEventListener('mousedown',function(e){if(e.target===root)close()});
    $('.ff-q').oninput=renderList;
    $('.ff-list').onclick=function(e){
      var r=e.target.closest('.ff-row');
      if(r&&!r.classList.contains('off'))select(r.dataset.name);
    };
    $('.ff-prev').onclick=function(){step(-1)};
    $('.ff-next').onclick=function(){step(1)};
    $('.ff-shot').onclick=shoot;
    $('.ff-clips').onclick=function(e){
      var c=e.target.closest('.ff-clip'),v=video();
      if(!c||!v)return;
      v.pause();v.currentTime=Number(c.dataset.t)+(S.fps>0?0.5/S.fps:0);
    };
    document.addEventListener('keydown',function(e){
      if(!root.classList.contains('on'))return;
      if(e.key==='Escape'){close();return}
      if(e.target.matches&&e.target.matches('input,textarea'))return;
      if(e.key===','||e.key==='.'){e.preventDefault();step(e.key===','?-1:1)}     // 逗号 / 句号逐帧(同常见剪辑软件)
    });
  }
  function video(){return root.querySelector('.ff-vwrap video')}

  function renderList(){
    var kw=$('.ff-q').value.trim().toLowerCase();
    var rows=S.list.filter(function(p){
      if(!kw)return true;
      var s=p.source||{};
      return [p.name,p.title,s.title,s.filename,(p.tags||[]).join(' ')].some(function(v){return String(v||'').toLowerCase().indexOf(kw)>=0});
    });
    if(!S.list.length){$('.ff-list').innerHTML='<div class="none">'+esc(T('还没有素材项目,先去「素材库」页导入视频'))+'</div>';return}
    if(!rows.length){$('.ff-list').innerHTML='<div class="none">'+esc(T('没有匹配的素材项目'))+'</div>';return}
    $('.ff-list').innerHTML=rows.map(function(p){
      var s=p.source||{},meta=[s.duration_s?fmtDur(s.duration_s):'',s.width&&s.height?s.width+'×'+s.height:''].filter(Boolean).join(' · ');
      var th=p.thumb?' style="background-image:url(&quot;'+esc(fileUrl(p.name,p.thumb))+'&quot;)"':'';
      return '<div class="ff-row'+(p.has_source?'':' off')+(p.name===S.cur?' on':'')+'" data-name="'+esc(p.name)+'" title="'+esc(p.has_source?title(p):T('没有原片'))+'">'
        +'<div class="th"'+th+'>'+(p.thumb?'':'🎞')+'</div><div class="tx"><div class="tt" data-no-i18n>'+esc(title(p))+'</div>'
        +'<div class="mt">'+(p.has_source?'<span data-no-i18n>'+esc(meta||p.name)+'</span>':esc(T('没有原片')))+'</div></div></div>';
    }).join('');
  }
  function syncTime(){
    var v=video();if(!v)return;
    var t=v.currentTime||0,f=frameOf(t);
    $('.ff-time').textContent=fmt(t)+' / '+fmt(v.duration||(S.proj&&S.proj.source||{}).duration_s||0)+(f>=0?' · '+F('第 {n} 帧',{n:f}):'');
    root.querySelectorAll('.ff-clip').forEach(function(c){c.classList.toggle('on',t>=Number(c.dataset.t)&&t<Number(c.dataset.e))});
  }
  function step(d){
    var v=video();if(!v||!S.fps)return;
    v.pause();
    var f=Math.max(0,frameOf(v.currentTime)+d);
    v.currentTime=Math.min((v.duration||1e9)-0.5/S.fps,(f+0.5)/S.fps);     // 落在帧中间,免得边界取整跳帧
  }
  function select(name){
    S.cur=name;S.proj=null;S.proxy=false;savePrefs();renderList();
    status('');$('.ff-ctl').hidden=true;$('.ff-clips').innerHTML='';
    var row=S.list.filter(function(p){return p.name===name})[0]||{name:name};
    $('.ff-ptitle').innerHTML='<span data-no-i18n>'+esc(title(row))+'</span><a href="/footage#p='+encodeURIComponent(name)+'" target="_blank" rel="noopener">'+esc(T('在素材库打开'))+' ↗</a>';
    $('.ff-vwrap').textContent=T('读取中…');
    api(API+'/'+encodeURIComponent(name)).then(function(p){
      if(S.cur!==name)return;
      var s=p.source||{};
      if(!s.file){$('.ff-vwrap').textContent=T('这个项目没有原片视频');return}
      S.proj=p;S.fps=Number(s.fps)||0;
      $('.ff-vwrap').innerHTML='<video controls preload="auto" playsinline src="'+esc(fileUrl(name,s.file))+'"></video>';
      var v=video();
      v.addEventListener('timeupdate',syncTime);v.addEventListener('seeked',syncTime);v.addEventListener('loadedmetadata',syncTime);
      v.addEventListener('error',function(){
        if(S.proxy||S.cur!==name)return;
        S.proxy=true;$('.ff-proxy').hidden=false;            // 原片格式浏览器不支持(如 HEVC / 某些 mkv):改放分析代理,时间轴相同
        v.src=fileUrl(name,'source/proxy.mp4');
      });
      $('.ff-proxy').hidden=true;$('.ff-ctl').hidden=false;
      $('.ff-prev').disabled=$('.ff-next').disabled=!S.fps;
      $('.ff-clips').innerHTML=(p.clips||[]).filter(function(c){return c.thumbnail}).map(function(c){
        return '<div class="ff-clip" data-t="'+(Number(c.start_s)||0)+'" data-e="'+(Number(c.end_s)||0)+'"><img loading="lazy" src="'+esc(fileUrl(name,c.thumbnail))+'" alt=""><span>'+esc(fmt(c.start_s).slice(0,5))+'</span></div>';
      }).join('');
      syncTime();
    }).catch(function(err){if(S.cur===name)$('.ff-vwrap').textContent=T('读取失败:')+err.message});
  }
  function shoot(){
    var v=video();
    if(!v||!S.proj||S.busy)return;
    v.pause();
    var b=$('.ff-shot');S.busy=true;b.disabled=true;
    status(T('截图中…'));
    api('/api/v1/projects/'+encodeURIComponent(S.opts.project)+'/references/footage-frame',{footage:S.cur,t:v.currentTime||0,category:S.opts.target})
      .then(function(d){
        status(d.existed?F('「{cat}」里已有这张图:{path}',{cat:catName(),path:d.path}):F('已加入「{cat}」:{path}',{cat:catName(),path:d.path}));
        if(S.opts.onAdded)try{S.opts.onAdded(d)}catch(_){}
      })
      .catch(function(err){status(T('加入失败:')+err.message,true)})
      .then(function(){S.busy=false;b.disabled=false});
  }

  function close(){
    var v=root&&video();if(v)v.pause();
    root.classList.remove('on');
  }
  function open(opts){
    if(!root)build();
    S.opts=opts||{};
    $('.ff-title').textContent='🎞 '+T('素材库')+' · '+(S.opts.ico?S.opts.ico+' ':'')+catName();
    root.classList.add('on');
    status('');
    $('.ff-list').innerHTML='<div class="none">'+esc(T('读取中…'))+'</div>';
    api(API).then(function(d){
      S.list=d.projects||[];renderList();
      if(!S.cur){                                            // 首次打开:接着上次选的项目(还在且有原片时)
        var last=prefs().name,hit=S.list.filter(function(p){return p.name===last&&p.has_source})[0];
        if(hit)select(hit.name);
      }
    }).catch(function(err){$('.ff-list').innerHTML='<div class="none">'+esc(T('读取失败:')+err.message)+'</div>'});
  }

  window.FootageFrame={open:open,close:close};
})();
