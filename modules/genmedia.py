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

  python3 modules/genmedia.py upscale --input in.mp4 --output out_2k.mp4 \
      [--prompt "<该组原始 video_prompt>"] [--source-task-id <任务id>] \
      [--resolution 1080p] [--aspect 16:9] [--seed 1234] [--dry-run]

  超分:视频配置生效渠道为 ComfyUI 时走 SeedVR2,复用「生成模型」页视频 ComfyUI
  的连接配置:本地/Comfy Cloud 固定使用 comfy/video-upscale-seedvr2-api.json;
  RunningHub 运行方式用 RunningHub 渠道选中的云端超分工作流(无占位符时直绑源视频
  加载节点 + SeedVR2Preprocess 上游缩放节点的目标边长/宽高 + 采样器种子;按边缩放
  的模板画幅跟随源视频,源视频 ≤30MB)。目标尺寸由 --resolution/--aspect 指定
  (缺省取项目成片档/16:9),fps、时长和音轨跟随源视频。
  其他视频渠道走 MiniMax Regenerate-2K:固定输出 2K,凭证共用「生成模型」页视频
  MiniMax 的 Key/接口区域(环境变量 MINIMAX_API_KEY 兜底)。base_video 模式的源视频
  须为 MiniMax-H3 768P 直出成片规格(24fps、含音轨、宽高均被 32 整除、面积≤768×1344、
  107-362 帧≈4-15s;提交前 ffprobe 预检,>45MB 走对象存储预签名 URL);或
  --source-task-id 传 7 天内 succeeded 的 MiniMax 生成任务 id 免传源视频。
  MiniMax 按 output_seconds 计费,SeedVR2 按 ComfyUI 配置计费。

  python3 modules/genmedia.py music --prompt "<音乐描述>" --output bgm.mp3 [--dry-run]
  python3 modules/genmedia.py tts --text "<旁白文本>" --output narr.mp3 \
      [--character CHAR-0001] [--variant child] [--project demo] \
      [--voice <音色;仅云渠道,旁白缺省用配置页默认音色>] \
      [--speed 1.0] [--instructions "<语气/情绪指令>"] [--dry-run]

Python:
  from modules.genmedia import generate_image, generate_video, generate_upscale, \
      generate_music, generate_tts, get_config

渠道:
  图像: openrouter(chat completions, modalities=image) / ideogram
        / volcengine(方舟 images/generations,Seedream 系列)
        / byteplus(海外 ModelArk,与方舟同构 API)
        / minimax(POST /v1/image_generation,Image-01;参考图仅 1 张 subject_reference)
        / comfyui(本地 / Comfy Cloud / RunningHub 云托管)
  视频: openrouter(POST /v1/videos 异步任务) / volcengine(方舟 contents/generations/tasks)
        / byteplus(海外 ModelArk,与方舟同构 API)
        / minimax(POST /v2/video_generation 异步任务,MiniMax-H3;分辨率仅 768P/2K
        两档,--resolution 项目档位自动就近映射;时长 [4,15] 整数秒;支持首尾帧/
        多参考图(≤9)/参考音视频;原生音画同生,不支持 --seed 与 --generate-audio off)
        / comfyui(本地/Comfy Cloud/RunningHub,需配置 API 格式工作流 JSON;
        RunningHub 用工作区保存的云端工作流,占位符约定与本地一致)
  超分: seedvr2(ComfyUI SeedVR2 视频超分;复用视频 ComfyUI 配置,本地/Cloud 固定
        工作流 video-upscale-seedvr2-api.json,RunningHub 用选中的云端超分工作流)
        / minimax(POST /v2/video_regeneration,
        Regenerate-2K 异步任务;模型固定 MiniMax-H3,分辨率固定 2K)
  音乐: openrouter(chat completions 流式, modalities=audio;Lyria 3 Pro 完整歌曲 /
        Lyria 3 Clip 30s 片段;输出格式按扩展名 mp3/wav/flac/opus)
        / elevenlabs(POST /v1/music,Eleven Music v1/v2;--duration 指定时长 3–600s,
        省略=模型自定;force_instrumental 由「生成模型」页配置,默认纯音乐;仅 .mp3/.opus)
        / minimax(POST /v1/music_generation,Music 3.0/2.6;仅 .mp3/.wav,--duration 忽略;
        force_instrumental 由「生成模型」页配置,默认纯音乐,关闭时按 prompt 自动写词演唱)
        / comfyui(本地/云端,需配置 API 格式工作流 JSON;推荐 ACE-Step,见 comfy/music-ace-step-v1-api.md)
  TTS : openrouter(POST /api/v1/audio/speech,原始字节流;.mp3 或 pcm 裸流;
        Grok Voice / MAI-Voice-2 / Voxtral / Kokoro 等,音色名因模型而异)
        / volcengine(豆包语音 openspeech v3 单向流式,Doubao-Seed-TTS 2.0;
        凭证=新版语音技术控制台「API Key 管理」的 API Key,非方舟 ARK Key;
        音色为 speaker 名(控制台「音色库」),S_ 开头的克隆音色自动切 seed-icl-2.0 资源)
        / minimax(POST /v1/t2a_v2,Speech 2.8 系列;音色为 voice_id,
        可在「生成模型」页拉取音色库选择)
        / elevenlabs(POST /v1/text-to-speech/{voice_id};音色为 voice_id,
        可在「生成模型」页从 Voice Library 搜索并一键加入账号)
        / comfyui(本地/云端,需配置 API 格式工作流 JSON;推荐 IndexTTS-2;
        根据角色设定从内置音色目录自动选择参考音频,远端音频按需下载缓存)

  minimax 各能力共用「接口区域」配置(api_base):海外版 api.minimax.io 与
  国内版 api.minimaxi.com 账号与 Key 不互通,须与 Key 来源平台一致。

ComfyUI 自定义工作流占位符(文本替换):
  字符串位: "{{PROMPT}}" "{{NEGATIVE}}" "{{CHECKPOINT}}" "{{FIRST_FRAME}}" "{{LAST_FRAME}}"
            "{{TEXT}}" "{{LYRICS}}" "{{VOICE}}" "{{REF_AUDIO}}"
  数值位: "{{WIDTH}}" "{{HEIGHT}}" "{{SEED}}" "{{DURATION}}" "{{FRAMES}}"
          "{{LTX_FRAMES}}" "{{SPEED}}"
  Seedance 云工作流(ByteDance2ReferenceNode,专用分支注入):"{{RESOLUTION}}"
          "{{RATIO}}" "{{GENERATE_AUDIO}}";参考图不走占位符,动态挂
          model.reference_images.image_N(1 起编号)
  占位符独占整个字符串时会保留注入值的类型；旧版不加引号的数值模板仍兼容。
  FRAMES 按 16fps 将 DURATION 换算为 Wan 视频所需的 4n+1 帧数。
  LTX_FRAMES 按 24fps 换算为 LTX 视频所需的 8n+1 帧数。
  音乐/TTS 以 SaveAudio/SaveAudioMP3 落盘;DURATION 为秒,TEXT 为 TTS 文本,
  REF_AUDIO 为已上传到 ComfyUI input 的参考音频文件名。
"""
import argparse
import base64
import gzip
import http.client
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

try:
    from modules.timbre_selector import select_timbre
except ModuleNotFoundError:  # python modules/genmedia.py ...
    from timbre_selector import select_timbre

try:  # 诊断事件旁路(设置「高级→诊断数据」,本地落盘不出网);缺席时静默跳过
    from modules import diagnostics as _diagnostics
except Exception:
    try:
        import diagnostics as _diagnostics
    except Exception:
        _diagnostics = None

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
            "elevenlabs": "ELEVENLABS_API_KEY", "minimax": "MINIMAX_API_KEY"}

ASPECT_SIZES = {"16:9": (1280, 720), "9:16": (720, 1280), "1:1": (1024, 1024),
                "4:3": (1152, 864), "3:4": (864, 1152), "21:9": (1680, 720)}

H3_DEFAULTS = {
    "unet": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
    "text_encoder": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    "video_vae": "minimax_h3_video_vae_fp16.safetensors",
    "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
    "weight_dtype": "default", "clip_device": "default",
    "sampler": "res_multistep", "scheduler": "simple", "steps": 20,
    # ref_image_size 默认 match(参考图压到与输出同像素面积,速度/成本优先);
    # 单次请求可用 --ref-image-size max 改为短边 ≤2048 不压缩直进模型(480p 草稿档
    # 下 match 会把人脸参考压到只剩几十像素,是脸部不一致的主因之一;但参考 token
    # 全程陪跑采样,max 更慢更贵,按组按需取舍)
    "ref_image_size": "match", "fps": 24,
}
H3_REFERENCE_NODE = "MiniMaxH3ReferenceToVideo"
# Comfy Cloud 的 Seedance 2.x 付费 API 节点(r2v);model 输入选版本("Seedance 2.0/2.5")
SEEDANCE_REFERENCE_NODE = "ByteDance2ReferenceNode"
LTX25_DEFAULTS = {
    "unet": "ltx-2.5-22b-distilled-transformer-bf16.safetensors",
    "text_encoder": "gemma4-12b-with-proj-ltx-2.5-bf16.safetensors",
    "video_vae": "ltx-2.5-video-vae-conv-bf16.safetensors",
    "audio_vae": "ltx-2.5-audio-vae-bf16.safetensors",
    "weight_dtype": "default", "clip_device": "default", "fps": 24,
    "cfg": 1.0, "sampler": "euler_ancestral",
    "sigmas": "1.0, 0.99375, 0.9875, 0.98125, 0.975, 0.909375, 0.725, 0.421875, 0.0",
    "tile_size": 512, "tile_overlap": 64, "temporal_size": 64,
    "temporal_overlap": 8, "first_strength": 0.85, "last_strength": 0.85,
    "ref_strength": 0.75,
}

VIDEO_POLL_INTERVAL = 10
VIDEO_TIMEOUT = 1800
COMFY_TIMEOUT = 1800
COMFY_QUEUE_SUBMIT_GRACE = 30
COMFY_QUEUE_DISAPPEAR_GRACE = 15


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
    # 「每 Agent 模型配置」的 runninghub/comfyui 与「🎨 生成模型」页的两个标签页同口径:
    # 存储都在 comfyui 段,靠 mode 区分 —— runninghub ⇒ mode=RunningHub 页所选站点(rh_site),
    # comfyui ⇒ mode=ComfyUI 页所选本地/云端(comfy_mode);影子字段缺失时按现行 mode 兜底
    force_mode = ""
    if ov in ("runninghub", "comfyui") and isinstance(cfg.get("comfyui"), dict):
        c = cfg["comfyui"]
        cur = str(c.get("mode") or "local")
        if ov == "runninghub":
            site = str(c.get("rh_site") or "")
            force_mode = site if site in RH_BASES else (cur if cur in RH_BASES else "rh_cn")
        else:
            cm = str(c.get("comfy_mode") or "")
            force_mode = cm if cm in ("local", "cloud") else (cur if cur in ("local", "cloud") else "local")
        provider = "comfyui"
    elif ov and isinstance(cfg.get(ov), dict):
        provider = ov
    pc = dict(cfg[provider])
    if force_mode:
        pc["mode"] = force_mode
    if provider == "minimax":
        # 海外/国内区域 Key 分别保存,按 api_base 归一到 api_key 供下游统一取用
        pc["api_key"] = _minimax_key(pc)
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
            body = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return body
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


# ---------------- 火山方舟 私域虚拟人像素材库(设置 → 高级 → 虚拟人像资产库) ----------------
# 台账由 services/runtime/core.py 在入库时落盘:{"assets": {<文件sha256>: {"asset_id", "status", ...}}}
AVATAR_LEDGER_PATH = RUNTIME_DIR / "avatar_assets.json"


def _avatar_asset_uri(path: str) -> str | None:
    """参考图已入方舟虚拟人像库(Active)且功能启用时返回 asset://<asset_ID>,否则 None。

    以资产 URI 提交可规避 Seedance 对含人脸参考图的审核拦截(资产入库时已过审核);
    按文件内容 sha256 匹配,与图片所在目录无关。"""
    try:
        if not (json.loads(CONFIG_PATH.read_text()).get("avatar_assets") or {}).get("enabled"):
            return None
        ledger = json.loads(AVATAR_LEDGER_PATH.read_text()).get("assets") or {}
    except Exception:
        return None
    if not ledger:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    ent = ledger.get(hashlib.sha256(p.read_bytes()).hexdigest()) or {}
    if ent.get("status") == "Active" and ent.get("asset_id"):
        return f"asset://{ent['asset_id']}"
    return None


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
# 仅域名与模型 ID 前缀不同(doubao-seedream-* vs seedream-*/dreamina-seedance-*;
# Seedance 2.5 为 doubao-seedance-2-5-260628 vs dreamina-seedance-2-5-260628)
ARK_API_BASES = {"volcengine": "https://ark.cn-beijing.volces.com/api/v3",
                 "byteplus": "https://ark.ap-southeast.bytepluses.com/api/v3"}


def _ark_base(cfg) -> str:
    return ARK_API_BASES[cfg.get("provider") or "volcengine"]


def _image_ark(cfg, prompt, negative, refs, width, height, seed):
    """返回 (图像字节, usage dict);usage 供调用方落台账,响应无 usage 时为空 dict。"""
    text = prompt + (f"\n避免出现:{negative}" if negative else "")
    body = {"model": cfg["model"], "prompt": text,
            "size": f"{width}x{height}",
            "response_format": "url", "watermark": False}
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
    usage = resp.get("usage") if isinstance(resp.get("usage"), dict) else {}
    if data[0].get("b64_json"):
        return base64.b64decode(data[0]["b64_json"]), usage
    if data[0].get("url"):
        return _request(data[0]["url"], timeout=300), usage
    raise RuntimeError(f"方舟返回格式异常:{json.dumps(data[0])[:400]}")


# ---------------- MiniMax 云端通用(图像/视频/音乐/TTS 共用) ----------------

# 海外版与国内版账号/Key 不互通,「生成模型」页「接口区域」落 api_base;两平台 API 同构
MINIMAX_DEFAULT_BASE = "https://api.minimax.io"


def _minimax_base(cfg) -> str:
    return (cfg.get("api_base") or MINIMAX_DEFAULT_BASE).rstrip("/")


def _minimax_key(pc) -> str:
    """按「接口区域」(api_base)取对应区域的 Key:国内版 minimaxi.com → api_key_cn,
    否则海外版 → api_key_io;旧版单一 api_key 兜底(环境变量兜底由调用方处理)。"""
    field = "api_key_cn" if "minimaxi.com" in str(pc.get("api_base") or "") else "api_key_io"
    return str(pc.get(field) or pc.get("api_key") or "").strip()


def _minimax_post(cfg, path: str, payload: dict, timeout: int = 300) -> dict:
    """MiniMax API POST:HTTP 200 也可能业务失败,统一校验 base_resp.status_code。"""
    resp = _post_json(_minimax_base(cfg) + path, payload,
                      {"Authorization": f"Bearer {cfg['api_key']}"}, timeout=timeout)
    base = resp.get("base_resp") or {}
    if base.get("status_code"):
        raise RuntimeError(
            f"MiniMax {path} 失败(code={base['status_code']}):"
            f"{base.get('status_msg') or json.dumps(resp, ensure_ascii=False)[:300]}")
    return resp


# ---------------- 图像:MiniMax(POST /v1/image_generation,Image-01) ----------------

def _image_minimax(cfg, prompt, negative, refs, width, height, seed):
    text = prompt + (f"\n避免出现:{negative}" if negative else "")
    body = {"model": cfg["model"], "prompt": text,
            "aspect_ratio": _closest_aspect(width, height),
            "response_format": "url"}   # Image-01 无 seed 参数,seed 入参不生效
    if refs:
        if len(refs) > 1:
            raise RuntimeError("MiniMax 图像每次仅支持 1 张参考图(subject_reference),"
                               f"收到 {len(refs)}")
        body["subject_reference"] = [{"type": "character",
                                      "image_file": _file_to_data_url(refs[0])}]
    resp = _minimax_post(cfg, "/v1/image_generation", body)
    data = resp.get("data") or {}
    urls = data.get("image_urls") or []
    if urls:
        return _request(urls[0], timeout=300)
    b64 = data.get("image_base64") or []
    if b64:
        return base64.b64decode(b64[0])
    raise RuntimeError(f"MiniMax 未返回图像(model={cfg['model']}):"
                       f"{json.dumps(resp, ensure_ascii=False)[:400]}")


# ---------------- ComfyUI 通用 ----------------

# Comfy Cloud(官方云端):与本地 ComfyUI 大体同构 API(/prompt /queue /view /upload/image
# 均在 /api 前缀下),X-API-Key 单头鉴权。已知差异(「同构」假设已破三次,勿再默认同构):
#   1) /history/{prompt_id} 不可用(404 "Use /api/jobs/{prompt_id} instead"),
#      任务状态须轮询 /jobs/{prompt_id}(_comfy_cloud_job);
#   2) 无单节点 /object_info/{class} (404),只能全量拉取且响应 gzip;
#   3) /view 返回 302 → 签名 URL。
COMFY_CLOUD_URL = "https://cloud.comfy.org/api"

# RunningHub(第三方云托管 ComfyUI):非原生同构 API,走私有 REST
# (/task/openapi/create → status 轮询 → outputs 取 fileUrl 下载);工作流须先保存在
# RunningHub 工作区并跑通,提交时以 workflow 字段整体覆盖云端模板(占位符替换与本地
# 同一套 {{TOKEN}} 约定)。JSON 任务接口同时携带 Bearer 头和 apiKey;素材上传走
# V2 /openapi/v2/media/upload/binary,只用 Bearer 头和 file multipart。.cn 与 .ai
# 各自使用对应站点与 Key,账号不互通。
RH_BASES = {"rh_cn": "https://www.runninghub.cn", "rh_ai": "https://www.runninghub.ai"}
RH_CACHE_DIR = RUNTIME_DIR / "rh_workflows"
RH_POLL_INTERVAL = 5
RH_CREATE_WAIT_TIMEOUT = 7200

# RunningHub 工作流 create 接口的可恢复背压。421/1520 是并发上限，415 是机器资源不足，
# 804 表示同 Key 仍有任务运行；1003/1010/1011 是频率或服务繁忙。其余错误必须立即暴露，
# 尤其不能把鉴权、余额、参数、内容审核错误伪装成等待。
_RH_CREATE_BACKPRESSURE = {
    415: (30, "可用机器不足"),
    421: (20, "账户并发已满"),
    804: (20, "同一 API Key 仍有任务运行"),
    1003: (10, "请求频率受限"),
    1010: (30, "服务暂不可用"),
    1011: (30, "服务繁忙"),
    1520: (20, "账户并发已满"),
}


def _comfy_is_rh(cfg) -> bool:
    """ComfyUI 渠道运行方式是否 RunningHub(rh_cn/rh_ai)。"""
    return (cfg.get("mode") or "local") in RH_BASES


def _comfy_desc(cfg) -> str:
    """comfyui 渠道的人类可读描述,必带 mode:info/dry-run 曾只打 url/workflow,
    RunningHub 模式下与「本地未配置」输出一模一样,agent 据此误判渠道而拒跑。"""
    mode = cfg.get("mode") or "local"
    if _comfy_is_rh(cfg):
        return (f"mode={mode}(RunningHub {RH_BASES[mode].split('//')[1]})"
                f" workflow_id={cfg.get('rh_workflow_id') or '(未选择)'}"
                f" instance={cfg.get('rh_instance_type') or 'standard'}")
    if mode == "cloud":
        return f"mode=cloud(Comfy Cloud) workflow={cfg.get('workflow') or '(内置默认)'}"
    return (f"mode=local url={cfg.get('url') or '-'}"
            f" workflow={cfg.get('workflow') or '(内置默认)'}"
            f" checkpoint={cfg.get('checkpoint') or '-'}")


def _rh_ctx(cfg) -> tuple[str, str, str]:
    """RunningHub 生效上下文:返回 (base, api_key, workflow_id),缺配置即报错。"""
    mode = cfg.get("mode")
    base = RH_BASES[mode]
    # .cn/.ai 账号与 Key 不互通,按站点分别保存(rh_api_key_cn/rh_api_key_ai);
    # 旧版单一 rh_api_key 兜底
    key = (str(cfg.get(f"rh_api_key_{mode[3:]}") or "").strip()
           or str(cfg.get("rh_api_key") or "").strip()
           or os.environ.get("RUNNINGHUB_API_KEY", "").strip())
    if not key:
        raise RuntimeError("RunningHub 未配置 API Key:「🎨 生成模型」页 RunningHub 渠道选"
                           "对应站点并填写(或设环境变量 RUNNINGHUB_API_KEY)")
    wf_id = str(cfg.get("rh_workflow_id") or "").strip()
    if not wf_id:
        raise RuntimeError("RunningHub 未选择云端工作流:「🎨 生成模型」页 RunningHub 渠道"
                           "粘贴工作区的工作流 ID 验证并添加后选择")
    return base, key, wf_id


def _rh_post(base: str, key: str, path: str, payload: dict, timeout: int = 300) -> dict:
    """RunningHub OpenAPI POST;网络层错误转为可读 RuntimeError,业务码由调用方判定。"""
    try:
        return _post_json(base + path, {"apiKey": key, **payload},
                          {"Authorization": f"Bearer {key}"}, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"RunningHub 服务不可达:{base}{path}") from exc


def _rh_upload(cfg, path: str) -> str:
    """通过 RunningHub V2 媒体接口上传输入，返回 ComfyUI ``fileName``。"""
    p = Path(path)
    if not p.is_file():
        raise RuntimeError(f"输入文件不存在: {path}")
    if p.stat().st_size > 30 * 1024 * 1024:
        raise RuntimeError(f"RunningHub 上传限制单文件 30MB,超限: {path}")
    base, key, _ = _rh_ctx(cfg)
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    # V2 已废弃旧 /task/openapi/upload 所需的 apiKey/fileType form 字段。
    # Key 只放 Authorization 头，避免新端点将旧字段解析为冲突凭据。
    parts = [(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
              f"filename=\"{p.name}\"\r\nContent-Type: {mime}\r\n\r\n").encode()]
    parts.append(p.read_bytes())
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    resp = json.loads(_request(base + "/openapi/v2/media/upload/binary", b"".join(parts),
                               {"Content-Type": f"multipart/form-data; boundary={boundary}",
                                "Authorization": f"Bearer {key}"}, timeout=300))
    if resp.get("code") != 0 or not (resp.get("data") or {}).get("fileName"):
        raise RuntimeError(f"RunningHub 上传失败(code={resp.get('code')}):"
                           f"{str(resp.get('message') or resp.get('msg'))[:300]}")
    return resp["data"]["fileName"]


def _rh_workflow_text(cfg) -> str:
    """RunningHub 工作流 JSON 文本:本地缓存优先(设置页验证时写入),缺失则拉取补缓存。"""
    base, key, wf_id = _rh_ctx(cfg)
    cache = RH_CACHE_DIR / f"{cfg.get('mode')}-{wf_id}.json"
    if cache.is_file():
        return cache.read_text(encoding="utf-8")
    resp = _rh_post(base, key, "/api/openapi/getJsonApiFormat", {"workflowId": wf_id})
    text = (resp.get("data") or {}).get("prompt") if resp.get("code") == 0 else None
    if not text:
        raise RuntimeError(f"RunningHub 获取工作流 {wf_id} 失败(code={resp.get('code')}):"
                           f"{str(resp.get('msg'))[:300]}")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(text, encoding="utf-8")
    return text


def _rh_failed_reason(base: str, key: str, task_id: str) -> str:
    """任务失败后从 outputs 接口尽力取 failedReason(805 响应携带)。"""
    try:
        resp = _rh_post(base, key, "/task/openapi/outputs", {"taskId": task_id}, timeout=60)
    except RuntimeError:
        return "未能获取失败详情"
    detail = resp.get("data") or resp.get("msg") or resp
    return json.dumps(detail, ensure_ascii=False)[:800]


def _rh_prune_inert_nodes(workflow: dict) -> list[str]:
    """剪除不参与执行的孤岛节点(创作者留在工作流里的备注/模板文本,如 JjkText)。

    RH 提交以 workflow 整包文本覆盖云端模板,这类节点会把大段无关模板内容
    (曾实测三份共 ~20KB 的 H3 提示词模板)原样带进每次请求。判定取交集从严:
    输出无任何下游消费、输入无任何节点连线(纯字面量)、且非 Save/Preview 类
    落盘节点——三者同时成立才剪,连着线的一律不动。返回被剪节点 id 列表。"""
    consumed = {str(value[0]) for node in workflow.values() if isinstance(node, dict)
                for value in (node.get("inputs") or {}).values() if _node_link(value)}
    pruned = []
    for nid, node in list(workflow.items()):
        if not isinstance(node, dict) or nid in consumed:
            continue
        cls = str(node.get("class_type") or "").lower()
        if "save" in cls or "preview" in cls:
            continue
        if any(_node_link(value) for value in (node.get("inputs") or {}).values()):
            continue
        workflow.pop(nid)
        pruned.append(nid)
    return pruned


def _rh_create_backoff(value, attempt: int) -> tuple[float, str] | None:
    """Return a jittered create retry delay only for documented backpressure."""
    code = None
    if isinstance(value, dict):
        raw_code = value.get("code")
        if raw_code in (None, ""):
            raw_code = value.get("errorCode")
        text = " ".join(str(value.get(key) or "") for key in
                        ("msg", "message", "errorMessage", "errorCode"))
        try:
            code = int(raw_code)
        except (TypeError, ValueError):
            pass
    else:
        text = str(value)
        # _request 会把 HTTP 状态和响应 JSON 一起放入 RuntimeError；尽力提取业务码。
        match = re.search(r'["\'](?:code|errorCode)["\']\s*:\s*["\']?(-?\d+)', text)
        if match:
            code = int(match.group(1))
        elif re.search(r"\bHTTP\s+429\b", text, re.IGNORECASE):
            code = 1003
    if code in _RH_CREATE_BACKPRESSURE:
        base, reason = _RH_CREATE_BACKPRESSURE[code]
    else:
        normalized = text.lower()
        keyword_groups = (
            (("task_queue_maxed", "concurrency limit", "concurrent limit",
              "并发上限", "并发已满", "队列已满"), 20, "账户并发已满"),
            (("task_instance_maxed", "instance maxed", "machine unavailable",
              "机器数不足", "资源紧张"), 30, "可用机器不足"),
            (("api_key task is running", "apikey_task_is_running", "task is running",
              "任务正在运行"), 20, "同一 API Key 仍有任务运行"),
            (("rate limit", "too many requests", "请求频率", "限流"),
             10, "请求频率受限"),
            (("system is currently busy", "service unavailable", "系统繁忙", "服务繁忙"),
             30, "RunningHub 服务繁忙"),
        )
        found = next(((base, reason) for words, base, reason in keyword_groups
                      if any(word in normalized for word in words)), None)
        if not found:
            return None
        base, reason = found
    delay = min(120.0, base * (1.5 ** max(0, attempt - 1)))
    return delay + random.uniform(0.0, min(5.0, delay * 0.2)), reason


def _rh_run(cfg, workflow: dict, output: str, want_video: bool,
            task_id: str | None = None, on_submit=None, on_status=None) -> str:
    """RunningHub 建任务/恢复轮询并下载首个匹配产物。

    ``task_id`` 与回调供数字人等按片段持久化远端任务的调用方使用；普通生成调用
    不传时行为保持不变。已有 task_id 时绝不再次 create，避免中断恢复造成重复计费。
    """
    audio_exts = (".mp3", ".wav", ".flac", ".ogg", ".opus", ".m4a")
    video_exts = (".mp4", ".webm", ".gif", ".webp")
    want_audio = (not want_video) and Path(output).suffix.lower() in audio_exts
    base, key, wf_id = _rh_ctx(cfg)
    inst = str(cfg.get("rh_instance_type") or "").strip().lower()
    if not task_id:
        pruned = _rh_prune_inert_nodes(workflow)
        if pruned:
            print(f"[genmedia] RunningHub 提交前剪除 {len(pruned)} 个孤岛节点:"
                  + ",".join(pruned), file=sys.stderr, flush=True)
        payload = {"workflowId": wf_id,
                   "workflow": json.dumps(workflow, ensure_ascii=False)}
        # 运行模式(机器规格):standard 不传 instanceType 沿用平台默认(现行为);
        # plus/ultra 等直接透传,按秒单价更高,取值不合法由建任务接口报错(不计费)
        if inst and inst != "standard":
            payload["instanceType"] = inst
        create_deadline = time.time() + RH_CREATE_WAIT_TIMEOUT
        create_attempt = 0
        while True:
            create_attempt += 1
            try:
                resp = _rh_post(base, key, "/task/openapi/create", payload)
            except RuntimeError as exc:
                backoff = _rh_create_backoff(exc, create_attempt)
                if not backoff:
                    raise
                retry_source = exc
            else:
                # 813 表示请求已经进入平台队列；只要返回 taskId，就按成功受理继续轮询。
                create_data = resp.get("data")
                returned_task_id = (create_data.get("taskId")
                                    if isinstance(create_data, dict) else None)
                accepted = str(resp.get("code")) in ("0", "813") and returned_task_id
                if accepted:
                    break
                backoff = _rh_create_backoff(resp, create_attempt)
                if not backoff:
                    raise RuntimeError(f"RunningHub 建任务失败(code={resp.get('code')}):"
                                       f"{str(resp.get('msg') or resp.get('message'))[:400]}")
                retry_source = resp
            delay, reason = backoff
            remaining = create_deadline - time.time()
            if remaining <= 0:
                code = retry_source.get("code") if isinstance(retry_source, dict) else "HTTP"
                raise RuntimeError(
                    f"RunningHub 等待并发名额超时({RH_CREATE_WAIT_TIMEOUT}s,"
                    f"最后 code={code},尝试 {create_attempt} 次)：{reason}")
            delay = min(delay, remaining)
            if on_status:
                on_status("waiting_capacity")
            print(f"[genmedia] RunningHub {reason}，片段保持等待；"
                  f"{delay:.1f}s 后第 {create_attempt + 1} 次尝试创建任务",
                  file=sys.stderr, flush=True)
            time.sleep(delay)
        data = resp.get("data") or {}
        task_id = data.get("taskId")
        if not task_id:
            raise RuntimeError(f"RunningHub 建任务未返回 taskId:{json.dumps(resp)[:400]}")
        task_id = str(task_id)
        if on_submit:
            on_submit(task_id)
        if str(data.get("taskStatus") or "").upper() == "FAILED":
            if on_status:
                on_status("failed")
            raise RuntimeError("RunningHub 工作流校验失败:"
                               f"{str(data.get('promptTips') or resp.get('msg'))[:800]}")
        # taskId 是排错/对账/防重复计费的唯一凭据,创建即打印(非默认机器规格一并回显)
        print(f"[genmedia] RunningHub 任务已创建 {task_id} → {Path(output).name}"
              + (f"(instanceType={inst})" if inst and inst != "standard" else ""),
              file=sys.stderr, flush=True)
    else:
        task_id = str(task_id)
        print(f"[genmedia] RunningHub 恢复任务 {task_id} → {Path(output).name}",
              file=sys.stderr, flush=True)
    deadline = time.time() + COMFY_TIMEOUT
    poll_errors = 0
    while time.time() < deadline:
        time.sleep(RH_POLL_INTERVAL)
        try:
            st = _rh_post(base, key, "/task/openapi/status", {"taskId": task_id}, timeout=60)
        except RuntimeError:
            # 轮询窗口长,代理/网络瞬断不该丢掉远端仍在跑且已计费的任务;
            # 连续多次不可达才放弃(_rh_post 仅在网络层错误抛 RuntimeError)
            poll_errors += 1
            if poll_errors >= 3:
                raise RuntimeError(
                    f"RunningHub 状态轮询连续 {poll_errors} 次网络不可达"
                    f"(taskId={task_id});任务可能仍在云端执行,"
                    "请先到 RunningHub 网页端核对再决定重投,防止双份计费")
            continue
        poll_errors = 0
        status = st.get("data")
        if isinstance(status, dict):  # 容错:部分版本把状态包在对象里
            status = status.get("taskStatus") or status.get("status")
        status = str(status or "").upper()
        if on_status:
            on_status(status.lower() or "unknown")
        if status in ("SUCCESS",):
            if on_status:
                on_status("downloading")
            return _rh_download_output(base, key, task_id, output, want_video, want_audio)
        if status == "FAILED":
            raise RuntimeError(f"RunningHub 任务失败(taskId={task_id}):"
                               f"{_rh_failed_reason(base, key, task_id)}")
        if status in ("QUEUED", "RUNNING", "CREATE"):
            continue
        if st.get("code") != 0:
            raise RuntimeError(f"RunningHub 状态查询失败(code={st.get('code')}):"
                               f"{str(st.get('msg'))[:400]}")
    try:
        _rh_post(base, key, "/task/openapi/cancel", {"taskId": task_id}, timeout=30)
    except RuntimeError:
        pass
    raise RuntimeError(f"RunningHub 超时({COMFY_TIMEOUT}s,taskId={task_id},已尝试取消)")


def _rh_download_output(base: str, key: str, task_id: str, output: str,
                        want_video: bool, want_audio: bool) -> str:
    """SUCCESS 后取 outputs 清单,按目标类型择优下载(fileUrl 为公网直链)。"""
    files = []
    for _ in range(6):  # SUCCESS 与 outputs 可见之间偶有间隙(804=running),短暂重试
        resp = _rh_post(base, key, "/task/openapi/outputs", {"taskId": task_id}, timeout=60)
        if resp.get("code") == 0 and resp.get("data"):
            files = [f for f in resp["data"] if isinstance(f, dict) and f.get("fileUrl")]
            if files:
                break
        if resp.get("code") not in (0, 804):
            raise RuntimeError(f"RunningHub 获取产物失败(code={resp.get('code')}):"
                               f"{str(resp.get('msg'))[:400]}")
        time.sleep(RH_POLL_INTERVAL)
    if not files:
        raise RuntimeError(f"RunningHub 任务成功但无文件产物(taskId={task_id};"
                           "工作流缺 SaveImage/SaveVideo/SaveAudio 落盘节点?)")
    audio_exts = (".mp3", ".wav", ".flac", ".ogg", ".opus", ".m4a")
    video_exts = (".mp4", ".webm", ".gif", ".webp")

    def _rank(f):
        name = (f.get("fileUrl") or "").split("?", 1)[0].lower()
        ext = "." + str(f.get("fileType") or "").lower().lstrip(".")
        if want_audio:
            return 0 if name.endswith(audio_exts) or ext in audio_exts else 1
        if want_video:
            return 0 if name.endswith(video_exts) or ext in video_exts else 1
        return 0 if name.endswith((".png", ".jpg", ".jpeg", ".webp")) else 1

    pick = sorted(files, key=_rank)[0]
    # 产物是公网直链的大文件(超分 mp4 数十 MB),中途断流(IncompleteRead/超时)只是
    # 网络抖动;任务已计费成功,重试下载而不是报失败让上层重投双份计费
    last_exc = None
    for attempt in range(4):
        try:
            return _save(_request(pick["fileUrl"], timeout=600), output)
        except (http.client.IncompleteRead, TimeoutError, ConnectionError, OSError) as exc:
            last_exc = exc
            print(f"[genmedia] RunningHub 产物下载中断({type(exc).__name__}),"
                  f"第 {attempt + 1}/4 次重试…", file=sys.stderr)
            time.sleep(RH_POLL_INTERVAL)
    raise RuntimeError(f"RunningHub 产物下载失败(taskId={task_id},任务已成功,产物直链 "
                       f"{pick['fileUrl']}):{last_exc}")


def _comfy_endpoint(cfg) -> tuple[str, dict]:
    """ComfyUI 渠道生效端点:mode=cloud 固定 Comfy Cloud,否则用配置的本地 url。

    返回 (base, headers);headers 须随该渠道全部 HTTP 请求发送。
    """
    if (cfg.get("mode") or "local") == "cloud":
        key = (cfg.get("cloud_api_key") or "").strip()
        if not key:
            raise RuntimeError("ComfyUI 云端(Comfy Cloud)未配置 API Key:"
                               "请在「🎨 生成模型」页 ComfyUI 渠道选「云端」并填写")
        return COMFY_CLOUD_URL, {"X-API-Key": key}
    base = (cfg.get("url") or "").rstrip("/")
    if not base:
        raise RuntimeError("ComfyUI 渠道未配置服务地址(「🎨 生成模型」页 ComfyUI 渠道)")
    return base, {}


def _comfy_upload(base: str, path: str, headers: dict | None = None) -> str:
    """上传输入文件到 ComfyUI input 目录,返回服务器端文件名(图/音频通用)。"""
    p = Path(path)
    if not p.is_file():
        raise RuntimeError(f"输入文件不存在: {path}")
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(p.name)[0] or "image/png"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"{p.name}\"\r\nContent-Type: {mime}\r\n\r\n").encode() \
        + p.read_bytes() \
        + (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue"
           f"\r\n--{boundary}--\r\n").encode()
    resp = json.loads(_request(base + "/upload/image", body,
                               {"Content-Type": f"multipart/form-data; boundary={boundary}",
                                **(headers or {})}))
    return resp["name"]


def _comfy_video_frame_count(duration: float | None, fps: int = 16) -> int:
    """Convert seconds to the nearest Wan-compatible 4n+1 frame count."""
    seconds = duration if duration is not None and duration > 0 else 5
    return max(1, round(seconds * fps / 4) * 4 + 1)


def _comfy_ltx_video_frame_count(duration: float | None, fps: int = 24) -> int:
    """Convert seconds to the nearest LTX-compatible 8n+1 frame count."""
    seconds = duration if duration is not None and duration > 0 else 5
    return max(1, round(seconds * fps / 8) * 8 + 1)


def _comfy_h3_frame_count(duration: float | None, fps: int = 24) -> int:
    """MiniMax H3 requires a 17n+5 frame count at its configured output FPS."""
    seconds = duration if duration is not None and duration > 0 else 5
    frames = max(5, round(seconds * fps))
    return frames + (5 - frames % 17) % 17


def _resolve_comfy_workflow_path(wf_path: str) -> Path:
    """Resolve a workflow path, including configs saved before ``comfy/`` moved.

    ComfyUI templates are repository resources rather than runtime data.  Keep
    the old ``data/comfy/`` spelling readable so existing user settings do not
    break when the templates move beside ``data/``.
    """
    path = Path(wf_path)
    if path.is_absolute():
        return path
    primary = ROOT / path
    legacy_prefix = "data/comfy/"
    if str(path).replace("\\", "/").startswith(legacy_prefix):
        migrated = ROOT / "comfy" / str(path).replace("\\", "/")[len(legacy_prefix):]
        if migrated.is_file():
            return migrated
    return primary


def _h3_reference_tags(prompt: str) -> str:
    """Translate project reference tags to MiniMax-H3's documented syntax.

    Prompt packages use the provider-neutral ``@Image 1`` notation.  H3's
    ReferenceToVideo node binds dynamic sockets through ``<Picture 1>`` (and
    analogous Audio/Video tags), so translate only direct reference markers at
    the ComfyUI H3 boundary.
    """
    def replace(match: re.Match[str]) -> str:
        kind = match.group(1).lower()
        target = {"image": "Picture", "picture": "Picture",
                  "audio": "Audio", "video": "Video"}[kind]
        return f"<{target} {match.group(2)}>"

    return re.sub(r"@(?:\s*)?(image|picture|audio|video)\s*(\d+)", replace,
                  prompt, flags=re.IGNORECASE)


def _h3_settings() -> dict:
    """Return the single supported MiniMax H3 runtime configuration."""
    settings = dict(H3_DEFAULTS)
    try:
        settings["fps"] = int(settings["fps"])
        settings["steps"] = int(settings["steps"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("ComfyUI MiniMax-H3 配置的 fps/steps 必须为整数") from exc
    if settings["fps"] <= 0 or settings["steps"] <= 0:
        raise RuntimeError("ComfyUI MiniMax-H3 配置的 fps/steps 必须大于 0")
    return settings


def _ltx25_settings() -> dict:
    settings = dict(LTX25_DEFAULTS)
    for key in ("fps", "tile_size", "tile_overlap", "temporal_size", "temporal_overlap"):
        try:
            settings[key] = int(settings[key])
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"ComfyUI LTX-2.5 配置的 {key} 必须为整数") from exc
        if settings[key] <= 0:
            raise RuntimeError(f"ComfyUI LTX-2.5 配置的 {key} 必须大于 0")
    for key in ("cfg", "first_strength", "last_strength", "ref_strength"):
        try:
            settings[key] = float(settings[key])
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"ComfyUI LTX-2.5 配置的 {key} 必须为数字") from exc
    return settings


def _h3_dimensions(aspect: str, resolution: str, default_short_side: int = 480) -> tuple[int, int]:
    """Map VideoAgents output settings to H3's 32-pixel latent grid."""
    ratio_text = aspect or "16:9"
    try:
        numerator, denominator = (float(x.strip()) for x in ratio_text.split(":", 1))
        ratio = numerator / denominator
        if ratio <= 0:
            raise ValueError
    except (TypeError, ValueError, ZeroDivisionError):
        ratio = 16 / 9
    resolution_sides = {"360p": 360, "480p": 480, "720p": 720, "1080p": 1080}
    if resolution == "4k":
        raise RuntimeError("MiniMax-H3 本地 Base 工作流不支持 4k;"
                           "请先按草稿档生成，再走现有 upscale 成片流程")
    short_side = resolution_sides.get(resolution, default_short_side)
    short_side = max(32, round(short_side / 32) * 32)
    if ratio >= 1:
        return max(32, round(short_side * ratio / 32) * 32), short_side
    return short_side, max(32, round(short_side / ratio / 32) * 32)


def _ltx25_dimensions(aspect: str, resolution: str) -> tuple[int, int]:
    """Map output settings to LTX's 32-pixel spatial grid."""
    ratio_text = aspect or "16:9"
    try:
        numerator, denominator = (float(x.strip()) for x in ratio_text.split(":", 1))
        ratio = numerator / denominator
        if ratio <= 0:
            raise ValueError
    except (TypeError, ValueError, ZeroDivisionError):
        ratio = 16 / 9
    short_side = {"360p": 360, "480p": 480, "720p": 720,
                  "1080p": 1080, "2k": 1440, "4k": 2160}.get(resolution, 512)
    short_side = max(32, round(short_side / 32) * 32)
    if ratio >= 1:
        return max(32, round(short_side * ratio / 32) * 32), short_side
    return short_side, max(32, round(short_side / ratio / 32) * 32)


def _comfy_configured_workflow(cfg: dict) -> dict | None:
    """读取配置的工作流 JSON 用于节点类型判定(RunningHub 为缓存/拉取的云端工作流);
    配置不全或不可读时返回 None,可读的配置错误留给后续 _comfy_workflow/_rh_run 报出。"""
    if _comfy_is_rh(cfg):
        try:
            return json.loads(_rh_workflow_text(cfg))
        except (RuntimeError, json.JSONDecodeError):
            return None
    wf_path = (cfg.get("workflow") or "").strip()
    if not wf_path:
        return None
    try:
        return json.loads(
            _resolve_comfy_workflow_path(wf_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _is_h3_ref2va_workflow(cfg: dict) -> bool:
    workflow = _comfy_configured_workflow(cfg) or {}
    return any(isinstance(node, dict) and node.get("class_type") == H3_REFERENCE_NODE
               for node in workflow.values())


def _is_ltx25_workflow(cfg: dict) -> bool:
    workflow = _comfy_configured_workflow(cfg) or {}
    return any(isinstance(node, dict) and node.get("class_type") == "EmptyLTXVLatentVideo"
               for node in workflow.values()) and any(
                   isinstance(node, dict) and node.get("class_type") == "LTXVConcatAVLatent"
                   for node in workflow.values())


def _seedance_cloud_workflow_gen(cfg: dict) -> float:
    """配置的工作流含 ByteDance2ReferenceNode 时返回其 Seedance 版本(2.5/2.0),否则 0。
    版本取节点 model 输入的字面量("Seedance 2.5"),非方舟 model id,不走 _seedance_gen。"""
    workflow = _comfy_configured_workflow(cfg) or {}
    for node in workflow.values():
        if isinstance(node, dict) and node.get("class_type") == SEEDANCE_REFERENCE_NODE:
            model = str((node.get("inputs") or {}).get("model") or "")
            return 2.5 if "2.5" in model else 2.0
    return 0.0


def _comfy_h3_validate_components(base: str, settings: dict,
                                  headers: dict | None = None) -> None:
    """Fail before upload when the selected H3 component files are not installed."""
    # Comfy Cloud 无单节点 /object_info/<节点> 端点(404: "Use /api/object_info
    # instead"),仅支持全量;本地同样兼容全量,统一一次取回。全量约 9MB,明文长流
    # 易被代理掐断(IncompleteRead),请求 gzip 压到约 0.7MB
    info = _get_json(f"{base}/object_info",
                     {"Accept-Encoding": "gzip", **(headers or {})}, timeout=120)
    try:
        unets = set(info["UNETLoader"]["input"]["required"]["unet_name"][0])
        text_encoders = set(info["CLIPLoader"]["input"]["required"]["clip_name"][0])
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("ComfyUI 未返回可用的 H3 模型清单，无法安全提交任务") from exc
    missing = []
    if settings["unet"] not in unets:
        missing.append(f"UNet={settings['unet']}")
    if settings["text_encoder"] not in text_encoders:
        missing.append(f"text encoder={settings['text_encoder']}")
    if not missing:
        return
    mode_hint = ""
    if ("minimax_h3_fl2va_pruned_int8_convrot.safetensors" in unets
            and settings["unet"].startswith("minimax_h3_ref2va_")):
        mode_hint = (
            " 已检测到 fl2va pruned，但当前工作流是 Ref2VA 多参考模式；"
            "FL2VA 权重不能替代 ref2va 权重。"
        )
    raise RuntimeError(
        "ComfyUI 缺少 MiniMax-H3 Ref2VA 所选组件: " + ", ".join(missing) + "."
        + mode_hint + " 请从 Comfy-Org/MiniMax-H3 安装对应文件后重启 ComfyUI。"
    )


def _node_link(value) -> bool:
    """ComfyUI 输入值是否节点连线([node_id, output_index])而非字面量。"""
    return (isinstance(value, list) and len(value) == 2
            and isinstance(value[1], int) and not isinstance(value[1], bool))


def _h3_node(workflow: dict) -> dict:
    target = next((node for node in workflow.values()
                   if isinstance(node, dict) and node.get("class_type") == H3_REFERENCE_NODE), None)
    if target is None:
        raise RuntimeError("MiniMax-H3 工作流缺少 MiniMaxH3ReferenceToVideo 节点")
    return target


def _apply_h3_prompt(workflow: dict, prompt: str) -> None:
    """Bind the request prompt through the Ref2VA-linked text primitive.

    Some exported RunningHub workflows contain a creator's demonstration text
    instead of a {{PROMPT}} token.  The reference node link is authoritative,
    so replace that linked primitive rather than letting the demo prompt leak
    into a production request.
    """
    inputs = _h3_node(workflow).setdefault("inputs", {})
    slot = inputs.get("prompt")
    if not _node_link(slot):
        inputs["prompt"] = prompt  # 字面值位(本地模板 {{PROMPT}} 填充后即此形态)
        return
    linked = (workflow.get(str(slot[0])) or {}).setdefault("inputs", {})
    for key in ("value", "text", "string"):
        if isinstance(linked.get(key), str):
            linked[key] = prompt
            return
    raise RuntimeError(
        "MiniMax-H3 工作流的 prompt 输入连到无法识别的节点(非 value/text/string 文本位),"
        "无法注入本次提示词;请把云端模板的 prompt 改接文本 primitive 或改用 {{PROMPT}} 占位符")


def _apply_h3_duration(workflow: dict, duration: float | None) -> None:
    """Bind requested seconds through Ref2VA's linked PrimitiveFloat duration node.

    RunningHub workflow exports commonly keep this value as a literal (rather
    than a {{DURATION}} token).  Patch only the node feeding the Ref2VA length
    expression so a group request is not silently rendered at the template's
    default duration.
    """
    if duration is None or duration <= 0:
        return
    inputs = _h3_node(workflow).setdefault("inputs", {})
    slot = inputs.get("length")
    if not _node_link(slot):
        # 字面值位 = 帧数语义(本地模板 {{H3_FRAMES}} 填充后即此形态)
        inputs["length"] = _comfy_h3_frame_count(duration)
        return
    upstream = (workflow.get(str(slot[0])) or {}).setdefault("inputs", {})
    if isinstance(upstream.get("value"), (int, float)) \
            and not isinstance(upstream.get("value"), bool):
        # length 直连数值 primitive,无秒→帧换算节点,按帧数语义填
        upstream["value"] = _comfy_h3_frame_count(duration)
        return
    # length ← 换算表达式(秒×fps 对齐 17n+5)← 秒数 primitive:只改喂表达式的秒数位
    seconds_nodes = []
    for value in upstream.values():
        if not _node_link(value):
            continue
        node = (workflow.get(str(value[0])) or {}).get("inputs") or {}
        if isinstance(node.get("value"), (int, float)) \
                and not isinstance(node.get("value"), bool):
            seconds_nodes.append(node)
    if len(seconds_nodes) != 1:
        raise RuntimeError(
            "MiniMax-H3 工作流的 length 连线无法定位唯一的时长 primitive"
            f"(候选 {len(seconds_nodes)} 个),无法注入 --duration;"
            "请把云端模板的时长改为单一 PrimitiveFloat 喂换算表达式,或改用 {{H3_FRAMES}} 占位符")
    seconds_nodes[0]["value"] = float(duration)


def _apply_h3_ref_image_size(workflow: dict, ref_image_size: str) -> None:
    """覆写 Ref2VA 节点的 ref_image_size 为内置默认(云端导出件常是作者写死的字面值,
    占位符替换空转;该输入是 combo 字面量位,直接赋值即幂等覆写)。"""
    _h3_node(workflow).setdefault("inputs", {})["ref_image_size"] = ref_image_size


def _add_h3_references(workflow: dict, image_names: list[str], audio_names: list[str]) -> None:
    """Attach only submitted refs to H3's dynamic Ref2VA sockets."""
    if len(image_names) > MAX_VIDEO_REFS:
        raise RuntimeError(f"MiniMax-H3 参考图最多 {MAX_VIDEO_REFS} 张,收到 {len(image_names)}")
    if len(audio_names) > MAX_AUDIO_REFS:
        raise RuntimeError(f"MiniMax-H3 参考音频最多 {MAX_AUDIO_REFS} 段,收到 {len(audio_names)}")
    if audio_names and not image_names:
        raise RuntimeError("MiniMax-H3 参考音频必须与至少一张参考图一起使用")
    target = _h3_node(workflow)
    inputs = target.setdefault("inputs", {})
    # 先拆掉模板遗留的全部参考连线:云端导出件常带作者的演示素材,提交槽位数少于
    # 模板时残留连线会把演示图/音频静默混入生产请求;拆线后不再被引用的
    # LoadImage/LoadAudio 节点一并删除(其文件只存在于模板作者账号,留着会校验失败)
    stale_ids = []
    for key in [k for k in inputs if k.startswith(("ref_images.", "ref_audios."))]:
        link = inputs.pop(key)
        if _node_link(link):
            stale_ids.append(str(link[0]))
    referenced = {str(value[0]) for node in workflow.values() if isinstance(node, dict)
                  for value in (node.get("inputs") or {}).values() if _node_link(value)}
    for nid in stale_ids:
        if nid not in referenced:
            workflow.pop(nid, None)
    node_ids = [int(key) for key in workflow if str(key).isdigit()]
    next_id = max(node_ids, default=0) + 1
    for index, name in enumerate(image_names):
        node_id = str(next_id)
        next_id += 1
        workflow[node_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
        inputs[f"ref_images.ref_image_{index}"] = [node_id, 0]
    for index, name in enumerate(audio_names):
        node_id = str(next_id)
        next_id += 1
        workflow[node_id] = {"class_type": "LoadAudio", "inputs": {"audio": name}}
        inputs[f"ref_audios.ref_audio_{index}"] = [node_id, 0]


def _add_seedance_references(workflow: dict, image_names: list[str]) -> None:
    """把已上传的参考图动态挂到 ByteDance2ReferenceNode 的 reference_images 接口。
    与 H3 同款约定:模板不含静态 LoadImage,只挂实际提交的参考图;编号从 1 起
    (对齐 Comfy Cloud 导出件的 model.reference_images.image_1 写法)。"""
    target = next((node for node in workflow.values()
                   if isinstance(node, dict)
                   and node.get("class_type") == SEEDANCE_REFERENCE_NODE), None)
    if target is None:
        raise RuntimeError("Seedance 云工作流缺少 ByteDance2ReferenceNode 节点")
    node_ids = [int(key) for key in workflow if str(key).isdigit()]
    next_id = max(node_ids, default=0) + 1
    inputs = target.setdefault("inputs", {})
    for index, name in enumerate(image_names):
        node_id = str(next_id)
        next_id += 1
        workflow[node_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
        inputs[f"model.reference_images.image_{index + 1}"] = [node_id, 0]


def _add_ltx25_references(workflow: dict, image_names: list[str], first_name: str,
                          last_name: str, settings: dict, frames: int) -> None:
    """Attach LTX image inputs at runtime, keeping the API template input-free.

    ``first`` uses the native first-frame condition node. Other reference images
    and ``last`` use LTXVAddLatentGuide; guides are chained so all supplied
    images contribute to the same positive/negative conditioning and latent.
    """
    if not image_names and not first_name and not last_name:
        return
    nodes = [node for node in workflow.values() if isinstance(node, dict)]
    video_node = next((node for node in nodes
                       if node.get("class_type") == "EmptyLTXVLatentVideo"), None)
    concat = next((node for node in nodes
                   if node.get("class_type") == "LTXVConcatAVLatent"), None)
    conditioning = next((node for node in nodes
                         if node.get("class_type") == "LTXVConditioning"), None)
    guider = next((node for node in nodes if node.get("class_type") == "CFGGuider"), None)
    vae_ids = [key for key, node in workflow.items()
               if isinstance(node, dict) and node.get("class_type") == "VAELoader"]
    if not (video_node and concat and conditioning and guider and vae_ids):
        raise RuntimeError("LTX-2.5 工作流缺少首尾帧/参考图所需的核心节点")
    video_id = next(key for key, node in workflow.items() if node is video_node)
    concat_id = next(key for key, node in workflow.items() if node is concat)
    conditioning_id = next(key for key, node in workflow.items() if node is conditioning)
    vae_id = vae_ids[0]
    numeric_ids = [int(key) for key in workflow if str(key).isdigit()]
    next_id = max(numeric_ids, default=0) + 1

    def load_image(name: str) -> list:
        nonlocal next_id
        node_id = str(next_id)
        next_id += 1
        workflow[node_id] = {"class_type": "LoadImage", "inputs": {"image": name}}
        return [node_id, 0]

    current_video = [video_id, 0]
    current_positive, current_negative = [conditioning_id, 0], [conditioning_id, 1]

    if first_name:
        node_id = str(next_id)
        next_id += 1
        workflow[node_id] = {"class_type": "LTXVImgToVideoInplace", "inputs": {
            "vae": [vae_id, 0], "image": load_image(first_name),
            "latent": current_video, "strength": settings["first_strength"]}}
        current_video = [node_id, 0]

    # Reference images are identity/keyframe guides at the beginning of the clip.
    guide_names = [(name, 0, settings["ref_strength"]) for name in image_names]
    if last_name:
        guide_names.append((last_name, -1, settings["last_strength"]))
    for name, frame_idx, strength in guide_names:
        guide_id = str(next_id)
        next_id += 1
        workflow[guide_id] = {"class_type": "LTXVAddGuide", "inputs": {
            "vae": [vae_id, 0], "positive": current_positive,
            "negative": current_negative, "latent": current_video,
            "image": load_image(name), "frame_idx": frame_idx,
            "strength": strength}}
        current_positive, current_negative, current_video = [guide_id, 0], [guide_id, 1], [guide_id, 2]

    concat["inputs"]["video_latent"] = current_video
    guider["inputs"]["positive"] = current_positive
    guider["inputs"]["negative"] = current_negative


def _extract_last_frame(video_path: str, frame_path: str) -> None:
    """H3 muxes audio/video locally; derive the continuity anchor from its finished MP4."""
    target = Path(frame_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["ffmpeg", "-y", "-sseof", "-0.1", "-i", video_path,
         "-frames:v", "1", str(target)],
        capture_output=True, text=True, timeout=90)
    if result.returncode or not target.is_file() or target.stat().st_size == 0:
        detail = (result.stderr or result.stdout).strip()[-500:]
        raise RuntimeError(f"MiniMax-H3 成片已生成但尾帧提取失败:{detail}")


def _comfy_execution_error(status: dict) -> str:
    """Extract the actionable node error without burying it in ComfyUI history JSON."""
    for message in status.get("messages") or []:
        if not isinstance(message, list) or len(message) < 2 or message[0] != "execution_error":
            continue
        detail = message[1] if isinstance(message[1], dict) else {}
        parts = []
        if detail.get("node_id"):
            parts.append(f"node_id={detail['node_id']}")
        if detail.get("node_type"):
            parts.append(f"node_type={detail['node_type']}")
        if detail.get("exception_type"):
            parts.append(f"exception_type={detail['exception_type']}")
        if detail.get("exception_message"):
            exception_message = str(detail["exception_message"]).strip()
            parts.append(f"exception_message={exception_message}")
            if "ACE-Step-v1-3.5B" in exception_message:
                parts.append(
                    "hint=ACE-Step 节点按 ComfyUI 安装目录的 models/TTS 查找，"
                    "请把共享模型目录映射到该目录并重启 ComfyUI;"
                    "仅下载到 HuggingFace cache 或 extra_model_paths.yaml 的其他目录不够"
                )
            if ("hostbuf_file_reader_read failed" in exception_message
                    or "HostBuffer.read_file_slice failed" in exception_message):
                parts.append(
                    "hint=MiniMax-H3 int8 权重在 ComfyUI async-offload/prefetch 读取失败;"
                    "停止重试，先核对 minimax_h3_ref2va_pruned_int8_convrot.safetensors 的 SHA256/"
                    "实际读取路径并更新 ComfyUI 与 comfy-aimdo；64G 主机先用 "
                    "--disable-pinned-memory --disable-async-offload 作一次诊断重试，"
                    "并确认宿主机 RAM/VRAM 没有被其他任务占用"
                )
        if parts:
            return ", ".join(parts)
        return json.dumps(message, ensure_ascii=False)[:1200]
    return "ComfyUI history 未提供 execution_error 详情"


def _comfy_queue_contains(queue: dict, prompt_id: str) -> bool:
    """Whether a prompt is still listed by ComfyUI as pending or running."""
    for name in ("queue_pending", "queue_running"):
        for item in queue.get(name) or []:
            # Queue entries are positional lists in current ComfyUI, but preserve
            # compatibility with custom queue serializers.
            if prompt_id in json.dumps(item, ensure_ascii=False):
                return True
    return False


_CLOUD_JOB_RUNNING = object()  # _comfy_cloud_job 哨兵:任务仍在排队/执行中


def _comfy_cloud_job(base: str, pid: str, headers: dict | None):
    """Comfy Cloud 任务状态:GET /jobs/{prompt_id},归一成本地 /history 条目结构。

    返回值:None = 云端尚无该任务记录(HTTP 404,刚提交尚未入库);
    _CLOUD_JOB_RUNNING = pending/in_progress;否则为 {"status": {...}, "outputs": {...}},
    status_str 取 success/error 与本地 history 对齐,execution_error 塞进 messages 供
    _comfy_execution_error 复用。真机返回体(2026-08 实测):
    {id, status: pending|in_progress|completed|failed|cancelled, outputs?: {node: {images|...}},
     execution_error?: {node_id, node_type, exception_type, exception_message, traceback},
     execution_status: null(与开源版不同,不能依赖), outputs_count, ...}
    """
    try:
        job = _get_json(f"{base}/jobs/{pid}", headers)
    except RuntimeError as exc:
        if str(exc).startswith("HTTP 404 "):
            return None
        raise
    state = str(job.get("status") or "").lower()
    if state in ("pending", "in_progress", "queued", "running"):
        return _CLOUD_JOB_RUNNING
    messages = []
    err = job.get("execution_error")
    if isinstance(err, dict) and err:
        messages.append(["execution_error", err])
    elif state != "completed":
        messages.append(["execution_error", {
            "exception_type": state or "unknown",
            "exception_message": ("任务在 Comfy Cloud 被取消" if state == "cancelled"
                                  else f"Comfy Cloud 任务状态 {state or '(空)'} 且未附 execution_error")}])
    ok = state == "completed"
    return {
        "status": {"status_str": "success" if ok else "error",
                   "completed": ok, "messages": messages},
        "outputs": job.get("outputs") or {},
    }


def _comfy_run(base: str, workflow: dict, output: str, want_video: bool,
               headers: dict | None = None, prompt_id: str | None = None,
               on_submit=None, on_status=None) -> str:
    """提交工作流,轮询完成,下载首个产物到 output。

    want_video=True 优先选视频扩展名;want_video=False 时若 output 是音频扩展名
    则优先选音频产物,否则按图片处理(兼容旧调用)。
    """
    audio_exts = (".mp3", ".wav", ".flac", ".ogg", ".opus", ".m4a")
    video_exts = (".mp4", ".webm", ".gif", ".webp")
    want_audio = (not want_video) and Path(output).suffix.lower() in audio_exts
    pid = str(prompt_id or "").strip()
    if not pid:
        try:
            resp = _post_json(base + "/prompt",
                              {"prompt": workflow, "client_id": uuid.uuid4().hex}, headers)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"ComfyUI 服务不可达，未能提交任务:{base}") from exc
        pid = str(resp.get("prompt_id") or "").strip()
        if not pid:
            raise RuntimeError(f"ComfyUI 提交失败:{json.dumps(resp)[:400]}")
        if on_submit:
            on_submit(pid)
    deadline = time.time() + COMFY_TIMEOUT
    queue_missing_since = None
    seen_in_queue = False
    is_cloud = base.rstrip("/") == COMFY_CLOUD_URL
    while time.time() < deadline:
        time.sleep(2)
        try:
            if is_cloud:
                # Comfy Cloud 无 /history,状态在 /jobs/{prompt_id}
                hist = _comfy_cloud_job(base, pid, headers)
                if hist is _CLOUD_JOB_RUNNING:
                    seen_in_queue = True
                    queue_missing_since = None
                    continue
            else:
                hist = _get_json(f"{base}/history/{pid}", headers).get(pid)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(
                f"ComfyUI 服务不可达，任务可能因服务重启或崩溃而中断(prompt_id={pid})"
            ) from exc
        if not hist:
            try:
                queue = _get_json(f"{base}/queue", headers)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise RuntimeError(
                    f"ComfyUI 服务不可达，任务可能因服务重启或崩溃而中断(prompt_id={pid})"
                ) from exc
            if _comfy_queue_contains(queue, pid):
                seen_in_queue = True
                queue_missing_since = None
                if on_status:
                    on_status("running")
                continue
            if queue_missing_since is None:
                queue_missing_since = time.time()
            grace = (COMFY_QUEUE_DISAPPEAR_GRACE if seen_in_queue
                     else COMFY_QUEUE_SUBMIT_GRACE)
            if time.time() - queue_missing_since >= grace:
                state = ("执行中的任务已从队列消失" if seen_in_queue
                         else "已提交任务未出现在队列")
                raise RuntimeError(
                    f"ComfyUI {state}且未写入 history；服务可能已重启、任务被取消，"
                    f"或队列异常丢失(prompt_id={pid})"
                )
            continue
        status = hist.get("status") or {}
        if status.get("status_str") == "error":
            if on_status:
                on_status("failed")
            raise RuntimeError(f"ComfyUI 执行出错:{_comfy_execution_error(status)}")
        if on_status:
            on_status("running")
        outputs = hist.get("outputs") or {}
        files = []
        for node_out in outputs.values():
            # 收集全部已知媒体键,之后按目标扩展名排序。部分自定义音频节点会把
            # 产物放在 images/preview 键,只按键名判断会漏掉或拿错文件。
            for key in ("images", "gifs", "video", "videos", "audio", "audios"):
                files += node_out.get(key) or []
        if files:
            def _rank(f):
                name = f.get("filename", "").lower()
                if want_audio:
                    return 0 if name.endswith(audio_exts) else 1
                if want_video:
                    return 0 if name.endswith(video_exts) else 1
                return 0 if name.endswith((".png", ".jpg", ".jpeg", ".webp")) else 1
            pick = sorted(files, key=_rank)[0]
            q = urllib.parse.urlencode({"filename": pick["filename"],
                                        "subfolder": pick.get("subfolder", ""),
                                        "type": pick.get("type", "output")})
            # Comfy Cloud 的 /view 返回 302 → 签名 URL,urllib 自动跟随
            if on_status:
                on_status("downloading")
            return _save(_request(f"{base}/view?{q}", headers=headers, timeout=300), output)
        if status.get("completed"):
            need = ("SaveAudio/SaveAudioMP3" if want_audio
                    else "SaveVideo" if want_video else "SaveImage/SaveVideo")
            raise RuntimeError(f"ComfyUI 已完成但无文件产物(工作流缺 {need} 节点?)")
    raise RuntimeError(f"ComfyUI 超时({COMFY_TIMEOUT}s)")


def _comfy_fill_workflow(text: str, tokens: dict) -> dict:
    """工作流 JSON 文本的 {{TOKEN}} 占位符替换;残留占位符视为缺少输入。"""
    try:
        workflow = json.loads(text)
    except json.JSONDecodeError:
        # Backward compatibility for templates with unquoted numeric placeholders.
        workflow = None
    if workflow is not None:
        def replace_tokens(value):
            if isinstance(value, dict):
                return {k: replace_tokens(v) for k, v in value.items()}
            if isinstance(value, list):
                return [replace_tokens(v) for v in value]
            if not isinstance(value, str):
                return value
            for k, v in tokens.items():
                marker = "{{%s}}" % k
                if value == marker:
                    return v
                if marker in value:
                    value = value.replace(marker, str(v))
            return value

        workflow = replace_tokens(workflow)
        missing = sorted(set(re.findall(r"\{\{([A-Z][A-Z0-9_]*)\}\}",
                                              json.dumps(workflow))))
        if missing:
            raise RuntimeError(f"ComfyUI 工作流缺少输入:{', '.join(missing)}")
        return workflow
    for k, v in tokens.items():
        if isinstance(v, str):
            text = text.replace("{{%s}}" % k, json.dumps(v)[1:-1])  # 转义后嵌入字符串位
        else:
            text = text.replace("{{%s}}" % k, str(v))
    missing = sorted(set(re.findall(r"\{\{([A-Z][A-Z0-9_]*)\}\}", text)))
    if missing:
        raise RuntimeError(f"ComfyUI 工作流缺少输入:{', '.join(missing)}")
    return json.loads(text)


def _comfy_workflow(cfg, tokens: dict, kind: str) -> dict:
    """加载配置的工作流模板并做占位符替换;图像无模板时用内置 txt2img。

    RunningHub 运行方式改用云端工作流(rh_workflow_id 经 getJsonApiFormat 拉取,
    本地缓存),占位符约定与本地模板完全一致。
    """
    if _comfy_is_rh(cfg):
        return _comfy_fill_workflow(_rh_workflow_text(cfg), tokens)
    wf_path = (cfg.get("workflow") or "").strip()
    if wf_path:
        p = _resolve_comfy_workflow_path(wf_path)
        if not p.is_file():
            raise RuntimeError(f"配置的 ComfyUI 工作流不存在: {wf_path}")
        return _comfy_fill_workflow(p.read_text(), tokens)
    if kind == "video":
        raise RuntimeError("ComfyUI 视频生成必须在「🎨 生成模型」页配置工作流 JSON(API 格式)")
    if kind in ("music", "tts"):
        raise RuntimeError(f"ComfyUI {kind} 必须在「🎨 生成模型」页配置工作流 JSON"
                           f"(推荐 comfy/{'ace-step-v1-music' if kind=='music' else 'indextts2-tts'}-api.json)")
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


IMAGE_SAMPLER_CLASSES = ("KSampler", "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced")


def _image_primary_sampler(workflow: dict) -> dict:
    """定位工作流的主采样器:不(传递地)依赖其他采样器产物的那一个(图像/音乐共用)。

    云端模板常带二段式精修/放大链(第二个采样器拿第一段产物当参考),画面内容
    由主采样器的 conditioning 决定,prompt/negative/seed 直绑只认主采样器,
    精修链的增强提示词保持模板原值。
    """
    samplers = {nid: node for nid, node in workflow.items()
                if isinstance(node, dict) and node.get("class_type") in IMAGE_SAMPLER_CLASSES}

    def reaches_other_sampler(start: str) -> bool:
        seen, stack = {start}, [start]
        while stack:
            node = workflow.get(stack.pop())
            if not isinstance(node, dict):
                continue
            for value in (node.get("inputs") or {}).values():
                if not _node_link(value) or str(value[0]) in seen:
                    continue
                up = str(value[0])
                seen.add(up)
                if up in samplers:
                    return True
                stack.append(up)
        return False

    primary = [nid for nid in samplers if not reaches_other_sampler(nid)]
    if len(primary) != 1:
        raise RuntimeError(
            f"RunningHub 工作流无法定位唯一主采样器(KSampler 系候选 {len(primary)} 个),"
            "无法直绑本次参数;请精简模板或改用 {{PROMPT}} 等占位符")
    return samplers[primary[0]]


def _image_cond_text_slot(workflow: dict, sampler: dict, side: str,
                          text_keys: tuple = ("text", "prompt")):
    """顺主采样器 positive/negative conditioning 连线找文本编码节点。

    返回 (节点 inputs, 文本键, 节点id);途经单输入 conditioning 透传节点
    (FluxGuidance/ReferenceLatent 等)继续下探。ConditioningZeroOut 表示该侧
    文本被零化(模板不用这侧文本),返回 None 由调用方定性。
    音乐分支传 text_keys=("tags",...) 复用同一走线(ACE 风格位叫 tags)。
    """
    inputs = sampler.get("inputs") or {}
    link = inputs.get(side)
    if link is None and _node_link(inputs.get("guider")):
        # SamplerCustomAdvanced:conditioning 藏在 guider 节点
        # (CFGGuider 有 positive/negative,BasicGuider 只有 conditioning=正面)
        guider = (workflow.get(str(inputs["guider"][0])) or {}).get("inputs") or {}
        link = guider.get(side)
        if link is None and side == "positive":
            link = guider.get("conditioning")
    for _ in range(24):
        if not _node_link(link):
            return None
        nid = str(link[0])
        node = workflow.get(nid)
        if not isinstance(node, dict) or node.get("class_type") == "ConditioningZeroOut":
            return None
        node_inputs = node.setdefault("inputs", {})
        for text_key in text_keys:
            value = node_inputs.get(text_key)
            if isinstance(value, str) or _node_link(value):
                return node_inputs, text_key, nid
        link = node_inputs.get("conditioning")
    return None


def _rh_resolve_text_slot(workflow: dict, node_inputs: dict, text_key: str):
    """把编码节点的文本位解析为实际写入位置 (容器 inputs, 键)。

    字面值位就地覆写;连线则改写上游文本 primitive(value/text/string 值位,
    与 H3 同款);上游不可识别(如图像反推、字符串拼装节点)时退回编码节点断链
    覆写字面值——text/prompt 位本身就是标准文本位,断开的上游分支不再进执行图。
    """
    value = node_inputs.get(text_key)
    if _node_link(value):
        linked = (workflow.get(str(value[0])) or {}).setdefault("inputs", {})
        for key in ("value", "text", "string", "prompt"):
            if isinstance(linked.get(key), str):
                return linked, key
    return node_inputs, text_key


def _apply_rh_image_seed(workflow: dict, sampler: dict, seed) -> None:
    """种子直绑:采样器种子字面值位,或经 noise 连线的 RandomNoise 类节点。
    定位不到只如实提醒不报错——种子不注入只影响重跑变化,不产生错误内容。"""
    inputs = sampler.setdefault("inputs", {})
    candidates = [(inputs, ("seed", "noise_seed"))]
    if _node_link(inputs.get("noise")):
        upstream = (workflow.get(str(inputs["noise"][0])) or {}).setdefault("inputs", {})
        candidates.append((upstream, ("noise_seed", "seed", "value")))
    for container, keys in candidates:
        for key in keys:
            value = container.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                container[key] = seed
                return
    print("[genmedia] RunningHub 工作流未定位到采样器种子位,seed 未注入(按模板内种子生成)",
          file=sys.stderr)


def _apply_rh_image_reference(workflow: dict, file_name: str) -> None:
    """把已上传的 --ref 绑进图生图模板唯一的 LoadImage 输入图节点。"""
    loads = [node for node in workflow.values()
             if isinstance(node, dict) and node.get("class_type") == "LoadImage"]
    if len(loads) != 1:
        raise RuntimeError(
            f"RunningHub 图生图工作流须恰好 1 个 LoadImage 输入图节点(找到 {len(loads)} 个),"
            "无法定位 --ref 绑定位;请精简模板或改用 {{FIRST_FRAME}} 占位符")
    loads[0].setdefault("inputs", {})["image"] = file_name


def _apply_rh_image_bindings(raw: str, workflow: dict, prompt: str, negative: str,
                             ref_name: str | None, seed) -> None:
    """RunningHub 图像云工作流的无占位符直绑兜底(与视频 H3 同款语义)。

    云端工作区导出件常无 {{TOKEN}} 占位符而是作者演示字面值,占位符替换空转,
    演示提示词/演示图会静默混入生产请求;对缺对应占位符的模板顺主采样器连线
    直接绑定本次参数,prompt/ref 定位不到一律提交前报错不发请求不计费。
    带占位符的模板逐项跳过兜底,行为不变。
    """
    sampler_cache = []

    def sampler() -> dict:
        if not sampler_cache:
            sampler_cache.append(_image_primary_sampler(workflow))
        return sampler_cache[0]

    prompt_target = None
    if "{{PROMPT}}" not in raw:
        slot = _image_cond_text_slot(workflow, sampler(), "positive")
        if slot is None:
            raise RuntimeError(
                "RunningHub 图像工作流顺 positive 连线未找到可写文本位(text/prompt),"
                "无法注入本次提示词;请把模板提示词改接文本节点或改用 {{PROMPT}} 占位符")
        container, key = _rh_resolve_text_slot(workflow, slot[0], slot[1])
        container[key] = prompt
        prompt_target = (id(container), key)
    if negative and "{{NEGATIVE}}" not in raw:
        slot = _image_cond_text_slot(workflow, sampler(), "negative")
        target = None if slot is None else _rh_resolve_text_slot(workflow, slot[0], slot[1])
        if target is None or (id(target[0]), target[1]) == prompt_target:
            raise RuntimeError(
                "当前 RunningHub 图像工作流无独立负面文本位,--negative 无法注入;"
                "请在「🎨 生成模型」页把 ComfyUI 负面模式改为「并入正面提示词」"
                "(append_exclusions),或改用带 {{NEGATIVE}} 占位符的模板")
        target[0][target[1]] = negative
    if seed is not None and "{{SEED}}" not in raw:
        _apply_rh_image_seed(workflow, sampler(), seed)
    if "{{WIDTH}}" not in raw and "{{HEIGHT}}" not in raw:
        print("[genmedia] RunningHub 图像工作流无分辨率占位符,--aspect/--size 不进云端,"
              "按模板内分辨率节点出图", file=sys.stderr)
    if ref_name:
        if "{{FIRST_FRAME}}" not in raw:
            _apply_rh_image_reference(workflow, ref_name)
        return
    # 无 --ref:文生图模板残留被引用的 LoadImage 会把作者演示图静默混入
    # (H3 残留素材同款纪律);未被引用的孤儿节点不进执行图,不拦
    referenced = {str(value[0]) for node in workflow.values() if isinstance(node, dict)
                  for value in (node.get("inputs") or {}).values() if _node_link(value)}
    if any(isinstance(node, dict) and node.get("class_type") == "LoadImage"
           and nid in referenced for nid, node in workflow.items()):
        raise RuntimeError(
            "RunningHub 文生图工作流含在用的 LoadImage 输入图节点,作者演示图会混入生产请求;"
            "该模板请配置为图生图工作流搭配 --ref 使用,或改选纯文生图模板")


def _rh_bind_number(workflow: dict, inputs: dict, key: str, value) -> bool:
    """数值位直绑:字面值就地覆写;连线则改写上游数值 primitive 的 value 位。"""
    slot = inputs.get(key)
    if isinstance(slot, (int, float)) and not isinstance(slot, bool):
        inputs[key] = value
        return True
    if _node_link(slot):
        linked = (workflow.get(str(slot[0])) or {}).setdefault("inputs", {})
        if isinstance(linked.get("value"), (int, float)) \
                and not isinstance(linked.get("value"), bool):
            linked["value"] = value
            return True
    return False


def _apply_rh_music_bindings(raw: str, workflow: dict, prompt: str, lyrics: str,
                             duration: float, seed, lyrics_configured: bool) -> None:
    """RunningHub 音乐云工作流的无占位符直绑兜底(与图像分支同款语义)。

    风格提示词绑主采样器 positive 连线上的音频文本编码节点(ACE 系的 tags 位,
    兜底 text/prompt),歌词绑同节点 lyrics 位(模板演示歌词不得混入,未配歌词
    时按 [Instrumental] 覆写);时长绑编码节点 duration 位与各节点 seconds 位
    (ACE 两处常同连一个 Float primitive);prompt/时长定位不到提交前报错
    不发请求不计费。带占位符模板逐项跳过,兜底幂等。
    """
    cache: dict = {}

    def encode_slot():
        if "slot" not in cache:
            cache["sampler"] = _image_primary_sampler(workflow)
            cache["slot"] = _image_cond_text_slot(
                workflow, cache["sampler"], "positive", ("tags", "text", "prompt"))
        return cache["slot"]

    def encode_slot_soft():
        # prompt 之外的项缺位时,锚点定位失败不该硬报错(由各项自行定性)
        try:
            return encode_slot()
        except RuntimeError:
            return None

    if "{{PROMPT}}" not in raw:
        slot = encode_slot()
        if slot is None:
            raise RuntimeError(
                "RunningHub 音乐工作流顺 positive 连线未找到风格文本位(tags/text/prompt),"
                "无法注入本次风格提示词;请把模板改接文本节点或改用 {{PROMPT}} 占位符")
        container, key = _rh_resolve_text_slot(workflow, slot[0], slot[1])
        container[key] = prompt
    if "{{LYRICS}}" not in raw:
        slot = encode_slot_soft()
        value = slot[0].get("lyrics") if slot else None
        if isinstance(value, str) or _node_link(value):
            container, key = _rh_resolve_text_slot(workflow, slot[0], "lyrics")
            container[key] = lyrics
        elif lyrics_configured:
            raise RuntimeError(
                "RunningHub 音乐工作流未找到 lyrics 歌词位,配置的歌词无法注入;"
                "请改用带歌词输入的 ACE 工作流或 {{LYRICS}} 占位符")
    if "{{DURATION}}" not in raw:
        # 编码节点 duration 与 latent seconds 常同连一个 Float primitive,分别尝试即可
        slot = encode_slot_soft()
        bound = bool(slot) and _rh_bind_number(workflow, slot[0], "duration", duration)
        for node in workflow.values():
            if isinstance(node, dict) and "seconds" in (node.get("inputs") or {}):
                bound = _rh_bind_number(workflow, node["inputs"], "seconds", duration) or bound
        if not bound:
            raise RuntimeError(
                "RunningHub 音乐工作流未定位到时长位(duration/seconds 数值或其上游 primitive),"
                "无法注入时长,模板默认时长会照单计费;请改模板或用 {{DURATION}} 占位符")
    if seed is not None and "{{SEED}}" not in raw:
        slot = encode_slot_soft()
        if cache.get("sampler") is None:
            print("[genmedia] RunningHub 工作流未定位到采样器种子位,seed 未注入(按模板内种子生成)",
                  file=sys.stderr)
        else:
            _apply_rh_image_seed(workflow, cache["sampler"], seed)
            if slot:  # ACE 编码节点自带音频码生成种子,一并绑定保证重跑有变化
                _rh_bind_number(workflow, slot[0], "seed", seed)


def _rh_tts_generate_node(workflow: dict) -> dict:
    """TTS 生成节点定位:从 SaveAudio*/PreviewAudio 落盘节点顺 audio/samples 连线
    上溯到第一个带 text 输入的节点(TTS 工作流通常无采样器,主采样器锚点不适用)。"""
    sinks = sorted((node for node in workflow.values() if isinstance(node, dict)
                    and str(node.get("class_type") or "").startswith(("SaveAudio", "PreviewAudio"))),
                   key=lambda n: 0 if str(n.get("class_type")).startswith("SaveAudio") else 1)
    for sink in sinks:
        link = (sink.get("inputs") or {}).get("audio")
        for _ in range(16):
            if not _node_link(link):
                break
            node = workflow.get(str(link[0]))
            if not isinstance(node, dict):
                break
            inputs = node.setdefault("inputs", {})
            value = inputs.get("text")
            if isinstance(value, str) or _node_link(value):
                return node
            link = inputs.get("audio") or inputs.get("samples")
    raise RuntimeError(
        "RunningHub TTS 工作流未定位到带 text 输入的生成节点(从 SaveAudio 顺 audio 连线上溯),"
        "无法注入本次台词;请检查模板接线或改用 {{TEXT}} 占位符")


def _apply_rh_tts_bindings(raw: str, workflow: dict, text: str, ref_audio: str,
                           seed, speed) -> None:
    """RunningHub TTS 云工作流的无占位符直绑兜底(与图像分支同款语义)。

    台词绑生成节点 text 位(字面值/上游文本 primitive,演示台词不得混入);
    自动选择并上传的参考音色绑模板唯一 LoadAudio(0/多个提交前报错,防止
    静默用作者演示音色出声);seed 绑生成节点;speed 无对应输入位时如实
    stderr 记录。带占位符模板逐项跳过,兜底幂等。
    """
    cache: list = []

    def gen_node() -> dict:
        if not cache:
            cache.append(_rh_tts_generate_node(workflow))
        return cache[0]

    if "{{TEXT}}" not in raw and "{{PROMPT}}" not in raw:
        container, key = _rh_resolve_text_slot(workflow, gen_node()["inputs"], "text")
        container[key] = text
    if "{{REF_AUDIO}}" not in raw and "{{VOICE}}" not in raw:
        loads = [node for node in workflow.values()
                 if isinstance(node, dict) and node.get("class_type") == "LoadAudio"]
        if len(loads) != 1:
            raise RuntimeError(
                f"RunningHub TTS 工作流须恰好 1 个 LoadAudio 参考音频节点(找到 {len(loads)} 个),"
                "无法定位音色绑定位,已选参考音色进不去会静默用模板演示音色;"
                "请精简模板或改用 {{REF_AUDIO}} 占位符")
        loads[0].setdefault("inputs", {})["audio"] = ref_audio
    if seed is not None and "{{SEED}}" not in raw:
        if not _rh_bind_number(workflow, gen_node()["inputs"], "seed", seed):
            print("[genmedia] RunningHub TTS 工作流未定位到种子位,seed 未注入(按模板内种子出声)",
                  file=sys.stderr)
    if speed and float(speed) != 1.0 and "{{SPEED}}" not in raw:
        if not _rh_bind_number(workflow, gen_node()["inputs"], "speed", float(speed)):
            print("[genmedia] RunningHub TTS 工作流无 speed 输入位,--speed 未注入(按模板语速出声)",
                  file=sys.stderr)


def _image_comfyui(cfg, prompt, negative, refs, width, height, seed, output):
    rh = _comfy_is_rh(cfg)
    base = hdrs = None
    if not rh:
        base, hdrs = _comfy_endpoint(cfg)
    negative_mode = cfg.get("negative_mode") or "conditioning"
    if negative and negative_mode == "append_exclusions":
        prompt = f"{prompt}\nExclude from the image: {negative}"
        negative = ""
    elif negative and negative_mode == "unsupported":
        raise RuntimeError("当前 ComfyUI 图片工作流不支持 negative prompt")
    tokens = {"PROMPT": prompt, "NEGATIVE": negative or "",
              "WIDTH": width, "HEIGHT": height, "SEED": seed,
              "CHECKPOINT": cfg.get("checkpoint") or ""}
    workflow_cfg = cfg
    if refs:
        if len(refs) > 1:
            raise RuntimeError("当前 ComfyUI 参考图工作流仅支持一张 --ref")
        if rh:
            ref_id = str(cfg.get("rh_ref_workflow_id") or "").strip()
            if not ref_id:
                raise RuntimeError("ComfyUI 图片渠道未配置 RunningHub 图生图工作流"
                                   "(「🎨 生成模型」页验证并添加后选择)")
            workflow_cfg = {**cfg, "rh_workflow_id": ref_id}
            tokens["FIRST_FRAME"] = _rh_upload(cfg, refs[0])
        else:
            ref_workflow = (cfg.get("ref_workflow") or "").strip()
            if not ref_workflow:
                raise RuntimeError("ComfyUI 图片渠道未配置参考图工作流(ref_workflow)")
            tokens["FIRST_FRAME"] = _comfy_upload(base, refs[0], hdrs)
            workflow_cfg = {**cfg, "workflow": ref_workflow}
    wf = _comfy_workflow(workflow_cfg, tokens, "image")
    if rh:
        # 云端工作区模板常无占位符,占位符替换空转;缺哪项就顺连线直绑哪项
        _apply_rh_image_bindings(_rh_workflow_text(workflow_cfg), wf, prompt,
                                 negative, tokens.get("FIRST_FRAME"), seed)
        return _rh_run(workflow_cfg, wf, output, want_video=False)
    return _comfy_run(base, wf, output, want_video=False, headers=hdrs)


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

MAX_VIDEO_REFS = 9        # 方舟 reference_image 上限(Seedance 2.0 系列)
MAX_AUDIO_REFS = 3        # 方舟 reference_audio 上限(Seedance 2.0 系列)
MAX_AUDIO_TOTAL_S = 15.2  # 方舟 r2v reference_audio 总时长硬限(超限任务创建即 400 InvalidParameter;
                          # 实证:tothemoon 2026-07-20 两段 ~12s 样本合计 24.1s 被拒——voiceprint 规格 ≤5s/段,§8A)
# Seedance 2.5(doubao-seedance-2-5-* / dreamina-seedance-2-5-*)放宽后的上限
# (官方教程 docs.volcengine.com/docs/82379/2607688:30图+10视频+10音频,
#  参考音/视频总时长各 ≤30s,单段 [2,30]s;时长 [4,30] 或 -1;分辨率仅 480p/720p)
V25_MAX_VIDEO_REFS = 30
V25_MAX_AUDIO_REFS = 10
V25_MAX_AUDIO_TOTAL_S = 30.2
V25_MAX_VIDEOIN_REFS = 10
V25_MAX_VIDEOIN_TOTAL_S = 30.2


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
MAX_VIDEOIN_REFS = 3      # 方舟 reference_video 上限(Seedance 2.0:单个 2-15s,总时长 ≤15s)
MAX_VIDEOIN_BYTES = 45 * 1024 * 1024  # 参考视频 data URL 内联上限(base64 膨胀后仍须 <64MB 请求体)


def _seedance_gen(model: str) -> float:
    """Seedance 主版本号:2.5 / 2.0(其余 2.x 按 2.0 口径)/ 0(1.x 或非 Seedance)。
    大小写不敏感,并兼容 seedance-2.5 点号写法(容错自定义 model id)。"""
    m = (model or "").lower()
    if "seedance-2-5" in m or "seedance-2.5" in m:
        return 2.5
    return 2.0 if "seedance-2" in m else 0.0


def _is_seedance2(model: str) -> bool:
    return _seedance_gen(model) >= 2.0


# Seedance 2.5 任务类型触发词(官方文档口径):视频编辑/视频延长任务对 ratio(仅
# adaptive)与 duration(编辑仅 -1)有硬约束,违规异步报错 TaskTypeConstraint。
# 意图最终由模型判定,此处仅作提交前的保守预警/参数修正,不拦截。
_V25_EDIT_RE = re.compile(r"编辑视频|删除|去掉|删掉|修改|替换|改成|增加|加上")
_V25_EXTEND_RE = re.compile(r"向前延长|向后延长|延续|续写")


def _ark_video_body(cfg, prompt, first, last, duration, resolution, aspect, seed,
                    refs, audio_refs, gen_audio, want_last_frame,
                    video_refs=None, to_url=None, video_to_url=None):
    """构造方舟视频任务请求体(独立函数便于 dry-run 校验;to_url 可替换文件内联逻辑,
    video_to_url 单独指定参考视频的 URL 化方式——方舟要求 reference_video 为公网 URL)。"""
    to_url = to_url or _file_to_data_url
    video_to_url = video_to_url or to_url
    gen = _seedance_gen(cfg["model"])
    is_v2 = gen >= 2.0
    is_v25 = gen >= 2.5
    # 按模型代际取参考素材上限(2.5 全面放宽:30图/10视频/10音频,总时长各 ≤30s)
    max_refs = V25_MAX_VIDEO_REFS if is_v25 else MAX_VIDEO_REFS
    max_arefs = V25_MAX_AUDIO_REFS if is_v25 else MAX_AUDIO_REFS
    max_atotal = V25_MAX_AUDIO_TOTAL_S if is_v25 else MAX_AUDIO_TOTAL_S
    max_vrefs = V25_MAX_VIDEOIN_REFS if is_v25 else MAX_VIDEOIN_REFS
    ver_name = "Seedance 2.5" if is_v25 else "Seedance 2.0"
    if refs and (first or last):
        raise RuntimeError("首帧/首尾帧与多参考图(--ref)是互斥模式,不能同时传")
    if refs and len(refs) > max_refs:
        raise RuntimeError(f"参考图最多 {max_refs} 张({ver_name}),收到 {len(refs)}")
    if audio_refs and len(audio_refs) > max_arefs:
        raise RuntimeError(f"参考音频最多 {max_arefs} 段({ver_name}),收到 {len(audio_refs)}")
    if audio_refs:
        # 参考音频总时长前置机检(§7A audioref_total_le_15s;2.5 放宽至 30s):
        # 总时长超限方舟必拒,提交前拦下并给出修法
        durs = [_audio_duration_s(p) for p in audio_refs]
        if any(d is None for d in durs):
            print("[genmedia] 无法实测参考音频时长(ffprobe 不可用?),"
                  f"跳过总时长预检(方舟硬限总时长 {max_atotal}s)",
                  file=sys.stderr)
        elif sum(durs) > max_atotal:
            detail = "、".join(f"{Path(p).name}={d:.1f}s"
                               for p, d in zip(audio_refs, durs))
            raise RuntimeError(
                f"参考音频总时长 {sum(durs):.1f}s 超过方舟硬限 {max_atotal}s"
                f"({ver_name};§7A):{detail}。"
                "请截短样本后重试(voiceprint 规格 ≤5s/段,§8A;可用 "
                "ffmpeg -i in.mp3 -t 4.9 -c copy out.mp3 截断)")
    if video_refs:
        if not is_v2:
            raise RuntimeError("参考视频(--ref-video,V2V 编辑/延长)仅 Seedance 2.x 系列支持")
        if first or last:
            raise RuntimeError("参考视频(--ref-video)与首帧/尾帧是互斥模式,不能同时传")
        if len(video_refs) > max_vrefs:
            raise RuntimeError(f"参考视频最多 {max_vrefs} 个({ver_name}),收到 {len(video_refs)}")
        for v in video_refs:
            p = Path(v)
            if p.is_file() and p.stat().st_size > MAX_VIDEOIN_BYTES:
                raise RuntimeError(f"参考视频 {v} 超过 {MAX_VIDEOIN_BYTES // 1024 // 1024}MB"
                                   "(方舟参考视频单文件上限),请先压缩")
        # 参考视频总时长预检(2.0 ≤15s / 2.5 ≤30s;ffprobe 不可用则跳过交方舟拒绝)
        vmax = V25_MAX_VIDEOIN_TOTAL_S if is_v25 else 15.2
        vdurs = [_audio_duration_s(str(v)) for v in video_refs
                 if Path(str(v)).is_file()]
        if len(vdurs) == len(video_refs) and all(d is not None for d in vdurs) \
                and sum(vdurs) > vmax:
            raise RuntimeError(
                f"参考视频总时长 {sum(vdurs):.1f}s 超过方舟硬限 {vmax}s({ver_name}),请先截短")
    # Seedance 2.5 特殊任务约束(官方文档:违规将异步报错 TaskTypeConstraint):
    # 首帧/首尾帧任务 ratio 仅支持 adaptive → 自动改写;编辑/延长意图仅预警不拦截
    if is_v25 and aspect and aspect != "adaptive":
        if first or last:
            print(f"[genmedia] Seedance 2.5 首帧/首尾帧任务仅支持 ratio=adaptive"
                  f"(输出自动与首帧图同比),已忽略 --aspect {aspect}", file=sys.stderr)
            aspect = "adaptive"
        elif video_refs and (_V25_EDIT_RE.search(prompt) or _V25_EXTEND_RE.search(prompt)):
            print(f"[genmedia] Seedance 2.5 视频编辑/延长任务仅支持 ratio=adaptive"
                  f"(输出自动与输入视频同比),已忽略 --aspect {aspect}", file=sys.stderr)
            aspect = "adaptive"
    if is_v25 and video_refs and _V25_EDIT_RE.search(prompt) \
            and duration is not None and duration != -1:
        print(f"[genmedia] 提示词疑似视频编辑任务:Seedance 2.5 编辑任务 duration 仅支持 -1"
              f"(自动与输入视频等长),当前 --duration {duration:g} 若被判定为编辑将异步报错",
              file=sys.stderr)
    if is_v25 and resolution and resolution not in ("480p", "720p"):
        print(f"[genmedia] Seedance 2.5 仅支持 480p/720p,分辨率 {resolution} 已压到 720p",
              file=sys.stderr)
        resolution = "720p"
    text = prompt
    if resolution:
        text += f" --resolution {resolution}"
    if duration:
        if is_v2:
            # Seedance 2.x 只收整数秒或 -1(模型自定时长):2.0 为 [4,15],2.5 为 [4,30]
            dmax = 30 if is_v25 else 15
            d = int(round(duration))
            if d != duration:
                print(f"[genmedia] {ver_name} 时长需整数,{duration} 取整为 {d}", file=sys.stderr)
            if d != -1 and not 4 <= d <= dmax:
                raise RuntimeError(f"{ver_name} 时长须在 [4,{dmax}] 秒或 -1,收到 {d}")
            text += f" --dur {d}"
        else:
            text += f" --dur {duration:g}"
    if aspect:
        text += f" --ratio {aspect}"
    if seed is not None:
        if is_v2:
            print(f"[genmedia] {ver_name} 不支持 seed,已忽略", file=sys.stderr)
        else:
            text += f" --seed {seed}"
    content = [{"type": "text", "text": text}]
    for path, role in ((first, "first_frame"), (last, "last_frame")):
        if path:
            content.append({"type": "image_url", "role": role,
                            "image_url": {"url": to_url(path)}})
    for path in refs or []:
        # 已入虚拟人像库的参考图改用 asset://<id> 提交(仅 Seedance 2.x 支持资产 URI)
        asset_uri = _avatar_asset_uri(path) if is_v2 else None
        if asset_uri:
            print(f"[genmedia] 参考图已入虚拟人像库,以资产 URI 提交:"
                  f"{Path(path).name} → {asset_uri}", file=sys.stderr, flush=True)
        content.append({"type": "image_url", "role": "reference_image",
                        "image_url": {"url": asset_uri or to_url(path)}})
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


def _record_image_usage(output, cfg, usage, width, height):
    """图像生成成功后把接口返回的 usage 追加到输出目录 usage_ledger.jsonl(镜像
    _record_video_usage 的台账机制,kind=image 供工作流预览按图/视频分桶统计)。
    方舟图像 usage 无 completion_tokens,以 output_tokens 对齐口径。
    落盘失败只告警不中断:图像本体已保存,计费记录不应影响产出。"""
    rec = {"file": Path(output).name, "kind": "image",
           "provider": cfg.get("provider"), "model": cfg.get("model"),
           "size": f"{width}x{height}",
           "completion_tokens": usage.get("output_tokens"),
           "total_tokens": usage.get("total_tokens"),
           "generated_images": usage.get("generated_images"),
           "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        ledger = Path(output).parent / "usage_ledger.jsonl"
        with open(ledger, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[genmedia] usage 台账写入失败(不影响产出):{e}", file=sys.stderr)


def _record_video_usage(output, task_id, cfg, usage, resolution, duration):
    """成功生成后把方舟查询接口返回的 usage 落盘,双写:
    ① 同名 .meta.json 的 usage 字段(合并写,已有内容保留;下游 agent 重写 meta 时应保留该字段)
    ② 输出目录 usage_ledger.jsonl 追加一行累计台账——重roll/覆盖/删档都不丢历史,
       是项目级 token 消耗统计(工作流预览 API workflow preview)的权威数据源。
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


# ---------------- 视频:MiniMax(POST /v2/video_generation,MiniMax-H3) ----------------

# H3 分辨率仅 768P/2K 两档;项目「输出设置」档位(360p..4k)就近映射,避免与现有
# draft/final 分辨率闸门(_resolution_gate)冲突:草稿档一律 768P,1080p/4k 走 2K
MINIMAX_RESOLUTION_MAP = {"360p": "768P", "480p": "768P", "720p": "768P",
                          "1080p": "2K", "4k": "2K", "768p": "768P", "2k": "2K"}
MINIMAX_VIDEO_RATIOS = ("21:9", "16:9", "4:3", "1:1", "3:4", "9:16")
MINIMAX_MAX_VIDEO_REFS = 9   # H3 reference_image 上限

# H3 校验 data URI 时由 MIME 子类型反推扩展名做白名单匹配,mimetypes 的标准值会被拒
# (audio/mpeg→".mpeg"、audio/x-wav→".x-wav"),须显式映射为「子类型=扩展名」的写法
MINIMAX_AUDIO_MIME = {".mp3": "audio/mp3", ".wav": "audio/wav", ".m4a": "audio/m4a",
                      ".aac": "audio/aac", ".flac": "audio/flac", ".ogg": "audio/ogg"}


def _minimax_audio_data_url(path: str) -> str:
    p = Path(path)
    if not p.is_file():
        raise RuntimeError(f"参考音频不存在: {path}")
    mime = MINIMAX_AUDIO_MIME.get(p.suffix.lower())
    if not mime:
        raise RuntimeError(f"MiniMax-H3 参考音频扩展名不受支持: {p.name}"
                           f"(支持 {'/'.join(sorted(MINIMAX_AUDIO_MIME))}),请先转码为 .mp3/.wav")
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def _minimax_video_resolution(resolution: str) -> str:
    if not resolution:
        return "768P"
    mapped = MINIMAX_RESOLUTION_MAP.get(resolution.lower())
    if not mapped:
        raise RuntimeError(f"MiniMax-H3 分辨率仅 768P/2K 两档,无法映射 {resolution}"
                           f"(可映射档位:{'/'.join(sorted(MINIMAX_RESOLUTION_MAP))})")
    if mapped.lower() != resolution.lower():
        print(f"[genmedia] MiniMax-H3 分辨率仅 768P/2K,{resolution} 已就近映射为 {mapped}",
              file=sys.stderr)
    return mapped


def _minimax_video_task(cfg, path: str, body: dict, output: str) -> str:
    """提交 MiniMax 异步视频任务并轮询到完成,下载产物到 output(生成与超分共用)。"""
    task = _minimax_post(cfg, path, body)
    tid = task.get("task_id")
    if not tid:
        raise RuntimeError(f"MiniMax 视频任务创建失败:"
                           f"{json.dumps(task, ensure_ascii=False)[:400]}")
    print(f"[genmedia] 任务已创建 {tid} → {Path(output).name}", file=sys.stderr, flush=True)
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    query_url = f"{_minimax_base(cfg)}/v2/query/video_generation/{tid}"
    started = time.time()
    deadline = started + VIDEO_TIMEOUT
    last_status, last_beat = "", started
    while time.time() < deadline:
        time.sleep(VIDEO_POLL_INTERVAL)
        try:
            st = _get_json(query_url, headers)
        except Exception as e:
            # 轮询瞬时失败不中止:任务已在 MiniMax 侧运行,中止会诱发上层重试重复计费
            print(f"[genmedia] 轮询失败({e}),{VIDEO_POLL_INTERVAL}s 后重试",
                  file=sys.stderr, flush=True)
            continue
        # regeneration 查询响应包一层 task 对象,生成任务为扁平结构,两种形态都兼容
        st = st.get("task") or st
        status = st.get("status")
        now = time.time()
        if status != last_status or now - last_beat >= 60:
            print(f"[genmedia] {Path(output).name}: {status},已等待 {int(now - started)}s",
                  file=sys.stderr, flush=True)
            last_status, last_beat = status, now
        if status == "succeeded":
            url = (st.get("content") or {}).get("url")
            if not url:
                raise RuntimeError(f"MiniMax 任务成功但无视频 URL:{json.dumps(st)[:400]}")
            return _save(_request(url, timeout=600), output)
        if status in ("failed", "cancelled"):
            raise RuntimeError(f"MiniMax 视频任务失败:"
                               f"{json.dumps(st, ensure_ascii=False)[:400]}")
    raise RuntimeError(f"MiniMax 视频超时({VIDEO_TIMEOUT}s),task={tid}")


def _video_minimax(cfg, prompt, first, last, duration, resolution, aspect, seed, output,
                   refs=None, audio_refs=None, gen_audio=None, return_last_frame="",
                   video_refs=None):
    if refs and (first or last):
        raise RuntimeError("首帧/首尾帧与多参考图(--ref)是互斥模式,不能同时传")
    if video_refs and (first or last):
        raise RuntimeError("参考视频(--ref-video)与首帧/尾帧是互斥模式,不能同时传")
    if refs and len(refs) > MINIMAX_MAX_VIDEO_REFS:
        raise RuntimeError(f"MiniMax-H3 参考图最多 {MINIMAX_MAX_VIDEO_REFS} 张,收到 {len(refs)}")
    if gen_audio is False:
        print("[genmedia] MiniMax-H3 原生音画同生,不支持关闭 generate_audio,已忽略",
              file=sys.stderr)
    d = int(round(duration)) if duration else 5
    if duration and d != duration:
        print(f"[genmedia] MiniMax-H3 时长需整数,{duration} 取整为 {d}", file=sys.stderr)
    if not 4 <= d <= 15:
        raise RuntimeError(f"MiniMax-H3 时长须在 [4,15] 整数秒,收到 {d}")
    content = [{"type": "text", "text": prompt}]
    for path, role in ((first, "first_frame"), (last, "last_frame")):
        if path:
            content.append({"type": "image_url", "role": role,
                            "image_url": {"url": _file_to_data_url(path)}})
    for path in refs or []:
        content.append({"type": "image_url", "role": "reference_image",
                        "image_url": {"url": _file_to_data_url(path)}})
    for path in video_refs or []:
        # 参考视频体积大,内联 base64 易超请求体上限,与方舟同策略走对象存储预签名 URL
        content.append({"type": "video_url", "role": "reference_video",
                        "video_url": {"url": _storage_upload_url(path)}})
    for path in audio_refs or []:
        content.append({"type": "audio_url", "role": "reference_audio",
                        "audio_url": {"url": _minimax_audio_data_url(path)}})
    body = {"model": cfg["model"], "content": content,
            "resolution": _minimax_video_resolution(resolution), "duration": d}
    has_media = len(content) > 1
    if aspect and aspect in MINIMAX_VIDEO_RATIOS:
        body["ratio"] = aspect
    elif aspect and not has_media:
        raise RuntimeError(f"MiniMax-H3 文生视频画幅仅支持 "
                           f"{'/'.join(MINIMAX_VIDEO_RATIOS)},收到 {aspect}")
    elif aspect:
        print(f"[genmedia] MiniMax-H3 不支持画幅 {aspect},按参考素材自适应(adaptive)",
              file=sys.stderr)
    elif not has_media:
        body["ratio"] = "16:9"   # 文生视频 ratio 必填且不能 adaptive
    saved = _minimax_video_task(cfg, "/v2/video_generation", body, output)
    if return_last_frame:
        # H3 无 last_frame 返回参数,续接锚从成片本地抽取
        _extract_last_frame(saved, return_last_frame)
    return saved


# ---------------- 超分:MiniMax / ComfyUI SeedVR2 ----------------

# /v2/video_regeneration 仅支持 MiniMax-H3 + resolution=2K;源视频须满足 H3 768P
# 直出成片规格,不合规提交即拒——提交前用 ffprobe 预检拦下并给出修法
MINIMAX_UPSCALE_MODEL = "MiniMax-H3"
MINIMAX_UPSCALE_MIN_FRAMES = 107     # 约 4s @24fps
MINIMAX_UPSCALE_MAX_FRAMES = 362     # 约 15s @24fps
MINIMAX_UPSCALE_MAX_AREA = 768 * 1344   # 1,032,192 px


def _minimax_upscale_config() -> dict:
    """超分共用「生成模型」页视频段的 MiniMax 凭证(api_key/api_base),与生效视频
    渠道无关——只要 MiniMax Key 已配置(或设环境变量 MINIMAX_API_KEY)即可用。"""
    try:
        pc = dict((json.loads(CONFIG_PATH.read_text()).get("video") or {}).get("minimax") or {})
    except Exception:
        pc = {}
    pc["api_key"] = _minimax_key(pc) or os.environ.get(ENV_KEYS["minimax"], "")
    if not pc["api_key"]:
        raise RuntimeError("超分渠道 minimax 未配置 API Key(Web 控制台「🎨 生成模型」"
                           "视频生成的 MiniMax 标签页填入当前接口区域的 Key,"
                           "或设环境变量 MINIMAX_API_KEY)")
    return {"provider": "minimax", **pc}


def _probe_video_meta(path: str) -> dict | None:
    """ffprobe 实测视频规格(宽高/fps/帧数/有无音轨);ffprobe 不可用/失败返回 None
    (跳过预检,交由 MiniMax 侧拒绝)。"""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error",
             "-show_entries", "stream=codec_type,width,height,r_frame_rate,nb_frames",
             "-show_entries", "format=duration", "-of", "json", path],
            capture_output=True, text=True, timeout=30)
        info = json.loads(out.stdout)
        meta = {"has_audio": False, "width": 0, "height": 0, "fps": 0.0, "frames": 0}
        for s in info.get("streams") or []:
            if s.get("codec_type") == "audio":
                meta["has_audio"] = True
            elif s.get("codec_type") == "video":
                meta["width"] = int(s.get("width") or 0)
                meta["height"] = int(s.get("height") or 0)
                num, _, den = (s.get("r_frame_rate") or "0/1").partition("/")
                meta["fps"] = float(num) / float(den) if den and float(den) else 0.0
                meta["frames"] = int(s.get("nb_frames") or 0)
        if not meta["frames"] and meta["fps"]:
            dur = float((info.get("format") or {}).get("duration") or 0)
            meta["frames"] = int(round(dur * meta["fps"]))
        return meta if meta["width"] else None
    except Exception:
        return None


def _minimax_upscale_precheck(path: str) -> None:
    """Regenerate-2K 源视频规格预检(须为 MiniMax-H3 768P 直出成片口径):
    含音轨、24fps、宽高均被 32 整除、面积 ≤768×1344、107-362 帧(约 4-15s)。"""
    meta = _probe_video_meta(path)
    if meta is None:
        print("[genmedia] 无法实测源视频规格(ffprobe 不可用?),跳过预检,"
              "不合规将被 MiniMax 侧拒绝", file=sys.stderr)
        return
    w, h = meta["width"], meta["height"]
    problems = []
    if not meta["has_audio"]:
        problems.append("缺少音轨(源视频必须含音频)")
    if round(meta["fps"]) != 24:
        problems.append(f"帧率 {meta['fps']:.2f}fps ≠ 24fps")
    if w % 32 or h % 32:
        problems.append(f"分辨率 {w}x{h} 宽高须均被 32 整除")
    if w * h > MINIMAX_UPSCALE_MAX_AREA:
        problems.append(f"面积 {w}x{h}={w * h}px 超过 {MINIMAX_UPSCALE_MAX_AREA}px(768×1344)")
    if meta["frames"] and not \
            MINIMAX_UPSCALE_MIN_FRAMES <= meta["frames"] <= MINIMAX_UPSCALE_MAX_FRAMES:
        problems.append(f"帧数 {meta['frames']} 不在 [{MINIMAX_UPSCALE_MIN_FRAMES},"
                        f"{MINIMAX_UPSCALE_MAX_FRAMES}](约 4-15s @24fps)")
    if problems:
        raise RuntimeError(
            f"源视频 {Path(path).name} 不满足 Regenerate-2K 输入规格"
            f"(须为 MiniMax-H3 768P 直出成片):{';'.join(problems)}。"
            "该超分渠道仅适用 H3 768P 生成的 clip,不合规请回退常规超分手段")


def _upscale_minimax(cfg, input_video: str, prompt: str, source_task_id: str,
                     output: str) -> str:
    if source_task_id:
        if input_video:
            raise RuntimeError("--input 与 --source-task-id 互斥:传任务 id 时无需源视频")
        body = {"model": MINIMAX_UPSCALE_MODEL, "source_task_id": source_task_id,
                "resolution": "2K"}
    else:
        p = Path(input_video or "")
        if not input_video or not p.is_file():
            raise RuntimeError(f"源视频不存在: {input_video or '(未传 --input)'}")
        if not prompt:
            raise RuntimeError("base_video 模式必须传 --prompt(该组生成时的原始 video_prompt)")
        _minimax_upscale_precheck(str(p))
        # 请求体上限 64MB:小文件 base64 内联,大文件与参考视频同策略走对象存储预签名 URL
        url = _storage_upload_url(str(p)) if p.stat().st_size > MAX_VIDEOIN_BYTES \
            else _file_to_data_url(str(p))
        body = {"model": MINIMAX_UPSCALE_MODEL, "resolution": "2K",
                "content": [{"type": "text", "text": prompt},
                            {"type": "video_url", "role": "base_video",
                             "video_url": {"url": url}}]}
    return _minimax_video_task(cfg, "/v2/video_regeneration", body, output)


SEEDVR2_WORKFLOW = "comfy/video-upscale-seedvr2-api.json"


def _seedvr2_dimensions(aspect: str, resolution: str) -> tuple[int, int]:
    ratio_text = aspect or "16:9"
    try:
        numerator, denominator = (float(x.strip()) for x in ratio_text.split(":", 1))
        ratio = numerator / denominator
        if ratio <= 0:
            raise ValueError
    except (TypeError, ValueError, ZeroDivisionError):
        raise RuntimeError(f"超分画幅无效: {aspect or ratio_text},应为如 16:9")
    short_side = {"360p": 360, "480p": 480, "720p": 720,
                  "1080p": 1080, "2k": 1440, "4k": 2160}.get(
                      (resolution or "1080p").lower())
    if not short_side:
        raise RuntimeError(f"超分分辨率不支持: {resolution},可选 360p/480p/720p/1080p/2k/4k")
    if ratio >= 1:
        width, height = short_side * ratio, short_side
    else:
        width, height = short_side, short_side / ratio
    return max(8, round(width / 8) * 8), max(8, round(height / 8) * 8)


def _seedvr2_config() -> dict:
    """读取「生成模型」页视频段的 ComfyUI 配置,超分工作流使用内置模板。"""
    try:
        pc = dict((json.loads(CONFIG_PATH.read_text()).get("video") or {}).get("comfyui") or {})
    except Exception:
        pc = {}
    if not pc:
        raise RuntimeError("SeedVR2 超分需要先在「🎨 生成模型」页配置视频 ComfyUI 渠道")
    # 本地 / Comfy Cloud 用内置模板;RunningHub 运行方式改用「🎨 生成模型」页 RunningHub
    # 渠道选中的云端超分工作流(rh_workflow_id),节点直绑见 _apply_rh_upscale_bindings
    pc["workflow"] = SEEDVR2_WORKFLOW
    return {"provider": "comfyui", **pc}


def _upscale_default_resolution() -> str:
    project = os.environ.get("VIDEOAGENTS_PROJECT", "")
    if project:
        try:
            settings = json.loads((DATA_DIR / "projects" / project / "settings.json").read_text())
            return str((settings.get("output") or {}).get("final_resolution") or "1080p")
        except Exception:
            pass
    return "1080p"


def _rh_bind_number_like(workflow: dict, inputs: dict, key: str, value) -> bool:
    """数值位直绑(含数值字符串):RunningHub 导出的 Int/primitive 节点常把 value 存成
    "4096" 这类字符串,_rh_bind_number 只认 int/float 会漏绑;这里按原类型回写。"""
    if _rh_bind_number(workflow, inputs, key, value):
        return True
    slot = inputs.get(key)
    if isinstance(slot, str) and slot.strip().lstrip("-").isdigit():
        inputs[key] = str(value)
        return True
    if _node_link(slot):
        linked = (workflow.get(str(slot[0])) or {}).setdefault("inputs", {})
        cur = linked.get("value")
        if isinstance(cur, str) and cur.strip().lstrip("-").isdigit():
            linked["value"] = str(value)
            return True
    return False


def _rh_upscale_video_node(workflow: dict) -> tuple[dict, str]:
    """定位云端超分工作流唯一的源视频加载节点,返回 (inputs, 文件名键)。
    兼容 VHS_LoadVideo(video)/ 原生 LoadVideo(file)/ 其他 *LoadVideo* 节点。"""
    found = []
    for nid, node in workflow.items():
        if not isinstance(node, dict):
            continue
        cls = str(node.get("class_type") or "")
        if "loadvideo" not in cls.lower().replace("_", "").replace(" ", ""):
            continue
        inputs = node.setdefault("inputs", {})
        for key in ("video", "file", "video_path", "path"):
            if isinstance(inputs.get(key), str):
                found.append((nid, cls, inputs, key))
                break
    if len(found) != 1:
        raise RuntimeError(
            f"RunningHub 超分工作流须恰好 1 个源视频加载节点(LoadVideo 系,找到 {len(found)} 个),"
            "无法绑定 --input;请精简云端工作流或改用 {{VIDEO}} 占位符")
    return found[0][2], found[0][3]


def _rh_upscale_resize_node(workflow: dict) -> tuple[str, dict] | None:
    """定位决定目标尺寸的缩放节点:优先顺 SeedVR2Preprocess.resized_images 上溯,
    否则按已知缩放节点特征扫描;返回 (节点id, inputs),找不到返回 None。"""
    def is_resize(inputs: dict) -> bool:
        return ("scale_to_side" in inputs and "scale_to_length" in inputs) or \
            ("width" in inputs and "height" in inputs) or \
            ("resize_type.width" in inputs and "resize_type.height" in inputs)

    for node in workflow.values():
        if isinstance(node, dict) and node.get("class_type") == "SeedVR2Preprocess":
            link = (node.get("inputs") or {}).get("resized_images")
            for _ in range(8):  # 途经纯透传节点继续上溯
                if not _node_link(link):
                    break
                nid = str(link[0])
                up = workflow.get(nid)
                if not isinstance(up, dict):
                    break
                inputs = up.setdefault("inputs", {})
                if is_resize(inputs):
                    return nid, inputs
                link = inputs.get("image") or inputs.get("images") or inputs.get("input")
    for nid, node in workflow.items():
        if not isinstance(node, dict):
            continue
        inputs = node.setdefault("inputs", {})
        cls = str(node.get("class_type") or "").lower()
        if "scale_to_side" in inputs or (is_resize(inputs) and ("resize" in cls or "scale" in cls)):
            return nid, inputs
    return None


def _apply_rh_upscale_bindings(workflow: dict, video_name: str, width: int, height: int,
                               seed) -> dict:
    """RunningHub 超分云工作流的无占位符直绑兜底(与视频 H3 / 图像分支同款语义)。

    RH 工作区导出件通常没有 {{VIDEO}}/{{WIDTH}} 占位符而是作者演示字面值——源视频是
    作者的演示文件名、目标长边是模板默认值(如 4096),占位符替换会空转;这里顺节点
    直接写入本次源视频、目标尺寸与种子(占位符模板下为幂等覆写)。返回本次实际绑定
    摘要(target 描述)供日志/回执使用。"""
    inputs, key = _rh_upscale_video_node(workflow)
    inputs[key] = video_name
    summary = {"video_key": key}

    resize = _rh_upscale_resize_node(workflow)
    if resize is None:
        raise RuntimeError(
            "RunningHub 超分工作流未定位到目标尺寸缩放节点(SeedVR2Preprocess 上游的 "
            "ImageScaleByAspectRatio / ResizeImageMaskNode / width+height 类节点),"
            "--resolution/--aspect 无法注入;请精简云端工作流或改用 {{WIDTH}}/{{HEIGHT}} 占位符")
    nid, r = resize
    if "scale_to_side" in r and "scale_to_length" in r:
        # LayerUtility ImageScaleByAspectRatio V2:按边长缩放,画幅跟随源视频
        side = str(r.get("scale_to_side") or "").lower()
        if side == "longest":
            target = max(width, height)
        elif side == "shortest":
            target = min(width, height)
        elif side == "width":
            target = width
        elif side == "height":
            target = height
        else:
            r["scale_to_side"] = "longest"
            side, target = "longest", max(width, height)
        if not _rh_bind_number_like(workflow, r, "scale_to_length", target):
            raise RuntimeError(f"RunningHub 超分工作流节点 {nid} 的 scale_to_length 位不可写"
                               f"(值 {r.get('scale_to_length')!r}),目标尺寸无法注入")
        summary["target"] = f"{side}={target}"
    elif "resize_type.width" in r:
        r["resize_type"] = "scale dimensions"
        r["resize_type.width"], r["resize_type.height"] = width, height
        summary["target"] = f"{width}x{height}"
    else:
        ok_w = _rh_bind_number_like(workflow, r, "width", width)
        ok_h = _rh_bind_number_like(workflow, r, "height", height)
        if not (ok_w and ok_h):
            raise RuntimeError(f"RunningHub 超分工作流节点 {nid} 的 width/height 位不可写,"
                               "目标尺寸无法注入")
        summary["target"] = f"{width}x{height}"

    if seed is not None:
        try:
            _apply_rh_image_seed(workflow, _image_primary_sampler(workflow), seed)
        except RuntimeError:
            print("[genmedia] RunningHub 超分工作流未定位到采样器,seed 未注入(按模板内种子生成)",
                  file=sys.stderr)

    # VHS 元批处理(VHS_BatchManager + LoadVideo/VideoCombine 的 meta_batch)靠服务端
    # 把同一提示词以新 prompt_id 反复重排队分批处理,RunningHub 只跟踪首个 prompt:
    # 首批(如 36 帧)跑完即报 SUCCESS 而产物要到末批才落盘,表现为「任务成功但无产物」
    # (实测 taskId 2089256283901550594)。RH 提交一律拆掉批处理,整段一次处理
    batch_ids = {nid for nid, node in workflow.items() if isinstance(node, dict)
                 and node.get("class_type") == "VHS_BatchManager"}
    if batch_ids:
        for nid in batch_ids:
            workflow.pop(nid)
        for node in workflow.values():
            inputs = node.get("inputs") if isinstance(node, dict) else None
            if inputs and _node_link(inputs.get("meta_batch")) \
                    and str(inputs["meta_batch"][0]) in batch_ids:
                inputs.pop("meta_batch")
        summary["unbatched"] = sorted(batch_ids)

    # 作者常并挂一个 save_output=false 的预览合成节点:纯落盘旁路,不影响执行图,
    # 但云端会重复编码一遍且产物清单里多一份无音轨预览;有正式落盘节点时剪掉
    combines = {nid: node for nid, node in workflow.items()
                if isinstance(node, dict) and "videocombine" in
                str(node.get("class_type") or "").lower()}
    if any((n.get("inputs") or {}).get("save_output") is not False for n in combines.values()):
        for nid, node in combines.items():
            if (node.get("inputs") or {}).get("save_output") is False:
                workflow.pop(nid)
                summary.setdefault("pruned_preview", []).append(nid)
    return summary


def _upscale_seedvr2(cfg, input_video: str, output: str, resolution: str,
                     aspect: str, seed: int) -> str:
    p = Path(input_video or "")
    if not input_video or not p.is_file():
        raise RuntimeError(f"源视频不存在: {input_video or '(未传 --input)'}")
    width, height = _seedvr2_dimensions(aspect, resolution)
    if _comfy_is_rh(cfg):
        uploaded = _rh_upload(cfg, str(p))
        workflow = _comfy_workflow(cfg, {"VIDEO": uploaded, "WIDTH": width,
                                         "HEIGHT": height, "SEED": seed}, "video")
        summary = _apply_rh_upscale_bindings(workflow, uploaded, width, height, seed)
        print(f"[genmedia] RunningHub 超分直绑:video={uploaded} target={summary.get('target')}"
              + (f" 剪除预览合成节点 {summary['pruned_preview']}"
                 if summary.get("pruned_preview") else "")
              + (f" 拆除 VHS 元批处理 {summary['unbatched']}(RH 不支持重排队分批)"
                 if summary.get("unbatched") else ""), file=sys.stderr)
        return _rh_run(cfg, workflow, output, want_video=True)
    base, headers = _comfy_endpoint(cfg)
    uploaded = _comfy_upload(base, str(p), headers)
    workflow = _comfy_workflow(cfg, {"VIDEO": uploaded, "WIDTH": width,
                                     "HEIGHT": height, "SEED": seed}, "video")
    return _comfy_run(base, workflow, output, want_video=True, headers=headers)


def _video_comfyui(cfg, prompt, first, last, duration, resolution, aspect, seed, output,
                   refs=None, audio_refs=None, generate_audio=None, return_last_frame="",
                   video_refs=None, ref_image_size=""):
    rh = _comfy_is_rh(cfg)
    base = hdrs = None
    if not rh:
        base, hdrs = _comfy_endpoint(cfg)

    def upload(path):
        return _rh_upload(cfg, path) if rh else _comfy_upload(base, path, hdrs)

    h3 = _is_h3_ref2va_workflow(cfg)
    if h3:
        if first or last or video_refs:
            raise RuntimeError("当前 MiniMax-H3 Ref2VA 工作流支持多图/音频参考;"
                               "首尾帧与 --ref-video 请使用对应 H3 FL2VA/视频参考工作流")
        if generate_audio is False:
            raise RuntimeError("MiniMax-H3 Ref2VA 固定输出原生音频,不支持 --generate-audio off")
        settings = _h3_settings()
        if ref_image_size:
            settings["ref_image_size"] = ref_image_size
        if not rh:
            # RunningHub 无 /object_info 组件预检;节点/模型缺失由建任务 promptTips
            # 或任务失败详情报出
            _comfy_h3_validate_components(base, settings, hdrs)
        prompt = _h3_reference_tags(prompt)
        width, height = _h3_dimensions(aspect, resolution)
        tokens = {
            "PROMPT": prompt, "SEED": seed, "WIDTH": width, "HEIGHT": height,
            "FPS": settings["fps"], "H3_FRAMES": _comfy_h3_frame_count(duration, settings["fps"]),
            "H3_UNET": settings["unet"], "H3_TEXT_ENCODER": settings["text_encoder"],
            "H3_VIDEO_VAE": settings["video_vae"], "H3_AUDIO_VAE": settings["audio_vae"],
            "H3_WEIGHT_DTYPE": settings["weight_dtype"], "H3_CLIP_DEVICE": settings["clip_device"],
            "H3_SAMPLER": settings["sampler"], "H3_SCHEDULER": settings["scheduler"],
            "H3_STEPS": settings["steps"], "H3_REF_IMAGE_SIZE": settings["ref_image_size"],
        }
        wf = _comfy_workflow(cfg, tokens, "video")
        # 云端导出件(尤其 RunningHub 工作区模板)常无 {{TOKEN}} 占位符而是作者演示
        # 字面值,占位符替换会空转;顺 H3 节点连线直接绑定本次 prompt 与时长,
        # 防止演示提示词/模板默认时长静默混入生产请求(占位符模板下为幂等覆写)
        _apply_h3_prompt(wf, prompt)
        _apply_h3_duration(wf, duration)
        _apply_h3_ref_image_size(wf, settings["ref_image_size"])
        _add_h3_references(wf, [upload(path) for path in refs or []],
                           [upload(path) for path in audio_refs or []])
        saved = (_rh_run(cfg, wf, output, want_video=True) if rh
                 else _comfy_run(base, wf, output, want_video=True, headers=hdrs))
        if return_last_frame:
            _extract_last_frame(saved, return_last_frame)
        return saved
    ltx25 = _is_ltx25_workflow(cfg)
    if ltx25:
        if audio_refs:
            raise RuntimeError("LTX-2.5 原生工作流支持生成音频;当前模板不接受外部参考音频，"
                               "请使用 --prompt 描述音频内容")
        if generate_audio is False:
            raise RuntimeError("LTX-2.5 音视频联合工作流固定输出原生音频,不支持 --generate-audio off")
        settings = _ltx25_settings()
        width, height = _ltx25_dimensions(aspect, resolution)
        frames = _comfy_ltx_video_frame_count(duration, settings["fps"])
        tokens = {
            "PROMPT": prompt, "NEGATIVE": "", "SEED": seed,
            "WIDTH": width, "HEIGHT": height, "FPS": settings["fps"],
            "LTX_FRAMES": frames, "LTX_UNET": settings["unet"],
            "LTX_TEXT_ENCODER": settings["text_encoder"],
            "LTX_VIDEO_VAE": settings["video_vae"], "LTX_AUDIO_VAE": settings["audio_vae"],
            "LTX_WEIGHT_DTYPE": settings["weight_dtype"],
            "LTX_CLIP_DEVICE": settings["clip_device"], "LTX_CFG": settings["cfg"],
            "LTX_SAMPLER": settings["sampler"], "LTX_SIGMAS": settings["sigmas"],
            "LTX_TILE_SIZE": settings["tile_size"], "LTX_TILE_OVERLAP": settings["tile_overlap"],
            "LTX_TEMPORAL_SIZE": settings["temporal_size"],
            "LTX_TEMPORAL_OVERLAP": settings["temporal_overlap"],
        }
        wf = _comfy_workflow(cfg, tokens, "video")
        _add_ltx25_references(wf, [upload(path) for path in refs or []],
                              upload(first) if first else "", upload(last) if last else "",
                              settings, frames)
        saved = (_rh_run(cfg, wf, output, want_video=True) if rh
                 else _comfy_run(base, wf, output, want_video=True, headers=hdrs))
        if return_last_frame:
            _extract_last_frame(saved, return_last_frame)
        return saved
    if ref_image_size:
        raise RuntimeError("--ref-image-size 仅 MiniMax-H3 Ref2VA 工作流支持"
                           "(控制参考图是否压缩到输出像素面积),当前工作流请去掉该参数")
    sd_gen = _seedance_cloud_workflow_gen(cfg)
    if sd_gen:
        is_v25 = sd_gen >= 2.5
        ver_name = "Seedance 2.5" if is_v25 else "Seedance 2.0"
        if not rh and (cfg.get("mode") or "local") != "cloud":
            raise RuntimeError(f"{ver_name} 工作流用的 ByteDance2ReferenceNode 是 "
                               "Comfy Cloud 付费 API 节点,本地 ComfyUI 无法执行;"
                               "请把 ComfyUI 运行方式切到 Comfy Cloud")
        if first or last or video_refs:
            raise RuntimeError(f"当前 {ver_name} 云工作流是参考图生视频(r2v)模板,"
                               "不支持首尾帧与 --ref-video;这些模式请另建对应工作流"
                               "或改用方舟(火山引擎/BytePlus)渠道")
        if audio_refs:
            raise RuntimeError(f"{ver_name} 云节点(ByteDance2ReferenceNode)未暴露"
                               "参考音频接口;需要 --audio-ref 请改用方舟"
                               "(火山引擎/BytePlus)渠道")
        max_refs = V25_MAX_VIDEO_REFS if is_v25 else MAX_VIDEO_REFS
        if refs and len(refs) > max_refs:
            raise RuntimeError(f"参考图最多 {max_refs} 张({ver_name}),收到 {len(refs)}")
        if is_v25 and resolution and resolution not in ("480p", "720p"):
            print(f"[genmedia] Seedance 2.5 仅支持 480p/720p,分辨率 {resolution} 已压到 720p",
                  file=sys.stderr)
            resolution = "720p"
        if not is_v25 and resolution and resolution not in ("480p", "720p", "1080p"):
            fixed = "480p" if resolution == "360p" else "1080p"
            print(f"[genmedia] Seedance 2.0 云节点分辨率档位 480p/720p/1080p,"
                  f"{resolution} 已改为 {fixed}", file=sys.stderr)
            resolution = fixed
        ratio = aspect or "adaptive"
        if ratio != "adaptive" and ratio not in ASPECT_SIZES:
            print(f"[genmedia] {ver_name} 不支持比例 {ratio},已改用 adaptive(跟随参考图)",
                  file=sys.stderr)
            ratio = "adaptive"
        # Seedance 2.x 只收整数秒或 -1(模型自定时长):2.0 为 [4,15],2.5 为 [4,30]
        d = int(round(duration)) if duration else 5
        if duration and d != duration:
            print(f"[genmedia] {ver_name} 时长需整数,{duration:g} 取整为 {d}", file=sys.stderr)
        dmax = 30 if is_v25 else 15
        if d != -1 and not 4 <= d <= dmax:
            raise RuntimeError(f"{ver_name} 时长须在 [4,{dmax}] 秒或 -1,收到 {d}")
        tokens = {"PROMPT": prompt, "SEED": seed, "DURATION": d,
                  "RESOLUTION": resolution or "720p", "RATIO": ratio,
                  "GENERATE_AUDIO": True if generate_audio is None else bool(generate_audio)}
        wf = _comfy_workflow(cfg, tokens, "video")
        _add_seedance_references(wf, [upload(path) for path in refs or []])
        saved = (_rh_run(cfg, wf, output, want_video=True) if rh
                 else _comfy_run(base, wf, output, want_video=True, headers=hdrs))
        if return_last_frame:
            _extract_last_frame(saved, return_last_frame)
        return saved
    tokens = {"PROMPT": prompt, "NEGATIVE": "", "SEED": seed,
              "DURATION": duration or 5, "WIDTH": 0, "HEIGHT": 0,
              "FRAMES": _comfy_video_frame_count(duration),
              "LTX_FRAMES": _comfy_ltx_video_frame_count(duration),
              "CHECKPOINT": cfg.get("checkpoint") or ""}
    if aspect in ASPECT_SIZES:
        tokens["WIDTH"], tokens["HEIGHT"] = ASPECT_SIZES[aspect]
    if first:
        tokens["FIRST_FRAME"] = upload(first)
    if last:
        tokens["LAST_FRAME"] = upload(last)
    wf = _comfy_workflow(cfg, tokens, "video")
    if rh:
        return _rh_run(cfg, wf, output, want_video=True)
    return _comfy_run(base, wf, output, want_video=True, headers=hdrs)


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


# ---------------- 音乐:MiniMax(POST /v1/music_generation,Music 系列) ----------------
# force_instrumental(「生成模型」页配置,默认纯音乐)走 is_instrumental;关闭时开
# lyrics_optimizer,由模型按 prompt 自动写词演唱。音频以 hex 编码随响应返回。

MM_MUSIC_FORMATS = {".mp3": "mp3", ".wav": "wav"}


def _music_minimax(cfg, prompt, output, duration_s=None):
    fmt = MM_MUSIC_FORMATS.get(Path(output).suffix.lower())
    if not fmt:
        raise RuntimeError("MiniMax 音乐输出仅支持 .mp3 / .wav 扩展名")
    if duration_s:
        print("[genmedia] MiniMax 音乐渠道不支持 --duration,已忽略(时长由模型决定)",
              file=sys.stderr)
    body = {"model": cfg["model"], "prompt": prompt[:2000],
            "output_format": "hex",
            "audio_setting": {"sample_rate": 44100, "bitrate": 256000, "format": fmt}}
    if cfg.get("force_instrumental", True):
        body["is_instrumental"] = True
    else:
        body["lyrics_optimizer"] = True   # 无独立歌词入参,按 prompt 自动写词
    resp = _minimax_post(cfg, "/v1/music_generation", body, timeout=MUSIC_TIMEOUT)
    audio_hex = (resp.get("data") or {}).get("audio") or ""
    if not audio_hex:
        raise RuntimeError(f"MiniMax 未返回音频(model={cfg['model']}):"
                           f"{json.dumps(resp, ensure_ascii=False)[:400]}")
    return _save(bytes.fromhex(audio_hex), output)


# ---------------- 音乐:ComfyUI(本地,推荐 ACE-Step 工作流) ----------------
# 占位符:PROMPT / LYRICS(默认 [Instrumental]) / DURATION 秒 / SEED
# 工作流须以 SaveAudio 或 SaveAudioMP3 落盘,见 comfy/music-ace-step-v1-api.json。

def _music_comfyui(cfg, prompt, output, duration_s=None):
    rh = _comfy_is_rh(cfg)
    base = hdrs = None
    if not rh:
        base, hdrs = _comfy_endpoint(cfg)
        if not (cfg.get("workflow") or "").strip():
            raise RuntimeError("ComfyUI 音乐生成必须在「🎨 生成模型」页配置工作流 JSON"
                               "(推荐 comfy/music-ace-step-v1-api.json)")
    duration = max(1.0, min(240.0, float(duration_s))) if duration_s and duration_s > 0 else 30.0
    # ACE-Step 纯音乐用 [Instrumental];若配置 force_instrumental=false 且未给歌词,
    # 仍走 instrumental,避免空歌词触发节点 assert。
    lyrics = (cfg.get("lyrics") or "").strip() or "[Instrumental]"
    tokens = {
        "PROMPT": prompt,
        "LYRICS": lyrics,
        "DURATION": duration,
        "SEED": random.randint(0, 2**31 - 1),
    }
    wf = _comfy_workflow(cfg, tokens, "music")
    if rh:
        # 云端工作区模板常无占位符,占位符替换空转;缺哪项就顺连线直绑哪项
        _apply_rh_music_bindings(_rh_workflow_text(cfg), wf, prompt, lyrics,
                                 duration, tokens["SEED"],
                                 bool((cfg.get("lyrics") or "").strip()))
        return _rh_run(cfg, wf, output, want_video=False)
    return _comfy_run(base, wf, output, want_video=False, headers=hdrs)


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


# ---------------- TTS:MiniMax(POST /v1/t2a_v2,Speech 系列) ----------------
# 音色为 voice_id(「生成模型」页可拉取音色库选择,克隆音色需先在平台创建);
# instructions 不支持自由文本(官方仅 emotion 枚举,不做不可靠的自动映射)。

def _tts_minimax(cfg, text, output, voice, speed, instructions):
    vid = voice or cfg.get("voice") or ""
    if not vid:
        raise RuntimeError("MiniMax TTS 未指定音色:--voice 传 voice_id,"
                           "或在「🎨 生成模型」页拉取音色库设为默认")
    voice_setting = {"voice_id": vid}
    if speed and speed != 1.0:
        voice_setting["speed"] = max(0.5, min(2.0, float(speed)))
    if instructions:
        print("[genmedia] MiniMax TTS 不支持自由文本 instructions,已忽略"
              "(情绪仅官方 emotion 枚举,可通过文本措辞/标点控制语气)", file=sys.stderr)
    fmt = "mp3" if Path(output).suffix.lower() == ".mp3" else "pcm"
    audio_setting = {"sample_rate": 24000, "format": fmt, "channel": 1}
    if fmt == "mp3":
        audio_setting["bitrate"] = 128000
    body = {"model": cfg["model"], "text": text,
            "voice_setting": voice_setting, "audio_setting": audio_setting,
            "language_boost": "auto", "output_format": "hex"}
    resp = _minimax_post(cfg, "/v1/t2a_v2", body, timeout=TTS_TIMEOUT)
    audio_hex = (resp.get("data") or {}).get("audio") or ""
    if not audio_hex:
        raise RuntimeError(f"MiniMax TTS 返回空音频(model={cfg['model']},voice={vid}):"
                           f"{json.dumps(resp, ensure_ascii=False)[:300]}")
    return _save(bytes.fromhex(audio_hex), output)


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


# ---------------- TTS:ComfyUI(本地,推荐 IndexTTS-2 工作流) ----------------
# 占位符:TEXT / REF_AUDIO(参考音频文件名) / SEED / SPEED
# 默认根据角色内容从内置音色目录自动选择参考音频(远端库按需下载缓存到 data/TimbreModel);
# voice 仅保留真实本地文件覆盖。
# 工作流须以 SaveAudio/SaveAudioMP3 落盘,见 comfy/tts-indextts2-api.json。


def _resolve_tts_reference(cfg, text, output, voice="", character="", variant="",
                           project="", instructions="") -> dict:
    if voice:
        raw = Path(voice)
        candidates = [raw] if raw.is_absolute() else [Path.cwd() / raw, ROOT / raw]
        manual = next((path.resolve() for path in candidates if path.is_file()), None)
        if manual:
            return {"path": str(manual), "file": manual.name, "score": None,
                    "reason": "explicit local file", "character": character or "manual",
                    "variant": variant or "default", "profile": {}}
        print(f"[genmedia] 忽略非本地音频 --voice={voice!r},改用 TimbreModel 自动选型",
              file=sys.stderr)
    return select_timbre(
        text=text,
        output=output,
        character=character,
        variant=variant,
        project=project,
        instructions=instructions,
        timbre_dir=cfg.get("timbre_dir") or "data/TimbreModel",
        catalog_path=cfg.get("timbre_catalog") or "data/TimbreModel/catalog.json",
    )


def _tts_comfyui(cfg, text, output, voice, speed, instructions,
                 character="", variant="", project=""):
    rh = _comfy_is_rh(cfg)
    base = hdrs = None
    if not rh:
        base, hdrs = _comfy_endpoint(cfg)
        if not (cfg.get("workflow") or "").strip():
            raise RuntimeError("ComfyUI TTS 必须在「🎨 生成模型」页配置工作流 JSON"
                               "(推荐 comfy/tts-indextts2-api.json)")
    selection = _resolve_tts_reference(
        cfg, text, output, voice, character, variant, project, instructions)
    ref = selection["path"]
    print("[genmedia] 自动音色:"
          f"{selection['character']}/{selection['variant']} → {selection['file']}"
          f" (score={selection['score']}, {selection['reason']})", file=sys.stderr)
    if instructions:
        print("[genmedia] ComfyUI TTS 不支持 instructions 参数,已忽略"
              "(情绪请用工作流内 Emotion 节点或改 prompt 文本)", file=sys.stderr)
    ref_audio = _rh_upload(cfg, ref) if rh else _comfy_upload(base, ref, hdrs)
    tokens = {
        "TEXT": text,
        "PROMPT": text,  # 兼容把文本写在 PROMPT 位的工作流
        "REF_AUDIO": ref_audio,
        "VOICE": ref_audio,
        "SEED": random.randint(0, 2**31 - 1),
        "SPEED": float(speed) if speed else 1.0,
    }
    wf = _comfy_workflow(cfg, tokens, "tts")
    if rh:
        # 直绑兜底报错留在 try 外:属提交前配置问题,不得包装成「后端执行失败」
        _apply_rh_tts_bindings(_rh_workflow_text(cfg), wf, text, ref_audio,
                               tokens["SEED"], speed)
    try:
        return (_rh_run(cfg, wf, output, want_video=False) if rh
                else _comfy_run(base, wf, output, want_video=False, headers=hdrs))
    except RuntimeError as exc:
        raise RuntimeError(
            "ComfyUI TTS 后端执行失败"
            f"(自动参考音频已选择并上传:{selection['file']});"
            f"必须按以下原始错误分类,不得改写为缺少参考音频:{exc}"
        ) from exc


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
        data, usage = _image_ark(cfg, prompt, negative, refs, width, height, seed)
        saved = _save(data, output)
        if usage.get("output_tokens") or usage.get("total_tokens"):
            _record_image_usage(output, cfg, usage, width, height)
        return saved
    if cfg["provider"] == "minimax":
        return _save(_image_minimax(cfg, prompt, negative, refs, width, height, seed), output)
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
                   video_refs: list[str] | None = None,
                   ref_image_size: str = "") -> str:
    """生成一段视频,返回保存的绝对路径。渠道/模型按 data/.videoagents/genconfig.json。

    ref_image_size 仅 ComfyUI H3 Ref2VA 工作流支持:空=内置默认 match(参考图压到
    与输出同像素面积);max=短边 ≤2048 不压缩直进模型,人脸/身份保真更好但更慢更贵,
    建议仅对脸部一致性要求高的组按需指定。

    refs/audio_refs/generate_audio/return_last_frame 为多模态参考模式(Seedance 2.x
    多镜头组生成)专用,仅火山引擎/BytePlus/MiniMax(H3)及 ComfyUI(H3 Ref2VA /
    LTX-2.5 / Seedance 云工作流, 只有H3 Ref2VA 支持 audio_refs)渠道支持;refs 与
    first/last_frame 互斥。minimax 渠道:分辨率仅 768P/2K 两档(项目档位自动就近
    映射),时长 [4,15] 整数秒,原生音画同生(generate_audio=off 不生效),
    return_last_frame 从成片本地抽帧;video_refs 经对象存储预签名 URL 传入。
    video_refs 为 V2V 编辑/延长模式(Seedance 2.x):传待修改/待延长的原视频
    (2.0:≤3 个,单个 2-15s 且总时长 ≤15s;2.5:≤10 个,单个 2-30s 且总时长
    ≤30s),prompt 用「视频n/图片n」序号引用素材,典型用法是局部穿帮修复
    (定向修改,其余画面保持不变);与 first/last_frame 互斥。
    Seedance 2.5(doubao-seedance-2-5-* / dreamina-seedance-2-5-*)差异:时长上限
    30s、参考素材 30图+10视频+10音频、分辨率仅 480p/720p(越档自动压 720p)、
    支持纯音频参考;视频编辑/延长与首帧任务 ratio 仅 adaptive(首帧任务自动改写)。
    """
    _forbid_dispatch_layer("视频")
    cfg = get_config("video")
    if ref_image_size and cfg["provider"] != "comfyui":
        raise RuntimeError(f"--ref-image-size 仅 ComfyUI MiniMax-H3 Ref2VA 工作流支持,"
                           f"当前渠道 {cfg['provider']} 请去掉该参数")
    resolution = _resolution_gate(resolution)
    seed = seed if seed is not None else random.randint(1, 2**31)
    if cfg["provider"] in ("volcengine", "byteplus"):
        return _video_ark(cfg, prompt, first_frame, last_frame, duration,
                          resolution, aspect, seed, output,
                          refs, audio_refs, generate_audio, return_last_frame,
                          video_refs)
    if cfg["provider"] == "minimax":
        return _video_minimax(cfg, prompt, first_frame, last_frame, duration,
                              resolution, aspect, seed, output,
                              refs, audio_refs, generate_audio, return_last_frame,
                              video_refs)
    if cfg["provider"] == "comfyui" and (_is_h3_ref2va_workflow(cfg)
                                         or _seedance_cloud_workflow_gen(cfg)
                                         or _is_ltx25_workflow(cfg)):
        return _video_comfyui(cfg, prompt, first_frame, last_frame, duration,
                              resolution, aspect, seed, output, refs, audio_refs,
                              generate_audio, return_last_frame, video_refs,
                              ref_image_size=ref_image_size)
    if refs or audio_refs or video_refs or return_last_frame or generate_audio is not None:
        raise RuntimeError(f"渠道 {cfg['provider']} 不支持多参考图/参考音频/参考视频"
                           "/return_last_frame/generate_audio,"
                           "请在生成模型页切换到火山引擎/BytePlus 或改用首尾帧模式")
    fn = {"openrouter": _video_openrouter, "comfyui": _video_comfyui}[cfg["provider"]]
    if fn is _video_comfyui:
        # 非 H3 的 ComfyUI 基础工作流:透传后由 _video_comfyui 内部拒绝,防静默忽略
        return fn(cfg, prompt, first_frame, last_frame, duration, resolution, aspect,
                  seed, output, ref_image_size=ref_image_size)
    return fn(cfg, prompt, first_frame, last_frame, duration, resolution, aspect, seed, output)


def generate_upscale(input_video: str = "", output: str = "", prompt: str = "",
                     source_task_id: str = "", resolution: str = "",
                     aspect: str = "", seed: int | None = None) -> str:
    """视频超分,返回保存的绝对路径。

    视频配置生效渠道为 comfyui 时使用 SeedVR2 工作流,复用视频 ComfyUI 配置;
    其他渠道使用 MiniMax Regenerate-2K。SeedVR2 的 resolution/aspect/seed 控制
    目标尺寸与随机种子,输出视频的 fps、时长和音轨跟随源视频。MiniMax 的
    base_video 模式传 input_video + prompt,source_task_id 模式传 7 天内 succeeded
    的任务 id 免传源视频。
    """
    _forbid_dispatch_layer("超分")
    try:
        video_cfg = json.loads(CONFIG_PATH.read_text()).get("video") or {}
        provider = str(video_cfg.get("provider") or "")
    except Exception:
        provider = ""
    if provider == "comfyui":
        resolution = resolution or _upscale_default_resolution()
        return _upscale_seedvr2(_seedvr2_config(), input_video, output, resolution,
                                aspect, seed if seed is not None else random.randint(1, 2**31))
    cfg = _minimax_upscale_config()
    return _upscale_minimax(cfg, input_video, prompt, source_task_id, output)


def generate_tts(text: str, output: str, voice: str = "", speed: float | None = None,
                 instructions: str = "", character: str = "", variant: str = "",
                 project: str = "") -> str:
    """TTS 旁白/语音合成,返回保存的绝对路径。渠道/模型按 data/.videoagents/genconfig.json 的 tts 段。

    输出 .mp3 为 mp3,其余扩展名为 pcm(24kHz 裸流,需自行封装)。云渠道 voice 缺省用
    配置页默认音色(openrouter=音色名 / volcengine=speaker 名 /
    minimax=voice_id / elevenlabs=voice_id)。
    ComfyUI 渠道按 character(省略时从 output 的 CHAR-ID 推断)读取项目
    voice/personality/appearance,按 modules/timbre_catalog.json 索引的远端
    ComfyUI-Index-TTS/TimbreModel 音频库自动选择参考音频(首次使用下载缓存到
    data/TimbreModel/);旁白不传 character;voice 仅保留真实本地音频文件的兼容覆盖。
    instructions:openrouter 仅 OpenAI 系模型生效,volcengine 注入 context_texts
    情绪指令,minimax/elevenlabs 不支持(忽略),comfyui 参与音色自动匹配、不注入合成。
    """
    _forbid_dispatch_layer("TTS 语音")
    cfg = get_config("tts")
    fn = {"openrouter": _tts_openrouter, "volcengine": _tts_volcengine,
          "minimax": _tts_minimax, "elevenlabs": _tts_elevenlabs,
          "comfyui": _tts_comfyui}.get(cfg["provider"])
    if not fn:
        raise RuntimeError(f"TTS 不支持渠道 {cfg['provider']}"
                           "(可选 openrouter / volcengine / minimax / elevenlabs / comfyui)")
    if cfg["provider"] == "comfyui":
        return fn(cfg, text, output, voice, speed, instructions,
                  character, variant, project)
    return fn(cfg, text, output, voice, speed, instructions)


def generate_music(prompt: str, output: str, duration_s: float | None = None) -> str:
    """生成一段音乐(BGM),返回保存的绝对路径。渠道/模型按 data/.videoagents/genconfig.json 的 music 段。

    输出格式按 output 扩展名(openrouter:mp3/wav/flac/opus;elevenlabs:mp3/opus;
    minimax:mp3/wav)。
    duration_s:elevenlabs 生效(music_length_ms,3–600s);comfyui 注入 DURATION 占位符
    (默认 30s);openrouter/minimax 省略或忽略=模型按 prompt 自定。
    openrouter 时长由模型决定:Lyria 3 Pro 完整歌曲,Lyria 3 Clip 30s 片段/Loop。
    """
    _forbid_dispatch_layer("音乐")
    cfg = get_config("music")
    if cfg["provider"] == "elevenlabs":
        return _music_elevenlabs(cfg, prompt, output, duration_s)
    if cfg["provider"] == "minimax":
        return _music_minimax(cfg, prompt, output, duration_s)
    if cfg["provider"] == "comfyui":
        return _music_comfyui(cfg, prompt, output, duration_s)
    if cfg["provider"] != "openrouter":
        raise RuntimeError(f"音乐生成不支持渠道 {cfg['provider']}"
                           "(可选 openrouter / elevenlabs / minimax / comfyui)")
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
                else _comfy_desc(cfg)
            print(f"{kind:5s} → {cfg['provider']:10s} {desc}")
        except RuntimeError as e:
            print(f"{kind:5s} → ⚠ {e}")


def _cmd_image(args):
    _check_id_digits(args.output)
    if args.dry_run:
        cfg = get_config("image")
        desc = _comfy_desc(cfg) if cfg["provider"] == "comfyui" \
            else f"model={cfg.get('model') or '-'}"
        print(f"[dry-run] image via {cfg['provider']} {desc} → {args.output}")
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
        desc = _comfy_desc(cfg) if cfg["provider"] == "comfyui" \
            else f"model={cfg.get('model') or '-'}"
        line = f"[dry-run] video via {cfg['provider']} {desc} → {args.output}"
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
                         video_refs=args.ref_video, ref_image_size=args.ref_image_size)
    print(f"已生成: {out}")


def _cmd_upscale(args):
    _check_id_digits(args.output)
    if args.dry_run:
        try:
            video_cfg = json.loads(CONFIG_PATH.read_text()).get("video") or {}
            provider = str(video_cfg.get("provider") or "")
        except Exception:
            provider = ""
        if provider == "comfyui":
            cfg = _seedvr2_config()
            resolution = args.resolution or _upscale_default_resolution()
            width, height = _seedvr2_dimensions(args.aspect, resolution)
            if _comfy_is_rh(cfg):
                # 只读校验云端工作流可直绑(不上传、不建任务)
                wf = json.loads(_rh_workflow_text(cfg))
                summary = _apply_rh_upscale_bindings(wf, "<uploaded>", width, height, args.seed)
                print(f"[dry-run] upscale via runninghub workflow={cfg.get('rh_workflow_id')}"
                      f" site={cfg.get('mode')} target={summary.get('target')}"
                      f" seed={args.seed if args.seed is not None else '(random)'} → {args.output}")
                return
            print(f"[dry-run] upscale via seedvr2 workflow={SEEDVR2_WORKFLOW}"
                  f" size={width}x{height} seed={args.seed if args.seed is not None else '(random)'}"
                  f" mode={cfg.get('mode') or 'local'} → {args.output}")
            return
        cfg = _minimax_upscale_config()
        if args.input and not args.source_task_id:
            _minimax_upscale_precheck(args.input)
            print(f"[dry-run] 源视频 {args.input} 通过 Regenerate-2K 输入规格预检")
        print(f"[dry-run] upscale via minimax model={MINIMAX_UPSCALE_MODEL}"
              f" resolution=2K api_base={_minimax_base(cfg)} → {args.output}")
        return
    out = generate_upscale(args.input, args.output, args.prompt, args.source_task_id,
                            args.resolution, args.aspect, args.seed)
    print(f"已生成: {out}")


def _cmd_music(args):
    if args.dry_run:
        cfg = get_config("music")
        desc = _comfy_desc(cfg) if cfg["provider"] == "comfyui" \
            else f"model={cfg.get('model') or '-'}"
        print(f"[dry-run] music via {cfg['provider']} {desc}"
              f" format={MUSIC_FORMATS.get(Path(args.output).suffix.lower(), 'mp3')} → {args.output}")
        return
    out = generate_music(args.prompt, args.output, args.duration)
    print(f"已生成: {out}")


def _cmd_upload(args):
    """上传本地文件到对象存储,stdout 只打印预签名 URL(供 core/脚本捕获)。"""
    print(_storage_upload_url(args.input))


def _cmd_tts(args):
    if args.dry_run:
        cfg = get_config("tts")
        voice = args.voice or cfg.get("voice") or "eve"
        if cfg["provider"] == "comfyui":
            selected = _resolve_tts_reference(
                cfg, args.text, args.output, args.voice, args.character,
                args.variant, args.project, args.instructions)
            voice = f"auto:{selected['file']} ({selected['reason']})"
        desc = _comfy_desc(cfg) if cfg["provider"] == "comfyui" \
            else f"model={cfg.get('model') or '-'}"
        print(f"[dry-run] tts via {cfg['provider']} {desc}"
              f" voice={voice}"
              f" format={'mp3' if Path(args.output).suffix.lower()=='.mp3' else 'pcm'} → {args.output}")
        return
    out = generate_tts(args.text, args.output, args.voice, args.speed, args.instructions,
                       args.character, args.variant, args.project)
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
                    help="时长(秒);Seedance 2.0 为 [4,15] 整数或 -1(模型自定),"
                         "Seedance 2.5 为 [4,30] 整数或 -1(单段最长 30s 直出)")
    pv.add_argument("--resolution", default="", help="如 720p / 1080p")
    pv.add_argument("--aspect", default="", help="画幅,如 16:9")
    pv.add_argument("--seed", type=int, default=None)
    pv.add_argument("--ref-video", nargs="+", default=None,
                    help="参考视频路径(2.0 ≤3 个/总时长≤15s,2.5 ≤10 个/总时长≤30s;"
                         "V2V 编辑/延长,Seedance 2.x 专用,"
                         "prompt 用「视频n」序号引用;与首尾帧互斥)")
    pv.add_argument("--ref", nargs="+", default=None,
                    help="参考图路径(可多张,2.0 ≤9 / 2.5 ≤30;"
                         "多模态参考/多镜头组模式,与首尾帧互斥)")
    pv.add_argument("--audio-ref", nargs="+", default=None,
                    help="参考音频路径(2.0 ≤3 段/总时长≤15s,2.5 ≤10 段/总时长≤30s;"
                         "如角色 TTS 音色样本)")
    pv.add_argument("--generate-audio", choices=["on", "off", ""], default="",
                    help="原生音频开关(Seedance 2.x;缺省沿用模型默认 on)")
    pv.add_argument("--ref-image-size", choices=["match", "max"], default="",
                    help="参考图尺寸策略(仅 MiniMax-H3 Ref2VA):默认 match 压到与输出"
                         "同像素面积;max 短边 ≤2048 不压缩,身份保真更好但更慢更贵")
    pv.add_argument("--return-last-frame", default="",
                    help="尾帧 PNG 落盘路径(用于组间续接锚)")
    pv.add_argument("--dry-run", action="store_true")

    pu = sub.add_parser("upscale", help="视频超分(ComfyUI SeedVR2 或 MiniMax Regenerate-2K)")
    pu.add_argument("--input", default="",
                    help="源视频路径;MiniMax 须为 H3 768P 直出规格,与 --source-task-id 二选一")
    pu.add_argument("--output", required=True, help="输出 mp4 路径")
    pu.add_argument("--prompt", default="",
                    help="MiniMax base_video 模式所需的原始 video_prompt;SeedVR2 忽略")
    pu.add_argument("--source-task-id", default="",
                    help="MiniMax 7 天内 succeeded 的任务 id(免传源视频,与 --input 互斥;SeedVR2 不使用)")
    pu.add_argument("--resolution", default="", help="SeedVR2 目标分辨率,如 1080p/4k")
    pu.add_argument("--aspect", default="", help="SeedVR2 目标画幅,如 16:9")
    pu.add_argument("--seed", type=int, default=None, help="SeedVR2 随机种子")
    pu.add_argument("--dry-run", action="store_true")

    pt = sub.add_parser("tts", help="TTS 旁白/语音合成")
    pt.add_argument("--text", required=True, help="要合成的文本(旁白/台词)")
    pt.add_argument("--output", required=True, help="输出音频路径(.mp3;其他扩展名为 pcm 裸流)")
    pt.add_argument("--character", default="",
                    help="角色 ID(如 CHAR-0001);省略时从输出文件名推断,旁白留空")
    pt.add_argument("--variant", default="", help="年龄/形态版本(如 child;可选)")
    pt.add_argument("--project", default=(os.environ.get("VIDEOAGENTS_PROJECT")
                                           or os.environ.get("WEBUI_PROJECT", "")),
                    help="项目名或项目目录;Agent 环境通常自动注入")
    pt.add_argument("--voice", default="",
                    help="音色(云渠道:缺省用配置页默认,openrouter=音色名/火山=speaker 名/"
                         "minimax=voice_id/elevenlabs=voice_id,角色配音按 casting 传;"
                         "ComfyUI:仅接受真实本地音频文件的兼容覆盖,通常不要传)")
    pt.add_argument("--speed", type=float, default=None, help="语速倍率(可选)")
    pt.add_argument("--instructions", default="",
                    help="语气/情绪指令(OpenAI 系模型生效,火山注入情绪指令;"
                         "comfyui 参与音色自动匹配、不注入合成)")
    pt.add_argument("--dry-run", action="store_true")

    pup = sub.add_parser("upload", help="上传本地文件到对象存储并打印预签名 URL"
                                        "(渠道按「设置 → 文件托管」;供需要公网 URL 的 API 使用)")
    pup.add_argument("--input", required=True, help="本地文件路径")

    pm = sub.add_parser("music", help="生成音乐(BGM)")
    pm.add_argument("--prompt", required=True, help="英文音乐描述:风格/情绪/乐器/节奏(Lyria Pro 可含歌词)")
    pm.add_argument("--output", required=True, help="输出音频路径(.mp3/.wav/.flac/.opus;elevenlabs 仅 .mp3/.opus;minimax 仅 .mp3/.wav)")
    pm.add_argument("--duration", type=float, default=None,
                    help="目标时长秒(elevenlabs:3–600;comfyui:注入 DURATION,默认 30;"
                         "openrouter/minimax 忽略;省略=模型/工作流默认)")
    pm.add_argument("--dry-run", action="store_true")

    args = ap.parse_args()
    t0 = time.time()
    try:
        {"info": _cmd_info, "image": _cmd_image, "video": _cmd_video,
         "upscale": _cmd_upscale, "music": _cmd_music, "tts": _cmd_tts,
         "upload": _cmd_upload}[args.cmd](args)
    except RuntimeError as e:
        _diag_report(args, t0, error=str(e))
        print(f"生成失败: {e}", file=sys.stderr)
        sys.exit(1)
    else:
        _diag_report(args, t0)


def _diag_report(args, t0: float, error: str = "") -> None:
    """诊断事件旁路(modules/diagnostics.py):白名单字段本地落盘,错误消息
    模板化后只存模板与签名,不出网。info/dry-run 不记;渠道/模型 best-effort,
    读不到(如配置缺失本身就是报错原因)不影响记录。"""
    if _diagnostics is None or args.cmd in ("info", "upload") \
            or getattr(args, "dry_run", False):
        return
    provider = model = ""
    try:
        if args.cmd == "upscale":
            try:
                video_cfg = json.loads(CONFIG_PATH.read_text()).get("video") or {}
                provider = "seedvr2" if video_cfg.get("provider") == "comfyui" else "minimax"
            except Exception:
                provider = "minimax"
            model = SEEDVR2_WORKFLOW if provider == "seedvr2" else MINIMAX_UPSCALE_MODEL
        else:
            cfg = get_config(args.cmd)
            provider = cfg.get("provider", "")
            model = str(cfg.get("model") or "")
            if not model and cfg.get("workflow"):   # comfyui:只取工作流文件名,不落路径
                model = Path(str(cfg["workflow"])).name
    except Exception:
        pass
    _diagnostics.record_gen_event(args.cmd, not error, error, provider, model,
                                  duration_s=time.time() - t0)


if __name__ == "__main__":
    main()
