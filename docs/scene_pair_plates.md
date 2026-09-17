# 场景正/反向双图（scene pair plates）— 实验记录

状态：**实验 v1**（2026-09-14，offer ep02 SCN-0006 神庙偏殿 grp003 首测通过）。与现行「白模 → 全景 → 母图」链（`docs/shot_plates.md`）和四向图实验（`docs/cardinal_plates.md`）并行，不接进 p6/p7 工作流、不改机检；产物落 spike 目录，不动 `assets/prompts` 与 `assets/clips`。

## 起因

offer ep02 grp003 第 6–7 s / 第 10 s、grp004 第 4.5–6 s 的背景被渲成了俯视布局图。根因：旧链路（2026-08-24）把 `directing/<ep>/blocking_maps/grpNNN.png`（俯视图叠动线）与整张 `grid_9views.png` 九宫格一起挂进 refs，只靠正文 `do not render the map` 与 `framed like tile N of [Image 5]` 约束，模型照样把俯视图当场景画。SCN-0006 没有白模与全景，HEAD 的母图链跑不起来，于是另起一条只靠 `layout.json` 的轻量链。

## 思路（用户定）

1. **正向图**：站在入口（门洞）内一步朝屋内看，12 mm 等效平视超广角，整间屋主体陈设一次入画。参考图只有一张标点俯视图（红点 + 视锥 + 方位字母）。
2. **反向图**：以正向图为母版（`[Image 1]` = 正向成图、`[Image 2]` = 标点图），站在屋子远端朝入口回望，补全正向图缺的那面墙；提示词逐面墙声明开口数（没开口的墙明说），门窗逐个给墙位/距离/画面横向百分比。
3. **组 prompt**：两张整图替换俯视动线图 + 九宫格，插在角色 sheet 之后；`Shot 1:` 前写 `Scene plates:` 段（两图各自站位、朝向、画内清单）；每个 `Shot k:` 段头写 `Scene plate: this shot uses [Image N] … and not [Image M]. Camera of this shot: standing at … looking … — <layout views[tile].desc_en>`；人物位置沿用原有逐字站位句；`Global constraints:` 追加禁俯视/地图/拼格。

## 代码

- `modules/scene_pair_plates.py` 规则与数据结构；`code/render_scene_pair_plates.py` CLI（用法见脚本 docstring）。
- 复用 `modules/shot_plates.py` 的罗盘/正则/图号重排、`modules/cardinal_plates.py` 的镜头视场与负面词、`modules/prompt_layout.paragraphize`。
- 站位缺省：正向 = 入口向看向点内推 0.6 m，看向离入口最远的 point 型地标；反向 = 正向轴线上离看向点退 1 m 回望入口。**两者都建议按场景手调**（见下方实测），`--front-at/--front-look/--reverse-at/--reverse-look` 接受归一化坐标或地标 id。
- 逐镜选图：镜的朝向取 `layout.json#views[tile].axis_bearing_deg`，tile 从 `shots/<id>/composition.json#view_tile_relation` 正则取（`--tiles` 覆盖）；取 |Δ| 最小的图；|Δ| > 60° 标 `weak`，Shot 句加「目标地标在图外，构图按文字」。
- 数据：`assets/concepts/scenes/<sid>/pair/{index.json, front.png/json/prompt.txt, reverse.png/json/prompt.txt, viewpoint_front.jpg, viewpoint_reverse.jpg}`；集索引 `directing/<ep>/pair_plates.json`；spike 产物 `qa/reports/scene_pair_spike/<grp>/{prompt.json, video_prompt_sent.txt, grpNNN.mp4, grpNNN.last_frame.png, meta.json, run.log, frames_sheet.png}`。

## 实测（offer SCN-0006 神庙偏殿，doubao-seedream-5-0-lite，2560x1440；Seedance 2.0 480p）

命令：
```
python code/render_scene_pair_plates.py --project offer --ep ep02 --scene SCN-0006 --front-look WUCHANG_IDOL --reverse-at 0.88,0.30 --seed 20260915
python code/render_scene_pair_plates.py --project offer --ep ep02 --scene SCN-0006 --write-prompt grp003
python code/render_scene_pair_plates.py --project offer --ep ep02 --scene SCN-0006 --run-video grp003
```

- **站位**：缺省看向点选到了最远的东窗，手动改看向无常像（= 布局图 tile 5 定场轴，朝向 104°）。反向缺省站在无常像前 1 m 朝西北北回望，两扇南窗与大鼓全落画外；改站到东北侧 `[0.88,0.30]`（东窗旁 0.7 m）朝西看门洞（267°），门洞居中、藤椅与南窗画左、大鼓画右、像台画右、无常像在镜旁不入画——反向图这才真正补上门窗那面墙。
- **正向图**第 1 版：大鼓画了两面、北墙靠门处多出一扇窗。加两句后第 2 版即过：`Exactly one of each, never duplicated: <点状陈设清单>`；`Wall by wall: the north wall has 0 openings — a solid wall with no door and no window; …`。
- **反向图**第 1 版：没有照抄正向构图（cardinal 实验里挂别向成图会照抄的风险**没有**出现，「同一间屋从对面回望、远变近、门洞居中」的措辞有效），材质与光线一致；但大鼓跑到画左、北墙多一扇窗、俯视图底边的两扇南窗被画成了**地面格栅**。加 `The floor is one continuous unbroken floor … the bright slots along the edges of the plan are windows standing in the walls` + 负面词 `floor grate, floor hatch, trapdoor…`，换 seed 第 2 版即过。
- **grp003 选图**：Shot 1/2（tile 4，233°）→ reverse Δ−35°；Shot 3（tile 1，127°）→ front Δ+24°；Shot 4（tile 7，197°）→ reverse Δ−70° weak。refs 8 张（3 角色 + 2 图 + 3 道具）。
- **成片**：11.1 s / 24 fps / 864×496 / 有音轨，切点 3 个（2.96 / 6.25 / 7.63 s）。四镜全部是平视偏殿内景：Shot 1/2 藤椅边、砖墙与门洞；Shot 3 无常像 + 像台 + 门洞 + 藤椅同框（门洞按机位本不该入画，但空间是同一间殿）；Shot 4 砖台边两扇南窗与条栅光斑。旧片第 6.5–7.5 s 的俯视布局图消失。weak 的 Shot 4 靠文字也守住了南窗构图。
- 费用：图 3 张（正向 2 版 + 反向 2 版 = 4 次生成）各 14,400 tokens；视频 110,902 tokens。

## 已知局限 / 下一步

- 两张图只覆盖两个朝向；朝正南/正北的镜（本场 tile 7、tile 6、tile 3）两张都偏 >60°，全靠文字。若某场景多数镜朝同一侧，可把正向轴改到那一侧（`--front-at/--front-look`），或加第三张。
- 分镜机位的判定只到「九格视角号」粒度（无白模）；有白模的场景应改从 `episode.json` 取真实机位朝向。
- 尚未与现行母图链比过同一组；grp004 未重跑。
- 组 prompt 的旧俯视句清理只覆盖 `Spatial layout / Map usage / Map markers / framed like tile / route on the map`；其它项目若有别的写法要补正则。

## 布局覆盖（2026-09-14 下午，用户口述布局 ≠ bible）

用户看图后指出偏殿的真实设想与 `layout.json` / `architecture.json` / `layout_top.png` 不一致（唯一一道门对室外、无常像在对门墙正中、藤椅在东南角挨炕、南墙窗下一条空砖炕、北墙空墙无神像台）。**不是提示词问题**，第一轮两张图忠实照 bible 画的。处理：

- 新增 `assets/concepts/scenes/<sid>/pair/layout_override.json`：可整体替换 `landmarks` / `views`，另给 `entrance_beyond`（门洞外看到什么）、`architecture_desc`（替换建筑描述）、`extra_rules`（追加句）。只影响本链路出图与选图，**不改 bible**；回写 bible 是用户另行决定的事（会牵动 34 份分镜卡的站位与屏侧）。
- 有覆盖时标点图不再叠旧 `layout_top.png`（它画的是旧陈设，会与文字打架），改叠在按地标现画的无文字示意平面图 `pair/plan_override.jpg` 上（墙、门窗缺口、家具色块、沿墙面状家具贴墙画）。
- 结果：正向图第 1 版北墙多一扇窗，换 seed 第 2 版即过；反向图一次过（门洞居中透出院子与天光、炕与南窗画左、大鼓画右、北墙空墙）。
- 注意：`--write-prompt` 生成的 spike prompt 里的 `Scene plates:` 段按当时的地标清单写，布局覆盖后要重新跑 `--write-prompt` 才会更新（本轮用户未要求重跑视频，未更新）。

## 新布局出片（2026-09-14 晚，grp003 + grp004）

覆盖文件再加两项：`shots{<shot_id>: {plate, camera_en, substitutions[]}}`（逐镜强制用图、机位句、只在该 Shot 段内做的措辞替换）与 `substitutions[]`（全文替换）、`global_constraints_extra`（追加进 Global constraints）。旧布局写死在分镜正文里的方位句（「facing north-east into the hall」「砖台边沿」「自西北角朝东南看,藤椅与角落高像同框」等）用它改成新布局口径。

- 用图：grp003 四镜全 front（sh009 看南墙炕与窗，front 里炕的椅端可见，强制 front）；grp004 sh010 front、sh011/sh012 reverse（看门洞方向，reverse 门洞居中、炕与窗在画左）。
- grp003 一次过：四镜全在新布局里（炕尽头藤椅、东墙正中高像、门洞透出院子、炕沿起弓两窗在身后），切点 3 个。
- grp004 第 1 版：末镜金光打到了东墙高像上、章墨没出画——原提示词根本没提无常像，新图把它放到了显眼位置。加 `global_constraints_extra`「东墙高像永不发光、金光只属于女童怀里的小像」+ sh011/sh012 机位句「高像在镜头旁侧、不入画」后第 2 版过：金光回到小像，章墨从炕沿跃起冲出门洞。
- 经验：换了场景图之后，凡是新图里显眼而原提示词没约束的物件（这里是高像），要补一句它「只是布景、不发光不动」，否则模型会把事件效果挪到它身上。

## grp012（2026-09-15，单镜 4 s 三区同框）

sh028 要在一镜里同框「门口滑坐的女童 / 殿心捡弓转身的章墨 / 藤椅上正对他背影的娘娘像」。旧布局藤椅挨着门，42 mm 横向就能同框；新布局门在西墙、藤椅在东南角是对角线两端，只能从东北角（大鼓旁）用 24 mm 朝西南看才装得下——覆盖里把镜头改成 24 mm 并重写机位句与三条站位句，用 reverse 图。

- 第 1 版：空间对（门在右、炕与窗在左、章墨捡弓转身、女童滑坐门口），**但藤椅是空的**，娘娘像没坐进去，东墙高像又冒到画左。
- 第 2 版（加「藤椅里必须坐着 [Image 3] 小像、画面里没有任何大型立像」）：模型改用了 front 图的构图（高像居中、炕在右、大鼓在左），女童也不在门口了，藤椅仍空——比第 1 版更差。
- 旧成片同样没做出这一拍（像是漂在藤椅上方的大像）。
- 结论：这一拍的难点不在场景参考，而是「远景里一尊小像坐在椅子里」这个信息在 480p 单镜三区同框里太小，两种场景图都撑不住；建议拆镜（先一镜殿心+门口，再切一镜藤椅上的像）或把机位拉近到炕尾，不要再靠加约束句重 roll。
- **拆镜版（2026-09-15，用户裁定）**：覆盖文件新增 `groups{<gid>: {shots[虚拟 shot id], shot_text}}`——`shots` 替换组的镜列表（tile 由 `shots[id].tile` 给），`shot_text` 整段替换正文里 `Shot 1:` 到 `Global constraints:` 之间的内容，逐镜 `Scene plate:` 句照常插入。sh028 拆为 sh028a（35 mm 自东朝西看门洞，门口 + 殿心，reverse）与 sh028b（50 mm 自殿心朝东南看炕尾藤椅，章墨后背作画右前景，front）。一次过：Shot 1 女童门口滑坐、章墨捡弓转身；Shot 2 像端坐藤椅含笑无裂痕、兔子在旁、炕与窗在画右。瑕疵：Shot 2 只有约 0.5 s（切点 3.58 s，计划 1.5 s）；章墨腕上的金珠被画成了金色腕带。

## B 路线：主视角一张 + 标注俯视图 · 与 A 路线对比（2026-09-16）

**做法**：`draw_blocking_plan()` 按当前布局现画示意俯视图（PIL，零生成）——色块 + 英文道具标签（door / kang / chair / idol / drum / S window / E window）+ 角色起点圆 / 终点方 + 字母 + 箭头 + 罗盘 N，角色坐标手写在 `layout_override.json#groups[<gid>].blocking[]`。refs = 角色 sheet + **主视角一张**（`groups[<gid>].main_plate`）+ 俯视图 + 道具（8/8/9）。`Scene plates:` 段两句：照片一句 + 「[Image M] is a schematic top-down floor plan … NOT a photograph and NOT a camera view … never render the plan」；每镜末尾加「character and prop positions follow the plan [Image M]」；看另一面的镜写「looks the other way from [Image N] … lay them out by the plan」；Global constraints 追加 `no diagram, no coloured blocks, no labels, letters or arrows from the plan`。其余正文与 A 路线逐字相同（diff 核过）。产物 `qa/reports/scene_pair_spike_map/<grp>/`，对照表 `compare_<grp>.png`（`--compare`）。

**打分**（✓ 对 / △ 勉强 / ✗ 错；A = 正反两张，B = 一张 + 俯视图；各 1 轮）

| 组 / 镜 | 俯视渗漏 | 道具位置对布局 | 道具跨镜漂移 | 人物站位 | 关键一拍 |
|---|---|---|---|---|---|
| grp003 S1–S2 椅旁 / 爬椅 | A✓ B✓ | A✓ B✓（窗在身后、椅在炕尾） | A✓ B✓ | A✓ B✓ | — |
| grp003 S3 高像+藤椅同框 | A✓ B✓ | A△（门洞入画）B△（东窗居中）| A✓ B✓ | A✓ B✓ | — |
| grp003 S4 炕沿起弓 | A✓ B✓ | A✓ B✓（两窗在身后） | A✓ B✓ | A✓ B✓ | A✓ B✓ |
| grp004 S1 椅中睡 | A✓ B✓ | A✓ B✓ | A✓ B✓ | A✓ B✓ | — |
| grp004 S2 跃起冲出门 | A✓ B✓ | A✓ B✓（炕在左、门居中、门外院子） | A✓ B✓ | A✓ B✓ | A✓ B✓ |
| grp004 S3 金光 | A✓ B✓ | **A✓（门在身后）B✗（机位跑到东侧，高像贴着藤椅入画）** | A✓ B△ | A✓ B✓ | 金光归小像 A✓ B✓ |
| grp012 S1 门口+殿心 | A✓ B✓ | A✓ B✓（门居中透院子） | A✓ B✓ | A✓（女童在门南侧=画左）B△（在门北侧） | — |
| grp012 S2 像坐椅里 | A✓ B✓ | A✓ B✓ | A✓ B✓ | A✓ B✓ | A✓ B✓ |

**结论**
- 俯视渗漏：两条路线 0 例。示意图版俯视图（色块 + 标签，非照片）没有像 8 月那张照片级动线图那样被当场景渲出来——渗漏的根因是「照片级俯视渲染」，不是「俯视图」本身。
- 道具位置 / 跨镜漂移：B 没有比 A 更稳；唯一的明显差异是 grp004 S3，B 把高像拉进了画面（俯视图上高像与藤椅相邻、且只有正向图可参考，模型把机位挪到了东侧）。A 因为有反向图，看门方向的镜有真图可依。
- 人物站位：两条路线都基本按文字/图走；B 的字母标记没有带来可见的额外约束（grp012 女童落在门的另一侧）。
- 代价：B 多占一张 refs（grp012 顶到 9），少了另一面的真图。
- **建议**：场景一致性以 A 路线（正反两张真图）为主；标注俯视图不作为场景参考，若要用，只在多人走位复杂的组作为「站位辅助」附加，且必须是示意图而非照片。单轮随机性大，此结论是趋势判断；若要定案，A/B 各补 1–2 轮。

## C 路线：正向 + 反向 + 标注俯视图（2026-09-16）

refs = 角色 + front + reverse + 标注俯视图 + 道具（9/9/9；grp012 超 10 张砍掉 PROP-0015 金丝珠子）。逐镜句与 A 完全相同（uses [Image N] and not [Image M]）+ 末尾「character and prop positions follow the plan [Image 6]」；`Scene plates:` 段 = A 的两图句 + 俯视图声明句。产物 `qa/reports/scene_pair_spike_c/`，三栏对比视频 `compare_ABC.mp4`（左 A 中 B 右 C）。

| 组 / 镜 | A | B | C |
|---|---|---|---|
| grp003 S1–S4 | 全 ✓ | 全 ✓ | 全 ✓（与 A 同质） |
| grp004 S2 跃起冲门（应看门，reverse） | ✓ 炕左门中 | ✓ 自己编出了门 | **✗ 机位跑到东侧**：高像在左、炕在右、章墨往画左跑，门不入画 |
| grp004 S3 金光（应看门，reverse） | ✓ 门在身后 | ✗ 高像贴椅入画 | **✗✗ 门在后景（对了），但椅旁多出一尊巨型送子娘娘像并且金光打在它身上**（违反 no second statue） |
| grp012 S1 门口 + 殿心（应看门，reverse） | ✓ 门居中透院子 | △ 门挤到画边 | **✗ 看向东墙**：高像在章墨身后、女童靠着炕坐，门不入画 |
| grp012 S2 像坐椅里（front） | ✓ | ✓ | **✗ 椅子里只有兔子和红布，像不见了** |
| 俯视渗漏 | 0 | 0 | 0 |

**结论**：C 不但没有叠加 A 与 B 的优点，反而是三条里最差的：三组里两组的「看门」镜都跑到了正向图那一面，还出现了巨型复制像与像消失。俯视图三次实验都没渗漏（示意图安全），但也三次都没提供可见的约束；把它和两张真图一起挂，反而稀释了逐镜「uses [Image N]」的指向。A 路线在三轮里 20 镜零走偏，是唯一稳定的。各 1 轮的样本下这个排序已经足够清楚：**A > B > C**。
