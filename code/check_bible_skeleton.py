#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bible 骨架机检 bible_skeleton_ok + ascii_filename(WORKFLOW.md Phase 0 p0-bible-init,2026-10-22,#153)。

memory-bible 初始化 `bible/` 骨架后跑本脚本复核(调度层可随时复跑),逐项 PASS / FAIL / WARN:
  1. bible_files_parse   WORKFLOW.md §2 列出的 Bible 文件与目录都在且能解析:根下 world / timeline / geography / religion /
                         culture / politics / economy / cultivation / dictionary / style / props / costumes / color_script
                         共 13 份 .json 存在、是合法 JSON、顶层为对象;characters/、creatures/、scenes/ 三个目录存在,
                         其下(递归)每个 .json 都能解析。
  2. bible_host_keys     宿主固定读取的路径与顶层键在(空容器 / 空串即算通过),口径与宿主读取函数一致:
                           characters/index.json#characters   (services/runtime/core.py 人物预览 / 组卡)
                           creatures/index.json#creatures     (core.py 生物预览 / 组卡)
                           scenes/index.json#scenes           (core.py 场景预览 / 场次表)
                           props.json#props 或 #entries        (core.py _preview_props 两种都认)
                           costumes.json#costumes / #entries / #characters[](core.py _costume_entries 三种都认)
                           style.json 顶层风格串 style_fragment_en / style_fragment_ui(背景图 / 全景链路读 _en,
                                                              提示词规约取 _ui)
                           color_script.json#episodes(数组或 {epNN: …} 对象,modules/color_script.py 两种都认)
                         值为 null:宿主按空处理的(props / costumes / style 风格串 / episodes)算通过,三份 index.json 的数组键
                         core.py 直接迭代、null 会抛错,须是数组。
                         只有部分宿主模块兼容的写法记 WARN 不判失败:三份 index.json 写成 entries(script_breakdown /
                         storyboard_board 认、core.py 预览页读不到)、style.json 只有 style_anchor_string_en(手绘分镜成图读)
                         或 style_prompt_en.base(场景图兜底读)。键不在、值类型不对(如 index.json 数组键为 null、文件顶层是数组)= FAIL。
  3. ascii_filename      bible/ 下(递归)全部文件名与目录名仅 ASCII(WORKFLOW.md §1 原则 9)。
  4. bible_changelog     变更日志 bible/changelog.md 存在(存量项目写成 changelog.json 同效,须能解析)。
写入规则文件没有固定文件名,不查。所有读取的路径 resolve 后须仍在项目目录内(符号链接指出去 = FAIL)。

用法:python3 code/check_bible_skeleton.py --project <slug> [--json]
  --project 只收项目名([字母数字_-],≤80 字符),含 / 或 .. 的一律拒绝(退出码 2);项目根 = $VIDEOAGENTS_DATA_DIR/projects/<slug>
  (缺省仓库 data/projects/<slug>),--out-root <目录> 可直接指定项目根(验证对拍用,同 code/_common.py 口径)。
退出码:0 PASS(可含 WARN)、1 有 FAIL、2 项目名不合法或 bible/ 不存在。只读,不改任何文件。
宿主 CLI,Agent 只准调用,禁止复制/改写到项目 code/。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402

CHECK = "bible_skeleton_ok"
WORLD_FILES = ("world", "timeline", "geography", "religion", "culture", "politics",
               "economy", "cultivation", "dictionary")
ROOT_FILES = WORLD_FILES + ("style", "props", "costumes", "color_script")
ENTITY_DIRS = ("characters", "creatures", "scenes")
# (相对 bible/ 的路径, 宿主认的键, 只有部分模块兼容的别名键, 值为 null 宿主是否按空处理)
# 三份 index.json:core.py 写的是 idx.get(键, []) 后直接迭代——值为 null 会抛错,故须是数组;
# props / costumes:core.py 写的是 doc.get(键) or …or [],null 按空处理
LIST_KEYS = (
    ("characters/index.json", ("characters",), ("entries",), False),
    ("creatures/index.json", ("creatures",), ("entries",), False),
    ("scenes/index.json", ("scenes",), ("entries",), False),
    ("props.json", ("props", "entries"), (), True),
    ("costumes.json", ("costumes", "entries", "characters"), (), True),
)
STYLE_KEYS = ("style_fragment_en", "style_fragment_ui")
STYLE_PARTIAL = ("style_anchor_string_en", "style_prompt_en")
CHANGELOGS = ("changelog.md", "changelog.json")
_SLUG = re.compile(r"[\w-]+")


def valid_slug(name) -> bool:
    """与 services/runtime/core.py require_project_slug 同口径:只收名字,不收路径。"""
    value = str(name or "")
    return bool(value) and len(value) <= 80 and bool(_SLUG.fullmatch(value))


class Report:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, check: str, path: str, status: str, detail: str = ""):
        self.items.append({"check": check, "path": path, "status": status, "detail": detail})

    @property
    def failed(self) -> list[dict]:
        return [i for i in self.items if i["status"] == "FAIL"]

    @property
    def warned(self) -> list[dict]:
        return [i for i in self.items if i["status"] == "WARN"]


def run(root: Path) -> Report:
    rep = Report()
    root = root.resolve()
    bible = root / "bible"

    def inside(p: Path) -> bool:
        try:
            p.resolve().relative_to(root)
            return True
        except ValueError:
            return False

    def load(rel: str):
        """(数据, 错误串);错误串非空 = 不可用。"""
        p = bible / rel
        if not inside(p):
            return None, "路径 resolve 后不在项目目录内(符号链接指出去了?)"
        if not p.is_file():
            return None, "文件不存在"
        try:
            return json.loads(p.read_text(encoding="utf-8")), ""
        except (OSError, UnicodeDecodeError, ValueError) as e:
            return None, f"解析失败:{type(e).__name__}: {e}"

    # ---- 1. §2 文件与目录在且能解析
    docs: dict[str, object] = {}
    for stem in ROOT_FILES:
        rel = f"{stem}.json"
        data, err = load(rel)
        if err:
            rep.add("bible_files_parse", f"bible/{rel}", "FAIL", err)
        elif not isinstance(data, dict):
            rep.add("bible_files_parse", f"bible/{rel}", "FAIL", f"顶层须是 JSON 对象,实际是 {type(data).__name__}")
        else:
            docs[rel] = data
            rep.add("bible_files_parse", f"bible/{rel}", "PASS")
    for d in ENTITY_DIRS:
        p = bible / d
        if not inside(p):
            rep.add("bible_files_parse", f"bible/{d}/", "FAIL", "路径 resolve 后不在项目目录内(符号链接指出去了?)")
            continue
        if not p.is_dir():
            rep.add("bible_files_parse", f"bible/{d}/", "FAIL", "目录不存在")
            continue
        bad, n = [], 0
        for f in sorted(p.rglob("*.json")):
            if not f.is_file():
                continue
            n += 1
            rel = f.relative_to(bible).as_posix()
            if not inside(f):
                bad.append(f"{rel}(不在项目目录内)")
                continue
            try:
                json.loads(f.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError) as e:
                bad.append(f"{rel}({type(e).__name__})")
        if bad:
            rep.add("bible_files_parse", f"bible/{d}/", "FAIL",
                    f"{len(bad)}/{n} 个 JSON 解析失败:" + ";".join(bad[:5]) + (" …" if len(bad) > 5 else ""))
        else:
            rep.add("bible_files_parse", f"bible/{d}/", "PASS", f"{n} 个 JSON")

    # ---- 2. 宿主固定读取的路径与顶层键
    for rel, keys, partial, null_ok in LIST_KEYS:
        if rel in docs:
            data, err = docs[rel], ""
        else:
            data, err = load(rel)
        label = f"bible/{rel}#{keys[0]}"
        if err:
            rep.add("bible_host_keys", label, "FAIL", err)
            continue
        if not isinstance(data, dict):
            rep.add("bible_host_keys", label, "FAIL", f"顶层须是 JSON 对象,实际是 {type(data).__name__}(宿主按对象读)")
            continue
        hit = next((k for k in keys if k in data), None)
        if hit is not None:
            v = data[hit]
            if isinstance(v, list):
                rep.add("bible_host_keys", label, "PASS", f"{hit}: {len(v)} 条")
            elif v is None and null_ok:
                rep.add("bible_host_keys", label, "PASS", f"{hit}: null(宿主按空处理)")
            else:
                rep.add("bible_host_keys", label, "FAIL", f"{hit} 须是数组,实际是 {type(v).__name__}")
            continue
        alias = next((k for k in partial if isinstance(data.get(k), list)), None)
        if alias is not None:
            rep.add("bible_host_keys", label, "WARN",
                    f"顶层键写成 {alias}——只有部分宿主模块兼容,core.py 预览页只认 {keys[0]};新骨架请写 {keys[0]}")
            continue
        rep.add("bible_host_keys", label, "FAIL",
                "缺顶层键 " + " / ".join(keys) + f"(现有键:{', '.join(sorted(map(str, data))[:8]) or '无'})")

    style = docs.get("style.json")
    label = "bible/style.json#" + "|".join(STYLE_KEYS)
    if style is None:
        rep.add("bible_host_keys", label, "FAIL", "style.json 不可用(见 bible_files_parse)")
    else:
        hit = [k for k in STYLE_KEYS if k in style]
        wrong = [k for k in hit if not isinstance(style[k], (str, type(None)))]   # 宿主 .get(k) or '':null 按空串
        if hit and not wrong:
            rep.add("bible_host_keys", label, "PASS",
                    ", ".join(f"{k}: {'空' if not (style[k] or '').strip() else f'{len(style[k])} 字'}" for k in hit))
        elif wrong:
            rep.add("bible_host_keys", label, "FAIL", ", ".join(f"{k} 须是字符串" for k in wrong))
        elif "style_anchor_string_en" in style or isinstance(style.get("style_prompt_en"), dict):
            have = [k for k in STYLE_PARTIAL if k in style]
            rep.add("bible_host_keys", label, "WARN",
                    f"只有 {', '.join(have)}——背景图 / 全景链路读 style_fragment_en,读不到风格串;新骨架请写 style_fragment_en / style_fragment_ui")
        else:
            rep.add("bible_host_keys", label, "FAIL",
                    "缺顶层风格串键 style_fragment_en / style_fragment_ui(骨架写空串即可;宿主背景图 / 全景链路按 style_fragment_en 取风格串)")

    cs = docs.get("color_script.json")
    label = "bible/color_script.json#episodes"
    if cs is None:
        rep.add("bible_host_keys", label, "FAIL", "color_script.json 不可用(见 bible_files_parse)")
    elif "episodes" not in cs:
        rep.add("bible_host_keys", label, "FAIL",
                f"缺顶层键 episodes(modules/color_script.py 只认它;现有键:{', '.join(sorted(map(str, cs))[:8]) or '无'})")
    elif not isinstance(cs["episodes"], (list, dict, type(None))):   # episode_entries:eps or [],null 按空处理
        rep.add("bible_host_keys", label, "FAIL", f"episodes 须是数组或对象,实际是 {type(cs['episodes']).__name__}")
    else:
        rep.add("bible_host_keys", label, "PASS", f"episodes: {len(cs['episodes'] or [])} 条")

    # ---- 3. 文件名全 ASCII
    if inside(bible):
        names = [p for p in bible.rglob("*") if not p.name.isascii()]
        if names:
            rels = [p.relative_to(bible).as_posix() for p in names]
            rep.add("ascii_filename", "bible/", "FAIL",
                    f"{len(rels)} 个非 ASCII 文件 / 目录名:" + ";".join(rels[:5]) + (" …" if len(rels) > 5 else ""))
        else:
            rep.add("ascii_filename", "bible/", "PASS")
    else:
        rep.add("ascii_filename", "bible/", "FAIL", "bible/ resolve 后不在项目目录内")

    # ---- 4. 变更日志
    found = [n for n in CHANGELOGS if (bible / n).is_file() and inside(bible / n)]
    if not found:
        rep.add("bible_changelog", "bible/changelog.md", "FAIL", "变更日志不存在(memory-bible 受控变更逐条追加,骨架阶段即建)")
    elif found[0] == "changelog.json":
        _, err = load("changelog.json")
        rep.add("bible_changelog", "bible/changelog.json", "FAIL" if err else "PASS", err or "存量写法 changelog.json")
    else:
        rep.add("bible_changelog", "bible/changelog.md", "PASS")
    return rep


def main(argv=None) -> int:
    def configure(ap):
        ap.add_argument("--json", action="store_true", help="机器可读输出")
    args, root = parse_args("Bible 骨架机检 bible_skeleton_ok", ep=False, argv=argv, configure=configure)
    if not valid_slug(args.project):
        msg = f"项目名不合法(只收 [字母数字_-] 的名字,不收路径):{args.project!r}"
        if args.json:
            print(json.dumps({"check": CHECK, "pass": False, "error": msg}, ensure_ascii=False))
        else:
            print(f"[RESULT] {CHECK}: FAIL({msg})")
        return 2
    if not (root / "bible").is_dir():
        msg = f"bible/ 不存在:{root / 'bible'}"
        if args.json:
            print(json.dumps({"check": CHECK, "pass": False, "error": msg}, ensure_ascii=False))
        else:
            print(f"MISSING {root / 'bible'}")
        return 2
    rep = run(root)
    ok = not rep.failed
    if args.json:
        print(json.dumps({"check": CHECK, "project": args.project, "pass": ok,
                          "ascii_filename": not any(i["check"] == "ascii_filename" and i["status"] == "FAIL"
                                                    for i in rep.items),
                          "errors": [f"{i['path']}: {i['detail']}" for i in rep.failed],
                          "warnings": [f"{i['path']}: {i['detail']}" for i in rep.warned],
                          "items": rep.items}, ensure_ascii=False, indent=2))
        return 0 if ok else 1
    print(f"=== {CHECK} ({args.project}) ===")
    for i in rep.items:
        tag = {"PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN"}[i["status"]]
        print(f"[CHECK] {i['check']} {i['path']}: {tag}" + (f"  {i['detail']}" if i["detail"] else ""))
    print(f"[RESULT] {CHECK}: {'PASS' if ok else 'FAIL'}({len(rep.items)} 项,{len(rep.failed)} 失败,{len(rep.warned)} 提醒)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
