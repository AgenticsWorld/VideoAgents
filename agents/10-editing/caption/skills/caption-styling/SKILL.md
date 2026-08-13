# SKILL.md — 花字风格与素材库(caption-styling)

> 花字 Agent 的设计手册:先同步素材库,再按影片题材选风格包。渲染细节归宿主 CLI,本 skill 管"用什么、怎么配"。

## 何时读我

- 每次接到花字**设计**工单(av2-caption / p9-caption),动笔前;
- 花字**烧录**工单(caption-render)开始前(第 0 步同步素材)。

## 第 0 步:同步远端素材库

```bash
python3 code/render_captions.py assets-sync
```

- 素材库是独立 GitHub 仓库(配置在 `modules/caption_assets.json` 的 `repo`,或环境变量 `VIDEOAGENTS_CAPTION_ASSETS_REPO=owner/name[@branch]`),用户动态维护,**素材二进制不进代码仓库**;
- 同步产物落 `data/fonts/remote/`、`data/sfx/remote/`(远端删除的文件本地同步移除),并自动重扫两份 manifest;
- 输出 `[SKIP] 未配置` 时不算失败:继续用本地已有素材;但若 manifest 里连一套 CJK 字体都没有,先跑 `scripts/fetch_sfx.py --synth` 兜底音效,并上报用户补字体,不要拿系统宋体硬凑风格化标题。

## 题材 → 风格包映射

按项目 `brief.md` / `bible/style.json` 判断题材,选对应包;拿不准时用「历史纪实」的克制版(headline 减半)。

| 题材 | headline 预设 | keyword 预设 | 动画组合(入/出) | 音效族 | 密度 |
|---|---|---|---|---|---|
| **历史正剧/纪实**(本仓库 demo 同款) | 毛笔体(MaShanZheng;狂放升级:ZhiMangXing 行书/LiuJianMaoCao 草书)+ 金橙/红渐变 + 白内圈深外圈双描边 + 金晕 | 综艺粗体(QingKeHuangYou;更重:NotoSansCJKSC-Black 超黑)白字黑边白外圈 | 红标题 `smash`/`zoom_out`;书法标题 `pop_bounce`/`flash_out`;keyword `slide_up`/`fade` | 落点重音优先 cinema 族(braam_hit/sub_drop/riser_hit/thunder_hit)与 kenney-war 刀剑金属(sword_slice/metal_clash);whoosh 前导;keyword 用 pluck/drop/pop 池 | 高:~8-12s 一条,开场最密 |
| **悬疑/惊悚** | 细宋/楷 + 暗红或冷白 + 窄描边 + 青蓝 glow | 冷白细体,少用底衬 | `fade` 慢入 / `flash_out`;禁 pop_bounce | 低频 boom、riser、金属刮擦;不用可爱 pop | 低:只标关键线索,~30s 一条 |
| **都市/轻喜/综艺** | 得意黑(SmileySans-Oblique,斜体窄黑冲击首选)/黄油体/快乐体 + 多色分词 + 粗黑描边 | 同族 + 逐字底衬块(色随语义换) | `pop_bounce`/`dissolve`,底衬词 `pop_bounce`/`fade` | pluck/pop/ding 高频轮换,变调幅度开大;高潮点用 explosion/sub_drop | 最高:逐句情绪词都上屏 |
| **科幻/未来** | 细黑体 + 冷色渐变 + 强 glow(青/紫) | 细黑 + glow,禁毛笔体 | `fade`+`zoom_out`;可用 `smash` 表现冲击 | 电子 blip/glitch/synth 族 | 中 |
| **情感/文艺** | 楷书暖色 + 米色描边,禁双层重描边 | 楷书小字号 | `fade`/`fade`,禁 smash/flash | 风铃/轻 ding,音量再压 3dB | 低 |

## 字号标定(2026-08-12,按 demo 实测)

- headline `size_pct` **15–20**(demo 大标题字高 ≈ 画面高 20-30%,四字标题应横贯半个画面以上);
- keyword **11–14**(demo 关键词 ≈ 12-16%);
- 字号偏小是"没有冲击力"的第一大原因(两次返工教训),同屏排布冲突时**优先挪位置、不缩字号**;
- **一律水平排布**:`angle_deg` 斜排默认禁用(大字号下小角度倾斜读作"歪了",2026-08-12 用户裁定),仅用户显式要求时使用。

## 通用纪律(任何题材都适用)

1. **冲击帧必须发生在不透明时**:smash/zoom_out 的时序配比已由渲染器保证,设计侧不要用 `style_override` 把入出场时长改得过长(入 ≤0.5s、出 ≤0.4s);
2. **音效反单调三板斧**:headline 双层(whoosh 前导 + 落点重音)、keyword 池轮换、逐条 `pitch` 0.9–1.1 微变;
3. **同屏 ≤2 条且错位**;不入底部字幕安全区;文案溯源(词典或母带原文)机检口径不变;
4. **素材图卡(cards)默认不使用**——仅当用户显式要求时启用,且只用项目内图片;
5. 交付前:设计过 `check_captions.py --require design`,烧录过 `--require render`。

## 参考对标

风格靶子是用户提供的 `花字音效demo/后期.mp4` 与 `花字参考.mp4`(历史正剧包即按此拆解):
入场 = 逐字快速揭示/巨字砸入;出场两大招 = 放大冲出镜头、闪白熄灭;红标题带金色外晕。
新题材没有把握时,请用户提供 10–30s 参考片段,逐帧拆解后再定包。
