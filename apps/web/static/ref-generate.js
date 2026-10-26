/* 「✨ 生成」弹窗(2026-10-26;参考文件页 preview_refs.html,后端 core.api_refgen_*)。
 * 写提示词(「🎨 风格库」点一种风格,把它的提示词加进提示词框)→ 按页面顶部「🎨 图像模型」(image_model_prefs.refs)出一张图
 * → 窗口里预览(点图放大)→「加入「板块」」复制进 refs/<板块>/,或「重新生成」(丢掉这张,按当前提示词再出一张)。
 * 用法:<script src="/static/ref-generate.js"></script>(放 i18n.js 之后)
 *      RefGenerate.open({project, target, catName, onAdded})
 *        target  = 打开它的板块 key,加入参考就进这个板块;catName = 板块显示名(中文原文,组件里再 t());
 *        onAdded(res) 加入成功后回调(页面据此刷新列表)。
 *      RefGenerate.engine({provider, model}) → 「渠道 · 模型」展示名(已翻译;卡片上「✨ AI 生成」那行也用它)。
 * 关掉窗口不中断出图;同一页再打开时接着显示这次的结果。
 * 风格库与控制台「画面风格」、画板是同一份(apps/web/static/styles/):改了 styles/ 下任何文件,STYLE_LIB_V 与 index.html 一起改。
 */
(function(){
  'use strict';
  var T=function(s){return (window.t||function(x){return x})(s)};
  var F=function(s,p){return window.I18N&&I18N.f?I18N.f(s,p):String(s).replace(/\{(\w+)\}/g,function(m,k){return k in p?p[k]:m})};
  var esc=function(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})};
  var STYLE_LIB_V='20261021';
  var ASPECTS=['16:9','9:16','1:1','4:3','3:4'];      // 同 core.REFGEN_ASPECTS
  var PROV_NAMES={agentics:'Agentics',openrouter:'OpenRouter',volcengine:'火山引擎',byteplus:'BytePlus',fal:'Fal',minimax:'MiniMax',rhapi:'RH',comfyui:'ComfyUI'};
  var COMFY_MODE_NAMES={local:'本地',cloud:'云端(Comfy Cloud)',rh_cn:'RunningHub 国内(.cn)',rh_ai:'RunningHub 国际(.ai)'};
  var PREF_KEY='refgen_prefs';
  var POLL_MS=1500;

  var st=document.createElement('style');
  st.textContent=[
    '.rg-mask{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:90;display:none;align-items:center;justify-content:center;padding:16px}',
    '.rg-mask.on{display:flex}',
    '.rg-box{position:relative;background:var(--panel,#161a23);border:1px solid var(--border,#2a3040);border-radius:12px;width:min(1080px,100%);height:min(720px,100%);display:flex;flex-direction:column;overflow:hidden;box-shadow:0 10px 40px rgba(0,0,0,.5)}',
    '.rg-head{display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid var(--border,#2a3040)}',
    '.rg-head h3{font-size:15px;white-space:nowrap}.rg-head .rg-hint{color:var(--dim,#8b93a7);font-size:12px;flex:1;min-width:0}',
    '.rg-main{flex:1;display:flex;min-height:0}',
    '.rg-form{width:400px;flex:none;display:flex;flex-direction:column;gap:8px;padding:12px 14px;border-inline-end:1px solid var(--border,#2a3040);overflow-y:auto}',
    '.rg-lab{display:flex;align-items:center;gap:8px;font-size:13px;font-weight:600}.rg-lab button{margin-inline-start:auto}',
    '.rg-prompt{width:100%;flex:1;min-height:180px;resize:none;font-size:13px;line-height:1.55}',
    '.rg-chip{display:inline-flex;align-items:center;gap:6px;align-self:flex-start;max-width:100%;border:1px solid var(--accent,#6c9ef8);border-radius:12px;padding:1px 4px 1px 10px;font-size:12px}',
    '.rg-chip[hidden]{display:none}.rg-chip span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.rg-chip button{font-size:11px;padding:0 6px;line-height:1.5;border-radius:10px}',
    '.rg-row{display:flex;align-items:center;gap:8px;font-size:13px}.rg-row .rg-go{margin-inline-start:auto;min-width:96px}',
    '.rg-row select{background:var(--panel2,#1c2130);color:var(--text,#dde3ee);border:1px solid var(--border,#2a3040);border-radius:6px;padding:5px 8px;font-size:13px}',
    '.rg-status{font-size:12px;color:var(--dim,#8b93a7);min-height:18px;word-break:break-word;white-space:pre-wrap}',
    '.rg-status.err{color:var(--red,#f87171)}',
    '.rg-pv{flex:1;display:flex;flex-direction:column;min-width:0;padding:12px 14px;gap:10px}',
    '.rg-img{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;background:var(--bg,#0f1117);border:1px dashed var(--border,#2a3040);border-radius:10px;overflow:hidden;color:var(--dim,#8b93a7);font-size:13px;text-align:center;padding:10px}',
    '.rg-img img{max-width:100%;max-height:100%;object-fit:contain;cursor:zoom-in;border-radius:4px;display:block}',
    '.rg-img.busy{animation:rg-pulse 1.6s ease-in-out infinite}',
    '@keyframes rg-pulse{50%{opacity:.55}}',
    '.rg-acts{display:flex;justify-content:flex-end;gap:8px}.rg-acts[hidden]{display:none}',
    '.rg-sty{position:absolute;inset:0;z-index:2;background:var(--panel,#161a23);display:none;flex-direction:column}',
    '.rg-sty.on{display:flex}',
    '.rg-sbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px 14px;border-bottom:1px solid var(--border,#2a3040)}',
    '.rg-sbar h3{font-size:15px;white-space:nowrap}.rg-sbar .rg-shint{color:var(--dim,#8b93a7);font-size:12px}',
    '.rg-cats{display:flex;gap:6px;flex-wrap:wrap}.rg-sq{margin-inline-start:auto;width:170px}',
    '.rg-cat{border:1px solid var(--border,#2a3040);border-radius:14px;padding:2px 12px;cursor:pointer;user-select:none;background:var(--panel2,#1c2130);font-size:12.5px}',
    '.rg-cat:hover{border-color:var(--accent,#6c9ef8)}',
    '.rg-cat.on{background:var(--accent,#6c9ef8);border-color:var(--accent,#6c9ef8);color:#0b1020;font-weight:600}',
    '.rg-sbody{flex:1;overflow-y:auto;padding:12px 14px}',      // 滚动放外层:网格自己限高时,3:2 卡片的行会被压扁叠在一起
    '.rg-sgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}',
    '.rg-sgrid .none{grid-column:1/-1;color:var(--dim,#8b93a7);text-align:center;padding:30px 0}',
    '.rg-card{position:relative;border:2px solid transparent;border-radius:8px;overflow:hidden;cursor:pointer;aspect-ratio:3/2;background:var(--panel2,#1c2130)}',
    '.rg-card:hover{border-color:var(--accent,#6c9ef8)}.rg-card.on{border-color:var(--green,#4ade80)}',
    '.rg-card img{width:100%;height:100%;object-fit:cover;display:block}',
    '.rg-card .nm{position:absolute;left:0;right:0;bottom:0;padding:14px 8px 5px;font-size:12px;color:#fff;background:linear-gradient(transparent,rgba(0,0,0,.78));white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.rg-card .cat{position:absolute;top:5px;right:5px;font-size:10px;background:rgba(0,0,0,.55);color:#fff;border-radius:4px;padding:0 5px}',
    '.rg-card .zoom{position:absolute;top:5px;left:5px;width:22px;height:22px;display:flex;align-items:center;justify-content:center;font-size:12px;background:rgba(0,0,0,.55);border-radius:4px;cursor:zoom-in;opacity:.75}',
    '.rg-card .zoom:hover{opacity:1}',
    '.rg-view{position:fixed;inset:0;z-index:95;background:rgba(0,0,0,.88);display:none;align-items:center;justify-content:center;cursor:zoom-out}',
    '.rg-view.on{display:flex}.rg-view img{max-width:96vw;max-height:96vh;border-radius:6px}',
    '@media (max-width:760px){.rg-main{flex-direction:column;overflow-y:auto}.rg-form{width:auto;border-inline-end:0;border-bottom:1px solid var(--border,#2a3040)}.rg-pv{min-height:320px}}'
  ].join('\n');
  document.head.appendChild(st);

  var S={opts:null,job:null,timer:0,style:null,lib:null,cat:'全部',busy:false};
  var root=null;
  function $(s){return root.querySelector(s)}
  function prefs(){try{return JSON.parse(localStorage.getItem(PREF_KEY)||'{}')||{}}catch(_){return {}}}
  function savePrefs(){try{localStorage.setItem(PREF_KEY,JSON.stringify({aspect:$('.rg-aspect').value}))}catch(_){}}
  function base(){return '/api/v1/projects/'+encodeURIComponent(S.opts.project)+'/references/generate'}
  function api(url,body){
    return fetch(url,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
      .then(function(r){return r.json().catch(function(){return {}}).then(function(d){if(!r.ok)throw new Error(d.detail||('HTTP '+r.status));return d})});
  }
  function status(msg,err){var el=$('.rg-status');el.textContent=msg||'';el.classList.toggle('err',!!err)}
  function catName(){return T(S.opts.catName||S.opts.target||'')}
  function engine(j){
    if(!j||!j.provider)return T('跟随全局');
    var m=j.provider==='comfyui'?T(COMFY_MODE_NAMES[j.model]||j.model||''):(j.model||'');
    return T(PROV_NAMES[j.provider]||j.provider)+(m?' · '+m:'');
  }
  function secs(j){return Math.max(0,Math.round(((j.finished||Date.now()/1000)-(j.started||Date.now()/1000))))}

  function build(){
    root=document.createElement('div');
    root.className='rg-mask';
    root.innerHTML='<div class="rg-box" role="dialog" aria-modal="true">'
      +'<div class="rg-head"><h3 class="rg-title"></h3><span class="rg-hint">'+esc(T('按页面顶部选的图像模型出图,满意再加入参考'))+'</span>'
      +'<button type="button" class="rg-x" title="'+esc(T('关闭'))+'">✕</button></div>'
      +'<div class="rg-main"><div class="rg-form">'
      +'<div class="rg-lab">'+esc(T('提示词'))+'<button type="button" class="rg-stybtn">'+esc(T('🎨 风格库'))+'</button></div>'
      +'<textarea class="rg-prompt" placeholder="'+esc(T('描述想要的画面,如:商代青铜鼎特写,正面平视,浅灰背景'))+'"></textarea>'
      +'<div class="rg-chip" hidden><span data-no-i18n></span><button type="button" class="rg-unsty" title="'+esc(T('去掉这个风格'))+'">✕</button></div>'
      +'<div class="rg-row">'+esc(T('画幅'))+' <select class="rg-aspect">'+ASPECTS.map(function(a){return '<option value="'+a+'">'+a+'</option>'}).join('')+'</select>'
      +'<button type="button" class="rg-go primary">'+esc(T('生成'))+'</button></div>'
      +'<div class="rg-status"></div></div>'
      +'<div class="rg-pv"><div class="rg-img">'+esc(T('生成的图会显示在这里'))+'</div>'
      +'<div class="rg-acts" hidden><button type="button" class="rg-again">'+esc(T('重新生成'))+'</button><button type="button" class="rg-add primary"></button></div></div></div>'
      +'<div class="rg-sty"><div class="rg-sbar"><h3>'+esc(T('🎨 风格库'))+'</h3><span class="rg-shint">'+esc(T('点一种风格,把它的提示词加进提示词框'))+'</span>'
      +'<span class="rg-cats"></span><input type="text" class="rg-sq" placeholder="'+esc(T('搜索风格…'))+'">'
      +'<button type="button" class="rg-sx" title="'+esc(T('关闭'))+'">✕</button></div><div class="rg-sbody"><div class="rg-sgrid"></div></div></div>'
      +'</div><div class="rg-view"><img alt=""></div>';
    document.body.appendChild(root);

    $('.rg-x').onclick=close;
    root.addEventListener('mousedown',function(e){if(e.target===root)close()});
    $('.rg-go').onclick=generate;
    $('.rg-again').onclick=generate;
    $('.rg-add').onclick=adopt;
    $('.rg-aspect').onchange=savePrefs;
    $('.rg-prompt').addEventListener('input',syncChip);
    $('.rg-prompt').addEventListener('keydown',function(e){if(e.key==='Enter'&&(e.metaKey||e.ctrlKey)){e.preventDefault();if(!S.busy)generate()}});
    $('.rg-unsty').onclick=function(){
      var el=$('.rg-prompt');
      if(S.style)el.value=el.value.replace(S.style.text,'').replace(/\n{3,}/g,'\n\n').trim();
      S.style=null;syncChip();el.focus();
    };
    $('.rg-stybtn').onclick=openStyles;
    $('.rg-sx').onclick=function(){$('.rg-sty').classList.remove('on')};
    $('.rg-sq').oninput=renderStyles;
    $('.rg-cats').onclick=function(e){var c=e.target.dataset.cat;if(c){S.cat=c;renderStyles()}};
    $('.rg-sgrid').onclick=function(e){
      if(e.target.dataset.szoom){zoom('/static/styles/'+e.target.dataset.szoom+'?v='+STYLE_LIB_V);return}
      var c=e.target.closest('[data-sty]');
      if(c)pickStyle(S.lib.filter(function(s){return s.id===c.dataset.sty})[0]);
    };
    $('.rg-img').addEventListener('click',function(e){if(e.target.tagName==='IMG')zoom(e.target.src)});
    $('.rg-view').onclick=function(){$('.rg-view').classList.remove('on')};
    document.addEventListener('keydown',function(e){
      if(e.key!=='Escape'||!root.classList.contains('on'))return;
      if($('.rg-view').classList.contains('on'))$('.rg-view').classList.remove('on');
      else if($('.rg-sty').classList.contains('on'))$('.rg-sty').classList.remove('on');
      else close();
    });
  }
  function zoom(src){$('.rg-view img').src=src;$('.rg-view').classList.add('on')}

  /* ---------- 风格库:点一种风格,把它的提示词加进提示词框;再点别的风格就替换上一段 ---------- */
  function loadStyles(){
    if(S.lib)return Promise.resolve(S.lib);
    var lang=window.I18N&&I18N.lang||'zh';
    return fetch('/static/styles/style_library.json?v='+STYLE_LIB_V).then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json()})
      .then(function(lib){
        var tr=lang==='zh'?Promise.resolve({}):fetch('/static/styles/i18n/'+lang+'.json?v='+STYLE_LIB_V)
          .then(function(r){return r.ok?r.json():{}}).catch(function(){return {}});
        return tr.then(function(m){
          lib.forEach(function(s){var x=m[s.id]||{};s.tname=x.name||s.name;s.tprompt=x.prompt||s.prompt});
          S.lib=lib;return lib;
        });
      });
  }
  function styleInText(){return S.style&&$('.rg-prompt').value.indexOf(S.style.text)>=0?S.style:null}
  function syncChip(){
    var s=styleInText(),c=$('.rg-chip');
    c.hidden=!s;
    if(s){c.querySelector('span').textContent='🎨 '+s.name;c.title=s.text}
  }
  function pickStyle(s){
    if(!s)return;
    var el=$('.rg-prompt'),cur=styleInText();
    if(cur)el.value=el.value.replace(cur.text,s.tprompt);
    else el.value=(el.value.trim()?el.value.replace(/\s+$/,'')+'\n':'')+s.tprompt;
    S.style={id:s.id,name:s.tname,text:s.tprompt};
    $('.rg-sty').classList.remove('on');
    syncChip();el.focus();
  }
  function renderStyles(){
    var kw=$('.rg-sq').value.trim().toLowerCase(),cur=styleInText();
    var hit=function(s){return [s.tname,s.tprompt,s.name,s.prompt].some(function(v){return String(v||'').toLowerCase().indexOf(kw)>=0})};
    var cats=['全部'];S.lib.forEach(function(s){if(cats.indexOf(s.category)<0)cats.push(s.category)});
    $('.rg-cats').innerHTML=cats.map(function(c){return '<span class="rg-cat'+(c===S.cat?' on':'')+'" data-cat="'+esc(c)+'">'+esc(T(c))+'</span>'}).join('');
    var list=S.lib.filter(function(s){return (S.cat==='全部'||s.category===S.cat)&&(!kw||hit(s))});
    $('.rg-sgrid').innerHTML=list.length?list.map(function(s){
      return '<div class="rg-card'+(cur&&cur.id===s.id?' on':'')+'" data-sty="'+esc(s.id)+'" title="'+esc(s.tprompt)+'">'
        +'<img loading="lazy" src="/static/styles/'+esc(s.image)+'?v='+STYLE_LIB_V+'" alt=""><span class="zoom" data-szoom="'+esc(s.image)+'" title="'+esc(T('放大查看'))+'">🔍</span>'
        +'<span class="cat">'+esc(T(s.category))+'</span><span class="nm" data-no-i18n>'+esc(s.tname)+'</span></div>'}).join('')
      :'<div class="none">'+esc(T('没有匹配的风格'))+'</div>';
  }
  function openStyles(){
    loadStyles().then(function(){$('.rg-sty').classList.add('on');renderStyles();$('.rg-sq').focus()})
      .catch(function(err){status(T('风格库加载失败:')+err.message,true)});
  }

  /* ---------- 生成 / 轮询 / 加入参考 ---------- */
  function render(){
    var j=S.job,img=$('.rg-img'),acts=$('.rg-acts'),running=!!j&&(j.status==='queued'||j.status==='running');
    S.busy=running;
    $('.rg-go').disabled=running;$('.rg-again').disabled=running;
    img.classList.toggle('busy',running);
    if(!j){img.textContent=T('生成的图会显示在这里');acts.hidden=true;status('');return}
    if(running){
      if(!img.querySelector('img'))img.textContent=F('生成中 · {engine} · {s} 秒',{engine:engine(j),s:secs(j)});
      status(F('生成中 · {engine} · {s} 秒',{engine:engine(j),s:secs(j)}));
      acts.hidden=true;return;
    }
    if(j.status==='failed'){
      img.textContent=T('生成的图会显示在这里');acts.hidden=true;
      status(T('生成失败:')+(j.error||''),true);return;
    }
    if(!img.querySelector('img')||img.querySelector('img').dataset.job!==j.id){
      img.innerHTML='<img alt="" title="'+esc(T('点图放大'))+'" data-job="'+esc(j.id)+'" src="'+esc(j.url||'')+'">';
    }
    acts.hidden=false;
    var added=(j.adopted||{})[S.opts.target];
    $('.rg-add').textContent=added?T('✓ 已加入'):F('加入「{cat}」',{cat:catName()});
    $('.rg-add').disabled=!!added;
    if(!S.msg)status(F('已生成 · {engine} · 用时 {s} 秒',{engine:engine(j),s:secs(j)}));
  }
  function poll(){
    clearTimeout(S.timer);
    var j=S.job;
    if(!j||!(j.status==='queued'||j.status==='running'))return;
    S.timer=setTimeout(function(){
      api(base()+'/'+encodeURIComponent(j.id)).then(function(d){
        if(!S.job||S.job.id!==d.id)return;
        S.job=d;if(root.classList.contains('on'))render();poll();
      }).catch(function(err){
        if(!S.job||S.job.id!==j.id)return;
        S.job={id:j.id,status:'failed',error:err.message};render();
      });
    },POLL_MS);
    if(root.classList.contains('on'))render();      // 每次轮询前刷新已用秒数
  }
  function generate(){
    var prompt=$('.rg-prompt').value.trim();
    if(!prompt){status(T('请先写提示词'),true);$('.rg-prompt').focus();return}
    var prev=S.job,s=styleInText();
    S.msg='';S.busy=true;$('.rg-go').disabled=true;$('.rg-again').disabled=true;
    api(base(),{prompt:prompt,category:S.opts.target,aspect:$('.rg-aspect').value,
                style:s?{id:s.id,name:s.name}:null,discard:prev&&prev.status!=='running'&&prev.status!=='queued'?prev.id:''})
      .then(function(d){S.job=d;$('.rg-img').innerHTML='';render();poll()})
      .catch(function(err){S.busy=false;$('.rg-go').disabled=false;$('.rg-again').disabled=false;status(T('生成失败:')+err.message,true)});
  }
  function adopt(){
    var j=S.job;
    if(!j||j.status!=='done')return;
    var b=$('.rg-add');b.disabled=true;
    api(base()+'/'+encodeURIComponent(j.id)+'/adopt',{category:S.opts.target}).then(function(d){
      (j.adopted=j.adopted||{})[S.opts.target]=d.path;
      S.msg=d.existed?F('「{cat}」里已有这张图:{path}',{cat:catName(),path:d.path}):F('已加入「{cat}」:{path}',{cat:catName(),path:d.path});
      status(S.msg);render();
      if(S.opts.onAdded)try{S.opts.onAdded(d)}catch(_){}
    }).catch(function(err){b.disabled=false;status(T('加入失败:')+err.message,true)});
  }

  function close(){root.classList.remove('on');$('.rg-sty').classList.remove('on');$('.rg-view').classList.remove('on')}
  function open(opts){
    if(!root)build();
    S.opts=opts||{};S.msg='';
    $('.rg-title').textContent='✨ '+T('生成图片')+' · '+(S.opts.ico?S.opts.ico+' ':'')+catName();
    var p=prefs();
    if(p.aspect&&ASPECTS.indexOf(p.aspect)>=0)$('.rg-aspect').value=p.aspect;
    root.classList.add('on');
    render();poll();
    setTimeout(function(){$('.rg-prompt').focus()},0);
  }

  window.RefGenerate={open:open,close:close,engine:engine};   // engine({provider, model}) → 已翻译的「渠道 · 模型」
})();
