"""code/ 脚本共享:CLI 参数(--project/--ep/--out-root)与仓库路径定位。

import 本模块的副作用:把 modules/ 加入 sys.path,之后可直接
`from audiodsp import ...` / `from genmedia import ...`。
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("VIDEOAGENTS_DATA_DIR", REPO_ROOT / "data")).expanduser().resolve()
MODULES_DIR = REPO_ROOT / "modules"
if str(MODULES_DIR) not in sys.path:
    sys.path.insert(0, str(MODULES_DIR))
# modules/ 内部互相以 `from modules.xxx import` 引用(如 scene_cast → whitebox_refs),顶层直导 `from scene_cast import` 的
# CLI 也要能解析 `modules.*`,因此仓库根同样入 sys.path
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))


def reexec_with_host_python(argv=None):
    """当前解释器缺 Playwright 时,换宿主派单注入的解释器(VIDEOAGENTS_PYTHON=服务自身 sys.executable)重跑一次。

    前科(2026-09-20 fengshen3 ep06):codex 的登录 shell 里 python3 → Homebrew 3.13(无 Playwright),
    python → conda(齐全),Agent 写 python3 即报「缺少 Playwright」。须在 CLI 产生任何输出/副作用之前调用(各入口 parse_args 之后第一件事),免得 stdout 重复。
    不满足条件(有 Playwright / 未注入 / 已是宿主解释器 / 已重跑过)时原样返回,由后续 import 照常报错。
    """
    try:
        import playwright  # noqa: F401
        return
    except ImportError:
        pass
    host = os.environ.get('VIDEOAGENTS_PYTHON', '').strip()
    if not host or os.environ.get('VIDEOAGENTS_PYTHON_REEXEC') or not Path(host).is_file():
        return
    try:
        if os.path.samefile(host, sys.executable):
            return
    except OSError:
        pass
    print(f'{Path(sys.argv[0]).name}: {sys.executable} 缺少 Playwright,改用宿主解释器 {host} 重跑', file=sys.stderr, flush=True)
    sys.stdout.flush()
    code = subprocess.call([host, *(argv or sys.argv)], env={**os.environ, 'VIDEOAGENTS_PYTHON_REEXEC': '1'})
    sys.exit(code)


_NEGATIVE_VALUE = re.compile(r'^-\.?\d[\d.,eE+\-]*$')      # -8,5 / -1,0.85,0 / -.5,2:负数开头的坐标串


def _glue_negative_values(argv: list) -> list:
    """`--anchor -1,0.85,0`:argparse 只把纯数字(-1 / -0.5)认作负数值,带逗号的坐标串会被当成选项而报
    「expected one argument」。把「--选项 负数坐标串」并成 `--选项=值`;已写成 --opt=… 的、纯负数的原样不动。"""
    out = []
    for tok in argv:
        if out and _NEGATIVE_VALUE.match(tok) and ',' in tok and out[-1].startswith('--') and '=' not in out[-1]:
            out[-1] = f'{out[-1]}={tok}'
        else:
            out.append(tok)
    return out


def parse_args(desc: str = "", ep: bool = True, argv=None, configure=None):
    """统一入参。返回 (args, proj_root)。

    --project 缺省取 VIDEOAGENTS_PROJECT 环境变量(runtime 派单时注入),再退回 demo;
    --out-root 指定后产物写到该目录下(验证对拍走 scratch,不覆盖在库交付物);
    未指定时优先使用 $VIDEOAGENTS_DATA_DIR/projects/<project>,再退回仓库 data/projects。
    configure(ap) 可给脚本追加自有参数。
    无参调用与旧脚本硬编码 demo/ep01 的行为完全等价。
    """
    ap = argparse.ArgumentParser(description=desc)
    if configure:
        configure(ap)
    ap.add_argument("--project", default=os.environ.get("VIDEOAGENTS_PROJECT", "demo"),
                    help="项目名(data/projects/<project>),缺省 $VIDEOAGENTS_PROJECT 或 demo")
    if ep:
        ap.add_argument("--ep", default="ep01", help="集号,缺省 ep01")
    ap.add_argument("--out-root", default=None,
                    help="产物根目录,缺省 $VIDEOAGENTS_DATA_DIR/projects/<project>;验证时可指向 scratch")
    args = ap.parse_args(_glue_negative_values(sys.argv[1:] if argv is None else list(argv)))
    proj_root = Path(args.out_root) if args.out_root \
        else DATA_DIR / "projects" / args.project
    return args, proj_root


def project_output_setting(proj_root: Path, key: str, default=None):
    """读项目根 settings.json 的 output.<key>(Web 客户端「输出设置」,缺省回落 default)。"""
    try:
        import json
        st = json.loads((Path(proj_root) / "settings.json").read_text())
        return (st.get("output") or {}).get(key, default)
    except Exception:
        return default


def narration_enabled(proj_root: Path) -> bool:
    """项目输出设置「旁白」(output.narration_enabled,默认关;2026-09-16 起未配置过的存量项目也视为关):
    关=用户约定全片没有任何旁白,p5-narration/p8-narrator 不派发,narration 系列机检(§7D/§8B)一律跳过。"""
    return project_output_setting(proj_root, "narration_enabled") is True


def spatial_blocking_enabled(proj_root: Path) -> bool:
    """项目输出设置「白模/人物精确空间位置」(output.spatial_blocking,默认关,2026-09-16 起未配置过的存量项目也视为关;2026-09-07 起含义=用白模摄影机视角视频给视频生成定位人物):开=场景布局包流程(俯视图/九格图/layout.json 直接进 refs + 组级 blocking_map 数据)+ 白模链(scene-modeling/whitebox-staging,导出视频自动接成组 video_refs,机检 whitebox_ref_bound);
    关=单张场景概念图旧流程,scene_layout_pack_ok / blocking_map_present / layout_map_bound 等机检跳过。"""
    return project_output_setting(proj_root, "spatial_blocking") is True
