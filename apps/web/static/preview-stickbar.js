/* Shared current-scene bar for the board and screenplay previews. */
window.PreviewStickbar = (() => {
  const box = document.getElementById('content');
  const bar = document.getElementById('stickbar');
  const style = document.createElement('style');
  style.textContent = `
    #cwrap{flex:1;min-height:0;min-width:0;position:relative;display:flex;flex-direction:column}
    #content{min-height:0;--stick:0px}
    #stickbar{position:absolute;inset:0 0 auto;z-index:20;display:flex;align-items:center;gap:8px;padding:6px 22px;background:var(--panel);border-bottom:1px solid var(--border);box-shadow:0 3px 10px #0005;font-size:13.5px;min-height:38px;overflow-x:auto;white-space:nowrap}
    #stickbar[hidden]{display:none}
    #stickbar .stick-label{color:var(--accent);font-weight:600;padding:0;border:0;background:none;max-width:50vw;overflow:hidden;text-overflow:ellipsis;flex-shrink:0}
    #stickbar .stick-label:hover{text-decoration:underline}
    #stickbar .stick-id{color:var(--green);display:inline-flex;align-items:center;gap:6px}
    #stickbar .stick-copy{padding:0 5px;font-size:12px;flex:none}
  `;
  document.head.appendChild(style);
  const tr = text => window.t ? window.t(text) : text;
  let scenes = [], shots = [], frame = null, key = '';
  function copyButton(id) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'stick-copy';
    button.textContent = '⧉';
    button.title = button.ariaLabel = tr('复制编号') + ' · ' + id;
    button.onclick = async () => {
      let copied = false;
      try { await navigator.clipboard.writeText(id); copied = true; }
      catch (_) {
        const input = document.createElement('textarea');
        input.value = id;
        input.style.cssText = 'position:fixed;opacity:0';
        document.body.appendChild(input);
        input.select();
        try { copied = document.execCommand('copy'); } catch (_) {}
        input.remove();
        button.focus({preventScroll:true});
      }
      button.textContent = copied ? '✅' : '⚠';
      button.title = copied ? tr('已复制') : tr('复制失败');
      setTimeout(() => { button.textContent = '⧉'; button.title = button.ariaLabel; }, 1200);
    };
    return button;
  }
  function headerHeight() { return box.querySelector('table.sc thead')?.offsetHeight || 0; }
  function update() {
    frame = null;
    const top = box.getBoundingClientRect().top;
    const threshold = top + 38 + headerHeight();
    let scene = null, shot = null;
    for (const el of scenes) {
      if (el.getBoundingClientRect().top >= threshold) break;
      scene = el;
    }
    if (box.scrollTop <= 0) scene = null;
    if (scene?.classList.contains('scene')) {
      if (scene.getBoundingClientRect().bottom <= top + 38) scene = null;
      else for (const el of shots) {
        if (!scene.contains(el)) continue;
        const rect = el.getBoundingClientRect();
        if (rect.top <= threshold && rect.bottom > threshold) { shot = el; break; }
      }
    } else if (scene && box.querySelector('table.sc').getBoundingClientRect().bottom <= threshold) scene = null;
    const nextKey = scene ? JSON.stringify([scene.id, scene.dataset.stickLabel, shot?.dataset.stickShot]) : '';
    if (nextKey !== key) {
      key = nextKey;
      bar.replaceChildren();
      bar.hidden = !scene;
      if (scene) {
        const label = document.createElement('button');
        label.type = 'button';
        label.className = 'stick-label';
        label.textContent = '🎬 ' + scene.dataset.stickScene + ' ' + scene.dataset.stickLabel;
        label.title = label.textContent;
        label.onclick = () => jumpTo(scene);
        bar.append(label, copyButton(scene.dataset.stickScene));
        for (const id of shot ? JSON.parse(shot.dataset.stickShot) : []) {
          const item = document.createElement('span');
          item.className = 'stick-id';
          item.append(document.createTextNode('› ' + id), copyButton(id));
          bar.append(item);
        }
      }
    }
    box.style.setProperty('--stick', bar.hidden ? '0px' : bar.offsetHeight + 'px');
  }
  function schedule() { if (frame === null) frame = requestAnimationFrame(update); }
  function refresh() {
    const nextScenes = [...box.querySelectorAll('[data-stick-scene]')];
    if (nextScenes.length !== scenes.length || nextScenes.some((el, i) => el !== scenes[i])) key = null;
    scenes = nextScenes;
    shots = [...box.querySelectorAll('[data-stick-shot]')];
    schedule();
  }
  function jumpTo(el) {
    box.scrollTop += el.getBoundingClientRect().top - box.getBoundingClientRect().top - 38 - headerHeight() - 6;
    update();
  }
  new MutationObserver(refresh).observe(box, {childList:true, subtree:true});
  new ResizeObserver(schedule).observe(box);
  box.addEventListener('scroll', schedule, {passive:true});
  box.addEventListener('load', schedule, true);
  box.addEventListener('click', schedule);
  box.addEventListener('transitionend', schedule);
  refresh();
  return {jumpTo};
})();
