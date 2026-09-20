#!/usr/bin/env python3
"""改已有图片(镜像 / 旋转 / 裁切 / 缩放)并**按原编码**写回,或把超大的真 PNG 归一成 JPEG 字节(文件名不变)。

为什么要有它:本库的图像资产按契约一律叫 *.png(layout_top.png、sheet、概念图…,几百处引用),而图像模型(Seedream 等)返回的是
JPEG 字节,genmedia 原样落盘——所以「.png」只是名字,内容以文件头为准(浏览器、PIL、各渠道都按内容识别)。Agent 自己用
PIL 改图再 `im.save('x.png')` 会按扩展名存成**真 PNG**:2560×1440 的实拍质感图 4–5 MB(原 0.3–0.6 MB),作为参考图内联进方舟
images/generations 会因请求体过大被拒(HTTP 400 Error when parsing request,2026-09-20 fengshen3 SCN-0036)。

  python code/image_edit.py <图片路径> --flip-h                 # 水平镜像(--flip-v 垂直;--rotate 90|180|270;--crop x0,y0,x1,y1;--resize WxH)
  python code/image_edit.py <图片路径>… --normalize             # 超过 --max-kb(默认 2048)的无透明真 PNG → JPEG 字节,文件名不变
  python code/image_edit.py <图片路径>… --check                 # 只报编码 / 体积,不改;有超限真 PNG 时退出码 1

写回前原件备份到 --backup-dir(缺省:同目录 candidates/,没有则同目录)下 <名>.<时间>.orig<原扩展名>;--no-backup 关闭。
带透明通道的 PNG(抠图、叠加层)保持 PNG 不转。
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageFile

JPEG_QUALITY = 92


def encoding_of(data: bytes) -> str:
    return 'JPEG' if data[:3] == b'\xff\xd8\xff' else 'PNG' if data[:8] == b'\x89PNG\r\n\x1a\n' else 'WEBP' if data[:4] == b'RIFF' else 'OTHER'


def has_alpha(im: Image.Image) -> bool:
    return im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info)


def encode(im: Image.Image, fmt: str) -> bytes:
    buf = io.BytesIO()
    if fmt == 'JPEG':
        ImageFile.MAXBLOCK = max(ImageFile.MAXBLOCK, im.size[0] * im.size[1] * 4)   # optimize=True 对高熵大图会撑爆默认缓冲(broken data stream)
        im.convert('RGB').save(buf, 'JPEG', quality=JPEG_QUALITY, optimize=True, subsampling=0)
    else:
        im.save(buf, fmt if fmt in ('PNG', 'WEBP') else 'PNG')
    return buf.getvalue()


def backup(path: Path, backup_dir: Path | None) -> Path:
    dest = backup_dir or (path.parent / 'candidates' if (path.parent / 'candidates').is_dir() else path.parent)
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / f"{path.stem}.{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.orig{path.suffix}"
    shutil.copy2(path, out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('paths', nargs='+')
    ap.add_argument('--flip-h', action='store_true'); ap.add_argument('--flip-v', action='store_true')
    ap.add_argument('--rotate', type=int, choices=(90, 180, 270))
    ap.add_argument('--crop', help='x0,y0,x1,y1 像素'); ap.add_argument('--resize', help='WxH 像素')
    ap.add_argument('--normalize', action='store_true'); ap.add_argument('--check', action='store_true')
    ap.add_argument('--max-kb', type=int, default=2048)
    ap.add_argument('--backup-dir'); ap.add_argument('--no-backup', action='store_true')
    args = ap.parse_args()
    editing = args.flip_h or args.flip_v or args.rotate or args.crop or args.resize
    if not (editing or args.normalize or args.check):
        ap.error('没有指定操作')
    bad = 0
    for raw in args.paths:
        path = Path(raw)
        if not path.is_file():
            print(f'错误:文件不存在 {path}', file=sys.stderr); return 1
        data = path.read_bytes(); fmt = encoding_of(data); im = Image.open(io.BytesIO(data)); im.load()
        oversize = fmt == 'PNG' and not has_alpha(im) and len(data) > args.max_kb * 1024
        if args.check:
            print(f"{'FAIL' if oversize else 'ok  '} {path}  内容={fmt} {im.size[0]}x{im.size[1]} {len(data) / 1024:.0f} KB"
                  + ('  ← 无透明真 PNG 超限,跑 --normalize' if oversize else ''))
            bad += oversize
            continue
        out_fmt = fmt if fmt in ('JPEG', 'PNG', 'WEBP') else 'PNG'
        if editing:
            if args.flip_h: im = im.transpose(Image.FLIP_LEFT_RIGHT)
            if args.flip_v: im = im.transpose(Image.FLIP_TOP_BOTTOM)
            if args.rotate: im = im.rotate(-args.rotate, expand=True)
            if args.crop: im = im.crop(tuple(int(v) for v in args.crop.split(',')))
            if args.resize: im = im.resize(tuple(int(v) for v in args.resize.lower().split('x')), Image.LANCZOS)
        if (args.normalize or editing) and out_fmt == 'PNG' and not has_alpha(im):
            probe = encode(im, 'PNG') if editing else data
            if len(probe) > args.max_kb * 1024:
                out_fmt = 'JPEG'
        if not editing and out_fmt == fmt:
            print(f'跳过 {path}:内容={fmt} {len(data) / 1024:.0f} KB,无需归一'); continue
        new = encode(im, out_fmt)
        kept = None if args.no_backup else backup(path, Path(args.backup_dir) if args.backup_dir else None)
        path.write_bytes(new)
        print(f"已写回 {path}:内容 {fmt}→{out_fmt} {len(data) / 1024:.0f}→{len(new) / 1024:.0f} KB {im.size[0]}x{im.size[1]}"
              + (f';原件备份 {kept}' if kept else ''))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
