# data/sfx/ — 花字音效库(不进 git)

花字出现时的配套音效(whoosh/impact/pop 等)从本目录选取,**不逐次生成**。

初始化(二选一或并用):

```bash
python3 scripts/fetch_sfx.py --synth
```

用 ffmpeg 合成一套 CC0 入门音效(离线、零版权风险,效果基础);或手动下载
CC0 素材(Kenney kenney.nl、freesound.org 按 CC0 过滤)后导入:

```bash
python3 scripts/fetch_sfx.py --import-dir ~/Downloads/kenney_impact_sounds
```

素材变动后刷新 manifest:

```bash
python3 code/render_captions.py sfx-scan
```

`manifest.json` 记录每条音效的 `id`/`tags`/`duration_s`/`license`/`gain_db_default`;
其中 tags、license、gain_db_default 是人工可改字段,重扫会保留。花字设计 Agent
按 tags 挑音效,`captions.json` 里按 `sfx_id` 引用;11-qa/copyright 终审会核对
license,请只放可商用素材(CC0 优先)。

注意:音频文件名请使用 ASCII;素材与 manifest 均被 .gitignore 排除。
