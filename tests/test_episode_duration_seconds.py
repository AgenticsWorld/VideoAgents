"""每集时长界面按秒填(2026-11-01):视频节奏弹窗与新建向导「每集时长(秒)」,settings.json 仍存 episode_minutes(分钟);
注入提示词与初始化工单先写秒再注分钟,去掉 50/60*60 这类浮点尾差。"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

INDEX = ROOT / "apps/web/static/index.html"
I18N_DIR = ROOT / "apps/web/static/i18n"
LANGS = ("en", "de", "es", "fr", "pt", "ru", "vi", "id", "ja", "ko", "ar")


def test_fmt_episode_len():
    from services.runtime import core
    assert core._fmt_episode_len(10) == "600 秒(= 10 分钟)"
    assert core._fmt_episode_len(5) == "300 秒(= 5 分钟)"
    assert core._fmt_episode_len(0.75) == "45 秒(= 0.75 分钟)"
    assert core._fmt_episode_len(50 / 60) == "50 秒(≈ 0.83 分钟)"      # 界面填 50 秒 → 存 0.8333…,不出浮点尾差
    assert core._fmt_episode_len(7 / 60) == "7 秒(≈ 0.12 分钟)"
    assert core._fmt_episode_len("1.5") == "90 秒(= 1.5 分钟)"


def _ep_line(core, project):
    return next(ln for ln in core.build_role_prompt("01-story/episode-planner", project).splitlines()
                if ln.startswith("- 每集目标时长:"))


def test_role_prompt_episode_line(tmp_path, monkeypatch):
    from services.runtime import core
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path)
    p = tmp_path / "p"
    p.mkdir()
    sp = p / "settings.json"
    sp.write_text(json.dumps({"duration": {"episode_minutes": 50 / 60}}))
    assert _ep_line(core, "p").startswith("- 每集目标时长:50 秒(≈ 0.83 分钟)—— 剧本分集(episode_plan 每集预算)")
    sp.write_text("{}")                                                  # 缺省 10 分钟
    assert _ep_line(core, "p").startswith("- 每集目标时长:600 秒(= 10 分钟)——")
    sp.write_text(json.dumps({"duration": {"episode_minutes": "auto"}}))
    assert _ep_line(core, "p").startswith("- 每集目标时长:根据剧本自动 —— 不设固定每集预算")


def test_project_init_message_episode_text(tmp_path, monkeypatch):
    from services.runtime import core
    monkeypatch.setattr(core, "PROJECTS_DIR", tmp_path)
    p = tmp_path / "p"
    p.mkdir()
    (p / "settings.json").write_text(json.dumps({"duration": {"episode_minutes": 0.75}}))
    msg = core._project_init_message("p")
    assert "每集约 45 秒(= 0.75 分钟)、" in msg
    (p / "settings.json").write_text(json.dumps({"duration": {"episode_minutes": "auto"}}))
    assert "每集时长根据剧本自动决定" in core._project_init_message("p")


def test_index_inputs_in_seconds():
    s = INDEX.read_text(encoding="utf-8")
    assert "每集时长(分钟)" not in s
    assert '<label>每集时长(秒)</label><input type="number" id="npd-ep" min="1" step="1" value="300">' in s
    assert '<label>每集时长(秒)</label><input type="number" id="dur-ep" min="1" step="1" placeholder="600">' in s
    assert "$('#npd-ep').value=300;" in s                                  # 向导重置默认 5 分钟 = 300 秒
    assert "episode_minutes:$('#npd-ep-auto').checked?'auto':parseFloat($('#npd-ep').value)/60" in s
    assert "episode_minutes:epAuto?'auto':ep/60," in s
    assert "$('#dur-ep').value=auto?600:epMinToSec(d.episode_minutes??10);" in s


@pytest.mark.skipif(not shutil.which("node"), reason="node 不可用")
def test_ep_min_to_sec_node():
    s = INDEX.read_text(encoding="utf-8")
    fn = re.search(r"^function epMinToSec\(m\)\{.*\}$", s, re.M).group(0)
    js = fn + "\nconsole.log(JSON.stringify([epMinToSec(10),epMinToSec(50/60),epMinToSec(0.75),epMinToSec('1.5'),"\
              "epMinToSec(7/60),epMinToSec(undefined),epMinToSec(JSON.parse(JSON.stringify(50/60)))]))"
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == [600, 50, 45, 90, 7, 600, 50]


def test_i18n_label_renamed():
    for lang in LANGS:
        s = (I18N_DIR / f"{lang}.js").read_text(encoding="utf-8")
        assert '"每集时长(分钟)": ' not in s, lang
        m = re.search(r'^"每集时长\(秒\)": "([^"]+)",$', s, re.M)
        assert m, lang
    en = (I18N_DIR / "en.js").read_text(encoding="utf-8")
    assert '"每集时长(秒)": "Episode duration (seconds)",' in en
