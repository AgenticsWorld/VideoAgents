# 四方向平视背景图(cardinal plates)— 实验记录

状态:**实验 v5**(2026-09-12,dzg6 ep01 SCN-0075 S02 + SCN-0001)。与现行「全景制分镜背景图」(`docs/shot_plates.md`、`docs/scene_panos.md`)并行,不改动现有链路,不接进组 prompt refs。

## 思路(v5,2026-09-12)

1. 视点固定在场景俯视图**正中心**(白模米制 [0, 0];落在实体内则吸附到最近的 0.25 m 空网格点;`--at` 可改),镜头高度 = 本场景机高中位数(≥1.4 m)。
   四个方向共用这一个视点,不再按方向后退。
2. 参考图只有一张**标点俯视图**(俯视图叠视点红点 + 本向视锥 + 方位字母)。白模帧参考图已去掉(不再渲白模、不再用 `renderView`),
   几何/内容全靠文字:四向方位描述 + 逐物体距离/高度/画面横向百分比 + 严格一点透视措辞。
3. 提示词明确要求**画面视角不倾斜**:镜头轴线水平(pitch 0°,不俯不仰)、不滚转、不侧转,地平线为画面正中的水平线,竖直边保持竖直无梯形;
   负面词加 tilted camera / high angle / low angle / looking up / looking down / converging verticals / keystone distortion。
   原「严格一点透视 · 正对」措辞保留。
4. 四张**整图**直接作该场景的分镜背景图,不按镜裁切;每镜按白模机位朝向(与运镜)从四张里选 1~多张(picks 记偏角与是否出边,仅供参考)。

历史:v1 只给俯视图直接出四张(方位随机偏转);v2 俯视图→全景→几何投四向(模型不按平面图距离画);v3 站点渲白模全景→投 16 mm 白模帧
(方位对了,但站点离实体太近,模型自行偏转机位 15°~30°);v4 视点按方向退到被实体/边界挡住前 + 12 mm 透视相机直出白模帧 + 严格一点透视措辞
(SCN-0075 四张正对,产物在 `prev-v4*/` 与 `*.prev-*.png`)。v5 = 用户定去掉白模帧、视点回到正中心、加不倾斜措辞。

## 代码

- `modules/cardinal_plates.py` 规则与数据结构;`code/render_cardinal_plates.py` CLI。
- 用法:
  ```
  python code/render_cardinal_plates.py --project dzg6 --ep ep01 --scene SCN-0075 --scene-no S02 --dry-run       # 视点 + 标点图 + 提示词
  python code/render_cardinal_plates.py --project dzg6 --ep ep01 --scene SCN-0075 --scene-no S02 --sun west-north-west --seed N
  python code/render_cardinal_plates.py --project dzg6 --ep ep01 --scene SCN-0075 --only north --force        # 只重出北图
  python code/render_cardinal_plates.py --project dzg6 --ep ep01 --scene SCN-0075 --assign-only               # 只重算逐镜选图
  ```
  `--at x,z` 视点(白模米制,负数要写成 `--at=-3.3,0.2`;缺省俯视图正中心);`--lens` 等效焦距(缺省 12);
  `--only north,south` 只出子集;`--force` 重出;`--force-geometry` 重算视点/重画标点图。

## 数据

- 库:`assets/concepts/scenes/<sid>/cardinal/`
  - `index.json`(schema `cardinal_plates.v5`):`station`(视点 = 俯视图正中心)、`stations{north|east|south|west}`(与 station 同点,保留结构)、
    `lens_mm_equiv / fov_h_deg / fov_v_deg / plate_size`、`plates{dir}`(file、bearing_deg、camera、station、refs、prompt、seed、channel)
  - `viewpoint_<dir>.jpg` 俯视图叠视点红点 + 本向视锥 + 方向字母(唯一参考图);
    `<dir>.png / <dir>.json / <dir>.prompt.txt` 成图与记录;`review.html` 校对页
  - 旧版产物:`prev-v3/`、`prev-v4-far/`、`<dir>.prev-*.png`、`<dir>.whitebox.jpg`(v4 白模帧,已不再使用)
- 集索引:`directing/<ep>/cardinal_plates.json`(schema `cardinal_plates_episode.v5`,v4 旧文件改名 `cardinal_plates.v4.json`):每镜 category、bearing_start/end、fov、picks[]{dir, role, file, delta_deg, fits}
- 坐标约定同白模:`x = (u-0.5)·dims_x`,`z = (v-0.5)·dims_z`;罗盘按 `layout.json orientation`(缺省上北下南)。

## 选图规则(`pick_plates`)

| 运镜档 | 选图 |
|---|---|
| static / push_pull | 本镜水平视窗整个落在某张里(±2°)→ 该方向一张;跨边 → 相邻两张 |
| pan_tilt | 起点方向 ∪ 短弧扫过的方向 ∪ 终点方向 |
| track / complex | 起点、终点各按上法取,合并 |

不裁切:picks 直接引用整图;`delta_deg` = 机位朝向相对该图正向的偏角,`fits` = 本镜视窗是否整个在图内。

## 实测结论 v4(dzg6 SCN-0075 S02,火山 doubao-seedream-5-0-pro,2560x1440,seed 20260912)

- 中心站点 [-3.75, 2.75];视点:北图 z=4.75(树排前,退 2 m)、南图 z=-8.75(院墙前,退 11.5 m)、东图 x=-19.5、西图 x=19.5。
  曾试过北图退到楼前 z=9.0(`prev-v4-far/`):视点在树底下,画面上角被树冠遮,用户否决。
- v3 的北/南图机位斜 15°~30°(南向白模帧只有一面墙 + 树冠,模型无从守正);v4 北图院墙水平、大门居中、单消失点在正中,
  南图楼面正对、路缘水平,东/西图沿马路一点透视。改法 = 视点退开 + 12 mm 整场入画 + 「严格一点透视」措辞,三者一起用。
- 参考图只挂白模帧 + 标点图后四张各自重出(seed 同),构图都守住;`prev-v4-far/north.copied-east.png` 是挂了东/西图后照抄东图的反例。

## 实测结论 v1(dzg6 SCN-0075,火山 doubao-seedream-5-0-pro,1920x1080,seed 20260911)

- **11 mm(≈117°)首轮**:北图直线、内容对,但实际视场只有 ~65°;南图走成鱼眼(车道线弯);东图一致性好,但把太阳辉光画在正前方(实际在镜头背后)。
- 负面词加 `fisheye / barrel distortion / curved horizon / bent straight lines` + 提示词改「建筑级直线超广角」措辞后:
  - 14 mm 南图:直线守住,但机位偏转向西南(马路斜着跑向画右);
  - 加「square-on:视线垂直方向的东西横穿画面」措辞后 **12 mm 南图:直线 + 正对 + 太阳方位都对**;
  - 11 mm 同措辞:直线守住,但又偏转向西。
- 结论:同一 seed 下构图是否正对带随机性,与焦距无强关联;**12 mm(≈113°)** 是本次「直线透视能守住的最大视角」,定为缺省。模型实际画出的视场普遍窄于标称。
- 网络:参考图走 base64 上传,本机到火山北京上行一度只有 ~2 KB/s(6 MB PNG 标点图直接 400 / 超时),恢复到 ~20 KB/s 后 1.1 MB JPEG 正常;标点图存 JPEG q92 原分辨率。

## 备份

`assets/concepts/scenes/SCN-0075/cardinal/backup-11mm/` 保留首轮 11 mm 四张(未加直线负面词版本)。
