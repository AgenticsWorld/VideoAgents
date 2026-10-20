"""NSFW 模式的「拒绝判定」(审核模型,2026-10-20)。

语言模型被内容审核拒绝时没有错误码,宿主原先只按拒绝套话(core._LLM_REFUSAL_RE)认;自然措辞的拒绝
(「这单我没有提交……我不做这种编辑」)认不出来。用户在 NSFW 模式里打开「拒绝判定用审核模型」后,
宿主先按运行形态零成本预筛(正常结束、回复不长、没交产物,见 core._refusal_judge_gate),筛中的才调本模块
把 Agent 最后的回复交给审核模型判一次。

- 审核模型是 TypeSafe 的决策模型 Jev(typesafe/jev-1.13),走 OpenRouter 的 Decisions API(POST /api/alpha/decisions):
  不生成文字,对「state + 带类别说明的 choice 题」直接给出各类别概率。只有 policy(因内容 / 安全 / 政策拒绝)算拒绝。
- 只给回复正文末尾 TAIL 字,不给原始请求与图片。
- 2026-10-20 实测(用户拍板换掉路由聊天模型 typesafe/jev-router):原工单 4542f699e746 的拒绝判 policy(概率 1),
  缺文件 / 反问 / 已完成三类各判对;单次约 700 输入 token、$0.00002–0.00003、1.4 s。Agentics 渠道不支持该接口,只走 OpenRouter。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

MODELS = ("typesafe/jev-1.13",)
URL = "https://openrouter.ai/api/alpha/decisions"
KINDS = ("policy", "missing_input", "clarification", "done", "other")
TAIL = 1500
TIMEOUT = 30

SITUATION = "An AI agent ended a task without producing the requested output. This is its final reply (any language)."
QUESTION = {
    "type": "choice",
    "instructions": "Why did the agent stop without producing the requested output?",
    "criteria": {
        "policy": "The agent itself declined on content, safety, ethics, legal or policy grounds "
                  "(e.g. sexual content, nudity, violence, real-person imagery), in any wording or language.",
        "missing_input": "It could not proceed because files, inputs, tools, credentials or permissions were missing or broken.",
        "clarification": "It stopped to ask the user a question or to wait for a decision.",
        "done": "It reports the task as completed.",
        "other": "Anything else."}}

# 测试按钮用的样例:自然措辞的拒绝,拒绝套话正则认不出(应判 policy)
SAMPLE = "这单我没有提交。按要求要把画面里的人物改成裸体,这类编辑我不做。"


class JudgeError(RuntimeError):
    pass


def request_body(text: str, model: str) -> dict:
    return {"model": model,
            "state": {"situation": SITUATION, "final_reply": (text or "").strip()[-TAIL:]},
            "questions": {"kind": QUESTION}}


def parse(data: dict) -> dict:
    """Decisions API 返回 → {kind, refusal, confidence, probabilities, snapshot, cost};没有可读的判定时 JudgeError。"""
    try:
        ans = data["answers"]["kind"]
        kind = str(ans.get("choice") or "").strip().lower()
    except (KeyError, TypeError, AttributeError):
        raise JudgeError(f"unexpected response: {json.dumps(data, ensure_ascii=False)[:200]}") from None
    if not kind:
        raise JudgeError(f"no verdict in response: {json.dumps(ans, ensure_ascii=False)[:200]}")
    if kind not in KINDS:
        kind = "other"
    probs = ans.get("probabilities") if isinstance(ans.get("probabilities"), dict) else {}
    cost = (data.get("usage") or {}).get("cost")
    conf = ans.get("confidence")
    return {"kind": kind, "refusal": kind == "policy",
            "confidence": conf if isinstance(conf, (int, float)) else None,
            "probabilities": {k: v for k, v in probs.items() if isinstance(v, (int, float))},
            "snapshot": str(data.get("model") or ""),
            "cost": cost if isinstance(cost, (int, float)) else None}


def classify(text: str, *, api_key: str, model: str, timeout: float = TIMEOUT) -> dict:
    """判一次:返回 parse() 的结果加 {model, ms};网络 / HTTP / 返回格式出错时 JudgeError。"""
    req = urllib.request.Request(
        URL, data=json.dumps(request_body(text, model)).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}",
                 "X-Title": "VideoAgents"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise JudgeError(f"HTTP {e.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        raise JudgeError(str(getattr(e, "reason", None) or e)[:300]) from None
    return {**parse(data), "model": model, "ms": int((time.time() - t0) * 1000)}
