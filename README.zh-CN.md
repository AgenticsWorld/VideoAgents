<p align="center">
  <img src="docs/assets/readme/logo.png" alt="Agentics WorldOS" width="96" />
</p>

<h1 align="center">Agentics WorldOS</h1>

<p align="center">
  <strong>先创造一个虚拟世界，再在这个世界里创作 —— 今天做分集视频，小说、音频、游戏、VR 也从同一个世界里长出来。</strong>
</p>

<p align="center">
  <sub>原名 <strong>VideoAgents</strong>。客户端、安装包和命令仍沿用 VideoAgents 这个名字。</sub>
</p>

<p align="center">
  <a href="#quickstart"><strong>快速开始</strong></a> &middot;
  <a href="#works-with"><strong>支持的渠道</strong></a> &middot;
  <a href="https://github.com/AgenticsWorld/VideoAgents-Samples"><strong>示例项目</strong></a> &middot;
  <a href="https://discord.gg/faaZCrWxCq"><strong>Discord</strong></a> &middot;
  <a href="https://x.com/AgenticsWorld"><strong>X</strong></a> &middot;
  <a href="https://agentics.world"><strong>官网</strong></a> &middot;
  <a href="README.md"><strong>English</strong></a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-GPL--3.0-blue" alt="GPL-3.0 License" /></a>
  <a href="https://github.com/AgenticsWorld/WorldOS/releases"><img src="https://img.shields.io/github/v/release/AgenticsWorld/WorldOS" alt="Latest release" /></a>
  <a href="https://github.com/AgenticsWorld/WorldOS/stargazers"><img src="https://img.shields.io/github/stars/AgenticsWorld/WorldOS?style=flat" alt="Stars" /></a>
  <a href="https://discord.gg/faaZCrWxCq"><img src="https://img.shields.io/badge/discord-join-7289da" alt="Discord" /></a>
</p>

<br/>

<p align="center">
  <a href="docs/assets/readme/storyboard-preview.jpg"><img src="docs/assets/readme/storyboard-preview.jpg" alt="分镜预览页：左侧是参考图、背景图和道具图，右侧是生成的组视频" width="900" /></a>
</p>

<p align="center">
  <sub>示例项目 <a href="https://github.com/AgenticsWorld/VideoAgents-Samples">alices</a>（《爱丽丝梦游仙境》）的「分镜预览」页（英文界面）：一个生成组挂的人物定妆图、背景图和道具图，以及它生成出来的视频。</sub>
</p>

<br/>

# 世界只建一次，之后在里面想创作多少就创作多少

大多数 AI 工具每做一件作品都从一句空白提示词开始。每个镜头、每一章、每段片子都把里面的人和地方重新发明一遍：上一镜的杯子到下一镜换了颜色，角色的脸一集一集地走样，做个番外又得从零开始。

Agentics WorldOS 把顺序倒过来。它是一套开源、文件驱动的多 Agent 系统，先建起一个**虚拟世界**——它的历史与规则、它的人物和他们的声音、它的生物、地点与道具、它的美术风格——并以文件的形式保存在你自己的电脑上。之后做的每一件作品都在这个世界**里面**完成：每一集、每一章、每一段配乐都取自同一套正史、同一批定妆图、同一组 3D 场景，所以前后一致；新作品复用世界里已有的东西，不必再花一次钱重做。

**如果说视频模型是一台摄影机，Agentics WorldOS 就是摄影机前的那个世界，以及围着它运转的整个制片厂。**

### 两层：世界，以及在世界里做出的作品

一个项目就是一个世界。

|              | 里面有什么                                                                                                                                                                 | 在项目目录中的位置                                   |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| 🌍 **世界**  | 世界圣经（时间线、事件、规则、势力）· 带定妆图、服装和声音的角色 · 生物 · 带俯视布局图、3D 白模、360° 全景图和可漫游世界模型的场景 · 带材质与尺寸的道具 · 统一的美术风格 · 音乐库 | `bible/`、`assets/`：每样东西只认一个锚点，所有作品共用 |
| 🎬 **作品**  | 用这个世界做出的分集和其它作品，各自有剧本、故事板、预演、生成记录、剪辑和发布包                                                                                           | `story/`、`directing/`、`edit/`、`publish/`          |

世界先由你签字确认，之后才开始在里面做花钱的活。确认之后 Agent 一律把它当作事实，世界观一致性审核会拿它核对作品。

### 用一个世界能做什么

|     | 作品         | 状态                    | 怎么做                                                                                                               |
| --- | ------------ | ----------------------- | -------------------------------------------------------------------------------------------------------------------- |
| 🎬  | **影视剧**   | ✅ 内置（默认类型）      | 按原著逐集改编；也可以要求只借世界观、不改编原著情节，写原创故事（衍生模式）。                                         |
| 📚  | **小说**     | 🧩 插件，随本仓库提供    | `derivative-fiction` 在正史之内写前传、番外、支线和 if 线；`fusion-fiction` 把一本书的故事搬进另一本书的世界。         |
| 🎧  | **有声剧**   | 🧩 插件                  | [VideoAgents-Plugins](https://github.com/AgenticsWorld/VideoAgents-Plugins) 里的有声剧团队。                          |
| 🎮  | **游戏**     | 🧭 规划中                | 世界里已经有做游戏的起点：米制尺度的 3D 场景、带碰撞网格的世界模型、角色定妆图、声音、音乐和音效。                     |
| 🥽  | **VR**       | 🧭 规划中                | 世界里已经有每个场景关键位置的 360° 全景图，以及能在浏览器里漫游的高斯泼溅世界模型。                                   |

只需要单独一件作品时，另有四种轻量项目类型，跳过大部分世界层：🎙️ **音频配画**（给一段讲述音频配上生成的画面）、🧑‍💼 **数字人**（人物照片加对话音频，生成口型同步的说话人视频）、✂️ **混剪**（从本地素材库的现成镜头里挑片配画）、🖥️ **动效短片**（不用视频模型：从 59 种风格里选一个，由 Agent 把片子写成网页逐帧渲染，配乐也用代码写，音效程序化合成）。

### 怎么用

|        | 步骤           | 做什么                                                                                                     |
| ------ | -------------- | ---------------------------------------------------------------------------------------------------------- |
| **01** | 带来原作       | 新建项目、导入小说或文稿，选定画面风格，设好单集时长和发布平台。                                           |
| **02** | 建世界         | 世界圣经 → 角色、服装与声音 → 生物、场景与道具 → 按需搭 3D 场景、全景图和世界模型。每一层都由你签字。     |
| **03** | 在世界里创作   | 剧本 → 故事板 → 3D 白模预演 → 视频 → 声音 → 剪辑 → 后期 → 发布。上一步没签字，下一步花钱的活就不会开始。 |
| **04** | 回到这个世界   | 世界一直都在。原创新集、前传小说、有声剧：每件新作品都从你已经建好的世界出发，而不是从一句空白提示词出发。 |

119 个专业 Agent 分属 17 个部门。世界观、角色、生物、场景和美术部门负责建世界；剧情、导演、视频生成、音频、剪辑、质量审核和发布部门在世界里创作；音频配画、混剪、数字人、动效短片四支专属团队负责轻量类型。它们按声明式工作流推进，并在最多 12 个人工确认闸门处停下来等你签字，创作方向始终由你决定。系统在你自己的电脑上运行：原作文本、世界和生成的媒体都保存在本地工作区。

<br/>

<a id="works-with"></a>

## 支持的引擎与渠道

执行引擎和生成渠道都由你自己选。每类生成服务单独配置，不绑定某一个模型或厂商。

|                     | 支持                                                                                                              |
| ------------------- | ----------------------------------------------------------------------------------------------------------------- |
| 🤖 **执行引擎**     | Agentics（内置：随程序打包的 Pi，使用 Agentics 账号或本地 OpenAI 兼容模型）· Claude Code · Codex CLI · Kimi Code · Pi · OpenCode · Grok Build · Antigravity CLI |
| 🖼 **图像**         | Agentics · OpenRouter · 火山引擎 · BytePlus · Fal · RunningHub · ComfyUI                                           |
| 🎬 **视频**         | Agentics · OpenRouter · 火山引擎 · BytePlus · Fal · RunningHub · ComfyUI                                           |
| 🎙 **语音 / TTS**   | Agentics · OpenRouter · 火山引擎 · ElevenLabs · MiniMax · ComfyUI                                                  |
| 🎵 **音乐**         | 代码作曲 · Agentics · OpenRouter · ElevenLabs · ComfyUI                                                            |
| 🔊 **音效**         | 代码音效 · Openverse · ElevenLabs · Fal                                                                            |
| 🧑 **数字人**       | Agentics · HeyGen · Kling AI · ComfyUI                                                                             |
| 🔍 **超分**         | FFmpeg · Agentics · 火山引擎 · MiniMax · ComfyUI                                                                   |
| 🌍 **世界模型**     | World Labs Marble · HunyuanWorld（自有 GPU 部署）                                                                  |
| ☁️ **对象存储**     | 火山引擎 TOS · 阿里云 OSS · 腾讯云 COS · 任意 S3 兼容服务                                                          |
| 📣 **发布平台**     | YouTube · YouTube Shorts · Bilibili · TikTok · Instagram · 抖音 · 小红书                                                       |
| 📱 **手机消息**     | 微信 · 飞书 · WhatsApp                                                                                             |

<sub>ComfyUI 工作流可在本地、Comfy Cloud 或 RunningHub 上运行。控制台界面支持 12 种语言。</sub>

<br/>

## 如果你符合下面的情况，Agentics WorldOS 适合你

- ✅ 手里有一个**写在文稿里的世界**——小说、网文、设定集——想让它上屏幕，也不止于屏幕
- ✅ 想**用一个世界做出很多作品**：正片、番外、前传小说、有声剧，不用每次重新设计一遍角色
- ✅ 想把**小说或网文拍成多集视频**，但没有一整个剧组
- ✅ 做过一分钟的 AI 短片，到**十分钟级的中长剧**就卡住了：道具服装跳变、人多了镜头失控、反复重抽把预算烧光
- ✅ 希望 Agent **自己把流水线跑下去**，但创作上的决定仍由你确认
- ✅ 想**为每类任务自己选模型**，有更好的模型时随时切换
- ✅ 希望小说原文、你的世界和生成素材**留在自己的电脑上**
- ✅ 希望每一步都留下**可以打开、修改和追溯的文件**

<br/>

## 功能

### 🌍 建世界

<table>
<tr>
<td align="center" width="33%">
<h3>📖 世界圣经</h3>
把长篇小说拆成事件、时间线和世界圣经——规则、地点、势力——作为后续所有环节的事实依据。
</td>
<td align="center" width="33%">
<h3>🧑‍🎨 角色与资产</h3>
角色、服装、生物、场景、道具各自建卡，配参考图和声音样本，整个世界每件资产只认一个形象锚点。
</td>
<td align="center" width="33%">
<h3>🎨 美术风格</h3>
整个世界统一一种画风，从 1300 多种风格的风格库里挑，也可以自己写。
</td>
</tr>
<tr>
<td align="center">
<h3>🧊 3D 场景</h3>
可选：按米制尺度为每个场景搭白模，人物、道具和摄影机带关键帧调度。导出的机位白模视频就是视频生成的参考视频。
</td>
<td align="center">
<h3>🌐 全景图与世界模型</h3>
在每个场景的关键位置出 360° 全景图，再生成可用 WASD 漫游的高斯泼溅世界（World Labs Marble 或自有 GPU 上的 HunyuanWorld）。
</td>
<td align="center">
<h3>🎵 声音与音乐</h3>
为每个角色设计或克隆音色，项目音乐库的主题在一集又一集里反复出现。
</td>
</tr>
</table>

### 🎬 在世界里创作

<table>
<tr>
<td align="center" width="33%">
<h3>📜 剧本预览</h3>
剧本与时长、情绪、节奏、钩子分析并排成表，任意一块都能把修改意见直接派给负责的 Agent。
</td>
<td align="center" width="33%">
<h3>✏️ 故事板与动态样片</h3>
逐镜分镜表配铅笔草图，可用便宜的图像模型批量出，也可以在手机上手绘，再串成不花生成费的动态样片。
</td>
<td align="center" width="33%">
<h3>🎥 导演台</h3>
在 3D 场景里点选任意人物、道具或机位写修改意见，也可以自己拖动。每次修改自动存版本，可 A|B 对比。
</td>
</tr>
<tr>
<td align="center">
<h3>🎬 图像与视频生成</h3>
提示词按镜头表逐镜写出，每个生成组都带着它必须对齐的定妆图、背景图和道具图。
</td>
<td align="center">
<h3>🎙 对白、音乐与声音</h3>
视频模型原生对白、世界里的角色音色、音乐库里的配乐、音效，以及最终混音。
</td>
<td align="center">
<h3>🎚 后期处理</h3>
调色、修补、特效、包装和声音都按「处方」管理，作用范围从整集到组内时间段，母本永不覆盖。
</td>
</tr>
<tr>
<td align="center">
<h3>📚 小说与有声剧</h3>
插件在正史之内写新小说、把两本书融合成一个世界，或把故事做成有声剧。
</td>
<td align="center">
<h3>🛡 人工确认闸门</h3>
世界圣经、角色与资产、美术风格、剧本、故事板、白模、分镜、视频提示词、视觉生成、后期、首集成片、发布。
</td>
<td align="center">
<h3>✅ 八路质量审核</h3>
逻辑、角色一致性、时间线、世界观一致性、画面、音频、内容安全、版权，八个维度并行审核。
</td>
</tr>
</table>

### 🧰 每个世界的底层

<table>
<tr>
<td align="center" width="25%">
<h3>💰 成本控制</h3>
先用不花钱的预演把戏排好，付费步骤排在签字之后，优先复用，草稿分辨率加超分交付，简单任务用便宜模型：成片视频约每分钟 3 美元。
</td>
<td align="center" width="25%">
<h3>🔌 插件与技能</h3>
声明式插件扩展新的工位、流程和作品类型，技能教会某个 Agent 一门手艺，两者都可以用 zip 安装。
</td>
<td align="center" width="25%">
<h3>🗂 全程文件化</h3>
每张设定卡、每条提示词、每份镜头表和审核报告都是项目里的文件，每次生成都记下所用的模型、渠道和参考图。
</td>
<td align="center" width="25%">
<h3>🏠 本地优先</h3>
在你的电脑上运行，默认只监听 <code>127.0.0.1</code>，并提供版本化 API、OpenAPI 文档和实时事件，便于集成。
</td>
</tr>
</table>

<br/>

## 界面一览

以下截图均为英文界面。

<table>
<tr>
<td align="center" width="33%"><a href="docs/assets/readme/console.jpg"><img src="docs/assets/readme/console.jpg" alt="控制台" /></a><br/><sub><strong>控制台</strong>：Agent 列表、与每个 Agent 的对话、运行面板</sub></td>
<td align="center" width="33%"><a href="docs/assets/readme/workflow.jpg"><img src="docs/assets/readme/workflow.jpg" alt="工作流预览" /></a><br/><sub><strong>工作流预览</strong>：流水线 DAG、任务状态、耗时与 token 用量</sub></td>
<td align="center" width="33%"><a href="docs/assets/readme/script.jpg"><img src="docs/assets/readme/script.jpg" alt="剧本预览" /></a><br/><sub><strong>剧本预览</strong>：剧本分块及对应的时长、事件与钩子</sub></td>
</tr>
<tr>
<td align="center"><a href="docs/assets/readme/characters.jpg"><img src="docs/assets/readme/characters.jpg" alt="人物预览" /></a><br/><sub><strong>人物预览</strong>：定妆图、音色与服装</sub></td>
<td align="center"><a href="docs/assets/readme/scenes.jpg"><img src="docs/assets/readme/scenes.jpg" alt="场景预览" /></a><br/><sub><strong>场景预览</strong>：俯视布局图、场景图与 3D 白模</sub></td>
<td align="center"><a href="docs/assets/readme/props.jpg"><img src="docs/assets/readme/props.jpg" alt="道具预览" /></a><br/><sub><strong>道具预览</strong>：道具卡，含材质与尺寸定义</sub></td>
</tr>
<tr>
<td align="center"><a href="docs/assets/readme/storyboard.jpg"><img src="docs/assets/readme/storyboard.jpg" alt="故事板" /></a><br/><sub><strong>故事板</strong>：分镜表、铅笔草图与动态样片</sub></td>
<td align="center"><a href="docs/assets/readme/director.jpg"><img src="docs/assets/readme/director.jpg" alt="导演台" /></a><br/><sub><strong>导演台</strong>：在 3D 白模现场里做调度</sub></td>
<td align="center"><a href="docs/assets/readme/post.jpg"><img src="docs/assets/readme/post.jpg" alt="后期处理" /></a><br/><sub><strong>后期处理</strong>：A|B 监视器、处方与整集时间轴</sub></td>
</tr>
</table>

<br/>

## Agentics WorldOS 解决什么问题

| 没有共享的世界 | 有了 Agentics WorldOS |
| --- | --- |
| ❌ 每换一种形式都要从零开始：改编版、番外和有声版各自把同样的人物和地点重新设计一遍。 | ✅ 一个项目一个世界：人物、地点、道具、声音和风格只建一次、只签一次字，在里面做的每件作品都复用它们。 |
| ❌ 上一镜桌上的杯子是青花瓷，下一镜变成了白瓷；主角上一场穿的孝服，下一场又换回了干净的常服。 | ✅ 全片资产库：道具卡、按「场合 × 时期」排的服装矩阵，以及每个生成组都要对齐的参考图。 |
| ❌ 一两个人在画面里还好控制。人一多、镜头一长，谁站在哪、看向哪、走到哪，靠文字说不清。 | ✅ 按米制尺度做 3D 白模预演：人物、道具和摄影机带关键帧调度，审看后导出机位白模视频，作为参考视频挂进生成指令。 |
| ❌ 把「这场戏的张力」翻译成模型能执行的指令，靠人工逐镜手搓，慢而且不稳定。 | ✅ Prompt Agent 逐镜按「运镜、动作与表情、空间位置、音频」写提示词：一镜只用一种运镜，动作写明幅度和速度，情绪写成看得见的身体细节。 |
| ❌ 角色长相跑偏，不在场的人也被画进画面。 | ✅ 新生成的关键帧与定妆图做人脸相似度比对；提示词带身份锁，画面里的每个人都必须对得上一张参考图。 |
| ❌ 片子都生成完了，才发现剧本或分镜不对。 | ✅ 把审看前移到不花钱的环节：剧本预览、故事板草图和动态样片。上游在签字之后又被改动时，页面会标记签字已过期。 |
| ❌ 预算大多花在最后用不上的废片上：反复重抽、高分辨率直出、每一镜都重新出参考图。 | ✅ 先做不花钱的预演，付费步骤排在签字之后，优先复用，草稿分辨率加超分交付，自动重跑默认关闭：成片成本约每分钟 3 美元。 |
| ❌ 成片里有一处瑕疵，只能整组重抽。 | ✅ 后期处理工作台：调色、修补、特效和声音都是带版本的处方，可 A\|B 对比、随时回滚。 |
| ❌ 换脸有肖像权风险，还容易做出每部剧都一样的「平均脸」。 | ✅ 角色是按文字设定和美术风格生成的原创形象，不拿真人照片做换脸。 |
| ❌ 平台审查看得很细，一项素材拿不出授权就可能下架。 | ✅ 内容安全 Agent 和版权 Agent 在发布前逐集审查：镜头、封面、字幕、配乐和字体。 |
| ❌ 说不清某一版角色或镜头是怎么来的。 | ✅ 每一步都落成项目里的文件，每个生成物都记下所用的模型、渠道和参考图。 |

<br/>

<a id="quickstart"></a>

## 快速开始

开源，本地运行，执行引擎和生成渠道用你自己的账号。桌面客户端仍叫 VideoAgents。

### 直接安装桌面客户端

| 平台                    | 下载                                                                                  |
| ----------------------- | ------------------------------------------------------------------------------------- |
| macOS（Apple 芯片）     | [VideoAgents.zip](https://s3.agentics.world/packages/video-agents/mac/VideoAgents.zip) |
| Windows（x64）          | [VideoAgents.zip](https://s3.agentics.world/packages/video-agents/win/VideoAgents.zip) |

1. **下载、解压、打开。** 安装包不含 Python，首次启动时客户端会自动下载运行环境。
2. **在顶栏选一个执行引擎。** 对应的命令行没装或没登录时会弹出引导窗口：用官方脚本一键安装，再弹出终端运行它自己的登录命令；已装好但不是最新版本时会提示「一键更新」（运行该命令行自带的更新命令）或「暂不更新」。
3. **配置生成渠道。** 在 ⚙️ 设置 → 生成模型 里填入要用的图像、视频、音频服务的凭据。
4. **新建项目（也就是你的第一个世界）并导入小说。** 剧组从世界圣经开始干活，每到一个确认闸门就停下来等你签字。

媒体检查、音频处理和剪辑需要 FFmpeg。Windows 客户端在本机没有 FFmpeg 时会提示自动下载；macOS 用 `brew install ffmpeg` 安装。

### 或者从源码运行

环境要求：Python 3.10 或更高版本、FFmpeg、至少一种执行引擎。Node.js 22.12 或更高版本只在开发或打包桌面客户端时需要。

```bash
git clone https://github.com/AgenticsWorld/WorldOS.git
cd WorldOS
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python apps/web/check.py
python apps/web/server.py
```

浏览器打开 <http://127.0.0.1:8630>，API 文档位于 <http://127.0.0.1:8630/api/v1/docs>。完成可编辑安装后也可以直接运行 `videoagents-web`；只需要 API 服务时运行 `videoagents-api`。

需要无人值守的全自动流水线时，用 `make run-auto` 启动，`claude` 引擎的 Agent 执行命令时不再弹审批。该模式下 Agent 可以执行任意命令，请只在可信的机器上使用。

桌面端开发、打包、发版和内置 Agentics 引擎见 [`docs/DEVELOPMENT.zh-CN.md`](docs/DEVELOPMENT.zh-CN.md)。

<br/>

## 更多

- **插件** — [VideoAgents-Plugins](https://github.com/AgenticsWorld/VideoAgents-Plugins)：扩展剧组的 Agent 团队与流程，如衍生小说、双书融合、有声剧（音频配画、混剪、数字人已并入主程序，新建项目时选项目类型即可）。在 ⚙️ 设置 → 插件 里上传 zip 安装，上传的插件保存在可写的运行时数据目录。
- **技能** — [VideoAgents-Skills](https://github.com/AgenticsWorld/VideoAgents-Skills)：教某个 Agent 一门手艺的 `SKILL.md` 技能包。在控制台的「技能」按钮里上传 zip 安装。
- **示例项目** — [VideoAgents-Samples](https://github.com/AgenticsWorld/VideoAgents-Samples)：可以直接打开看的完整项目，包括本页截图用的 _alices_。在项目下拉里用「导入项目…」导入 zip。

<br/>

## 仓库结构

```text
agents/           Agent 职责、工作流文档和 DAG
code/             仓库级校验与工具脚本
modules/          媒体与音频适配器
services/api/     可独立运行的 Python API 服务、Schema 和运行状态
services/runtime/ Agent 调度、项目业务、Provider 与内部运行工具
apps/web/         原 WebUI 静态页面、Python 静态服务与同源 API 反向代理
apps/desktop/     Electron 外壳、Python Web 服务生命周期、打包与更新
plugins/          声明式 Agent 插件（仓库内置衍生小说团队）
tests/            回归测试（pytest）：模块、流水线 CLI 与仓库完整性
data/projects/    每个世界一个目录：世界圣经、资产、作品与生成媒体，默认不入 Git
```

`agents/WORKFLOW.md` 是供人阅读的流程权威，`agents/workflow.yaml` 是 orchestrator 的机器执行输入。浏览器与 Electron 共用同一套静态 WebUI，旁边是版本化的 `/api/v1`、OpenAPI、持久运行状态与实时 SSE 通知。完整的组件关系和目录决策见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

<br/>

## 配置与安全

运行配置和 SQLite 控制状态保存在 `data/.videoagents/`，项目素材始终保存在 `data/projects/`。这些目录可能包含 API Key、运行日志、小说原文和生成媒体，均不会入库。

公开版默认：

- 仅监听 `127.0.0.1`
- Agent 权限模式为 `acceptEdits`
- 不读取 Claude OAuth 凭据进行用量探测

需要局域网手绘分镜或更高自动化权限时，参考 [`.env.example`](.env.example) 显式开启。启用前请阅读 [`SECURITY.md`](SECURITY.md)，不要把控制台直接暴露到公网。

<br/>

## 开发

```bash
python -m pip install -e ".[dev]"
pytest
npm run test:web   # 浏览器端白模 / 导演台代码的 Node 检查
```

CI 跑 `tests/` 全量：模块与流水线 CLI 回归、仓库完整性（JSON/YAML、工作流 Agent 引用、安全默认值）、HTTP 层（路由表、离线 GET 冒烟、Web 控制台页面）。需要 `ffmpeg` 的用例在未安装时自动跳过。

完整的开发指南见 [`docs/DEVELOPMENT.zh-CN.md`](docs/DEVELOPMENT.zh-CN.md)，参与贡献见 [`CONTRIBUTING.md`](CONTRIBUTING.md)。

<br/>

## 社区

- [Discord](https://discord.gg/faaZCrWxCq) — 加入社群
- [X](https://x.com/AgenticsWorld) — 关注更新与公告
- [GitHub Issues](https://github.com/AgenticsWorld/WorldOS/issues) — 报告问题、提功能建议

<br/>

## 许可证

Copyright 2026 AgenticsWorld.

项目采用 [GNU 通用公共许可证第 3 版](LICENSE)（`GPL-3.0-only`）。允许使用、修改、分发和商业使用；分发本项目或其修改版时，必须以同一许可证发布并提供对应的源代码，保留许可证与版权声明（含 [`NOTICE`](NOTICE)），并标明所做修改及修改日期。本软件不附带任何担保。

v1.0.38 及更早的发布版本以 Apache License 2.0 发布，这些版本仍可按该许可证使用。

标准引用信息见 [`CITATION.cff`](CITATION.cff)。

## 商标与服务

文中出现的第三方产品和渠道名称只用于说明可对接的集成，商标和服务权利归各自所有者。Agentics WorldOS（VideoAgents）不提供也不再分发任何第三方模型或服务。

<br/>

---

<p align="center">
  <sub>以 GPL-3.0 开源。给想亲手建世界、在里面当导演，而不想整天盯着模型抽卡的人。</sub>
</p>
