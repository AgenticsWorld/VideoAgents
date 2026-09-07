# code/ — 仓库级通用工具

只放**项目无关、可参数化**的工具脚本;共享库放 `modules/`(genmedia / audiodsp / vc / deepagents_runner)。

| 文件 | 用途 |
|---|---|
| `_common.py` | 脚本共享 CLI:`--project`(缺省 `$VIDEOAGENTS_PROJECT` 或 demo)/ `--ep` / `--out-root`;import 副作用把 `modules/` 加入 `sys.path` |
| `blocking_bound_check.py` | 空间站位机检 blocking_bound(WORKFLOW.md §7A):组 prompt 每镜空间位置句与 blocking.json 各角色 `space_fragment_en` 逐字核对,`--project/--ep` 入参;prompt 批产出后全批跑,video-generation 开跑前单组复核 |
| `blocking_map_check.py` | 分镜组人物动线数据机检(WORKFLOW.md Phase 4/6,2026-08-19;**2026-09-07 改版:原 `render_blocking_map.py` 去掉渲染只留机检**——不再把 blocking_map 叠加渲染成 `directing/epNN/blocking_maps/<grp>.png`,人物空间位置与动线由 3D 白模参考视频 `render_whitebox.py` 承担,组 refs 直接挂干净 `layout_top.png`):机检 `scene_layout_pack_ok`(`--scene <id>`:布局包三件套/像素/地标/九格)、`blocking_map_present`(地标引用、route_en、角色集合=characters_union、同场景相邻组动线衔接;草案 `--source storyboard`)、`label_ok`(label 为短规范名:非代词、无括号等说明性标点、≤8 字或 ≤3 英文词、全集同角色同词)与 `creature_blocking_ok`(2026-08-27:creatures_union 每个生物或作独立态条目 id `CRE-*` 或由骑手 `mounted`,两态不并存);**宿主 CLI,Agent 只准调用,禁止复制/改写到项目 code/**,`--project/--ep [grp…] --strict` 入参 |
| `performance_bound_check.py` | 表演证据层机检 performance_bound(WORKFLOW.md §7A,2026-08-26,仅对白组):组 prompt 每对白镜每说话角色的触发词在 `{}` 台词内且 `{}` 外再出现一次(绑定短语)、blocking `performance.end_state` 逐字命中、`forbidden_early` 不在绑定短语之前(WARN)、全文无 `AU\d` 编码;`--project/--ep [grp…] --strict` 入参;prompt 批产出后全批跑,video-generation 开跑前单组复核 |
| `check_dialogue_fit.py` | 对白时长适配机检 dialogue_fit + 精简目标清单(WORKFLOW.md §7D ①/①′,2026-08-30,p6-dialogue-fit 节点):按角色 `voice.json#speed_cpm` 中点重算每句估时,查 `dialogue_fit_group`(组 Σ台词估时 ≤ 组时长×0.7)、`dialogue_fit_shot`(Σ ≤ 镜长,>85% WARN)、`line_le_cap`(单句 ≤ shot_max_s×0.7)、`line_est_consistent`(记录估时=重算)、`lines_text_match_source`(shot_list 台词与 screenplay 对白层逐句一致);报告 `directing/epNN/dialogue_fit.json` 含超限组 `trim_targets[]`(需削减秒数、逐句 target_chars)供 dialogue-rewrite 精简;`--write-est` 重算并回写三处估时;无 shot_list 时退化为仅剧本层(Phase 5 line_duration_fits);`--project/--ep [grp…] --ratio --shot-ratio --strict` 入参;shot_list 定稿后必跑,PASS 前不派 blocking、不得发起 H3A |
| `refs_referenced_check.py` | 素材引用完备机检 refs_all_referenced / audioref_all_referenced / prop_ref_bound / tailframe_declared(WORKFLOW.md §7A,2026-08-27):组 prompt refs 每张图正文至少以 `[Image N]`/`@Image N` 引用一次、audio_refs 每段至少 `[Audio N]`/`@Audio N` 引用一次,道具图须 `<道具名>@Image N` 绑定句(只 `[Image N]` 为 WARN,`--strict` 为违规),refs 含前组尾帧时必有开场声明句,越界编号一并报;`--project/--ep [grp…] --strict` 入参;prompt 批产出后全批跑,video-generation 开跑前单组复核 |
| `sync_scene_cast.py` | 按同集 `scene_no + scene_id` 自动汇总在场人物；`--project/--ep [grp…] --write` 补齐组场次关联、已有 prompt 的身份/服装图与绑定，保留原 Image 序号；不带 `--write` 检查缺图/漏引。白模调度前、prompt 完成后执行，视频生成前复核。 |
| `sync_whitebox_refs.py` | 白模参考视频接线机检/回写 whitebox_ref_bound(2026-09-07,仅「人物精确空间位置」开启):按本组生效视频模型的参考视频预算把 `assets/whitebox/<ep>/<grp>/camera.mp4`(优先)与 `top.mp4` 写进组 prompt `video_refs`,`Shot 1:` 前插入固定段 `Whitebox reference:`(两路视频作用)+ `Whitebox legend:`(颜色↔人物取自 episode.json、眼睛鼻尖=朝向、摄像机盒+射线=镜头方向)并给 Global constraints 并入禁白模外观句;`--write` 幂等回写(render_whitebox.py 导出后自动调用),不带则纯机检;`--project/--ep [grp…]` 入参 |
| `layout_map_bound_check.py` | 空间布局机检 layout_map_bound(WORKFLOW.md §7A;2026-09-07 改版):组 prompt refs 是否挂该场景干净俯视图 `layout_top.png` + 场景 9 宫格图(已退役的 `blocking_maps/` 动线标注图不得再挂)、含 Spatial layout 声明句、Map usage 俯视图仅作空间位置参考句 + Global constraints no top-down or bird's-eye view(map_reference_only,2026-09-03)、主体定义句 `<label>@Image N` 与 blocking_map label 同词、逐角色 `route_en` 逐字命中,`--project/--ep` 入参;prompt 批产出后全批跑,video-generation 开跑前单组复核 |
| `check_generation_groups.py` | 生成组机检 + 草案分组(WORKFLOW.md §7A 规则),`<shot_list.json>` 入参 |
| `check_av_sync.py` | 音画锁定同步机检 av_sync(audio-to-video 插件):母带零重编码(逐帧 md5)、画面精确铺满、累计漂移为 0;分 timeline/clips/final 三阶段共 15 项,timeline-planner 交付 `--stamp` 盖章,生成/剪辑开工只检,`--project/--ep/--require` 入参;共享库 `modules/avsync.py` |
| `dub_group.py` | 对白后期配音(WORKFLOW.md §8C,项目「输出设置→对白配音=后期配音」时的 p7-dub):组 clip 原生轨实测开口时段(silencedetect;`--detect-only`/`--segments` 手工覆盖)→ 按 casting.json 角色声线逐句 TTS 冻结版台词 → 语速/atempo 贴合开口时长 → 原生轨压低叠 TTS、画面流原样封装回组 clip(原生轨备份、重跑幂等);`--project/--ep/--group` 入参,`--dry-run`,视频原声模式自动拒跑;TTS 火山渠道模型为 seed-audio-1.0(描述定制嗓音)时不传 casting 的 speaker 名,由 genmedia 按声纹卡描述 + 项目 voiceprint 样本参考锚逐句合成(同角色不漂音色) |
| `finalize_episode.py` | 终版封装宿主 CLI + 机检 intro_offset_ok(WORKFLOW.md §9B,2026-08-26):`probe` 实测各段时长写台账 `edit/epNN/final_layout.json`;`shift` 把 subtitles.srt/.ass 整体 +片头实测时长产 `subtitles_final.*`(无片头原样拷贝);`assemble` intro+正片画面+final_audio+outro+teaser 一次 concat 成 final.mp4(声轨随正片段拼接,片头偏移天然产生,禁 -shortest,自动 shift+check);`check` 成片时长=Σ各段、字幕逐条平移量、成片声轨 vs final_audio 互相关实测滞后=片头(±80ms,三窗一致);`--project/--ep [--cut/--audio/--final/--layout]` 入参;edit 封装后、platform-adapter 打包前必跑 |
| `check_narration_sync.py` | 旁白挂点同步机检 narration_anchor_sync(WORKFLOW.md §8B):narration_track 与 shot_list.narration_anchors 指纹/逐段核对;narrator 交付 `--stamp` 盖章,mix/edit 开工只检,`--project/--ep` 入参;项目旁白开关(output.narration_enabled)关闭时整体跳过(skipped: narration off) |
| `verify_episode_plan.py` | episode_plan.json 只读机检(事件覆盖/时长预算/ID 合法),`--project` 入参 |

## 项目制作脚本不放这里

Agent 为某项目写的一次性制作脚本(内嵌该项目的角色/镜头/打点等创作数据)是**项目产物**,
落 `data/projects/<slug>/code/` 并用 `python3 .version/vc.py register` 登记版本
(约定见 `agents/WORKFLOW.md` §2)。样例:demo 的 ep01 全套制作脚本在
`data/projects/demo/code/`。

项目内脚本定位仓库根的标准头(向上找 `modules/`,兼容任意深度):

```python
import sys as _sys
from pathlib import Path as _Path
_REPO = next(p for p in _Path(__file__).resolve().parents if (p / "modules").is_dir())
_sys.path[:0] = [str(_REPO / "modules"), str(_REPO / "code")]
```

白模空间/双视角参考视频：`python code/render_whitebox.py --project <slug> --ep ep01` 默认编译并自动保存 top.mp4、camera.mp4、manifest.json 至 `assets/whitebox/<ep>/<grp>/`。可追加组号，或用 `--scene <sid>` 更新引用该场景的所有组；完整且指纹/规格匹配的视频自动复用，`--force` 强制重出。`--check-only` 仅校验，不完成视频交付；旧 `--export` 保留兼容。尺度、关键帧与 Agent 分工见 [白模规约](../docs/whitebox.md)。
