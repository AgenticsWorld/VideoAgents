"""素材完成推送:概念图主图与分镜组视频落盘后自动推到手机消息通道(现仅飞书)。

- 监视范围:data/projects/*/assets/concepts/{characters,scenes,props}/ 两级内
  的图片(candidates/ 等更深子目录是候选/过程稿,不算主图不推),以及
  assets/clips/ 两级内的 mp4(archive_* 等三级目录天然排除);
- 完成判定:文件新出现或 mtime/size 变化,且最近 STABLE_S 秒无改动
  (genmedia 一次性 write_bytes 落盘,近似原子);重生成/超分覆盖同名会再推;
- 防洪:首次运行与新见项目(项目复制/导入)只记基线不推,仅推此后新增;
- 已推清单持久化 RUNTIME_DIR/media_push.json,重启不重推;单文件推送失败
  重试 MAX_PUSH_FAILS 轮后放弃(错误落 feishu.RELAY,显示在设置页);
- 未绑定飞书或联系人未就绪时静默跳过并记为已推(与确认卡片同口径),
  绑定后只推新完成的素材。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from . import core, feishu

STATE_PATH = core.RUNTIME_DIR / "media_push.json"
SCAN_INTERVAL_S = 20
STABLE_S = 10                 # 最近无改动秒数,视为写完
MAX_PUSH_FAILS = 3
PUSH_GAP_S = 1.0              # 批量完成时的发送间隔,避免触发频控

IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
VID_EXTS = {".mp4"}

_KIND_LABELS = {"characters": "🧑 人物图", "scenes": "🏞 场景图", "props": "🎁 道具图"}


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _collect(base: Path, exts: set[str], out: dict, root: Path):
    """收 base 两级内的目标文件;更深层(candidates/ archive_* 等)不收。"""
    try:
        entries = list(base.iterdir()) if base.is_dir() else []
    except OSError:
        return
    for e1 in entries:
        try:
            if e1.is_file():
                files = [e1]
            elif e1.is_dir() and not e1.name.startswith("."):
                files = [e2 for e2 in e1.iterdir() if e2.is_file()]
            else:
                continue
        except OSError:
            continue
        for f in files:
            if f.name.startswith(".") or f.suffix.lower() not in exts:
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            out[str(f.relative_to(root))] = [st.st_mtime, st.st_size]


def _scan() -> dict[str, list]:
    """全项目扫描 → {相对 PROJECTS_DIR 的路径: [mtime, size]}。"""
    out: dict[str, list] = {}
    try:
        projects = [p for p in core.PROJECTS_DIR.iterdir()
                    if p.is_dir() and not p.name.startswith(".")]
    except OSError:
        return out
    for proj in projects:
        assets = proj / "assets"
        for cat in _KIND_LABELS:
            _collect(assets / "concepts" / cat, IMG_EXTS, out, core.PROJECTS_DIR)
        _collect(assets / "clips", VID_EXTS, out, core.PROJECTS_DIR)
    return out


def _caption(key: str) -> tuple[str, str]:
    """→ (kind, 消息说明)。kind: image|video。"""
    parts = Path(key).parts               # (项目, assets, concepts|clips, …)
    proj = parts[0]
    if parts[2] == "concepts":
        label = _KIND_LABELS.get(parts[3], "🖼 概念图")
        return "image", f"{label}完成 · {proj} · {'/'.join(parts[4:])}"
    return "video", f"🎬 分镜组视频完成 · {proj} · {'/'.join(parts[3:])}"


async def relay_loop():
    """后台常驻(services.api lifespan 与各通道 relay 一起启动)。"""
    state = _load_state()
    baseline = not state                  # 首次运行:只记基线不推,防存量刷屏
    seen: dict = dict(state.get("seen") or {})
    known_projects = set(state.get("projects") or [])
    fails: dict[str, int] = {}
    while True:
        try:
            cur = await asyncio.to_thread(_scan)
            now = time.time()
            cur_projects = {Path(k).parts[0] for k in cur}
            new_projects = cur_projects - known_projects
            dirty = baseline or bool(new_projects)
            for k in [k for k in seen if k not in cur]:
                seen.pop(k)               # 文件已删:清条目,重生成后会再推
                dirty = True
            pending = []
            for k, meta in cur.items():
                if seen.get(k) == meta:
                    continue
                if baseline or Path(k).parts[0] in new_projects:
                    seen[k] = meta        # 存量/整项目复制导入:静默记基线
                    dirty = True
                elif now - meta[0] >= STABLE_S:
                    pending.append((k, meta))
            for k, meta in sorted(pending, key=lambda kv: kv[1][0]):
                kind, caption = _caption(k)
                push = feishu.push_image if kind == "image" else feishu.push_video
                try:
                    sent = await push(str(core.PROJECTS_DIR / k), caption)
                except Exception as e:  # noqa: BLE001
                    fails[k] = fails.get(k, 0) + 1
                    feishu.RELAY["err_out"] = f"推送素材失败({Path(k).name}):{e}"
                    if fails[k] < MAX_PUSH_FAILS:
                        continue          # 留待下轮重试
                    sent = False
                seen[k] = meta            # 成功/未绑定跳过/重试超限:不再推
                fails.pop(k, None)
                dirty = True
                if sent:
                    await asyncio.sleep(PUSH_GAP_S)
            known_projects |= cur_projects
            baseline = False
            if dirty:
                core.atomic_write_json(
                    STATE_PATH, {"seen": seen, "projects": sorted(known_projects)})
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            feishu.RELAY["err_out"] = f"素材监视循环异常:{e}"
        await asyncio.sleep(SCAN_INTERVAL_S)
