/* 文本(语言)模型清单(agentics 引擎各渠道,配置键沿用 deepagents;2026-09-11 自 models.html 抽出):「生成模型」页 models.html
 * 与控制台顶栏 index.html(引擎选 agentics 时的渠道/模型两层下拉)共用,改这里两处同时生效。
 * [模型 id, 说明];openrouter 为推荐置顶清单(完整目录另拉 /providers/openrouter/models?modality=text)。
 * 云端渠道没有内置清单(2026-10-26 起不再默认 DeepSeek):模型由用户手填,或取端点 /providers/deepagents/models?provider=cloud。 */
window.TEXT_MODEL_LISTS={
openrouter:[
    ['anthropic/claude-sonnet-5','Claude Sonnet 5(Anthropic)· 写作/长文本/性价比'],
    ['anthropic/claude-opus-4.8','Claude Opus 4.8(Anthropic)· 旗舰,复杂创作/指令遵循强'],
    ['openai/gpt-5.6-sol','GPT-5.6 Sol(OpenAI)· 旗舰通用'],
    ['google/gemini-3.1-pro-preview','Gemini 3.1 Pro(Google)· 长上下文/多模态理解'],
    ['google/gemini-3.5-flash','Gemini 3.5 Flash(Google)· 快/低成本'],
    ['x-ai/grok-4.5','Grok 4.5(xAI)· 通用旗舰'],
    ['deepseek/deepseek-v4-pro','DeepSeek V4 Pro · 中文优秀/低价'],
    ['moonshotai/kimi-k2.6','Kimi K2.6(月之暗面)· 中文创作/长文本'],
    ['qwen/qwen3-max','Qwen3 Max(阿里)· 中文/通用'],
    ['z-ai/glm-5.2','GLM 5.2(智谱)· 中文/Agent 任务'],
    ['minimax/minimax-m3','MiniMax M3 · 中文/Agent 任务'],
],
};
