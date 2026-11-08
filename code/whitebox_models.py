#!/usr/bin/env python3
"""白模模型库(2026-11-08,docs/whitebox.md「自定义形状:连续软体 tube 与模型库 model」)。

  python code/whitebox_models.py --project <slug> list              # 列出模型库:零件数、铰链、包围尺寸、被哪些组引用
  python code/whitebox_models.py --project <slug> check [MDL…]      # 结构校验(缺省整库;另报计划引用了但库里没有的模型)
  python code/whitebox_models.py --project <slug> preview MDL…       # 出多角度预览 directing/whitebox_models/previews/<MDL>.jpg

模型文件 directing/whitebox_models/<MDL-…>.json 由白模调度按剧情编写(项目级,跨集复用);计划 props 写
"model": "MDL-…" 引用。preview 与预览 / 导出同一渲染器,旁边站一个 1.7 m 灰色参考人,有铰链时另出最小 / 最大两格;
依赖同 render_whitebox.py --stills(Playwright + Chromium),全本地渲染、无生成费用。改模型会让引用它的组机位审查指纹
(placement)与已导出白模视频失效——list 的 used_by 就是受影响的组。
"""
import json
import sys

from _common import parse_args, reexec_with_host_python  # noqa: E402  (code/ 在 sys.path 上)


def configure(ap):
    ap.add_argument('mode', choices=('list', 'check', 'preview'))
    ap.add_argument('models', nargs='*', help='模型 id(MDL-…);preview 必填,check 缺省整库')


def main():
    args, base = parse_args(__doc__, ep=False, configure=configure)
    reexec_with_host_python()
    from modules.whitebox_models import describe, render_previews
    if args.mode == 'preview':
        if not args.models:
            raise ValueError('preview 需要至少一个模型 id')
        checked = describe(base, args.models)
        if checked['errors']:
            print(json.dumps(checked, ensure_ascii=False, indent=1), flush=True)
            return 1
        sheets = render_previews(base, args.models, progress=lambda mid: print(f'{mid}: preview ok', flush=True))
        print(json.dumps({'previews': sheets, 'next': '用读图工具逐张看:形状、比例(对照 1.7 m 参考人)、铰链转向是否符合剧情'},
                         ensure_ascii=False, indent=1), flush=True)
        return 0
    result = describe(base, args.models or None)
    result['text'] = (f"[whitebox_models] {args.project}: {len(result['models'])} 个模型"
                      + (f",{len(result['errors'])} 个错误 -> FAIL" if result['errors'] else ' -> PASS'))
    print(json.dumps(result, ensure_ascii=False, indent=1), flush=True)
    return 1 if result['errors'] else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(f'whitebox_models: {error}', file=sys.stderr)
        sys.exit(1)
