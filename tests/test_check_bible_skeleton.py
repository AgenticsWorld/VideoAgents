"""Bible 骨架机检 code/check_bible_skeleton.py(#153):空骨架通过、缺顶层键 / 非 ASCII 文件名失败、项目名不收路径。"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "code" / "check_bible_skeleton.py"

WORLD = ("world", "timeline", "geography", "religion", "culture", "politics", "economy", "cultivation", "dictionary")


def _skeleton(proj: Path) -> Path:
    """最小合法空骨架:全部容器为空。"""
    b = proj / "bible"
    for d in ("characters", "creatures", "scenes"):
        (b / d).mkdir(parents=True)
    for w in WORLD:
        (b / f"{w}.json").write_text(json.dumps({"_meta": {"file": f"bible/{w}.json"}}), encoding="utf-8")
    docs = {
        "characters/index.json": {"characters": []},
        "creatures/index.json": {"creatures": []},
        "scenes/index.json": {"scenes": []},
        "props.json": {"props": []},
        "costumes.json": {"characters": []},
        "style.json": {"style_fragment_en": "", "style_fragment_ui": None, "negative_prompt_en": ""},
        "color_script.json": {"episodes": []},
    }
    for rel, data in docs.items():
        (b / rel).write_text(json.dumps(data), encoding="utf-8")
    (b / "changelog.md").write_text("# Bible changelog\n", encoding="utf-8")
    return b


def _run(tmp_path: Path, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("VIDEOAGENTS_")}
    env["VIDEOAGENTS_DATA_DIR"] = str(tmp_path)
    r = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=60)
    return r.returncode, r.stdout + r.stderr


def _json(tmp_path, slug="p1"):
    rc, out = _run(tmp_path, "--project", slug, "--json")
    return rc, json.loads(out)


def test_empty_skeleton_passes(tmp_path):
    _skeleton(tmp_path / "projects" / "p1")
    rc, out = _run(tmp_path, "--project", "p1")
    assert rc == 0, out
    assert "[RESULT] bible_skeleton_ok: PASS" in out
    for name in ("bible_files_parse", "bible_host_keys", "ascii_filename", "bible_changelog"):
        assert f"[CHECK] {name} " in out
    assert "FAIL" not in out.replace("0 失败", "")
    rc, d = _json(tmp_path)
    assert rc == 0 and d["pass"] is True and d["ascii_filename"] is True and not d["errors"]


def test_missing_top_level_key_fails(tmp_path):
    b = _skeleton(tmp_path / "projects" / "p1")
    (b / "characters" / "index.json").write_text(json.dumps({"_meta": {}}), encoding="utf-8")
    (b / "color_script.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    rc, d = _json(tmp_path)
    assert rc == 1 and d["pass"] is False
    failed = {i["path"] for i in d["items"] if i["status"] == "FAIL"}
    assert failed == {"bible/characters/index.json#characters", "bible/color_script.json#episodes"}


def test_host_key_types_and_aliases(tmp_path):
    b = _skeleton(tmp_path / "projects" / "p1")
    # 宿主 core.py 只认 creatures,storyboard_board 兼容 entries:记 WARN 不失败
    (b / "creatures" / "index.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    # props.json 的 entries 是 core.py 认的别名;null 宿主按空处理
    (b / "props.json").write_text(json.dumps({"entries": None}), encoding="utf-8")
    rc, d = _json(tmp_path)
    assert rc == 0, d
    assert [i["path"] for i in d["items"] if i["status"] == "WARN"] == ["bible/creatures/index.json#creatures"]
    # index.json 数组键为 null:core.py 直接迭代会抛错 → FAIL;style 只有嵌套写法 → FAIL;顶层是数组 → FAIL
    (b / "scenes" / "index.json").write_text(json.dumps({"scenes": None}), encoding="utf-8")
    (b / "style.json").write_text(json.dumps({"style": {"style_fragment_en": "x"}}), encoding="utf-8")
    (b / "world.json").write_text("[]", encoding="utf-8")
    rc, d = _json(tmp_path)
    assert rc == 1
    failed = {i["path"] for i in d["items"] if i["status"] == "FAIL"}
    assert failed == {"bible/scenes/index.json#scenes", "bible/style.json#style_fragment_en|style_fragment_ui",
                      "bible/world.json"}


def test_unparsable_json_and_missing_changelog_fail(tmp_path):
    b = _skeleton(tmp_path / "projects" / "p1")
    (b / "characters" / "CHAR-0001").mkdir()
    (b / "characters" / "CHAR-0001" / "profile.json").write_text("{bad", encoding="utf-8")
    (b / "changelog.md").unlink()
    rc, d = _json(tmp_path)
    assert rc == 1
    failed = {i["path"]: i["detail"] for i in d["items"] if i["status"] == "FAIL"}
    assert set(failed) == {"bible/characters/", "bible/changelog.md"}
    assert "CHAR-0001/profile.json" in failed["bible/characters/"]
    # 存量写法 changelog.json 同效
    (b / "characters" / "CHAR-0001" / "profile.json").write_text("{}", encoding="utf-8")
    (b / "changelog.json").write_text("[]", encoding="utf-8")
    rc, d = _json(tmp_path)
    assert rc == 0, d


def test_non_ascii_filename_fails(tmp_path):
    b = _skeleton(tmp_path / "projects" / "p1")
    (b / "scenes" / "SCN-0001").mkdir()
    (b / "scenes" / "SCN-0001" / "客栈.json").write_text("{}", encoding="utf-8")
    rc, out = _run(tmp_path, "--project", "p1")
    assert rc == 1
    assert "[CHECK] ascii_filename bible/: FAIL" in out and "客栈.json" in out
    rc, d = _json(tmp_path)
    assert d["ascii_filename"] is False


def test_symlink_out_of_project_fails(tmp_path):
    b = _skeleton(tmp_path / "projects" / "p1")
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"props": []}), encoding="utf-8")
    (b / "props.json").unlink()
    (b / "props.json").symlink_to(outside)
    rc, d = _json(tmp_path)
    assert rc == 1
    assert any(i["path"] == "bible/props.json" and i["status"] == "FAIL" and "项目目录" in i["detail"] for i in d["items"])


def test_project_must_be_a_name(tmp_path):
    _skeleton(tmp_path / "projects" / "p1")
    _skeleton(tmp_path / "p2")          # 在 projects/ 之外:../p2 不得被读到
    for bad in ("../p2", "p1/../p1", "/etc", "..", ""):
        rc, out = _run(tmp_path, "--project", bad)
        assert rc == 2, (bad, out)
        assert "PASS" not in out
    rc, out = _run(tmp_path, "--project", "nope")
    assert rc == 2 and "MISSING" in out


def test_workflow_spec_names_the_check():
    yaml = __import__("pytest").importorskip("yaml")
    dag = yaml.safe_load((ROOT / "agents" / "workflow.yaml").read_text(encoding="utf-8"))
    task = next(t for ph in dag["phases"] for t in ph.get("tasks", []) if t.get("id") == "p0-bible-init")
    assert task["validation"]["auto"] == ["bible_skeleton_ok", "ascii_filename"]
    cmd = "python3 code/check_bible_skeleton.py --project <slug>"
    md = (ROOT / "agents" / "WORKFLOW.md").read_text(encoding="utf-8")
    row = [ln for ln in md.splitlines() if ln.startswith("| memory-bible | 初始化空 Bible 骨架")]
    assert len(row) == 1 and cmd in row[0] and "bible_skeleton_ok" in row[0]
    soul = (ROOT / "agents" / "00-orchestration" / "memory-bible" / "SOUL.md").read_text(encoding="utf-8")
    assert cmd in soul and "- p0-bible-init:**bible_skeleton_ok + ascii_filename**" in soul
