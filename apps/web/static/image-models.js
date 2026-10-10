/* 图像模型清单(各渠道,2026-09-11 自 models.html 抽出):「生成模型」页 models.html 与「故事板」页
 * preview_board.html(草图渠道/模型单独选)共用,改这里两处同时生效。
 * [模型 id, 说明];ID 与各渠道官方目录一致;openrouter 为推荐置顶清单(完整目录页面另拉 /providers/openrouter/models)。 */
window.IMAGE_MODEL_LISTS={
openrouter:[   // 2026-10-10 对照 openrouter.ai/api/v1/images/models 在线目录(59 个)刷新:补 Nano Banana 2.1 /
               // Seedream 5.0 Flash / Hy Image 3.5 Preview / FLUX.3 Image;GPT Image 2.5 即 ChatGPT Images 2.5(两档)
  ['google/gemini-nano-banana-2.1','Nano Banana 2.1 · Google 新一代,接替 Nano Banana 2 / Pro,参考图 ≤14'],
  ['google/gemini-3.1-flash-image','Nano Banana 2(Gemini 3.1 Flash)· 快/画质高/角色一致性好'],
  ['google/gemini-3-pro-image','Nano Banana Pro(Gemini 3 Pro)· 专业设计/长文本/复杂构图'],
  ['google/gemini-3.1-flash-lite-image','Nano Banana 2 Lite(Gemini 3.1 Flash Lite)· 最快/最省,批量草图'],
  ['openai/gpt-image-2.5-sunburst','GPT Image 2.5 Sunburst(ChatGPT Images 2.5)· 精度档,细节/编辑'],
  ['openai/gpt-image-2.5-flare','GPT Image 2.5 Flare(ChatGPT Images 2.5)· 速度档,量大日常出图'],
  ['openai/gpt-5.4-image-2','GPT-5.4 Image 2 · Prompt 理解强,插画/产品图/海报'],
  ['openai/gpt-image-2','GPT Image 2 · 官方图片模型,支持编辑'],
  ['openai/gpt-image-1-mini','GPT Image 1 Mini · 低成本/快速'],
  ['bytedance-seed/seedream-5-0-pro','Seedream 5.0 Pro · 精准编辑/写实场景,商业出图'],
  ['bytedance-seed/seedream-5-0-lite','Seedream 5.0 Lite · 复杂 Prompt/多参考图'],
  ['bytedance-seed/seedream-5-0-flash','Seedream 5.0 Flash · 5.0 系列速度档,量大/低成本'],
  ['bytedance-seed/seedream-4.5','Seedream 4.5 · 人像/美学/小文字渲染优秀'],
  ['x-ai/grok-imagine-image-2.0','Grok Imagine Image 2.0 · 文生图/参考图编辑'],
  ['x-ai/grok-imagine-image-quality','Grok Imagine · 写实/人脸/Logo/海报'],
  ['qwen/qwen-image-3-pro','Qwen Image 3 Pro · 中文/小字渲染/世界知识'],
  ['tencent/hy-image-v3.5-preview','Hy Image 3.5 Preview(腾讯混元)· 生成/编辑一体,参考图 ≤20'],
  ['microsoft/mai-image-2.6','MAI-Image-2.6(微软)· 精度档,设计级画面/编辑'],
  ['meta/muse-image','Muse Image(Meta)· 先推理后出图,复杂构图/编辑'],
  ['black-forest-labs/flux-3-image','FLUX.3 Image · FLUX 新一代旗舰,文生图/多参考图编辑'],
  ['black-forest-labs/flux.2-max','FLUX.2 Max · FLUX 旗舰画质'],
  ['black-forest-labs/flux.2-pro','FLUX.2 Pro · 开源生态最成熟'],
  ['black-forest-labs/flux.2-flex','FLUX.2 Flex · FLUX 灵活版'],
  ['recraft/recraft-v4.1-pro','Recraft V4.1 Pro · 设计/排版/品牌风格'],
],
volcengine:[
  ['doubao-seedream-5-0-pro-260628','Seedream 5.0 Pro(最新,文/图生图/多参考图,出图≥1280×720)'],
  ['doubao-seedream-5-0-260128','Seedream 5.0'],
  ['doubao-seedream-5-0-lite-260128','Seedream 5.0 Lite'],
  ['doubao-seedream-4-5-251128','Seedream 4.5(文/图生图/多图融合)'],
  ['doubao-seedream-4-0-250828','Seedream 4.0(文/图生图)'],
],
byteplus:[
  ['dola-seedream-5-0-pro-260628','Seedream 5.0 Pro(最新,文/图生图/多参考图,出图≥1280×720)'],
  ['seedream-5-0-260128','Seedream 5.0(文/图生图/多参考图/组图)'],
  ['seedream-5-0-lite-260128','Seedream 5.0 Lite(轻量版,推荐用于含人脸图片,默认Seedance过审)'],
  ['seedream-4-5-251128','Seedream 4.5(文/图生图/多图融合)'],
  ['seedream-4-0-250828','Seedream 4.0(文/图生图)'],
],
fal:[   // 首项 = 新配置默认(与 core.py DEFAULT_GENCONFIG 一致)
        // 2026-10-10 对照 Fal 模型目录刷新:补 Seedream 5.0 Pro / Flash、Nano Banana 2.1、GPT Image 2.5 Sunburst、
        // FLUX.3、Ideogram V4.5(清单里的模型 genmedia 都有内置请求体映射,tests/test_image_model_lists.py 核对)。
        // Seedream 5.0 Lite 目录里的规范 ID 已去掉 fal-ai/ 前缀(bytedance/seedream/v5/lite),旧 ID 仍可调用,不改
  ['fal-ai/bytedance/seedream/v5/lite','Seedream 5.0 Lite(Fal 托管;文/图生图/多参考图 ≤10,出图 2K–4K)'],
  ['bytedance/seedream/v5/pro','Seedream 5.0 Pro(Fal 托管;文/图生图/多参考图 ≤10,出图 1K–2K)'],
  ['bytedance/seedream/v5/flash','Seedream 5.0 Flash(Fal 托管;5.0 系列速度档,参考图 ≤10,出图 1K–2K)'],
  ['fal-ai/bytedance/seedream/v4.5','Seedream 4.5(Fal 托管;文/图生图/多图融合,参考图 ≤10)'],
  ['google/nano-banana-2.1','Nano Banana 2.1(Fal 托管;Google 新一代生成/编辑,多参考图,1K/2K/4K)'],
  ['fal-ai/nano-banana-pro','Nano Banana Pro(Fal 托管;Google 生成/编辑旗舰版,多参考图,1K/2K/4K)'],
  ['fal-ai/nano-banana-2','Nano Banana 2(Fal 托管;Google 快速版生成/编辑,多参考图)'],
  ['openai/gpt-image-2.5/sunburst','GPT Image 2.5 Sunburst(Fal 托管;ChatGPT Images 2.5 精度档,参考图 ≤16,无 seed)'],
  ['openai/gpt-image-2.5/flare','GPT Image 2.5 Flare(Fal 托管;ChatGPT Images 2.5 速度档,参考图 ≤16,无 seed)'],
  ['openai/gpt-image-2','GPT Image 2(Fal 托管;OpenAI,参考图 ≤16,无 seed)'],
  ['blackforestlabs/flux-3','FLUX.3(Fal 托管;FLUX 新一代旗舰,文生图/多参考图编辑 ≤10,最高 4K,无 seed)'],
  ['fal-ai/flux-2-pro','FLUX.2 Pro(Fal 托管;文生图/多图编辑)'],
  ['fal-ai/flux-2-max','FLUX.2 Max(Fal 托管;旗舰版,文生图/多图编辑)'],
  ['fal-ai/flux-pro/kontext/max','FLUX Kontext Max(Fal 托管;文生图/多参考图编辑)'],
  ['alibaba/qwen-image-3','Qwen Image 3(Fal 托管;中英提示词,参考图 ≤3,支持负面提示词)'],
  ['fal-ai/hunyuan-image/v3','HunyuanImage 3.0(Fal 托管;仅文生图,支持负面提示词)'],
  ['ideogram/v4.5','Ideogram V4.5(Fal 托管;文字排版强,出图至 2K;带参考图时第一张作底图编辑,另可带参考图 ≤4)'],
],
minimax:[
  ['image-01','Image-01(文/图生图,单角色参考图)'],
],
};
