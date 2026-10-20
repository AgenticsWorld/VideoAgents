"""NSFW 模式的「拒绝判定」(审核模型,2026-10-20)。

语言模型被内容审核拒绝时没有错误码,宿主原先只按拒绝套话(core._LLM_REFUSAL_RE)认;自然措辞的拒绝
(「这单我没有提交……我不做这种编辑」)认不出来。用户在 NSFW 模式里打开「拒绝判定用审核模型」后,
宿主先按运行形态零成本预筛(正常结束、回复不长、没交产物,见 core._refusal_judge_gate),筛中的才调本模块
把 Agent 最后的回复交给审核模型判一次。

- 只给回复正文末尾 TAIL 字,不给原始请求与图片:审核模型看到的只是一段说明,自身不容易拒判。
- 要求只回 JSON {"kind": ...};只有 policy(因内容 / 安全 / 政策拒绝)算拒绝,缺文件、反问用户、已完成都不算。
- 两个渠道(Agentics 桌面端账号 / OpenRouter)都走 OpenAI 兼容 chat/completions。
- 模型清单 MODELS 与「🎨 生成模型 → 审核模型」一致。typesafe/jev-router 是路由模型(按请求挑底层模型与推理强度);
  2026-10-20 实测一次约 1.3k 输入 token、$0.002、3.5 s,路由到 x-ai/grok-4.7;它不在 OpenRouter 的
  Decisions API(/api/alpha/decisions)上,那里只有 typesafe/jev-1.13 这类决策模型。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

MODELS = ("typesafe/jev-router",)
KINDS = ("policy", "missing_input", "clarification", "done", "other")
TAIL = 1500
TIMEOUT = 30

SYSTEM = (
    "You classify why an AI agent ended a task. The user message is the final reply the agent wrote "
    "when it stopped without producing the requested output. It may be in any language.\n"
    'Answer with JSON only, no other text: {"kind": "<policy|missing_input|clarification|done|other>"}\n'
    "- policy: the agent itself declined the request on content, safety, ethics, legal or policy grounds "
    "(for example sexual content, nudity, violence, real-person imagery), in any wording.\n"
    "- missing_input: it could not proceed because files, inputs, tools, credentials or permissions were missing or broken.\n"
    "- clarification: it stopped to ask the user a question or to wait for a decision.\n"
    "- done: it reports the task as completed.\n"
    "- other: anything else.")

# 测试按钮用的样例:自然措辞的拒绝,拒绝套话正则认不出(应判 policy)
SAMPLE = "这单我没有提交。按要求要把画面里的人物改成裸体,这类编辑我不做。"

_JSON_RE = re.compile(r"\{[^{}]*\}")


class JudgeError(RuntimeError):
    pass


def request_body(text: str, model: str) -> dict:
    return {"model": model, "temperature": 0, "max_tokens": 400,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": (text or "").strip()[-TAIL:]}]}


def parse(data: dict) -> dict:
    """chat/completions 返回 → {kind, refusal, routed_model, cost};没有可读的判定时 JudgeError。"""
    try:
        content = str(data["choices"][0]["message"].get("content") or "")
    except (KeyError, IndexError, TypeError, AttributeError):
        raise JudgeError(f"unexpected response: {json.dumps(data, ensure_ascii=False)[:200]}") from None
    kind = ""
    for m in _JSON_RE.finditer(content):
        try:
            kind = str(json.loads(m.group(0)).get("kind") or "").strip().lower()
        except (ValueError, AttributeError):
            continue
        if kind:
            break
    if not kind:
        raise JudgeError(f"no verdict in reply: {content[:200]}")
    if kind not in KINDS:
        kind = "other"
    cost = (data.get("usage") or {}).get("cost")
    return {"kind": kind, "refusal": kind == "policy", "routed_model": str(data.get("model") or ""),
            "cost": cost if isinstance(cost, (int, float)) else None}


def classify(text: str, *, base_url: str, api_key: str, model: str, timeout: float = TIMEOUT) -> dict:
    """判一次:返回 {kind, refusal, routed_model, cost, model, ms};网络 / HTTP / 返回格式出错时 JudgeError。"""
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(request_body(text, model)).encode(),
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
