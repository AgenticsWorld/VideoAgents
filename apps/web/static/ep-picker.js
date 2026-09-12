/* 分集入口(2026-09-12):剧本/故事板/分镜/视频四个预览页共用的顶部集行。
   集数 ≤ EpPicker.MAX 时逐集按钮平铺(原样);超过时收成一行「◀ [下拉] ▶ k/N」,
   避免几十集的项目(liaozhai2 之类)把顶部版面撑掉半屏。后期处理页本就是下拉,不经此处。
   用法:EpPicker.render($('#eps'), episodes, EP, ep=>{EP=ep;load()}, {btnTag, optTag})
     btnTag(e) → 按钮模式集号后缀 html(状态角标,可省) ; optTag(e) → 下拉模式 option 纯文本后缀(可省) */
(function(){
  const esc=s=>String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const tr=s=>(window.t?window.t(s):s);
  let cssDone=false;
  function css(){
    if(cssDone)return;cssDone=true;
    const st=document.createElement('style');
    st.textContent=`#eps.compact{align-items:center;flex-wrap:nowrap}
#eps.compact select{background:var(--panel2);color:var(--text);border:1px solid var(--accent);border-radius:6px;padding:4px 8px;font-size:13px;font-weight:600;cursor:pointer;max-width:min(420px,60vw)}
#eps.compact select:hover{border-color:var(--link)}
#eps.compact .epnav{padding:4px 9px}
#eps.compact .epnav:disabled{opacity:.4;cursor:default}
#eps.compact .epn{color:var(--dim);font-size:12px;white-space:nowrap}`;
    document.head.appendChild(st);
  }
  window.EpPicker={
    MAX:12,
    render(box,episodes,cur,onPick,opt){
      opt=opt||{};box.innerHTML='';
      episodes=episodes||[];
      if(episodes.length<=EpPicker.MAX){
        box.classList.remove('compact');
        episodes.forEach(e=>{
          const b=document.createElement('button');
          b.className=e.ep===cur?'on':'';
          b.innerHTML=esc(e.ep)+(opt.btnTag?opt.btnTag(e):'');   // 入口只显示集号(+状态角标),集名挂 tooltip
          if(e.title)b.title=e.title;
          b.onclick=()=>onPick(e.ep);
          box.appendChild(b);
        });
        return;
      }
      css();box.classList.add('compact');
      const i=Math.max(0,episodes.findIndex(e=>e.ep===cur));
      const prev=document.createElement('button');prev.className='epnav';prev.textContent='◀';prev.title=tr('上一集');prev.disabled=i<=0;
      prev.onclick=()=>onPick(episodes[i-1].ep);
      const sel=document.createElement('select');sel.title=tr('切换分集');
      episodes.forEach(e=>{
        const o=document.createElement('option');o.value=e.ep;
        const tag=opt.optTag?opt.optTag(e):'';
        o.textContent=e.ep+(tag?' '+tag:'')+(e.title?' · '+e.title:'');
        o.selected=e.ep===cur;sel.appendChild(o);
      });
      sel.onchange=()=>onPick(sel.value);
      const next=document.createElement('button');next.className='epnav';next.textContent='▶';next.title=tr('下一集');next.disabled=i>=episodes.length-1;
      next.onclick=()=>onPick(episodes[i+1].ep);
      const n=document.createElement('span');n.className='epn';n.textContent=(i+1)+' / '+episodes.length;
      box.append(prev,sel,next,n);
    }
  };
})();
