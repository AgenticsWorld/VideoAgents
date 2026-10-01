/* 打印页(故事板 / 素材库共用,2026-10-01):把一张表格输出成单独的白底页面,并弹系统打印界面(打印或存 PDF)。
 * PrintPage.open({title, head, cols, rows, css, hint, lang}) → 'window' | 'iframe'
 *   title = 页面标题(也是存 PDF 的默认文件名);head = 页眉 HTML(放进 thead,每页重复;不传则只留列名行);
 *   cols  = [{label, cls}] 列名与 <col> 的 class(列宽由调用方 css 定);rows = <tr>… HTML;css = 调用方追加的样式;
 *   hint  = 屏幕上显示的一行提示(打印时隐藏)。
 * 浏览器开新标签页;桌面端(Electron 拒绝 window.open)或弹窗被拦时退回隐藏 iframe,只打印 iframe(返回 'iframe',调用方自行提示)。
 * 分页:整页一张表,thead 每页重复作页眉;tr break-inside:avoid 不让带图的行被分页切断;tr.sec(分节行)贴住下一行。
 * 页面自带脚本:load(图片全部加载完或失败)后把大图缩到 900px 宽并转 JPEG(PNG 原图逐张近 1 MB,整集存 PDF 会上百 MB),再 print()。 */
window.PrintPage = (() => {
  const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  const CSS = `
@page{margin:12mm 10mm}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-print-color-adjust:exact;print-color-adjust:exact}
body{background:#fff;color:#111;font:10.5pt/1.55 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.hint{color:#888;font-size:9pt;padding:0 0 3mm}
table{width:100%;border-collapse:collapse;table-layout:fixed}
thead{display:table-header-group}
th,td{border:1px solid #bbb;padding:2mm 2.5mm;vertical-align:top;text-align:left}
tr{break-inside:avoid;page-break-inside:avoid}
th.eph{border:0;border-bottom:1.5px solid #111;padding:0 0 2mm;font-size:13pt;font-weight:700}
th.eph .sub{font-weight:400;font-size:9.5pt;color:#444;margin-left:3mm}
th.col{background:#eee;font-size:9pt;font-weight:600;color:#333;padding:1mm 2.5mm}
tr.sec{break-after:avoid;page-break-after:avoid}
tr.sec td{background:#f4f4f4;font-size:10pt;padding:1.2mm 2.5mm}
.shot{width:100%;border:1px solid #ccc;display:flex;align-items:center;justify-content:center;overflow:hidden;color:#999;font-size:9pt;break-inside:avoid}
.shot img{width:100%;height:100%;object-fit:cover;display:block}
@media screen{body{max-width:210mm;margin:0 auto;padding:8mm 10mm 20mm}}
@media print{.hint{display:none}}
`;
  const JS = `addEventListener('load',async function(){
  for(const im of document.images){
    try{
      if(!im.naturalWidth||(im.naturalWidth<=900&&/\\.jpe?g(\\?|$)/i.test(im.src)))continue;
      const k=Math.min(1,900/im.naturalWidth),c=document.createElement('canvas');
      c.width=Math.round(im.naturalWidth*k);c.height=Math.round(im.naturalHeight*k);
      const x=c.getContext('2d');x.fillStyle='#fff';x.fillRect(0,0,c.width,c.height);x.drawImage(im,0,0,c.width,c.height);
      im.src=c.toDataURL('image/jpeg',.85);await im.decode();
    }catch(e){}
  }
  setTimeout(function(){focus();print()},200);
});`;
  // 新开页是 about:blank,图片地址一律写绝对
  const abs = u => new URL(u, location.href).href;
  function html(o) {
    const cols = o.cols || [];
    return `<!DOCTYPE html><html lang="${esc(o.lang || 'zh')}"><head><meta charset="utf-8"><title>${esc(o.title || '')}</title><style>${CSS}${o.css || ''}</style></head><body>`
      + (o.hint ? `<div class="hint">${esc(o.hint)}</div>` : '')
      + `<table><colgroup>${cols.map(c => `<col class="${esc(c.cls || '')}">`).join('')}</colgroup>`
      + `<thead>${o.head ? `<tr><th class="eph" colspan="${cols.length}">${o.head}</th></tr>` : ''}`
      + `<tr>${cols.map(c => `<th class="col">${esc(c.label)}</th>`).join('')}</tr></thead>`
      + `<tbody>${o.rows || ''}</tbody></table><script>${JS}<\/script></body></html>`;
  }
  function open(o) {
    const doc = html(o);
    let w = null; try { w = window.open('', '_blank'); } catch (_) {}
    if (w) { w.document.open(); w.document.write(doc); w.document.close(); return 'window'; }
    const old = document.getElementById('printframe'); if (old) old.remove();
    const f = document.createElement('iframe'); f.id = 'printframe';
    f.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden';
    f.onload = () => { try { f.contentWindow.onafterprint = () => f.remove(); } catch (_) {} };
    f.srcdoc = doc; document.body.appendChild(f);
    return 'iframe';
  }
  return {open, html, abs, esc};
})();
