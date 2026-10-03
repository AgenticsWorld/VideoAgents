# -*- coding: utf-8 -*-
"""HTTP 层回归:路由表不互相遮蔽 + 空数据目录下 GET 冒烟 + 网页入口页都在。

业务逻辑的测试大多直接调 core 函数,HTTP 这一层(路由注册顺序、参数绑定、异常转状态码)此前没有覆盖:
  - 前科(2026-09-23 导入项目):`/projects/import` 注册在 `/projects/{project}` 之后,请求被参数路由吃掉;
  - GET 应当只读:对不存在的项目发 GET 不得把项目目录建出来(否则打错 / 已删的项目名变成空项目出现在列表里)。
冒烟在子进程里跑——DATA_DIR / HOME 指向临时目录、PATH 收窄、禁用网络,不碰本机真实项目与外部服务;
引擎 CLI 不在 PATH、模型目录拉不到时接口应返回带 detail 的 4xx/502/503,而不是未捕获异常(500)。
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import warnings
from collections import namedtuple

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------- 路由表

FlatRoute = namedtuple("FlatRoute", "path methods path_regex path_format include_in_schema")
HTTP_METHODS = {"get", "post", "put", "delete", "patch", "options", "head", "trace"}


def flat_routes(app):
    """按匹配顺序展开全部 API 路由(完整路径,含 include_router 的前缀)。
    FastAPI 旧版 include_router 把子路由摊平进 app.routes;新版(实测 0.142)留成一个节点、匹配时才展开,
    节点的 effective_candidates() 给出带完整路径的条目。展开得对不对由 test_route_table_matches_openapi 用公开的 OpenAPI 对账。"""
    out = []

    def walk(routes):
        for r in routes:
            if isinstance(r, APIRoute) or isinstance(getattr(r, "original_route", None), APIRoute):
                out.append(FlatRoute(r.path, frozenset(r.methods), r.path_regex, r.path_format, r.include_in_schema))
            elif hasattr(r, "effective_candidates"):
                walk(r.effective_candidates())

    walk(app.routes)
    return out


def _openapi_operations(app):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        spec = app.openapi()
    return {(path, method) for path, ops in spec["paths"].items() for method in ops if method in HTTP_METHODS}


def _apps():
    from apps.web import server
    from services.api import app as api_app
    return {"api": api_app.app, "web": server.app, "draw": server.draw_app}


@pytest.mark.parametrize("name", ["api", "web", "draw"])
def test_route_table_matches_openapi(name):
    # 本文件其余检查都建立在 flat_routes 之上:它漏了路由,遮蔽检查就会空跑。FastAPI 再改内部结构时在这里报错
    app = _apps()[name]
    flat = {(r.path_format, m.lower()) for r in flat_routes(app) if r.include_in_schema for m in r.methods}
    assert flat == _openapi_operations(app)


# ---------------------------------------------------------------- 路由遮蔽

def shadowed_routes(app):
    """后注册的路由若被先注册的同方法路由整个匹配走,就永远到不了。
    判定:把后者路径里的参数代入占位值得到一条具体路径,看更早的路由能否匹配它。返回 [(方法, 先注册, 后注册)]。"""
    routes = flat_routes(app)
    found = []
    for j, later in enumerate(routes):
        sample = re.sub(r"\{[^}]+\}", "zz9", later.path)
        for earlier in routes[:j]:
            methods = earlier.methods & later.methods
            if methods and earlier.path_regex.match(sample):
                found.append((sorted(methods), earlier.path, later.path))
                break
    return found


def test_detector_catches_param_route_registered_first():
    app = FastAPI()

    @app.get("/projects/{project}")
    def one(project: str):
        return project

    @app.post("/projects/{project}")
    def create(project: str):
        return project

    @app.get("/projects/import")          # 被上面的 GET 吃掉
    def imp():
        return "import"

    @app.put("/projects/import")          # 方法不同,不算遮蔽
    def put_imp():
        return "import"

    assert shadowed_routes(app) == [(["GET"], "/projects/{project}", "/projects/import")]


def test_detector_catches_path_converter_and_duplicates():
    app = FastAPI()

    @app.get("/files/{path:path}")
    def files(path: str):
        return path

    @app.get("/files/thumb/{name}")       # {path:path} 会吞掉后面所有层级
    def thumb(name: str):
        return name

    @app.get("/health")
    def health():
        return "a"

    @app.get("/health")                   # 重复注册:后者不可达
    def health_again():
        return "b"

    assert [(a, b) for _m, a, b in shadowed_routes(app)] == [("/files/{path:path}", "/files/thumb/{name}"), ("/health", "/health")]


def test_detector_sees_through_included_router():
    # 真实布局:路由挂在带前缀的 APIRouter 上再 include 进 app
    app, api = FastAPI(), APIRouter(prefix="/api/v1")

    @api.get("/projects/{project}")
    def one(project: str):
        return project

    @api.get("/projects/import")
    def imp():
        return "import"

    app.include_router(api)

    @app.get("/api/v1/projects/{project}/late")     # 直接挂在 app 上、排在 include 之后,不受影响
    def late(project: str):
        return project

    assert [r.path for r in flat_routes(app)] == ["/api/v1/projects/{project}", "/api/v1/projects/import",
                                                  "/api/v1/projects/{project}/late"]
    assert shadowed_routes(app) == [(["GET"], "/api/v1/projects/{project}", "/api/v1/projects/import")]


def test_detector_accepts_static_before_param():
    app = FastAPI()

    @app.get("/projects/import")
    def imp():
        return "import"

    @app.get("/projects/{project}")
    def one(project: str):
        return project

    assert shadowed_routes(app) == []


def test_api_routes_not_shadowed():
    app = _apps()["api"]
    assert len(flat_routes(app)) > 200     # 确实拿到了完整路由表
    assert shadowed_routes(app) == []


def test_web_routes_not_shadowed():
    assert shadowed_routes(_apps()["web"]) == []
    assert shadowed_routes(_apps()["draw"]) == []


# ---------------------------------------------------------------- GET 冒烟(隔离子进程)

EXISTING, MISSING = "demo", "ghost"
MIN_COVERAGE = 0.8      # 参数名都能代入的 GET 路由占比下限;新增路径参数名后掉到线下 = 该把它补进 _SMOKE 的 FILL

_SMOKE = r'''
import json, re, shutil, socket, sys, time, warnings

def _blocked(*a, **k):
    raise OSError("network disabled in smoke test")

socket.socket.connect = _blocked
socket.socket.connect_ex = _blocked
socket.create_connection = _blocked

from fastapi.testclient import TestClient
from services.api import app as api_app
from services.runtime import core

existing, missing = sys.argv[1], sys.argv[2]
(core.PROJECTS_DIR / existing).mkdir(parents=True, exist_ok=True)
FILL = {"ep": "ep01", "grp": "grp001", "gid": "grp001", "sid": "SCN-0001", "shot_id": "sh001", "kind": "characters", "v": "1"}
SKIP = {"/api/v1/events"}      # SSE 长连接,不会自己结束
client = TestClient(api_app.app, raise_server_exceptions=False)      # 不进 lifespan:不起看门狗 / 各渠道 relay
with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    spec = api_app.app.openapi()      # 公开接口清单(不含 include_in_schema=False 的兜底路由)
gets = [p for p, ops in spec["paths"].items() if "get" in ops and p not in SKIP]
rows, skipped = [], []
for path in gets:
    params = re.findall(r"\{([^}]+)\}", path)
    if any(p != "project" and p not in FILL for p in params):
        skipped.append(path)
        continue
    for project in ([existing, missing] if "project" in params else [""]):
        fill = dict(FILL, project=project)
        url = re.sub(r"\{([^}]+)\}", lambda m: fill[m.group(1)], path)
        ghost = core.PROJECTS_DIR / missing
        t = time.time()
        try:
            resp = client.get(url)
            code, body = resp.status_code, resp.text[:300]
        except Exception as e:  # noqa: BLE001
            code, body = -1, repr(e)[:300]
        created = ghost.exists()
        if created:
            shutil.rmtree(ghost)
        rows.append({"path": path, "url": url, "project": project, "code": code, "body": body,
                     "created": created, "s": round(time.time() - t, 2)})
print("SMOKE_JSON=" + json.dumps({"total": len(gets), "skipped": skipped, "rows": rows}))
'''


@pytest.fixture(scope="module")
def smoke(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("api-smoke")
    (tmp / "home").mkdir()
    env = {
        "HOME": str(tmp / "home"), "USERPROFILE": str(tmp / "home"),
        "PATH": os.pathsep.join([str(Path(sys.executable).parent), "/usr/bin", "/bin"]),
        "VIDEOAGENTS_DATA_DIR": str(tmp / "data"),
        "PYTHONPATH": os.pathsep.join([str(ROOT)] + [p for p in sys.path if p]),     # 与当前解释器同一套可导入包
        "PYTHONIOENCODING": "utf-8",
    }
    for key in ("SYSTEMROOT", "TMPDIR", "TEMP", "TMP"):
        if key in os.environ:
            env[key] = os.environ[key]
    proc = subprocess.run([sys.executable, "-c", _SMOKE, EXISTING, MISSING], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("SMOKE_JSON=")), None)
    assert proc.returncode == 0 and line, f"冒烟子进程失败(exit {proc.returncode}):\n{proc.stderr[-3000:]}"
    return json.loads(line[len("SMOKE_JSON="):])


def _fmt(rows):
    return "\n".join(f"  {r['code']} {r['url']}  {r['body'][:160]}" for r in rows)


def test_smoke_covers_most_get_routes(smoke):
    hit = {r["path"] for r in smoke["rows"]}
    assert len(hit) >= MIN_COVERAGE * smoke["total"], f"只覆盖 {len(hit)}/{smoke['total']} 条 GET;未代入:{smoke['skipped']}"
    assert any(r["project"] == EXISTING for r in smoke["rows"]) and any(r["project"] == MISSING for r in smoke["rows"])


def test_get_never_raises_unhandled(smoke):
    bad = [r for r in smoke["rows"] if r["code"] == 500 or r["code"] < 0]
    assert not bad, "GET 抛出未捕获异常:\n" + _fmt(bad)


def test_get_errors_carry_detail(smoke):
    # 4xx / 5xx 都应是带 detail 的 JSON(ServiceError / HTTPException / 参数校验),前端按 detail 提示
    bad = []
    for r in smoke["rows"]:
        if r["code"] >= 400 and r["code"] != 500:
            try:
                ok = "detail" in json.loads(r["body"])
            except ValueError:
                ok = False
            if not ok:
                bad.append(r)
    assert not bad, "错误响应缺 detail:\n" + _fmt(bad)


def test_get_does_not_create_missing_project(smoke):
    bad = [r for r in smoke["rows"] if r["created"]]
    assert not bad, "GET 把不存在的项目目录建了出来:\n" + _fmt(bad)


def test_get_responds_quickly_offline(smoke):
    # 断网 / 无 CLI 时应立即失败返回,不该卡在重试或超时上
    slow = [r for r in smoke["rows"] if r["s"] > 10]
    assert not slow, "GET 过慢:\n" + "\n".join(f"  {r['s']}s {r['url']}" for r in slow)


# ---------------------------------------------------------------- 网页入口页

@pytest.fixture(scope="module")
def web():
    from apps.web import server
    return server, TestClient(server.app)      # 不用 with:不进 lifespan(否则会去拉起 API 进程)


def test_every_page_route_serves_html(web):
    server, client = web
    pages = [r.path for r in flat_routes(server.app)
             if "GET" in r.methods and "{" not in r.path and not r.path.startswith("/api/")]
    assert "/" in pages and len(pages) >= 10
    for path in pages:
        resp = client.get(path)
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/html"), path


def test_every_preview_page_exists(web):
    server, client = web
    for page in sorted(server.PREVIEW_FILES):
        assert client.get(f"/preview/{page}").status_code == 200, page
    assert client.get("/preview/nope").status_code == 404


def test_draw_page_requires_token_shape(web):
    _server, client = web
    assert client.get("/draw/" + "a" * 32).status_code == 200
    assert client.get("/draw/not-a-token").status_code == 404
