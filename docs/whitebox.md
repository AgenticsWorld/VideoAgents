# Three.js 白模空间与参考视频

场景预览放在图片之后，尺寸上限为图片预览的两倍（760×460 CSS px）；分镜预览每组的「🧊白模」同时播放空间视角和镜头视角，可拖动时间。两个页面的空间视角默认俯视图，可切换到旋转视角后拖动、缩放；摄像机视口保持实际机位。预览不再提供“导出 MP4”按钮，视频由建模流程自动保存。两个白模视口均为原210px高度的1.5倍，即315px高，宽度按项目画幅同比放大；容器较窄时等比缩小或换行。统一使用本地 Three.js 0.180.0，不依赖 CDN；预览与视频导出共用同一个渲染器。参考视频只有 camera.mp4（最终机位白模；2026-09-08 起白模只导出摄影机视角 camera.mp4,不再导出俯视 top.mp4(俯视仅在预览页交互查看)，旧项目目录里残留的 top.mp4 重出时自动删除、不再挂参考）。

## 系统与项目职责

系统拥有 `modules/whitebox.py` 编译和校验、`whitebox-renderer.js` 渲染、`modules/whitebox_export.py` 导出，以及 API/预览界面。项目 Agent 只拥有场景资产和调度计划，不复制宿主工具。

运行链：environment-concept 布局包 → **05-scenes/scene-modeling** 场景白模 → shot-planning + blocking + camera-movement + continuity-planning → **07-directing/whitebox-staging** 数值时间线和参考视频 → 分镜确认/视频生成。

项目「输出设置 → 人物精确空间位置」开启（默认开）时，orchestrator 在有布局包后自动添加上述工单（workflow.yaml `whitebox_requested` = 该开关，用户工单单独要求白模参考视频时同此），场景工单以 SCN-ID 为粒度，调度工单以 ep 为粒度，逐组按依赖先后执行。已有项目可直接编译，不修改其分镜、动线或视频。

## 坐标、比例与场景资产

`bible/scenes/<sid>/whitebox.json`：

```json
{
  "schema_version": "whitebox_scene.v1",
  "scene_id": "SCN-0001",
  "dimensions_m": [6, 3, 5],
  "inferred": true,
  "scale_basis": "按俯视图以0.9m门宽校准，非实测",
  "objects": [
    {"id":"table", "shape":"box", "size_m":[1.2,0.75,0.65], "xy":[0.5,0.5]},
    {"id":"column", "shape":"cylinder", "size_m":[0.3,3,0.3], "position":[-2,1.5,-1]}
  ]
}
```

1单位=1米，Y向上，X向地图右方，Z向地图下方。`dimensions_m=[宽,高,深]`。地图归一化坐标 `(u,v)` 映射到 `[(u-.5)*宽,高度,(v-.5)*深]`。`position` 是几何体中心；`xy` 默认中心高度为 size_m[1]/2，可用 elevation_m 覆盖。`shape` 默认 box，可用 sphere/cylinder。`size_m` 是三个轴的包围盒尺寸，yaw 为绕Y轴弧度。人物轨迹 position 是脚下/支撑平面的锚点，不能误用物体中心。

没有标尺的图无法恢复绝对尺度，必须保留 inferred 和比例锚说明。场景预览仅在 `bible/scenes/<sid>/whitebox.json` 含有显式 objects 数组时显示3D区域；没有建模的场景不显示白模或加载占位。已建模的空场地可以使用空数组，推断尺寸的模型仍正常显示。legacy 分镜编译缺场景资产时按地标生成简模并明确告警；其 20×12m 默认图幅不等于校准值。一个场景白模对应一套布局，暂不自动选择 layout 的替代形态。

## 飞行、升降与三维路径

人物、生物和坐骑均支持全部三轴的运动；现有坐标系为 **Y向上、Z为地面纵深**，不要把输入中的Z当作高度。空中支撑锚点不贴地，模型不会被强制归零；空间视图的动线也保留高度。

组 blocking_map 的 start/path/end 可以使用 `{"xy":[0.4,0.6],"altitude_m":8}`，也可以直接写米制 `{"position":[-1,8,2]}`；点上的 height_m 为旧版 altitude_m 别名，与角色条目的 height_m（身高）区分。逐镜 blocking 支持 position_start/position_end（三维）或 xy_start/xy_end 搭配 altitude_start_m/altitude_end_m。path/beat 可以仅写 `{"t":2,"altitude_m":10}` 表示升高，也可给完整 position；只改xy或地标时保留原高度。精确 whitebox_plans 的 position 直接按三维插值，不受地面限制。

例如4秒飞行：t=0 位置[-1,2,0]、t=2 位置[0,10,2]、t=4 位置[1,6,4]；人物与生物均可使用。同场续组继承包括高度。跟随摄像机保留垂直位移，推断取景中心计入主体高度。

## 分镜组调度

按场次建立 `scene_cast`：同集内相同 `scene_no + scene_id` 的人物与生物默认属于同一在场集合，无对白、静止或镜头外人物也要关联身份/服装参考图并保留白模。不同场次、不同地点不合并；缺少场次号的旧组保守独立。`characters_union` 仍记录组内叙事角色，不能用它裁掉在场人物，也不能据此给整个组写固定画面人数限制。

在白模调度前、prompt 完成后执行 `python code/sync_scene_cast.py --project <slug> --ep <ep> --write [grp…]`。它自动从同场次各组汇总人物，写入组 `scene_cast/scene_cast_refs`，补齐已存在 prompt 的 refs 与主体绑定，不改变已有 `[Image N]` 序号；可重复运行。参考图按本场次服装台账或同场次已有选图确定，缺图明确报错，不跨时代借装。图数超过渠道上限时按既有 refs_cap 流程处理，不裁掉在场人物；此命令只准备数据，不调用生成服务。prompt 前还没有文件时，先保存组关联供工位读取，prompt 产出后再同步。视频生成前通过 `refs_referenced_check.py` 复核，其检查已包含按场次独立推导的参考图完整性。

编译器给遗漏人物沿用同场次最近前组的尾位置/姿态；无前组锚点才用后组首锚，并记录推断。关键帧 `visible:false` 的退场状态继续继承，不让已离场人物复活；仅在画外不等于退场。补充人物的精确轨迹写入计划 `scene_actors`（结构与 actors 一样），不改变旧动线图字母；找不到同场次空间锚点则报错，不放到原点凑数。组级 `scene_presence: {"CHAR-…":{"state":"absent|remote|present","reason":"…"}}` 可明确整组的缺席、远程声音或在场状态；镜内进退场仍用关键帧。只有完成场次同步的组才启用新名单规则：无 `visibility_override_reason` 的摄像机名单被忽略；未迁移的旧组保留原名单，避免在本次选择范围外改镜头。

原始输入：`directing/<ep>/shot_list.json` 的 generation_groups、blocking_map 和 shots，逐镜 camera.json/blocking.json，场景 layout.json。颜色按组内 blocking_map 数组顺序取自固定调色板（与分镜预览组卡的人物 chip 同色），不是跨所有组的永久颜色。骑乘生物与骑手同色。字母字段只保留在数据中作兼容（2026-09-07 起字母动线图 `directing/<ep>/blocking_maps/` 已退役，人物空间位置参考改由本白模视频承担），白模画面不绘制头顶字母、编号或字幕，通过模型颜色和画面外的角色色点图例区分。

精确计划：`directing/<ep>/whitebox_plans/<gid>.json`。actors 和 cameras 可分别省略；若提供 actors，须覆盖 blocking_map 全体及其坐骑，ID必须一致，颜色与字母由系统锁定。

计划也可提供 `extras`（独立的 `EXTRA-` ID、label、kind、color、size_m、keyframes），用于没有登记角色的群演镜头，不更改正式角色集合。人物、群演关键帧的 `visible` 布尔值在该时间点切换，可表示画外电话人物与退场；默认显示。`props` 支持与场景相同的简单几何体（id、shape、size_m、position、yaw、color），可用 `shot_ids` 限定出现镜头，也可用覆盖组时长的 keyframes 表示三维运动。预览与导出共用这些规则。

人物和生物的头部均有白色眼睛、深色瞳孔与突出的白色鼻尖，用于识别正脸方向。模型局部 +Z 为正前方，yaw=0 朝 +Z，yaw=π/2 朝 +X；面部随转身和坐卧姿态变化，在空间、俯视、摄像机预览与导出视频中保持一致。

对话身体朝向需按对象的 ID 和实际坐标校验：`yaw=atan2(target.x-self.x,target.z-self.z)`，水平正面向量为 `[sin(yaw),0,cos(yaw)]`。不要把“画左/画右”当作世界方向，也不要让“朝南”覆盖“面向某人”而不核对坐标。可在组动线登记 `facing_target_id`、逐镜 blocking 登记 `body_facing_target_id`；这些是调度依据，实际渲染仍读取计划关键帧 yaw，不会自动追踪此 ID。计划可记录 `facing_targets` 的对象和起止时段作验收声明，同样不替代数值轨迹。对方移动时补充朝向关键帧并检查插值全过程，直接相向交谈参考角误差≤3°；双向分别计算。

身体朝向、头部/眼神与行走方向分别处理：老道儿偏视或看掌心不要求身体背向王三合；grp035 的0–2秒为转向、行走和落座过渡，落座后才按面对面交谈验收。修正身体方向后，应再检查反打相机是否落在正面及对话轴线正确一侧。dzg6/ep01 的 `whitebox/check_facing.mjs` 独立检查 grp028–grp035 的3015个人物时刻，包含移动目标与组末；另检查 sh050/sh057/sh060 的正脸机位和几何遮挡。该检查与穿模检查各自独立。所有涉及的旧稿“并肩同向”需在 blocking、composition、草描和 prompt 中同步改正。

每段 cameras 可选 `visible_actor_ids`，限定镜头中可见的角色/群演；空数组适用于只有器物的插入镜头。被镜头排除的角色仍留在空间视角和俯视图中，只有关键帧 `visible:false` 才会从所有视角退场。

默认省略 `visible_actor_ids`，由实际机位、画幅和几何遮挡决定入画。不要把 `shot.characters`、`composition.subject` 或 `secondary_subjects` 直接复制成可见名单：这些字段可能只列叙事主体，未包括构图中的静止人物、前景人物及背景群演。必须同时阅读 `layers`、`notes` 和分镜草描，逐一核对所有应入画人物。只有明确的特殊隐藏需求才设置名单，并在该镜 `basis` 写明排除对象和原因；不能用隐藏演员代替正确取景或掩盖穿模。排查“空间视图有人、摄像机视图没人”时，依次检查：关键帧 `visible` → 镜头名单及实际渲染层 → 视锥/画幅 → 几何遮挡。验收名单应独立从构图要求整理，不能从被测可见名单反推，否则会漏检被错误隐藏的人物。

反例 dzg6/ep01 grp050：sh088 名单隐藏王三合和小包子，sh089 名单隐藏老道儿；后者虽未列入叙事角色列表，`composition.layers.mg` 和 `notes` 明确要求他在中间静坐。该组已取消名单，保留真实机位、人物和时长。`whitebox/check_grp050.mjs` 独立要求三位坐席人物出现，逐帧检查渲染层、头部与肩线上身的入画及遮挡；座椅与下半身的局部遮挡不属于此专项的全身无遮挡承诺。

```json
{
  "actors": [{
    "id":"CHAR-0001", "size_m":[0.48,1.7,0.38],
    "keyframes":[
      {"t":0,"position":[-1,0,0],"yaw":1.57,"pose":"stand"},
      {"t":2,"position":[-1,0,0],"yaw":1.57,"pose":"stand"},
      {"t":5,"position":[1,0,0],"yaw":1.57,"pose":"sit"},
      {"t":8,"position":[1,0,0],"yaw":1.57,"pose":"sit"}
    ]
  }],
  "cameras":[{
    "shot_id":"sh001", "start":0, "duration_s":8, "movement":"static",
    "keyframes":[
      {"t":0,"position":[1,1.6,4],"target":[0,1.2,0],"fov":45},
      {"t":8,"position":[1,1.6,4],"target":[0,1.2,0],"fov":45}
    ]
  }],
  "continuity":{"actors":"validate","camera":"cut"}
}
```

人物关键帧 t 是组内秒，摄像机关键帧 t 是镜内秒；camera.start 是组内秒。关键帧严格递增，覆盖0到对应时长；摄像机分段与镜头顺序和时长一致。位置、target、fov、yaw 线性插值，yaw 走最短角；pose 离散切换。关键帧可带 hold=true（到下一帧前保持）或 easing="smooth"。定点停留建议写重复位置。镜头切换时直接切换机位。

旧数据编译优先消费数值 xy_start/xy_end、path/beat 的 t+xy/landmark/pose、camera.whitebox_keyframes。没有数值运动节拍时按组时长均匀分配动线节点，文字姿态只作粗略推断；身高默认1.7m，生物默认1.4m。机位先取 layout view_tile 视轴，再按景别和镜首人物位置估计距离；缺 view_tile 暂取第一机位，所有推断均出警告。只描述运动方式但没有幅度时，推拉按视距20%、横移按1m估计，必须由 whitebox-staging 校准。暂不支持的运镜按固定机位显示并警告。

continuity 从源组 continuity_from 取前组。actors 默认 validate，camera 默认 cut。inherit 使用前组末位置/姿态或机位，不改后续目标；跨场景/场次或无前组时拒绝继承。validate 仅报告冲突，不擅自移动演员。同场次的时间跳跃也应显式 cut。

## 防穿模检查

镜头看得见主体、编译成功、首尾坐标正确，都不代表运动过程没有穿模。白模是按计划插值的渲染器，不会自动作碰撞避让；调度工位必须主动设计可行路径并验证。

- 人物与人物：以实际身体包围盒/胶囊体计算相对运动的最近距离，包含宽度、朝向、姿态、手持物与坐骑。普通非接触动作可先取0.20m表面净距；发现交叉则加绕行点、让路或调整通行时机，保持已定首尾锚点与时长。拥抱、搀扶等只允许相应局部接触，不能放任躯干互穿。
- 人物与墙体：把墙和柱按主体包围半径加0.15m参考余量扩张，检查每段运动是否进入障碍区。门洞路径按「门内 → 门洞 → 全身完全出门 → 转弯 → 走廊」设计，转弯扫过范围也要留在通道内。不要把门内点与转弯后的点直接直线连接。
- 时间覆盖：优先检查连续扫掠体；两个移动角色需检查相对运动，避免只验各自空间路径。非线性插值、转身、坐卧和飞行需用对应三维包围体作自适应细分或逐帧加扫掠复核，采样步长必须小于角色尺寸与最小障碍厚度对应的运动步长。检查镜间与组间衔接、接触事件前后。
- 修正与回执：分别报告摄像机检查和身体碰撞检查，记录冲突对象、时间区间、净距、处理方法和重验结果。禁止通过换机位、隐藏人、缩小人、删墙或无依据扩大门洞掩盖问题；主路线改变要回写权威 blocking 数据。当前通用 `--check-only` 仅保证数据结构、时间与连续性校验，**不含自动碰撞验收**，不可将其通过描述成已无穿模。

项目回归案例：`dzg6/ep01/grp012` 的王三合绕开何香后回到原终点；`grp014` 先沿门洞直行、全身出门后向西转弯。对应计划含 `workflow_notes` 和可见检查说明；`data/projects/dzg6/directing/ep01/whitebox/check_collisions.py` 用保守的直立人物包围圆及膨胀障碍盒连续检查这两组线性轨迹，`collision-before.json` 保留原问题，`collision-review.json` 记录修正后结果。该专用检查不代表整集或其他姿态已通过碰撞验收。

室内取景需保留墙壁，它们定义房间尺度、门洞及碰撞边界。发生隔墙遮挡时，优先将机位置于室内可拍范围，再按构图调整朝向和参考视角；对每个需要入画的主要人物，检查脸部、上身和左右轮廓，而不是只检测一条相机中心线或主角头部射线。允许按镜头意图出现过肩、局部遮挡，但不能漏验被墙完全挡住的其他主要人物。辅助剖切视图可以便于观察室内，但不能删除物理墙或当作正常参考镜头；虚拟拆墙/摄影棚可拆墙必须明确标注。`sh040` 已由东墙外机位移回室内，保留固定三人构图及墙体；`whitebox/check_sh040.mjs` 按24fps检查全6秒、三人的脸部及上身轮廓共2592条视线，结果见 `sh040-camera-review.json`。白模参考视角调整单独记录，正式镜头描述未改。

## 编译、输出与验证

```sh
python code/render_whitebox.py --project dzg6 --ep ep01 --check-only
python code/render_whitebox.py --project dzg6 --ep ep01
python code/render_whitebox.py --project dzg6 --ep ep01 grp002
python code/render_whitebox.py --project dzg6 --ep ep01 --scene SCN-0002
python code/render_whitebox.py --project dzg6 --ep ep01 grp002 --force --fps 24
```

不指定组时处理全体；--scene 处理该集中引用指定场景的所有组，不能与组号同时使用。默认命令在编译后自动导出保存，无需 --export（该旧参数保留兼容）。每次建模或修改后由 Agent 执行；--check-only 不写编译产物或视频，仅供检查，不能当作完成交付。编译结果为 `directing/<ep>/whitebox/episode.json` 和场景目录 `whitebox.scene.json`；计划与编译输出分开，重编译不覆写 Agent 设计。报错包含 group_id；选中组编译错误或视频渲染失败均非零退出。

自动保存按 manifest 比对场景/调度源指纹、渲染器指纹、画幅、分辨率与帧率，且核对 camera.mp4 存在且非空。匹配则复用，否则重出；--force 强制重出。场景几何更新会使该场景下各组的视频过期，场景建模 Agent 需对所有引用它的已有分镜集执行 --scene。首次场景建模无分镜时只交付场景模型，分镜就绪后由白模调度 Agent 自动保存视频。视频先在临时目录完成编码，再发布与更新清单；编码失败保留原有效视频，不能标注本次更新成功。

导出需要项目 Python 依赖 Playwright、已安装 Chromium（`python -m playwright install chromium`）以及 PATH 中 FFmpeg；可用 VIDEOAGENTS_CHROMIUM 指向独立 Chromium 可执行文件。通过 file URL 加载本地渲染器，逐帧按 i/fps 渲染，不依赖实时录屏速度。输出 `assets/whitebox/<ep>/<grp>/{camera.mp4,manifest.json}`，H.264/yuv420p，无音频，默认24fps、长边约960px，画幅读取项目 settings.json 的 output.aspect_preset/aspect_custom：YouTube=960×540（16:9）、抖音=540×960（9:16），自定义比例同样保留。摄像机 aspect、预览和导出像素尺寸使用同一比例；布局图坐标和场景米制尺度不因横竖屏变化而变形。--width/--height 必须同时提供且保持项目比例，支持1080×1920等竖屏尺寸。时长精度为一帧；manifest 记录源数据 SHA-256、帧数和规格。仅一项导出任务同时运行；浏览器关闭后服务端任务继续。

API（前缀 `/api/v1/projects/<project>/whitebox`）：GET `/scenes/<sid>`、GET `/<ep>`；既有 POST `/<ep>/exports/<gid>` 和 GET 同路径状态接口保留供兼容调用，预览页不再触发。路径标识严格限定，禁止目录穿越。预览 GET 不写项目，也不会因用户打开页面而重复渲染。旧服务尚未重启时，前端仍可读取已编译产物预览；自动保存由 Agent 执行宿主 CLI，不依赖用户页面。

`dzg6/ep01` 的8个场景已按其俯视图写入带比例依据的白模。它们位于用户项目 data 目录（按仓库约定不提交）。数值轨迹尚有遗留推断，不能把粗模视为最终镜头调度。最终视频模型画面仍在原分镜组卡中，白模输出不会自动上传到生成渠道。

## 接入视频生成（2026-09-07）

项目「输出设置 → 人物精确空间位置」开启即启用整条白模链（workflow.yaml `whitebox_requested` = 该开关），并把导出的视频自动接成该分镜组视频生成的参考视频：`render_whitebox.py` 导出后自动执行 `python code/sync_whitebox_refs.py --project <slug> --ep <ep> --write [grp…]`（不带 `--write` 为机检 `whitebox_ref_bound`）。对已有组 prompt `assets/prompts/<ep>/<grp>.json`：

- `video_refs`：`camera.mp4`（画面视角，`[Video 1]`）在前（2026-09-08 起白模只有这一路，原 `output.whitebox_top_video` 开关废止）；预算按本组生效视频模型（组级覆盖优先）——Seedance 2.0 参考视频 ≤3 个且总时长 ≤15s，2.5 ≤10 个且 ≤30s，comfyui/runninghub 不支持参考视频则不挂；取舍与原因写入 `whitebox_refs.skipped_reason`。
- 正文 `Shot 1:` 前插入固定英文段：`Whitebox reference:`（camera-view 视频作用：定机位/构图/人物画面位置/景深/朝向/节奏）+ `Whitebox legend:`（按 episode.json 该组 `actors[]` 逐人 `<color> figure = <label> (<id>)`，骑乘生物「riding the same-colored creature」，群演 extras；眼睛与鼻尖=朝向；摄像机本体不出现在画面视角里）+ 禁复现白模外观句；`Global constraints:` 并入 `No whitebox look …`。段落幂等刷新，原 prompt 首次备份到 `directing/<ep>/whitebox/prompt_backups/`。
- video-generation 按 `video_refs` 顺序传 `--ref-video`；方舟/MiniMax 的 reference_video 须公网 URL，须先在「设置 → 文件托管」配置对象存储。参考视频与首尾帧模式互斥（长镜头自动选择尾段视频或尾帧图，详见 [自动续接](continuity.md)；摄影机白模与尾段共用参考视频预算）。

尚无 prompt 的组由 prompt 工位产出后再跑一次 `--write`；场景/调度更新重出视频后再跑即自动刷新。开关关闭的项目不接、脚本报 skipped。

参考实现 API：[Three.js OrbitControls](https://threejs.org/docs/pages/OrbitControls.html)、[WebGLRenderer](https://threejs.org/docs/pages/WebGLRenderer.html)。
