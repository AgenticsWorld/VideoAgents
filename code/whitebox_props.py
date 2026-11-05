#!/usr/bin/env python3
"""白模剧情道具挂手占位(2026-11-05,docs/whitebox.md「道具占位:code/whitebox_props.py」)。

用法:
  python code/whitebox_props.py --project <slug> --ep <ep> [grp…]            # 只列方案(不写文件)
  python code/whitebox_props.py --project <slug> --ep <ep> [grp…] --write    # 写进白模计划(先备份)

对 render_whitebox.py 报 props_coverage 缺项的组:道具库登记了主人、主人在本组且有手部通道的道具,生成一个挂在主人
手上的占位几何体(attach + ref,尺寸/颜色取道具库);其余列出原因。写入后本组机位审查指纹(placement)会失效,
须复核取景后用宿主 modules.whitebox_camera 重打,再按镜头表 content 补脱手/运动/回手关键帧。
"""
import json
import sys

from _common import parse_args, reexec_with_host_python  # noqa: E402  (code/ 在 sys.path 上)


def configure(ap):
    ap.add_argument('groups', nargs='*', help='组号;缺省整集')
    ap.add_argument('--write', action='store_true', help='把占位写进 directing/<ep>/whitebox_plans/<grp>.json(先备份)')


def main():
    args, base = parse_args(__doc__, configure=configure)
    reexec_with_host_python()
    from modules.whitebox_props import suggest, write
    result = suggest(base, args.ep, args.groups or None)
    out = {'groups': result['groups'], 'errors': result['errors'], 'written': []}
    if args.write:
        out['written'] = write(base, args.ep, result['groups'])
        if out['written']:
            out['next'] = ('计划已加道具:本组机位审查指纹(placement)会失效——复核取景与遮挡(--stills)后用 modules.whitebox_camera 重打,'
                           '再按镜头表 content 给占位补 attached 切换与运动关键帧,最后整集 render_whitebox.py --check-only / --compile-only')
    added = sum(len(r['added']) for r in result['groups'])
    skipped = sum(len(r['skipped']) for r in result['groups'])
    out['text'] = (f"[whitebox_props] {args.project}/{args.ep}: 可生成占位 {added} 个、需手工处理 {skipped} 个"
                   + (f";已写入 {len(out['written'])} 组" if args.write else ';未写文件(加 --write 写入)'))
    print(json.dumps(out, ensure_ascii=False, indent=1), flush=True)
    return 1 if result['errors'] and not result['groups'] else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(f'whitebox_props: {error}', file=sys.stderr)
        sys.exit(1)
