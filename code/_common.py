"""code/ 脚本共享:CLI 参数(--project/--ep/--out-root)与仓库路径定位。

import 本模块的副作用:把 modules/ 加入 sys.path,之后可直接
`from audiodsp import ...` / `from genmedia import ...`。
"""
import argparse
import os
import re
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

from host_python import ensure_host_python  # noqa: E402  (只用标准库)


def reexec_with_host_python(argv=None):
    """当前解释器缺依赖、或依赖装了却加载不了时,换宿主派单注入的解释器(VIDEOAGENTS_PYTHON=服务自身 sys.executable)重跑一次。

    判定与重跑见 modules/host_python.py(真 import 探测,按解释器缓存)。前科:2026-09-20 fengshen3 ep06 codex 的 python3 →
    Homebrew 3.13(缺 Playwright);2026-10-22 conda base 的 scipy 1.15.3 在 macOS 27 上 import scipy.signal 失败。
    code/ 下的 CLI 作主脚本 import 本模块时已自动调用一次(见文件末尾);各入口保留的显式调用重复执行无副作用。
    不满足条件(依赖都能加载 / 未注入 / 已是宿主环境 / 已重跑过)时原样返回,由后续 import 照常报错。
    """
    ensure_host_python(argv)


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


# import 即兜底:主脚本是 code/ 下的宿主 CLI 时,赶在它 import 第三方包之前换解释器
# (服务进程、pytest 里 import 本模块不触发;个别先 import 第三方包再 import 本模块的入口另行显式调用)
_MAIN_FILE = getattr(sys.modules.get("__main__"), "__file__", None)
if _MAIN_FILE and Path(_MAIN_FILE).resolve().parent == Path(__file__).resolve().parent:
    reexec_with_host_python()
