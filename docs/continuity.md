# 长镜头自动续接

开启项目 `settings.json` 的 `duration.long_take` 后，连戏规划在
`directing/epNN/continuity.json`（兼容 `continuity_plan.json`）的 `group_transitions` 中指定：

- `boundary_type: "continuous"`：同一动作或运镜跨组，首镜不切镜；优先向后续写前组最后 2–3 秒。
- `boundary_type: "cut"`：反打、换机位等切镜连戏，使用前组尾帧参考图。旧数据缺省按 cut 处理，不能仅根据开关猜测镜头意图。
- `anchor: "none"`：明确断点。跨场景、叠化/淡入淡出等转场始终不续接。

`anchor: last_frame` 保留为兼容旧规划的续接意图；实际选择在组 prompt 的 `continuity_ref.mode`
（`none` / `last_frame` / `tail_video`）中记录。图片继续存入 `refs`，尾段存入 `video_refs`，不混用数组。

## 执行顺序

1. 连戏规划标明 boundary_type；连续边界核对姿态、道具、机位、运动方向和速度，不写换构图切镜开场。
2. prompt 工位写好整组 prompt 后，执行 `python3 code/sync_continuity_refs.py --project <slug> --ep epNN --write`。
   此时允许前组视频不存在，保留续接依赖和参考路径，不能因此删掉引用。
3. video-generation 按链串行，前组交付后执行
   `python3 code/sync_continuity_refs.py --project <slug> --ep epNN grpNNN --prepare`。
   重新读取组 JSON，按原序传入更新后的 `video_prompt`、`refs`、`video_refs`、`audio_refs`。
4. 运行既有引用/白模检查；`sync_continuity_refs.py` 不带写入参数可检查规划是否已同步。
   `genmedia video` 正式生成及 dry-run 都在提交前拦截未准备、漏传、乱序或前组重生成后的过期输入。

## 素材和预算

尾段写入 `assets/continuity/epNN/<prev>.continuation.mp4`，与正片目录分离，避免被误选作成片。
分析和缓存包含源视频、切点 meta 的大小/修改时间指纹。原视频不修改；首次更新 prompt 的备份在
`directing/epNN/continuity/prompt_backups/`。尾帧继续保存在原有 clips 目录供预览和兜底。

尾段通过 FFmpeg 解码重编码截取，最长 3 秒，保留原分辨率，不做超分；去掉音轨，避免重复旧对白。
实际切点使用 meta 的 `boundaries_s` 和尾部场景变化/黑场检测共同约束。末尾连续区间不足 2 秒回退尾帧；
实际结尾为黑场时阻止续接，要求修复前组或明确转场断点，不能将黑帧作兜底图。
缺视频则等待，解码/探测失败则报错，不假装准备成功。场景检测为启发式，不能代替接缝目检。

视频参考按当前组模型/渠道能力选择。摄影机白模及其他已有参考优先保留，续接占用 1 个视频名额和
实际尾段时长；预算只够2秒时缩短尾段，连2秒都放不下才回退尾帧。可选俯视白模最后分配，默认仍关闭。图片与音频保留，硬上限仍由
genmedia 校验；回退后图片超限必须解决，不可静默删人物图。仅支持首尾帧、完全不接受参考素材的端点
（例如当前 Fal Kling / H3 Max Turbo）仍需既有拆段首尾帧兜底，不能把多参考图请求直接提交给这些端点。

尾段声明明确要求向后延长而非照抄参考；角色图定义身份，白模指导后续站位，不覆盖边界画面。
声音由本组对白/声音指令生成。Seedance 2.5 延长任务通过既有适配器处理自适应画幅。

## 重生成与验收

依赖判据必须同时检查前组尾帧图片、前组 `.continuation.mp4` 和 `continuity_ref.from_group`。
不能再用“refs 没有尾帧图”判断独立组。重生成前组后，后组必须重新 prepare；已经生成的后组需复查接缝，
不能只换参考路径就宣称旧成片已连续。跨组动作断续、回放旧动作、黑帧、人物/道具漂移均应复检。
视频参考与尾帧软引用都不保证像素级无缝；低清素材反复续接仍可能累积漂移。

关闭开关后运行同步命令清理自动续接素材及声明，保留普通人物/场景/白模参考，解除续接链。
