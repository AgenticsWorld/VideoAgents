from modules.prompt_layout import paragraphize, same_layout_insensitive
from modules.continuity_refs import layout_only


def test_paragraphize_breaks_before_anchors_and_is_idempotent():
    vp = ('Overall visual style: ink.。Shot 1: a {台词。} Shot 2｜标题。b. Scene presence references:\nX@Image 1: CHAR-X。\n'
          'End scene presence references.\nWhitebox reference: [Video 1] v. Whitebox legend: red = X. Shot plates: 【场景】A. '
          "Director's note (user instruction, must follow): n. Global constraints: no watermark.")
    out = paragraphize(vp)
    assert out.split('\n\n') == [
        'Overall visual style: ink.。', 'Shot 1: a {台词。}', 'Shot 2｜标题。b.',
        'Scene presence references:\nX@Image 1: CHAR-X。\nEnd scene presence references.',
        'Whitebox reference: [Video 1] v.', 'Whitebox legend: red = X.', 'Shot plates: 【场景】A.',
        "Director's note (user instruction, must follow): n.", 'Global constraints: no watermark.']
    assert paragraphize(out) == out and same_layout_insensitive(vp, out)


def test_paragraphize_keeps_inline_mentions_and_h3_field_head():
    vp = 'plate of Shot 1 (start) and [Shot 2] here; shot 3 of 4.\ndetailed_description:\nShot 1: x\n\n\n\nsummary: y'
    assert paragraphize(vp) == 'plate of Shot 1 (start) and [Shot 2] here; shot 3 of 4.\n\ndetailed_description:\nShot 1: x\n\nsummary: y'


def test_layout_only_difference_is_not_an_unsynced_continuation():
    before = {'refs': ['a.png'], 'video_prompt': 'Style. Shot 1: x. Global constraints: y.'}
    after = {'refs': ['a.png'], 'video_prompt': paragraphize(before['video_prompt'])}
    assert layout_only(before, after)
    assert not layout_only(before, {**after, 'refs': []})
