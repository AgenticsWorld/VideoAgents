"""grp/sh 编号位数漂移兼容:webui 预览读侧容错 + genmedia 写侧 grpsh_id_3digits 硬校验。

规范三位零填充(grp001/sh001,WORKFLOW.md §7A),但生成 agent 写盘时位数偶发漂移
(grp01.mp4 对 group_id=grp001),读侧按 前缀词+编号数值 容错,写侧提交前直接拒单。
"""
import pytest

from modules import genmedia
from webui import server


def test_num_id_key():
    assert server._num_id_key("grp001") == ("grp", 1)
    assert server._num_id_key("grp01") == ("grp", 1)
    assert server._num_id_key("grp1") == ("grp", 1)
    assert server._num_id_key("grp010") == ("grp", 10)
    assert server._num_id_key("grp01.mp4") == ("grp", 1)
    assert server._num_id_key("sh001") == ("sh", 1)
    assert server._num_id_key("archive") is None
    assert server._num_id_key("") is None


def test_id_name_match_group_top_level_only():
    assert server._id_name_match("grp001", "grp001.mp4")
    assert server._id_name_match("grp001", "grp01.mp4")
    assert server._id_name_match("grp001", "grp1.mp4")
    assert not server._id_name_match("grp001", "grp010.mp4")
    assert not server._id_name_match("grp001", "grp02.mp4")
    # 组级 clips 只认顶层文件,归档子目录不混入
    assert not server._id_name_match("grp001", "archive/grp001_x_raw/pt1.mp4")


def test_id_name_match_shot_any_segment():
    assert server._id_name_match("sh001", "sh001_take2.mp4", any_segment=True)
    assert server._id_name_match("sh001", "archive/sh001_old.mp4", any_segment=True)
    assert server._id_name_match("sh001", "sh01.mp4", any_segment=True)
    assert not server._id_name_match("sh001", "grp001.mp4", any_segment=True)
    assert not server._id_name_match("sh001", "sh010.mp4", any_segment=True)


def test_id_file_fallback(tmp_path):
    (tmp_path / "grp01.meta.json").write_text("{}")
    assert server._id_file(tmp_path, "grp001", ".meta.json").name == "grp01.meta.json"
    exact = tmp_path / "grp002.meta.json"
    exact.write_text("{}")
    (tmp_path / "grp02.meta.json").write_text("{}")
    assert server._id_file(tmp_path, "grp002", ".meta.json") == exact  # 正名优先
    missing = server._id_file(tmp_path, "grp009", ".meta.json")
    assert missing == tmp_path / "grp009.meta.json"  # 找不到按原路径返回


def test_id_dir_fallback(tmp_path):
    (tmp_path / "grp03").mkdir()
    assert server._id_dir(tmp_path, "grp003").name == "grp03"
    exact = tmp_path / "grp004"
    exact.mkdir()
    (tmp_path / "grp04").mkdir()
    assert server._id_dir(tmp_path, "grp004") == exact  # 正名优先
    assert server._id_dir(tmp_path, "grp005") == tmp_path / "grp005"


def test_genmedia_rejects_bad_digits():
    with pytest.raises(RuntimeError, match="grpsh_id_3digits"):
        genmedia._check_id_digits("assets/clips/ep01/grp01.mp4")
    with pytest.raises(RuntimeError, match="grpsh_id_3digits"):
        genmedia._check_id_digits("assets/prompts/ep01/sh01.json")
    with pytest.raises(RuntimeError, match="grpsh_id_3digits"):
        genmedia._check_id_digits("assets/keyframes/ep01/grp01/kf_01.png")  # 目录段同查
    with pytest.raises(RuntimeError, match="grpsh_id_3digits"):
        genmedia._check_id_digits("grp001.mp4", "grp01.last_frame.png")
    with pytest.raises(RuntimeError, match="grpsh_id_3digits"):
        genmedia._check_id_digits("assets/clips/ep01/grp0001.mp4")  # 四位也拒


def test_genmedia_accepts_canonical_and_unrelated():
    genmedia._check_id_digits("assets/clips/ep01/grp001.mp4",
                              "assets/clips/ep01/grp001.last_frame.png")
    genmedia._check_id_digits("assets/keyframes/ep01/grp012/kf_action_01.png")
    genmedia._check_id_digits("designs/characters/CHAR-0001/front.png")
    genmedia._check_id_digits("assets/audio/bgm/ep01/ep01_bgm_01.mp3")
    genmedia._check_id_digits("shared/out.mp4")  # sh 后无紧跟数字,不误伤
    genmedia._check_id_digits("", None)
