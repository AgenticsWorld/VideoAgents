# 镜次时长契约（shot_timing_bound，2026-09-23）

## 问题

组级视频 prompt 的各 `Shot N:` 段从不带时长。出片时只有组 `total_duration_s` 以 `--duration` 传给 genmedia，
组内每镜多长全由模型自分。fengshen3 ep06 19 个组、143 镜全部如此，而 `shot_list.json` 每镜都有 `duration_s`。

## 各模型官方口径

| 生效模型 | 官方写法 | 本契约 |
|---|---|---|
| Seedance 2.5 | `0-5秒：…；5-10秒：…` 连续整数秒时间段；时间段是「事件预算」，官方明说不能精确到 0.5 秒，事件过密减段不细分 | 写，整数秒切段 |
| Wan 3.0 | `镜头N xx-xx秒`，用户给多少逐字保留 | 写，同 2.5 切段 |
| MiniMax H3 | `[Shot N] At MM:SS.mmm, …` 切点时间戳，首镜不写，严格递增 | 写，逐镜精确切点 |
| Seedance 2.0 | 官方指南：「模型对精确时间（如 0–3 秒）的支持不稳定，强行限制时长可能导致生成结果异常」 | **不写**，存量标签剔除 |
| 其他 / 未知 | — | skipped |

生效模型判定链与 `modules.shot_plates.group_is_v25 / group_is_h3` 一致：组级覆盖 → 集级 episode.json → 项目提示词技能快照 → genmedia 当前模型。

## 写法

**Seedance 2.5 / Wan 3.0**：按 shot_list 累计边界四舍五入到整数秒切段，边界没前进的亚秒快切镜并入相邻段；
标签写在该段**第一镜**的段头后，段内其余镜不带标签；自 0 起、首尾相接、末段止于 `round(total_duration_s)`。

```
Shot 1: 0-2秒：场景激活：…            ← 中文界面
Shot 5: 8-10秒（Shot 5–Shot 8）：…    ← 四个亚秒镜并成一段
Shot 1: 0-2s: …                       ← 非中文界面
Shot 5: 8-10s (Shot 5–Shot 8): …
```

**MiniMax H3**：Shot k（k≥2）段头后写 `At MM:SS.mmm,` = 前 k-1 镜 `duration_s` 之和。

```
[Shot 1] 固定机位大全景…
[Shot 2] At 00:06.000, 画面硬切到…
```

组总时长仍只由 `--duration` 传参，正文不写「生成 N 秒视频」。

## 工具

```
python3 code/sync_shot_timing.py --project <slug> --ep ep06 [grp…]          # 机检
python3 code/sync_shot_timing.py --project <slug> --ep ep06 [grp…] --write  # 回写 + 机检（幂等）
VIDEOAGENTS_UI_LANG=en …                                                  # 强制标签语言
```

`--write` 首次改动把原 prompt 备份到 `directing/<ep>/whitebox/prompt_backups/<grp>.json`（与其余 sync 共用）。
prompt 工位写完 prompt 后必跑一次 `--write`（SOUL.md 职责 2「镜次时长」条；运行时契约段同样注入）。
存量项目换模型后重跑 `--write` 即按新写法改写（2.5→2.0 会剔除标签，2.5→H3 会改成切点）。

## 机检（`shot_timing_bound`，workflow.yaml p7-prompt validation.auto）

- 该写的组：标签齐全、单位一致、自 0 起、首尾相接不重叠、末段止于 `round(total_duration_s)`；
  每段长度与覆盖各镜 `duration_s` 之和相差 ≤1s；覆盖多镜的段必标合并范围。
- H3 组：Shot k≥2 各有切点、= 累计时长（±0.06s）、严格递增、小于组时长；首镜无切点。
- Seedance 2.0 组：不得有任何秒数标签。
- 所有组：`total_duration_s` 与 Σ `duration_s` 相差 >0.5s 判 shot-planning 数据违规（退回 shot-planning）；
  Shot 段数 ≠ 镜数先修组结构。

实现：`modules/shot_timing.py`（纯函数 `plan_segments / apply_prompt / check_prompt` 有 `tests/test_shot_timing.py` 覆盖）。
