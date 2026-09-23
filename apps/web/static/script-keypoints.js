/* User-owned screenplay selections. DOM offsets are UTF-16; no HTML is persisted. */
window.ScriptKeypoints = (() => {
  const style = document.createElement('style');
  style.textContent = `.kp-toolbar{position:fixed;z-index:1000;display:flex;gap:6px;padding:6px;background:var(--panel);border:1px solid var(--border);border-radius:8px;box-shadow:0 4px 18px #0008}.kp-toolbar[hidden]{display:none}.kp-plot{background:#77551d;color:#fff1c2}.kp-detail{background:#284e79;color:#e0efff}.kp-both{background:linear-gradient(#77551d 50%,#284e79 50%);color:white}.kp-list{margin:12px 0;padding:10px 12px;border:1px solid var(--border);border-radius:8px}.kp-list summary{cursor:pointer}.kp-item{margin-top:8px;display:flex;gap:8px;align-items:baseline}.kp-item .kp-quote{flex:1;white-space:pre-wrap}.kp-note{color:var(--dim);font-size:12px}`;
  document.head.append(style);
  const toolbar = document.createElement('div');
  toolbar.className = 'kp-toolbar'; toolbar.hidden = true; toolbar.setAttribute('role', 'toolbar'); toolbar.setAttribute('aria-label', '标记剧本关键点');
  toolbar.innerHTML = '<button class="kp-plot" data-kind="plot">关键情节</button><button class="kp-detail" data-kind="detail">关键细节</button>';
  document.body.append(toolbar);
  let state, parts = [], busy = false;
  const active = () => (state?.data.keypoints || []).filter(x => !x.removed);
  const label = x => x.kind === 'plot' ? '关键情节' : '关键细节';
  function hide() { toolbar.hidden = true; parts = []; }
  function cells() { return [...document.querySelectorAll('table.sc td.l')]; }
  function sceneFor(cell) {
    let row = cell.closest('tr');
    while (row && !row.dataset.stickScene) row = row.previousElementSibling;
    return row?.dataset.stickScene || '';
  }
  function paint() {
    if (!state) return;
    const unresolved = new Set();
    const allCells = cells();
    const ranges = new Map(allCells.map(c => [c, []]));
    for (const item of active()) for (const part of item.parts) {
      const candidates = allCells.filter(c => sceneFor(c) === part.scene && c.textContent === part.context);
      const cell = candidates[part.occurrence || 0];
      if (!cell) { unresolved.add(item.id); continue; }
      ranges.get(cell).push({start:part.start, end:part.end, kind:item.kind});
    }
    for (const [cell, marks] of ranges) {
      if (!marks.length) continue;
      const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT);
      const nodes = []; let offset = 0;
      while (walker.nextNode()) { const node = walker.currentNode; nodes.push({node, offset}); offset += node.length; }
      for (const {node, offset} of nodes) {
        const ends = new Set([0, node.length]);
        for (const m of marks) if (m.start < offset + node.length && m.end > offset) {
          ends.add(Math.max(0, m.start-offset)); ends.add(Math.min(node.length, m.end-offset));
        }
        if (ends.size === 2 && !marks.some(m => m.start < offset+node.length && m.end > offset)) continue;
        const cuts = [...ends].sort((a,b)=>a-b), frag = document.createDocumentFragment();
        for (let i=1;i<cuts.length;i++) {
          const a=cuts[i-1], b=cuts[i], kinds=new Set(marks.filter(m=>m.start<offset+b && m.end>offset+a).map(m=>m.kind));
          const text=node.data.slice(a,b);
          if (!kinds.size) frag.append(document.createTextNode(text));
          else { const mark=document.createElement('mark'); mark.className=kinds.size>1?'kp-both':'kp-'+[...kinds][0]; mark.textContent=text; mark.title=[...kinds].map(kind=>label({kind})).join('、'); frag.append(mark); }
        }
        node.replaceWith(frag);
      }
    }
    document.querySelector('.kp-list')?.remove();
    const list = document.createElement('details'); list.className='kp-list'; list.open=active().length>0;
    const summary=document.createElement('summary'); summary.textContent=I18N.f('剧本关键点（{n}）',{n:active().length});list.append(summary);
    const note=document.createElement('div');note.className='kp-note';note.textContent='选中剧本文字可标记「关键情节」或「关键细节」。关键点必须在分镜中体现；取消需确认。';list.append(note);
    for(const item of active()) {
      const row=document.createElement('div');row.className='kp-item';
      const tag=document.createElement('span');tag.className='kp-'+item.kind;tag.textContent=label(item);row.append(tag);
      const quote=document.createElement('span');quote.className='kp-quote';quote.textContent=item.parts.map(p=>p.quote).join('\n');row.append(quote);
      const status=document.createElement('span');status.className='kp-note';status.textContent=unresolved.has(item.id)?'原文已变化，仍须保留':(state.data.keypoints_missing||[]).includes(item.id)?'待分镜体现':'已关联分镜';row.append(status);
      const button=document.createElement('button');button.textContent='取消标记';button.disabled=busy;
      button.onclick=()=>{if(confirm(I18N.f('确认取消「{k}」？取消后，该内容不再作为分镜必须保留的关键点。',{k:label(item)})+'\n\n'+item.parts.map(p=>p.quote).join('\n')))save({action:'remove',id:item.id,confirmed:true});};row.append(button);list.append(row);
    }
    document.querySelector('table.sc')?.before(list);
  }
  async function save(body) {
    if (busy || !state) return;
    busy=true; hide();const current=state;
    toolbar.querySelectorAll('button').forEach(b=>b.disabled=true);
    try {
      const r=await fetch(`/api/v1/projects/${encodeURIComponent(current.project)}/script/${encodeURIComponent(current.data.ep)}/keypoints`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const d=await r.json();if(!r.ok)throw new Error(d.detail||'保存失败');
      window.getSelection()?.removeAllRanges();
      if(state===current)await current.reload();
    }catch(e){alert('关键点保存失败：'+e.message)}finally{busy=false;toolbar.querySelectorAll('button').forEach(b=>b.disabled=false);document.querySelectorAll('.kp-item button').forEach(b=>b.disabled=false);}
  }
  function select() {
    if(busy || !state) return;
    const sel=window.getSelection();
    if(!sel || sel.isCollapsed || !sel.rangeCount){hide();return;}
    const r=sel.getRangeAt(0);
    const container=n=>(n.nodeType===Node.ELEMENT_NODE?n:n.parentElement)?.closest('table.sc td.l');
    if(!container(r.startContainer)||!container(r.endContainer)){hide();return;}
    parts=[];
    for(const cell of cells()) {
      if(!r.intersectsNode(cell))continue;
      const local=document.createRange();local.selectNodeContents(cell);
      if(cell.contains(r.startContainer))local.setStart(r.startContainer,r.startOffset);
      if(cell.contains(r.endContainer))local.setEnd(r.endContainer,r.endOffset);
      const quote=local.toString();if(!quote.trim())continue;
      const pre=document.createRange();pre.selectNodeContents(cell);pre.setEnd(local.startContainer,local.startOffset);
      const start=pre.toString().length;
      const occurrence=cells().filter(c=>sceneFor(c)===sceneFor(cell)&&c.textContent===cell.textContent).indexOf(cell);
      parts.push({scene:sceneFor(cell),context:cell.textContent,quote,start,end:start+quote.length,occurrence});
    }
    if(!parts.length){hide();return;}
    const rect=r.getBoundingClientRect();toolbar.hidden=false;
    toolbar.style.left=Math.max(8,Math.min(rect.left,innerWidth-toolbar.offsetWidth-8))+'px';
    toolbar.style.top=Math.max(8,Math.min(rect.top-toolbar.offsetHeight-8,innerHeight-toolbar.offsetHeight-8))+'px';
  }
  toolbar.addEventListener('mousedown',e=>e.preventDefault());
  toolbar.addEventListener('click',e=>{const b=e.target.closest('[data-kind]');if(b&&parts.length)save({action:'add',kind:b.dataset.kind,parts});});
  document.addEventListener('selectionchange',()=>{if(!toolbar.contains(document.activeElement))select();});
  document.addEventListener('keydown',e=>{if(e.key==='Escape')hide();});
  document.addEventListener('scroll',hide,true);window.addEventListener('resize',hide);
  return {render(data,project,reload){hide();state={data,project,reload};paint();},clear(){hide();state=null;}};
})();
