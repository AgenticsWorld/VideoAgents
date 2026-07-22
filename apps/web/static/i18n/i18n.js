/* 界面多语言运行时(所有页面共用)
 * 用法:<script src="/static/i18n/i18n.js"></script>(放 <head> 内,越早越好)
 * 词典:/static/i18n/<lang>.js,每语言一个文件,定义 window.I18N_DICT = {中文原文: 译文}
 * 原文(中文)即词典 key:词典缺失的条目自动回退显示中文,不会空白
 * 机制:DOM 文本节点/​title/placeholder/alt 属性按词典精确替换;以「:」「:」或空格结尾的
 *      key 作为前缀规则匹配动态拼接串;MutationObserver 覆盖 JS 异步渲染的内容
 * 语言选择:localStorage.webui_lang;首次打开按浏览器语言自动判断;控制台 ⚙️ 设置菜单可改
 */
(function () {
  'use strict';
  // 语言注册表:code -> 自称(选项本身不翻译)
  var LANGS = {
    en: 'English', zh: '中文', ja: '日本語', ko: '한국어', vi: 'Tiếng Việt',
    es: 'Español', fr: 'français', de: 'Deutsch', id: 'Indonesia',
    pt: 'Português', ru: 'русский', ar: 'عربي',
  };

  function detect() {
    var cands = navigator.languages || [navigator.language || 'en'];
    for (var i = 0; i < cands.length; i++) {
      var p = String(cands[i]).toLowerCase().split('-')[0];
      if (p === 'in') p = 'id';           // 旧版印尼语代码
      if (LANGS[p]) return p;
    }
    return 'en';
  }

  var lang = null;
  try { lang = localStorage.getItem('webui_lang'); } catch (e) { /* ignore */ }
  if (!LANGS[lang]) {                      // 仅第一次打开时按浏览器判断
    lang = detect();
    try { localStorage.setItem('webui_lang', lang); } catch (e) { /* ignore */ }
  }

  window.I18N_DICT = window.I18N_DICT || {};
  // 同步加载词典,保证首屏渲染前就绪(中文无需词典)
  if (lang !== 'zh') {
    document.write('<script src="/static/i18n/' + lang + '.js"><\/script>');
  }

  var PREFIXES = null;                     // 以:/:/空格结尾的 key,按长度降序
  function prefixes() {
    if (!PREFIXES) {
      PREFIXES = Object.keys(window.I18N_DICT).filter(function (k) {
        var c = k.charAt(k.length - 1);
        return c === ':' || c === ':' || c === ' ';
      }).sort(function (a, b) { return b.length - a.length; });
    }
    return PREFIXES;
  }

  // 翻译一段完整字符串:精确命中 > 前缀命中 > 原样返回
  function tr(s) {
    if (lang === 'zh' || !s) return s;
    var d = window.I18N_DICT;
    if (d[s] !== undefined) return d[s];
    var k = s.trim();
    if (!k) return s;
    var head = s.slice(0, s.indexOf(k)), tail = s.slice(s.indexOf(k) + k.length);
    if (d[k] !== undefined) return head + d[k] + tail;
    var ps = prefixes();
    for (var i = 0; i < ps.length; i++) {
      if (k.indexOf(ps[i]) === 0) return head + d[ps[i]] + k.slice(ps[i].length) + tail;
    }
    return s;
  }

  // 参数化翻译:I18N.f('第 {a}/{b} 步', {a:1, b:5})
  function f(key, params) {
    var v = tr(key);
    for (var k in params) v = v.split('{' + k + '}').join(params[k]);
    return v;
  }

  var ATTRS = ['title', 'placeholder', 'alt'];
  var SKIP = { SCRIPT: 1, STYLE: 1, TEXTAREA: 0 };   // textarea 值不动,placeholder 照译

  function inSkipped(el) {
    for (var n = el; n; n = n.parentElement) {
      if (n.id === 'soul-view') return true;         // SOUL.md 原文不翻译
      if (n.hasAttribute && n.hasAttribute('data-no-i18n')) return true;
    }
    return false;
  }

  function trText(node) {
    var v = node.nodeValue;
    if (!v || !/[一-鿿]/.test(v)) return;
    var p = node.parentElement;
    if (p && (SKIP[p.tagName] || inSkipped(p))) return;
    var nv = tr(v);
    if (nv !== v) node.nodeValue = nv;
  }

  function trAttr(el, name) {
    if (!el.getAttribute) return;
    var v = el.getAttribute(name);
    if (!v || !/[一-鿿]/.test(v) || inSkipped(el)) return;
    var nv = tr(v);
    if (nv !== v) el.setAttribute(name, nv);
  }

  function apply(root) {
    if (lang === 'zh') return;
    if (root.nodeType === 3) { trText(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 9) return;
    var w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (var n = w.nextNode(); n; n = w.nextNode()) trText(n);
    var els = root.querySelectorAll ? root.querySelectorAll('[title],[placeholder],[alt]') : [];
    for (var i = 0; i < els.length; i++) {
      for (var j = 0; j < ATTRS.length; j++) trAttr(els[i], ATTRS[j]);
    }
    if (root.nodeType === 1) for (j = 0; j < ATTRS.length; j++) trAttr(root, ATTRS[j]);
  }

  function start() {
    document.documentElement.lang = lang;
    if (lang === 'ar') document.documentElement.dir = 'rtl';
    if (lang === 'zh') return;
    apply(document.documentElement);
    if (document.title) { var t2 = tr(document.title); if (t2 !== document.title) document.title = t2; }
    new MutationObserver(function (muts) {
      for (var i = 0; i < muts.length; i++) {
        var m = muts[i];
        if (m.type === 'characterData') trText(m.target);
        else if (m.type === 'attributes') trAttr(m.target, m.attributeName);
        else for (var j = 0; j < m.addedNodes.length; j++) apply(m.addedNodes[j]);
      }
    }).observe(document.documentElement, {
      subtree: true, childList: true, characterData: true,
      attributes: true, attributeFilter: ATTRS,
    });
    // 原生弹窗(alert/confirm)也走词典
    var _alert = window.alert, _confirm = window.confirm;
    window.alert = function (m) { return _alert(tr(String(m))); };
    window.confirm = function (m) { return _confirm(tr(String(m))); };
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', start);
  } else { start(); }

  // 打开控制台时把界面语言同步到服务端(agent 对话/汇报语言跟随):
  // 服务端未设置或与本地不一致(如曾被其他浏览器的首访自动判定写成别的语言)都以本地为准写回,
  // 保证用户看到的界面语言 = agent 汇报语言
  if (location.pathname === '/') {
    fetch('/api/v1/config/generation').then(function (r) { return r.json(); }).then(function (cfg) {
      if (cfg.ui_language !== lang) {
        var project = 'demo';
        try { project = JSON.parse(localStorage.getItem('webui_prefs') || '{}').project || 'demo'; } catch (e) { /* ignore */ }
        return fetch('/api/v1/config/generation', {
          method: 'PATCH', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ui_language: lang, project: project }),
        });
      }
    }).catch(function () { /* ignore */ });
  }

  window.I18N = {
    lang: lang, langs: LANGS, t: tr, f: f,
    // 用户在设置菜单显式切换语言:本地保存 + 同步服务端 + 刷新页面
    setLang: function (code, project) {
      if (!LANGS[code] || code === lang) return Promise.resolve();
      try { localStorage.setItem('webui_lang', code); } catch (e) { /* ignore */ }
      return fetch('/api/v1/config/generation', {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ui_language: code, project: project || 'demo' }),
      }).catch(function () { /* 服务端不可达也允许本地切换 */ })
        .then(function () { location.reload(); });
    },
  };
  window.t = window.t || tr;               // 页面脚本可直接用 t('…')
})();
