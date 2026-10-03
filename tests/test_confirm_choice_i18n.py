# -*- coding: utf-8 -*-
"""确认/签字答复的语言归一(services/runtime/core.py canonical_choice + api_confirm_create/answer):
英文等界面下用户点 Sign off / Hold 回来的答复须归一为「签字」/「暂缓」,Hold 绝不能被当成签字。"""
import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.runtime import core  # noqa: E402


@pytest.mark.parametrize("text,kind,want", [
    ("Hold", "sign", "暂缓"), ("hold", "sign", "暂缓"), (" On Hold ", "sign", "暂缓"),
    ("Sign off", "sign", "签字"), ("Sign", "sign", "签字"), ("签字", "sign", "签字"), ("暂缓", "sign", "暂缓"),
    ("Zurückstellen", "sign", "暂缓"), ("Freigeben", "sign", "签字"),      # de
    ("保留", "sign", "暂缓"), ("承認", "sign", "签字"), ("署名", "sign", "签字"),   # ja(署名=签字确认)
    ("보류", "sign", "暂缓"), ("Hoãn lại", "sign", "暂缓"), ("تأجيل", "sign", "暂缓"),
    ("Rerun", "confirm", "重跑"), ("Skip", "confirm", "跳过"), ("Überspringen", "confirm", "跳过"),
    ("Hold", "confirm", "Hold"),          # 重跑类不认签字词
    ("Rerun", "sign", "Rerun"),           # 签字类不认重跑词
    ("暂缓,先跑其余组", "sign", "暂缓,先跑其余组"),   # 自定义选项原样
    ("", "sign", ""), (None, "sign", ""),
])
def test_canonical_choice(text, kind, want):
    assert core.canonical_choice(text, kind) == want


def test_dictionaries_all_map_sign_and_hold():
    """11 份界面词典里「签字」「暂缓」的译文都能归一回原键(词典改译文时本测试兜底)。"""
    for f in (ROOT / "apps" / "web" / "static" / "i18n").glob("*.js"):
        if f.name == "i18n.js":
            continue
        text = f.read_text(encoding="utf-8")
        for key in ("签字", "暂缓"):
            vals = core._CHOICE_DICT_PATTERN.findall(text)
            got = {k: v for k, v in vals}
            assert key in got, (f.name, key)
            assert core.canonical_choice(got[key], "sign") == key, (f.name, key, got[key])


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_create_normalizes_translated_options_and_answer_hold_is_not_sign(monkeypatch):
    core.CONFIRMS.clear()
    monkeypatch.setattr(core, "notify_user", lambda *a, **k: None)
    r = _run(core.api_confirm_create({"question": "[H3A] please review", "kind": "sign",
                                      "options": ["Sign off", "Hold"], "default": "Sign off"}))
    c = core.CONFIRMS[r["confirm_id"]]
    assert c["options"] == ["签字", "暂缓"] and c["default"] == "签字"
    res = _run(core.api_confirm_answer(c["id"], {"answer": "Hold"}))
    assert res["answer"] == "暂缓" and c["answer"] == "暂缓"
    assert "continuation_attempted" not in c


def test_sign_answer_empty_rejected(monkeypatch):
    core.CONFIRMS.clear()
    monkeypatch.setattr(core, "notify_user", lambda *a, **k: None)
    r = _run(core.api_confirm_create({"question": "[H1] world bible", "kind": "sign"}))
    with pytest.raises(core.ServiceError):
        _run(core.api_confirm_answer(r["confirm_id"], {"answer": ""}))
    assert core.CONFIRMS[r["confirm_id"]]["answer"] is None


def test_confirm_kind_rerun_skip_translated(monkeypatch):
    core.CONFIRMS.clear()
    monkeypatch.setattr(core, "notify_user", lambda *a, **k: None)
    r = _run(core.api_confirm_create({"question": "rerun?", "options": ["Rerun", "Skip"]}))
    c = core.CONFIRMS[r["confirm_id"]]
    assert c["options"] == ["重跑", "跳过"]
    res = _run(core.api_confirm_answer(c["id"], {"answer": "Skip"}))
    assert res["answer"] == "跳过"
    r2 = _run(core.api_confirm_create({"question": "rerun again?"}))
    res2 = _run(core.api_confirm_answer(r2["confirm_id"], {"answer": ""}))   # 重跑类空答复仍落默认
    assert res2["answer"] == core.CONFIRMS[r2["confirm_id"]]["default"]
