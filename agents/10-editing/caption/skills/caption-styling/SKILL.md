# SKILL.md — 花字风格与 HTML 模版编写(caption-styling)

> 花字 Agent 的设计手册。2026-08-14 起花字渲染引擎为 HTML+CSS(captions_html.py,
> libass 已退役):每个项目由你**定制一套花字模版**(自带动画的 HTML 文件),
> captions.json(schema v3)逐条引用模版 + 填参数。渲染细节归宿主 CLI,
> 本 skill 管"模版怎么写、样式怎么配"。

## 何时读我

- 每次接到花字**设计**工单(av2-caption / p9-caption),动笔前;
- 花字**烧录**工单(caption-render)开始前(第 0 步同步素材 + doctor)。

## 第 0 步:同步素材库 + 环境自检

```bash
python3 code/render_captions.py assets-sync
python3 code/render_captions.py doctor
```

- 素材库(字体/音效二进制)仍是独立 GitHub 仓库,同步落 `data/fonts|sfx/remote/`;
- doctor 检查 playwright/Chromium/fonttools;FAIL 时按提示装依赖,装不上就上报,
  **不要**试图退回 libass 或自写渲染;
- manifest 里连一套 CJK 字体都没有时先上报用户补字体,不拿系统宋体硬凑。

## 工作方式:内容驱动创作 + 项目视觉语言

**样式跟项目走,不存在全局预设库**(2026-08-14 用户裁定);**动画按内容逐条
创作,不要每条花字都套用同几个模版**(2026-08-17 用户裁定)。

1. **首个花字设计工单先定「项目视觉语言约定」**:读 `bible/style.json` + 抽看
   实际画面,定下本项目的色板、字体、描边体系、动画性格边界(写在
   `<项目>/edit/caption_templates/STYLE.md`)。全集视觉统一靠这份约定,
   **不靠压缩模版数量**。
2. **逐条设计,动画内容驱动**:每条花字的文案、字号、位置、动画都从内容出发——
   战报类砸入、感叹类弹跳、转折类翻滚、抒情类慢显……情绪/语义/画面不同就**写
   不同的动画代码**(新模版落 `caption_templates/`,遵守视觉语言约定 + 协议自检);
   只有内容性质真正重复(同类战报、同类吐槽)才复用已有模版换参数。
   模版是复用载体,不是默认答案;"这条内容该怎么动"永远先于"库里有什么"。
3. **成本护栏**:贴片按 (模版,文字,参数) hash 缓存,新模版只增加编写成本不增加
   渲染成本;全集模版数机检上限 20(防失控,不是目标);每个新模版必须过
   contact sheet 自检(渲 → 看 → 改)才入册。跨项目不搬运,新项目重新设计。

## 模版协议 captpl.v1(违反即机检 FAIL)

- 画布 1800×700 CSS px,基准字号 **150 CSS px**(引擎按 `em_pct` 换算
  device_scale_factor 渲染,合成端不缩放);
- 必须暴露 `window.seek(t)`:**幂等**地把所有样式设到 t 秒状态。禁止
  CSS animation/@keyframes、requestAnimationFrame、定时器、随机数——动画只能是
  t 的纯函数,这是确定性重渲的根基;
- 必须设 `window.__anchorEl = "<文字块元素 id>"`:合成端按它的 bbox 做锚点定位,
  **只包文字本体,不含装饰**(装饰可以出血,文字块不能越出画布);
- 字体一律写 `font://<font_id>`(如 `font://user:MaShanZheng`,id 来自
  data/fonts/manifest.json;用户在「参考文件」页上传到 `refs/fonts/` 的项目字体
  自动并入,id 为 `proj:<family>`,**有则优先使用**,`render_captions.py fonts-list
  --project <slug>` 可查)。**禁止**直接写系统字体名——缺字形会静默回退;
- 参数经 URL query 注入:`text`、`dur`(总时长秒)、`params`(JSON,模版自定义:
  segments 分段色/box 底衬色/glow 辉光色等);
- 入场/出场时长模版内定(入 0.25–0.55s、出 ≤0.4s);idle 段的非位移效果
  (扫光/辉光/星光)须无缝循环(正弦/相位式写法,任意 dur 都成立);
  **idle 期文字 transform 必须静止**(见规范第 5 条红线);
- 模版自包含:禁止 `http(s)://` 外部引用。

## 模版编写规范与坑清单(每条都是踩过的)

1. **描边宽度 ×2 换算**:CSS `-webkit-text-stroke` 骑在字形边上(一半在字内),
   视觉外扩 = 名义值一半;libass 直觉的数值会淡一半。名义值 = 目标视觉宽 × 2,
   多层描边逐层堆(back 深色宽 → mid 浅色窄 → front 填充)。
2. **渐变必须落在 span 上**:逐字 span 带 transform/will-change 会创建独立绘制
   上下文,父级 `background-clip: text` 对其失效(字会露出下层颜色)。
3. **逐字动画三层同步**:多层描边 = 同文本堆叠多层,逐字变换必须三层同一 span
   索引同步设置,否则描边和填充错位。
4. **字号对成片实测标定**:不同字体字面率不同(站酷黄油体 ≈0.8em),字号别信
   配置换算,渲一帧跟老成片/参考片比;宁大勿小(headline em_pct 15–21,
   keyword 12–16;字号偏小是"没冲击力"的第一大原因,两次返工教训)。
5. **"活"的写法与静止红线**:入场有物理感(spring 过冲回弹 + 方向性拖影 +
   逐字 stagger 0.04–0.08s);出场干脆(≤0.4s,比入场快);装饰(速度线/墨渍/
   印章/星光)与文字**独立时间轴**,错开 0.1–0.2s。**但 idle 期文字的位置/尺寸/
   角度必须完全静止**(2026-08-17 用户裁定:持续波浪晃动/摆动看着晕)——
   idle 的"活"只允许非位移效果:扫光 sweep、辉光强度呼吸、星光闪烁、装饰
   自身的小动画;任何作用在文字 transform 上的正弦循环都是违禁写法。
6. **装饰克制**:大面积色块贴在浅色画面上会糊成一团(墨渍 alpha ≤0.2);
   装饰服务文字,不抢主体。
7. **斜排默认禁用**:大字号下小角度倾斜读作"歪了"(2026-08-12 用户裁定),
   仅用户显式要求时用。
8. **自检命令**:模版写完先渲单条看 contact sheet,再跑
   `check_captions.py --require design`;烧录后 `--require render`。

## 最小教学范例(协议格式;完整参考:history 项目 caption_templates/)

```html
<!doctype html><html><head><meta charset="utf-8"><style>
  @font-face { font-family: "F"; src: url("font://user:MaShanZheng"); }
  html,body { margin:0; background:transparent; overflow:hidden; }
  #stage { position:relative; width:1800px; height:700px;
           display:flex; align-items:center; justify-content:center; }
  .txt { font-family:"F"; font-size:150px; white-space:nowrap;
         position:absolute; left:0; top:0; }
  #back span { -webkit-text-stroke:28px #333; color:#333; }   /* 视觉≈14px */
  #front span { color:#fff; }
  .txt span { display:inline-block; }
  #sizer { visibility:hidden; position:static; }
</style></head><body>
<div id="stage"><div id="group"><div id="tstack">
  <div class="txt" id="sizer"></div><div class="txt" id="back"></div>
  <div class="txt" id="front"></div>
</div></div></div>
<script>
const q = new URLSearchParams(location.search);
const TEXT = q.get("text") || "示例", DUR = parseFloat(q.get("dur") || "3");
const P = JSON.parse(q.get("params") || "{}");
for (const id of ["sizer","back","front"])
  document.getElementById(id).innerHTML = [...TEXT].map(c=>`<span>${c}</span>`).join("");
window.__anchorEl = "tstack";
const spring = t => t>=1 ? 1 : 1 - Math.exp(-7*t)*Math.cos(2.2*Math.PI*t)*1.2*(1-t);
const c01 = t => Math.max(0, Math.min(1, t));
window.seek = t => {                       // 幂等:样式是 t 的纯函数
  const L = ["back","front"].map(id => [...document.getElementById(id).children]);
  for (let i = 0; i < L[0].length; i++) {
    const p = c01((t - i*0.05) / 0.4);     // 逐字 stagger + spring 入场
    const dy = 70*(1 - spring(p));         // idle 期位置静止(红线);
    for (const l of L) { l[i].style.transform = `translateY(${dy}px)`;
                         l[i].style.opacity = p > 0 ? 1 : 0; }
  }
  const g = document.getElementById("group");
  const o = c01((t-(DUR-0.28))/0.28);      // 出场:快速淡出上移
  g.style.opacity = 1 - o*o*o; g.style.transform = `translateY(${-20*o*o*o}px)`;
};
seek(0);
</script></body></html>
```

## 用途类型与策略(2026-09-24)

- 每条花字的 `type` 取用途目录 `modules/caption_catalog.json`(35 个用途 / 四类),`tier` 写字号档
  (headline / keyword / label,缺省按类型推导,em_pct 按档机检);
- 动笔前跑 `python3 code/render_captions.py policy --project <slug> --ep epNN`:策略 auto = 按下表题材
  自选类型(不必每类都出);manual = 只准出用户勾选的类型(选中 = 允许,不 = 必出),不可用类型
  (av 项目 / 缺上游数据)也在输出里;
- 交付前 `render_captions.py policy --stamp` 把 `caption_policy` 盖进 captions.json(机检 `caption_policy_fresh`)。

## 题材 → 风格语言(设计模版时的方向,不再是预设名)

| 题材 | headline 语言 | keyword 语言 | 动画性格 | 音效族 | 密度 |
|---|---|---|---|---|---|
| **历史正剧/纪实** | 毛笔体+金橙/红渐变+多层描边,可配底线描画/印章/扫光 | 综艺粗体白字黑边白圈 | 砸入+震动 / 落笔回弹;出场冲出/闪白 | cinema 重音+刀剑金属;whoosh 前导 | 高:8–12s 一条,开场最密 |
| **悬疑/惊悚** | 细宋/楷+暗红或冷白+青蓝 glow | 冷白细体 | 慢 fade,禁弹跳 | 低频 boom/riser | 低:只标线索 |
| **都市/轻喜/综艺** | 得意黑/黄油体+多色分词+粗黑描边 | 同族+逐字底衬块 | 弹跳/翻滚,活泼 | pluck/pop/ding 高频轮换 | 最高:逐句情绪词 |
| **科幻/未来** | 细黑体+冷色渐变+强 glow | 细黑+glow | fade+zoom,禁毛笔体 | 电子 blip/glitch | 中 |
| **情感/文艺** | 楷书暖色,禁重描边 | 楷书小字号 | 双 fade,禁 smash | 风铃/轻 ding,再压 3dB | 低 |

## 通用纪律(任何题材)

1. **音效反单调三板斧**:headline 双层(whoosh 前导 offset≈-0.32 + 落点重音
   offset≈-0.03)、keyword 池轮换(相邻不重样)、逐条 `pitch` 0.9–1.1 微变;
2. **同屏 ≤2 条且错位**;不入底部字幕安全区(引擎在 0.82H 再兜底收拢一次);
3. 文案溯源(词典或母带原文)机检口径不变;segments 拼接必须 == text;
   **入出点 = 这段文字被念出的起止**(2026-08-18):设计前 `render_captions.py speech-align`,
   逐条 `speech-lookup --text` 取时间或写完 `speech-snap` 吸附;机检 `caption_speech_aligned`
   ±0.15s。入场动画从 start 起算,所以模版入场要快(≤0.3s 可读),别把入场拖到字念完;
4. cards 图卡 v3 暂不支持(默认不使用,需求出现时先上报);
5. 交付前:设计过 `check_captions.py --require design`,烧录过 `--require render`。

## 参考对标

风格靶子:`花字音效demo/后期.mp4`、`花字参考.mp4`(节奏与密度)、
`花字音效demo/剪映文字模版.mp4`(动画质感:方向性拖影、过冲回弹、idle 微动、
装饰独立动画——HTML 模版的动画品质以此为准)。
新题材没把握时,请用户提供 10–30s 参考片段,逐帧拆解后再设计风格系统。
