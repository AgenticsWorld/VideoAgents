# code/ — 仓库级通用工具

只放**项目无关、可参数化**的工具脚本;共享库放 `modules/`(genmedia / audiodsp / vc / deepagents_runner)。

| 文件 | 用途 |
|---|---|
| `_common.py` | 脚本共享 CLI:`--project`(缺省 `$VIDEOAGENTS_PROJECT` 或 demo)/ `--ep` / `--out-root`;import 副作用把 `modules/` 加入 `sys.path` |
| `check_generation_groups.py` | 生成组机检 + 草案分组(WORKFLOW.md §7A 规则),`<shot_list.json>` 入参 |
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
