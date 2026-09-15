# data/fonts/ — 花字外置字体目录(不进 git)

花字(caption)渲染的字体来源有三处:

1. **本机系统字体**(macOS `/System/Library/Fonts` 等)——自动扫描,无需放这里;
2. **本目录**:放置额外的 `.ttf` / `.otf` / `.ttc` 字体文件(建议可商用的开源中文字体,
   如 思源宋体/思源黑体、站酷系列、马善政毛笔体等)。渲染时通过 ffmpeg
   `subtitles=...:fontsdir=data/fonts` 生效,**无需安装到系统**。

放入/删除字体后刷新 manifest:

```bash
python3 code/render_captions.py fonts-scan
```

生成的 `manifest.json` 记录每个字体面的 `id`(如 `user:MaShanZheng`)、family、
是否覆盖 CJK。花字设计 Agent 从 manifest 挑字体,`captions.json` 里按 `font_id` 引用。

3. **项目字体**(推荐给单个项目专用的字体):在 Web 客户端【参考文件】页「字体」板块
   上传,落在 `data/projects/<slug>/refs/fonts/`。渲染/机检时自动并入清单(id 为
   `proj:<family>`,排在最前,**不需要**重跑 fonts-scan),花字、封面、片头片尾、
   字幕等工位全片统一优先使用。查看:`python3 code/render_captions.py fonts-list --project <slug>`。

注意:字体文件名请使用 ASCII(仓库文件名红线);字体二进制与 manifest 均被
.gitignore 排除,不会进入仓库。
