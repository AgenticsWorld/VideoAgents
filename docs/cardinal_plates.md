# 四方向平视背景图(cardinal plates)— 实验记录

状态:**实验**(2026-09-11,dzg6 ep01 S02 / SCN-0075)。与现行「全景制分镜背景图」(`docs/shot_plates.md`、`docs/scene_panos.md`)并行,不改动现有链路,不接进组 prompt refs。

## 思路

1. 以场景俯视图 `layout_top.png` 为唯一几何依据,在场景内一个「站点」出 **北、南** 两张平视空场景图;
2. 以俯视图 + 已成的北图、南图为参考,出 **东、西** 两张(材质/光线/天气以北南两图为准,提示词按方位写明「北侧的东西在画左/画右」);
3. 四张图连同站点坐标入库,作该场景的分镜背景图;
4. 每镜按白模机位朝向(与运镜)从四张里选 1~多张,长焦镜头在超广角图里做偏轴裁切。

## 代码

- `modules/cardinal_plates.py` 规则与数据结构;`code/render_cardinal_plates.py` CLI。
- 用法:
  ```
  python code/render_cardinal_plates.py --project dzg6 --ep ep01 --scene SCN-0075 --at=-3.3,0.2 --dry-run
  python code/render_cardinal_plates.py --project dzg6 --ep ep01 --scene SCN-0075 --at=-3.3,0.2 --lens 12 --seed N grp005 grp006
  python code/render_cardinal_plates.py --project dzg6 --ep ep01 --scene SCN-0075 --assign-only grp005
  ```
  `--at x,z` 白模米制站点(注意负数要写成 `--at=-3.3,0.2`);`--lens` 等效焦距;`--only north,south` 只出子集;`--force` 重出;
  `--shrink-refs` 参考图压成长边 1280 JPEG 上传(上行极慢时用,缺省原图上传)。

## 数据

- 库:`assets/concepts/scenes/<sid>/cardinal/`
  - `index.json`(schema `cardinal_plates.v1`):`station{position_m, xy_norm, px, dimensions_m}`、`fov_v_deg / fov_h_deg / lens_mm_equiv`、
    `plates{north|south|east|west}`(file、bearing_deg、refs、upload_refs、prompt、seed、stage、channel、camera)
  - `viewpoint_<dir>.jpg` 俯视图叠站点红点 + 视锥扇形 + 方向字母(给图像模型定位);`<dir>.png / <dir>.json` 成图与记录
  - `<dir>.crop_f<fov>_b<bearing>.png` 按镜偏轴裁切
- 集索引:`directing/<ep>/cardinal_plates.json`(schema `cardinal_plates_episode.v1`):每镜 category、bearing_start/end、fov、picks[]{dir, role, file, crop}
- 坐标约定同白模:`x = (u-0.5)·dims_x`,`z = (v-0.5)·dims_z`;罗盘按 `layout.json orientation`(缺省上北下南)。

## 选图规则(`pick_plates`)

| 运镜档 | 选图 |
|---|---|
| static / push_pull | 机位朝向离某正方向 ≤30° → 该方向一张;否则夹着它的两张 |
| pan_tilt | 起点方向 ∪ 短弧扫过的方向 ∪ 终点方向 |
| track / complex | 起点、终点各按最近方向取,合并 |

裁切:本镜垂直视场小于平视图时按 `tan(fov/2)` 比例取窗口,再按朝向偏角做直线透视平移 `x = tan(Δ)/tan(hfov/2) × W/2`,越界贴边。

## 实测结论(dzg6 SCN-0075,火山 doubao-seedream-5-0-pro,1920x1080,seed 20260911)

- **11 mm(≈117°)首轮**:北图直线、内容对,但实际视场只有 ~65°;南图走成鱼眼(车道线弯);东图一致性好,但把太阳辉光画在正前方(实际在镜头背后)。
- 负面词加 `fisheye / barrel distortion / curved horizon / bent straight lines` + 提示词改「建筑级直线超广角」措辞后:
  - 14 mm 南图:直线守住,但机位偏转向西南(马路斜着跑向画右);
  - 加「square-on:视线垂直方向的东西横穿画面」措辞后 **12 mm 南图:直线 + 正对 + 太阳方位都对**;
  - 11 mm 同措辞:直线守住,但又偏转向西。
- 结论:同一 seed 下构图是否正对带随机性,与焦距无强关联;**12 mm(≈113°)** 是本次「直线透视能守住的最大视角」,定为缺省。模型实际画出的视场普遍窄于标称。
- 网络:参考图走 base64 上传,本机到火山北京上行一度只有 ~2 KB/s(6 MB PNG 标点图直接 400 / 超时),恢复到 ~20 KB/s 后 1.1 MB JPEG 正常;标点图存 JPEG q92 原分辨率。

## 备份

`assets/concepts/scenes/SCN-0075/cardinal/backup-11mm/` 保留首轮 11 mm 四张(未加直线负面词版本)。
