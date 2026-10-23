/* 「🔎 图库」弹窗(2026-10-23;参考文件页 preview_refs.html,后端 modules/stock_images.py)。
 * 搜免费图库(Openverse 免 Key;Pexels / Pixabay 有 API Key 才可选),点图把它下载进参考文件的某个板块。
 * 用法:<script src="/static/stock-gallery.js"></script>(放 i18n.js 之后)
 *      StockGallery.open({project, categories, target, imported, onAdded})
 *        categories = [{key, ico, name}] 可加入的板块(name 为中文原文,组件里再 t());
 *        target     = 打开时底栏「目标板块」的默认值(从某个板块的 🔎 点进来时就是该板块);
 *        imported() → {"<provider>:<id>": [板块 key…]} 已在参考文件里的图(卡片打 ✓);
 *        onAdded(res) 每加入一张后回调(页面据此刷新列表)。
 * 两种加法:卡片底部板块图标「点哪个进哪个」;勾选多张后底栏「加入参考文件」统一加进目标板块。
 * 子目录 / 注释只用于目标板块(图标点的板块与目标板块相同时也用上)。
 */
(function(){
  'use strict';
  var T=function(s){return (window.t||function(x){return x})(s)};
  var F=function(s,p){return window.I18N&&I18N.f?I18N.f(s,p):String(s).replace(/\{(\w+)\}/g,function(m,k){return k in p?p[k]:m})};
  var esc=function(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})};
  var CJK=/[぀-ヿ㐀-鿿가-힯]/;
  var SUBDIR_CATS={characters:1,creatures:1,props:1};
  var PREF_KEY='stock_gallery_prefs';

  var st=document.createElement('style');
  st.textContent=[
    '.sg-mask{position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:90;display:none;align-items:center;justify-content:center;padding:16px}',
    '.sg-mask.on{display:flex}',
    '.sg-box{background:var(--panel,#161a23);border:1px solid var(--border,#2a3040);border-radius:12px;width:min(1180px,100%);height:min(860px,100%);display:flex;flex-direction:column;overflow:hidden;box-shadow:0 10px 40px rgba(0,0,0,.5)}',
    '.sg-head{display:flex;align-items:center;gap:10px;padding:10px 14px;border-bottom:1px solid var(--border,#2a3040)}',
    '.sg-head h3{font-size:15px;white-space:nowrap}.sg-head .sg-hint{color:var(--dim,#8b93a7);font-size:12px;flex:1;min-width:0}',
    '.sg-bar{display:flex;flex-wrap:wrap;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid var(--border,#2a3040)}',
    '.sg-bar input.sg-q{flex:1;min-width:200px}',
    '.sg-bar select,.sg-foot select{background:var(--panel2,#1c2130);color:var(--text,#dde3ee);border:1px solid var(--border,#2a3040);border-radius:6px;padding:5px 8px;font-size:13px}',
    '.sg-status{padding:6px 14px;font-size:12px;color:var(--dim,#8b93a7);min-height:28px}',
    '.sg-status.err{color:var(--red,#f87171)}',
    '.sg-body{flex:1;overflow-y:auto;padding:4px 14px 14px}',
    '.sg-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px}',
    '.sg-card{position:relative;background:var(--panel2,#1c2130);border:1px solid var(--border,#2a3040);border-radius:9px;overflow:hidden;display:flex;flex-direction:column}',
    '.sg-card.sel{border-color:var(--accent,#6c9ef8);box-shadow:0 0 0 2px var(--accent,#6c9ef8) inset}',
    '.sg-img{position:relative;height:150px;background:var(--bg,#0f1117);cursor:pointer}',
    '.sg-img img{width:100%;height:100%;object-fit:cover;display:block}',
    '.sg-chk{position:absolute;left:6px;top:6px;width:20px;height:20px;border-radius:5px;border:1.5px solid #fff;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;font-size:13px;color:#0b1020;pointer-events:none}',
    '.sg-card.sel .sg-chk{background:var(--accent,#6c9ef8);border-color:var(--accent,#6c9ef8)}',
    '.sg-zoom{position:absolute;right:6px;top:6px;font-size:12px;padding:1px 6px;background:rgba(0,0,0,.5);border:1px solid rgba(255,255,255,.3);border-radius:5px;color:#fff;cursor:zoom-in}',
    '.sg-img.noimg::after{content:"🖼";position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:36px;opacity:.35}',
    '.sg-quick{display:flex;gap:2px;padding:5px 6px 0;flex-wrap:wrap}',
    '.sg-quick button,.sg-vacts button{font-size:12px;padding:0 3px;line-height:1.6;background:var(--panel,#161a23);border:1px solid var(--border,#2a3040);border-radius:5px;cursor:pointer}',
    '.sg-quick button:hover,.sg-vacts button:hover{border-color:var(--accent,#6c9ef8)}',
    '.sg-quick button.done,.sg-vacts button.done{border-color:var(--green,#4ade80);background:rgba(74,222,128,.12)}',
    '.sg-quick button:disabled{opacity:.5;cursor:wait}',
    '.sg-meta{padding:4px 7px 6px;font-size:11px;color:var(--dim,#8b93a7);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}',
    '.sg-meta .by{color:var(--yellow,#fbbf24)}',
    '.sg-more{text-align:center;padding:14px 0 4px}',
    '.sg-foot{border-top:1px solid var(--border,#2a3040);padding:10px 14px;display:flex;flex-wrap:wrap;align-items:center;gap:8px;font-size:13px}',
    '.sg-foot input.sg-sub{width:140px}.sg-foot input.sg-note{flex:1;min-width:160px}',
    '.sg-credit{flex-basis:100%;color:var(--dim,#8b93a7);font-size:11.5px}',
    '.sg-view{position:fixed;inset:0;z-index:95;background:rgba(0,0,0,.88);display:none;align-items:center;justify-content:center;gap:16px;padding:20px}',
    '.sg-view.on{display:flex}',
    '.sg-view img{max-width:calc(100vw - 380px);max-height:92vh;border-radius:6px;background:#000}',
    '.sg-vinfo{width:320px;max-height:92vh;overflow-y:auto;background:var(--panel,#161a23);border:1px solid var(--border,#2a3040);border-radius:10px;padding:14px;font-size:12.5px;display:flex;flex-direction:column;gap:8px}',
    '.sg-vinfo .ttl{font-size:14px;font-weight:600;word-break:break-word}',
    '.sg-vinfo .row{color:var(--dim,#8b93a7);word-break:break-word}.sg-vinfo .row b{color:var(--text,#dde3ee);font-weight:500}',
    '.sg-vacts{display:flex;flex-wrap:wrap;gap:5px}.sg-vacts button{font-size:12.5px;padding:3px 8px}',
    '@media (max-width:760px){.sg-view{flex-direction:column}.sg-view img{max-width:96vw;max-height:55vh}.sg-vinfo{width:96vw;max-height:40vh}}'
  ].join('\n');
  document.head.appendChild(st);

  var S={opts:null,providers:null,results:[],page:1,hasMore:false,busy:false,sel:{},done:{},q:'',view:null,seq:0};
  var root=null;
  function $(s){return root.querySelector(s)}
  function prefs(){try{return JSON.parse(localStorage.getItem(PREF_KEY)||'{}')||{}}catch(_){return {}}}
  function savePrefs(){
    try{localStorage.setItem(PREF_KEY,JSON.stringify({provider:$('.sg-prov').value,orientation:$('.sg-ori').value,kind:$('.sg-kind').value}))}catch(_){}
  }
  function keyOf(it){return it.provider+':'+it.id}
  function cat(k){return (S.opts.categories||[]).filter(function(c){return c.key===k})[0]}
  function catName(k){var c=cat(k);return c?T(c.name):k}
  function api(url,body){
    return fetch(url,body===undefined?{cache:'no-store'}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
      .then(function(r){return r.json().catch(function(){return {}}).then(function(d){if(!r.ok)throw new Error(d.detail||('HTTP '+r.status));return d})});
  }
  function status(msg,err){var el=$('.sg-status');el.textContent=msg||'';el.classList.toggle('err',!!err)}

  function build(){
    root=document.createElement('div');
    root.className='sg-mask';
    root.innerHTML='<div class="sg-box" role="dialog" aria-modal="true">'
      +'<div class="sg-head"><h3>🔎 '+esc(T('图库'))+'</h3><span class="sg-hint">'+esc(T('搜索免费可商用的图片,点卡片下方的板块图标即可加入参考文件(下载到项目 refs/,长边超过 2560 自动缩小)'))+'</span>'
      +'<button type="button" class="sg-x" title="'+esc(T('关闭'))+'">✕</button></div>'
      +'<div class="sg-bar"><select class="sg-prov" title="'+esc(T('图源'))+'"></select>'
      +'<input type="text" class="sg-q" placeholder="'+esc(T('搜索关键词,如:青铜鼎、ancient palace'))+'">'
      +'<button type="button" class="sg-tr" disabled title="'+esc(T('Openverse 的图片标题大多是英文,用中文搜结果少。点此用 Agentics 引擎当前的文本模型把关键词译成英文再搜(每点一次调用一次模型)'))+'">'+esc(T('译成英文'))+'</button>'
      +'<button type="button" class="sg-go primary">'+esc(T('搜索'))+'</button>'
      +'<select class="sg-ori"><option value="">'+esc(T('全部方向'))+'</option><option value="horizontal">'+esc(T('横图'))+'</option><option value="vertical">'+esc(T('竖图'))+'</option><option value="square">'+esc(T('方图'))+'</option></select>'
      +'<select class="sg-kind"><option value="">'+esc(T('全部类型'))+'</option><option value="photo">'+esc(T('照片'))+'</option><option value="illustration">'+esc(T('插画 / 艺术品'))+'</option></select></div>'
      +'<div class="sg-status"></div>'
      +'<div class="sg-body"><div class="sg-grid" data-no-i18n></div><div class="sg-more" style="display:none"><button type="button" class="sg-morebtn">'+esc(T('加载更多'))+'</button></div></div>'
      +'<div class="sg-foot"><span class="sg-cnt"></span><select class="sg-target" title="'+esc(T('目标板块'))+'"></select>'
      +'<input type="text" class="sg-sub" placeholder="'+esc(T('子目录(可选)'))+'" title="'+esc(T('只对目标板块生效;角色 / 生物 / 道具可填角色名、生物名或 costumes(仅英文数字)'))+'">'
      +'<input type="text" class="sg-note" placeholder="'+esc(T('注释(可选,写进 refs/NOTES.md,Agent 必读)'))+'" title="'+esc(T('只对目标板块生效'))+'">'
      +'<button type="button" class="sg-add primary" disabled>'+esc(T('加入参考文件'))+'</button>'
      +'<div class="sg-credit"></div></div></div>'
      +'<div class="sg-view"><img alt="" referrerpolicy="no-referrer"><div class="sg-vinfo" data-no-i18n></div></div>';
    document.body.appendChild(root);

    $('.sg-x').onclick=close;
    root.addEventListener('mousedown',function(e){if(e.target===root)close()});
    $('.sg-go').onclick=function(){search(1)};
    $('.sg-q').addEventListener('keydown',function(e){if(e.key==='Enter'&&!e.isComposing){e.preventDefault();search(1)}});
    $('.sg-q').addEventListener('input',syncTr);
    $('.sg-tr').onclick=translate;
    $('.sg-morebtn').onclick=function(){search(S.page+1)};
    $('.sg-prov').onchange=function(){savePrefs();syncProv();if(S.q)search(1)};
    $('.sg-ori').onchange=function(){savePrefs();if(S.q)search(1)};
    $('.sg-kind').onchange=function(){savePrefs();if(S.q)search(1)};
    $('.sg-target').onchange=function(){syncTarget();renderCards()};
    $('.sg-sub').addEventListener('input',renderQuickTitles);
    $('.sg-add').onclick=addSelected;
    $('.sg-grid').addEventListener('click',onGrid);
    // 缩略图加载失败(Openverse /thumb/ 对部分来源回 424 JSON,浏览器按 ORB 拦掉):换备用地址一次,再失败显示占位
    $('.sg-grid').addEventListener('error',function(e){
      var im=e.target;
      if(!im||im.tagName!=='IMG')return;
      if(im.dataset.alt&&im.src!==im.dataset.alt){im.src=im.dataset.alt;im.dataset.alt='';return}
      im.style.visibility='hidden';im.parentNode.classList.add('noimg');
    },true);
    $('.sg-view').addEventListener('click',function(e){
      var b=e.target.closest('button[data-cat]');
      if(b){addOne(S.view,b.dataset.cat,b);return}
      if(e.target.closest('a'))return;
      if(!e.target.closest('.sg-vinfo'))hideView();
    });
    document.addEventListener('keydown',function(e){
      if(e.key!=='Escape'||!root.classList.contains('on'))return;
      if($('.sg-view').classList.contains('on'))hideView();else close();
    });
  }

  function syncTr(){$('.sg-tr').disabled=!CJK.test($('.sg-q').value)}
  function syncProv(){
    var p=$('.sg-prov').value;
    $('.sg-kind').style.display=p==='pexels'?'none':'';      // Pexels 只有照片
    var info=(S.providers||[]).filter(function(x){return x.id===p})[0]||{};
    $('.sg-credit').innerHTML=esc(T('图片来自'))+' <a href="'+esc(info.home||'#')+'" target="_blank" rel="noopener">'+esc(info.label||p)+'</a> · '
      +esc(p==='openverse'?T('只收 CC0 / 公有领域 / CC BY;CC BY 图出现在成片里时须署名,来源记在 refs/CREDITS.md')
        :p==='pexels'?T('Pexels License:可商用、不必署名'):T('Pixabay Content License:可商用、不必署名;含商标的图不能商用'));
  }
  function syncTarget(){
    var k=$('.sg-target').value;
    $('.sg-sub').style.display=SUBDIR_CATS[k]?'':'none';
  }
  function syncCount(){
    var n=Object.keys(S.sel).length;
    $('.sg-cnt').textContent=n?F('已选 {n} 张 →',{n:n}):T('勾选多张后可一起加入 →');
    $('.sg-add').disabled=!n||S.busy;
  }

  function subFor(k){return k===$('.sg-target').value&&SUBDIR_CATS[k]?$('.sg-sub').value.trim():''}
  function noteFor(k){return k===$('.sg-target').value?$('.sg-note').value.trim():''}
  function quickTitle(k){
    var sub=subFor(k);
    return F('加入「{cat}」',{cat:catName(k)+(sub?'/'+sub:'')});
  }
  function renderQuickTitles(){
    root.querySelectorAll('button[data-cat]').forEach(function(b){b.title=quickTitle(b.dataset.cat)});
  }
  function doneCats(it){
    var k=keyOf(it),m=(S.opts.imported&&S.opts.imported())||{},out={};
    (m[k]||[]).forEach(function(c){out[c]=1});
    Object.keys(S.done[k]||{}).forEach(function(c){out[c]=1});
    return out;
  }
  function quickHtml(it){
    var done=doneCats(it);
    return (S.opts.categories||[]).map(function(c){
      return '<button type="button" data-cat="'+esc(c.key)+'" class="'+(done[c.key]?'done':'')+'" title="'+esc(quickTitle(c.key))+'">'+c.ico+'</button>';
    }).join('');
  }
  function cardHtml(it,i){
    var k=keyOf(it),done=doneCats(it);
    var doneTxt=Object.keys(done).map(function(c){return '✓ '+catName(c)}).join(' ');
    return '<div class="sg-card'+(S.sel[k]?' sel':'')+'" data-i="'+i+'">'
      +'<div class="sg-img" title="'+esc(T('点图勾选'))+'"><img loading="lazy" referrerpolicy="no-referrer" src="'+esc(it.thumb)+'" data-alt="'+esc(it.thumb_alt||'')+'" alt="">'
      +'<span class="sg-chk">'+(S.sel[k]?'✓':'')+'</span><button type="button" class="sg-zoom" title="'+esc(T('看大图与来源'))+'">🔍</button></div>'
      +'<div class="sg-quick">'+quickHtml(it)+'</div>'
      +'<div class="sg-meta" title="'+esc((it.title||'')+(it.author?' — '+it.author:''))+'"><span class="'+(it.attribution_required?'by':'')+'">'+esc(it.license_label)+'</span> · '+esc(it.site)
      +(doneTxt?' · <span style="color:var(--green,#4ade80)">'+esc(doneTxt)+'</span>':'')+'</div></div>';
  }
  function renderCards(){
    $('.sg-grid').innerHTML=S.results.map(cardHtml).join('');
    $('.sg-more').style.display=S.hasMore?'':'none';
    syncCount();
  }
  function refreshCard(i){
    var el=$('.sg-grid').querySelector('.sg-card[data-i="'+i+'"]');
    if(el)el.outerHTML=cardHtml(S.results[i],i);
    if(S.view===S.results[i])showView(S.view);
  }

  function onGrid(e){
    var card=e.target.closest('.sg-card');
    if(!card)return;
    var i=+card.dataset.i,it=S.results[i];
    var b=e.target.closest('button[data-cat]');
    if(b){addOne(it,b.dataset.cat,b);return}
    if(e.target.closest('.sg-zoom')){showView(it);return}
    if(e.target.closest('.sg-img')){
      var k=keyOf(it);
      if(S.sel[k])delete S.sel[k];else S.sel[k]=it;
      refreshCard(i);syncCount();
    }
  }

  function rateText(d){
    var r=d.rate||{},parts=[];
    if(r.burst&&r.burst.available!=null)parts.push(F('本分钟剩 {a}',{a:r.burst.available}));
    if(r.sustained&&r.sustained.available!=null)parts.push(F('今天剩 {a}',{a:r.sustained.available}));
    if(r.monthly&&r.monthly.available!=null)parts.push(F('本月剩 {a}',{a:r.monthly.available}));
    return parts.length?' · '+F('额度:{list}',{list:parts.join(' / ')}):'';
  }
  function search(page){
    var q=$('.sg-q').value.trim();
    if(!q){status(T('请输入搜索关键词'),true);$('.sg-q').focus();return}
    var prov=$('.sg-prov').value,seq=++S.seq;
    if(page===1){S.results=[];S.q=q;renderCards()}
    status(T('搜索中…'));
    $('.sg-morebtn').disabled=true;
    api('/api/v1/stock/search',{provider:prov,q:q,page:page,orientation:$('.sg-ori').value,kind:prov==='pexels'?'':$('.sg-kind').value})
      .then(function(d){
        if(seq!==S.seq)return;
        S.page=d.page||page;S.hasMore=!!d.has_more;
        S.results=page===1?d.results:S.results.concat(d.results);
        renderCards();
        var msg=S.results.length?F('{total} 条结果,已显示 {n} 条',{total:d.total,n:S.results.length}):T('没有找到图片,换个关键词试试');
        if(prov==='openverse'&&CJK.test(q)&&(d.total||0)<60)msg+=' · '+T('用中文搜到的少,试试「译成英文」');
        status(msg+(d.cached?' · '+T('24 小时内搜过,用的缓存'):rateText(d)));
      })
      .catch(function(err){if(seq===S.seq)status(T('搜索失败:')+err.message,true)})
      .then(function(){$('.sg-morebtn').disabled=false});
  }
  function translate(){
    var q=$('.sg-q').value.trim();
    if(!q)return;
    var b=$('.sg-tr');b.disabled=true;
    status(T('正在用文本模型翻译…'));
    api('/api/v1/stock/translate',{text:q}).then(function(d){
      $('.sg-q').value=d.text;syncTr();
      search(1);
      $('.sg-q').title=F('由 {q} 翻译(模型 {m})',{q:q,m:d.model||''});
    }).catch(function(err){status(T('翻译失败:')+err.message,true);syncTr()});
  }

  function addOne(it,catKey,btn){
    if(!it)return Promise.resolve(false);
    if(btn)btn.disabled=true;
    var sub=subFor(catKey);
    status(F('正在下载到「{cat}」…',{cat:catName(catKey)+(sub?'/'+sub:'')}));
    return api('/api/v1/projects/'+encodeURIComponent(S.opts.project)+'/references/import',
      {provider:it.provider,id:it.id,category:catKey,subdir:sub,note:noteFor(catKey),query:S.q})
      .then(function(d){
        var k=keyOf(it);(S.done[k]=S.done[k]||{})[catKey]=1;
        var i=S.results.indexOf(it);if(i>=0)refreshCard(i);
        status(d.existed?F('「{cat}」里已有这张图:{path}',{cat:catName(catKey),path:d.path}):F('已加入「{cat}」:{path}',{cat:catName(catKey),path:d.path}));
        if(S.opts.onAdded)try{S.opts.onAdded(d)}catch(_){}
        return true;
      })
      .catch(function(err){status(T('加入失败:')+err.message,true);if(btn)btn.disabled=false;return false});
  }
  function addSelected(){
    var items=Object.keys(S.sel).map(function(k){return S.sel[k]}),catKey=$('.sg-target').value;
    if(!items.length||!catKey)return;
    S.busy=true;syncCount();
    var ok=0,i=0;
    (function next(){
      if(i>=items.length){
        S.busy=false;
        renderCards();
        status(F('已把 {n} 张加入「{cat}」',{n:ok,cat:catName(catKey)}));
        return;
      }
      var it=items[i++];
      $('.sg-add').textContent=F('加入中 {done}/{total}…',{done:i,total:items.length});
      addOne(it,catKey,null).then(function(good){
        if(!good){S.busy=false;$('.sg-add').textContent=T('加入参考文件');syncCount();return}   // 失败即停,错误留在状态栏
        ok++;delete S.sel[keyOf(it)];
        if(i>=items.length)$('.sg-add').textContent=T('加入参考文件');
        next();
      });
    })();
  }

  function showView(it){
    S.view=it;
    var v=$('.sg-view');
    v.querySelector('img').src=it.preview||it.thumb;
    var link=function(u,txt){return u?'<a href="'+esc(u)+'" target="_blank" rel="noopener">'+esc(txt)+'</a>':esc(txt)};
    var done=doneCats(it);
    v.querySelector('.sg-vinfo').innerHTML='<div class="ttl">'+esc(it.title||T('(无标题)'))+'</div>'
      +(it.author?'<div class="row">'+esc(T('作者'))+': <b>'+link(it.author_url,it.author)+'</b></div>':'')
      +'<div class="row">'+esc(T('来源'))+': <b>'+esc(it.site)+'</b>'+(it.page_url?' · '+link(it.page_url,T('原页面')):'')+'</div>'
      +'<div class="row">'+esc(T('许可'))+': <b>'+link(it.license_url,it.license_label)+'</b>'+(it.attribution_required?' · <span style="color:var(--yellow,#fbbf24)">'+esc(T('出现在成片里须署名'))+'</span>':'')+'</div>'
      +(it.width?'<div class="row">'+esc(T('尺寸'))+': <b>'+it.width+' × '+it.height+'</b></div>':'')
      +'<div class="row">'+esc(T('加入参考文件'))+':</div><div class="sg-vacts">'+(S.opts.categories||[]).map(function(c){
        return '<button type="button" data-cat="'+esc(c.key)+'" class="'+(done[c.key]?'done':'')+'" title="'+esc(quickTitle(c.key))+'">'+c.ico+' '+esc(T(c.name))+'</button>'}).join('')+'</div>';
    v.classList.add('on');
  }
  function hideView(){S.view=null;$('.sg-view').classList.remove('on')}

  function close(){hideView();root.classList.remove('on')}
  function fill(){
    var p=prefs();
    $('.sg-prov').innerHTML=(S.providers||[]).map(function(x){
      return '<option value="'+esc(x.id)+'"'+(x.ready?'':' disabled')+'>'+esc(x.label+(x.ready?'':' '+T('(需 API Key:⚙️ 设置 → 存储与图库)')))+'</option>'}).join('');
    var ready=(S.providers||[]).filter(function(x){return x.ready}).map(function(x){return x.id});
    $('.sg-prov').value=ready.indexOf(p.provider)>=0?p.provider:(ready[0]||'openverse');
    if(p.orientation!=null)$('.sg-ori').value=p.orientation;
    if(p.kind!=null)$('.sg-kind').value=p.kind;
    syncProv();
  }
  function open(opts){
    if(!root)build();
    S.opts=opts||{};
    $('.sg-target').innerHTML=(S.opts.categories||[]).map(function(c){return '<option value="'+esc(c.key)+'">'+c.ico+' '+esc(T(c.name))+'</option>'}).join('');
    if(S.opts.target&&cat(S.opts.target))$('.sg-target').value=S.opts.target;
    if(S.opts.subdir!=null)$('.sg-sub').value=S.opts.subdir;
    syncTarget();
    root.classList.add('on');
    renderCards();
    syncTr();
    setTimeout(function(){$('.sg-q').focus()},0);
    if(S.providers){fill();return}
    status(T('加载图源…'));
    api('/api/v1/stock/providers').then(function(d){S.providers=d.providers||[];fill();status('')})
      .catch(function(err){S.providers=[{id:'openverse',label:'Openverse',home:'https://openverse.org',ready:true}];fill();
        status(T('图源列表加载失败(服务可能还没重启):')+err.message,true)});
  }

  window.StockGallery={open:open,close:close};
})();
