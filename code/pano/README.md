# code/pano — 场景 720 全景 × 白模坐标对齐 × 机位场景图(spike 工具包)

目标:让「摆机位截真实场景图」不靠真 3D 世界模型。全景只提供外观,白模只提供几何;
机位图 = 全景投射到白模几何后从白模机位渲染,再由 Seedream 单参考精修;最后与白模视频一起喂视频模型。
设计与两组试产记录见 offer 项目 `qa/reports/pano_spike.md`(data 目录,不入库)。宿主系统未集成,均为独立 CLI。

## 流程(以 offer / SCN-0004 / grp005 为例)

```sh
P=data/projects/offer
# ① 全景:俯视图 + 九格图 + 提示词 → Seedream 5.0 pro(2:1,pro 上限 4624220 px)
python code/pano/gen_pano.py --project offer --scene SCN-0004 --prompt-file $P/qa/reports/pano_spike/pano/pano_02_pro_prompt.txt \
    --out assets/panorama/SCN-0004/pano_01.png --model doubao-seedream-5-0-pro-260628 --size 3040x1520
# ② 8 个 90° 透视窗
python code/pano/make_windows.py $P/assets/panorama/SCN-0004/pano_01.png $P/assets/panorama/SCN-0004/align
# ③ 对齐:视觉模型找地标 → 后方交会解观察点 P 与中央列朝向 yaw0(rms ≤ 5°、|P−intent| ≤ 2 m 为通过)
python code/pano/align.py locate --project offer --scene SCN-0004 --pano $P/assets/panorama/SCN-0004/pano_01.png \
    --workdir $P/assets/panorama/SCN-0004/align --landmarks $P/qa/reports/pano_spike/scn0004_landmarks.json
python code/pano/align.py solve  --project offer --scene SCN-0004 --pano $P/assets/panorama/SCN-0004/pano_01.png \
    --workdir $P/assets/panorama/SCN-0004/align --landmarks $P/qa/reports/pano_spike/scn0004_landmarks.json \
    --intent-xz 0 0.5 --intent-yaw0-deg -90
python code/pano/make_viewer.py $P/assets/panorama/SCN-0004/pano_01.png $P/assets/panorama/SCN-0004/align/annot.jpg \
    $P/assets/panorama/SCN-0004/align/pano.json $P/assets/panorama/SCN-0004/viewer.html   # 浏览器 360° 查看
# ④ 机位截图(projected):按 whitebox_plans 的 camera 出每镜首帧 + 与白模同帧并排
python code/pano/shots.py --project offer --ep ep01 --gid grp005 --pano $P/assets/panorama/SCN-0004/pano_01.png \
    --meta $P/assets/panorama/SCN-0004/align/pano.json --outdir $P/assets/panorama_shots/ep01/grp005/work
# ⑤ 精修成场景图(Seedream 单参考;desc.json 每镜文字/追加参考/抠物件)
python code/pano/make_stills.py --project offer --ep ep01 --gid grp005 --shots-dir $P/assets/panorama_shots/ep01/grp005/work \
    --desc $P/qa/reports/pano_spike/grp005/desc.json
# 把 <sid>_still.png 挂进 assets/prompts/<ep>/<gid>.json 的 refs(Seedance 2.0 上限 9 张,九格图为机检硬性要求),
# 正文每镜写 "framed exactly like [Image N] (environment still of this camera)";白模视频由 sync_whitebox_refs.py 挂。
# ⑥ 提交 Seedance(先 --dry-run 看 roles 与上限)
python code/pano/run_seedance.py --project offer --ep ep01 --gid grp005 --suffix _pano --dry-run
python code/pano/run_seedance.py --project offer --ep ep01 --gid grp005 --suffix _pano
```

## 约定与已知边界

- 坐标与白模一致:Y 上、X 图右、Z 图下,yaw=atan2(dx,dz);全景 `u=((yaw0−az)/2π+0.5) mod 1`,`v=0.5−el/π`;面朝 +Z 时右向量为 −X。
- 全景的「方位」可信(硬地标 rms 1–3.5°),「距离/尺寸」不可信(可移动物件常缩到 1/2);对齐只用建筑固定物,
  可移动物/模糊点(layout kind = spot/furniture/path/ground/…)只报偏差。
- 提示词按画面横向位置写方位,不用「身后」;接缝方向选树丛;pro 的「dark low-key」会把天画成乌云,精修步默认强制日光。
- crop 模式只在机位贴近观察点且主体远时成立;默认 projected。小物件(石兽/椅)在全景里约 0.5 m 偏差 + 纹理密度不足,
  精修时要么抠掉(`inpaint_objects`)交给角色 sheet,要么把 sheet 作 `extra_refs` 一起喂。
- `shots.py` 会应用 whitebox_plans 里同名 props 对场景物体的覆盖(与渲染器约定一致)。
- 试产结论(grp004/grp005):机位、站位、朝向、动作时序与白模逐帧一致;Seedance 2.0 对切点时序(晚约 1 s)与
  人物离镜头距离(近景抓领被放远)跟随弱,属视频模型上限。
- `render_whitebox.py` 需系统 python3(装有 Playwright);本目录脚本用 `.venv/bin/python`(numpy/scipy/cv2)。
