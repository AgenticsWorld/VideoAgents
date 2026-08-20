# code/ — 仓库级通用工具

只放**项目无关、可参数化**的工具脚本;共享库放 `modules/`(genmedia / audiodsp / vc / deepagents_runner)。

| 文件 | 用途 |
|---|---|
| `_common.py` | 脚本共享 CLI:`--project`(缺省 `$VIDEOAGENTS_PROJECT` 或 demo)/ `--ep` / `--out-root`;import 副作用把 `modules/` 加入 `sys.path` |
| `blocking_bound_check.py` | 空间站位机检 blocking_bound(WORKFLOW.md §7A):组 prompt 每镜空间位置句与 blocking.json 各角色 `space_fragment_en` 逐字核对,`--project/--ep` 入参;prompt 批产出后全批跑,video-generation 开跑前单组复核 |
| `render_blocking_map.py` | 分镜组人物动线图渲染 + 机检(WORKFLOW.md Phase 4/6,2026-08-19):把 shot_list/storyboard 组级 `blocking_map`(逐角色起点/动线/终点,引用场景 `layout.json` 地标)叠加渲染到该场景俯视空间布局图 `layout_top.png` → `directing/epNN/blocking_maps/<grp>.png`(草案 `--source storyboard` 落 `draft/`);同时机检 `scene_layout_pack_ok`(`--scene <id> --check-only`:布局包三件套/像素/地标/九格)与 `blocking_map_present`(地标引用、route_en、同场景相邻组动线衔接),`--project/--ep` 入参 |
| `layout_map_bound_check.py` | 空间布局机检 layout_map_bound(WORKFLOW.md §7A):组 prompt refs 是否挂动线俯视图 + 场景 9 宫格图、含 Spatial layout 声明句、逐角色 `route_en` 逐字命中,`--project/--ep` 入参;prompt 批产出后全批跑,video-generation 开跑前单组复核 |
| `check_generation_groups.py` | 生成组机检 + 草案分组(WORKFLOW.md §7A 规则),`<shot_list.json>` 入参 |
| `check_av_sync.py` | 音画锁定同步机检 av_sync(audio-to-video 插件):母带零重编码(逐帧 md5)、画面精确铺满、累计漂移为 0;分 timeline/clips/final 三阶段共 15 项,timeline-planner 交付 `--stamp` 盖章,生成/剪辑开工只检,`--project/--ep/--require` 入参;共享库 `modules/avsync.py` |
| `dub_group.py` | 对白后期配音(WORKFLOW.md §8C,项目「输出设置→对白配音=后期配音」时的 p7-dub):组 clip 原生轨实测开口时段(silencedetect;`--detect-only`/`--segments` 手工覆盖)→ 按 casting.json 角色声线逐句 TTS 冻结版台词 → 语速/atempo 贴合开口时长 → 原生轨压低叠 TTS、画面流原样封装回组 clip(原生轨备份、重跑幂等);`--project/--ep/--group` 入参,`--dry-run`,视频原声模式自动拒跑 |
| `check_narration_sync.py` | 旁白挂点同步机检 narration_anchor_sync(WORKFLOW.md §8B):narration_track 与 shot_list.narration_anchors 指纹/逐段核对;narrator 交付 `--stamp` 盖章,mix/edit 开工只检,`--project/--ep` 入参 |
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
