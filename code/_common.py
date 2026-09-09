"""code/ 脚本共享:CLI 参数(--project/--ep/--out-root)与仓库路径定位。

import 本模块的副作用:把 modules/ 加入 sys.path,之后可直接
`from audiodsp import ...` / `from genmedia import ...`。
"""
import argparse
import os
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
    args = ap.parse_args(argv)
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
    """项目输出设置「旁白」(output.narration_enabled,默认开):关=用户约定全片没有任何旁白,
    p5-narration/p8-narrator 不派发,narration 系列机检(§7D/§8B)一律跳过。"""
    return project_output_setting(proj_root, "narration_enabled", True) is not False


def spatial_blocking_enabled(proj_root: Path) -> bool:
    """项目输出设置「人物精确空间位置」(output.spatial_blocking,默认开;2026-09-07 起含义=用白模摄影机视角视频给视频生成定位人物):开=场景布局包流程(俯视图/九格图/layout.json 直接进 refs + 组级 blocking_map 数据)+ 白模链(scene-modeling/whitebox-staging,导出视频自动接成组 video_refs,机检 whitebox_ref_bound);
    关=单张场景概念图旧流程,scene_layout_pack_ok / blocking_map_present / layout_map_bound 等机检跳过。"""
    return project_output_setting(proj_root, "spatial_blocking", True) is not False
