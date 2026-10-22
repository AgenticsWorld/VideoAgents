"""「加载技能」装到数据目录的用户技能目录(不写随安装包的 agents/…/skills/,桌面端升级换包不丢)。"""
import asyncio
import hashlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "code"))

PROMPT = "08-video-gen/prompt"             # 有内置技能 sd25-pe
WRITER = "01-story/dialogue-rewrite"       # 没有内置技能
SKILL_MD = b"---\nname: my-polish\ndescription: \xe5\xaf\xb9\xe7\x99\xbd\xe6\xb6\xa6\xe8\x89\xb2\n---\n# my-polish\n"


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    from services.runtime import core

    monkeypatch.setattr(core, "USER_SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(core, "STATE", {})
    monkeypatch.setattr(core, "ui_lang_code", lambda cfg=None: "zh")
    core.scan_agent_skills(refresh=True)
    yield core
    monkeypatch.undo()
    core.scan_agent_skills(refresh=True)        # 别把指向 tmp 的缓存留给后面的测试


def upload(core, agent, data=SKILL_MD, filename="SKILL.md"):
    return asyncio.run(core.api_skills_upload(agent, data, filename))


def test_upload_goes_to_user_dir_not_bundle(runtime):
    core = runtime
    r = upload(core, WRITER)
    f = core.USER_SKILLS_DIR / WRITER / "my-polish" / "SKILL.md"
    assert r["ok"] and f.read_bytes() == SKILL_MD
    assert not (core.AGENTS_DIR / WRITER / "skills").exists()
    s = next(x for x in r["skills"] if x["id"] == f"{WRITER}/my-polish")
    assert s["source"] == "user" and s["path"] == str(f) and Path(s["path"]).is_absolute()
    assert s["description"] == "对白润色"
    assert asyncio.run(core.api_skills_text(s["id"]))["text"] == SKILL_MD.decode()
    # 内置技能照旧是仓库相对路径、source=bundled
    sd25 = next(x for x in r["skills"] if x["id"] == f"{PROMPT}/sd25-pe")
    assert sd25["source"] == "bundled" and sd25["path"] == f"agents/{PROMPT}/skills/sd25-pe/SKILL.md"


def test_upload_rejects_same_name_as_installed_or_builtin(runtime):
    core = runtime
    upload(core, WRITER)
    with pytest.raises(core.ServiceError) as e:
        upload(core, WRITER)
    assert e.value.status_code == 409 and "已装过" in str(e.value)
    with pytest.raises(core.ServiceError) as e:
        upload(core, PROMPT, b"---\nname: sd25-pe\n---\nx\n")
    assert e.value.status_code == 409 and "内置技能同名" in str(e.value)
    assert not (core.USER_SKILLS_DIR / PROMPT).exists()


def test_user_copy_shadowed_by_builtin_is_listed_apart_and_deletable(runtime):
    core = runtime
    d = core.USER_SKILLS_DIR / PROMPT / "sd25-pe"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("# old user copy\n", encoding="utf-8")
    got = asyncio.run(core.api_skills_get(refresh=True))
    ids = [s["id"] for s in got["skills"]]
    assert ids.count(f"{PROMPT}/sd25-pe") == 1
    assert next(s for s in got["skills"] if s["id"] == f"{PROMPT}/sd25-pe")["source"] == "bundled"
    assert [(s["id"], s["source"]) for s in got["skills_shadowed"]] == [(f"{PROMPT}/sd25-pe", "user")]
    r = asyncio.run(core.api_skills_delete(f"{PROMPT}/sd25-pe"))       # 删的是用户那份
    assert r["skills_shadowed"] == [] and not d.exists()
    assert (core.AGENTS_DIR / PROMPT / "skills" / "sd25-pe" / "SKILL.md").is_file()
    assert not (core.USER_SKILLS_DIR / PROMPT).exists()                  # 空的 <agent>/、<类别>/ 一并清掉


def test_delete_only_user_skills(runtime):
    core = runtime
    upload(core, WRITER)
    r = asyncio.run(core.api_skills_delete(f"{WRITER}/my-polish"))
    assert r["ok"] and f"{WRITER}/my-polish" not in {s["id"] for s in r["skills"]}
    assert not (core.USER_SKILLS_DIR / "01-story").exists()
    with pytest.raises(core.ServiceError) as e:
        asyncio.run(core.api_skills_delete(f"{PROMPT}/sd25-pe"))
    assert e.value.status_code == 400
    for bad in (f"{WRITER}/my-polish", "../../etc/passwd", f"{WRITER}/../x"):
        with pytest.raises(core.ServiceError) as e:
            asyncio.run(core.api_skills_delete(bad))
        assert e.value.status_code == 404


def test_skill_record_version_hashes_user_skill(runtime):
    from modules import skill_records

    core = runtime
    s = next(x for x in upload(core, WRITER)["skills"] if x["id"] == f"{WRITER}/my-polish")
    run = {"id": "r1", "project": "p", "agent": WRITER, "agent_name": "w", "created": 0, "message": ""}
    rec = skill_records.seed(run, [dict(s, selected=True)], core.ROOT)
    assert rec[0]["version"] == hashlib.sha256(SKILL_MD).hexdigest()


def test_skill_md_path_resolves_builtin_then_user(monkeypatch, tmp_path):
    from modules import user_skills
    import prompt_skill_check as psc

    monkeypatch.setenv("VIDEOAGENTS_SKILLS_DIR", str(tmp_path))
    mine = tmp_path / PROMPT / "my-pe" / "SKILL.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("x", encoding="utf-8")
    assert user_skills.user_skills_dir() == tmp_path.resolve()
    assert psc.skill_md_path(f"{PROMPT}/my-pe") == mine.resolve()
    assert psc.skill_md_path(f"{PROMPT}/sd25-pe") == ROOT / "agents" / PROMPT / "skills" / "sd25-pe" / "SKILL.md"
    # 两处都没有 → 内置路径(调用方报找不到);非法 id 不进用户目录
    assert psc.skill_md_path(f"{PROMPT}/nope") == ROOT / "agents" / PROMPT / "skills" / "nope" / "SKILL.md"
    (tmp_path / "u").mkdir()
    (tmp_path / "y").mkdir()                              # = <用户目录 u>/../y:不加 id 校验就会被读到
    (tmp_path / "y" / "SKILL.md").write_text("x", encoding="utf-8")
    assert not str(user_skills.skill_md_path("../y", user_dir=tmp_path / "u")).startswith(str(tmp_path))
    monkeypatch.delenv("VIDEOAGENTS_SKILLS_DIR")
    monkeypatch.setenv("VIDEOAGENTS_DATA_DIR", str(tmp_path / "data"))
    assert user_skills.user_skills_dir() == (tmp_path / "data" / "skills").resolve()
