/* 预览页顶部「图像模型」下拉(2026-09-11):故事板页草图模型与场景/人物/生物/道具预览页共用。
 * 用法:<script src="/static/image-models.js"></script><script src="/static/image-model-picker.js"></script>
 *      const P=mountImageModelPicker({kind:'scenes', host:'#imp', label:'🎨 图像模型', title:'…'});
 *      P.value() → {provider, model} 或 {}(跟随全局);P.reload() 重新拉取。
 *      compact:true(场景页两块并排用):渠道项只写「跟随全局」、模型名截到 20 字、下拉最宽 150px,完整名放 title。
 * 数据:GET/POST /api/v1/config/image-model/<kind>(kind: sketch|scenes|characters|creatures|props),
 *      存服务端 state.json image_model_prefs[kind],与全局「生成模型」设置分开;空渠道 = 跟随全局。
 * 出图侧:genmedia 按输出目录 assets/concepts/<kind>/ 自动套用该类别的选择(草图由 storyboard_sketch.py 显式传)。
 * 模型清单 IMAGE_MODEL_LISTS 来自 image-models.js(与「生成模型」页共用)。
 * ComfyUI(2026-09-13):二级下拉不是模型而是运行方式(本地/云端/RunningHub 国内/国际),清单与已配置标记由
 *      接口 channels[].modes 给出,值仍走 model 槽(local|cloud|rh_cn|rh_ai),未配置的方式置灰。
 * Agentics(2026-09-18):图像分文生图/图生图两个 profile,均在「生成模型」页设置,出图时按有无参考图自动选;
 *      二级下拉只有「跟随全局」一项(model 存空),title 列出两个 profile(channels[].t2i / i2i)。 */
(function(){
  'use strict';
  const PROV_NAMES={agentics:'Agentics',openrouter:'OpenRouter',volcengine:'火山引擎',byteplus:'BytePlus',fal:'Fal',minimax:'MiniMax',comfyui:'ComfyUI'};
  const COMFY_MODE_NAMES={local:'本地',cloud:'云端(Comfy Cloud)',rh_cn:'RunningHub 国内(.cn)',rh_ai:'RunningHub 国际(.ai)'};
  const tt=s=>(window.t?window.t(s):s);
  const ff=(s,p)=>(window.I18N&&window.I18N.f?window.I18N.f(s,p):s);
  const clip=(s,n)=>{s=String(s||'').replace(/\s+/g,' ');return s.length>n?s.slice(0,n)+'…':s};
  function ensureStyle(){
    if(document.getElementById('imp-style'))return;
    const st=document.createElement('style');st.id='imp-style';
    st.textContent='.imp{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;color:var(--dim,#889);white-space:nowrap}'
      +'.imp select,.imp input{background:var(--panel2,#1c1f26);color:var(--text,#e6e8ee);border:1px solid var(--border,#2a2f3a);border-radius:6px;padding:5px 8px;font-size:13px;max-width:240px}'
      +'.imp select{cursor:pointer}.imp select:hover{border-color:var(--accent,#5b8cff)}.imp select:disabled{opacity:.5}'
      +'.imp.compact{gap:4px}.imp.compact select,.imp.compact input{max-width:150px;padding:4px 6px;font-size:12px}';
    document.head.appendChild(st);
  }
  window.mountImageModelPicker=function(opt){
    const kind=opt.kind;
    const host=typeof opt.host==='string'?document.querySelector(opt.host):opt.host;
    if(!host)return {value:()=>({}),reload:()=>Promise.resolve()};
    ensureStyle();
    host.classList.add('imp');if(opt.compact)host.classList.add('compact');host.innerHTML='';
    const clipN=opt.compact?20:46;
    if(opt.title)host.title=opt.title;
    const lab=document.createElement('span');lab.textContent=tt(opt.label||'🎨 图像模型');
    const sp=document.createElement('select'),sm=document.createElement('select'),ci=document.createElement('input');
    ci.placeholder=tt('自定义模型 id');ci.hidden=true;sm.hidden=true;
    host.append(lab,sp,sm,ci);
    let D={channels:[],global:{},provider:'',model:''};
    const effProv=()=>sp.value||D.global.provider||'';
    function value(){
      const p=effProv(),m=sm.value==='__custom__'?ci.value.trim():sm.value;
      if(!sp.value&&(!m||m===D.global.model))return {};      // 跟随全局且模型没改:不传,按全局
      return p?{provider:p,model:m}:{};
    }
    function fillModels(model){
      const p=effProv();sm.innerHTML='';
      const ch=(D.channels||[]).find(c=>c.id===p)||{};
      if(!p){sm.hidden=true;ci.hidden=true;return}
      if(p==='agentics'){  // 二级只有「跟随全局」:文生图/图生图 profile 按「生成模型」页设置,出图时按有无参考图自动选
        ci.hidden=true;sm.hidden=false;
        const o=document.createElement('option');o.value='';o.textContent=tt('跟随全局');
        o.title=ff('文生图 {t} · 图生图 {i}',{t:ch.t2i||'—',i:ch.i2i||'—'});sm.appendChild(o);sm.value='';sm.title=o.title;
        return;
      }
      sm.title='';
      if(p==='comfyui'){   // 二级 = 运行方式;默认 = 「生成模型」页保存的运行方式(ch.model)
        ci.hidden=true;sm.hidden=false;
        const modes=(ch.modes&&ch.modes.length)?ch.modes:Object.keys(COMFY_MODE_NAMES).map(id=>({id,configured:true}));
        modes.forEach(m=>{const o=document.createElement('option');o.value=m.id;
          o.textContent=tt(COMFY_MODE_NAMES[m.id]||m.label||m.id)+(m.configured?'':' ('+tt('未配置')+')');o.disabled=!m.configured;sm.appendChild(o)});
        const want=(model&&modes.some(m=>m.id===model))?model:(ch.model||'local');
        sm.value=modes.some(m=>m.id===want&&m.configured)?want:((modes.find(m=>m.configured)||{}).id||want);
        return;
      }
      sm.hidden=false;
      const list=((window.IMAGE_MODEL_LISTS||{})[p]||[]).slice();
      if(ch.model&&!list.some(x=>x[0]===ch.model))list.unshift([ch.model,ch.model+' ('+tt('当前全局')+')']);
      if(!model)model=ch.model||(list[0]||[''])[0];
      list.forEach(([id,label])=>{const o=document.createElement('option');o.value=id;o.textContent=clip(label||id,clipN);o.title=id;sm.appendChild(o)});
      const oc=document.createElement('option');oc.value='__custom__';oc.textContent=tt('自定义…');sm.appendChild(oc);
      if(list.some(x=>x[0]===model))sm.value=model;else{sm.value='__custom__';ci.value=model}
      ci.hidden=sm.value!=='__custom__';
    }
    function render(){
      sp.innerHTML='';
      const g=D.global||{};
      const o0=document.createElement('option');o0.value='';
      const gm=g.provider==='comfyui'?tt(COMFY_MODE_NAMES[g.model]||g.model||'—'):(g.model||'—');
      const gfull=ff('跟随全局({p} · {m})',{p:PROV_NAMES[g.provider]||g.provider||'—',m:clip(gm,28)});
      o0.textContent=opt.compact?tt('跟随全局'):gfull;o0.title=gfull;sp.appendChild(o0);
      if(opt.compact)sp.title=gfull;
      (D.channels||[]).forEach(c=>{const o=document.createElement('option');o.value=c.id;
        o.textContent=(PROV_NAMES[c.id]||c.id)+(c.configured?'':' ('+tt('未配置')+')');o.disabled=!c.configured;sp.appendChild(o)});
      sp.value=(D.channels||[]).some(c=>c.id===D.provider&&c.configured)?D.provider:'';
      fillModels(D.model||'');
    }
    async function reload(){
      try{
        const r=await fetch('/api/v1/config/image-model/'+encodeURIComponent(kind));
        if(!r.ok)throw new Error(r.status);
        const j=await r.json();
        D={channels:j.channels||[],global:j.global||{},provider:j.provider||'',model:j.model||''};
      }catch(_){D={channels:[],global:{},provider:'',model:''}}
      render();
    }
    async function save(){
      const v=value();
      D.provider=v.provider||'';D.model=v.model||'';
      try{await fetch('/api/v1/config/image-model/'+encodeURIComponent(kind),{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({provider:v.provider||'',model:v.model||''})});}catch(_){}
      if(opt.onChange)opt.onChange(v);
    }
    sp.onchange=()=>{fillModels('');save()};
    sm.onchange=()=>{
      // 跟随全局状态下换了模型:渠道落到全局渠道并保存,渠道下拉同步显示
      if(!sp.value&&sm.value!==(D.global||{}).model)sp.value=effProv();
      ci.hidden=sm.value!=='__custom__';
      if(sm.value!=='__custom__')save();else ci.focus();
    };
    ci.onchange=save;
    reload();
    return {value,reload};
  };
})();
