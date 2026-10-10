#!/usr/bin/env python3
"""画板出图入口(2026-10-10;modules/image_canvas.py,docs/image_canvas.md)。画板页面发起的编辑 / 放大任务都经这里落成历史版本;
任务由页面建好(assets/canvas/<画板目录>/jobs/<任务号>/job.json:底图、带圈标注图、参考图、图像渠道 / 模型都已定),本脚本只管出图。

  python3 code/canvas_edit.py submit --project <slug> --doc <画板目录名> --job <任务号> --prompt-file <提示词文件>
  python3 code/canvas_edit.py submit --project <slug> --doc <画板目录名> --job <任务号> --prompt "<提示词>"
      画板修图 Agent(06-art/image-retouch)用:交提示词。用户勾了「发送前先看提示词」的任务只把提示词存为待确认(不出图,退出码 0),
      用户在画板确认后由宿主出图;没勾的直接按任务里选定的图像渠道 / 模型出图,落成一个新版本。
  python3 code/canvas_edit.py submit … --template      宿主用(直接发送):按任务里宿主拼好的模板提示词出图
  python3 code/canvas_edit.py submit … --confirmed     宿主用(用户确认了提示词):按任务里存的提示词出图
  python3 code/canvas_edit.py upscale --project <slug> --doc <画板目录名> --job <任务号>     宿主用:跑放大任务
  python3 code/canvas_edit.py show --project <slug> --file <项目内相对路径>                 看一张图的画板状态(JSON,只读;没有画板记录时退出码 1)
  python3 code/canvas_edit.py adopt --project <slug> --file <相对路径> --version v003 [--library-only]
      设为最终版(同页面按钮):固定路径的图写回原路径;分镜背景图登记为 <key>_revN 并把引用它的分镜改指过去(--library-only 只登记不替换)

图序固定:[Image 1] 底图、[Image 2] 带圈标注图(有圈选时)、其后是参考图(任务 images 的顺序),提示词里按这个编号引用。
一次只出一张,不多出候选;失败原样报告(任务记 failed,页面会显示原因),不自行换渠道 / 换模型重试。
退出码:0 完成(或已存为待确认);1 出错。
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import parse_args  # noqa: E402
from modules import image_canvas as ic  # noqa: E402


def _doc(base: Path, name: str) -> Path:
    if not re.fullmatch(r'[\w\-.]{4,160}', name or '') or '..' in name:
        raise ic.CanvasError('--doc 不是合法的画板目录名')
    doc = base / ic.ROOT_REL / name
    if not (doc / 'history.json').is_file():
        raise ic.CanvasError(f'没有这个画板:{name}', 404)
    return doc


def _out(payload: dict) -> None:
    print(json.dumps({'canvas_edit': payload}, ensure_ascii=False), flush=True)


def main() -> int:
    def configure(ap):
        ap.add_argument('action', choices=['submit', 'upscale', 'show', 'adopt'])
        ap.add_argument('--doc', default='', help='画板目录名(assets/canvas/ 下)')
        ap.add_argument('--job', default='', help='任务号')
        ap.add_argument('--prompt', default='', help='编辑提示词(英文;与 --prompt-file 二选一)')
        ap.add_argument('--prompt-file', default='', help='编辑提示词文件')
        ap.add_argument('--template', action='store_true', help='宿主用:按任务里的模板提示词出图')
        ap.add_argument('--confirmed', action='store_true', help='宿主用:按任务里用户确认过的提示词出图')
        ap.add_argument('--file', default='', help='show / adopt:图片的项目内相对路径')
        ap.add_argument('--version', default='', help='adopt:版本号 vNNN')
        ap.add_argument('--library-only', action='store_true', help='adopt 分镜背景图:只登记进库,不替换引用它的分镜')
    args, base = parse_args(__doc__, ep=False, configure=configure)
    log = lambda m: print(m, file=sys.stderr, flush=True)   # noqa: E731
    try:
        if args.action == 'show':
            doc, h = ic.open_doc(base, args.file, create=False)
            st = ic.state(base, doc, h)
            _out({k: st[k] for k in ('file', 'doc', 'kind', 'storage', 'final', 'disk', 'info', 'job', 'last_job')}
                 | {'versions': [{k: v.get(k) for k in ('id', 'op', 'parent', 'width', 'height', 'created_at', 'provider', 'model', 'plate_key')}
                                 for v in st['versions']]})
            return 0
        if args.action == 'adopt':
            doc, _ = ic.open_doc(base, args.file, create=False)
            _out(ic.adopt(base, doc, args.version, repoint=not args.library_only, log=log))
            return 0
        doc = _doc(base, args.doc)
        job = ic.load_job(doc, args.job)
        if args.action == 'upscale':
            if job.get('status') != 'queued':
                raise ic.CanvasError(f"任务状态是 {job.get('status')},不能出图")
            v = ic.run_upscale(base, doc, args.job, log=log)
            _out({'job': args.job, 'status': 'done', 'version': v['id'], 'size': f"{v['width']}x{v['height']}"})
            return 0
        if job.get('op') != 'edit':
            raise ic.CanvasError(f'{args.job} 不是编辑任务')
        if args.template or args.confirmed:
            if job.get('status') != 'queued':
                raise ic.CanvasError(f"任务状态是 {job.get('status')},不能出图")
            prompt = job.get('template_prompt') if args.template else job.get('prompt')
        else:
            prompt = Path(args.prompt_file).read_text(encoding='utf-8') if args.prompt_file else args.prompt
            if not (prompt or '').strip():
                raise ic.CanvasError('缺少提示词(--prompt 或 --prompt-file)')
            if job.get('status') != 'drafting':
                raise ic.CanvasError(f"任务状态是 {job.get('status')},不再接收提示词(用户可能已取消,或已提交过)。不要重试,原样报告")
            if job.get('preview'):
                ic.set_job(doc, args.job, status='awaiting_confirm', prompt=prompt.strip())
                log('[canvas] 用户勾了「发送前先看提示词」:提示词已存为待确认,用户在画板确认后由宿主出图。本单到此结束,不要再出图。')
                _out({'job': args.job, 'status': 'awaiting_confirm'})
                return 0
        v = ic.run_edit(base, doc, args.job, prompt, log=log)
        _out({'job': args.job, 'status': 'done', 'version': v['id'], 'size': f"{v['width']}x{v['height']}",
              'provider': v.get('provider'), 'model': v.get('model'), 'lock': v.get('lock')})
        return 0
    except ic.CanvasError as e:
        print(f'错误:{e}', file=sys.stderr, flush=True)
        return 1
    except Exception as e:  # noqa: BLE001  出图失败(渠道 / Key / 审核拒收…):任务已记 failed,原因原样给出
        print(f'错误:{e}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())
