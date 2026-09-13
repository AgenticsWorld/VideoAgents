/* 右侧快速定位栏(剧本预览 / 故事板共用,2026-09-13;样式同分镜预览 #nav)
 * 页面结构:<div id="main"><div id="cwrap">…#content…</div><nav id="nav"></nav></div>
 * PreviewNav.render(title, items):items = [{id, cls:'sc'|'s', label, hint?, badges?}],顺序即文档顺序;
 * 点击用 PreviewStickbar.jumpTo 定位(让出置顶栏/表头);滚动时高亮视口顶端上方最近的锚点。 */
window.PreviewNav = (() => {
  const box = document.getElementById('content');
  const nav = document.getElementById('nav');
  const style = document.createElement('style');
  style.textContent = `
    #main{flex:1;display:flex;min-height:0}
    #nav{width:240px;flex:none;border-left:1px solid var(--border);background:var(--panel);overflow-y:auto;padding:8px 6px 20px}
    #nav .ttl{font-size:11px;color:var(--dim);padding:2px 8px 6px}
    #nav .nv{display:flex;align-items:center;gap:4px;width:100%;text-align:left;background:none;border:none;border-radius:6px;padding:3px 8px;font-size:12px;color:var(--text);cursor:pointer}
    #nav .nv .lb{flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    #nav .nv .bd{flex:none;font-size:10px;color:var(--dim);white-space:nowrap}
    #nav .nv:hover{background:var(--panel2)}
    #nav .nv.sc{font-weight:600;margin-top:6px;color:var(--accent)}
    #nav .nv.s{padding-left:24px;color:var(--text);font-size:11.5px}
    #nav .nv.on{background:var(--panel2);outline:1px solid var(--border)}
    @media(max-width:900px){#nav{display:none}}
  `;
  document.head.appendChild(style);
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  let items = [], frame = null;
  function render(title, list) {
    items = list || [];
    nav.innerHTML = items.length ? `<div class="ttl">${esc(title)}</div>` + items.map(n =>
      `<button class="nv ${n.cls}" data-t="${esc(n.id)}" title="${esc(n.label)}${n.hint ? ' · ' + esc(n.hint) : ''}${n.badges ? ' · ' + esc(n.badges) : ''}"><span class="lb">${esc(n.label)}</span>${n.badges ? `<span class="bd">${esc(n.badges)}</span>` : ''}</button>`).join('') : '';
    schedule();
  }
  function update() {
    frame = null;
    const threshold = box.getBoundingClientRect().top + 38 + (box.querySelector('table.sc thead')?.offsetHeight || 0) + 12;
    let cur = null;
    for (const n of items) {
      const el = document.getElementById(n.id);
      if (!el) continue;
      if (el.getBoundingClientRect().top <= threshold) cur = n.id; else break;
    }
    if (!cur && items.length) cur = items[0].id;
    nav.querySelectorAll('.nv').forEach(b => b.classList.toggle('on', b.dataset.t === cur));
    const on = nav.querySelector('.nv.on');
    if (on) on.scrollIntoView({block:'nearest'});
  }
  function schedule() { if (frame === null) frame = requestAnimationFrame(update); }
  nav.addEventListener('click', e => {
    const b = e.target.closest('.nv'); if (!b) return;
    const el = document.getElementById(b.dataset.t);
    if (el) window.PreviewStickbar ? PreviewStickbar.jumpTo(el) : el.scrollIntoView();
  });
  box.addEventListener('scroll', schedule, {passive:true});
  return {render};
})();
