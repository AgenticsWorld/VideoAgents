/* 图像模型清单(各渠道,2026-09-11 自 models.html 抽出):「生成模型」页 models.html 与「故事板预览」页
 * preview_board.html(草图渠道/模型单独选)共用,改这里两处同时生效。
 * [模型 id, 说明];ID 与各渠道官方目录一致;openrouter 为推荐置顶清单(完整目录页面另拉 /providers/openrouter/models)。 */
window.IMAGE_MODEL_LISTS={
openrouter:[
  ['google/gemini-3.1-flash-image','Nano Banana 2(Gemini 3.1 Flash)· 快/画质高/角色一致性好'],
  ['google/gemini-3-pro-image','Nano Banana Pro(Gemini 3 Pro)· 专业设计/长文本/复杂构图'],
  ['openai/gpt-5.4-image-2','GPT-5.4 Image 2 · Prompt 理解强,插画/产品图/海报'],
  ['openai/gpt-image-2','GPT Image 2 · 最新官方图片模型,支持编辑'],
  ['openai/gpt-image-1-mini','GPT Image 1 Mini · 低成本/快速'],
  ['bytedance-seed/seedream-4.5','Seedream 4.5 · 人像/美学/小文字渲染优秀'],
  ['x-ai/grok-imagine-image-quality','Grok Imagine · 写实/人脸/Logo/海报'],
  ['black-forest-labs/flux.2-pro','FLUX.2 Pro · 开源生态最成熟'],
  ['black-forest-labs/flux.2-max','FLUX.2 Max · FLUX 旗舰画质'],
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
  ['fal-ai/bytedance/seedream/v5/lite','Seedream 5.0 Lite(Fal 托管;文/图生图/多参考图 ≤10,出图 2K–4K)'],
  ['fal-ai/bytedance/seedream/v4.5','Seedream 4.5(Fal 托管;文/图生图/多图融合,参考图 ≤10)'],
  ['fal-ai/nano-banana-pro','Nano Banana Pro(Fal 托管;Google 生成/编辑旗舰版,多参考图,1K/2K/4K)'],
  ['fal-ai/nano-banana-2','Nano Banana 2(Fal 托管;Google 快速版生成/编辑,多参考图)'],
  ['openai/gpt-image-2.5/flare','GPT Image 2.5 Flare(Fal 托管;OpenAI 通用版,参考图 ≤16,无 seed)'],
  ['openai/gpt-image-2','GPT Image 2(Fal 托管;OpenAI,参考图 ≤16,无 seed)'],
  ['fal-ai/flux-2-pro','FLUX.2 Pro(Fal 托管;文生图/多图编辑)'],
  ['fal-ai/flux-2-max','FLUX.2 Max(Fal 托管;旗舰版,文生图/多图编辑)'],
  ['fal-ai/flux-pro/kontext/max','FLUX Kontext Max(Fal 托管;文生图/多参考图编辑)'],
  ['alibaba/qwen-image-3','Qwen Image 3(Fal 托管;中英提示词,参考图 ≤3,支持负面提示词)'],
  ['fal-ai/hunyuan-image/v3','HunyuanImage 3.0(Fal 托管;仅文生图,支持负面提示词)'],
],
minimax:[
  ['image-01','Image-01(文/图生图,单角色参考图)'],
],
};
