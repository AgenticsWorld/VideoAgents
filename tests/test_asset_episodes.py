"""资产 → 出现分集反查(modules/asset_episodes.py):名字一次扫描与逐个子串搜索等价、按集缓存只重扫变化的那一集。"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules import asset_episodes as ae  # noqa: E402


def test_names_hit_equals_substring_search():
    names = ['乾坤圈', '乾坤', '坤圈带', '混天绫', '天绫', '火尖枪', '风火轮', '火轮', 'AK47', 'K4']
    for text in ('哪吒祭起乾坤圈带着混天绫', '乾坤圈', '坤圈带', '风火轮AK47', '没有任何道具', '混天', ''):
        want = {n for n in names if n in text}
        assert ae._names_hit(ae._names_pattern(names), text) & set(names) == want, text
    assert ae._names_hit(ae._names_pattern([]), '乾坤圈') == set()


def _project(tmp_path: Path) -> Path:
    base = tmp_path / 'p'
    (base / 'bible').mkdir(parents=True)
    (base / 'bible' / 'props.json').write_text(json.dumps({'props': [
        {'id': 'PROP-0001', 'name': '乾坤圈', 'aliases': ['金圈']},
        {'id': 'PROP-0002', 'name': '混天绫'},
        {'id': 'PROP-00010', 'name': '火尖枪'}]}, ensure_ascii=False), encoding='utf-8')
    for ep, text in (('ep01', '哪吒手持金圈。'), ('ep02', '道具 PROP-00010 出场,另有混天绫。')):
        d = base / 'story' / 'episodes' / ep
        d.mkdir(parents=True)
        (d / 'screenplay.md').write_text(text, encoding='utf-8')
    return base


def test_build_and_per_episode_cache(tmp_path, monkeypatch):
    base = _project(tmp_path)
    res = ae.build(base)
    assert [e['ep'] for e in res['episodes']] == ['ep01', 'ep02']
    # 别名命中 ep01;PROP-00010 不算 PROP-0001 的命中
    assert res['props'] == {'PROP-0001': ['ep01'], 'PROP-0002': ['ep02'], 'PROP-00010': ['ep02']}

    scanned = []
    real = ae._scan_episode
    monkeypatch.setattr(ae, '_scan_episode', lambda cat, fam, files: scanned.append(files[0].parent.name) or real(cat, fam, files))
    assert ae.build(base) == res and scanned == []          # 文件没变:不重扫
    f = base / 'story' / 'episodes' / 'ep01' / 'screenplay.md'
    f.write_text('哪吒手持金圈与混天绫。', encoding='utf-8')
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    res2 = ae.build(base)
    assert scanned == ['ep01']                               # 只重扫变化的那一集
    assert res2['props']['PROP-0002'] == ['ep01', 'ep02']
    # 资产目录变了(新增道具):各集都要重扫
    doc = json.loads((base / 'bible' / 'props.json').read_text(encoding='utf-8'))
    doc['props'].append({'id': 'PROP-0003', 'name': '哪吒'})
    (base / 'bible' / 'props.json').write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    scanned.clear()
    assert ae.build(base)['props']['PROP-0003'] == ['ep01'] and sorted(scanned) == ['ep01', 'ep02']
