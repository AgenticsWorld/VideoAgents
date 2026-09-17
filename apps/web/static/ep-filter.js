/* 按集过滤(2026-09-17):场景/人物/生物/道具 四个预览页共用——搜索框前的分集下拉,选集后列表只留该集出现的资产。
   数据:GET /previews/asset-episodes → {episodes:[{ep,title}], scenes|characters|creatures|props:{id:[ep…]}}(modules/asset_episodes.py)
   选中的集按项目存 localStorage(epfilter:<project>),四个页面之间切换沿用同一集。
   用法:EpFilter.mount({kind:'props',onChange:renderList});  load() 里 await EpFilter.load(proj);  过滤 EpFilter.match(id) */
(function(){
  const tr=s=>(window.t?window.t(s):s);
  let KIND='',SEL=null,MAP={},EPS=[],EP='',PROJ='';
  const key=()=>'epfilter:'+PROJ;
  function css(){
    const st=document.createElement('style');
    st.textContent=`.epf-row{display:flex;align-items:center;gap:6px;margin:10px}
.epf-row #search{margin:0;flex:1;min-width:0}
.epf-row select{flex:none;max-width:96px;padding:6px 4px;border-radius:8px;border:1px solid var(--border);background:var(--panel2);color:var(--text);cursor:pointer}
.epf-row select.on{border-color:var(--accent);color:var(--accent)}`;
    document.head.appendChild(st);
  }
  function fill(){
    SEL.innerHTML='';
    const all=document.createElement('option');all.value='';all.textContent=tr('全部集');SEL.appendChild(all);
    EPS.forEach(e=>{
      const o=document.createElement('option');o.value=e.ep;
      const n=Object.values(MAP).filter(v=>v.includes(e.ep)).length;
      o.textContent=e.ep+' ('+n+')';
      if(e.title)o.title=e.title;
      SEL.appendChild(o);
    });
    SEL.value=EP;SEL.classList.toggle('on',!!EP);
    SEL.style.display=EPS.length?'':'none';
  }
  window.EpFilter={
    mount(opt){
      KIND=opt.kind;
      const inp=document.querySelector(opt.search||'#search');
      css();
      const row=document.createElement('div');row.className='epf-row';
      inp.parentNode.insertBefore(row,inp);
      SEL=document.createElement('select');SEL.id='epfilter';SEL.title=tr('按集过滤:只显示该集出现的资产');
      row.append(SEL,inp);
      SEL.onchange=()=>{
        EP=SEL.value;SEL.classList.toggle('on',!!EP);
        try{EP?localStorage.setItem(key(),EP):localStorage.removeItem(key())}catch(e){}
        opt.onChange&&opt.onChange(EP);
      };
      fill();
    },
    // 失败不拦页面:拿不到映射就当没有分集,下拉隐藏、match 全放行
    async load(project){
      PROJ=project;MAP={};EPS=[];
      try{
        const r=await fetch('/api/v1/projects/'+encodeURIComponent(project)+'/previews/asset-episodes');
        if(r.ok){const d=await r.json();MAP=d[KIND]||{};EPS=d.episodes||[]}
      }catch(e){}
      let saved='';try{saved=localStorage.getItem(key())||''}catch(e){}
      EP=EPS.some(e=>e.ep===saved)?saved:'';
      if(SEL)fill();
    },
    get ep(){return EP},
    match(id){return !EP||(MAP[id]||[]).includes(EP)}
  };
})();
