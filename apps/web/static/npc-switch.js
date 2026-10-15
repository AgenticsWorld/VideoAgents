/* NPC 参与构图 · 场次开关(2026-10-09,docs/npc_staging.md)——剧本预览页与故事板页共用。
 * 数据:预览接口的 npc = {master: auto|off, scenes: {S01: {on, density, source, reason, auto, user, suggest, applied?}}}
 * 写入:POST /api/v1/projects/<p>/episodes/<ep>/npc {scene, mode: auto|on|off, density} → story/episodes/<ep>/scene_npc.json
 * 用法:NpcSwitch.init({project:()=>..., ep:()=>..., data:()=>npc, onSaved:npc=>{...}});场次头放 NpcSwitch.chip(no)。
 */
(function(){
  'use strict';
  const T=s=>(window.t?window.t(s):s);
  const F=(s,p)=>(window.I18N&&I18N.f?I18N.f(s,p):String(s).replace(/\{(\w+)\}/g,(m,k)=>k in (p||{})?p[k]:m));
  const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const DENS={sparse:'稀疏',medium:'适中',dense:'热闹'};
  let CFG=null,OPEN=null;

  function css(){
    if(document.getElementById('npcsw-css'))return;
    const st=document.createElement('style');st.id='npcsw-css';
    st.textContent=`
.npcsw{display:inline-flex;align-items:center;gap:4px;font-size:11px;border:1px solid var(--border,#2a3040);border-radius:10px;padding:0 8px;margin:1px 3px 1px 0;white-space:nowrap;background:var(--panel,#161a23);color:var(--dim,#8b93a7);cursor:pointer;font-family:inherit;line-height:1.7}
.npcsw:hover{border-color:var(--accent,#6c9ef8);color:var(--text,#dde3ee)}
.npcsw.on{color:var(--green,#4ade80);border-color:rgba(74,222,128,.45)}
.npcsw .npcsw-src{opacity:.75;font-size:10px}
.npcsw .npcsw-stale{color:var(--yellow,#fbbf24)}
.npcsw.moff{opacity:.6}
#npcsw-pop{position:fixed;z-index:99;width:min(380px,92vw);background:var(--panel,#161a23);color:var(--text,#dde3ee);border:1px solid var(--border,#2a3040);border-radius:10px;padding:12px 14px;box-shadow:0 10px 40px #000;font-size:12.5px;line-height:1.6}
#npcsw-pop h4{font-size:13.5px;margin:0 0 4px}
#npcsw-pop .d{color:var(--dim,#8b93a7);font-size:11.5px;margin-bottom:8px}
#npcsw-pop .modes{display:flex;gap:6px;margin:6px 0}
#npcsw-pop .modes label{flex:1;display:flex;align-items:center;justify-content:center;gap:4px;border:1px solid var(--border,#2a3040);border-radius:6px;padding:4px 6px;cursor:pointer}
#npcsw-pop .modes label.sel{border-color:var(--accent,#6c9ef8);color:var(--accent,#6c9ef8)}
#npcsw-pop .modes input{display:none}
#npcsw-pop .row{display:flex;align-items:center;gap:8px;margin:6px 0}
#npcsw-pop select{flex:1;background:var(--panel2,#1c2130);color:var(--text,#dde3ee);border:1px solid var(--border,#2a3040);border-radius:6px;padding:3px 6px;font-size:12px}
#npcsw-pop .info{color:var(--dim,#8b93a7);font-size:11.5px;margin:3px 0}
#npcsw-pop .warn{color:var(--yellow,#fbbf24);font-size:11.5px;margin:3px 0}
#npcsw-pop .btns{display:flex;gap:8px;justify-content:flex-end;align-items:center;margin-top:10px}
#npcsw-pop .btns .err{flex:1;color:var(--red,#f87171);font-size:11.5px}
#npcsw-pop button{background:var(--panel2,#1c2130);color:var(--text,#dde3ee);border:1px solid var(--border,#2a3040);border-radius:6px;padding:4px 12px;cursor:pointer;font-size:12.5px;font-family:inherit}
#npcsw-pop button.pri{background:var(--accent,#6c9ef8);border-color:var(--accent,#6c9ef8);color:#0b1020;font-weight:600}`;
    document.head.appendChild(st);
  }

  function data(){return (CFG&&CFG.data&&CFG.data())||{master:'auto',scenes:{}}}
  function rec(no){return (data().scenes||{})[no]||{on:false,density:null,source:'none'}}
  function stateTxt(on,dens){return on?T('开')+'·'+T(DENS[dens]||'适中'):T('关')}

  /* 场次头 chip:🚶 NPC 开·适中 · 自动 [⚠ 分镜未按此设定] */
  function chip(no){
    css();
    const d=data(),r=rec(no);
    if(d.master==='off')return `<span class="npcsw moff" data-npc-scene="${esc(no)}" title="${esc(T('总开关已关闭:在「输出设置 → NPC 参与构图」里改为「按场次判定」后才能逐场开关'))}">🚶 NPC <span>${esc(T('总开关关'))}</span></span>`;
    const src=r.source==='user'?T('手动'):r.source==='auto'?T('自动'):T('未判定');
    const stale=r.applied==='stale'?` <span class="npcsw-stale">⚠ <span>${esc(T('分镜未按此设定'))}</span></span>`:'';
    return `<span class="npcsw ${r.on?'on':''}" data-npc-scene="${esc(no)}" title="${esc(T('点击切换本场 NPC 参与构图(自动 / 开 / 关 + 密度)'))}">🚶 NPC <span>${esc(stateTxt(r.on,r.density))}</span> <span class="npcsw-src">· <span>${esc(src)}</span></span>${stale}</span>`;
  }

  function close(){const p=document.getElementById('npcsw-pop');if(p)p.remove();OPEN=null}

  function open(no,anchor){
    close();css();OPEN=no;
    const d=data(),r=rec(no),u=r.user||{},a=r.auto,sg=r.suggest;
    const mode=u.mode||'auto';
    const pop=document.createElement('div');pop.id='npcsw-pop';
    let h=`<h4>🚶 <span>${esc(T('NPC 参与构图'))}</span> · <span data-no-i18n>${esc(no)}</span></h4>`;
    h+=`<div class="d">${esc(T('在画面里加入路人、前景物体等 NPC 元素,补充空间、丰富层次,让场景有前中后三层'))}</div>`;
    if(d.master==='off'){
      h+=`<div class="warn">${esc(T('总开关已关闭:在「输出设置 → NPC 参与构图」里改为「按场次判定」后才能逐场开关'))}</div><div class="btns"><span class="err"></span><button class="npc-cancel">${esc(T('关闭'))}</button></div>`;
    }else{
      h+=`<div class="modes">${['auto','on','off'].map(m=>`<label class="${m===mode?'sel':''}"><input type="radio" name="npcsw-mode" value="${m}" ${m===mode?'checked':''}><span>${esc(T(m==='auto'?'自动':m==='on'?'开':'关'))}</span></label>`).join('')}</div>`;
      h+=`<div class="info">${a?esc(F('自动判定:{v}',{v:stateTxt(a.on,a.density)})):esc(T('自动判定:未判定(按关处理)'))}${a&&a.reason?`<div data-no-i18n>${esc(a.reason)}</div>`:''}</div>`;
      if(sg)h+=`<div class="info">${esc(F('关键词建议:{v}',{v:stateTxt(sg.on,sg.density)}))} <span data-no-i18n>${esc(sg.reason||'')}</span></div>`;
      const dv=u.density||'';
      h+=`<div class="row"><span>${esc(T('密度'))}</span><select class="npc-dens"><option value="" ${dv?'':'selected'}>${esc(T('跟随自动判定'))}</option>${Object.keys(DENS).map(k=>`<option value="${k}" ${dv===k?'selected':''}>${esc(T(DENS[k]))}</option>`).join('')}</select></div>`;
      h+=`<div class="info">${esc(T('改动不会自动重做已出的分镜:故事板页会标出未按新设定的场次,用该场「✏️ 修改」让分镜师补写'))}</div>`;
      h+=`<div class="btns"><span class="err"></span><button class="npc-cancel">${esc(T('取消'))}</button><button class="pri npc-save">${esc(T('保存'))}</button></div>`;
    }
    pop.innerHTML=h;document.body.appendChild(pop);
    const syncSel=()=>{const m=(pop.querySelector('input[name=npcsw-mode]:checked')||{}).value;
      pop.querySelectorAll('.modes label').forEach(l=>l.classList.toggle('sel',l.querySelector('input').value===m));
      const s=pop.querySelector('.npc-dens');if(s)s.disabled=m==='off'};
    pop.querySelectorAll('input[name=npcsw-mode]').forEach(i=>i.onchange=syncSel);syncSel();
    const b=anchor.getBoundingClientRect(),pw=pop.offsetWidth,ph=pop.offsetHeight;
    pop.style.left=Math.max(8,Math.min(window.innerWidth-pw-8,b.left))+'px';
    pop.style.top=(b.bottom+6+ph>window.innerHeight?Math.max(8,b.top-ph-6):b.bottom+6)+'px';
    pop.querySelector('.npc-cancel').onclick=close;
    const sv=pop.querySelector('.npc-save');
    if(sv)sv.onclick=async()=>{
      const m=(pop.querySelector('input[name=npcsw-mode]:checked')||{}).value||'auto';
      const dens=m==='off'?'':(pop.querySelector('.npc-dens')||{}).value||'';
      sv.disabled=true;
      try{
        const r=await fetch(`/api/v1/projects/${encodeURIComponent(CFG.project())}/episodes/${encodeURIComponent(CFG.ep())}/npc`,
          {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scene:no,mode:m,density:dens})});
        const j=await r.json().catch(()=>({}));
        if(!r.ok)throw new Error(j.detail||j.error||r.status);
        close();
        if(CFG.onSaved)CFG.onSaved(j.npc||null,no);
      }catch(e){sv.disabled=false;pop.querySelector('.err').textContent=F('保存失败:{e}',{e:e.message})}
    };
  }

  document.addEventListener('click',e=>{
    const c=e.target.closest('[data-npc-scene]');
    if(c){e.preventDefault();e.stopPropagation();const no=c.dataset.npcScene;if(OPEN===no)close();else open(no,c);return}
    if(OPEN&&!e.target.closest('#npcsw-pop'))close();
  },true);
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&OPEN)close()});

  window.NpcSwitch={init(cfg){CFG=cfg;css()},chip,close,stateTxt};
})();
