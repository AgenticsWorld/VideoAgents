#!/usr/bin/env python3
"""数字人单人片段生成器。

输入一张人物图和一段该人物的对白音频，按「生成模型 → 数字人」配置自动路由到
HeyGen、Kling AI（北京）、RunningHub 云端工作流或本地 ComfyUI/InfiniteTalk。最终母带封装由
``modules/dialogue_video.py`` 完成；渠道返回的音轨不会进入成片。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from modules.avsync import probe_duration
    from modules.genmedia import (
        CONFIG_PATH, _comfy_endpoint, _comfy_fill_workflow,
        _comfy_run, _comfy_upload, _get_json, _node_link, _post_json, _request,
        _resolve_comfy_workflow_path, _rh_run, _rh_upload, _rh_workflow_text,
    )
except ModuleNotFoundError:  # python modules/digitalhuman.py ...
    from avsync import probe_duration
    from genmedia import (
        CONFIG_PATH, _comfy_endpoint, _comfy_fill_workflow,
        _comfy_run, _comfy_upload, _get_json, _node_link, _post_json, _request,
        _resolve_comfy_workflow_path, _rh_run, _rh_upload, _rh_workflow_text,
    )

HEYGEN_BASE = "https://api.heygen.com"
KLING_BASE = "https://api-beijing.klingai.com"
POLL_INTERVAL = 5
TASK_TIMEOUT = 1800


def _forbid_dispatch_layer() -> None:
    agent = os.environ.get("VIDEOAGENTS_AGENT", "")
    if agent.startswith("00-orchestration/"):
        raise RuntimeError(f"{agent} 属调度层，禁止直接生成数字人视频；请派发给插件执行 Agent")


def get_config() -> dict:
    if not CONFIG_PATH.is_file():
        raise RuntimeError(f"未找到生成模型配置 {CONFIG_PATH}；请先在 Web 控制台保存数字人配置")
    root = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    section = root.get("digital_human") or {}
    provider = section.get("provider")
    if provider not in {"heygen", "klingai", "runninghub", "comfyui"}:
        raise RuntimeError("数字人渠道未配置；请选择 HeyGen、Kling AI、RunningHub 或 ComfyUI")
    cfg = dict(section.get(provider) or {})
    if provider == "heygen":
        cfg["api_key"] = cfg.get("api_key") or os.environ.get("HEYGEN_API_KEY", "")
        cfg["api_base"] = HEYGEN_BASE
    elif provider == "klingai":
        cfg["api_key"] = cfg.get("api_key") or os.environ.get("KLINGAI_API_KEY", "")
        cfg["api_base"] = KLING_BASE
    elif provider == "runninghub":
        # 数字人的 RunningHub 是独立渠道；这里只在调用 genmedia 的 RH 传输层时
        # 转成其通用字段，不读取或覆盖 digital_human.comfyui。
        site = str(cfg.get("site") or "rh_cn")
        if site not in {"rh_cn", "rh_ai"}:
            raise RuntimeError("RunningHub 站点必须是 rh_cn 或 rh_ai")
        cfg = {
            **cfg,
            "mode": site,
            "rh_api_key_cn": cfg.get("api_key_cn") or "",
            "rh_api_key_ai": cfg.get("api_key_ai") or "",
            "rh_workflow_id": cfg.get("workflow_id") or "",
            "rh_instance_type": cfg.get("instance_type") or "standard",
        }
        active_key = (cfg[f"rh_api_key_{site[3:]}"] or
                      os.environ.get("RUNNINGHUB_API_KEY", ""))
        if not str(active_key).strip():
            raise RuntimeError(
                "RunningHub 未配置当前站点 API Key（数字人设置或环境变量 RUNNINGHUB_API_KEY）")
        if not str(cfg["rh_workflow_id"]).strip():
            raise RuntimeError("RunningHub 未选择数字人云端工作流")
    if provider in {"heygen", "klingai"} and not cfg.get("api_key"):
        env = "HEYGEN_API_KEY" if provider == "heygen" else "KLINGAI_API_KEY"
        raise RuntimeError(f"{provider} 未配置 API Key（设置页或环境变量 {env}）")
    return {"provider": provider, **cfg}


def _media_b64(path: str) -> tuple[str, str]:
    p = Path(path)
    if not p.is_file():
        raise RuntimeError(f"输入文件不存在：{p}")
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    return mime, base64.b64encode(p.read_bytes()).decode("ascii")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_job(job_path: str | None) -> dict:
    path = Path(job_path) if job_path else None
    if not path or not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"数字人任务台账损坏：{path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"数字人任务台账格式错误：{path}")
    return value


def _update_job(job_path: str | None, **changes) -> dict:
    """原子更新单个片段的公开任务台账；永远不写入渠道凭证。"""
    if not job_path:
        return {}
    path = Path(job_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = _load_job(job_path)
    value.update(changes)
    value["updated_at"] = _now()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return value


def _task_id(job_path: str | None, provider: str) -> str:
    job = _load_job(job_path)
    if job and job.get("provider") not in (None, provider):
        raise RuntimeError(
            f"已有未完成任务属于 {job.get('provider')}，不能切换到 {provider}；"
            "请恢复原渠道或显式重置该片段任务")
    return str(job.get("task_id") or "")


def _submitted(job_path: str | None, provider: str, task_id: str) -> None:
    job = _load_job(job_path)
    _update_job(job_path, provider=provider, task_id=task_id, status="submitted",
                provider_status="submitted", attempts=int(job.get("attempts") or 0) + 1,
                submitted_at=_now(), error=None)


def _save_url(url: str, output: str, headers: dict | None = None) -> str:
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    partial.write_bytes(_request(url, headers=headers, timeout=600))
    partial.replace(target)
    return output


def _upload_asset(path: str, headers: dict) -> dict:
    """HeyGen Upload Asset 接口接收文件原始字节，不是 multipart。"""
    p = Path(path)
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    raw = _request("https://upload.heygen.com/v1/asset", p.read_bytes(),
                   {**headers, "Content-Type": mime, "Accept": "application/json"},
                   method="POST", timeout=300)
    return json.loads(raw)


def _poll(deadline_s: int, fetch, status_of, result_of, label: str,
          on_status=None) -> str:
    deadline = time.time() + deadline_s
    while time.time() < deadline:
        data = fetch()
        status = str(status_of(data) or "").lower()
        if on_status:
            on_status(status or "unknown")
        if status in {"completed", "succeed", "succeeded", "success"}:
            url = result_of(data)
            if not url:
                raise RuntimeError(f"{label} 任务成功但没有视频 URL：{json.dumps(data)[:800]}")
            return url
        if status in {"failed", "error", "canceled", "cancelled"}:
            raise RuntimeError(f"{label} 任务失败：{json.dumps(data, ensure_ascii=False)[:1000]}")
        time.sleep(POLL_INTERVAL)
    raise RuntimeError(f"{label} 任务超时（{deadline_s}s）")


def _heygen(image: str, audio: str, output: str, prompt: str, cfg: dict,
            job_path: str | None = None) -> str:
    headers = {"x-api-key": cfg["api_key"]}
    video_id = _task_id(job_path, "heygen")
    if not video_id:
        uploaded = _upload_asset(audio, headers)
        ud = uploaded.get("data") or uploaded
        audio_asset_id = ud.get("id") or ud.get("asset_id")
        if not audio_asset_id:
            raise RuntimeError(f"HeyGen 音频上传未返回 asset id：{json.dumps(uploaded)[:600]}")
        mime, image_b64 = _media_b64(image)
        payload = {
            "type": "image",
            "image": {"type": "base64", "media_type": mime, "data": image_b64},
            "audio_asset_id": audio_asset_id,
            "resolution": cfg.get("resolution") or "720p",
            "aspect_ratio": cfg.get("aspect_ratio") or "16:9",
            "output_format": "mp4",
        }
        if prompt:
            payload["motion_prompt"] = prompt
        created = _post_json(HEYGEN_BASE + "/v3/videos", payload, headers)
        cd = created.get("data") or created
        video_id = cd.get("video_id") or cd.get("id")
        if not video_id:
            raise RuntimeError(f"HeyGen 未返回 video_id：{json.dumps(created)[:800]}")
        _submitted(job_path, "heygen", str(video_id))

    def fetch():
        return _get_json(f"{HEYGEN_BASE}/v3/videos/{video_id}", headers)

    def status_of(r):
        return (r.get("data") or r).get("status")

    def result_of(r):
        d = r.get("data") or r
        return d.get("video_url") or d.get("url") or (d.get("video") or {}).get("url")

    url = _poll(TASK_TIMEOUT, fetch, status_of, result_of, "HeyGen",
                lambda status: _update_job(job_path, status="running",
                                           provider_status=status))
    _update_job(job_path, status="downloading", remote_url=url)
    return _save_url(url, output)


def _kling(image: str, audio: str, output: str, prompt: str, cfg: dict,
           job_path: str | None = None) -> str:
    image_size, audio_size = Path(image).stat().st_size, Path(audio).stat().st_size
    if image_size > 10 * 1024 * 1024:
        raise RuntimeError("Kling AI 人物图片不能超过 10MB")
    if audio_size > 5 * 1024 * 1024:
        raise RuntimeError("Kling AI 对白音频不能超过 5MB；请缩短当前说话轮次")
    duration = probe_duration(audio)
    if not 2 <= duration <= 300:
        raise RuntimeError(f"Kling AI 对白音频时长须为 2–300 秒，当前 {duration:.3f}s")
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}
    endpoint = KLING_BASE + "/v1/videos/avatar/image2video"
    task_id = _task_id(job_path, "klingai")
    if not task_id:
        _, image_b64 = _media_b64(image)
        _, audio_b64 = _media_b64(audio)
        payload = {"image": image_b64, "sound_file": audio_b64,
                   "mode": cfg.get("mode") or "std"}
        if prompt:
            payload["prompt"] = prompt
        created = _post_json(endpoint, payload, headers)
        if created.get("code") not in (None, 0):
            raise RuntimeError(f"Kling AI 提交失败：{json.dumps(created, ensure_ascii=False)[:800]}")
        cd = created.get("data") or created
        task_id = cd.get("task_id") or cd.get("id")
        if not task_id:
            raise RuntimeError(f"Kling AI 未返回 task_id：{json.dumps(created)[:800]}")
        _submitted(job_path, "klingai", str(task_id))

    def fetch():
        return _get_json(f"{endpoint}/{task_id}", headers)

    def status_of(r):
        return (r.get("data") or r).get("task_status") or (r.get("data") or r).get("status")

    def result_of(r):
        d = r.get("data") or r
        videos = (d.get("task_result") or {}).get("videos") or d.get("videos") or []
        return (videos[0].get("url") if videos else None) or d.get("video_url")

    url = _poll(TASK_TIMEOUT, fetch, status_of, result_of, "Kling AI",
                lambda status: _update_job(job_path, status="running",
                                           provider_status=status))
    _update_job(job_path, status="downloading", remote_url=url)
    return _save_url(url, output)


def _comfy(image: str, audio: str, output: str, prompt: str,
           seed: int | None, cfg: dict, job_path: str | None = None) -> str:
    base, headers = _comfy_endpoint(cfg)
    workflow_path = _resolve_comfy_workflow_path(cfg.get("workflow") or
                                                 "comfy/digitalhuman-infinitetalk-api.json")
    if not workflow_path.is_file():
        raise RuntimeError(f"InfiniteTalk API 工作流不存在：{workflow_path}")
    image_name = _comfy_upload(base, image, headers)
    audio_name = _comfy_upload(base, audio, headers)
    duration = probe_duration(audio)
    frames = max(1, round(duration * 25 / 4) * 4 + 1)
    tokens = {
        "IMAGE": image_name, "FIRST_FRAME": image_name,
        "AUDIO": audio_name, "REF_AUDIO": audio_name,
        "PROMPT": prompt or "natural speaking, subtle head movement, steady camera",
        "SEED": seed if seed is not None else random.randrange(1, 2**31),
        "DURATION": duration, "FRAMES": frames,
    }
    workflow = _comfy_fill_workflow(workflow_path.read_text(encoding="utf-8"), tokens)
    prompt_id = _task_id(job_path, "comfyui")
    return _comfy_run(
        base, workflow, output, want_video=True, headers=headers,
        prompt_id=prompt_id or None,
        on_submit=lambda pid: _submitted(job_path, "comfyui", pid),
        on_status=lambda status: _update_job(job_path, status=status,
                                             provider_status=status),
    )


def _rh_active_loads(workflow: dict, class_type: str) -> list[tuple[str, dict]]:
    """返回真正连入执行图的加载节点；未被消费的作者孤岛素材不参与歧义计数。"""
    referenced = {str(value[0]) for node in workflow.values() if isinstance(node, dict)
                  for value in (node.get("inputs") or {}).values() if _node_link(value)}
    return [(str(nid), node.setdefault("inputs", {}))
            for nid, node in workflow.items() if isinstance(node, dict)
            and node.get("class_type") == class_type and str(nid) in referenced]


def _rh_literal_slots(workflow: dict, keys: tuple[str, ...],
                      class_contains: str = "") -> list[tuple[str, dict, str]]:
    out = []
    for nid, node in workflow.items():
        if not isinstance(node, dict):
            continue
        cls = str(node.get("class_type") or "").lower()
        if class_contains and class_contains not in cls:
            continue
        inputs = node.setdefault("inputs", {})
        for key in keys:
            if key in inputs and not _node_link(inputs[key]):
                out.append((str(nid), inputs, key))
    return out


def _apply_rh_digitalhuman_bindings(raw: str, workflow: dict, image_name: str,
                                     audio_name: str, prompt: str,
                                     seed: int, frames: int) -> dict:
    """为无占位符的 RunningHub 数字人导出件直绑本次输入。

    RunningHub 工作区导出的 API JSON 通常保留作者演示文件名。图片/音频是身份与
    口型的硬输入，缺占位符时必须能定位唯一在用节点，否则在创建付费任务前停止。
    prompt/seed/frames 在结构清晰时同步覆盖；无法唯一识别的可选数值沿用模板并回显。
    """
    summary = {"mode": "placeholders"}
    if not any(token in raw for token in ("{{IMAGE}}", "{{FIRST_FRAME}}")):
        loads = _rh_active_loads(workflow, "LoadImage")
        if len(loads) != 1:
            raise RuntimeError(
                f"RunningHub 数字人工作流须恰好 1 个在用的 LoadImage 节点(找到 {len(loads)} 个),"
                "无法自动绑定人物图片；请精简工作流或添加 {{IMAGE}}/{{FIRST_FRAME}} 占位符")
        loads[0][1]["image"] = image_name
        summary["image_node"] = loads[0][0]
        summary["mode"] = "auto_nodes"
    if not any(token in raw for token in ("{{AUDIO}}", "{{REF_AUDIO}}")):
        loads = _rh_active_loads(workflow, "LoadAudio")
        if len(loads) != 1:
            raise RuntimeError(
                f"RunningHub 数字人工作流须恰好 1 个在用的 LoadAudio 节点(找到 {len(loads)} 个),"
                "无法自动绑定对白音频；请精简工作流或添加 {{AUDIO}}/{{REF_AUDIO}} 占位符")
        loads[0][1]["audio"] = audio_name
        summary["audio_node"] = loads[0][0]
        summary["mode"] = "auto_nodes"
    if "{{PROMPT}}" not in raw:
        slots = _rh_literal_slots(workflow, ("positive_prompt",))
        if not slots:
            slots = _rh_literal_slots(workflow, ("prompt", "text"), "textencode")
        if len(slots) == 1:
            slots[0][1][slots[0][2]] = prompt
            summary["prompt_node"] = slots[0][0]
        else:
            print(f"[digitalhuman] RunningHub 未定位到唯一正向提示词位(找到 {len(slots)} 个),"
                  "沿用云端模板提示词", file=sys.stderr)
    if "{{SEED}}" not in raw:
        slots = _rh_literal_slots(workflow, ("seed", "noise_seed"), "sampler")
        numeric = [slot for slot in slots
                   if isinstance(slot[1].get(slot[2]), (int, float))
                   and not isinstance(slot[1].get(slot[2]), bool)]
        if len(numeric) == 1:
            numeric[0][1][numeric[0][2]] = seed
            summary["seed_node"] = numeric[0][0]
    if "{{FRAMES}}" not in raw:
        slots = _rh_literal_slots(workflow, ("num_frames", "frames"))
        numeric = [slot for slot in slots
                   if isinstance(slot[1].get(slot[2]), (int, float))
                   and not isinstance(slot[1].get(slot[2]), bool)]
        if len(numeric) == 1:
            numeric[0][1][numeric[0][2]] = frames
            summary["frames_node"] = numeric[0][0]
    return summary


def _runninghub(image: str, audio: str, output: str, prompt: str,
                seed: int | None, cfg: dict, job_path: str | None = None) -> str:
    """RunningHub 独立数字人渠道：上传图片/音频，填充云端工作流并持久化 task_id。"""
    task_id = _task_id(job_path, "runninghub")
    if task_id:
        # 恢复只轮询既有远端任务；不重新上传输入，更不会再次 create 计费任务。
        workflow = {}
    else:
        template = _rh_workflow_text(cfg)
        image_name = _rh_upload(cfg, image)
        audio_name = _rh_upload(cfg, audio)
        duration = probe_duration(audio)
        frames = max(1, round(duration * 25 / 4) * 4 + 1)
        tokens = {
            "IMAGE": image_name, "FIRST_FRAME": image_name,
            "AUDIO": audio_name, "REF_AUDIO": audio_name,
            "PROMPT": prompt or "natural speaking, subtle head movement, steady camera",
            "SEED": seed if seed is not None else random.randrange(1, 2**31),
            "DURATION": duration, "FRAMES": frames,
        }
        workflow = _comfy_fill_workflow(template, tokens)
        binding = _apply_rh_digitalhuman_bindings(
            template, workflow, image_name, audio_name, str(tokens["PROMPT"]),
            int(tokens["SEED"]), frames)
        print("[digitalhuman] RunningHub 输入绑定 "
              + json.dumps(binding, ensure_ascii=False, separators=(",", ":")),
              file=sys.stderr, flush=True)
    def update_status(status: str) -> None:
        waiting = status == "waiting_capacity"
        changes = {"status": "waiting_capacity" if waiting else "running",
                   "provider_status": status, "error": None}
        if waiting:
            job = _load_job(job_path)
            changes["capacity_waits"] = int(job.get("capacity_waits") or 0) + 1
        _update_job(job_path, **changes)

    return _rh_run(
        cfg, workflow, output, want_video=True, task_id=task_id or None,
        on_submit=lambda tid: _submitted(job_path, "runninghub", tid),
        on_status=update_status,
    )


def generate_avatar(image: str, audio: str, output: str, prompt: str = "",
                    seed: int | None = None, dry_run: bool = False,
                    job_path: str | None = None, retry_failed: bool = False) -> str:
    _forbid_dispatch_layer()
    for p in (image, audio):
        if not Path(p).is_file():
            raise RuntimeError(f"输入文件不存在：{p}")
    cfg = get_config()
    if dry_run:
        print(json.dumps({"provider": cfg["provider"], "image": image, "audio": audio,
                          "output": output, "duration_s": probe_duration(audio),
                          "prompt": prompt}, ensure_ascii=False, indent=2))
        return output
    fingerprint = {
        "image": str(Path(image)), "audio": str(Path(audio)), "output": str(Path(output)),
        "image_sha256": _sha256(image), "audio_sha256": _sha256(audio),
    }
    job = _load_job(job_path)
    if job.get("inputs") and job["inputs"] != fingerprint:
        raise RuntimeError("数字人任务输入已变化，拒绝复用旧渠道任务；请显式重置该片段任务")
    if job.get("status") == "completed" and Path(output).is_file():
        return output
    if job.get("status") in {"failed", "canceled", "cancelled"}:
        if not retry_failed:
            raise RuntimeError("渠道任务已失败；确认错误后使用 --retry-failed 显式重新提交")
        history = list(job.get("history") or [])
        history.append({k: job.get(k) for k in
                        ("provider", "task_id", "status", "provider_status", "error",
                         "submitted_at", "updated_at")})
        _update_job(job_path, history=history, task_id=None, status="retrying",
                    provider_status=None, remote_url=None, error=None)
    _update_job(job_path, schema_version=1, provider=cfg["provider"], inputs=fingerprint,
                status=_load_job(job_path).get("status") or "preparing",
                created_at=job.get("created_at") or _now())
    try:
        if cfg["provider"] == "comfyui":
            result = _comfy(image, audio, output, prompt, seed, cfg, job_path)
        elif cfg["provider"] == "runninghub":
            result = _runninghub(image, audio, output, prompt, seed, cfg, job_path)
        else:
            result = {"heygen": _heygen, "klingai": _kling}[cfg["provider"]](
                image, audio, output, prompt, cfg, job_path)
    except Exception as exc:
        current = _load_job(job_path)
        terminal = current.get("provider_status") in {
            "failed", "error", "canceled", "cancelled"
        }
        # 没有 task_id 时错误发生在上传/创建之前，不能误报为轮询失败。
        status = ("failed" if terminal else
                  "poll_error" if current.get("task_id") else "preflight_error")
        _update_job(job_path, status=status,
                    error=str(exc)[:2000])
        raise
    _update_job(job_path, status="completed", provider_status="completed",
                completed_at=_now(), output=str(Path(output)), error=None)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="按全局配置生成单人数字人说话片段")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    gen = sub.add_parser("generate")
    gen.add_argument("--image", required=True)
    gen.add_argument("--audio", required=True)
    gen.add_argument("--output", required=True)
    gen.add_argument("--prompt", default="")
    gen.add_argument("--seed", type=int)
    gen.add_argument("--dry-run", action="store_true")
    gen.add_argument("--job", help="渠道任务台账 JSON；中断后用同一路径恢复轮询")
    gen.add_argument("--retry-failed", action="store_true",
                     help="仅对已确认终止的失败任务重新提交")
    args = ap.parse_args(argv)
    if args.cmd == "info":
        cfg = get_config()
        safe = {k: v for k, v in cfg.items() if "key" not in k.lower()}
        print(json.dumps(safe, ensure_ascii=False, indent=2))
    else:
        print(generate_avatar(args.image, args.audio, args.output,
                              args.prompt, args.seed, args.dry_run,
                              args.job, args.retry_failed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
