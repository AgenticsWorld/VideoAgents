# -*- coding: utf-8 -*-
"""花字机检 CLI(code/check_captions.py)三个阶段的端到端回归:设计 / 烧录 / 成片。

每个用例搭一个最小项目,直接调 main(),按输出里的 `[CHECK] <机检名>: PASS|FAIL` 逐项断言——
锁的是「哪种问题让哪一项 FAIL、别的项不受牵连」,以及回执指纹在该过期时过期、不该过期时不过期。
烧录本身(起 Chromium 截图合成)不在这里:副本与回执按引擎的指纹口径直接伪造,工具链检查打桩。
烧录 / 成片阶段要探测视频,需要 ffmpeg;设计阶段不需要。
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "modules"))
sys.path.insert(0, str(ROOT / "code"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import avsync  # noqa: E402
import caption_catalog as cc  # noqa: E402
import caption_timeline as ct  # noqa: E402
import captions as cap  # noqa: E402
import captions_html as chtml  # noqa: E402
import check_captions as chk  # noqa: E402
from _caption_helpers import TEMPLATE, font_template, make_font  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="需要 ffmpeg/ffprobe")
EP = "ep01"
DESIGN = ("caption_schema_v2", "caption_groups_valid", "caption_time_consistent", "caption_assets_resolved",
          "caption_policy_fresh", "ascii_filename")


# ---------------------------------------------------------------- 项目搭建

def _cap(i, gid, ls, le, start, **over):
    c = {"id": f"cap{i:03d}", "text": "青云宗", "type": "location", "group_id": gid, "local_start": ls, "local_end": le,
         "start": start, "end": start + (le - ls), "template_ref": "template:title", "em_pct": 10, "position": "top_center"}
    c.update(over)
    return c


def _captions():
    """grp001 占 0–2 s、grp002 占 2–5 s:第二条的集级 start = 组起点 2.0 + local 1.0。"""
    return {"schema_version": 3, "captions": [_cap(1, "grp001", 0.5, 1.5, 0.5), _cap(2, "grp002", 1.0, 2.0, 3.0, text="藏经阁")]}


class Project:
    def __init__(self, root: Path):
        self.root = root / "demo"
        self.data_dir = root / "data"
        self.ed = self.root / "edit" / EP
        self.cap_dir = self.root / "assets" / "clips_caption" / EP
        self.ed.mkdir(parents=True)
        (self.root / "directing" / EP).mkdir(parents=True)
        (self.root / "directing" / EP / "shot_list.json").write_text(json.dumps({"episode": EP, "generation_groups": [
            {"group_id": "grp001", "scene_id": "SCN-0001", "shots": ["sh001"], "total_duration_s": 2},
            {"group_id": "grp002", "scene_id": "SCN-0001", "shots": ["sh002"], "total_duration_s": 3}],
            "shots": [{"shot_id": "sh001", "duration_s": 2}, {"shot_id": "sh002", "duration_s": 3}]}), encoding="utf-8")
        tpl = self.root / "edit" / "caption_templates"
        tpl.mkdir()
        (tpl / "title.html").write_text(TEMPLATE, encoding="utf-8")
        self.manifests()
        self.write(_captions())

    def manifests(self):
        (self.data_dir / "fonts").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "sfx").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "fonts" / "manifest.json").write_text(json.dumps({"schema": "fonts.manifest.v1", "fonts": []}))
        (self.data_dir / "sfx" / "manifest.json").write_text(json.dumps({"schema": "sfx.manifest.v1", "sfx": [
            {"id": "whoosh_01", "file": "whoosh_01.wav"}]}))

    def write(self, data):
        (self.ed / "captions.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def read(self):
        return json.loads((self.ed / "captions.json").read_text(encoding="utf-8"))

    def edit(self, fn):
        data = self.read()
        fn(data)
        self.write(data)

    def settings(self, **output):
        (self.root / "settings.json").write_text(json.dumps({"output": output}), encoding="utf-8")

    def fonts(self):
        return cap.load_fonts_manifest(self.data_dir / "fonts" / "manifest.json", self.root)


@pytest.fixture()
def project(tmp_path):
    return Project(tmp_path)


@pytest.fixture()
def run(monkeypatch, capsys):
    def _run(p: Project, *args, chromium=(True, "stub")):
        monkeypatch.setattr(chk, "FONTS_DIR", p.data_dir / "fonts")
        monkeypatch.setattr(chk, "SFX_DIR", p.data_dir / "sfx")
        monkeypatch.setattr(chtml, "chromium_ready", lambda: chromium)      # 不真起浏览器
        monkeypatch.setattr(sys, "argv", ["check_captions.py", "--project", "demo", "--ep", EP, "--out-root", str(p.root), *args])
        capsys.readouterr()
        with pytest.raises(SystemExit) as exc:
            chk.main()
        out = capsys.readouterr().out
        checks = dict(re.findall(r"^\[CHECK\] (\S+): (PASS|FAIL)", out, re.M))
        skips = set(re.findall(r"^\[SKIP \] (\S+):", out, re.M))
        return exc.value.code, checks, skips, out
    return _run


def _failed(checks):
    return sorted(k for k, v in checks.items() if v == "FAIL")


# ---------------------------------------------------------------- 设计阶段

def test_design_all_pass(project, run):
    code, checks, skips, out = run(project)
    assert code == 0, out
    assert checks == {name: "PASS" for name in DESIGN}
    assert {"caption_text_from_source", "caption_speech_aligned", "caption_types_allowed", "caption_types_covered",
            "render", "final"} <= skips
    assert "[RESULT] 6/6 PASS" in out


@pytest.mark.parametrize("index,change,failing", [
    (0, dict(group_id="grp999"), ["caption_groups_valid"]),
    (0, dict(local_end=9.0), ["caption_groups_valid", "caption_time_consistent"]),      # 超出组时长 = 这段画面不存在,时间轴那项也报
    (1, dict(start=2.0), ["caption_time_consistent"]),        # 写成了组内时间,没加组起点
    (0, dict(sfx={"sfx_id": "boom"}), ["caption_assets_resolved"]),
    (0, dict(type="nope"), ["caption_schema_v2"]),
    (1, dict(em_pct=40), ["caption_schema_v2"]),
    (0, dict(template_ref="template:missing"), ["caption_schema_v2"]),
])
def test_design_violation_fails_only_its_check(project, run, index, change, failing):
    project.edit(lambda d: d["captions"][index].update(change))
    code, checks, _skips, out = run(project)
    assert code == 1 and _failed(checks) == failing, out


def test_design_start_within_tolerance_or_absent(project, run):
    project.edit(lambda d: d["captions"][1].update(start=3.09))
    assert run(project)[0] == 0
    project.edit(lambda d: d["captions"][1].pop("start"))                # 没写集级 start:不对账
    assert run(project)[0] == 0
    project.edit(lambda d: d["captions"][1].update(start=3.2))
    assert _failed(run(project)[1]) == ["caption_time_consistent"]


def test_design_missing_manifests(project, run):
    (project.data_dir / "sfx" / "manifest.json").unlink()
    code, checks, _skips, out = run(project)
    assert code == 1 and _failed(checks) == ["caption_assets_resolved"] and "缺 fonts/sfx manifest" in out


def test_design_absent_captions_is_skip_unless_required(project, run):
    (project.ed / "captions.json").unlink()
    code, checks, skips, _out = run(project)
    assert code == 0 and checks == {} and "design" in skips
    code, checks, _skips, _out = run(project, "--require", "design")
    assert code == 1 and checks == {"caption_schema_v2": "FAIL"}


def test_design_missing_shot_list(project, run):
    (project.root / "directing" / EP / "shot_list.json").unlink()
    code, checks, _skips, _out = run(project)
    assert code == 1 and checks == {"caption_groups_valid": "FAIL"}


def test_design_non_ascii_filename(project, run):
    odd = project.ed / "花字.json"
    odd.write_text(json.dumps(_captions(), ensure_ascii=False), encoding="utf-8")
    code, checks, _skips, _out = run(project, "--captions", str(odd))
    assert code == 1 and _failed(checks) == ["ascii_filename"]


def test_design_manual_policy(project, run):
    project.settings(caption_mode="manual", caption_types=["location", "keyword"])
    code, checks, skips, out = run(project)
    assert code == 1 and _failed(checks) == ["caption_policy_fresh", "caption_types_covered"], out      # 缺盖章;keyword 勾了没出
    assert checks["caption_types_allowed"] == "PASS" and "caption_types_allowed" not in skips
    # 出了没勾选的类型
    project.edit(lambda d: d["captions"][0].update(type="headline", em_pct=20))
    assert "caption_types_allowed" in _failed(run(project)[1])
    # 盖章 + 写明 keyword 不出的理由 → 全过
    data = _captions()
    cc.stamp(data, {"mode": "manual", "types": ["keyword", "location"]})
    data["type_skips"] = {"keyword": "本集没有需要强调的关键词"}
    project.write(data)
    code, checks, _skips, out = run(project)
    assert code == 0 and checks["caption_types_covered"] == "PASS" and checks["caption_policy_fresh"] == "PASS", out
    # 用户改了勾选集合而设计没重跑 → 盖章过期
    project.settings(caption_mode="manual", caption_types=["location"])
    assert _failed(run(project)[1]) == ["caption_policy_fresh"]
    # 切回自动而盖章还是手动的 → 同样过期;类型两项改为跳过
    project.settings(caption_mode="auto")
    code, checks, skips, _out = run(project)
    assert _failed(checks) == ["caption_policy_fresh"] and {"caption_types_allowed", "caption_types_covered"} <= skips


def test_design_av_project_checks_text_against_transcript(project, run):
    (project.root / "av").mkdir()
    (project.root / "av" / "beat_track.json").write_text(json.dumps({"segments": [{"text": "他上了青云宗,"}, {"text": "进了藏经阁"}]},
                                                                    ensure_ascii=False), encoding="utf-8")
    checks = run(project)[1]
    assert checks["caption_text_from_source"] == "PASS"
    assert checks["caption_speech_aligned"] == "FAIL"                    # 有台本时间码却没有逐字轨
    project.edit(lambda d: d["captions"][0].update(text="无敌剑"))
    assert run(project)[1]["caption_text_from_source"] == "FAIL"
    (project.root / "bible").mkdir()
    (project.root / "bible" / "dictionary.json").write_text("{}")        # 有词典的项目走 dictionary_match_100
    code, checks, skips, _out = run(project)
    assert "caption_text_from_source" in skips and "caption_text_from_source" not in checks


def test_design_main_flow_with_subtitles_needs_word_track(project, run):
    (project.ed / "subtitles.srt").write_text("1\n00:00:00,500 --> 00:00:01,500\n他上了青云宗\n", encoding="utf-8")
    code, checks, _skips, out = run(project)
    assert code == 1 and _failed(checks) == ["caption_speech_aligned"] and "speech-align" in out


# ---------------------------------------------------------------- 素材

def _clip(path: Path, color: str, seconds: float, size="160x90", audio=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=24:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=f=440:d={seconds}", "-c:a", "aac", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    """各用例共用的合成素材(只生成一次,用时拷进各自的项目)。"""
    if not HAS_FFMPEG:
        pytest.skip("需要 ffmpeg/ffprobe")
    d = tmp_path_factory.mktemp("caption-media")
    _clip(d / "grp001.mp4", "red", 2.0)
    _clip(d / "grp002.mp4", "blue", 3.0)
    _clip(d / "grp001_big.mp4", "red", 2.0, size="320x180")
    _clip(d / "grp001_silent.mp4", "red", 2.0, audio=False)
    _clip(d / "grp001_other.mp4", "green", 2.0)
    _clip(d / "final.mp4", "gray", 5.0)
    _clip(d / "final_short.mp4", "gray", 4.0)
    # 花字版:a:0 = AAC 预混,a:1 = 存档轨
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(d / "final.mp4"), "-map", "0:v", "-map", "0:a", "-map", "0:a",
                    "-c:v", "copy", "-c:a", "aac", str(d / "final_caption.mp4")], check=True)
    # 中间夹 1 s 黑场(2.0–3.0 s)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=gray:s=160x90:r=24:d=2",
                    "-f", "lavfi", "-i", "color=c=black:s=160x90:r=24:d=1", "-f", "lavfi", "-i", "color=c=gray:s=160x90:r=24:d=2",
                    "-f", "lavfi", "-i", "sine=f=440:d=5", "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
                    "-map", "[v]", "-map", "3:a", "-map", "3:a", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(d / "final_caption_black.mp4")], check=True)
    return d


def _timeline(p: Project):
    tracks = [{"group_id": "grp001", "src": f"assets/clips/{EP}/grp001.mp4", "in": 0.0, "out": 2.0, "timeline_in": 0.0, "timeline_out": 2.0},
              {"group_id": "grp002", "src": f"assets/clips/{EP}/grp002.mp4", "in": 0.0, "out": 3.0, "timeline_in": 2.0, "timeline_out": 5.0}]
    (p.ed / "timeline.json").write_text(json.dumps({"episode": EP, "duration_s": 5.0, "tracks": {"video": tracks, "audio": []}}))


# ---------------------------------------------------------------- 烧录阶段

def _receipt(p: Project, gid: str, **over):
    """按引擎口径写一份新鲜回执:源 sha + 该组花字(画面相关字段)+ 模版 + 字体指纹 + 引擎版本。"""
    caps = [c for c in p.read()["captions"] if c["group_id"] == gid]
    src = p.root / "assets" / "clips" / EP / f"{gid}.mp4"
    rec = {"renderer": chtml.HTML_RENDERER_VERSION, "src_sha256": avsync.file_sha256(str(src)),
           "captions_hash": chtml._captions_hash_v3(caps, p.root, p.fonts()), "src_version": 0}
    rec.update(over)
    (p.cap_dir / f"{gid}.render.json").write_text(json.dumps(rec), encoding="utf-8")


@pytest.fixture()
def rendered(project, media):
    """两组都已「烧录」:副本 = 源的拷贝(规格必然一致),回执新鲜。"""
    for gid in ("grp001", "grp002"):
        src = project.root / "assets" / "clips" / EP / f"{gid}.mp4"
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(media / f"{gid}.mp4", src)
        project.cap_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, project.cap_dir / f"{gid}.mp4")
    _timeline(project)
    for gid in ("grp001", "grp002"):
        _receipt(project, gid)
    return project


RENDER = ("caption_toolchain_verified", "caption_glyph_coverage", "captions_rendered_all", "caption_render_spec_ok",
          "caption_clip_audio_intact")


@needs_ffmpeg
def test_render_all_pass(rendered, run):
    code, checks, skips, out = run(rendered, "--require", "render")
    assert code == 0, out
    assert checks == {name: "PASS" for name in DESIGN + RENDER} and "final" in skips


@needs_ffmpeg
def test_render_stage_is_skipped_before_copies_exist(project, run):
    code, checks, skips, _out = run(project)
    assert code == 0 and "render" in skips and not set(RENDER) & set(checks)
    code, checks, _skips, out = run(project, "--require", "render")      # 要求到位而一个副本都没有
    assert code == 1 and _failed(checks) == ["captions_rendered_all"] and "缺副本" in out


@needs_ffmpeg
def test_receipt_survives_edits_that_do_not_touch_pixels(rendered, run):
    rendered.edit(lambda d: d["captions"][0].update(sfx={"sfx_id": "whoosh_01"}, note="换了音效", type="keyword"))
    code, checks, _skips, out = run(rendered)
    assert code == 0 and checks["captions_rendered_all"] == "PASS", out


@needs_ffmpeg
@pytest.mark.parametrize("change", [dict(text="青云门"), dict(local_end=1.8, end=1.8), dict(position="center"), dict(em_pct=12)])
def test_receipt_goes_stale_when_caption_changes(rendered, run, change):
    rendered.edit(lambda d: d["captions"][0].update(change))
    code, checks, _skips, out = run(rendered)
    assert code == 1 and _failed(checks) == ["captions_rendered_all"] and "回执过期" in out and "grp001" in out
    _receipt(rendered, "grp001")                                         # 重渲(重写回执)后恢复
    assert run(rendered)[0] == 0


@needs_ffmpeg
def test_receipt_goes_stale_when_template_changes(rendered, run):
    (rendered.root / "edit" / "caption_templates" / "title.html").write_text(TEMPLATE + "<!-- 改了动画 -->", encoding="utf-8")
    code, checks, _skips, out = run(rendered)
    assert code == 1 and _failed(checks) == ["captions_rendered_all"] and "grp001" in out and "grp002" in out


@needs_ffmpeg
def test_receipt_goes_stale_when_source_clip_changes(rendered, run, media):
    shutil.copyfile(media / "grp001_other.mp4", rendered.root / "assets" / "clips" / EP / "grp001.mp4")      # 组 clip 重生成过
    code, checks, _skips, out = run(rendered)
    assert code == 1 and "captions_rendered_all" in _failed(checks) and "['grp001']" in out


@needs_ffmpeg
@pytest.mark.parametrize("over", [dict(renderer="h0"), dict(src_version=1), dict(captions_hash="x")])
def test_receipt_fields_must_match(rendered, run, over):
    _receipt(rendered, "grp002", **over)
    code, checks, _skips, out = run(rendered)
    assert code == 1 and _failed(checks) == ["captions_rendered_all"] and "['grp002']" in out


@needs_ffmpeg
def test_missing_copy_or_receipt(rendered, run):
    (rendered.cap_dir / "grp002.mp4").unlink()
    code, checks, _skips, out = run(rendered)
    assert code == 1 and _failed(checks) == ["captions_rendered_all"] and "缺副本:['grp002']" in out
    shutil.copyfile(rendered.root / "assets" / "clips" / EP / "grp002.mp4", rendered.cap_dir / "grp002.mp4")
    (rendered.cap_dir / "grp002.render.json").unlink()
    assert "缺副本:['grp002']" in run(rendered)[3]


@needs_ffmpeg
def test_group_without_captions_needs_no_copy(rendered, run):
    rendered.edit(lambda d: d["captions"].pop())                         # grp002 不再有花字
    (rendered.cap_dir / "grp002.mp4").unlink()
    (rendered.cap_dir / "grp002.render.json").unlink()
    assert run(rendered)[0] == 0


@needs_ffmpeg
def test_copy_with_different_resolution(rendered, run, media):
    shutil.copyfile(media / "grp001_big.mp4", rendered.cap_dir / "grp001.mp4")
    code, checks, _skips, out = run(rendered)
    assert code == 1 and _failed(checks) == ["caption_render_spec_ok"] and "160x90" in out and "320x180" in out


@needs_ffmpeg
def test_copy_with_different_duration(rendered, run, media):
    shutil.copyfile(media / "grp002.mp4", rendered.cap_dir / "grp001.mp4")       # 3 s 的副本顶了 2 s 的组
    assert _failed(run(rendered)[1]) == ["caption_render_spec_ok"]


@needs_ffmpeg
def test_copy_that_lost_its_audio(rendered, run, media):
    shutil.copyfile(media / "grp001_silent.mp4", rendered.cap_dir / "grp001.mp4")
    code, checks, _skips, out = run(rendered)
    assert code == 1 and "caption_clip_audio_intact" in _failed(checks) and "has_audio=True 副本=False" in out


@needs_ffmpeg
def test_glyph_coverage_checked_against_template_font(rendered, run):
    make_font(rendered.root / "refs" / "fonts" / "kai.ttf", "Test Kai", "青云宗")
    (rendered.root / "edit" / "caption_templates" / "title.html").write_text(font_template("proj:TestKai"), encoding="utf-8")
    for gid in ("grp001", "grp002"):
        _receipt(rendered, gid)
    code, checks, _skips, out = run(rendered)
    assert code == 1 and _failed(checks) == ["caption_glyph_coverage"] and "cap002" in out and "cap001:" not in out      # 「藏经阁」缺字形
    make_font(rendered.root / "refs" / "fonts" / "kai.ttf", "Test Kai", "青云宗藏经阁")
    for gid in ("grp001", "grp002"):
        _receipt(rendered, gid)
    assert run(rendered)[0] == 0
    # 模版引用了 manifest 里没有的字体
    (rendered.root / "edit" / "caption_templates" / "title.html").write_text(font_template("user:Nope"), encoding="utf-8")
    code, checks, _skips, out = run(rendered)
    assert code == 1 and "caption_glyph_coverage" in _failed(checks) and "不在 fonts manifest" in out


@needs_ffmpeg
def test_toolchain_failure_is_its_own_check(rendered, run):
    code, checks, _skips, out = run(rendered, chromium=(False, "Chromium 启动失败:未安装"))
    assert code == 1 and _failed(checks) == ["caption_toolchain_verified"] and "未安装" in out


# ---------------------------------------------------------------- 成片阶段

def _final_receipt(p: Project, **over):
    tl = ct.CaptionTimeline.resolve(p.root, EP, audio_used=True)
    items, _dropped = ct.plan_final_items(tl, p.read())
    authority, _kind = tl.audio_authority()
    rec = {"src_sha256": avsync.file_sha256(str(tl.final)), "captions_hash": chtml.episode_items_hash(items, p.root, p.fonts()),
           "renderer": chtml.HTML_RENDERER_VERSION, "audio_sha256": avsync.file_sha256(str(authority)),
           "sfx_plan": ct.sfx_plan_fingerprint(items), "timeline_fingerprint": tl.fingerprint(),
           "out_sha256": avsync.file_sha256(str(p.ed / "final_caption.mp4"))}
    rec.update(over)
    ct.final_receipt_path(p.root, EP).write_text(json.dumps(rec), encoding="utf-8")


@pytest.fixture()
def finished(rendered, media):
    """干净版 final.mp4(无片头,5 s)+ 花字版 final_caption.mp4(双音轨)+ 新鲜回执。"""
    shutil.copyfile(media / "final.mp4", rendered.ed / "cut_v1.mp4")
    shutil.copyfile(media / "final.mp4", rendered.ed / "final.mp4")
    shutil.copyfile(media / "final_caption.mp4", rendered.ed / "final_caption.mp4")
    _final_receipt(rendered)
    return rendered


FINAL = ("caption_final_duration_match", "caption_final_fresh", "caption_premix_present", "no_black_frames")


@needs_ffmpeg
def test_final_all_pass(finished, run):
    code, checks, skips, out = run(finished, "--require", "final")
    assert code == 0, out
    assert checks == {name: "PASS" for name in DESIGN + RENDER + FINAL}
    assert "final_caption_master_frames_intact" in skips                 # 主流程项目不走母带零重编码口径


@needs_ffmpeg
def test_final_required_but_absent(rendered, run):
    code, checks, _skips, out = run(rendered, "--require", "final")
    assert code == 1 and _failed(checks) == ["caption_final_duration_match"] and "final_caption.mp4" in out


@needs_ffmpeg
def test_final_duration_must_match_clean_cut(finished, run, media):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(media / "final_short.mp4"), "-map", "0:v", "-map", "0:a", "-map", "0:a",
                    "-c:v", "copy", "-c:a", "aac", str(finished.ed / "final_caption.mp4")], check=True)
    _final_receipt(finished)
    code, checks, _skips, out = run(finished)
    assert code == 1 and _failed(checks) == ["caption_final_duration_match"] and "ref=5." in out and "(final)" in out


@needs_ffmpeg
def test_final_receipt_missing(finished, run):
    ct.final_receipt_path(finished.root, EP).unlink()
    code, checks, _skips, out = run(finished)
    assert code == 1 and _failed(checks) == ["caption_final_fresh"] and "final_caption.json" in out


@needs_ffmpeg
def test_final_goes_stale_when_captions_change(finished, run):
    finished.edit(lambda d: d["captions"][0].update(text="青云门"))
    _receipt(finished, "grp001")                                         # 组副本重渲了,花字版成片没重出
    code, checks, _skips, out = run(finished)
    assert code == 1 and _failed(checks) == ["caption_final_fresh"] and "captions_hash" in out


@needs_ffmpeg
def test_final_goes_stale_when_sfx_plan_changes(finished, run):
    finished.edit(lambda d: d["captions"][0].update(sfx={"sfx_id": "whoosh_01"}))      # 画面没变,预混轨该重做
    code, checks, _skips, out = run(finished)
    assert code == 1 and _failed(checks) == ["caption_final_fresh"] and "sfx_plan" in out and "captions_hash" not in out


@needs_ffmpeg
def test_final_goes_stale_when_clean_cut_is_rebuilt(finished, run, media):
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=white:s=160x90:r=24:d=5", "-f", "lavfi", "-i", "sine=f=220:d=5",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(finished.ed / "final.mp4")],
                   check=True)
    code, checks, _skips, out = run(finished)
    assert code == 1 and "caption_final_fresh" in _failed(checks) and "src_sha256" in out and "audio_sha256" in out


@needs_ffmpeg
def test_final_goes_stale_when_intro_offset_changes(finished, run):
    (finished.ed / "final_layout.json").write_text(json.dumps({"cut_offset_s": 2.0, "segments": [
        {"name": "cut", "file": f"edit/{EP}/cut_v1.mp4"}]}), encoding="utf-8")
    code, checks, _skips, out = run(finished)
    assert code == 1 and "caption_final_fresh" in _failed(checks) and "timeline_fingerprint" in out and "captions_hash" in out


@needs_ffmpeg
def test_final_output_file_replaced(finished, run, media):
    shutil.copyfile(media / "final_caption_black.mp4", finished.ed / "final_caption.mp4")
    code, checks, _skips, out = run(finished)
    assert code == 1 and _failed(checks) == ["caption_final_fresh", "no_black_frames"] and "out_sha256" in out


@needs_ffmpeg
def test_final_needs_premix_and_archive_tracks(finished, run, media):
    shutil.copyfile(media / "final.mp4", finished.ed / "final_caption.mp4")          # 只有一条音轨
    _final_receipt(finished)
    code, checks, _skips, out = run(finished)
    assert code == 1 and _failed(checks) == ["caption_premix_present"] and "音轨数=1" in out


@needs_ffmpeg
def test_final_black_frames_and_design_whitelist(finished, run, media):
    shutil.copyfile(media / "final_caption_black.mp4", finished.ed / "final_caption.mp4")
    _final_receipt(finished)
    code, checks, _skips, out = run(finished)
    assert code == 1 and _failed(checks) == ["no_black_frames"] and "黑帧段" in out
    # 这段黑场是设计内的(转场台账登记、且台账的 out_cut 就是当前正片)→ 豁免
    ledger = {"src_cut": f"edit/{EP}/cut_v0.mp4", "out_cut": f"edit/{EP}/cut_v1.mp4",
              "black_frame_whitelist": [{"type": "dip_black", "start_s": 2.0, "end_s": 3.0}]}
    (finished.ed / "transitions_render.json").write_text(json.dumps(ledger), encoding="utf-8")
    _final_receipt(finished)
    code, checks, _skips, out = run(finished)
    assert code == 0 and checks["no_black_frames"] == "PASS" and "已豁免" in out, out
    # 台账是别的正片的 → 不豁免
    ledger["out_cut"] = f"edit/{EP}/cut_v9.mp4"
    (finished.ed / "transitions_render.json").write_text(json.dumps(ledger), encoding="utf-8")
    assert "no_black_frames" in _failed(run(finished)[1])


# ---------------------------------------------------------------- 黑帧辅助函数

def test_drop_whitelisted_spans():
    spans = ["2.0-3.0s", "10.0-11.5s", "20.0-20.6s"]
    assert chk._drop_whitelisted(spans, []) == (spans, 0)
    assert chk._drop_whitelisted(spans, [(2.0, 3.0)]) == (["10.0-11.5s", "20.0-20.6s"], 1)
    assert chk._drop_whitelisted(spans, [(2.1, 2.9)]) == (["10.0-11.5s", "20.0-20.6s"], 1)      # 0.15 s 容差
    assert chk._drop_whitelisted(spans, [(2.3, 3.0)])[1] == 0                                    # 黑场比登记的早开始 0.3 s
    assert chk._drop_whitelisted(spans, [(9.0, 12.0), (19.9, 20.7)]) == (["2.0-3.0s"], 2)
    assert chk._drop_whitelisted(spans, [(10.0, 11.0)])[1] == 0                                  # 只盖住一部分不算


class _TL:
    def __init__(self, cut, offset):
        self.cut, self.cut_offset_s = cut, offset


def test_black_whitelist_shifts_by_intro_and_binds_to_cut(tmp_path):
    ed = tmp_path / "edit" / EP
    ed.mkdir(parents=True)
    cut = ed / "cut_v2.mp4"
    assert chk._black_whitelist(tmp_path, EP, _TL(cut, 0.0)) == []                               # 没有台账
    (ed / "transitions_render.json").write_text(json.dumps({"out_cut": f"edit/{EP}/cut_v2.mp4", "black_frame_whitelist": [
        {"start_s": 2.0, "end_s": 3.0}, {"start_s": "x", "end_s": 4.0}, {"end_s": 5.0}]}))
    assert chk._black_whitelist(tmp_path, EP, _TL(cut, 0.0)) == [(2.0, 3.0)]                     # 坏条目跳过
    assert chk._black_whitelist(tmp_path, EP, _TL(cut, 6.5)) == [(8.5, 9.5)]                     # 成片基准 = +片头
    assert chk._black_whitelist(tmp_path, EP, _TL(ed / "cut_v3.mp4", 0.0)) == []                 # 台账不是当前正片的
    assert chk._black_whitelist(tmp_path, EP, None) == []
    (ed / "transitions_render.json").write_text("{broken")
    assert chk._black_whitelist(tmp_path, EP, _TL(cut, 0.0)) == []


@needs_ffmpeg
def test_black_spans_detected(media):
    assert chk._black_spans(media / "final_caption.mp4") == []
    spans = chk._black_spans(media / "final_caption_black.mp4")
    assert len(spans) == 1
    a, b = (float(x) for x in spans[0].rstrip("s").split("-"))
    assert a == pytest.approx(2.0, abs=0.1) and b == pytest.approx(3.0, abs=0.1)
    assert chk._black_spans(media / "final_caption_black.mp4", min_d=1.5) == []                  # 短于阈值的不算
