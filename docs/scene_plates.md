# 场景图（正向 / 反向）· 白模关闭项目的场景一致性方案（2026-09-17）

适用：项目「输出设置 → 白模」（`output.spatial_blocking`）**关闭**的项目——除 Seedance 2.5 外其它视频模型的默认路径。白模开启的项目走白模视频 + 全景 + 分镜背景图那条链（`docs/whitebox.md`、`docs/shot_plates.md`），本方案的节点、脚本、机检在白模开启时一律不实例化 / 报 `skipped`，两条链互不触碰。

## 为什么

offer ep02 grp003/grp004 的俯视布局图被当成场景渲进成片（8 月旧链路：俯视动线图 + 九宫格整图进 refs）。2026-09-14～16 在 offer SCN-0006 上做了 A/B/C 三条路线各 1 轮的对比（记录见 `docs/scene_pair_plates.md`）：A = 正向 + 反向两张真实场景图，20 镜零机位走偏；B = 主视角一张 + 示意俯视图、C = 两张 + 示意俯视图，各出现两处以上「看另一面的镜跑到正向图那一面」，C 还出现巨型复制像。结论 A > B > C，俯视图（无论示意图）不进 refs。

## 做法

1. **正向图 front**：`06-art/environment-concept` 在 p4 出 `assets/concepts/scenes/<sid>/main_01.png`——站在入口（门口）往内看的主视角，整间主体陈设一次入画，无人，平视不倾斜；并登记 `scene_plates.json`：
   ```json
   {"schema_version": "scene_plates.v1", "scene_id": "SCN-0006", "mode": "inherit",
    "front": {"file": "main_01.png", "standing_en": "just inside the west doorway", "looking_en": "east across the hall toward the idol on the far wall",
              "in_frame_en": ["the tall idol at the middle of the east wall", "the brick kang beneath the two south windows", "the rattan armchair at the end of the kang"],
              "behind_en": ["the west doorway to the yard"]},
    "reverse": null}
   ```
   `reverse{standing_en, looking_en, in_frame_en}` 可由工位预填，出图脚本采用；不填则由 front 推出（站在远端、回望入口、必须画出 `front.behind_en` 里的开口）。
2. **反向图 reverse**：宿主 `code/render_scene_plates.py` 以正向图为 `--ref` 出 `reverse_01.png`（提示词写明「同一地点的反向视角、远变近、门洞居中、不得照抄构图」，门窗位置按 architecture 描述），写回登记并自动 `sync_scene_plates --write`。
3. **每镜用哪张**：storyboard / shot-planning 每镜写 `plate_view: front | reverse`（本镜看正向图那一面还是回望入口那一面；语义判定，不写坐标；拿不准写 front）。
4. **组 prompt 接线**：`code/sync_scene_plates.py --project <slug> --ep epNN --write`——剔除该场景旧概念图/俯视图/九宫格 refs，把正向（+本组有镜用到且已出的反向）图插在角色/生物 sheet 之后，`Shot 1:` 前写 `Scene plates:` 段（两图各自站位/看向/画内/身后清单 + 「每镜只用点名的那张、不得渲成空场、不得俯视」），每个 Shot 段头写 `Scene plate: this shot uses [Image N] (the front view, standing …, looking …) and not [Image M].`，`Global constraints:` 末尾追加禁俯视/地图句；幂等，原 prompt 首次备份到 `directing/<ep>/scene_plates_backups/`。机检 `scene_plate_bound`。

## 配置

| 层级 | 位置 | 取值 | 说明 |
|---|---|---|---|
| 项目 | `settings.json` → `output.scene_plates`（输出设置弹窗 / 新建向导「场景图」，白模开启时置灰） | `auto`（默认）/ `single` / `pair` | auto = 正向必出，任一集 shot_list 该场景有镜 `plate_view=reverse` 才出反向；single = 只出正向（平面动画、单面布景），标 reverse 的镜照用正向并 WARN；pair = 每场景两张，p4 一并出 |
| 场景 | `scene_plates.json#mode`（场景预览页「🖼 场景图」板块下拉，`POST /projects/<p>/scenes/<sid>/plates/mode`） | `inherit` / `single` / `pair` | 优先于项目级 |
| 分镜 | 无开关 | — | 系统按每镜 `plate_view` 选图；反向未出时暂用正向并 WARN |

## 流程位置（workflow.yaml）

`p6-env-concept`（每集开头，只为本集用到且库里还没有的场景出正向图 + 登记；pair 模式同时出反向；2026-09-30 前在 p4 全量出）→ `p6-shots`/`p6-camera`（每镜 plate_view）→ **`p6-scene-plates`**（`condition: scene_plates_requested` = 白模关闭；`06-art/environment-concept` 跑 `render_scene_plates.py --ep epNN`，机检 `scene_plates_complete` = `--status`）→ `g6` → `p7-prompt`（写完必跑 `sync_scene_plates.py --write`，机检 `scene_plate_bound`）→ video-generation 开跑前复核。

## 脚本

```
python3 code/render_scene_plates.py --project <slug> --ep ep01 --status        # 机检 scene_plates_complete
python3 code/render_scene_plates.py --project <slug> --ep ep01                 # 按模式补需要的反向图(+自动接线)
python3 code/render_scene_plates.py --project <slug> --scene SCN-0006 --force  # 重出该场景反向图(旧图移入 candidates/)
python3 code/sync_scene_plates.py --project <slug> --ep ep01 [grp…] [--write]  # 机检 / 回写 scene_plate_bound
```

模块 `modules/scene_plates.py`；单测 `tests/test_scene_plates.py`。

**反向图过期**：出反向图时把正向图 sha256 记进 `reverse.front_sha256`；正向图重出后三处都比对——`--status` 报违规（`stale_reverse`）、`render_scene_plates.py` 不用 `--force` 也会自动重出、`sync_scene_plates` 不挂过期的反向图并 WARN，场景预览页标「⚠ 过期」。反向图整个项目每场景只出一次（登记在场景目录、跨集复用），不随分镜/组/集重复生成。存量只有旧版 `main_01.png`、没有登记的场景按 legacy 兼容（照挂正向图，站位/画内清单为空，`--status` WARN 建议回派补登记）。

## 预览页

- 场景预览页：白模关闭时「🖼 场景图（正向 / 反向）」板块——两张图 + 站位/看向/画内清单、本场景模式下拉、反向需求原因与用到反向图的镜。
- 分镜预览页：每镜「🖼 场景图」缩略标注本镜用正向还是反向（`plate_view` 缺则标「按正向」）。

## 出图提示词要点（offer 实测得来，写进 render 脚本）

- 反向图必须写「[Image 1] 是同一地点的正向视角；本图站在远端回望入口；远变近；门洞居中」和「不得照抄其构图」——实测挂正向成图作参考没有触发照抄。
- 「每件陈设只有一件、不得重复」「无开口的墙明说」「地面完整无格栅」三句能挡住重复大鼓、多画窗、把平面图边缘画成地面格栅三类偏差。
- 换了场景图后，凡是新图里显眼而原提示词没约束的物件（如东墙高像），要在 Global constraints 补一句「只是布景、不发光不动」，否则事件效果会挪到它身上。
