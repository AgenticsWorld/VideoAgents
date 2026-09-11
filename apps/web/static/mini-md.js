/* 极简 Markdown → HTML(预览页展示 Agent 产出的 .md 文档用,如导演计划 directing_plan.md)
 * 用法:<script src="/static/mini-md.js"></script>  →  MiniMD.render(text) 返回已转义的 HTML 字符串
 * 支持:# 标题 / 段落 / 有序·无序列表(单层缩进) / > 引用 / ``` 代码块 / 管道表格(含表头分隔行、行内单元格) /
 *      水平线 / 行内 **粗体** *斜体* `代码` [链接](url) / 换行合并。不支持 HTML 直通(全部转义),不支持嵌套列表深层。
 * 没有引第三方库:桌面端离线运行,且这些文档只需要「能读」不需要完整规范。
 */
(function(){
  'use strict';
  var esc=function(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})};
  function inline(s){
    s=esc(s);
    s=s.replace(/`([^`]+)`/g,function(m,c){return '<code>'+c+'</code>'});
    s=s.replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>').replace(/__([^_]+)__/g,'<b>$1</b>');
    s=s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\*)/g,'$1<i>$2</i>');
    s=s.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+|\/[^)\s]*|#[^)\s]*)\)/g,'<a href="$2" target="_blank" rel="noopener">$1</a>');
    return s;
  }
  function isTableSep(l){return /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l)}
  function splitRow(l){
    l=l.trim();if(l.startsWith('|'))l=l.slice(1);if(l.endsWith('|'))l=l.slice(0,-1);
    var out=[],cur='',i;
    for(i=0;i<l.length;i++){var c=l[i];if(c==='\\'&&l[i+1]==='|'){cur+='|';i++}else if(c==='|'){out.push(cur);cur=''}else cur+=c}
    out.push(cur);return out.map(function(x){return x.trim()});
  }
  function render(text){
    var lines=String(text||'').replace(/\r\n?/g,'\n').split('\n'),out=[],i=0,n=lines.length;
    var para=[],list=null;  // list = {tag, items[]}
    function flushPara(){if(para.length){out.push('<p>'+inline(para.join(' '))+'</p>');para=[]}}
    function flushList(){if(list){out.push('<'+list.tag+'>'+list.items.map(function(x){return '<li>'+x+'</li>'}).join('')+'</'+list.tag+'>');list=null}}
    function flushAll(){flushPara();flushList()}
    while(i<n){
      var l=lines[i];
      if(/^\s*```/.test(l)){flushAll();var code=[];i++;while(i<n&&!/^\s*```/.test(lines[i])){code.push(lines[i]);i++}i++;out.push('<pre><code>'+esc(code.join('\n'))+'</code></pre>');continue}
      var m;
      if((m=/^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(l))){flushAll();out.push('<h'+m[1].length+'>'+inline(m[2])+'</h'+m[1].length+'>');i++;continue}
      if(/^\s*([-*_])(\s*\1){2,}\s*$/.test(l)){flushAll();out.push('<hr>');i++;continue}
      if(/^\s*\|/.test(l)&&i+1<n&&isTableSep(lines[i+1])){
        flushAll();var head=splitRow(l),rows=[];i+=2;
        while(i<n&&/^\s*\|/.test(lines[i])){rows.push(splitRow(lines[i]));i++}
        out.push('<div class="md-table"><table><thead><tr>'+head.map(function(c){return '<th>'+inline(c)+'</th>'}).join('')+'</tr></thead><tbody>'
          +rows.map(function(r){return '<tr>'+head.map(function(_,k){return '<td>'+inline(r[k]||'')+'</td>'}).join('')+'</tr>'}).join('')+'</tbody></table></div>');
        continue;
      }
      if(/^\s*>/.test(l)){flushAll();var q=[];while(i<n&&/^\s*>/.test(lines[i])){q.push(lines[i].replace(/^\s*>\s?/,''));i++}out.push('<blockquote>'+render(q.join('\n'))+'</blockquote>');continue}
      if((m=/^\s*([-*+]|\d+[.)])\s+(.*)$/.exec(l))){
        flushPara();var tag=/^\d/.test(m[1])?'ol':'ul';
        if(!list||list.tag!==tag){flushList();list={tag:tag,items:[]}}
        var item=m[2];i++;
        while(i<n&&/^\s{2,}\S/.test(lines[i])&&!/^\s*([-*+]|\d+[.)])\s+/.test(lines[i])){item+=' '+lines[i].trim();i++}
        list.items.push(inline(item));continue;
      }
      if(!l.trim()){flushAll();i++;continue}
      flushList();para.push(l.trim());i++;
    }
    flushAll();
    return out.join('\n');
  }
  window.MiniMD={render:render,inline:inline};
})();
