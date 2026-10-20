"""Openverse 音效渠道(2026-10-20):modules/openverse.py 的关键词 / 请求 / 令牌,genmedia sfx 选 Openverse 时的
挑素材、截段、循环段与来源记录,以及保存配置时的许可范围 / 来源校验。不联网:urlopen 与检索 / 下载都打桩。"""
import asyncio
import io
import json
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import modules.genmedia as g  # noqa: E402
from modules import openverse as ov  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="需要 ffmpeg / ffprobe")


# ---------------------------------------------------------------- 关键词 / 时长档
def test_keywords_drop_filler_and_cap():
    assert ov.keywords("Heavy wooden door slamming shut in a stone hall") == ["heavy", "wooden", "door", "slamming"]
    assert ov.keywords("the sound of rain on a window, rain sfx") == ["rain", "window"]
    assert ov.keywords("") == []


def test_query_ladder_prefers_explicit_query_and_drops_words_from_the_end():
    assert ov.query_ladder("door slam", "ignored prompt words") == ["door slam", "door"]
    assert ov.query_ladder("", "low wind over snowfield") == ["low wind snowfield", "low wind", "low"]
    assert ov.query_ladder("", "的 声音") == []


def test_length_buckets():
    assert ov.length_buckets(0, 29) == "shortest"
    assert ov.length_buckets(10.5, 600) == "shortest,short,medium"
    assert ov.length_buckets(45, 600) == "short,medium"
    assert ov.length_buckets(0, None) == ""


# ---------------------------------------------------------------- HTTP
class _Resp(io.BytesIO):
    def __init__(self, data: bytes, headers=None):
        super().__init__(data)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _http_error(url, code, body=b"{}"):
    return urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(body))


@pytest.fixture
def token_path(tmp_path, monkeypatch):
    path = tmp_path / "openverse_token.json"
    monkeypatch.setattr(ov, "TOKEN_PATH", path)
    return path


def test_search_sends_json_accept_and_filters(monkeypatch, token_path):
    seen = {}
    payload = {"result_count": 2, "results": [
        {"id": "a1", "title": "Door Slam", "url": "https://cdn.example/a1.mp3", "duration": 1295,
         "license": "cc0", "license_version": "1.0", "source": "freesound", "filetype": "mp3",
         "foreign_landing_url": "https://freesound.org/s/1/", "tags": [{"name": "door"}, {"name": "slam"}]},
        {"id": "no-url", "title": "x", "url": ""}]}

    def fake_urlopen(req, timeout=None):
        seen["url"], seen["headers"] = req.full_url, dict(req.header_items())
        return _Resp(json.dumps(payload).encode(), {"X-RateLimit-Limit-anon_burst": "20/min",
                                                    "X-RateLimit-Available-anon_burst": "19",
                                                    "X-RateLimit-Available-anon_sustained": "199"})
    monkeypatch.setattr(ov.urllib.request, "urlopen", fake_urlopen)
    res = ov.search("door   slam", length="shortest")
    assert "q=door+slam" in seen["url"] and "license=cc0%2Cpdm" in seen["url"]
    assert "source=freesound" in seen["url"] and "length=shortest" in seen["url"] and "page_size=20" in seen["url"]
    assert seen["headers"]["Accept"] == "application/json" and "Authorization" not in seen["headers"]
    assert [r["id"] for r in res["results"]] == ["a1"]          # 没有音频地址的条目不要
    hit = res["results"][0]
    assert hit["duration_s"] == 1.295 and hit["license_label"] == "CC0 1.0" and hit["source_label"] == "Freesound"
    assert hit["landing_url"] == "https://freesound.org/s/1/" and hit["tags"] == ["door", "slam"]
    assert res["rate"] == {"burst": {"limit": "20/min", "available": "19"}, "sustained": {"available": "199"}}


@pytest.mark.parametrize("kwargs, message", [
    ({"licenses": "by-sa"}, "许可范围无效"),
    ({"sources": "jamendo"}, "来源无效"),
    ({"client_id": "only-id"}, "须同时填写"),
])
def test_search_rejects_bad_settings_before_request(monkeypatch, token_path, kwargs, message):
    monkeypatch.setattr(ov.urllib.request, "urlopen", lambda *a, **k: pytest.fail("不该发请求"))
    with pytest.raises(ov.OpenverseError, match=message) as ei:
        ov.search("door", **kwargs)
    assert ei.value.status == 400


def test_html_and_rate_limit_are_reported(monkeypatch, token_path):
    monkeypatch.setattr(ov.urllib.request, "urlopen", lambda req, timeout=None: _Resp(b"<!DOCTYPE html>"))
    with pytest.raises(ov.OpenverseError, match="没有返回 JSON"):
        ov.search("door")

    def limited(req, timeout=None):
        raise _http_error(req.full_url, 429)
    monkeypatch.setattr(ov.urllib.request, "urlopen", limited)
    with pytest.raises(ov.OpenverseError, match="限流") as ei:
        ov.search("door")
    assert ei.value.status == 429


def test_transport_failure_is_retried_once(monkeypatch, token_path):
    calls = []

    def flaky(req, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("The read operation timed out")
        return _Resp(json.dumps({"result_count": 0, "results": []}).encode())
    monkeypatch.setattr(ov.time, "sleep", lambda s: None)
    monkeypatch.setattr(ov.urllib.request, "urlopen", flaky)
    assert ov.search("door")["result_count"] == 0 and len(calls) == 2

    def down(req, timeout=None):
        calls.append(1)
        raise OSError("network unreachable")
    calls.clear()
    monkeypatch.setattr(ov.urllib.request, "urlopen", down)
    with pytest.raises(ov.OpenverseError, match="连不上 Openverse") as ei:
        ov.search("door")
    assert ei.value.status == 0 and len(calls) == 2


def test_token_is_cached_and_refreshed_once_on_401(monkeypatch, token_path):
    calls = []

    def fake_urlopen(req, timeout=None):
        if req.full_url.endswith("/auth_tokens/token/"):
            calls.append(("token", req.data.decode()))
            return _Resp(json.dumps({"access_token": f"tok{len(calls)}", "expires_in": 3600}).encode())
        auth = dict(req.header_items()).get("Authorization")
        calls.append(("search", auth))
        if auth == "Bearer tok1":
            raise _http_error(req.full_url, 401)
        return _Resp(json.dumps({"result_count": 0, "results": []}).encode())
    monkeypatch.setattr(ov.urllib.request, "urlopen", fake_urlopen)
    ov.search("door", client_id="cid", client_secret="sec")
    assert calls[0][0] == "token" and "grant_type=client_credentials" in calls[0][1] and "client_id=cid" in calls[0][1]
    assert [c for c in calls if c[0] == "search"] == [("search", "Bearer tok1"), ("search", "Bearer tok3")]
    cached = json.loads(token_path.read_text())
    assert cached["token"] == "tok3"
    calls.clear()
    ov.search("door", client_id="cid", client_secret="sec")
    assert calls == [("search", "Bearer tok3")]                  # 缓存命中,不再申请令牌


def test_download_stops_at_size_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(ov.urllib.request, "urlopen", lambda req, timeout=None: _Resp(b"x" * 5000))
    dest = tmp_path / "a.mp3"
    assert ov.download("https://cdn.example/a.mp3", dest) == 5000 and dest.stat().st_size == 5000
    with pytest.raises(ov.OpenverseError, match="MB"):
        ov.download("https://cdn.example/a.mp3", dest, max_bytes=1000)
    assert not dest.exists()


# ---------------------------------------------------------------- genmedia sfx(Openverse)
@pytest.fixture
def config(tmp_path, monkeypatch):
    path = tmp_path / "genconfig.json"
    monkeypatch.setattr(g, "CONFIG_PATH", path)
    monkeypatch.delenv("VIDEOAGENTS_AGENT", raising=False)

    def write(data):
        path.write_text(json.dumps(data), encoding="utf-8")
    write({"sfx": {"provider": "openverse", "openverse": {"license": "cc0,pdm,by", "source": "freesound"}}})
    return write


def _hit(id_, duration_s, license_="cc0", title=None, filetype="wav"):
    return {"id": id_, "title": title or id_, "url": f"https://cdn.example/{id_}.{filetype}", "duration_s": duration_s,
            "license": license_, "license_version": "4.0" if license_ == "by" else "1.0",
            "license_label": ov.license_label(license_, "4.0" if license_ == "by" else "1.0"),
            "license_url": "https://creativecommons.org/", "creator": "someone", "creator_url": "",
            "source": "freesound", "source_label": "Freesound", "filetype": filetype, "filesize": 1000,
            "landing_url": f"https://freesound.org/s/{id_}/", "attribution": f'"{id_}" by someone', "tags": []}


def _wav(path: Path, graph: str):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", graph, str(path)], check=True)


@pytest.fixture
def fake_openverse(monkeypatch):
    """检索按 {关键词: [条目]} 返回;下载按 {条目 id: lavfi 图} 现合成 wav。"""
    state = {"queries": [], "downloads": [], "results": {}, "audio": {}}

    def search(q, licenses, sources, length, client_id, client_secret):
        state["queries"].append((q, licenses, sources, length))
        hits = state["results"].get(q, [])
        return {"query": q, "result_count": len(hits), "results": hits, "rate": {}}

    def download(url, dest):
        id_ = url.rsplit("/", 1)[-1].split(".")[0]
        state["downloads"].append(id_)
        if id_ not in state["audio"]:
            raise ov.OpenverseError("HTTP 404:gone", 404)
        _wav(Path(dest).with_suffix(".tmp.wav"), state["audio"][id_])
        Path(dest).with_suffix(".tmp.wav").replace(dest)
        return Path(dest).stat().st_size
    monkeypatch.setattr(ov, "search", search)
    monkeypatch.setattr(ov, "download", download)
    return state


@needs_ffmpeg
def test_oneshot_ladders_query_skips_dead_link_trims_and_records_license(config, fake_openverse, tmp_path):
    tone = "anullsrc=r=48000:cl=mono:d=0.3[a];sine=f=440:r=48000:d=3,volume=0.1[b];[a][b]concat=n=2:v=0:a=1"
    fake_openverse["results"]["door"] = [_hit("long", 40), _hit("dead", 1.2), _hit("good", 3.3, "by")]
    fake_openverse["audio"]["good"] = tone
    out = tmp_path / "sh010_door.wav"
    saved = g.generate_sfx("a heavy door slams", str(out), duration_s=1.5, query="door slam")
    assert saved == [str(out)]
    assert [q[0] for q in fake_openverse["queries"]] == ["door slam", "door"]       # 0 条就减词重搜
    assert fake_openverse["queries"][0][1:] == ("cc0,pdm,by", "freesound", "shortest")
    assert fake_openverse["downloads"] == ["dead", "good"]          # 40 s 那条排到最后,坏链换下一条
    meta = json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))["sfx"]
    assert meta["provider"] == "openverse" and meta["query"] == "door"
    assert meta["license_source"] == "Openverse · Freesound · CC BY 4.0 · https://freesound.org/s/good/"
    assert meta["attribution_required"] is True and meta["openverse"]["attribution"] == '"good" by someone'
    assert meta["openverse"]["original_url"] == "https://cdn.example/good.wav"
    audio = meta["audio"]
    assert audio["postprocessed"] is True and audio["trimmed_s"]["leading"] == pytest.approx(0.3, abs=0.06)
    assert audio["after"]["duration_s"] == pytest.approx(1.5, abs=0.05)            # 截到 --duration
    assert audio["after"]["peak_db"] == pytest.approx(g.SFX_PEAK_DBFS, abs=0.6)
    assert not list(tmp_path.glob(".*.src*"))


@needs_ffmpeg
def test_loop_bed_is_seamless_and_peak_normalized(config, fake_openverse, tmp_path):
    # 441.3 Hz 正弦 × 2 s 不是整周期:直接截 2 s 首尾会跳变,交叉淡化后末样本应接上首样本
    fake_openverse["results"]["forest wind"] = [_hit("short", 2.2), _hit("huge", 500), _hit("bed", 6.0)]
    fake_openverse["audio"]["bed"] = "sine=f=441.3:r=48000:d=6,volume=0.5"
    out = tmp_path / "SCN-0001_bed.wav"
    g.generate_sfx("wind through a pine forest", str(out), duration_s=2.0, loop=True, query="forest wind")
    assert fake_openverse["queries"][0][3] == "shortest,short,medium"
    assert fake_openverse["downloads"] == ["bed"]       # 2.2 s 不够 2 s + 0.5 s 交叉淡化;500 s 的长录音排在够用的短素材后面
    meta = json.loads(out.with_suffix(".meta.json").read_text(encoding="utf-8"))["sfx"]
    assert meta["loop"] is True and meta["attribution_required"] is False
    assert meta["audio"]["loop_cut"] == {"start_s": 0.0, "length_s": 2.0, "crossfade_s": 0.5}
    assert meta["audio"]["after"]["duration_s"] == pytest.approx(2.0, abs=0.01)
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-f", "f32le", "-ac", "1", "-"],
                         capture_output=True, check=True).stdout
    import array
    y = array.array("f", pcm)
    step = max(abs(y[i + 1] - y[i]) for i in range(1000, 2000))      # 正常相邻样本差
    assert abs(y[0] - y[-1]) < 2 * step
    assert meta["audio"]["after"]["peak_db"] == pytest.approx(g.SFX_PEAK_DBFS, abs=0.6)


@needs_ffmpeg
def test_count_takes_distinct_items_and_reports_shortfall(config, fake_openverse, tmp_path, capsys):
    fake_openverse["results"]["sword clash"] = [_hit("s1", 1.0), _hit("s2", 1.0)]
    fake_openverse["audio"].update(s1="sine=f=600:r=48000:d=1", s2="sine=f=700:r=48000:d=1")
    saved = g.generate_sfx("sword clash", str(tmp_path / "hit.wav"), count=3)
    assert saved == [str(tmp_path / "hit_1.wav"), str(tmp_path / "hit_2.wav")]
    assert "只取到 2/3 条" in capsys.readouterr().err


@needs_ffmpeg
def test_nothing_found_explains_and_suggests_keywords(config, fake_openverse, tmp_path):
    with pytest.raises(RuntimeError, match="没找到合用的素材.*「door slam」0/0.*--query"):
        g.generate_sfx("door slam", str(tmp_path / "x.wav"))
    with pytest.raises(RuntimeError, match="英文关键词"):
        g.generate_sfx("门响", str(tmp_path / "x.wav"))


def test_dry_run_prints_ladder_without_network(config, monkeypatch, capsys):
    monkeypatch.setattr(ov, "search", lambda *a, **k: pytest.fail("dry-run 不该检索"))
    monkeypatch.setattr(sys, "argv", ["genmedia.py", "sfx", "--prompt", "rain on a tin roof", "--output", "rain.wav",
                                      "--dry-run"])
    g.main()
    out = capsys.readouterr().out
    assert "sfx via openverse license=cc0,pdm,by source=freesound auth=anonymous" in out
    assert "queries=rain tin roof | rain tin | rain" in out


# ---------------------------------------------------------------- 保存配置
def test_genconfig_save_validates_openverse_settings(tmp_path, monkeypatch):
    from services.runtime import core
    monkeypatch.setattr(core, "GENCONFIG_PATH", tmp_path / "genconfig.json")
    monkeypatch.setattr(core, "save_state", lambda *a, **k: None)

    async def no_refresh(cfg):
        return []
    monkeypatch.setattr(core, "_refresh_rh_wf_caches", no_refresh)
    cfg = core.load_genconfig()["sfx"]
    assert cfg["provider"] == "code" and cfg["openverse"]["license"] == "cc0,pdm"     # 新装默认代码音效(2026-10-20)
    saved = asyncio.run(core.api_genconfig_set({"sfx": {"provider": "fal", "openverse": {"license": "cc0,pdm,by"}}}))
    assert saved["config"]["sfx"]["provider"] == "fal" and saved["config"]["sfx"]["openverse"]["license"] == "cc0,pdm,by"
    for body in ({"sfx": {"provider": "freesound"}}, {"sfx": {"openverse": {"license": "by-sa"}}},
                 {"sfx": {"openverse": {"source": "jamendo"}}}):
        with pytest.raises(core.ServiceError) as ei:
            asyncio.run(core.api_genconfig_set(body))
        assert ei.value.status_code == 400
