#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""vc.py — 项目版本库工具(00-orchestration/version Agent 专用)

产物皆文件、皆有版本:落盘即登记新版本,旧版存于嵌入式 git 对象库,永不覆盖、永不删除。
本文件是唯一实现;各项目 data/projects/<slug>/.version/vc.py 为薄壳转发,
调用契约不变:
  python3 .version/vc.py init
  python3 .version/vc.py register <artifact...> --task-id T --attempt N --reason R [--tag TAG]
  python3 .version/vc.py log [artifact]
  python3 .version/vc.py show <artifact> <vN>            # 输出该版本内容到 stdout
  python3 .version/vc.py diff <artifact> <vA> <vB>
  python3 .version/vc.py rollback <artifact> <vN> --task-id T --attempt N [--reason R]
  python3 .version/vc.py freeze --tag TAG --task-id T <artifact-or-dir...>
  python3 .version/vc.py tag-add --tag TAG --task-id T --reason R <artifact-or-dir...>
  python3 .version/vc.py unlock <artifact-or-dir...> --task-id T --reason R
新项目接入:python3 modules/vc.py install data/projects/<slug>(写薄壳 + init)。
所有 artifact 路径均相对 .version/ 的上级目录(即项目根 data/projects/<slug>/)。
"""
import argparse, hashlib, json, os, stat, sys
from datetime import datetime, timezone

import pygit2

# 由 main(vdir=...) 初始化;缺省取本文件所在目录(直接把本文件拷进 .version/ 也能独立工作)
VDIR = ROOT = GITDIR = MANIFEST = CHANGELOG = None

SHIM = '''#!/usr/bin/env python3
"""薄壳:实现见仓库根 modules/vc.py;用法不变:python3 .version/vc.py <cmd> ..."""
import sys
from pathlib import Path

VDIR = Path(__file__).resolve().parent
for anc in VDIR.parents:
    if (anc / "modules" / "vc.py").is_file():
        sys.path.insert(0, str(anc / "modules"))
        break
from vc import main

if __name__ == "__main__":
    main(vdir=str(VDIR))
'''


def _init_paths(vdir=None):
    global VDIR, ROOT, GITDIR, MANIFEST, CHANGELOG
    VDIR = os.path.abspath(vdir) if vdir else os.path.dirname(os.path.abspath(__file__))
    ROOT = os.path.dirname(VDIR)
    GITDIR = os.path.join(VDIR, "repo.git")
    MANIFEST = os.path.join(VDIR, "manifest.json")
    CHANGELOG = os.path.join(VDIR, "changelog.jsonl")


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def repository():
    return pygit2.Repository(GITDIR)


def _tree_entry(tree, path):
    obj = tree
    for part in path.split("/"):
        obj = repository()[obj[part].id]
    return obj


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest():
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


def save_manifest(m):
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MANIFEST)


def append_changelog(rec):
    with open(CHANGELOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def rel(p):
    """归一化为项目根相对路径"""
    ap = os.path.abspath(os.path.join(ROOT, p) if not os.path.isabs(p) else p)
    r = os.path.relpath(ap, ROOT)
    if r.startswith(".."):
        sys.exit(f"错误:{p} 不在项目目录内")
    if r == ".version" or r.startswith(".version" + os.sep):
        sys.exit(f"错误:{p} 属于版本库自身,不做版本化")
    return r.replace(os.sep, "/")


def cmd_init(_):
    os.makedirs(GITDIR, exist_ok=True)
    if not os.path.exists(os.path.join(GITDIR, "HEAD")):
        pygit2.init_repository(
            GITDIR,
            bare=False,
            workdir_path=ROOT,
            initial_head="main",
            flags=(pygit2.enums.RepositoryInitFlag.MKPATH
                   | pygit2.enums.RepositoryInitFlag.NO_DOTGIT_DIR),
        )
    repo = repository()
    repo.config["user.name"] = "version-agent"
    repo.config["user.email"] = "version-agent@videoagents.local"
    with open(os.path.join(GITDIR, "info", "exclude"), "w") as f:
        f.write(".version/\n")
    if not os.path.exists(MANIFEST):
        save_manifest({"project": os.path.basename(ROOT), "created_at": now(),
                       "artifacts": {}, "tags": {}})
    if not os.path.exists(CHANGELOG):
        open(CHANGELOG, "a").close()
    print(f"版本库已初始化:{GITDIR}")


def cmd_install(a):
    """给项目目录写薄壳 .version/vc.py 并初始化版本库(新项目 p0-version-init 用)。"""
    proj = os.path.abspath(a.project_dir)
    if not os.path.isdir(proj):
        sys.exit(f"错误:项目目录不存在 {proj}")
    vdir = os.path.join(proj, ".version")
    os.makedirs(vdir, exist_ok=True)
    shim_path = os.path.join(vdir, "vc.py")
    tmp = shim_path + ".new"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(SHIM)
    os.replace(tmp, shim_path)
    _init_paths(vdir)
    cmd_init(a)
    print(f"薄壳已写入:{shim_path}")


def _register_one(m, path, task_id, attempt, reason, tag=None):
    p = rel(path)
    fp = os.path.join(ROOT, p)
    if not os.path.isfile(fp):
        sys.exit(f"错误:文件不存在 {p}")
    digest = sha256(fp)
    entry = m["artifacts"].setdefault(p, {"current": 0, "history": []})
    if entry["history"] and entry["history"][-1]["sha256"] == digest:
        print(f"跳过(内容未变化): {p} 仍为 @v{entry['current']}")
        return None
    # 冻结的是历史版本(git 对象 + 标签),新写入永远走新版本号,绝不触碰旧版
    if not os.access(fp, os.W_OK):
        os.chmod(fp, os.stat(fp).st_mode | stat.S_IWUSR)
    repo = repository()
    index = repo.index
    index.add(p)
    index.write()
    ver = entry["current"] + 1
    msg = f"{p} @v{ver} | task={task_id} attempt={attempt} | {reason}"
    tree = index.write_tree()
    parents = [] if repo.head_is_unborn else [repo.head.target]
    signature = pygit2.Signature("version-agent", "version-agent@videoagents.local")
    commit = str(repo.create_commit("HEAD", signature, signature, msg, tree, parents))
    rec = {"artifact": p, "version": f"v{ver}", "task_id": task_id,
           "attempt": attempt, "reason": reason, "frozen": False,
           "tag": tag, "commit": commit, "sha256": digest, "timestamp": now()}
    entry["current"] = ver
    entry["history"].append(rec)
    append_changelog(rec)
    print(f"已登记: {p} @v{ver} (commit {commit[:10]})")
    return rec


def cmd_register(a):
    m = load_manifest()
    for path in a.artifacts:
        _register_one(m, path, a.task_id, a.attempt, a.reason, a.tag)
    save_manifest(m)


def _find(m, artifact, vstr):
    p = rel(artifact)
    entry = m["artifacts"].get(p)
    if not entry:
        sys.exit(f"错误:{p} 未登记")
    v = int(vstr.lstrip("v"))
    for rec in entry["history"]:
        if rec["version"] == f"v{v}":
            return p, rec
    sys.exit(f"错误:{p} 无版本 v{v}(当前最高 v{entry['current']})")


def cmd_show(a):
    m = load_manifest()
    p, rec = _find(m, a.artifact, a.version)
    # pygit2 exposes the stored blob bytes directly, preserving trailing newlines.
    commit = repository()[pygit2.Oid(hex=rec["commit"])]
    blob = _tree_entry(commit.tree, p)
    sys.stdout.buffer.write(blob.data)


def cmd_diff(a):
    m = load_manifest()
    p, r1 = _find(m, a.artifact, a.v1)
    _, r2 = _find(m, a.artifact, a.v2)
    repo = repository()
    out = repo.diff(repo[r1["commit"]], repo[r2["commit"]], paths=[p]).patch
    print(out if out else f"{p}: {a.v1} 与 {a.v2} 内容一致")


def cmd_rollback(a):
    m = load_manifest()
    p, rec = _find(m, a.artifact, a.version)
    fp = os.path.join(ROOT, p)
    commit = repository()[pygit2.Oid(hex=rec["commit"])]
    blob = _tree_entry(commit.tree, p).data
    if os.path.exists(fp) and not os.access(fp, os.W_OK):
        os.chmod(fp, os.stat(fp).st_mode | stat.S_IWUSR)
    with open(fp, "wb") as f:
        f.write(blob)
    reason = a.reason or f"回滚至 {rec['version']}"
    new = _register_one(m, p, a.task_id, a.attempt, f"[rollback→{rec['version']}] {reason}")
    save_manifest(m)
    if new:
        assert new["sha256"] == rec["sha256"], "回滚校验失败:内容与目标版本不一致"
        print(f"回滚完成: {p} 现为 @{new['version']},内容等同 @{rec['version']} (sha256 校验通过)")


def cmd_freeze(a):
    m = load_manifest()
    if a.tag in m["tags"]:
        sys.exit(f"错误:标签 {a.tag} 已存在,冻结标签不可覆盖(需新标签)")
    reason = a.reason or f"冻结打标 {a.tag}"
    targets = []
    for t in a.artifacts:
        p = rel(t)
        fp = os.path.join(ROOT, p)
        if os.path.isdir(fp):
            targets += [q for q in m["artifacts"] if q == p or q.startswith(p + "/")]
        else:
            targets.append(p)
    targets = sorted(set(targets))
    frozen = []
    for p in targets:
        entry = m["artifacts"].get(p)
        if not entry or not entry["history"]:
            sys.exit(f"错误:{p} 未登记,无法冻结")
        rec = entry["history"][-1]
        rec["frozen"] = True
        rec["tag"] = a.tag
        os.chmod(os.path.join(ROOT, p), 0o444)   # 工作区写保护
        frozen.append({"artifact": p, "version": rec["version"], "commit": rec["commit"]})
        append_changelog({"artifact": p, "version": rec["version"], "task_id": a.task_id,
                          "attempt": 1, "reason": reason, "frozen": True,
                          "tag": a.tag, "commit": rec["commit"], "timestamp": now()})
    tag_name = a.tag.replace("@", "-at-").replace("/", "_")
    repository().create_reference(
        f"refs/tags/{tag_name}", pygit2.Oid(hex=frozen[-1]["commit"])
    )
    m["tags"][a.tag] = {"frozen_at": now(), "task_id": a.task_id, "artifacts": frozen}
    save_manifest(m)
    print(f"已冻结 {len(frozen)} 个产物 → 标签 {a.tag}(git tag: "
          f"{a.tag.replace('@','-at-').replace('/','_')});工作区文件已设只读")


def cmd_tag_add(a):
    """把已登记产物的当前版本追加冻结进一个已存在的标签(与 freeze 的『标签不可
    已存在』约束互补,专治『同一发布批次的文件因故迟到,需补入已冻结的标签』场景):
    要求标签必须已存在(freeze 首创、tag-add 追加);每个 artifact 的当前版本被
    标记 frozen=true 并写保护;若某 artifact 已在该标签下则跳过、不重复登记。"""
    m = load_manifest()
    if a.tag not in m["tags"]:
        sys.exit(f"错误:标签 {a.tag} 不存在,tag-add 仅用于追加进已存在的标签(首创请用 freeze)")
    targets = []
    for t in a.artifacts:
        p = rel(t)
        fp = os.path.join(ROOT, p)
        if os.path.isdir(fp):
            targets += [q for q in m["artifacts"] if q == p or q.startswith(p + "/")]
        else:
            targets.append(p)
    targets = sorted(set(targets))
    tag_entry = m["tags"][a.tag]
    already = {x["artifact"] for x in tag_entry["artifacts"]}
    added, skipped = [], []
    for p in targets:
        entry = m["artifacts"].get(p)
        if not entry or not entry["history"]:
            sys.exit(f"错误:{p} 未登记,无法追加进标签")
        rec = entry["history"][-1]
        if p in already:
            skipped.append(p)
            continue
        rec["frozen"] = True
        rec["tag"] = a.tag
        os.chmod(os.path.join(ROOT, p), 0o444)
        added.append({"artifact": p, "version": rec["version"], "commit": rec["commit"]})
        append_changelog({"artifact": p, "version": rec["version"], "task_id": a.task_id,
                          "attempt": 1, "reason": a.reason, "frozen": True,
                          "tag": a.tag, "commit": rec["commit"], "timestamp": now(),
                          "note": f"tag-add:追加进已存在标签 {a.tag}(非首创冻结)"})
    tag_entry["artifacts"].extend(added)
    save_manifest(m)
    print(f"已追加 {len(added)} 个产物进已存在标签 {a.tag};跳过 {len(skipped)} 个(已在该标签下)")
    for x in added:
        print(f"  {x['artifact']} @{x['version']}")
    for p in skipped:
        print(f"  跳过: {p}(已在标签 {a.tag} 下)")


def cmd_unlock(a):
    """受控解冻:对已冻结产物解除工作区只读位,供后续 Agent 替换内容；
    不修改历史版本记录（该版本的 frozen=true 标签仍如实保留，替换后必须
    调用 register 生成新版本，新版本默认不冻结，需另行 freeze 才会再次只读）。"""
    m = load_manifest()
    targets = []
    for t in a.artifacts:
        p = rel(t)
        fp = os.path.join(ROOT, p)
        if os.path.isdir(fp):
            targets += [q for q in m["artifacts"] if q == p or q.startswith(p + "/")]
        else:
            targets.append(p)
    targets = sorted(set(targets))
    unlocked, skipped = [], []
    for p in targets:
        entry = m["artifacts"].get(p)
        if not entry or not entry["history"]:
            sys.exit(f"错误:{p} 未登记,无法解锁")
        rec = entry["history"][-1]
        fp = os.path.join(ROOT, p)
        if not rec.get("frozen"):
            skipped.append(p)
            continue
        if os.path.exists(fp) and not os.access(fp, os.W_OK):
            os.chmod(fp, os.stat(fp).st_mode | stat.S_IWUSR)
        unlocked.append({"artifact": p, "version": rec["version"], "tag": rec.get("tag")})
        append_changelog({"type": "controlled_unlock", "artifact": p, "version": rec["version"],
                          "tag": rec.get("tag"), "task_id": a.task_id, "reason": a.reason,
                          "timestamp": now(),
                          "note": "工作区只读位已解除以便受控替换;该版本历史记录 frozen=true 保持不变;"
                                  "替换完成后必须调用 register 生成新版本(默认不冻结),如需重新只读须另行 freeze"})
    print(f"已解锁 {len(unlocked)} 个产物(工作区转为可写);跳过 {len(skipped)} 个(当前未处于冻结状态)")
    for u in unlocked:
        print(f"  {u['artifact']} @{u['version']}(原 tag={u['tag']})")
    for p in skipped:
        print(f"  跳过: {p}(未冻结)")


def cmd_log(a):
    m = load_manifest()
    arts = [rel(a.artifact)] if a.artifact else sorted(m["artifacts"])
    for p in arts:
        e = m["artifacts"].get(p)
        if not e:
            sys.exit(f"错误:{p} 未登记")
        for r in e["history"]:
            flag = f" [FROZEN {r['tag']}]" if r.get("frozen") else ""
            print(f"{p} @{r['version']}{flag}  task={r['task_id']} attempt={r['attempt']}"
                  f"  {r['timestamp']}  {r['reason']}")


def main(argv=None, vdir=None):
    _init_paths(vdir)
    ap = argparse.ArgumentParser(prog="vc.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    s = sub.add_parser("install")
    s.add_argument("project_dir")
    s.set_defaults(fn=cmd_install)
    s = sub.add_parser("register")
    s.add_argument("artifacts", nargs="+")
    s.add_argument("--task-id", required=True)
    s.add_argument("--attempt", type=int, default=1)
    s.add_argument("--reason", required=True)
    s.add_argument("--tag", default=None)
    s.set_defaults(fn=cmd_register)
    s = sub.add_parser("show")
    s.add_argument("artifact"); s.add_argument("version")
    s.set_defaults(fn=cmd_show)
    s = sub.add_parser("diff")
    s.add_argument("artifact"); s.add_argument("v1"); s.add_argument("v2")
    s.set_defaults(fn=cmd_diff)
    s = sub.add_parser("rollback")
    s.add_argument("artifact"); s.add_argument("version")
    s.add_argument("--task-id", required=True)
    s.add_argument("--attempt", type=int, default=1)
    s.add_argument("--reason", default=None)
    s.set_defaults(fn=cmd_rollback)
    s = sub.add_parser("freeze")
    s.add_argument("artifacts", nargs="+")
    s.add_argument("--tag", required=True)
    s.add_argument("--task-id", required=True)
    s.add_argument("--reason", default=None)
    s.set_defaults(fn=cmd_freeze)
    s = sub.add_parser("tag-add")
    s.add_argument("artifacts", nargs="+")
    s.add_argument("--tag", required=True)
    s.add_argument("--task-id", required=True)
    s.add_argument("--reason", required=True)
    s.set_defaults(fn=cmd_tag_add)
    s = sub.add_parser("unlock")
    s.add_argument("artifacts", nargs="+")
    s.add_argument("--task-id", required=True)
    s.add_argument("--reason", required=True)
    s.set_defaults(fn=cmd_unlock)
    s = sub.add_parser("log")
    s.add_argument("artifact", nargs="?")
    s.set_defaults(fn=cmd_log)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
