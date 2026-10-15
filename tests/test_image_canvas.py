"""画板(modules/image_canvas.py,docs/image_canvas.md):历史版本 / 外部更新 / 圈选蒙版与标注图 / 圈外锁定 / 提示词拼装 /
编辑与放大任务 / 设为最终版(固定路径、草图台账、分镜背景图登记 _revN 并改指)/ 影响清单 / 尺寸约束与保真超分请求体。不联网、不出图。"""
import asyncio
import hashlib
import io
import json
import sys
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageStat

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules import genmedia, image_canvas as ic  # noqa: E402
from modules import shot_plates as sp  # noqa: E402

SHEET = 'assets/concepts/characters/CHAR-1/sheet.png'
QUIET = lambda *_: None   # noqa: E731


def jpeg(size=(320, 180), color='gray') -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, 'JPEG', quality=95)
    return buf.getvalue()


def put(base: Path, rel: str, data: bytes) -> Path:
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


@pytest.fixture
def base(tmp_path):
    put(tmp_path, SHEET, jpeg())
    return tmp_path


@pytest.fixture
def channel(monkeypatch):
    """图像渠道事实打桩:不读本机生成模型配置。返回可改的 dict(limits / ref_capacity)。"""
    facts = {'provider': 'test', 'model': 'm1', 'limits': {'mode': 'unknown'}, 'ref_capacity': None}
    monkeypatch.setattr(ic, 'channel_facts', lambda provider, model: dict(facts))
    return facts


def mean_diff(a: Image.Image, b: Image.Image, box=None) -> float:
    d = ImageChops.difference(a.convert('RGB'), b.convert('RGB'))
    return sum(ImageStat.Stat(d.crop(box) if box else d).mean) / 3


# ---------------------------------------------------------------- 定位
def test_locate_storage_by_path_and_kind_refinement():
    assert ic.locate('assets/concepts/scenes/SCN-1/plates/L1_t3.png') == {'storage': 'plate', 'kind': 'plate', 'sid': 'SCN-1', 'key': 'L1_t3', 'id': 'SCN-1'}
    assert ic.locate('assets/concepts/scenes/SCN-1/plates/L1_t3.whitebox.jpg')['storage'] == 'fixed'     # 白模帧 / 全景截图不是库图
    assert ic.locate('assets/concepts/scenes/SCN-1/layout_top.png') == {'storage': 'fixed', 'kind': 'scene', 'id': 'SCN-1'}
    assert ic.locate('assets/storyboard/ep01/S01-03.png') == {'storage': 'sketch', 'kind': 'sketch', 'ep': 'ep01', 'key': 'S01-03'}
    assert ic.locate('assets/storyboard/ep01/_grids/g1.png')['storage'] == 'fixed'                        # 宫格整图不是某一镜的草图
    assert ic.locate('edit/ep02/thumbnail_main.png') == {'storage': 'fixed', 'kind': 'cover', 'ep': 'ep02'}
    assert ic.locate('edit/ep02/covers/youtube_thumb_v2.jpg')['kind'] == 'cover' and ic.locate('edit/ep02/final.png')['kind'] == 'image'
    assert ic.locate(SHEET)['kind'] == 'character' and ic.locate(SHEET, 'costume')['kind'] == 'costume'
    assert ic.locate('assets/concepts/props/P1/main_01.png', 'costume')['kind'] == 'prop'                # 页面传的类别只细化人物图
    assert ic.locate('refs/style/a.png', 'plate')['kind'] == 'image'                                     # storage 相关的类别不认页面说法


def test_safe_rel_rejects_escape_history_and_non_images(base):
    assert ic.safe_rel(base, '/' + SHEET) == SHEET
    put(base, 'brief.md', b'x')
    put(base, 'assets/canvas/x/v000.png', jpeg())
    for bad in ('../outside.png', 'brief.md', 'assets/canvas/x/v000.png', '', 'assets/concepts/characters/CHAR-1/nope.png'):
        with pytest.raises(ic.CanvasError):
            ic.safe_rel(base, bad)


# ---------------------------------------------------------------- 历史
def test_open_keeps_original_then_imports_external_change(base):
    doc, h = ic.open_doc(base, SHEET)
    assert doc.parent == base / 'assets/canvas' and [v['op'] for v in h['versions']] == ['original']
    v0 = h['versions'][0]
    assert (v0['width'], v0['height'], v0['encoding']) == (320, 180, 'JPEG') and h['final'] == h['disk'] == 'v000'
    assert (doc / 'v000.png').read_bytes() == (base / SHEET).read_bytes() and (doc / v0['thumb']).is_file()
    assert ic.open_doc(base, SHEET)[1]['versions'] == h['versions']                  # 没变就不新增
    put(base, SHEET, jpeg(color='red'))                                              # 别处重出了这张图
    _, h2 = ic.open_doc(base, SHEET)
    assert [(v['id'], v['op'], v['parent']) for v in h2['versions']] == [('v000', 'original', None), ('v001', 'external', 'v000')]
    assert h2['final'] == h2['disk'] == 'v001'
    put(base, SHEET, (doc / 'v000.png').read_bytes())                                # 改回与某一版相同的内容:认回那一版
    assert ic.open_doc(base, SHEET)[1]['final'] == 'v000'
    with pytest.raises(ic.CanvasError):
        ic.open_doc(base, put(base, 'refs/style/a.png', jpeg()).relative_to(base).as_posix(), create=False)


def test_transform_makes_versions_and_keeps_jpeg_bytes(base):
    doc, _ = ic.open_doc(base, SHEET)
    crop = ic.transform(base, doc, 'v000', 'crop', {'x': .25, 'y': .5, 'w': .5, 'h': .5})
    assert (crop['width'], crop['height'], crop['parent'], crop['op'], crop['encoding']) == (160, 90, 'v000', 'transform', 'JPEG')
    rot = ic.transform(base, doc, crop['id'], 'rotate', {'deg': 90})
    assert (rot['width'], rot['height'], rot['parent']) == (90, 160, crop['id'])
    big = ic.transform(base, doc, 'v000', 'resize', {'width': 640, 'height': 360})
    assert (big['width'], big['height']) == (640, 360) and ic.transform(base, doc, 'v000', 'flip_h')['transform'] == {'op': 'flip_h'}
    assert (base / SHEET).read_bytes() == (doc / 'v000.png').read_bytes()            # 没设为最终版,原图不动
    for op, params in (('crop', {'x': 0, 'y': 0, 'w': 1, 'h': 1}), ('crop', {'x': 0, 'y': 0, 'w': .01, 'h': .01}),
                       ('rotate', {'deg': 45}), ('resize', {'width': 320, 'height': 180}), ('sharpen', {})):
        with pytest.raises(ic.CanvasError):
            ic.transform(base, doc, 'v000', op, params)


def test_png_with_alpha_stays_png_and_big_plain_png_becomes_jpeg():
    rgba = Image.new('RGBA', (64, 64), (10, 20, 30, 120))
    assert ic.encoding_of(ic.encode_image(rgba, 'JPEG')) == 'PNG'
    assert ic.encoding_of(ic.encode_image(Image.new('RGB', (64, 64), 'white'), 'PNG')) == 'PNG'
    noisy = Image.frombytes('RGB', (1400, 1000), bytes(hashlib.sha256(str(i).encode()).digest()[0] for i in range(1400 * 1000 * 3)))
    assert ic.encoding_of(ic.encode_image(noisy, 'PNG')) == 'JPEG'                   # 无透明的大 PNG 改存 JPEG 字节


# ---------------------------------------------------------------- 圈选
REGIONS = [{'shape': 'rect', 'x': .1, 'y': .1, 'w': .3, 'h': .4, 'text': '换成红灯笼'},
           {'shape': 'ellipse', 'x': .6, 'y': .5, 'w': .3, 'h': .4},
           {'shape': 'lasso', 'points': [[.5, .05], [.6, .05], [.55, .2]], 'text': 'x'},
           {'shape': 'brush', 'points': [[.05, .9], [.3, .9]], 'width': .06}]


def test_norm_regions_validates_and_numbers():
    rs = ic.norm_regions(REGIONS + [{'shape': 'rect', 'x': .5, 'y': .5, 'w': .001, 'h': .2}, {'shape': 'star'}, 'junk',
                                    {'shape': 'lasso', 'points': [[0, 0], [1, 1]]}])
    assert [(r['n'], r['shape']) for r in rs] == [(1, 'rect'), (2, 'ellipse'), (3, 'lasso'), (4, 'brush')]
    assert ic.norm_regions([{'shape': 'rect', 'x': .9, 'y': .9, 'w': .5, 'h': .5}])[0]['w'] == pytest.approx(.1)   # 收进画面
    assert ic.region_where(rs[0]) == 'upper left of the frame, about x 10–40%, y 10–50%'
    assert ic.region_where(rs[1]).startswith('lower right of the frame') and ic.region_bbox(rs[3])[1] == pytest.approx(.87)


def test_mask_and_marked_image():
    rs = ic.norm_regions(REGIONS)
    m = ic.render_mask((200, 100), rs)
    assert m.getpixel((50, 30)) == 255 and m.getpixel((150, 70)) == 255 and m.getpixel((110, 10)) == 255 and m.getpixel((30, 90)) == 255
    assert m.getpixel((100, 60)) == 0 and m.getpixel((195, 5)) == 0
    src = Image.new('RGB', (400, 200), (90, 90, 90))
    marked = ic.render_marked(src, rs)
    assert marked.size == (400, 200) and marked.getpixel((100, 60)) == (90, 90, 90)          # 圈内内容不盖住
    assert marked.getpixel((40 - 2, 100)) != (90, 90, 90) and marked.getpixel((300, 10)) == (90, 90, 90)   # 只有描边变了
    assert ic.render_marked(Image.new('RGB', (4096, 2304), 'gray'), rs).size == (2048, 1152)


def test_lock_outside_keeps_outside_and_reports_drift():
    rs = ic.norm_regions([{'shape': 'rect', 'x': .4, 'y': .4, 'w': .2, 'h': .2}])
    src = Image.new('RGB', (400, 200), (100, 100, 100))
    blue = Image.new('RGB', (800, 400), (0, 0, 250))                                 # 模型把整张图都改了,且尺寸不同
    comp, drift, applied = ic.lock_outside(src, blue, rs)
    assert applied and comp.size == (400, 200) and comp.getpixel((200, 100)) == (0, 0, 250)
    assert comp.getpixel((20, 20)) == (100, 100, 100) and comp.getpixel((390, 190)) == (100, 100, 100)
    assert drift > ic.DRIFT_WARN
    good = src.copy()
    good.paste((250, 0, 0), (160, 80, 240, 120))                                     # 只改了圈内
    comp2, drift2, _ = ic.lock_outside(src, good, rs)
    assert drift2 == 0 and comp2.getpixel((200, 100)) == (250, 0, 0)
    out, drift3, applied3 = ic.lock_outside(src, Image.new('RGB', (300, 300), 'blue'), rs)
    assert not applied3 and drift3 is None and out.size == (300, 300)                # 画幅变了:对不上位,不合成


# ---------------------------------------------------------------- 参考图 / 任务
def test_refs_tray_upload_library_note_remove(base):
    doc, _ = ic.open_doc(base, SHEET)
    put(base, 'assets/concepts/props/P1/main_01.png', jpeg(color='green'))
    a = ic.add_ref(base, doc, data=jpeg(color='blue'), filename='哪吒行宫 牌匾.JPG', note='牌匾')
    b = ic.add_ref(base, doc, library_rel='assets/concepts/props/P1/main_01.png')
    assert (a['n'], b['n'], a['source'], b['source']) == (1, 2, 'upload', 'library')
    assert a['file'].startswith(doc.relative_to(base).as_posix() + '/refs/01_') and (base / a['file']).is_file()
    assert b['file'] == 'assets/concepts/props/P1/main_01.png'                       # 项目库里的图只记路径,不复制
    assert ic.update_ref(doc, 2, note='乾坤圈')['note'] == '乾坤圈'
    ic.update_ref(doc, 1, remove=True)
    assert ic.add_ref(base, doc, data=jpeg())['n'] == 3                              # 编号不回收
    assert [r['n'] for r in ic.state(base, doc, ic.load_history(doc))['refs']] == [2, 3]
    for bad in (dict(data=b'not an image'), dict(library_rel='../x.png'), dict(library_rel='brief.md')):
        with pytest.raises(ic.CanvasError):
            ic.add_ref(base, doc, **bad)
    groups = {g['group']: [i['file'] for i in g['items']] for g in ic.library_images(base)}
    assert groups['characters'] == [SHEET] and groups['props'] == ['assets/concepts/props/P1/main_01.png']
    assert ic.library_images(base, 'main_01')[0]['group'] == 'props' and not ic.library_images(base, 'zzz')


def edit_payload(**over):
    p = {'parent': 'v000', 'regions': [{'shape': 'rect', 'x': .1, 'y': .1, 'w': .3, 'h': .4, 'text': '牌匾换成 @图1 那块'}],
         'text': '整体更暗一点,参考 @ref 2 的色调,@图9 不存在', 'refs': [1, 2], 'lock': True, 'mode': 'direct', 'preview': True}
    p.update(over)
    return p


def test_edit_job_builds_images_prompt_and_is_exclusive(base):
    doc, _ = ic.open_doc(base, SHEET)
    ic.add_ref(base, doc, data=jpeg(color='blue'), filename='plaque.jpg', note='哪吒行宫的牌匾')
    ic.add_ref(base, doc, data=jpeg(color='black'), filename='mood.jpg')
    job = ic.create_edit_job(base, doc, edit_payload())
    assert job['status'] == 'awaiting_confirm' and job['prompt'] == job['template_prompt'] and job['lock'] is True
    assert [(i['n'], i['role']) for i in job['images']] == [(1, 'source'), (2, 'marked'), (3, 'ref'), (4, 'ref')]
    jd = doc / 'jobs' / job['id']
    assert all((jd / n).is_file() for n in ('source.png', 'marked.jpg', 'mask.png', 'region_1.jpg', 'job.json'))
    p = job['template_prompt']
    assert p.startswith('Edit [Image 1].') and '[Image 2] is a copy of [Image 1] with numbered coloured outlines' in p
    assert 'Region 1 (upper left of the frame, about x 10–40%, y 10–50%): 牌匾换成 [Image 3] 那块' in p
    assert 'Overall instruction: 整体更暗一点,参考 [Image 4] 的色调,@图9 不存在' in p                  # 没带的参考图保留原文
    assert '[Image 3] is a reference image: 哪吒行宫的牌匾.' in p and '[Image 4] is a reference image.' in p
    assert p.rstrip().endswith('Change only the marked regions.')
    with pytest.raises(ic.CanvasError) as e:                                         # 同一张图同时只跑一个任务
        ic.create_edit_job(base, doc, edit_payload())
    assert e.value.status == 409 and 'still running' in e.value.text(zh=False)
    ic.set_job(doc, job['id'], status='cancelled')
    plain = ic.create_edit_job(base, doc, edit_payload(regions=[], refs=[], text='去掉水印', preview=False))
    assert plain['status'] == 'queued' and not plain['lock'] and [i['role'] for i in plain['images']] == ['source']
    assert 'Instruction: 去掉水印' in plain['template_prompt'] and '[Image 2]' not in plain['template_prompt']
    ic.set_job(doc, plain['id'], status='done')
    assert ic.create_edit_job(base, doc, edit_payload(mode='agent', preview=False))['status'] == 'drafting'
    ic.set_job(doc, ic.load_history(doc)['jobs'][-1], status='failed')
    with pytest.raises(ic.CanvasError):
        ic.create_edit_job(base, doc, edit_payload(regions=[], text=''))             # 什么都没写


def test_stale_and_orphaned_jobs_are_released(base):
    doc, _ = ic.open_doc(base, SHEET)
    job = ic.create_edit_job(base, doc, edit_payload(refs=[], mode='agent', preview=False))
    ic.set_job(doc, job['id'], run_id='r1')
    h = ic.load_history(doc)
    assert ic.active_job(doc, h, lambda rid: None)['id'] == job['id']                # Agent 还在跑
    assert ic.active_job(doc, h, lambda rid: 'error:boom') is None                   # Agent 结束了却没交提示词
    assert ic.load_job(doc, job['id'])['status'] == 'failed' and 'boom' in ic.load_job(doc, job['id'])['error']
    job2 = ic.create_edit_job(base, doc, edit_payload(refs=[], preview=False))
    j = ic.load_job(doc, job2['id'])
    j['updated_ts'] -= 3600
    (doc / 'jobs' / j['id'] / 'job.json').write_text(json.dumps(j))
    assert ic.active_job(doc, ic.load_history(doc)) is None and ic.load_job(doc, job2['id'])['status'] == 'failed'


def test_run_edit_locks_outside_and_records_version(base, channel):
    doc, _ = ic.open_doc(base, SHEET)
    ic.add_ref(base, doc, data=jpeg(color='blue'), filename='plaque.jpg', note='牌匾')
    job = ic.create_edit_job(base, doc, edit_payload(refs=[1], preview=False))
    calls = []

    def fake(prompt, output, refs, size, seed):
        calls.append({'prompt': prompt, 'refs': refs, 'size': size})
        Image.new('RGB', (640, 360), (0, 0, 240)).save(output, 'PNG')                # 模型整张都改了,且放大了一倍
        genmedia._record_image_usage(output, {'provider': 'test', 'model': 'm1'}, {'output_tokens': 16000, 'total_tokens': 16300}, 640, 360)
    v = ic.run_edit(base, doc, job['id'], 'Replace the plaque in region 1 with the plaque of [Image 3].', generate=fake, log=QUIET)
    assert len(calls) == 1 and calls[0]['size'] == '320x180' and len(calls[0]['refs']) == 3
    assert [Path(r).name for r in calls[0]['refs'][:2]] == ['source.png', 'marked.jpg']
    assert (v['id'], v['op'], v['parent'], v['width'], v['height'], v['provider'], v['model']) == ('v001', 'edit', 'v000', 320, 180, 'test', 'm1')
    assert v['model_size'] == [640, 360] and v['lock']['applied'] and v['lock']['warn'] and v['lock']['raw'] == 'v001.raw.png'
    assert v['usage'] == {'total_tokens': 16300, 'completion_tokens': 16000}          # 接口返回了用量的渠道:记在版本上
    assert v['refs'] == [{'n': 1, 'file': job['images'][2]['file'], 'note': '牌匾'}] and v['regions'][0]['text'].startswith('牌匾')
    out, src = Image.open(doc / v['file']), Image.open(doc / 'v000.png')
    assert ic.encoding_of((doc / v['file']).read_bytes()) == 'JPEG'
    assert mean_diff(out, src, (200, 100, 320, 180)) < 2 and mean_diff(out, src, (50, 30, 110, 80)) > 80   # 圈外不变,圈内换了
    assert ic.load_job(doc, job['id'])['status'] == 'done' and ic.load_job(doc, job['id'])['version'] == 'v001'
    whole = ic.unlock_version(base, doc, 'v001')                                     # 改用模型返回的整图
    assert whole['id'] == 'v002' and whole['from_raw'] == 'v001' and not whole['lock']['applied']
    assert mean_diff(Image.open(doc / whole['file']), src, (200, 100, 320, 180)) > 80
    assert (base / SHEET).read_bytes() == (doc / 'v000.png').read_bytes()            # 仍然没动生产链路


def test_run_edit_failure_marks_job_failed_and_capacity_guard(base, channel):
    doc, _ = ic.open_doc(base, SHEET)
    job = ic.create_edit_job(base, doc, edit_payload(refs=[], preview=False))

    def boom(*a):
        raise RuntimeError('HTTP 400 content rejected')
    with pytest.raises(RuntimeError):
        ic.run_edit(base, doc, job['id'], 'x', generate=boom, log=QUIET)
    j = ic.load_job(doc, job['id'])
    assert j['status'] == 'failed' and 'content rejected' in j['error'] and len(ic.load_history(doc)['versions']) == 1
    channel['ref_capacity'] = 1                                                      # 原图 + 标注图 = 2 张,渠道只收 1 张
    job2 = ic.create_edit_job(base, doc, edit_payload(refs=[], preview=False))
    with pytest.raises(ic.CanvasError, match='只收 1 张图'):
        ic.run_edit(base, doc, job2['id'], 'x', generate=boom, log=QUIET)
    channel['ref_capacity'] = 0
    job3 = ic.create_edit_job(base, doc, edit_payload(refs=[], preview=False))
    with pytest.raises(ic.CanvasError, match='没有配置图生图'):
        ic.run_edit(base, doc, job3['id'], 'x', generate=boom, log=QUIET)
    j3 = ic.load_job(doc, job3['id'])
    assert '没有配置图生图' in j3['error'] and 'no image-to-image configured' in j3['error_en']      # 中英各一份,宿主按界面语言取


def test_edit_request_size_is_fitted_into_model_limits(base, channel):
    put(base, SHEET, jpeg((2858, 1608)))
    doc, _ = ic.open_doc(base, SHEET)
    channel['limits'] = {'mode': 'pixels', 'max_pixels': 2048 * 2048, 'min_pixels': 1024 * 1024, 'multiple': 2, 'basis': 'doc'}
    job = ic.create_edit_job(base, doc, edit_payload(regions=[], refs=[], text='x', preview=False))
    sizes = []

    def fake(prompt, output, refs, size, seed):
        sizes.append(size)
        w, h = (int(x) for x in size.split('x'))
        Image.new('RGB', (w, h), 'navy').save(output, 'PNG')
    v = ic.run_edit(base, doc, job['id'], 'x', generate=fake, log=QUIET)
    w, h = (int(x) for x in sizes[0].split('x'))
    assert w * h <= 2048 * 2048 and abs(w / h - 2858 / 1608) < .01
    assert (v['width'], v['height']) == (2858, 1608) and not v['lock']['applied']    # 编辑不改分辨率


def test_upscale_redraw_and_fidelity(base, channel):
    doc, _ = ic.open_doc(base, SHEET)
    with pytest.raises(ic.CanvasError):
        ic.create_upscale_job(base, doc, {'parent': 'v000', 'mode': 'redraw', 'width': 320})        # 没有变大
    job = ic.create_upscale_job(base, doc, {'parent': 'v000', 'mode': 'redraw', 'width': 641, 'provider': 'fal', 'model': 'x'})
    assert job['target'] == [640, 360] and job['status'] == 'queued'                 # 高按画幅推,取偶数
    seen = []

    def fake(prompt, output, refs, size, seed):
        seen.append((prompt, refs, size))
        Image.new('RGB', (1280, 720), 'teal').save(output, 'PNG')                    # 模型给得比请求大
    v = ic.run_upscale(base, doc, job['id'], generate=fake, log=QUIET)
    assert seen[0][0] == ic.UPSCALE_PROMPT and len(seen[0][1]) == 1 and seen[0][2] == '640x360'
    assert (v['op'], v['width'], v['height'], v['upscale']['mode'], v['upscale']['model_size']) == ('upscale', 640, 360, 'redraw', [1280, 720])

    job2 = ic.create_upscale_job(base, doc, {'parent': 'v000', 'mode': 'redraw', 'width': 640})
    with pytest.raises(ic.CanvasError, match='没有比原图'):                           # 模型没按请求尺寸出:判失败,不拿插值凑数
        ic.run_upscale(base, doc, job2['id'], generate=lambda p, o, r, s, sd: Image.new('RGB', (320, 180)).save(o, 'PNG'), log=QUIET)
    assert ic.load_job(doc, job2['id'])['status'] == 'failed'

    channel['limits'] = {'mode': 'pixels', 'max_pixels': 324 * 182, 'min_pixels': 0, 'multiple': 2}
    job3 = ic.create_upscale_job(base, doc, {'parent': 'v000', 'mode': 'redraw', 'width': 640})
    with pytest.raises(ic.CanvasError, match='放不大'):
        ic.run_upscale(base, doc, job3['id'], generate=fake, log=QUIET)

    job4 = ic.create_upscale_job(base, doc, {'parent': 'v000', 'mode': 'fidelity', 'upscaler': 'local', 'width': 960})
    v4 = ic.run_upscale(base, doc, job4['id'], log=QUIET)                            # 本机插值:真跑,不联网
    assert (v4['width'], v4['height'], v4['provider'], v4['model'], v4['upscale']['upscaler']) == (960, 540, 'local', 'lanczos', 'local')
    assert ic.encoding_of((doc / v4['file']).read_bytes()) == 'JPEG'
    with pytest.raises(ic.CanvasError):
        ic.create_upscale_job(base, doc, {'parent': 'v000', 'mode': 'fidelity', 'upscaler': 'local', 'width': 320 * 9})   # 超过最大倍数
    with pytest.raises(ic.CanvasError):
        ic.create_upscale_job(base, doc, {'parent': 'v000', 'mode': 'fidelity', 'upscaler': 'nope', 'width': 640})


# ---------------------------------------------------------------- 最终版
def test_adopt_fixed_path_writes_back_and_keeps_everything(base):
    doc, _ = ic.open_doc(base, SHEET)
    flipped = ic.transform(base, doc, 'v000', 'crop', {'x': 0, 'y': 0, 'w': .5, 'h': 1})
    put(base, SHEET, jpeg(color='red'))                                              # 设最终版之前原图被别处改过
    res = ic.adopt(base, doc, flipped['id'], log=QUIET)
    h = ic.load_history(doc)
    assert res['target_file'] == SHEET and (base / SHEET).read_bytes() == (doc / flipped['file']).read_bytes()
    assert h['final'] == h['disk'] == flipped['id'] and [v['op'] for v in h['versions']] == ['original', 'transform', 'external']
    assert h['final_log'][-1]['version'] == flipped['id']
    assert ic.open_doc(base, SHEET)[1]['final'] == flipped['id'] and len(ic.open_doc(base, SHEET)[1]['versions']) == 3
    ic.adopt(base, doc, 'v000', log=QUIET)                                           # 随时改回原图
    assert (base / SHEET).read_bytes() == (doc / 'v000.png').read_bytes()
    for vid in ('v000', flipped['id']):                                              # 原图 / 在用的版本不能删;别的可以
        if vid == 'v000':
            with pytest.raises(ic.CanvasError):
                ic.delete_version(base, doc, vid)
    assert ic.delete_version(base, doc, flipped['id'])['ok'] and (doc / 'trash' / flipped['file']).is_file()
    assert flipped['id'] not in [v['id'] for v in ic.state(base, doc, ic.load_history(doc))['versions']]


def test_adopt_sketch_updates_ledger(tmp_path):
    from modules import storyboard_board as sbb
    rel = 'assets/storyboard/ep01/S01-03.png'
    put(tmp_path, rel, jpeg())
    sbb.update_index(tmp_path, 'ep01', 'S01-03', {'status': 'done', 'mode': 'grid', 'provider': 'fal', 'model': 'old', 'note': 'keep'})
    doc, h = ic.open_doc(tmp_path, rel)
    assert h['storage'] == 'sketch' and ic.provenance(tmp_path, h)['model'] == 'old'
    v = ic.transform(tmp_path, doc, 'v000', 'flip_h')
    ic.adopt(tmp_path, doc, v['id'], log=QUIET)
    rec = sbb.load_index(tmp_path, 'ep01')['shots']['S01-03']
    assert rec['mode'] == 'canvas' and rec['canvas_version'] == v['id'] and rec['note'] == 'keep' and rec['status'] == 'done'
    assert (tmp_path / rel).read_bytes() == (doc / v['file']).read_bytes()


def plate_project(tmp_path, monkeypatch):
    """库图 L1_t3 被 ep01 sh001 起点 / 终点、ep02 sh005 起点引用;另一场景同名 key 不算。"""
    base, sid = tmp_path, 'SCN-1'
    src_rel = f'assets/concepts/scenes/{sid}/plates/L1_t3.png'
    put(base, src_rel, jpeg((192, 108)))
    entry = {'key': 'L1_t3', 'file': src_rel, 'grid9': True, 'master': False, 'camera': {'facing': 'north'}, 'lighting_scheme_id': 'L1',
             'time_of_day': 'dusk', 'size': '192x108', 'pano_ref': {'kind': 'grid9', 'tile': 2}, 'whitebox_frame': 'wb.jpg'}
    sp.save_library(base, sid, {'schema_version': sp.SCHEMA_LIBRARY, 'scene_id': sid, 'plates': [entry]})
    slot = lambda role, key='L1_t3': {'role': role, 'key': key, 'file': src_rel, 'reuse': 'grid9', 'view': {'fraction': .5}}   # noqa: E731
    sp.save_episode_index(base, 'ep01', {'schema_version': sp.SCHEMA_EPISODE, 'ep': 'ep01', 'shots': {
        'sh001': {'group_id': 'grp001', 'scene_id': sid, 'plates': [slot('start'), slot('end')]},
        'sh003': {'group_id': 'grp002', 'scene_id': 'SCN-2', 'plates': [slot('start')]}}})
    sp.save_episode_index(base, 'ep02', {'schema_version': sp.SCHEMA_EPISODE, 'ep': 'ep02', 'shots': {
        'sh005': {'group_id': 'grp003', 'scene_id': sid, 'plates': [slot('start')]}}})
    synced = []
    monkeypatch.setattr(sp, 'sync_group', lambda base, ep, gid, write=False, **k: synced.append((ep, gid)) or {'group_id': gid, 'errors': []})
    return base, sid, src_rel, entry, synced


def keys_of(base, ep, shot):
    return [s['key'] for s in sp.load_episode_index(base, ep)['shots'][shot]['plates']]


def test_adopt_plate_registers_rev_repoints_and_reverts(tmp_path, monkeypatch):
    base, sid, src_rel, entry, synced = plate_project(tmp_path, monkeypatch)
    doc, h = ic.open_doc(base, src_rel)
    assert h['storage'] == 'plate' and h['meta'] == {'sid': sid, 'key': 'L1_t3', 'id': sid}
    assert ic.impact(base, h) == {'users': ['ep01/sh001', 'ep01/sh001(end)', 'ep02/sh005'], 'notes': ['plate_new_key', 'videos_unchanged']}
    v1 = ic.transform(base, doc, 'v000', 'flip_h')
    res = ic.adopt(base, doc, v1['id'], log=QUIET)
    assert res['plate_key'] == 'L1_t3_rev1' and res['created'] and res['replaced'] == ['ep01/sh001', 'ep01/sh001(end)', 'ep02/sh005']
    lib = sp.load_library(base, sid)['plates']
    assert lib[0] == entry and (base / src_rel).read_bytes() == (doc / 'v000.png').read_bytes()      # 原图与原条目不动
    new = lib[1]
    assert new['key'] == 'L1_t3_rev1' and new['revised'] and not new['master'] and new['size'] == '192x108'
    assert new['created_by'] == {'source': 'canvas', 'scene_id': sid, 'tool': 'image_canvas', 'doc': doc.name, 'version': v1['id']}
    assert new['pano_ref']['kind'] == 'revision' and new['pano_ref']['source_kind'] == 'grid9' and new['camera'] == entry['camera']
    assert (base / new['file']).read_bytes() == (doc / v1['file']).read_bytes() and (base / new['file']).with_suffix('.json').is_file()
    assert keys_of(base, 'ep01', 'sh001') == ['L1_t3_rev1'] * 2 and keys_of(base, 'ep01', 'sh003') == ['L1_t3']    # 别的场景不动
    s0 = sp.load_episode_index(base, 'ep01')['shots']['sh001']['plates'][0]
    assert s0['reuse'] == 'revised' and s0['revised_from']['key'] == 'L1_t3' and s0['view'] == {'fraction': .5}
    assert sorted(synced) == [('ep01', 'grp001'), ('ep02', 'grp003')]
    h = ic.load_history(doc)
    assert h['final'] == v1['id'] and ic.version_of(h, v1['id'])['plate_key'] == 'L1_t3_rev1'
    assert ic.open_doc(base, new['file'])[0] == doc                                  # 从 _rev1 打开回到同一份历史
    with pytest.raises(ic.CanvasError):
        ic.delete_version(base, doc, v1['id'])                                       # 已登记的版本不能删

    v2 = ic.transform(base, doc, v1['id'], 'flip_v')                                 # 再改一版:登记 _rev2,跟着 _rev1 的镜一起走
    res2 = ic.adopt(base, doc, v2['id'], log=QUIET)
    assert res2['plate_key'] == 'L1_t3_rev2' and len(res2['replaced']) == 3 and keys_of(base, 'ep02', 'sh005') == ['L1_t3_rev2']
    res1 = ic.adopt(base, doc, v1['id'], log=QUIET)                                  # 选回已登记过的版本:不新建条目
    assert res1['plate_key'] == 'L1_t3_rev1' and not res1['created'] and len(sp.load_library(base, sid)['plates']) == 3
    assert keys_of(base, 'ep01', 'sh001') == ['L1_t3_rev1'] * 2
    res0 = ic.adopt(base, doc, 'v000', log=QUIET)                                    # 改回原图
    assert res0['plate_key'] == 'L1_t3' and not res0['created'] and keys_of(base, 'ep01', 'sh001') == ['L1_t3'] * 2
    assert ic.load_history(doc)['final'] == 'v000'


def test_adopt_plate_library_only_and_direct_revert_restores_reuse(tmp_path, monkeypatch):
    base, sid, src_rel, _, synced = plate_project(tmp_path, monkeypatch)
    doc, _ = ic.open_doc(base, src_rel)
    v1 = ic.transform(base, doc, 'v000', 'flip_h')
    res = ic.adopt(base, doc, v1['id'], repoint=False, log=QUIET)
    assert res['created'] and res['replaced'] == [] and keys_of(base, 'ep01', 'sh001') == ['L1_t3'] * 2 and not synced
    ic.adopt(base, doc, v1['id'], log=QUIET)
    assert keys_of(base, 'ep01', 'sh001') == ['L1_t3_rev1'] * 2 and len(sp.load_library(base, sid)['plates']) == 2
    ic.adopt(base, doc, 'v000', log=QUIET)
    s0 = sp.load_episode_index(base, 'ep01')['shots']['sh001']['plates'][0]
    assert s0['key'] == 'L1_t3' and s0['reuse'] == 'grid9' and 'revised_from' not in s0 and 'revision' not in s0


def test_impact_for_fixed_path_images(base):
    put(base, 'assets/prompts/ep01/grp001.json', json.dumps({'group_id': 'grp001', 'refs': [SHEET, 'x.png']}).encode())
    put(base, 'assets/prompts/ep01/grp002.json', json.dumps({'group_id': 'grp002', 'refs': ['other.png'], 'notes': SHEET}).encode())
    doc, h = ic.open_doc(base, SHEET, 'costume')
    assert ic.impact(base, h) == {'users': ['ep01/grp001'], 'notes': ['videos_unchanged', 'avatar']}     # 只认 refs 里的引用
    st = ic.state(base, doc, h)
    assert st['kind'] == 'costume' and st['setting_kind'] == 'costume' and st['pref_kind'] == 'characters'
    assert st['info']['width'] == 320 and st['info']['encoding'] == 'JPEG' and st['info']['created'] <= st['info']['modified'] + 1
    cover = 'edit/ep02/thumbnail_main.png'
    put(base, cover, jpeg())
    put(base, 'publish/douyin/package/ep02/cover.jpg', jpeg())
    _, hc = ic.open_doc(base, cover)
    assert ic.impact(base, hc) == {'users': [], 'notes': ['publish_copy']} and ic.state(base, ic.doc_dir_of(base, cover), hc)['setting_kind'] == ''


def test_describe_changes_follows_lineage(base, channel):
    doc, _ = ic.open_doc(base, SHEET)
    ic.add_ref(base, doc, data=jpeg(color='blue'), filename='p.jpg', note='哪吒行宫牌匾')
    job = ic.create_edit_job(base, doc, edit_payload(refs=[1], preview=False))
    v1 = ic.run_edit(base, doc, job['id'], 'x', generate=lambda p, o, r, s, sd: Image.new('RGB', (320, 180), 'blue').save(o, 'PNG'), log=QUIET)
    v2 = ic.transform(base, doc, v1['id'], 'rotate', {'deg': 180})
    text = ic.describe_changes(ic.load_history(doc), v2['id'])
    assert text.splitlines()[0].startswith('v001 编辑:区域1:牌匾换成 @图1 那块;整体更暗一点') and '图1=哪吒行宫牌匾' in text
    assert text.splitlines()[1] == 'v002 调整:rotate 180°' and ic.describe_changes(ic.load_history(doc), 'v000') == ''


# ---------------------------------------------------------------- 命令行
def run_cli(monkeypatch, base, *argv) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'code'))
    import canvas_edit
    monkeypatch.setattr(sys, 'argv', ['canvas_edit.py', *argv, '--out-root', str(base)])
    return canvas_edit.main()


def test_cli_submit_respects_preview_and_status(base, channel, monkeypatch, capsys):
    doc, _ = ic.open_doc(base, SHEET)
    made = []
    monkeypatch.setattr(ic, '_default_generate', lambda p, o, r, s, sd: made.append(p) or Image.new('RGB', (320, 180), 'blue').save(o, 'PNG'))
    job = ic.create_edit_job(base, doc, edit_payload(refs=[], mode='agent', preview=True))
    assert run_cli(monkeypatch, base, 'submit', '--doc', doc.name, '--job', job['id'], '--prompt', 'Make it darker.') == 0
    j = ic.load_job(doc, job['id'])
    assert j['status'] == 'awaiting_confirm' and j['prompt'] == 'Make it darker.' and not made          # 先看提示词:不出图
    assert run_cli(monkeypatch, base, 'submit', '--doc', doc.name, '--job', job['id'], '--prompt', 'again') == 1   # 不再接收提示词
    ic.set_job(doc, job['id'], status='queued', prompt='Make it much darker.')                         # 用户确认(可改过)
    assert run_cli(monkeypatch, base, 'submit', '--doc', doc.name, '--job', job['id'], '--confirmed') == 0
    assert made == ['Make it much darker.'] and ic.load_job(doc, job['id'])['version'] == 'v001'
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])['canvas_edit']
    assert out['status'] == 'done' and out['version'] == 'v001'

    job2 = ic.create_edit_job(base, doc, edit_payload(refs=[], mode='agent', preview=False))
    pf = base / 'p.txt'
    pf.write_text('Remove the lantern.\n', encoding='utf-8')
    assert run_cli(monkeypatch, base, 'submit', '--doc', doc.name, '--job', job2['id'], '--prompt-file', str(pf)) == 0
    assert made[-1] == 'Remove the lantern.' and ic.load_job(doc, job2['id'])['status'] == 'done'
    job3 = ic.create_edit_job(base, doc, edit_payload(refs=[], mode='direct', preview=False))
    assert run_cli(monkeypatch, base, 'submit', '--doc', doc.name, '--job', job3['id'], '--template') == 0
    assert made[-1] == job3['template_prompt']
    assert run_cli(monkeypatch, base, 'submit', '--doc', '../x', '--job', job3['id'], '--template') == 1
    assert run_cli(monkeypatch, base, 'show', '--file', SHEET) == 0
    assert run_cli(monkeypatch, base, 'adopt', '--file', SHEET, '--version', 'v003') == 0
    assert (base / SHEET).read_bytes() == (doc / 'v003.png').read_bytes()


# ---------------------------------------------------------------- 尺寸约束 / 保真超分(genmedia)
def test_image_size_limits_per_channel(monkeypatch):
    pro = genmedia.image_size_limits({'provider': 'volcengine', 'model': 'doubao-seedream-5-0-pro-260128'})
    assert pro == {'mode': 'pixels', 'max_pixels': genmedia.IMAGE_MAX_PIXELS_SEEDREAM_PRO, 'min_pixels': 3_686_400, 'multiple': 2, 'basis': 'measured'}
    assert genmedia.fit_image_request(pro, 1280, 720) == (2560, 1440)               # 小图(草图)编辑:请求按该模型出图下限,结果再对回底图尺寸
    assert genmedia.image_size_limits({'provider': 'byteplus', 'model': 'seedream-4-5-251128'})['min_pixels'] == 0
    w, h = genmedia.image_max_size(pro, 1920, 1080)
    assert w * h <= genmedia.IMAGE_MAX_PIXELS_SEEDREAM_PRO < (w + 2) * (h + 2) and abs(w / h - 16 / 9) < .005
    assert genmedia.image_size_limits({'provider': 'volcengine', 'model': 'my-custom-endpoint'}) == {'mode': 'unknown'}
    gpt = genmedia.image_size_limits({'provider': 'fal', 'model': 'openai/gpt-image-2.5/sunburst'})
    assert genmedia.image_max_size(gpt, 1920, 1080) == (3840, 2160) and genmedia.image_max_size(gpt, 1000, 1000) == (2880, 2880)
    assert genmedia.image_max_size(gpt, 4000, 1000) is None                          # 画幅超过该模型允许的长宽比
    pro2k = genmedia.image_size_limits({'provider': 'fal', 'model': 'bytedance/seedream/v5/pro'})
    assert genmedia.image_max_size(pro2k, 1000, 1000) == (2048, 2048) and genmedia.fit_image_request(pro2k, 2858, 1608)[0] < 2858
    assert genmedia.fit_image_request(pro2k, 1920, 1080) == (1920, 1080)
    banana = genmedia.image_size_limits({'provider': 'fal', 'model': 'google/nano-banana-2.1'})
    assert banana['mode'] == 'tiers' and banana['rule'] == 'long_edge' and genmedia.image_max_size(banana, 1920, 1080) is None
    sizes = [genmedia.image_tier_size(banana, t, 1920, 1080) for t in banana['tiers']]
    assert sizes == [(1280, 720), (2560, 1440), (3840, 2160)]
    for (w, h), tier in zip(sizes, ('1K', '2K', '4K')):                              # 请求尺寸确实落进对应的档
        assert genmedia._fal_image_body({'model': 'google/nano-banana-2.1'}, 'p', '', [], w, h, 1)[1]['resolution'] == tier
    flux = genmedia.image_size_limits({'provider': 'fal', 'model': 'blackforestlabs/flux-3'})
    for t in flux['tiers']:
        w, h = genmedia.image_tier_size(flux, t, 1080, 1920)
        assert w < h and genmedia._resolution_tier(w, h, genmedia.FAL_FLUX3_TIERS) == t
    monkeypatch.setattr(genmedia, '_openrouter_image_model_info',
                        lambda m: {'supported_parameters': {'resolution': {'values': ['4K', '1K', '2K']}}} if m == 'a/b' else None)
    assert genmedia.image_size_limits({'provider': 'openrouter', 'model': 'a/b'})['tiers'] == ['1K', '2K', '4K']
    assert genmedia.image_size_limits({'provider': 'openrouter', 'model': 'c/d'}) == {'mode': 'unknown'}
    assert genmedia.image_size_limits({'provider': 'comfyui', 'model': 'local'}) == {'mode': 'unknown'}
    assert genmedia.fit_image_request({'mode': 'unknown'}, 1234, 567) == (1234, 567)


def test_fidelity_upscalers_request_bodies_and_local(tmp_path, monkeypatch):
    spec = {u['id']: u for u in genmedia.IMAGE_UPSCALERS}
    assert genmedia._fal_upscale_body(spec['fal-ai/seedvr/upscale/image'], 'u', 2.5) == \
        {'image_url': 'u', 'upscale_mode': 'factor', 'upscale_factor': 2.5, 'output_format': 'png'}
    assert genmedia._fal_upscale_body(spec['fal-ai/esrgan'], 'u', 2) == {'image_url': 'u', 'scale': 2.0, 'output_format': 'png'}
    assert genmedia._fal_upscale_body(spec['bria/increase-resolution'], 'u', 2.4) == {'image_url': 'u', 'desired_increase': 4, 'output_type': 'png'}
    assert genmedia._fal_upscale_body(spec['fal-ai/aura-sr'], 'u', 2) == {'image_url': 'u'}             # 固定 4 倍:不发倍率
    monkeypatch.setattr(genmedia, '_fal_key_any', lambda: '')
    ups = {u['id']: u for u in genmedia.image_upscalers()}
    assert ups['local']['configured'] and not ups['fal-ai/esrgan']['configured']
    src = put(tmp_path, 'a.png', jpeg((100, 60)))
    info = genmedia.upscale_image(str(src), str(tmp_path / 'o.png'), 'local', 200, 120)
    assert info['size'] == [200, 120] and Image.open(tmp_path / 'o.png').size == (200, 120)
    for bad in (('local', 100, 60), ('local', 900, 540), ('nope', 200, 120), ('fal-ai/esrgan', 200, 120)):   # 没变大 / 超倍数 / 未知 / 没 Key
        with pytest.raises(RuntimeError):
            genmedia.upscale_image(str(src), str(tmp_path / 'x.png'), *bad)
    calls = []

    def fake_queue(cfg, endpoint, body, timeout, label, kind='', output=''):
        calls.append((cfg, endpoint, {k: v for k, v in body.items() if k != 'image_url'}))
        buf = io.BytesIO()
        Image.new('RGB', (400, 240), 'white').save(buf, 'PNG')                       # 固定 4 倍
        import base64
        return {'image': {'url': 'data:image/png;base64,' + base64.b64encode(buf.getvalue()).decode()}}
    monkeypatch.setattr(genmedia, '_fal_key_any', lambda: 'k')
    monkeypatch.setattr(genmedia, '_fal_queue_run', fake_queue)
    monkeypatch.setattr(genmedia, '_pending_task_clear', lambda o: None)
    info = genmedia.upscale_image(str(src), str(tmp_path / 'f.png'), 'fal-ai/aura-sr', 200, 120)
    assert calls == [({'api_key': 'k'}, 'fal-ai/aura-sr', {})] and info['model_size'] == [400, 240] and info['size'] == [200, 120]


# ---------------------------------------------------------------- 接口(services/runtime/core.py)
@pytest.fixture
def runtime(monkeypatch, tmp_path):
    from services.runtime import core
    monkeypatch.setattr(core, 'PROJECTS_DIR', tmp_path / 'projects')
    core.PROJECTS_DIR.mkdir()
    core.ensure_project('p')
    monkeypatch.setattr(core, 'load_genconfig', lambda: {'image': {'provider': 'fal', 'fal': {'api_key': 'k', 'model': 'm'}}})
    monkeypatch.setattr(core, 'ui_lang_code', lambda cfg=None: 'zh')
    monkeypatch.setattr(genmedia, '_fal_key_any', lambda: '')
    spawned, chats = [], []
    monkeypatch.setattr(core, '_canvas_spawn', lambda base, doc, jid, action, *flags: spawned.append((jid, action, flags)))

    async def fake_chat(body):
        chats.append(body)
        return {'ok': True, 'run_id': f'r{len(chats)}'}
    monkeypatch.setattr(core, 'api_chat', fake_chat)
    base = core.PROJECTS_DIR / 'p'
    put(base, SHEET, jpeg())
    return core, base, spawned, chats


def test_api_state_edit_confirm_cancel(runtime):
    core, base, spawned, chats = runtime
    st = asyncio.run(core.api_canvas_get('p', SHEET, 'character', '姜子牙', 'CHAR-1'))
    assert st['file'] == SHEET and st['final'] == 'v000' and st['ui'] == {'label': '姜子牙', 'oid': 'CHAR-1'}
    assert st['versions'][0]['url'].startswith('/projects/p/assets/canvas/') and st['info']['url'].startswith(f'/projects/p/{SHEET}?v=')
    assert st['models']['global'] == {'provider': 'fal', 'model': 'm'} and st['upscalers'][0]['id'] == 'local' and st['agent'] == core.CANVAS_AGENT
    body = {'file': SHEET, **edit_payload(refs=[], mode='direct', preview=True)}
    st = asyncio.run(core.api_canvas_edit('p', body))
    assert st['job']['status'] == 'awaiting_confirm' and st['job']['prompt'].startswith('Edit [Image 1].') and not spawned
    with pytest.raises(core.ServiceError) as e:
        asyncio.run(core.api_canvas_edit('p', body))
    assert e.value.status_code == 409
    st = asyncio.run(core.api_canvas_confirm('p', {'file': SHEET, 'job': st['job']['id'], 'prompt': 'Edit [Image 1]. Darker.'}))
    assert st['job']['status'] == 'queued' and spawned == [(st['job']['id'], 'submit', ('--confirmed',))]
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_canvas_confirm('p', {'file': SHEET, 'job': st['job']['id'], 'cancel': True}))   # 已在出图,不能取消
    ic.set_job(ic.doc_dir_of(base, SHEET), st['job']['id'], status='failed', error='x')
    st = asyncio.run(core.api_canvas_edit('p', {'file': SHEET, **edit_payload(refs=[], mode='direct', preview=False), 'provider': 'fal', 'model': 'zz'}))
    assert st['job']['status'] == 'queued' and st['job']['model'] == 'zz' and spawned[-1][1:] == ('submit', ('--template',))
    assert st['last_job']['status'] == 'failed'
    ic.set_job(ic.doc_dir_of(base, SHEET), st['job']['id'], status='failed', error='中文原因', error_en='English reason')
    assert asyncio.run(core.api_canvas_get('p', SHEET))['last_job']['error'] == '中文原因'
    core.ui_lang_code = lambda cfg=None: 'ja'                                        # 非中文界面一律英文(fixture 的 monkeypatch 会还原)
    last = asyncio.run(core.api_canvas_get('p', SHEET))['last_job']
    assert last['error'] == 'English reason' and 'error_en' not in last
    core.ui_lang_code = lambda cfg=None: 'zh'
    with pytest.raises(core.ServiceError):
        asyncio.run(core.api_canvas_edit('p', {'file': SHEET, 'text': 'x', 'provider': 'nope'}))


def test_api_agent_edit_dispatches_work_order(runtime):
    core, base, spawned, chats = runtime
    doc, _ = ic.open_doc(base, SHEET)
    ic.add_ref(base, doc, data=jpeg(color='blue'), filename='p.jpg', note='哪吒行宫牌匾')
    st = asyncio.run(core.api_canvas_edit('p', {'file': SHEET, 'kind': 'character', **edit_payload(refs=[1], mode='agent', preview=True)}))
    assert st['job']['status'] == 'drafting' and st['job']['run_id'] == 'r1' and not spawned
    msg = chats[0]['message']
    assert chats[0]['agent'] == core.CANVAS_AGENT and msg.startswith(f"[画板 {st['job']['id']}]")
    jd = doc / 'jobs' / st['job']['id']
    for needle in (f"[Image 1] 底图:{jd / 'source.png'}", f"[Image 2] 带圈标注图:{jd / 'marked.jpg'}", '区域1(upper left of the frame',
                   f"裁切图 {jd / 'region_1.jpg'}", '@图1 = [Image 3]', '哪吒行宫牌匾', '圈外锁定:开',
                   f"python3 code/canvas_edit.py submit --project p --doc {doc.name} --job {st['job']['id']} --prompt-file {jd / 'prompt.txt'}",
                   '发送前先看提示词'):
        assert needle in msg, needle
    st = asyncio.run(core.api_canvas_confirm('p', {'file': SHEET, 'job': st['job']['id'], 'cancel': True}))     # Agent 整理期间可取消
    assert st['job'] is None and st['last_job']['status'] == 'cancelled'


def test_api_transform_upscale_version_refs_limits(runtime, monkeypatch):
    core, base, spawned, chats = runtime
    st = asyncio.run(core.api_canvas_transform('p', {'file': SHEET, 'version': 'v000', 'op': 'flip_h'}))
    assert st['created'] == 'v001' and [v['id'] for v in st['versions']] == ['v000', 'v001']
    st = asyncio.run(core.api_canvas_upscale('p', {'file': SHEET, 'parent': 'v001', 'mode': 'fidelity', 'upscaler': 'local', 'width': 640}))
    assert st['job']['op'] == 'upscale' and st['job']['target'] == [640, 360] and spawned[-1][1] == 'upscale'
    with pytest.raises(core.ServiceError) as e:
        asyncio.run(core.api_canvas_upscale('p', {'file': SHEET, 'parent': 'v001', 'mode': 'fidelity', 'upscaler': 'fal-ai/esrgan', 'width': 640}))
    assert e.value.status_code == 409                                                # 同图互斥先于其它校验
    ic.set_job(ic.doc_dir_of(base, SHEET), st['job']['id'], status='done')
    with pytest.raises(core.ServiceError, match='Fal API Key'):
        asyncio.run(core.api_canvas_upscale('p', {'file': SHEET, 'parent': 'v001', 'mode': 'fidelity', 'upscaler': 'fal-ai/esrgan', 'width': 640}))
    up = asyncio.run(core.api_canvas_ref_upload(jpeg(color='blue'), 'p', SHEET, 'a.jpg'))
    assert up['ref']['n'] == 1 and up['ref']['url'].startswith('/projects/p/assets/canvas/')
    assert asyncio.run(core.api_canvas_ref('p', {'file': SHEET, 'n': 1, 'note': '牌匾'}))['refs'][0]['note'] == '牌匾'
    assert len(asyncio.run(core.api_canvas_ref('p', {'file': SHEET, 'library': SHEET}))['refs']) == 2
    assert [r['n'] for r in asyncio.run(core.api_canvas_ref('p', {'file': SHEET, 'n': 1, 'remove': True}))['refs']] == [2]
    assert asyncio.run(core.api_canvas_library('p', 'sheet'))['groups'][0]['items'][0]['url'].startswith(f'/projects/p/{SHEET}')
    st = asyncio.run(core.api_canvas_version('p', {'file': SHEET, 'version': 'v001', 'action': 'delete'}))
    assert [v['id'] for v in st['versions']] == ['v000']
    monkeypatch.setattr(ic, 'channel_facts', lambda provider, model: {
        'provider': provider or 'fal', 'model': model, 'ref_capacity': None,
        'limits': {'mode': 'pixels', 'max_pixels': 2048 * 2048, 'min_pixels': 0, 'multiple': 2} if model == 'px'
        else {'mode': 'tiers', 'tiers': ['1K', '2K'], 'rule': 'area'} if model == 'tier' else {'mode': 'unknown'}})
    lim = asyncio.run(core.api_canvas_limits('p', SHEET, 'v000', 'fal', 'px'))
    assert lim['current'] == [320, 180] and lim['max'][0] * lim['max'][1] <= 2048 * 2048 and lim['max'][0] > 2700
    assert [t['tier'] for t in asyncio.run(core.api_canvas_limits('p', SHEET, 'v000', 'fal', 'tier'))['tiers']] == ['1K', '2K']
    unk = asyncio.run(core.api_canvas_limits('p', SHEET, 'v000', '', ''))
    assert unk['limits'] == {'mode': 'unknown'} and unk['max'] is None and unk['tiers'] == []


def test_api_adopt_writes_revision_record_and_syncs_setting(runtime):
    core, base, spawned, chats = runtime
    put(base, 'assets/prompts/ep01/grp001.json', json.dumps({'refs': [SHEET]}).encode())
    asyncio.run(core.api_canvas_get('p', SHEET, 'character', '姜子牙', 'CHAR-1'))
    asyncio.run(core.api_canvas_transform('p', {'file': SHEET, 'version': 'v000', 'op': 'flip_h'}))
    imp = asyncio.run(core.api_canvas_impact('p', SHEET))
    assert imp == {'users': ['ep01/grp001'], 'notes': ['videos_unchanged', 'avatar'], 'setting_kind': 'character', 'storage': 'fixed'}
    out = asyncio.run(core.api_canvas_adopt('p', {'file': SHEET, 'version': 'v001', 'sync_setting': True}))
    assert out['final'] == 'v001' and out['users'] == ['ep01/grp001'] and out['adopted']['setting_run'] == 'r1'
    recs = core.load_revisions('p')
    assert len(recs) == 1 and recs[0]['id'] == out['adopted']['revision'] and recs[0]['source'] == 'canvas'
    assert recs[0]['status'] == 'done' and recs[0]['rerun_downstream'] is False and recs[0]['consumed'] is None
    assert recs[0]['record']['changed_files'] == [SHEET] and 'ep01/grp001' in recs[0]['record']['notes']
    assert 'canvas-' in core.pending_revisions_note('p')                             # 总制片下次唤醒时会看到
    chat = chats[0]
    assert chat['agent'] == core.REVISER_ID and '不要重出图' in chat['message'] and 'v001 调整:flip_h' in chat['message']
    assert chat['target'] == {'kind': 'character', 'id': 'CHAR-1', 'ep': '', 'files': [SHEET], 'label': '姜子牙', 'rerun_downstream': False}
    out2 = asyncio.run(core.api_canvas_adopt('p', {'file': SHEET, 'version': 'v001'}))    # 没换版本:不重复记
    assert 'revision' not in out2['adopted'] and len(core.load_revisions('p')) == 1 and len(chats) == 1


def test_routes_page_and_agent_are_registered():
    from services.runtime import core
    root = Path(__file__).resolve().parents[1]
    app_src = (root / 'services/api/app.py').read_text(encoding='utf-8')
    for path in ('canvas"', 'canvas/limits', 'canvas/library', 'canvas/impact', 'canvas/edit', 'canvas/confirm', 'canvas/upscale',
                 'canvas/transform', 'canvas/version', 'canvas/adopt', 'canvas/refs"', 'canvas/refs/upload'):
        assert f'/projects/{{project}}/{path}' in app_src, path
    assert '"canvas"' in (root / 'apps/web/server.py').read_text(encoding='utf-8')
    assert (root / 'apps/web/static/preview_canvas.html').is_file()
    soul = root / 'agents' / core.CANVAS_AGENT / 'SOUL.md'
    assert soul.is_file() and 'code/canvas_edit.py submit' in soul.read_text(encoding='utf-8')
    assert core.AM_AGENT_TIERS[core.CANVAS_AGENT] == 'low'


# ---------------------------------------------------------------- 界面词典
def canvas_ui_strings() -> list[str]:
    """画板页与入口脚本里要进词典的文案:静态文本节点 / title / placeholder(data-no-i18n 之外),加脚本里带中文的单引号字面量
    (页面的写法约定:界面文案一律是单引号字面量,经 t() / F() 或名称表取用)。"""
    import html.parser
    import re
    static = Path(__file__).resolve().parents[1] / 'apps/web/static'
    src = (static / 'preview_canvas.html').read_text(encoding='utf-8')
    cjk = re.compile('[\u4e00-\u9fff]')

    class Collect(html.parser.HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack, self.out = [], []

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag not in ('input', 'img', 'br', 'meta', 'link', 'hr'):
                self.stack.append((tag, 'data-no-i18n' in a))
            if 'data-no-i18n' in a or any(skip for _, skip in self.stack):
                return
            self.out += [a[k].strip() for k in ('title', 'placeholder', 'alt') if a.get(k) and cjk.search(a[k])]

        def handle_endtag(self, tag):
            while self.stack and self.stack.pop()[0] != tag:
                pass

        def handle_data(self, data):
            if cjk.search(data) and not any(skip or tag in ('script', 'style') for tag, skip in self.stack):
                self.out.append(data.strip())
    c = Collect()
    c.feed(src)
    script = src[src.index("'use strict';"):]
    keys = c.out + [s for s in re.findall(r"'((?:[^'\\\n]|\\.)*)'", script) if cjk.search(s)]
    keys += re.findall(r"T\('((?:[^'\\]|\\.)*)'\)", (static / 'canvas-entry.js').read_text(encoding='utf-8'))
    keys += [u['label'] for u in genmedia.IMAGE_UPSCALERS if cjk.search(u['label'])] + ['agent·画板修图师']
    return list(dict.fromkeys(keys))


def test_canvas_strings_are_translated_in_every_dictionary():
    keys = canvas_ui_strings()
    assert len(keys) > 150 and '设为最终版' in keys and '🖌 画板' in keys and '画板' in keys
    dicts = sorted((Path(__file__).resolve().parents[1] / 'apps/web/static/i18n').glob('??.js'))
    assert len(dicts) >= 11
    for path in dicts:
        text = path.read_text(encoding='utf-8')
        missing = [k for k in keys if f"\n{json.dumps(k, ensure_ascii=False)}: " not in text]
        assert not missing, (path.name, missing)
