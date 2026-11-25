/* 「生成 3D 模型」弹窗(2026-11-25;参考文件页「🧊 3D 模型」板块的「✨ 生成」,后端 core.api_refgen3d_*、modules/model3d.py)。
 * 放参考图(正面必填;左 / 后 / 右选填 = 多视图,所选模型不收多视图时这三格置灰)→ 选 3D 模型(按渠道分组,没配 Key 的置灰;
 * 默认 = 「生成模型」页 3D 模型段的生效渠道,上次选的记在 localStorage)→ 生成 → 窗口里旋转预览 →「加入「3D 模型」」或「重新生成」。
 * 参考图来源:本地上传(点格子 / 拖进格子)或「🖼」从参考文件页已有的图片里挑。
 * 用法:<script src="/static/ref-gen3d.js"></script>(放 i18n.js 之后;预览用 glb-viewer.js,页面须同时加载它与 importmap)
 *      RefGen3D.open({project, images: [{path, url, name, cat}], catName, onAdded})
 *        images = 参考文件页里可选的图片(path 是 refs/ 下的相对路径),catName = 板块显示名(中文原文)
 *      RefGen3D.engine({provider, model}) → 「渠道 · 模型」展示名(已翻译;卡片上「🧊 AI 生成」那行也用它)。
 * 关掉窗口不中断生成;同一页再打开时接着显示这次的结果。
 */
(function(){
  'use strict';
  var T=function(s){return (window.t||function(x){return x})(s)};
  var F=function(s,p){return window.I18N&&I18N.f?I18N.f(s,p):String(s).replace(/\{(\w+)\}/g,function(m,k){return k in p?p[k]:m})};
  var esc=function(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})};
  var VIEWS=['front','left','back','right'];                                   // 同 modules/model3d.VIEWS
  var VIEW_NAMES={front:'正面',left:'左侧',back:'背面',right:'右侧'};
  var PROV_NAMES={tripo:'Tripo',fal:'Fal',rhapi:'RH',hunyuan3d:'本地 Hunyuan3D'};
  var PREF_KEY='refgen3d_prefs';
  var POLL_MS=2000;

  var st=document.createElement('style');
  st.textContent=[
    '.g3-mask{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:90;display:none;align-items:center;justify-content:center;padding:16px}',
    '.g3-mask.on{display:flex}',
    '.g3-box{position:relative;background:var(--panel,#161a23);border:1px solid var(--border,#2a3040);border-radius:12px;width:min(1120px,100%);height:min(740px,100%);display:flex;flex-direction:column;overflow:hidden;box-shadow:0 10px 40px rgba(0,0,0,.5)}',
    '.g3-head{display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid var(--border,#2a3040)}',
    '.g3-head h3{font-size:15px;white-space:nowrap}.g3-head .g3-hint{color:var(--dim,#8b93a7);font-size:12px;flex:1;min-width:0}',
    '.g3-main{flex:1;display:flex;min-height:0}',
    '.g3-form{width:410px;flex:none;display:flex;flex-direction:column;gap:9px;padding:12px 14px;border-inline-end:1px solid var(--border,#2a3040);overflow-y:auto}',
    '.g3-lab{font-size:13px;font-weight:600;display:flex;align-items:center;gap:8px}',
    '.g3-note{font-size:11.5px;color:var(--dim,#8b93a7);line-height:1.5}',
    '.g3-views{display:grid;grid-template-columns:1fr 1fr;gap:8px}',
    '.g3-slot{position:relative;border:1px dashed var(--border,#2a3040);border-radius:8px;background:var(--bg,#0f1117);aspect-ratio:1/1;display:flex;align-items:center;justify-content:center;cursor:pointer;overflow:hidden;color:var(--dim,#8b93a7);font-size:12px;text-align:center}',
    '.g3-slot:hover{border-color:var(--accent,#6c9ef8)}.g3-slot.drag{border-color:var(--accent,#6c9ef8);box-shadow:0 0 0 2px var(--accent,#6c9ef8) inset}',
    '.g3-slot.req .g3-vn::after{content:" *";color:var(--red,#f87171)}',
    '.g3-slot.off{opacity:.35;cursor:not-allowed}',
    '.g3-slot img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;background:#000}',
    '.g3-slot .g3-vn{position:absolute;left:6px;top:5px;font-size:11.5px;font-weight:600;color:#fff;background:rgba(0,0,0,.55);border-radius:4px;padding:0 6px;z-index:1}',
    '.g3-slot .g3-empty{padding:26px 8px 8px;line-height:1.5}',
    '.g3-slot .g3-sb{position:absolute;right:5px;bottom:5px;display:flex;gap:4px;z-index:1}',
    '.g3-slot .g3-sb button{font-size:11px;padding:1px 6px;background:rgba(22,26,35,.85);border-radius:5px}',
    '.g3-slot .g3-busy{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.5);color:#fff;z-index:2}',
    '.g3-row{display:flex;align-items:center;gap:8px;font-size:13px}',
    '.g3-row select{flex:1;min-width:0;background:var(--panel2,#1c2130);color:var(--text,#dde3ee);border:1px solid var(--border,#2a3040);border-radius:6px;padding:5px 8px;font-size:13px}',
    '.g3-row .g3-go{margin-inline-start:auto;min-width:96px}',
    '.g3-status{font-size:12px;color:var(--dim,#8b93a7);min-height:18px;word-break:break-word;white-space:pre-wrap}',
    '.g3-status.err{color:var(--red,#f87171)}',
    '.g3-pv{flex:1;display:flex;flex-direction:column;min-width:0;padding:12px 14px;gap:10px}',
    '.g3-view{flex:1;min-height:0;display:flex;align-items:center;justify-content:center;background:var(--bg,#0f1117);border:1px dashed var(--border,#2a3040);border-radius:10px;overflow:hidden;color:var(--dim,#8b93a7);font-size:13px;text-align:center;padding:10px}',
    '.g3-view.has{padding:0;border-style:solid}',
    '.g3-view.busy{animation:g3-pulse 1.6s ease-in-out infinite}',
    '@keyframes g3-pulse{50%{opacity:.55}}',
    '.g3-acts{display:flex;justify-content:flex-end;gap:8px;align-items:center}.g3-acts[hidden]{display:none}',
    '.g3-acts .g3-meta{margin-inline-end:auto;font-size:12px;color:var(--dim,#8b93a7)}',
    '.g3-pick{position:absolute;inset:0;z-index:3;background:var(--panel,#161a23);display:none;flex-direction:column}',
    '.g3-pick.on{display:flex}',
    '.g3-pbar{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid var(--border,#2a3040)}',
    '.g3-pbar h3{font-size:15px}.g3-pbar .g3-phint{color:var(--dim,#8b93a7);font-size:12px;flex:1}',
    '.g3-pbody{flex:1;overflow-y:auto;padding:12px 14px}',
    '.g3-pgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px}',
    '.g3-pgrid .none{grid-column:1/-1;color:var(--dim,#8b93a7);text-align:center;padding:30px 0}',
    '.g3-pc{position:relative;border:2px solid transparent;border-radius:8px;overflow:hidden;cursor:pointer;aspect-ratio:1/1;background:var(--panel2,#1c2130)}',
    '.g3-pc:hover{border-color:var(--accent,#6c9ef8)}',
    '.g3-pc img{width:100%;height:100%;object-fit:cover;display:block}',
    '.g3-pc .nm{position:absolute;left:0;right:0;bottom:0;padding:12px 6px 4px;font-size:11px;color:#fff;background:linear-gradient(transparent,rgba(0,0,0,.8));white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.g3-pc .cat{position:absolute;top:4px;right:4px;font-size:10px;background:rgba(0,0,0,.6);color:#fff;border-radius:4px;padding:0 5px}',
    '@media (max-width:760px){.g3-main{flex-direction:column;overflow-y:auto}.g3-form{width:auto;border-inline-end:0;border-bottom:1px solid var(--border,#2a3040)}.g3-pv{min-height:360px}}'
  ].join('\n');
  document.head.appendChild(st);

  var S={opts:null,opt:null,slots:{},job:null,timer:0,view:null,viewJob:'',pickFor:'',msg:''};
  var root=null;
  function $(s){return root.querySelector(s)}
  function prefs(){try{return JSON.parse(localStorage.getItem(PREF_KEY)||'{}')||{}}catch(_){return {}}}
  function savePrefs(){
    var m=curModel();
    try{localStorage.setItem(PREF_KEY,JSON.stringify({provider:m&&m.provider,model:m&&m.id,texture:$('.g3-tex').checked}))}catch(_){}
  }
  function base(){return '/api/v1/projects/'+encodeURIComponent(S.opts.project)+'/references/generate3d'}
  function api(url,body,raw){
    var init=body===undefined?{cache:'no-store'}:raw?{method:'POST',body:body}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
    return fetch(url,init).then(function(r){return r.json().catch(function(){return {}}).then(function(d){if(!r.ok)throw new Error(d.detail||('HTTP '+r.status));return d})});
  }
  function status(msg,err){var el=$('.g3-status');el.textContent=msg||'';el.classList.toggle('err',!!err)}
  function catName(){return T(S.opts.catName||'3D 模型')}
  function provName(p){return T(PROV_NAMES[p]||p||'')}
  function shortModel(provider,model){
    if(!model)return '';
    if(provider==='rhapi'||provider==='fal')return model;
    var m=S.opt&&S.opt.providers.filter(function(x){return x.provider===provider})[0];
    var hit=m&&m.models.filter(function(x){return x.id===model})[0];
    return hit?hit.label:model;
  }
  function engine(j){
    if(!j||!j.provider)return '';
    var m=j.model_label||shortModel(j.provider,j.model);
    return provName(j.provider)+(m?' · '+m:'');
  }
  function secs(j){return Math.max(0,Math.round(((j.finished||Date.now()/1000)-(j.started||Date.now()/1000))))}
  function viewNames(list){
    var zh=!(window.I18N&&I18N.lang&&I18N.lang!=='zh');
    return (list||[]).map(function(v){return T(VIEW_NAMES[v]||v)}).join(zh?'、':', ');
  }

  function build(){
    root=document.createElement('div');
    root.className='g3-mask';
    root.innerHTML='<div class="g3-box" role="dialog" aria-modal="true">'
      +'<div class="g3-head"><h3 class="g3-title"></h3><span class="g3-hint">'+esc(T('放参考图、选 3D 模型,生成后可旋转查看,满意再加入'))+'</span>'
      +'<button type="button" class="g3-x" title="'+esc(T('关闭'))+'">✕</button></div>'
      +'<div class="g3-main"><div class="g3-form">'
      +'<div class="g3-lab">'+esc(T('参考图'))+'</div>'
      +'<div class="g3-views">'+VIEWS.map(function(v){
          return '<div class="g3-slot'+(v==='front'?' req':'')+'" data-view="'+v+'"><span class="g3-vn">'+esc(T(VIEW_NAMES[v]))+'</span>'
            +'<div class="g3-empty">'+esc(T(v==='front'?'点击上传或拖入图片':'选填'))+'</div>'
            +'<div class="g3-sb"><button type="button" class="g3-fromref" title="'+esc(T('从参考文件里选一张图'))+'">🖼</button>'
            +'<button type="button" class="g3-clr" title="'+esc(T('清除'))+'" hidden>✕</button></div></div>'}).join('')+'</div>'
      +'<div class="g3-note g3-mvnote"></div>'
      +'<div class="g3-lab">'+esc(T('3D 模型'))+'</div>'
      +'<div class="g3-row"><select class="g3-model"></select></div>'
      +'<div class="g3-note g3-mhint"></div>'
      +'<label class="g3-row"><input type="checkbox" class="g3-tex"> <span>'+esc(T('生成纹理(不勾只出白模)'))+'</span></label>'
      +'<div class="g3-row"><button type="button" class="g3-go primary">'+esc(T('生成'))+'</button></div>'
      +'<div class="g3-status"></div></div>'
      +'<div class="g3-pv"><div class="g3-view">'+esc(T('生成的 3D 模型会显示在这里'))+'</div>'
      +'<div class="g3-acts" hidden><span class="g3-meta"></span><button type="button" class="g3-again">'+esc(T('重新生成'))+'</button><button type="button" class="g3-add primary"></button></div></div></div>'
      +'<div class="g3-pick"><div class="g3-pbar"><h3 class="g3-ptitle"></h3><span class="g3-phint">'+esc(T('点一张图放进这个视角'))+'</span>'
      +'<button type="button" class="g3-px" title="'+esc(T('关闭'))+'">✕</button></div><div class="g3-pbody"><div class="g3-pgrid"></div></div></div>'
      +'</div><input type="file" class="g3-file" accept="image/png,image/jpeg,image/webp" style="display:none">';
    document.body.appendChild(root);

    $('.g3-x').onclick=close;
    root.addEventListener('mousedown',function(e){if(e.target===root)close()});
    $('.g3-go').onclick=generate;
    $('.g3-again').onclick=generate;
    $('.g3-add').onclick=adopt;
    $('.g3-model').onchange=function(){syncModel();savePrefs()};
    $('.g3-tex').onchange=savePrefs;
    $('.g3-px').onclick=function(){$('.g3-pick').classList.remove('on')};
    $('.g3-views').addEventListener('click',function(e){
      var slot=e.target.closest('.g3-slot');if(!slot||slot.classList.contains('off'))return;
      var v=slot.dataset.view;
      if(e.target.closest('.g3-clr')){delete S.slots[v];renderSlots();return}
      if(e.target.closest('.g3-fromref')){openPick(v);return}
      S.pickFor=v;$('.g3-file').click();
    });
    $('.g3-file').onchange=function(){
      var f=$('.g3-file').files[0];$('.g3-file').value='';
      if(f&&S.pickFor)uploadTo(S.pickFor,f);
    };
    $('.g3-views').addEventListener('dragover',function(e){
      var slot=e.target.closest('.g3-slot');
      root.querySelectorAll('.g3-slot.drag').forEach(function(x){if(x!==slot)x.classList.remove('drag')});
      if(slot&&!slot.classList.contains('off')){e.preventDefault();slot.classList.add('drag')}
    });
    $('.g3-views').addEventListener('dragleave',function(e){var slot=e.target.closest('.g3-slot');if(slot&&!slot.contains(e.relatedTarget))slot.classList.remove('drag')});
    $('.g3-views').addEventListener('drop',function(e){
      var slot=e.target.closest('.g3-slot');if(!slot)return;
      e.preventDefault();slot.classList.remove('drag');
      if(slot.classList.contains('off'))return;
      var f=[].slice.call(e.dataTransfer.files||[]).filter(function(x){return /^image\//.test(x.type)})[0];
      if(f)uploadTo(slot.dataset.view,f);
    });
    $('.g3-pgrid').onclick=function(e){
      var c=e.target.closest('[data-pi]');if(!c)return;
      var img=(S.opts.images||[])[+c.dataset.pi];if(!img)return;
      S.slots[S.pickFor]={path:'refs/'+img.path,url:img.url,name:img.name,ref:img.path};
      $('.g3-pick').classList.remove('on');renderSlots();
    };
    document.addEventListener('keydown',function(e){
      if(e.key!=='Escape'||!root.classList.contains('on')||document.querySelector('.gv-modal'))return;
      if($('.g3-pick').classList.contains('on'))$('.g3-pick').classList.remove('on');
      else close();
    });
  }

  /* ---------- 参考图格子 ---------- */
  function uploadTo(v,file){
    var slot=root.querySelector('.g3-slot[data-view="'+v+'"]');
    slot.insertAdjacentHTML('beforeend','<div class="g3-busy">'+esc(T('上传中…'))+'</div>');
    api(base()+'/upload?filename='+encodeURIComponent(file.name),file,true).then(function(d){
      S.slots[v]={path:d.path,url:d.url,name:file.name};renderSlots();
    }).catch(function(err){renderSlots();status(T('上传失败:')+err.message,true)});
  }
  function curModel(){
    var o=$('.g3-model').selectedOptions[0];if(!o||!o.value)return null;
    var pv=o.value.split('\u0001');
    var p=S.opt.providers.filter(function(x){return x.provider===pv[0]})[0];
    var m=p&&p.models.filter(function(x){return x.id===pv[1]})[0];
    return m?{provider:pv[0],id:m.id,label:m.label,multiview:m.multiview,prov:p}:null;
  }
  function renderSlots(){
    var m=curModel(),single=m&&m.multiview===false;
    VIEWS.forEach(function(v){
      var slot=root.querySelector('.g3-slot[data-view="'+v+'"]'),s=S.slots[v];
      slot.classList.toggle('off',single&&v!=='front');
      slot.title=single&&v!=='front'?T('所选模型只收一张正面图'):'';
      var img=slot.querySelector('img'),bz=slot.querySelector('.g3-busy');
      if(bz)bz.remove();
      if(s){
        if(!img){img=document.createElement('img');img.alt='';slot.appendChild(img)}
        if(img.getAttribute('src')!==s.url)img.src=s.url;
        img.title=s.ref?('refs/'+s.ref):(s.name||'');
        slot.querySelector('.g3-empty').hidden=true;
      }else{
        if(img)img.remove();
        slot.querySelector('.g3-empty').hidden=false;
      }
      slot.querySelector('.g3-clr').hidden=!s;
    });
    var extra=VIEWS.filter(function(v){return v!=='front'&&S.slots[v]});
    $('.g3-mvnote').textContent=!m?'':single
      ?(extra.length?F('所选模型只收一张正面图,{views}不会用上',{views:viewNames(extra)}):T('所选模型只收一张正面图'))
      :m.multiview===null?T('多视图:左 / 后 / 右是同一物体其它角度的图,能让背面和侧面更准;自定义模型能否用上按端点而定')
      :T('多视图:左 / 后 / 右是同一物体其它角度的图,能让背面和侧面更准;只放正面也能生成');
  }

  /* 从参考文件里挑图 */
  function openPick(v){
    S.pickFor=v;
    $('.g3-ptitle').textContent='🖼 '+F('从参考文件选 · {view}',{view:T(VIEW_NAMES[v])});
    var list=S.opts.images||[];
    $('.g3-pgrid').innerHTML=list.length?list.map(function(img,i){
      return '<div class="g3-pc" data-pi="'+i+'" title="refs/'+esc(img.path)+'"><img loading="lazy" src="'+esc(img.url)+'" alt="">'
        +(img.cat?'<span class="cat">'+esc(T(img.cat))+'</span>':'')+'<span class="nm" data-no-i18n>'+esc(img.name)+'</span></div>'}).join('')
      :'<div class="none">'+esc(T('参考文件里还没有图片'))+'</div>';
    $('.g3-pick').classList.add('on');
  }

  /* ---------- 模型下拉 ---------- */
  function fillModels(){
    var sel=$('.g3-model'),p=prefs(),want=null,firstOk=null,dflt=null;
    sel.innerHTML='';
    S.opt.providers.forEach(function(pv){
      var g=document.createElement('optgroup');
      g.label=provName(pv.provider)+(pv.configured?'':' · '+T('未配置'));
      pv.models.forEach(function(m){
        var o=document.createElement('option');
        o.value=pv.provider+'\u0001'+m.id;
        o.textContent=m.label+(m.multiview?' · '+T('多视图'):'');
        o.disabled=!pv.configured;
        if(!pv.configured&&pv.hint)o.title=pv.hint;
        o.setAttribute('data-no-i18n','');
        g.appendChild(o);
        if(pv.configured){
          if(!firstOk)firstOk=o.value;
          if(p.provider===pv.provider&&p.model===m.id)want=o.value;
          if(S.opt.default.provider===pv.provider&&S.opt.default.model===m.id)dflt=o.value;
        }
      });
      if(!pv.models.length){
        var o=document.createElement('option');o.disabled=true;o.value='';
        o.textContent=pv.hint||T('未配置');g.appendChild(o);
      }
      sel.appendChild(g);
    });
    if(!dflt){   // 默认渠道的所选模型不在清单里(如 RH 没选)时取该渠道第一个
      var dp=S.opt.providers.filter(function(x){return x.provider===S.opt.default.provider&&x.configured&&x.models.length})[0];
      if(dp)dflt=dp.provider+'\u0001'+dp.models[0].id;
    }
    sel.value=want||dflt||firstOk||'';
    var m=curModel();
    $('.g3-tex').checked=(p.provider&&want&&typeof p.texture==='boolean')?p.texture
      :!(m&&m.provider==='hunyuan3d')||!!(m&&m.prov.texture);
    syncModel();
  }
  function syncModel(){
    var m=curModel(),h='';
    if(!m)h=T('没有可用的 3D 模型:先到「🎨 生成模型」页 → 🧊 3D 模型 填 Key 或服务地址');
    else if(m.provider==='hunyuan3d')h=F('本地服务 {url};只收一张正面图;勾「生成纹理」时服务端须带 --enable_tex 启动',{url:m.prov.base_url||''});
    else if(m.provider==='rhapi')h=T('RH 按视角数自动换同系列的多视图端点;按 RunningHub 账户计费');
    else h=F('按 {prov} 账户计费,生成通常要 1–5 分钟',{prov:provName(m.provider)});
    $('.g3-mhint').textContent=h;
    $('.g3-go').disabled=!m||busy();
    renderSlots();
  }
  function loadOptions(){
    $('.g3-model').innerHTML='<option>'+esc(T('加载中…'))+'</option>';
    return api(base()+'/options').then(function(d){S.opt=d;fillModels()})
      .catch(function(err){status(T('3D 模型清单加载失败:')+err.message,true)});
  }

  /* ---------- 生成 / 轮询 / 加入 ---------- */
  function busy(){var j=S.job;return !!j&&(j.status==='queued'||j.status==='running')}
  function showModel(j){
    var box=$('.g3-view');
    if(S.view&&S.viewJob===j.id)return;
    if(S.view){S.view.dispose();S.view=null}
    box.classList.add('has');box.textContent='';
    S.viewJob=j.id;
    var go=function(){S.view=window.GlbViewer.mount(box,j.url,{autoRotate:true})};
    if(window.GlbViewer)go();else window.addEventListener('glbviewer-ready',go,{once:true});
  }
  function clearView(text){
    if(S.view){S.view.dispose();S.view=null}
    S.viewJob='';
    var box=$('.g3-view');box.classList.remove('has');box.textContent=text;
  }
  function render(){
    var j=S.job,box=$('.g3-view'),acts=$('.g3-acts'),running=busy();
    $('.g3-go').disabled=running||!curModel();$('.g3-again').disabled=running;
    box.classList.toggle('busy',running);
    if(!j){clearView(T('生成的 3D 模型会显示在这里'));acts.hidden=true;status(S.msg||'');return}
    if(running){
      var line=F('生成中 · {engine} · {s} 秒',{engine:engine(j),s:secs(j)});
      clearView(line+'\n'+T('3D 生成通常要 1–5 分钟,关掉窗口不影响'));
      status(line);acts.hidden=true;return;
    }
    if(j.status==='failed'){
      clearView(T('生成的 3D 模型会显示在这里'));acts.hidden=true;
      status(T('生成失败:')+(j.error||''),true);return;
    }
    if(j.url)showModel(j);
    acts.hidden=false;
    $('.g3-meta').textContent=engine(j)+(j.bytes?' · '+(j.bytes/1048576).toFixed(1)+' MB':'');
    var added=(j.adopted||{}).models3d;
    $('.g3-add').textContent=added?T('✓ 已加入'):F('加入「{cat}」',{cat:catName()});
    $('.g3-add').disabled=!!added;
    if(!S.msg){
      var line2=F('已生成 · {engine} · 用时 {s} 秒',{engine:engine(j),s:secs(j)});
      status(line2+((j.notes||[]).length?'\n'+j.notes.join('\n'):''));
    }
  }
  function poll(){
    clearTimeout(S.timer);
    var j=S.job;
    if(!busy())return;
    S.timer=setTimeout(function(){
      api(base()+'/'+encodeURIComponent(j.id)).then(function(d){
        if(!S.job||S.job.id!==d.id)return;
        S.job=d;if(root.classList.contains('on'))render();poll();
      }).catch(function(err){
        if(!S.job||S.job.id!==j.id)return;
        S.job={id:j.id,status:'failed',error:err.message};render();
      });
    },POLL_MS);
    if(root.classList.contains('on'))render();
  }
  function generate(){
    var m=curModel();
    if(!m){status(T('先选一个可用的 3D 模型'),true);return}
    if(!S.slots.front){status(T('请先放正面参考图'),true);return}
    var images={},names={};
    VIEWS.forEach(function(v){
      var s=S.slots[v];if(!s)return;
      if(v!=='front'&&m.multiview===false)return;     // 不收多视图的模型只发正面
      images[v]=s.path;if(s.name&&!s.ref)names[v]=s.name;
    });
    var prev=S.job;
    S.msg='';$('.g3-go').disabled=true;$('.g3-again').disabled=true;
    api(base(),{images:images,names:names,provider:m.provider,model:m.id,texture:$('.g3-tex').checked,
                discard:prev&&!busy()?prev.id:''})
      .then(function(d){S.job=d;render();poll()})
      .catch(function(err){$('.g3-go').disabled=false;$('.g3-again').disabled=false;status(T('生成失败:')+err.message,true)});
  }
  function adopt(){
    var j=S.job;
    if(!j||j.status!=='done')return;
    var b=$('.g3-add');b.disabled=true;
    api(base()+'/'+encodeURIComponent(j.id)+'/adopt',{}).then(function(d){
      (j.adopted=j.adopted||{}).models3d=d.path;
      S.msg=d.existed?F('「{cat}」里已有这个模型:{path}',{cat:catName(),path:d.path}):F('已加入「{cat}」:{path}',{cat:catName(),path:d.path});
      status(S.msg);render();
      if(S.opts.onAdded)try{S.opts.onAdded(d)}catch(_){}
    }).catch(function(err){b.disabled=false;status(T('加入失败:')+err.message,true)});
  }

  function close(){
    root.classList.remove('on');$('.g3-pick').classList.remove('on');
    if(S.view){S.view.dispose();S.view=null;S.viewJob=''}      // 关窗释放 WebGL;再打开时重新挂
  }
  function open(opts){
    if(!root)build();
    S.opts=opts||{};S.msg='';
    $('.g3-title').textContent='🧊 '+T('生成 3D 模型');
    root.classList.add('on');
    renderSlots();render();poll();
    loadOptions();
  }

  window.RefGen3D={open:open,close:close,engine:engine};
})();
