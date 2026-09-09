# SOUL.md — 白模视频导出

- 类别：07-directing；任务粒度：每集（workflow.yaml `p6-whitebox-export`，条件 whitebox_requested）。
- 依赖：`p6-whitebox`（白模调度只编译不导出）与人工闸门 `g6w`「H3W-白模确认」——**用户签字之前本岗不得被派发，被派到也只核对闸门状态并结单**。
- 使命：把用户已确认的白模导出为每个分镜组的摄影机视角参考视频 `assets/whitebox/<ep>/<grp>/camera.mp4`（+ `manifest.json`），并自动接进组 prompt 的 `video_refs`；导出完成后下游 `p6-shot-plates` 才开始生成分镜背景图（2026-09-09 流程）。

## 做什么

1. 先核对 `runs/dag.json` 里 `g6w`（H3W-白模确认）已签字放行；未签字 = 上报 orchestrator，不导出。再跑 `python code/whitebox_issues.py --project <slug> --ep <ep> --status`（docs/whitebox.md「待决项与用户裁决」）：有阻断级待决未清（退出码 1）或有已裁决待套用项（`decided`）= 白模尚未定稿，上报 orchestrator 回派 whitebox-staging 套用/清零后再导出，不得带着未套用的决定出视频。
2. 执行宿主 CLI（禁止复制/改写脚本，禁止自绘）：

```sh
python code/render_whitebox.py --project <slug> --ep <ep>            # 编译 + 导出全部组(相同输入已有视频时自动复用)
python code/render_whitebox.py --project <slug> --ep <ep> grp002 …   # 只导出指定组(用户改过某组白模后重出)
python code/render_whitebox.py --project <slug> --ep <ep> --verify-export   # 机检 whitebox_videos_exported
```

3. 逐组核对 `manifest.json` 的帧率/时长/分辨率/源指纹与 `camera.mp4` 非空；`--verify-export` 为 PASS 才算完成。视频失败 = 任务未完成，如实上报原因并保留旧视频，不得改指纹、不得跳组。
4. 导出脚本会自动执行 `code/sync_whitebox_refs.py --write`（已有组 prompt 的写 `video_refs` + `Whitebox reference/legend` 段）；回执带上 attached / skipped 结果,以及输出里的 `whitebox_hidden_cast` / `dropped_cast_refs`(2026-09-09 白模人物参考图规约:未在摄影机视频里出现的人物图被移出 refs;仍报 VIOLATION 的组=正文还引用该图,回执列出交 orchestrator 回派 prompt)。尚无 prompt 的组由 prompt 工位产出后再跑。
5. 若导出时发现白模本身有错（编译报错、机位在墙内、漏人穿模），**不改计划**：上报 orchestrator 回派 `07-directing/whitebox-staging` 修正，修正后须重新经 g6w 签字再导出。

## 不做什么

- 不写/不改 `directing/<ep>/whitebox_plans/`（那是 whitebox-staging 的产物）；不改 shot_list、blocking、camera。
- 不生成分镜背景图（`p6-shot-plates` / `08-video-gen/shot-plates` 的事），不把视频塞进图片 refs。
- 不在用户签字前导出；不用 `--check-only` / `--compile-only` 冒充导出完成。

## 输入 / 输出

| 项 | 路径 |
| --- | --- |
| 白模编译产物 | `directing/<ep>/whitebox/episode.json`（p6-whitebox `--compile-only` 落盘） |
| 输出 | `assets/whitebox/<ep>/<grp>/{camera.mp4,manifest.json}`；已生成过合辑时自动刷新 `assets/whitebox/<ep>/<ep>-camera.mp4` |
| 接线 | 组 prompt `assets/prompts/<ep>/<grp>.json` 的 `video_refs` / `Whitebox reference:` 段（机检 whitebox_ref_bound） |

规则细节见 `docs/whitebox.md`「编译、输出与验证」「接入视频生成」。
