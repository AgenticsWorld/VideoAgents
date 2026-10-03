"""镜次时长契约(modules/shot_timing.py)纯函数测试:切段、标签写入/剔除幂等、四种模型的机检。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules import shot_timing as st  # noqa: E402


def test_plan_segments_rounds_cumulative_boundaries_and_merges_subsecond_shots():
    assert st.plan_segments([13], 13) == [(0, 13, [0])]
    assert st.plan_segments([1.5, 2.8, 2.4, 1.3], 8) == [(0, 2, [0]), (2, 4, [1]), (4, 7, [2]), (7, 8, [3])]
    segs = st.plan_segments([1, 0.5, 0.6, 0.5, 0.7, 0.9, 1, 2.3, 0.8, 0.7], 9)
    assert segs[0] == (0, 1, [0]) and segs[-1][1] == 9
    assert [s for s, _, _ in segs][1:] == [e for _, e, _ in segs][:-1]  # 首尾相接
    assert sorted(i for _, _, ids in segs for i in ids) == list(range(10))     # 每镜恰在一段
    assert all(e - s >= 1 for s, e, _ in segs)
    assert st.plan_segments([], 5) == [] and st.plan_segments([0.4, 0.3], 0) == []
    # 尾部不足 1s 的镜并入末段,末段仍止于 total
    assert st.plan_segments([2, 2, 0.3], 4) == [(0, 2, [0]), (2, 4, [1, 2])]


def test_tags_and_cut_times():
    assert st.segment_tag(0, 2, 1, 1, True) == '0-2秒：'
    assert st.segment_tag(8, 10, 5, 8, True) == '8-10秒（Shot 5–Shot 8）：'
    assert st.segment_tag(0, 2, 1, 1, False) == '0-2s:'
    assert st.segment_tag(8, 10, 5, 8, False) == '8-10s (Shot 5–Shot 8):'
    assert st.cut_times([1.5, 5.2, 3.3]) == [0, 1.5, 6.7]
    assert st.cut_tag(6.7) == 'At 00:06.700,' and st.cut_tag(65.25) == 'At 01:05.250,'


SD25 = ('Overall visual style: x.\n\n【人物】李艮@Image 1。\n\n'
        'Shot 1:场景激活：使用场景A（[Image 6]）。李艮出水,镜头 8s 后上仰。使用：李艮。不采用：无。\n\n'
        'Shot 2:场景激活：使用场景B（[Image 7]）。哪吒回头。使用：哪吒。不采用：无。\n\n'
        'Shot 3:场景激活：使用场景B。快切。使用：哪吒。不采用：无。\n\n'
        'Shot 4:场景激活：使用场景C。收束。使用：李艮。不采用：无。\n\n'
        '【未采用素材】无。\n\n【保持一致】人物不变。\n\nGlobal constraints: no text.')


def test_apply_segments_zh_is_idempotent_and_passes_check():
    durs, total = [1.5, 2.8, 0.4, 0.3], 5
    out = st.apply_prompt(SD25, durs, total, st.KIND_SD25, True)
    assert 'Shot 1: 0-2秒：场景激活' in out and 'Shot 2: 2-4秒：场景激活' in out
    assert 'Shot 3: 4-5秒（Shot 3–Shot 4）：场景激活' in out            # 0.4s+0.3s 两镜并成 1 秒段
    assert 'Shot 4:场景激活' in out                                    # 段内其余镜不带标签
    assert '镜头 8s 后上仰' in out                                    # 散文里的秒数不被当标签
    assert st.apply_prompt(out, durs, total, st.KIND_SD25, True) == out
    assert st.strip_tags(out, False) == SD25
    assert st.check_prompt(out, durs, total, st.KIND_SD25, 'g') == ([], [])
    assert st.check_prompt(out, durs, total, st.KIND_WAN3, 'g') == ([], [])
    # 英文界面
    en = st.apply_prompt(SD25, durs, total, st.KIND_WAN3, False)
    assert 'Shot 1: 0-2s: 场景激活' in en and 'Shot 3: 4-5s (Shot 3–Shot 4): 场景激活' in en
    assert st.check_prompt(en, durs, total, st.KIND_SD25, 'g')[0] == []


def test_check_segments_reports_missing_gaps_and_wrong_lengths():
    durs, total = [1.5, 2.8, 0.4, 0.3], 5
    errs, _ = st.check_prompt(SD25, durs, total, st.KIND_SD25, 'g')
    assert len(errs) == 1 and '无时间段标签' in errs[0] and '0-2秒' in errs[0]
    bad = SD25.replace('Shot 1:', 'Shot 1: 0-3秒：').replace('Shot 2:', 'Shot 2: 3-5秒（Shot 2–Shot 4）：')
    errs, _ = st.check_prompt(bad, durs, total, st.KIND_SD25, 'g')
    assert len(errs) == 2 and 'Shot 1 段 0-3(3s)' in errs[0] and 'Shot 2 段 3-5(2s)' in errs[1]   # 连续但各段长度与镜时长不符
    gap = SD25.replace('Shot 1:', 'Shot 1: 0-2秒：').replace('Shot 2:', 'Shot 2: 3-5秒（Shot 2–Shot 4）：')
    errs, _ = st.check_prompt(gap, durs, total, st.KIND_SD25, 'g')
    assert any('不相接' in e for e in errs)
    short = SD25.replace('Shot 1:', 'Shot 1: 0-2秒：').replace('Shot 2:', 'Shot 2: 2-4秒（Shot 2–Shot 4）：')
    errs, _ = st.check_prompt(short, durs, total, st.KIND_SD25, 'g')
    assert any('未止于组总时长 5 秒' in e for e in errs)
    nomerge = SD25.replace('Shot 1:', 'Shot 1: 0-2秒：').replace('Shot 2:', 'Shot 2: 2-5秒：')
    errs, _ = st.check_prompt(nomerge, durs, total, st.KIND_SD25, 'g')
    assert any('未标合并范围' in e for e in errs)
    # 组 total 与 Σ duration_s 不一致 = shot-planning 数据问题
    errs, _ = st.check_prompt(st.apply_prompt(SD25, durs, 5, st.KIND_SD25, True), durs, 7, st.KIND_SD25, 'g')
    assert any('total_duration_s=7' in e for e in errs)
    # Shot 段数与镜数不符
    errs, _ = st.check_prompt(SD25, [1, 1, 1], 3, st.KIND_SD25, 'g')
    assert errs and 'Shot 段数 4 ≠ 组内镜数 3' in errs[0]


H3 = ('subject_definitions:\n<Subject 1> is 李艮 in <Picture 1>.\n\nsummary: two shots.\n\n'
      'retention_analysis:\n<Picture 2> (composition anchor of [Shot 2]): partially_preserved.\n\n'
      'detailed_description:\n[Shot 1] 固定机位大全景,李艮走远。\n\n'
      '[Shot 2] 画面硬切到中景空镜,构图对齐 <Picture 2>。\n\n'
      '[Shot 3] 特写。\n\noverall_soundscape: wind.\n\nnon_diegetic_music: none.')


def test_apply_h3_cut_times_skip_first_shot_and_use_cumulative_durations():
    durs, total = [6, 4.5, 2.5], 13
    out = st.apply_prompt(H3, durs, total, st.KIND_H3, True)
    assert '[Shot 1] 固定机位' in out
    assert '[Shot 2] At 00:06.000, 画面硬切' in out
    assert '[Shot 3] At 00:10.500, 特写' in out
    assert 'anchor of [Shot 2]): partially' in out           # retention_analysis 里的提及不被当段头
    assert st.apply_prompt(out, durs, total, st.KIND_H3, True) == out
    assert st.strip_tags(out, True) == H3
    assert st.check_prompt(out, durs, total, st.KIND_H3, 'g') == ([], [])
    errs, _ = st.check_prompt(H3, durs, total, st.KIND_H3, 'g')
    assert len(errs) == 2 and 'Shot 2 缺 H3 切点' in errs[0] and 'At 00:06.000,' in errs[0]
    wrong = out.replace('At 00:10.500,', 'At 00:09.000,')
    errs, _ = st.check_prompt(wrong, durs, total, st.KIND_H3, 'g')
    assert len(errs) == 1 and '≠ 前 2 镜累计 10.500s' in errs[0]
    first = out.replace('[Shot 1] ', '[Shot 1] At 00:00.000, ')
    assert any('首镜 Shot 1 不写' in e for e in st.check_prompt(first, durs, total, st.KIND_H3, 'g')[0])


def test_sd20_strips_tags_and_flags_them():
    durs, total = [1.5, 2.8, 0.4, 0.3], 5
    tagged = st.apply_prompt(SD25, durs, total, st.KIND_SD25, True)
    errs, _ = st.check_prompt(tagged, durs, total, st.KIND_SD20, 'g')
    assert len(errs) == 1 and 'Seedance 2.0' in errs[0] and '3 处' in errs[0]
    assert st.apply_prompt(tagged, durs, total, st.KIND_SD20, True) == SD25
    assert st.check_prompt(SD25, durs, total, st.KIND_SD20, 'g') == ([], [])
    assert st.check_prompt(SD25, durs, total, '', 'g') == ([], [])        # 未知模型不核


def test_model_kind_detection():
    assert st.model_kind('doubao-seedance-2-5-260628') == st.KIND_SD25
    assert st.model_kind('08-video-gen/prompt/sd25-pe') == st.KIND_SD25
    assert st.model_kind('video-seedance2_5_r2v-cloud-only') == st.KIND_SD25
    assert st.model_kind('doubao-seedance-2-0-260128') == st.KIND_SD20
    assert st.model_kind('08-video-gen/prompt/sd20-pe') == st.KIND_SD20
    assert st.model_kind('MiniMax-Hailuo-H3') == st.KIND_H3 and st.model_kind('08-video-gen/prompt/h3-pe') == st.KIND_H3
    assert st.model_kind('alibaba/wan-3.0/reference-to-video') == st.KIND_WAN3
    assert st.model_kind('08-video-gen/prompt/wan3-pe') == st.KIND_WAN3
    assert st.model_kind('kling-v2') == '' and st.model_kind('') == ''
