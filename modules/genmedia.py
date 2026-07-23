#!/usr/bin/env python3
"""genmedia.py — 统一图像/视频/音乐生成模块(零第三方依赖;仅 --ref-video 需装所选对象存储的 SDK)。

渠道与模型在 Web 客户端「生成服务」页配置(落盘 data/.videoagents/genconfig.json),
本模块按配置自动路由到对应渠道;Agent 只管出 prompt 与产物路径,不挑模型。

CLI:
  python3 modules/genmedia.py info
  python3 modules/genmedia.py image --prompt "..." --output out.png \
      [--negative "..."] [--aspect 16:9 | --size 1280x720] [--ref 参考图 ...] \
      [--n 4] [--seed 1234] [--dry-run]
  python3 modules/genmedia.py video --prompt "..." --output out.mp4 \
      [--first-frame a.png] [--last-frame b.png] [--duration 4] \
      [--resolution 1080p] [--aspect 16:9] [--seed 1234] [--dry-run] \
      [--ref 参考图 ...] [--ref-video 视频 ...] [--audio-ref 音频 ...] \
      [--generate-audio on|off] [--return-last-frame tail.png]

  视频三种图生模式互斥(方舟约束):首帧 / 首尾帧 / 多参考图(--ref,≤9 张)。
  多镜头组生成(Seedance 2.0):prompt 用官方 Shot 1:/Shot 2: 分镜结构,素材以
  [Image N]/[Audio N] 按 content 顺序引用;时长 [4,15] 整数秒或 -1;不支持 seed。
  V2V 编辑/延长(Seedance 2.0):--ref-video 传原视频(≤3 个,单个 2-15s 且总时长
  ≤15s,单文件 ≤45MB),prompt 用「视频n/图片n」序号引用素材,指令写清改什么、
  其余保持不变(局部穿帮修复);与首尾帧互斥;输入视频秒数计费(含视频输入档单价)。
  方舟要求 reference_video 为公网 URL:参考视频自动上传对象存储换预签名 URL,
  需先在 Web 控制台「设置 → 文件托管」配置渠道(火山 TOS/阿里 OSS/腾讯 COS/S3 兼容,
  生效渠道=选中的标签页;各渠道 SDK 按需安装:tos/oss2/cos-python-sdk-v5/boto3)。

  python3 modules/genmedia.py music --prompt "<音乐描述>" --output bgm.mp3 [--dry-run]
  python3 modules/genmedia.py tts --text "<旁白文本>" --output narr.mp3 \
      [--voice eve] [--speed 1.0] [--instructions "<语气/情绪指令>"] [--dry-run]

Python:
  from modules.genmedia import generate_image, generate_video, generate_music, \
      generate_tts, get_config

渠道:
  图像: openrouter(chat completions, modalities=image) / ideogram
        / volcengine(方舟 images/generations,Seedream 系列)
        / byteplus(海外 ModelArk,与方舟同构 API) / comfyui(本地)
  视频: openrouter(POST /v1/videos 异步任务) / volcengine(方舟 contents/generations/tasks)
        / byteplus(海外 ModelArk,与方舟同构 API)
        / comfyui(本地,需配置 API 格式工作流 JSON)
  音乐: openrouter(chat completions 流式, modalities=audio;Lyria 3 Pro 完整歌曲 /
        Lyria 3 Clip 30s 片段;输出格式按扩展名 mp3/wav/flac/opus)
        / elevenlabs(POST /v1/music,Eleven Music v1/v2;--duration 指定时长 3–600s,
        省略=模型自定;force_instrumental 由「生成模型」页配置,默认纯音乐;仅 .mp3/.opus)
  TTS : openrouter(POST /api/v1/audio/speech,原始字节流;.mp3 或 pcm 裸流;
        Grok Voice / MAI-Voice-2 / Voxtral / Kokoro 等,音色名因模型而异)
        / volcengine(豆包语音 openspeech v3 单向流式,Doubao-Seed-TTS 2.0;
        凭证=新版语音技术控制台「API Key 管理」的 API Key,非方舟 ARK Key;
        音色为 speaker 名(控制台「音色库」),S_ 开头的克隆音色自动切 seed-icl-2.0 资源)
        / elevenlabs(POST /v1/text-to-speech/{voice_id};音色为 voice_id,
        可在「生成模型」页从 Voice Library 搜索并一键加入账号)

ComfyUI 自定义工作流占位符(文本替换):
  字符串位: "{{PROMPT}}" "{{NEGATIVE}}" "{{CHECKPOINT}}" "{{FIRST_FRAME}}" "{{LAST_FRAME}}"
  数值位(不要加引号): {{WIDTH}} {{HEIGHT}} {{SEED}} {{DURATION}}
"""
import argparse
import base64
import hashlib
import json
import mimetypes
import os
import random
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", ROOT / "data")).expanduser().resolve()
RUNTIME_DIR = Path(os.environ.get(
    "VIDEOAGENTS_RUNTIME_DIR", DATA_DIR / ".videoagents"
)).expanduser().resolve()
CONFIG_PATH = Path(os.environ.get(
    "VIDEOAGENTS_CONFIG_PATH", RUNTIME_DIR / "genconfig.json"
)).expanduser().resolve()

# 配置里 Key 为空时的环境变量兜底
ENV_KEYS = {"openrouter": "OPENROUTER_API_KEY", "ideogram": "IDEOGRAM_API_KEY",
            "volcengine": "ARK_API_KEY", "byteplus": "BYTEPLUS_API_KEY",
            "elevenlabs": "ELEVENLABS_API_KEY"}

ASPECT_SIZES = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1024, 1024),
                "4:3": (1152, 864), "3:4": (864, 1152), "21:9": (1680, 720)}

VIDEO_POLL_INTERVAL = 10
VIDEO_TIMEOUT = 1800
COMFY_TIMEOUT = 1800


# ---------------- 配置 ----------------

AGENTMODELS_PATH = RUNTIME_DIR / "agentmodels.json"


def _forbid_dispatch_layer(kind: str) -> None:
    """调度层守卫:00-orchestration 各 Agent(总制片/context/evaluation 等)只派单
    不生成,禁止直接调用生成能力(SOUL.md 边界)。VIDEOAGENTS_AGENT 由 runtime 注入运行
    环境并传递到全部子进程,所以总制片自己写 driver 脚本绕道也拦得住;对 codex /
    deepagents 引擎(没有 PreToolUse hook)这里是唯一的机制级硬拦截。"""
    agent = os.environ.get("VIDEOAGENTS_AGENT", "")
    if agent.startswith("00-orchestration/"):
        raise SystemExit(
            f"[genmedia] 拒绝执行:{agent} 属调度层,只派单不生成,禁止直接生成{kind}。"
            "正确做法:生成工单并通过 services/runtime/dispatch.py 派发给对应执行 Agent"
            "(图像=06-art、视频=08-video-gen、旁白/对白/BGM=09-audio)。")


def _agent_provider_override(kind: str) -> str:
    """Agent 级渠道覆盖:runtime 在运行环境注入 VIDEOAGENTS_AGENT,若该 Agent 在
    data/.videoagents/agentmodels.json 里单独配置了 image/video 渠道,则优先于全局 provider。"""
    agent = os.environ.get("VIDEOAGENTS_AGENT", "")
    if not agent:
        return ""
    try:
        ov = json.loads(AGENTMODELS_PATH.read_text()).get(agent) or {}
        return str(ov.get(f"{kind}_provider") or "")
    except Exception:
        return ""


def get_config(kind: str) -> dict:
    """读取 kind(image|video)的生效渠道配置,返回 {provider, model, ...}。"""
    if not CONFIG_PATH.is_file():
        raise RuntimeError(f"未找到生成模型配置 {CONFIG_PATH};先在 Web 控制台「🎨 生成模型」页保存配置")
    cfg = json.loads(CONFIG_PATH.read_text())[kind]
    provider = cfg["provider"]
    ov = _agent_provider_override(kind)
    if ov and isinstance(cfg.get(ov), dict):
        provider = ov
    pc = dict(cfg[provider])
    if provider != "comfyui":
        pc["api_key"] = pc.get("api_key") or os.environ.get(ENV_KEYS[provider], "")
        if not pc["api_key"]:
            raise RuntimeError(f"{kind} 渠道 {provider} 未配置 API Key(Web 控制台填入,或设环境变量 {ENV_KEYS[provider]})")
        pc["model"] = pc.get("custom_model") or pc.get("model") or ""
        if not pc["model"]:
            raise RuntimeError(f"{kind} 渠道 {provider} 未选择模型")
    return {"provider": provider, **pc}


# ---------------- HTTP 基础 ----------------

def _request(url: str, data: bytes | None = None, headers: dict | None = None,
             method: str | None = None, timeout: int = 120) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:800]
        raise RuntimeError(f"HTTP {e.code} {url}\n{body}") from e


def _post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 300) -> dict:
    h = {"Content-Type": "application/json", **(headers or {})}
    return json.loads(_request(url, json.dumps(payload).encode(), h, timeout=timeout))


def _get_json(url: str, headers: dict | None = None, timeout: int = 60) -> dict:
    return json.loads(_request(url, headers=headers, timeout=timeout))


def _save(data: bytes, output: str) -> str:
    p = Path(output)
    if not p.is_absolute():
        p = Path.cwd() / p
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return str(p)


def _file_to_data_url(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        raise RuntimeError(f"参考图不存在: {path}")
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def _decode_data_url(url: str) -> bytes:
    if url.startswith("data:"):
        return base64.b64decode(url.split(",", 1)[1])
    return _request(url, timeout=300)


# 对象存储:AK/SK 为空时的环境变量兜底(按渠道)
STORAGE_ENV = {"tos": ("TOS_ACCESS_KEY", "TOS_SECRET_KEY"),
               "oss": ("OSS_ACCESS_KEY_ID", "OSS_ACCESS_KEY_SECRET"),
               "cos": ("COS_SECRET_ID", "COS_SECRET_KEY"),
               "s3": ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")}


def _upload_tos(c, ak, sk, bucket, key, path, expires):
    try:
        import tos
    except ImportError as e:
        raise RuntimeError("缺少火山 TOS SDK:python3 -m pip install tos") from e
    client = tos.TosClientV2(ak, sk, c.get("endpoint") or "tos-cn-beijing.volces.com",
                             c.get("region") or "cn-beijing")
    try:
        client.put_object_from_file(bucket, key, path)
        return client.pre_signed_url(tos.HttpMethodType.Http_Method_Get, bucket, key,
                                     expires=expires).signed_url
    finally:
        client.close()


def _upload_oss(c, ak, sk, bucket, key, path, expires):
    try:
        import oss2
    except ImportError as e:
        raise RuntimeError("缺少阿里云 OSS SDK:python3 -m pip install oss2") from e
    ep = c.get("endpoint") or "oss-cn-beijing.aliyuncs.com"
    if not ep.startswith("http"):
        ep = f"https://{ep}"
    b = oss2.Bucket(oss2.Auth(ak, sk), ep, bucket)
    b.put_object_from_file(key, path)
    return b.sign_url("GET", key, expires)


def _upload_cos(c, ak, sk, bucket, key, path, expires):
    try:
        from qcloud_cos import CosConfig, CosS3Client
    except ImportError as e:
        raise RuntimeError("缺少腾讯云 COS SDK:python3 -m pip install cos-python-sdk-v5") from e
    client = CosS3Client(CosConfig(Region=c.get("region") or "ap-beijing",
                                   SecretId=ak, SecretKey=sk))
    client.upload_file(Bucket=bucket, Key=key, LocalFilePath=path)
    return client.get_presigned_url(Method="GET", Bucket=bucket, Key=key, Expired=expires)


def _upload_s3(c, ak, sk, bucket, key, path, expires):
    """S3 兼容通用:AWS 官方 endpoint 留空;R2/MinIO/B2 等填各自 endpoint。"""
    try:
        import boto3
    except ImportError as e:
        raise RuntimeError("缺少 S3 SDK:python3 -m pip install boto3") from e
    kw = {"aws_access_key_id": ak, "aws_secret_access_key": sk,
          "region_name": c.get("region") or "us-east-1"}
    if c.get("endpoint"):
        ep = c["endpoint"]
        kw["endpoint_url"] = ep if ep.startswith("http") else f"https://{ep}"
    s3 = boto3.client("s3", **kw)
    s3.upload_file(path, bucket, key)
    return s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key},
                                     ExpiresIn=expires)


_STORAGE_UPLOADERS = {"tos": _upload_tos, "oss": _upload_oss,
                      "cos": _upload_cos, "s3": _upload_s3}


def _storage_upload_url(path: str) -> str:
    """上传本地文件到对象存储,返回预签名 GET URL。

    方舟对 reference_video 硬性要求公网 URL(base64 内联被 400 拒绝)。渠道与密钥
    在 Web 控制台「设置 → 文件托管」配置(genconfig.json storage,provider 为生效
    渠道:tos/oss/cos/s3),AK/SK 为空时回退 STORAGE_ENV 对应环境变量。object key
    取文件内容 sha1,同文件重传幂等;桶保持私有,链接凭签名限时访问。
    """
    p = Path(path)
    if not p.is_file():
        raise RuntimeError(f"参考视频不存在: {path}")
    try:
        st = json.loads(CONFIG_PATH.read_text()).get("storage") or {}
    except Exception:
        st = {}
    provider = st.get("provider") or "tos"
    fn = _STORAGE_UPLOADERS.get(provider)
    if not fn:
        raise RuntimeError(f"未知存储渠道 {provider}(可选 {sorted(_STORAGE_UPLOADERS)})")
    c = st.get(provider) or {}
    ak_env, sk_env = STORAGE_ENV[provider]
    ak = c.get("access_key") or os.environ.get(ak_env, "")
    sk = c.get("secret_key") or os.environ.get(sk_env, "")
    bucket = c.get("bucket") or ""
    if not (ak and sk and bucket):
        raise RuntimeError(
            f"reference_video 需公网 URL:请在 Web 控制台「设置 → 文件托管」的"
            f"「{provider}」标签页配置 bucket/access_key/secret_key"
            f"(AK/SK 也可用环境变量 {ak_env}/{sk_env})")
    key = ((c.get("prefix") or "genmedia-refs/")
           + hashlib.sha1(p.read_bytes()).hexdigest()[:16] + p.suffix.lower())
    url = fn(c, ak, sk, bucket, key, str(p), int(c.get("url_expires") or 86400))
    print(f"[genmedia] 参考视频已上传 {provider}: {key}", file=sys.stderr, flush=True)
    return url


# ---------------- 图像:OpenRouter ----------------

def _image_openrouter(cfg, prompt, negative, refs, width, height, seed):
    content = [{"type": "text", "text": prompt
                + (f"\nNegative (avoid): {negative}" if negative else "")
                + f"\nImage size: {width}x{height}"}]
    for r in refs or []:
        content.append({"type": "image_url", "image_url": {"url": _file_to_data_url(r)}})
    body = {"model": cfg["model"],
            "messages": [{"role": "user", "content": content}],
            "modalities": ["image", "text"]}
    resp = _post_json("https://openrouter.ai/api/v1/chat/completions", body,
                      {"Authorization": f"Bearer {cfg['api_key']}"}, timeout=300)
    msg = (resp.get("choices") or [{}])[0].get("message") or {}
    images = msg.get("images") or []
    if not images:
        raise RuntimeError(f"OpenRouter 未返回图像(model={cfg['model']}):"
                           f"{(msg.get('content') or json.dumps(resp)[:400])!s:.400}")
    return _decode_data_url(images[0]["image_url"]["url"])


# ---------------- 图像:Ideogram ----------------

def _image_ideogram(cfg, prompt, negative, refs, width, height, seed):
    model = cfg["model"]
    aspect = _closest_aspect(width, height)
    headers = {"Api-Key": cfg["api_key"]}
    if model.upper().startswith("V_3") or model.lower().startswith("v3"):
        body = {"prompt": prompt, "aspect_ratio": aspect.replace(":", "x"),
                "rendering_speed": "DEFAULT"}
        if negative:
            body["negative_prompt"] = negative
        if seed is not None:
            body["seed"] = seed
        resp = _post_json("https://api.ideogram.ai/v1/ideogram-v3/generate", body,
                          headers, timeout=300)
    else:
        req = {"prompt": prompt, "model": model,
               "aspect_ratio": "ASPECT_" + aspect.replace(":", "_")}
        if negative:
            req["negative_prompt"] = negative
        if seed is not None:
            req["seed"] = seed
        resp = _post_json("https://api.ideogram.ai/generate", {"image_request": req},
                          headers, timeout=300)
    data = resp.get("data") or []
    if not data or not data[0].get("url"):
        raise RuntimeError(f"Ideogram 未返回图像:{json.dumps(resp)[:400]}")
    return _request(data[0]["url"], timeout=300)


def _closest_aspect(width: int, height: int) -> str:
    ratio = width / height
    return min(ASPECT_SIZES, key=lambda a: abs(ASPECT_SIZES[a][0] / ASPECT_SIZES[a][1] - ratio))


# ---------------- 图像:方舟同构渠道(火山引擎 / BytePlus,Seedream 系列) ----------------

# volcengine=火山方舟国内区;byteplus=字节海外 ModelArk(ap-southeast-1),两者 API 同构,
# 仅域名与模型 ID 前缀不同(doubao-seedream-* vs seedream-*/dreamina-seedance-*)
ARK_API_BASES = {"volcengine": "https://ark.cn-beijing.volces.com/api/v3",
                 "byteplus": "https://ark.ap-southeast.bytepluses.com/api/v3"}


def _ark_base(cfg) -> str:
    return ARK_API_BASES[cfg.get("provider") or "volcengine"]


def _image_ark(cfg, prompt, negative, refs, width, height, seed):
    text = prompt + (f"\n避免出现:{negative}" if negative else "")
    body = {"model": cfg["model"], "prompt": text,
            "size": f"{width}x{height}",
            "response_format": "b64_json", "watermark": False}
    if seed is not None:
        body["seed"] = seed
    if refs:
        urls = [_file_to_data_url(r) for r in refs]
        body["image"] = urls[0] if len(urls) == 1 else urls   # Seedream 4.x 图生图/多图融合
    resp = _post_json(f"{_ark_base(cfg)}/images/generations", body,
                      {"Authorization": f"Bearer {cfg['api_key']}"}, timeout=300)
    data = resp.get("data") or []
    if not data:
        raise RuntimeError(f"方舟未返回图像:{json.dumps(resp, ensure_ascii=False)[:400]}")
    if data[0].get("b64_json"):
        return base64.b64decode(data[0]["b64_json"])
    if data[0].get("url"):
        return _request(data[0]["url"], timeout=300)
    raise RuntimeError(f"方舟返回格式异常:{json.dumps(data[0])[:400]}")


# ---------------- ComfyUI 通用 ----------------

def _comfy_upload(base: str, path: str) -> str:
    """上传输入图,返回服务器端文件名。"""
    p = Path(path)
    if not p.is_file():
        raise RuntimeError(f"输入图不存在: {path}")
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"{p.name}\"\r\nContent-Type: {mime}\r\n\r\n").encode() \
        + p.read_bytes() \
        + (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue"
           f"\r\n--{boundary}--\r\n").encode()
    resp = json.loads(_request(base + "/upload/image", body,
                               {"Content-Type": f"multipart/form-data; boundary={boundary}"}))
    return resp["name"]


def _comfy_run(base: str, workflow: dict, output: str, want_video: bool) -> str:
    """提交工作流,轮询完成,下载首个产物到 output。"""
    resp = _post_json(base + "/prompt", {"prompt": workflow, "client_id": uuid.uuid4().hex})
    pid = resp.get("prompt_id")
    if not pid:
        raise RuntimeError(f"ComfyUI 提交失败:{json.dumps(resp)[:400]}")
    deadline = time.time() + COMFY_TIMEOUT
    while time.time() < deadline:
        time.sleep(2)
        hist = _get_json(f"{base}/history/{pid}").get(pid)
        if not hist:
            continue
        status = hist.get("status") or {}
        if status.get("status_str") == "error":
            msgs = [m for m in status.get("messages", []) if m[0] == "execution_error"]
            raise RuntimeError(f"ComfyUI 执行出错:{json.dumps(msgs)[:600]}")
        outputs = hist.get("outputs") or {}
        files = []
        for node_out in outputs.values():
            for key in ("images", "gifs", "videos"):
                files += node_out.get(key) or []
        if files:
            pick = next((f for f in files
                         if want_video == f["filename"].lower().endswith((".mp4", ".webm", ".gif", ".webp"))),
                        files[0])
            q = urllib.parse.urlencode({"filename": pick["filename"],
                                        "subfolder": pick.get("subfolder", ""),
                                        "type": pick.get("type", "output")})
            return _save(_request(f"{base}/view?{q}", timeout=300), output)
        if status.get("completed"):
            raise RuntimeError("ComfyUI 已完成但无文件产物(工作流缺 SaveImage/SaveVideo 节点?)")
    raise RuntimeError(f"ComfyUI 超时({COMFY_TIMEOUT}s)")


def _comfy_workflow(cfg, tokens: dict, kind: str) -> dict:
    """加载配置的工作流模板并做占位符替换;图像无模板时用内置 txt2img。"""
    wf_path = (cfg.get("workflow") or "").strip()
    if wf_path:
        p = Path(wf_path)
        if not p.is_absolute():
            p = ROOT / p
        if not p.is_file():
            raise RuntimeError(f"配置的 ComfyUI 工作流不存在: {wf_path}")
        text = p.read_text()
        for k, v in tokens.items():
            if isinstance(v, str):
                text = text.replace("{{%s}}" % k, json.dumps(v)[1:-1])  # 转义后嵌入字符串位
            else:
                text = text.replace("{{%s}}" % k, str(v))
        return json.loads(text)
    if kind == "video":
        raise RuntimeError("ComfyUI 视频生成必须在「🎨 生成模型」页配置工作流 JSON(API 格式)")
    ckpt = (cfg.get("checkpoint") or "").strip()
    if not ckpt:
        raise RuntimeError("ComfyUI 未配置 checkpoint(「🎨 生成模型」页测试连接后选择)")
    return {
        "3": {"class_type": "KSampler", "inputs": {
            "cfg": 7, "denoise": 1, "sampler_name": "euler", "scheduler": "normal",
            "steps": 25, "seed": tokens["SEED"], "model": ["4", 0],
            "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {
            "batch_size": 1, "width": tokens["WIDTH"], "height": tokens["HEIGHT"]}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": tokens["PROMPT"]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": tokens["NEGATIVE"]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "genmedia", "images": ["8", 0]}},
    }


def _image_comfyui(cfg, prompt, negative, refs, width, height, seed, output):
    base = cfg["url"].rstrip("/")
    tokens = {"PROMPT": prompt, "NEGATIVE": negative or "",
              "WIDTH": width, "HEIGHT": height, "SEED": seed,
              "CHECKPOINT": cfg.get("checkpoint") or ""}
    if refs:
        tokens["FIRST_FRAME"] = _comfy_upload(base, refs[0])
    wf = _comfy_workflow(cfg, tokens, "image")
    return _comfy_run(base, wf, output, want_video=False)


# ---------------- 视频:OpenRouter ----------------

def _video_openrouter(cfg, prompt, first, last, duration, resolution, aspect, seed, output):
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    body = {"model": cfg["model"], "prompt": prompt}
    if duration:
        body["duration"] = duration
    if resolution:
        body["resolution"] = resolution
    if aspect:
        body["aspect_ratio"] = aspect
    if seed is not None:
        body["seed"] = seed
    frames = []
    for path, ftype in ((first, "first_frame"), (last, "last_frame")):
        if path:
            frames.append({"type": "image_url", "frame_type": ftype,
                           "image_url": {"url": _file_to_data_url(path)}})
    if frames:
        body["frame_images"] = frames
    job = _post_json("https://openrouter.ai/api/v1/videos", body, headers)
    jid, poll = job.get("id"), job.get("polling_url")
    if not jid:
        raise RuntimeError(f"OpenRouter 视频任务创建失败:{json.dumps(job)[:400]}")
    deadline = time.time() + VIDEO_TIMEOUT
    while time.time() < deadline:
        time.sleep(VIDEO_POLL_INTERVAL)
        st = _get_json(poll or f"https://openrouter.ai/api/v1/videos/{jid}", headers)
        status = st.get("status")
        if status == "completed":
            data = _request(f"https://openrouter.ai/api/v1/videos/{jid}/content?index=0",
                            headers=headers, timeout=600)
            return _save(data, output)
        if status == "failed":
            raise RuntimeError(f"OpenRouter 视频生成失败:{json.dumps(st)[:400]}")
    raise RuntimeError(f"OpenRouter 视频超时({VIDEO_TIMEOUT}s),job={jid}")


# ---------------- 视频:方舟同构渠道(火山引擎 / BytePlus,Seedance 系列) ----------------

MAX_VIDEO_REFS = 9        # 方舟 reference_image 上限
MAX_AUDIO_REFS = 3        # 方舟 reference_audio 上限
MAX_AUDIO_TOTAL_S = 15.2  # 方舟 r2v reference_audio 总时长硬限(超限任务创建即 400 InvalidParameter;
                          # 实证:tothemoon 2026-07-20 两段 ~12s 样本合计 24.1s 被拒——voiceprint 规格 ≤5s/段,§8A)


def _audio_duration_s(path: str) -> float | None:
    """ffprobe 实测音频时长(秒);ffprobe 不可用/失败返回 None(跳过预检,交由方舟侧拒绝)。"""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=30)
        return float(out.stdout.strip())
    except Exception:
        return None
MAX_VIDEOIN_REFS = 3      # 方舟 reference_video 上限(单个 2-15s,总时长 ≤15s)
MAX_VIDEOIN_BYTES = 45 * 1024 * 1024  # 参考视频 data URL 内联上限(base64 膨胀后仍须 <64MB 请求体)


def _is_seedance2(model: str) -> bool:
    return "seedance-2" in (model or "")


def _ark_video_body(cfg, prompt, first, last, duration, resolution, aspect, seed,
                    refs, audio_refs, gen_audio, want_last_frame,
                    video_refs=None, to_url=None, video_to_url=None):
    """构造方舟视频任务请求体(独立函数便于 dry-run 校验;to_url 可替换文件内联逻辑,
    video_to_url 单独指定参考视频的 URL 化方式——方舟要求 reference_video 为公网 URL)。"""
    to_url = to_url or _file_to_data_url
    video_to_url = video_to_url or to_url
    is_v2 = _is_seedance2(cfg["model"])
    if refs and (first or last):
        raise RuntimeError("首帧/首尾帧与多参考图(--ref)是互斥模式,不能同时传")
    if refs and len(refs) > MAX_VIDEO_REFS:
        raise RuntimeError(f"参考图最多 {MAX_VIDEO_REFS} 张,收到 {len(refs)}")
    if audio_refs and len(audio_refs) > MAX_AUDIO_REFS:
        raise RuntimeError(f"参考音频最多 {MAX_AUDIO_REFS} 段,收到 {len(audio_refs)}")
    if audio_refs:
        # audioref_total_le_15s 前置机检(§7A):总时长超限方舟必拒,提交前拦下并给出修法
        durs = [_audio_duration_s(p) for p in audio_refs]
        if any(d is None for d in durs):
            print("[genmedia] 无法实测参考音频时长(ffprobe 不可用?),"
                  f"跳过 audioref_total_le_15s 预检(方舟硬限总时长 {MAX_AUDIO_TOTAL_S}s)",
                  file=sys.stderr)
        elif sum(durs) > MAX_AUDIO_TOTAL_S:
            detail = "、".join(f"{Path(p).name}={d:.1f}s"
                               for p, d in zip(audio_refs, durs))
            raise RuntimeError(
                f"参考音频总时长 {sum(durs):.1f}s 超过方舟硬限 {MAX_AUDIO_TOTAL_S}s"
                f"(audioref_total_le_15s,§7A):{detail}。"
                "请截短样本后重试(voiceprint 规格 ≤5s/段,§8A;可用 "
                "ffmpeg -i in.mp3 -t 4.9 -c copy out.mp3 截断)")
    if video_refs:
        if not is_v2:
            raise RuntimeError("参考视频(--ref-video,V2V 编辑/延长)仅 Seedance 2.0 系列支持")
        if first or last:
            raise RuntimeError("参考视频(--ref-video)与首帧/尾帧是互斥模式,不能同时传")
        if len(video_refs) > MAX_VIDEOIN_REFS:
            raise RuntimeError(f"参考视频最多 {MAX_VIDEOIN_REFS} 个,收到 {len(video_refs)}")
        for v in video_refs:
            p = Path(v)
            if p.is_file() and p.stat().st_size > MAX_VIDEOIN_BYTES:
                raise RuntimeError(f"参考视频 {v} 超过 {MAX_VIDEOIN_BYTES // 1024 // 1024}MB"
                                   "(方舟参考视频单文件上限),请先压缩")
    text = prompt
    if resolution:
        text += f" --resolution {resolution}"
    if duration:
        if is_v2:
            # Seedance 2.0 只收 [4,15] 整数或 -1(模型自定时长)
            d = int(round(duration))
            if d != duration:
                print(f"[genmedia] Seedance 2.0 时长需整数,{duration} 取整为 {d}", file=sys.stderr)
            if d != -1 and not 4 <= d <= 15:
                raise RuntimeError(f"Seedance 2.0 时长须在 [4,15] 秒或 -1,收到 {d}")
            text += f" --dur {d}"
        else:
            text += f" --dur {duration:g}"
    if aspect:
        text += f" --ratio {aspect}"
    if seed is not None:
        if is_v2:
            print("[genmedia] Seedance 2.0 不支持 seed,已忽略", file=sys.stderr)
        else:
            text += f" --seed {seed}"
    content = [{"type": "text", "text": text}]
    for path, role in ((first, "first_frame"), (last, "last_frame")):
        if path:
            content.append({"type": "image_url", "role": role,
                            "image_url": {"url": to_url(path)}})
    for path in refs or []:
        content.append({"type": "image_url", "role": "reference_image",
                        "image_url": {"url": to_url(path)}})
    for path in video_refs or []:
        content.append({"type": "video_url", "role": "reference_video",
                        "video_url": {"url": video_to_url(path)}})
    for path in audio_refs or []:
        content.append({"type": "audio_url", "role": "reference_audio",
                        "audio_url": {"url": to_url(path)}})
    body = {"model": cfg["model"], "content": content}
    if gen_audio is not None:
        body["generate_audio"] = bool(gen_audio)
    if want_last_frame:
        body["return_last_frame"] = True
    return body


def _find_recent_ark_task(tasks_url, headers, since_ts: float, duration=None) -> str:
    """提交查重:在方舟任务列表中找疑似"刚由本次提交建成"的任务。

    创建接口无幂等 token,弱网下响应丢包会让客户端误判失败;盲目重试=重复计费
    (前科:ep01 grp031 重复计费)。匹配口径:created_at 落在本次提交时刻之后
    (容忍 30s 时钟偏差)且时长一致(可得时)。并行多路提交且同秒同时长时存在
    极小概率误配,窗口已尽量收紧。"""
    for _ in range(3):
        try:
            d = _get_json(f"{tasks_url}?page_size=10", headers)
            for t in (d.get("items") or []):
                if (t.get("created_at") or 0) < since_ts - 30:
                    continue
                tdur = t.get("duration") or (t.get("content") or {}).get("duration")
                if duration and tdur and int(round(duration)) != int(tdur):
                    continue
                return t.get("id") or ""
            return ""
        except Exception:
            time.sleep(5)
    return ""


def _record_video_usage(output, task_id, cfg, usage, resolution, duration):
    """成功生成后把方舟查询接口返回的 usage 落盘,双写:
    ① 同名 .meta.json 的 usage 字段(合并写,已有内容保留;下游 agent 重写 meta 时应保留该字段)
    ② 输出目录 usage_ledger.jsonl 追加一行累计台账——重roll/覆盖/删档都不丢历史,
       是分集 token 消耗统计(API videos preview)的权威数据源。
    落盘失败只告警不中断:视频本体已保存,计费记录不应影响产出。"""
    rec = {"file": Path(output).name, "task_id": task_id,
           "provider": cfg.get("provider"), "model": cfg.get("model"),
           "resolution": resolution, "duration_requested_s": duration,
           "completion_tokens": usage.get("completion_tokens"),
           "total_tokens": usage.get("total_tokens"),
           "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        ledger = Path(output).parent / "usage_ledger.jsonl"
        with open(ledger, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[genmedia] usage 台账写入失败(不影响产出):{e}", file=sys.stderr)
    try:
        meta_path = Path(output).with_suffix(".meta.json")
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta["usage"] = {k: v for k, v in rec.items() if k != "file"}
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                             encoding="utf-8")
    except Exception as e:
        print(f"[genmedia] usage 写入 meta.json 失败(不影响产出):{e}", file=sys.stderr)
    print(f"[genmedia] {Path(output).name}: usage completion_tokens="
          f"{usage.get('completion_tokens')} total_tokens={usage.get('total_tokens')}"
          f"(已记 meta.json 与 usage_ledger.jsonl)", file=sys.stderr, flush=True)


def _video_ark(cfg, prompt, first, last, duration, resolution, aspect, seed, output,
               refs=None, audio_refs=None, gen_audio=None, return_last_frame="",
               video_refs=None):
    tasks_url = f"{_ark_base(cfg)}/contents/generations/tasks"
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    body = _ark_video_body(cfg, prompt, first, last, duration, resolution, aspect, seed,
                           refs, audio_refs, gen_audio, bool(return_last_frame),
                           video_refs=video_refs, video_to_url=_storage_upload_url)
    submit_ts = time.time()
    try:
        task = _post_json(tasks_url, body, headers)
        tid = task.get("id")
    except urllib.error.HTTPError:
        raise                     # 4xx/5xx 是明确失败(审核/参数/配额),不查重,照常抛出
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # 网络层异常:任务可能已在方舟建成,先查重再定失败,避免上层重试重复计费
        print(f"[genmedia] 提交响应异常({e}),查任务列表核对是否已建成…",
              file=sys.stderr, flush=True)
        tid = _find_recent_ark_task(tasks_url, headers, submit_ts, duration)
        if not tid:
            raise RuntimeError(f"方舟提交失败且任务列表未见新任务:{e}") from e
        print(f"[genmedia] 方舟侧已存在本次提交的任务 {tid},转入轮询(未重复提交)",
              file=sys.stderr, flush=True)
    if not tid:
        raise RuntimeError(f"方舟视频任务创建失败:{json.dumps(task)[:400]}")
    print(f"[genmedia] 任务已创建 {tid} → {Path(output).name}", file=sys.stderr, flush=True)
    started = time.time()
    deadline = started + VIDEO_TIMEOUT
    last_status, last_beat = "", started
    while time.time() < deadline:
        time.sleep(VIDEO_POLL_INTERVAL)
        try:
            st = _get_json(f"{tasks_url}/{tid}", headers)
        except Exception as e:
            # 轮询瞬时失败不中止:任务已在方舟运行,中止会诱发上层重试重复计费
            print(f"[genmedia] 轮询失败({e}),{VIDEO_POLL_INTERVAL}s 后重试",
                  file=sys.stderr, flush=True)
            continue
        status = st.get("status")
        # 进度心跳:状态变化即报,同状态每 60s 报一次(方舟查询接口无百分比字段,只能报状态+等待时长)
        now = time.time()
        if status != last_status or now - last_beat >= 60:
            print(f"[genmedia] {Path(output).name}: {status},已等待 {int(now - started)}s",
                  file=sys.stderr, flush=True)
            last_status, last_beat = status, now
        if status == "succeeded":
            c = st.get("content") or {}
            url = c.get("video_url")
            if not url:
                raise RuntimeError(f"方舟任务成功但无 video_url:{json.dumps(st)[:400]}")
            saved = _save(_request(url, timeout=600), output)
            usage = st.get("usage") or {}
            if usage.get("completion_tokens") or usage.get("total_tokens"):
                _record_video_usage(output, tid, cfg, usage, resolution, duration)
            else:
                print("[genmedia] 任务成功但查询响应无 usage 明细,本次未落计费记录",
                      file=sys.stderr)
            lf_url = c.get("last_frame_url")
            if return_last_frame:
                if lf_url:
                    _save(_request(lf_url, timeout=600), return_last_frame)
                else:
                    print("[genmedia] 已请求 return_last_frame 但响应无 last_frame_url",
                          file=sys.stderr)
            return saved
        if status in ("failed", "cancelled"):
            raise RuntimeError(f"方舟视频生成失败:{json.dumps(st.get('error') or st)[:400]}")
    raise RuntimeError(f"方舟视频超时({VIDEO_TIMEOUT}s),task={tid}")


def _video_comfyui(cfg, prompt, first, last, duration, resolution, aspect, seed, output):
    base = cfg["url"].rstrip("/")
    tokens = {"PROMPT": prompt, "NEGATIVE": "", "SEED": seed,
              "DURATION": duration or 5, "WIDTH": 0, "HEIGHT": 0,
              "CHECKPOINT": cfg.get("checkpoint") or ""}
    if aspect in ASPECT_SIZES:
        tokens["WIDTH"], tokens["HEIGHT"] = ASPECT_SIZES[aspect]
    if first:
        tokens["FIRST_FRAME"] = _comfy_upload(base, first)
    if last:
        tokens["LAST_FRAME"] = _comfy_upload(base, last)
    wf = _comfy_workflow(cfg, tokens, "video")
    return _comfy_run(base, wf, output, want_video=True)


# ---------------- 音乐:OpenRouter(Lyria 3 系列) ----------------
# OpenRouter 音频输出走 chat completions:modalities ["text","audio"],必须流式,
# 音频以 base64 分块经 delta.audio.data 返回(docs/guides/overview/multimodal/audio)。

MUSIC_TIMEOUT = 600
MUSIC_FORMATS = {".mp3": "mp3", ".wav": "wav", ".flac": "flac", ".opus": "opus"}


def _music_openrouter(cfg, prompt, output):
    fmt = MUSIC_FORMATS.get(Path(output).suffix.lower(), "mp3")
    body = {"model": cfg["model"],
            "messages": [{"role": "user", "content": prompt}],
            "modalities": ["text", "audio"],
            "audio": {"voice": "alloy", "format": fmt},
            "stream": True}
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {cfg['api_key']}"})
    chunks, err_tail = [], ""
    try:
        with urllib.request.urlopen(req, timeout=MUSIC_TIMEOUT) as r:
            for raw in r:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                try:
                    evt = json.loads(line[6:])
                except ValueError:
                    continue
                if evt.get("error"):
                    raise RuntimeError(f"OpenRouter 音乐生成失败:{json.dumps(evt['error'])[:400]}")
                for ch in evt.get("choices") or []:
                    audio = (ch.get("delta") or {}).get("audio") or {}
                    if audio.get("data"):
                        chunks.append(base64.b64decode(audio["data"]))
                    err_tail = (ch.get("delta") or {}).get("content") or err_tail
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} 音乐生成失败:{e.read().decode('utf-8', 'replace')[:400]}") from e
    if not chunks:
        raise RuntimeError(f"OpenRouter 未返回音频(model={cfg['model']}):{err_tail[:200] or '空响应'}")
    return _save(b"".join(chunks), output)


# ---------------- 音乐:ElevenLabs(POST /v1/music,Eleven Music) ----------------
# body {prompt, model_id, music_length_ms?, force_instrumental};响应为音频字节流。
# music_length_ms ∈ [3000, 600000],省略则模型按 prompt 自定时长。

EL_MUSIC_FORMATS = {".mp3": "mp3_44100_192", ".opus": "opus_48000_128"}


def _music_elevenlabs(cfg, prompt, output, duration_s=None):
    fmt = EL_MUSIC_FORMATS.get(Path(output).suffix.lower())
    if not fmt:
        raise RuntimeError("ElevenLabs 音乐输出仅支持 .mp3 / .opus 扩展名")
    body = {"prompt": prompt, "model_id": cfg["model"],
            "force_instrumental": bool(cfg.get("force_instrumental", True))}
    if duration_s:
        body["music_length_ms"] = max(3000, min(600000, round(duration_s * 1000)))
    data = _request(f"https://api.elevenlabs.io/v1/music?output_format={fmt}",
                    json.dumps(body).encode(),
                    {"Content-Type": "application/json", "xi-api-key": cfg["api_key"]},
                    timeout=MUSIC_TIMEOUT)
    if not data:
        raise RuntimeError(f"ElevenLabs 音乐返回空音频(model={cfg['model']})")
    if data[:1] == b"{":
        raise RuntimeError(f"ElevenLabs 音乐生成失败:{data.decode('utf-8', 'replace')[:400]}")
    return _save(data, output)


# ---------------- TTS 旁白:OpenRouter(/api/v1/audio/speech) ----------------
# OpenAI 兼容 Speech 接口:POST 后直接返回原始音频字节流(非 JSON)。
# response_format 仅 mp3 / pcm;OpenAI 系模型可经 provider.options.openai.instructions
# 控制语气情绪(如 "沉稳的纪录片旁白语气")。

TTS_TIMEOUT = 300


def _tts_openrouter(cfg, text, output, voice, speed, instructions):
    fmt = "mp3" if Path(output).suffix.lower() == ".mp3" else "pcm"
    body = {"model": cfg["model"], "input": text,
            "voice": voice or cfg.get("voice") or "eve",
            "response_format": fmt}
    if speed:
        body["speed"] = speed
    if instructions:
        body["provider"] = {"options": {"openai": {"instructions": instructions}}}
    data = _request("https://openrouter.ai/api/v1/audio/speech",
                    json.dumps(body).encode(),
                    {"Content-Type": "application/json",
                     "Authorization": f"Bearer {cfg['api_key']}"},
                    timeout=TTS_TIMEOUT)
    if not data:
        raise RuntimeError(f"OpenRouter TTS 返回空音频(model={cfg['model']})")
    if data[:1] == b"{":  # 错误时返回 JSON 而非音频
        raise RuntimeError(f"OpenRouter TTS 失败:{data.decode('utf-8', 'replace')[:400]}")
    return _save(data, output)


# ---------------- TTS:火山引擎 豆包语音(openspeech v3 单向流式) ----------------
# 凭证是新版语音技术控制台「API Key 管理」的 API Key(X-Api-Key 单头鉴权,与方舟
# ARK Key 不同体系;旧版 App ID + Access Token 双头已废弃)。
# X-Api-Resource-Id 即模型档(seed-tts-2.0 / seed-tts-1.0 / 克隆 seed-icl-2.0);
# 响应为 NDJSON:每行 {"code":0,"data":"<base64 音频分片>"},结束行 code=20000000。

def _tts_volcengine(cfg, text, output, voice, speed, instructions):
    api_key = str(cfg.get("api_key") or "").strip()
    if not api_key:
        raise RuntimeError("火山 TTS 未配置 API Key(新版语音技术控制台「API Key 管理」"
                           "创建,「🎨 生成模型」页 TTS → 火山引擎 填入)")
    speaker = voice or cfg.get("voice") or ""
    if not speaker:
        raise RuntimeError("火山 TTS 未指定音色:--voice 传 speaker 名,"
                           "或在「🎨 生成模型」页配置默认音色(见豆包语音「音色列表」文档)")
    resource = "seed-icl-2.0" if speaker.startswith("S_") else (cfg["model"] or "seed-tts-2.0")
    audio_params = {"format": "mp3" if Path(output).suffix.lower() == ".mp3" else "pcm",
                    "sample_rate": 24000}
    if speed and speed != 1.0:
        # speech_rate ∈ [-50,100]:0=常速,100=2 倍速,-50=0.5 倍速
        audio_params["speech_rate"] = max(-50, min(100, round((speed - 1) * 100)))
    req_params = {"text": text, "speaker": speaker, "audio_params": audio_params}
    if instructions:
        req_params["additions"] = json.dumps(
            {"context_texts": [instructions]}, ensure_ascii=False)
    data = _request("https://openspeech.bytedance.com/api/v3/tts/unidirectional",
                    json.dumps({"user": {"uid": "videoagents"},
                                "req_params": req_params}).encode(),
                    {"Content-Type": "application/json",
                     "X-Api-Key": api_key,
                     "X-Api-Resource-Id": resource},
                    timeout=TTS_TIMEOUT)
    chunks = []
    for line in data.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        code = obj.get("code", 0)
        if code == 0 and obj.get("data"):
            chunks.append(base64.b64decode(obj["data"]))
        elif code not in (0, 20000000):
            raise RuntimeError(f"火山 TTS 失败(code={code}):"
                               f"{obj.get('message') or json.dumps(obj, ensure_ascii=False)[:300]}")
    if not chunks:
        raise RuntimeError(f"火山 TTS 返回空音频(speaker={speaker},resource={resource}):"
                           f"{data.decode('utf-8', 'replace')[:300]}")
    return _save(b"".join(chunks), output)


# ---------------- TTS:ElevenLabs(POST /v1/text-to-speech/{voice_id}) ----------------
# voice 传 voice_id(Voice Library 的音色须先加入自己账号,「生成模型」页可搜索+一键加入);
# instructions 不支持(v3 系模型的情绪走文本内 [audio tag],由上游在 text 里写)。

def _tts_elevenlabs(cfg, text, output, voice, speed, instructions):
    vid = voice or cfg.get("voice") or ""
    if not vid:
        raise RuntimeError("ElevenLabs TTS 未指定音色:--voice 传 voice_id,"
                           "或在「🎨 生成模型」页拉取/搜索音色后设为默认")
    fmt = "mp3_44100_128" if Path(output).suffix.lower() == ".mp3" else "pcm_24000"
    body = {"text": text, "model_id": cfg["model"]}
    if speed and speed != 1.0:
        body["voice_settings"] = {"speed": speed}
    if instructions:
        print("[genmedia] ElevenLabs 不支持 instructions 参数,已忽略"
              "(情绪用 v3 模型的文内 [audio tag])", file=sys.stderr)
    data = _request(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
                    f"?output_format={fmt}",
                    json.dumps(body).encode(),
                    {"Content-Type": "application/json", "xi-api-key": cfg["api_key"]},
                    timeout=TTS_TIMEOUT)
    if not data:
        raise RuntimeError(f"ElevenLabs TTS 返回空音频(model={cfg['model']},voice={vid})")
    if data[:1] == b"{":
        raise RuntimeError(f"ElevenLabs TTS 失败:{data.decode('utf-8', 'replace')[:400]}")
    return _save(data, output)


# ---------------- 对外 API ----------------

def generate_image(prompt: str, output: str, negative: str = "",
                   refs: list[str] | None = None, aspect: str = "",
                   size: str = "", seed: int | None = None) -> str:
    """生成一张图,返回保存的绝对路径。渠道/模型按 data/.videoagents/genconfig.json。"""
    _forbid_dispatch_layer("图像")
    cfg = get_config("image")
    if size:
        width, height = (int(x) for x in size.lower().split("x"))
    else:
        width, height = ASPECT_SIZES.get(aspect or "16:9", ASPECT_SIZES["16:9"])
    seed = seed if seed is not None else random.randint(1, 2**31)
    if cfg["provider"] == "openrouter":
        return _save(_image_openrouter(cfg, prompt, negative, refs, width, height, seed), output)
    if cfg["provider"] == "ideogram":
        return _save(_image_ideogram(cfg, prompt, negative, refs, width, height, seed), output)
    if cfg["provider"] in ("volcengine", "byteplus"):
        return _save(_image_ark(cfg, prompt, negative, refs, width, height, seed), output)
    return _image_comfyui(cfg, prompt, negative, refs, width, height, seed, output)


def _resolution_gate(resolution: str) -> str:
    """API 派单链路的视频分辨率闸门:按项目输出设置的草稿/成片两档强制约束,
    防止 Agent 工单跑偏高分辨率烧钱(分辨率与费用平方级相关)。
    仅允许草稿档与成片档;空值取草稿档;其余一律压到草稿档并记 stderr。
    非服务派单环境(无 VIDEOAGENTS_PROJECT)不干预,手工调用照传。"""
    proj = os.environ.get("VIDEOAGENTS_PROJECT", "")
    if not proj:
        return resolution
    try:
        st = json.loads((DATA_DIR / "projects" / proj / "settings.json").read_text())
        out = st.get("output") or {}
    except Exception:
        out = {}
    draft = out.get("draft_resolution") or "480p"
    final = out.get("final_resolution") or "480p"
    if not resolution:
        print(f"[genmedia] 未指定分辨率,按「输出设置」草稿档 {draft}", file=sys.stderr)
        return draft
    if resolution in (draft, final):
        return resolution
    print(f"[genmedia] 分辨率 {resolution} 不在「输出设置」允许档(草稿 {draft}/成片 {final}),"
          f"已强制压到草稿档 {draft}", file=sys.stderr)
    return draft


def generate_video(prompt: str, output: str, first_frame: str = "",
                   last_frame: str = "", duration: float | None = None,
                   resolution: str = "", aspect: str = "",
                   seed: int | None = None, refs: list[str] | None = None,
                   audio_refs: list[str] | None = None,
                   generate_audio: bool | None = None,
                   return_last_frame: str = "",
                   video_refs: list[str] | None = None) -> str:
    """生成一段视频,返回保存的绝对路径。渠道/模型按 data/.videoagents/genconfig.json。

    refs/audio_refs/generate_audio/return_last_frame 为多模态参考模式(Seedance 2.0
    多镜头组生成)专用,仅火山引擎/BytePlus 渠道支持;refs 与 first/last_frame 互斥。
    video_refs 为 V2V 编辑/延长模式(Seedance 2.0):传待修改/待延长的原视频
    (≤3 个,单个 2-15s 且总时长 ≤15s),prompt 用「视频n/图片n」序号引用素材,
    典型用法是局部穿帮修复(定向修改,其余画面保持不变);与 first/last_frame 互斥。
    """
    _forbid_dispatch_layer("视频")
    cfg = get_config("video")
    resolution = _resolution_gate(resolution)
    seed = seed if seed is not None else random.randint(1, 2**31)
    if cfg["provider"] in ("volcengine", "byteplus"):
        return _video_ark(cfg, prompt, first_frame, last_frame, duration,
                          resolution, aspect, seed, output,
                          refs, audio_refs, generate_audio, return_last_frame,
                          video_refs)
    if refs or audio_refs or video_refs or return_last_frame or generate_audio is not None:
        raise RuntimeError(f"渠道 {cfg['provider']} 不支持多参考图/参考音频/参考视频"
                           "/return_last_frame/generate_audio,"
                           "请在生成模型页切换到火山引擎/BytePlus 或改用首尾帧模式")
    fn = {"openrouter": _video_openrouter, "comfyui": _video_comfyui}[cfg["provider"]]
    return fn(cfg, prompt, first_frame, last_frame, duration, resolution, aspect, seed, output)


def generate_tts(text: str, output: str, voice: str = "", speed: float | None = None,
                 instructions: str = "") -> str:
    """TTS 旁白/语音合成,返回保存的绝对路径。渠道/模型按 data/.videoagents/genconfig.json 的 tts 段。

    输出 .mp3 为 mp3,其余扩展名为 pcm(24kHz 裸流,需自行封装)。voice 缺省用配置页
    默认音色(openrouter=音色名 / volcengine=speaker 名 / elevenlabs=voice_id);
    instructions:openrouter 仅 OpenAI 系模型生效,volcengine 注入 context_texts
    情绪指令,elevenlabs 不支持(忽略)。
    """
    _forbid_dispatch_layer("TTS 语音")
    cfg = get_config("tts")
    fn = {"openrouter": _tts_openrouter, "volcengine": _tts_volcengine,
          "elevenlabs": _tts_elevenlabs}.get(cfg["provider"])
    if not fn:
        raise RuntimeError(f"TTS 不支持渠道 {cfg['provider']}"
                           "(可选 openrouter / volcengine / elevenlabs)")
    return fn(cfg, text, output, voice, speed, instructions)


def generate_music(prompt: str, output: str, duration_s: float | None = None) -> str:
    """生成一段音乐(BGM),返回保存的绝对路径。渠道/模型按 data/.videoagents/genconfig.json 的 music 段。

    输出格式按 output 扩展名(openrouter:mp3/wav/flac/opus;elevenlabs:mp3/opus)。
    duration_s 仅 elevenlabs 生效(music_length_ms,3–600s;省略=模型按 prompt 自定);
    openrouter 时长由模型决定:Lyria 3 Pro 完整歌曲,Lyria 3 Clip 30s 片段/Loop。
    """
    _forbid_dispatch_layer("音乐")
    cfg = get_config("music")
    if cfg["provider"] == "elevenlabs":
        return _music_elevenlabs(cfg, prompt, output, duration_s)
    if cfg["provider"] != "openrouter":
        raise RuntimeError(f"音乐生成不支持渠道 {cfg['provider']}(可选 openrouter / elevenlabs)")
    if duration_s:
        print("[genmedia] openrouter 音乐渠道不支持 --duration,已忽略(时长由模型决定)",
              file=sys.stderr)
    return _music_openrouter(cfg, prompt, output)


# ---------------- CLI ----------------

def _check_id_digits(*paths):
    """grpsh_id_3digits 前置机检:输出路径里的组/镜编号必须三位零填充
    (grp001/sh001,与 shot_list 的 group_id/shot_id 逐字符一致,WORKFLOW.md §7A)。
    模型写盘时位数偶发漂移(grp01.mp4 对 group_id=grp001),webui 预览与后续机检
    全部对不上号,提交前拦下并给出正名。"""
    for path in paths:
        if not path:
            continue
        p = Path(path)
        for part in (*p.parent.parts, p.stem):
            m = re.match(r"^(grp|sh)(\d+)", part)
            if m and len(m.group(2)) != 3 and int(m.group(2)) < 1000:
                fixed = f"{m.group(1)}{int(m.group(2)):03d}{part[m.end():]}"
                raise RuntimeError(
                    f"输出路径段 {part!r} 编号位数不合规(grpsh_id_3digits):"
                    f" grp/sh 编号固定三位零填充,应为 {fixed!r}(完整路径 {path})")


def _cmd_info(_args):
    for kind in ("image", "video", "music", "tts"):
        try:
            cfg = get_config(kind)
            desc = f"model={cfg['model']}" if cfg["provider"] != "comfyui" \
                else f"url={cfg['url']} workflow={cfg.get('workflow') or '(内置默认)'} checkpoint={cfg.get('checkpoint') or '-'}"
            print(f"{kind:5s} → {cfg['provider']:10s} {desc}")
        except RuntimeError as e:
            print(f"{kind:5s} → ⚠ {e}")


def _cmd_image(args):
    _check_id_digits(args.output)
    if args.dry_run:
        cfg = get_config("image")
        print(f"[dry-run] image via {cfg['provider']}"
              f" model={cfg.get('model') or cfg.get('checkpoint') or '-'} → {args.output}")
        return
    outs = []
    for i in range(args.n):
        out = args.output if args.n == 1 else \
            str(Path(args.output).with_stem(f"{Path(args.output).stem}_{i+1:02d}"))
        seed = args.seed if (args.seed is not None and args.n == 1) else None
        outs.append(generate_image(args.prompt, out, args.negative, args.ref,
                                   args.aspect, args.size, seed))
        print(f"已生成: {outs[-1]}")


def _cmd_video(args):
    _check_id_digits(args.output, args.return_last_frame)
    gen_audio = {"on": True, "off": False, "": None}[args.generate_audio]
    if args.dry_run:
        cfg = get_config("video")
        resolution = _resolution_gate(args.resolution)
        line = (f"[dry-run] video via {cfg['provider']}"
                f" model={cfg.get('model') or cfg.get('workflow') or '-'} → {args.output}")
        if cfg["provider"] in ("volcengine", "byteplus"):
            # 走真实构造逻辑校验参数组合(互斥/上限/时长),但不发请求、不内联文件
            body = _ark_video_body(dict(cfg, api_key="dry"), args.prompt,
                                   args.first_frame, args.last_frame,
                                   args.duration, resolution, args.aspect, args.seed,
                                   args.ref, args.audio_ref,
                                   gen_audio, bool(args.return_last_frame),
                                   video_refs=args.ref_video,
                                   to_url=lambda p: f"file://{p}")
            roles = [c.get("role") for c in body["content"] if c["type"] != "text"]
            line += (f"\n[dry-run] text={body['content'][0]['text'][:200]}"
                     f"\n[dry-run] roles={roles}"
                     f" generate_audio={body.get('generate_audio')}"
                     f" return_last_frame={body.get('return_last_frame')}")
        print(line)
        return
    out = generate_video(args.prompt, args.output, args.first_frame, args.last_frame,
                         args.duration, args.resolution, args.aspect, args.seed,
                         args.ref, args.audio_ref, gen_audio, args.return_last_frame,
                         video_refs=args.ref_video)
    print(f"已生成: {out}")


def _cmd_music(args):
    if args.dry_run:
        cfg = get_config("music")
        print(f"[dry-run] music via {cfg['provider']} model={cfg.get('model') or '-'}"
              f" format={MUSIC_FORMATS.get(Path(args.output).suffix.lower(), 'mp3')} → {args.output}")
        return
    out = generate_music(args.prompt, args.output, args.duration)
    print(f"已生成: {out}")


def _cmd_tts(args):
    if args.dry_run:
        cfg = get_config("tts")
        print(f"[dry-run] tts via {cfg['provider']} model={cfg.get('model') or '-'}"
              f" voice={args.voice or cfg.get('voice') or 'eve'}"
              f" format={'mp3' if Path(args.output).suffix.lower()=='.mp3' else 'pcm'} → {args.output}")
        return
    out = generate_tts(args.text, args.output, args.voice, args.speed, args.instructions)
    print(f"已生成: {out}")


def main():
    ap = argparse.ArgumentParser(description="统一图像/视频/音乐生成(渠道按 data/.videoagents/genconfig.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info", help="查看当前生效渠道与模型")

    pi = sub.add_parser("image", help="生成图像")
    pi.add_argument("--prompt", required=True)
    pi.add_argument("--output", required=True, help="输出 png 路径")
    pi.add_argument("--negative", default="")
    pi.add_argument("--aspect", default="", help="画幅,如 16:9(与 --size 二选一)")
    pi.add_argument("--size", default="", help="精确尺寸,如 1280x720")
    pi.add_argument("--ref", nargs="+", default=None, help="参考图路径(可多张)")
    pi.add_argument("--n", type=int, default=1, help="候选张数(>1 时文件名加 _01.. 后缀)")
    pi.add_argument("--seed", type=int, default=None)
    pi.add_argument("--dry-run", action="store_true")

    pv = sub.add_parser("video", help="生成视频")
    pv.add_argument("--prompt", required=True)
    pv.add_argument("--output", required=True, help="输出 mp4 路径")
    pv.add_argument("--first-frame", default="", help="首帧图路径(图生视频)")
    pv.add_argument("--last-frame", default="", help="尾帧图路径")
    pv.add_argument("--duration", type=float, default=None,
                    help="时长(秒);Seedance 2.0 为 [4,15] 整数或 -1(模型自定)")
    pv.add_argument("--resolution", default="", help="如 720p / 1080p")
    pv.add_argument("--aspect", default="", help="画幅,如 16:9")
    pv.add_argument("--seed", type=int, default=None)
    pv.add_argument("--ref-video", nargs="+", default=None,
                    help="参考视频路径(≤3 个;V2V 编辑/延长,Seedance 2.0 专用,"
                         "prompt 用「视频n」序号引用;与首尾帧互斥)")
    pv.add_argument("--ref", nargs="+", default=None,
                    help="参考图路径(可多张,≤9;多模态参考/多镜头组模式,与首尾帧互斥)")
    pv.add_argument("--audio-ref", nargs="+", default=None,
                    help="参考音频路径(≤3 段,总时长 ≤15s;如角色 TTS 音色样本)")
    pv.add_argument("--generate-audio", choices=["on", "off", ""], default="",
                    help="原生音频开关(Seedance 2.0;缺省沿用模型默认 on)")
    pv.add_argument("--return-last-frame", default="",
                    help="尾帧 PNG 落盘路径(用于组间续接锚)")
    pv.add_argument("--dry-run", action="store_true")

    pt = sub.add_parser("tts", help="TTS 旁白/语音合成")
    pt.add_argument("--text", required=True, help="要合成的文本(旁白/台词)")
    pt.add_argument("--output", required=True, help="输出音频路径(.mp3;其他扩展名为 pcm 裸流)")
    pt.add_argument("--voice", default="", help="音色(缺省用配置页默认;各模型音色不同)")
    pt.add_argument("--speed", type=float, default=None, help="语速倍率(可选)")
    pt.add_argument("--instructions", default="", help="语气/情绪指令(仅 OpenAI 系模型生效)")
    pt.add_argument("--dry-run", action="store_true")

    pm = sub.add_parser("music", help="生成音乐(BGM)")
    pm.add_argument("--prompt", required=True, help="英文音乐描述:风格/情绪/乐器/节奏(Lyria Pro 可含歌词)")
    pm.add_argument("--output", required=True, help="输出音频路径(.mp3/.wav/.flac/.opus;elevenlabs 仅 .mp3/.opus)")
    pm.add_argument("--duration", type=float, default=None,
                    help="目标时长秒(仅 elevenlabs 渠道生效,3–600;省略=模型按 prompt 自定)")
    pm.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()
    try:
        {"info": _cmd_info, "image": _cmd_image, "video": _cmd_video,
         "music": _cmd_music, "tts": _cmd_tts}[args.cmd](args)
    except RuntimeError as e:
        print(f"生成失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
