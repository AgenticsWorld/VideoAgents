from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from services.runtime import core, wechat

from . import __version__
from .runtime_bridge import install_runtime_store
from .schemas import (
    ApprovalAnswer,
    CapabilityResponse,
    HealthResponse,
    ProviderProbe,
)
from .state import RuntimeStore


ROOT = Path(__file__).resolve().parents[2]
STORE = RuntimeStore(core.RUNTIME_DIR / "runtime.sqlite3")
_bridge_installed = False


def _body(model: Any, **extra: Any) -> dict[str, Any]:
    return {**model.model_dump(exclude_none=True), **extra}


def _artifact_urls(value: Any, project: str) -> Any:
    old = f"/projects/{core.safe_slug(project)}/"
    new = f"/api/v1/projects/{core.safe_slug(project)}/artifacts/"
    if isinstance(value, str):
        return new + value[len(old):] if value.startswith(old) else value
    if isinstance(value, dict):
        return {key: _artifact_urls(item, project) for key, item in value.items()}
    if isinstance(value, list):
        return [_artifact_urls(item, project) for item in value]
    return value


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _bridge_installed
    if not _bridge_installed:
        install_runtime_store(STORE)
        _bridge_installed = True
    watchdog = asyncio.create_task(core.idle_watchdog())
    wechat_relay = asyncio.create_task(wechat.relay_loop())
    try:
        yield
    finally:
        watchdog.cancel()
        wechat_relay.cancel()


app = FastAPI(
    title="VideoAgents API",
    version=__version__,
    lifespan=lifespan,
    docs_url="/api/v1/docs",
    openapi_url="/api/v1/openapi.json",
)
api = APIRouter(prefix="/api/v1")


@app.exception_handler(core.ServiceError)
async def service_error_handler(_: Request, exc: core.ServiceError) -> JSONResponse:
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@api.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    return HealthResponse(version=__version__)


@api.get("/capabilities", response_model=CapabilityResponse, tags=["system"])
async def capabilities(request: Request) -> CapabilityResponse:
    host = request.client.host if request.client else ""
    return CapabilityResponse(local_backend=host in {"127.0.0.1", "::1", "localhost"})


@api.get("/projects", tags=["projects"])
async def projects() -> list[str]:
    return await core.api_projects()


@api.post("/projects", tags=["projects"])
async def create_project(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_projects_create(body)


@api.post("/projects/{project}", tags=["projects"])
async def delete_project(project: str, body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_projects_delete(body)


@api.get("/projects/{project}/brief", tags=["projects"])
async def get_brief(project: str) -> dict[str, Any]:
    return await core.api_brief_get(project)


@api.post("/projects/{project}/brief", tags=["projects"])
async def set_brief(project: str, body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_brief_set(body)


@api.get("/projects/{project}/config", tags=["projects"])
async def get_project_config(project: str) -> dict[str, Any]:
    return await core.api_projconfig_get(project)


@api.post("/projects/{project}/config", tags=["projects"])
async def set_project_config(project: str, body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_projconfig_set(body)


@api.get("/projects/{project}/references", tags=["artifacts"])
async def references(project: str) -> dict[str, Any]:
    return _artifact_urls(await core.api_refs_list(project), project)


@api.post("/projects/{project}/references", tags=["artifacts"])
async def upload_reference(
    project: str,
    request: Request,
    category: str = "style",
    filename: str = "",
    subdir: str = "",
) -> dict[str, Any]:
    data = await request.body()
    return _artifact_urls(
        await core.api_refs_upload(data, project, category, subdir, filename), project
    )


@api.post("/projects/{project}/references/note", tags=["artifacts"])
async def note_reference(project: str, body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_refs_note(body)


@api.post("/projects/{project}/references/delete", tags=["artifacts"])
async def delete_reference(project: str, body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_refs_delete(body)


@api.get("/projects/{project}/previews/{kind}", tags=["artifacts"])
async def preview(project: str, kind: str, ep: str = "") -> dict[str, Any]:
    handlers = {
        "characters": lambda: core.api_preview_characters(project),
        "props": lambda: core.api_preview_props(project),
        "scenes": lambda: core.api_preview_scenes(project),
        "worldview": lambda: core.api_preview_worldview(project),
        "storyboard": lambda: core.api_preview_storyboard(project, ep),
        "videos": lambda: core.api_preview_videos(project, ep),
        "workflow": lambda: core.api_preview_workflow(project),
    }
    if kind not in handlers:
        raise HTTPException(404, "unknown preview type")
    return _artifact_urls(await handlers[kind](), project)


@api.get("/projects/{project}/artifacts/{artifact_path:path}", tags=["artifacts"])
async def artifact(project: str, artifact_path: str) -> FileResponse:
    base = core.PROJECTS_DIR / core.safe_slug(project)
    path = (base / artifact_path).resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError as exc:
        raise HTTPException(400, "invalid artifact path") from exc
    if not path.is_file() or ".version" in path.parts:
        raise HTTPException(404, "artifact not found")
    return FileResponse(path)


@api.get("/projects/{project}/versions", tags=["versions"])
async def versions(project: str) -> dict[str, Any]:
    return await core.api_versions_log(project)


@api.post("/projects/{project}/versions/clone", tags=["versions"])
async def clone_version(project: str, body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_versions_clone(body)


@api.get("/projects/{project}/versions/clone", tags=["versions"])
async def clone_status(project: str) -> dict[str, Any]:
    return await core.api_versions_clone_status(project)


@api.get("/agents", tags=["agents"])
async def agents(refresh: bool = False) -> list[dict[str, Any]]:
    return await core.api_agents(refresh)


@api.get("/plugins", tags=["plugins"])
async def plugins() -> list[dict[str, Any]]:
    return await core.api_plugins()


@api.post("/plugins", tags=["plugins"])
async def install_plugin(request: Request) -> dict[str, Any]:
    return await core.api_plugins_upload(await request.body())


@api.post("/plugins/{name}", tags=["plugins"])
async def update_plugin(name: str, body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_plugins_toggle(body)


@api.post("/plugins/{name}/delete", tags=["plugins"])
async def delete_plugin(name: str, body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_plugins_delete(body)


@api.get("/agents/models", tags=["agents"])
async def agent_models() -> dict[str, Any]:
    return await core.api_agentmodels()


@api.post("/agents/models", tags=["agents"])
async def set_agent_model(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_agentmodels_set(body)


@api.get("/agents/{agent:path}/soul", tags=["agents"])
async def agent_soul(agent: str) -> dict[str, Any]:
    return await core.api_soul(agent)


@api.get("/projects/{project}/agents/{agent:path}/messages", tags=["runs"])
async def history(project: str, agent: str, limit: int = 200) -> list[dict[str, Any]]:
    return await core.api_history(agent, project, limit)


@api.get("/runs", tags=["runs"])
async def runs() -> list[dict[str, Any]]:
    return await core.api_runs()


@api.post("/runs", tags=["runs"])
async def create_run(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_chat(body)


@api.get("/runs/{run_id}", tags=["runs"])
async def run(run_id: str) -> dict[str, Any]:
    return await core.api_run(run_id)


@api.post("/runs/{run_id}/progress", tags=["runs"])
async def run_progress(run_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_run_progress(run_id, body)


@api.post("/runs/cancel", tags=["runs"])
async def cancel_runs() -> dict[str, Any]:
    return await core.api_stop_all()


@api.post("/runs/{run_id}/cancel", tags=["runs"])
async def cancel_run(run_id: str) -> dict[str, Any]:
    return await core.api_stop_run(run_id)


@api.get("/approvals", tags=["approvals"])
async def approvals() -> list[dict[str, Any]]:
    return await core.api_confirms()


@api.post("/approvals", tags=["approvals"])
async def create_approval(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_confirm_create(body)


@api.get("/approvals/{approval_id}", tags=["approvals"])
async def approval(approval_id: str) -> dict[str, Any]:
    return await core.api_confirm_get(approval_id)


@api.post("/approvals/{approval_id}/answer", tags=["approvals"])
async def answer_approval(approval_id: str, body: ApprovalAnswer) -> dict[str, Any]:
    return await core.api_confirm_answer(approval_id, _body(body))


@api.get("/config/generation", tags=["configuration"])
async def generation_config() -> dict[str, Any]:
    return await core.api_genconfig_get()


@api.get("/config/global-model", tags=["configuration"])
async def global_model() -> dict[str, Any]:
    return await core.api_globalmodel_get()


@api.post("/config/global-model", tags=["configuration"])
async def set_global_model(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_globalmodel_set(body)


@api.get("/config/ui-prefs", tags=["configuration"])
async def ui_prefs() -> dict[str, Any]:
    return await core.api_uiprefs_get()


@api.post("/config/ui-prefs", tags=["configuration"])
async def set_ui_prefs(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_uiprefs_set(body)


@api.post("/config/generation", tags=["configuration"])
async def set_generation_config(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_genconfig_set(body)


@api.get("/engines/{engine}/availability", tags=["configuration"])
async def engine_availability(engine: str) -> dict[str, Any]:
    return await core.api_enginecheck(engine)


@api.get("/providers/openrouter/models", tags=["providers"])
async def openrouter_models(modality: str = "image", refresh: bool = False) -> dict[str, Any]:
    return await core.api_openrouter_models(modality, refresh)


@api.post("/providers/openrouter/test", tags=["providers"])
async def test_openrouter(body: ProviderProbe) -> dict[str, Any]:
    return await core.api_test_openrouter(body.model_dump())


@api.post("/providers/comfyui/test", tags=["providers"])
async def test_comfyui(body: ProviderProbe) -> dict[str, Any]:
    return await core.api_test_comfyui(body.model_dump())


@api.get("/providers/deepagents/models", tags=["providers"])
async def deepagents_models() -> dict[str, Any]:
    return await core.api_deepagents_models()


@api.post("/providers/deepagents/test", tags=["providers"])
async def test_deepagents(body: ProviderProbe) -> dict[str, Any]:
    return await core.api_test_deepagents(body.model_dump())


@api.post("/providers/elevenlabs/voices", tags=["providers"])
async def elevenlabs_voices(body: ProviderProbe) -> dict[str, Any]:
    return await core.api_elevenlabs_voices(body.model_dump())


@api.post("/providers/elevenlabs/voices/add", tags=["providers"])
async def add_elevenlabs_voice(body: ProviderProbe) -> dict[str, Any]:
    return await core.api_elevenlabs_voice_add(body.model_dump())


@api.post("/providers/volcengine/speakers", tags=["providers"])
async def volcengine_speakers(body: ProviderProbe) -> dict[str, Any]:
    return await core.api_volc_speakers(body.model_dump())


@api.get("/resources", tags=["resources"])
async def resources(fresh: bool = False) -> dict[str, Any]:
    return await core.api_resources(fresh)


@api.get("/resources/usage", tags=["resources"])
async def usage() -> dict[str, Any]:
    return await core.api_usage()


@api.get("/resources/config", tags=["resources"])
async def resource_config() -> dict[str, Any]:
    return await core.api_resources_config_get()


@api.post("/resources/config", tags=["resources"])
async def set_resource_config(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_resources_config_set(body)


@api.get("/watchdog/policy", tags=["automation"])
async def watchdog_policy() -> dict[str, Any]:
    return await core.api_watchdog_threshold_get()


@api.post("/watchdog/policy", tags=["automation"])
async def set_watchdog_policy(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_watchdog_threshold_set(body)


@api.get("/config/concurrency", tags=["automation"])
async def agent_concurrency() -> dict[str, Any]:
    return await core.api_agent_concurrency_get()


@api.post("/config/concurrency", tags=["automation"])
async def set_agent_concurrency(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_agent_concurrency_set(body)


@api.get("/config/agent-memory", tags=["automation"])
async def agent_memory() -> dict[str, Any]:
    return await core.api_agent_memory_get()


@api.post("/config/agent-memory", tags=["automation"])
async def set_agent_memory(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_agent_memory_set(body)


@api.get("/projects/{project}/watchdog", tags=["automation"])
async def watchdog(project: str) -> dict[str, Any]:
    return await core.api_watchdog_get(project)


@api.post("/projects/{project}/watchdog", tags=["automation"])
async def set_watchdog(project: str, body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_watchdog_set(body)


@api.post("/draw-sessions", tags=["storyboard"])
async def draw_session(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_draw_session(body)


@api.get("/draw-sessions/{token}", tags=["storyboard"])
async def draw_info(token: str) -> dict[str, Any]:
    return await core.api_draw_info(token)


@api.post("/draw-sessions/{token}", tags=["storyboard"])
async def submit_draw(token: str, body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_draw_submit(token, body)


@api.post("/storyboard/notes", tags=["storyboard"])
async def storyboard_note(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_grpnote_set(body)


@api.post("/storyboard/refs", tags=["storyboard"])
async def storyboard_ref(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_grpref_add(body)


@api.get("/projects/{project}/storyboard/{ep}/{grp}/sketches", tags=["storyboard"])
async def sketches(project: str, ep: str, grp: str) -> list[dict[str, Any]]:
    return await core.api_sketches(project, ep, grp)


@api.delete("/projects/{project}/storyboard/{ep}/{grp}/sketches/{name}", tags=["storyboard"])
async def delete_sketch(project: str, ep: str, grp: str, name: str) -> dict[str, Any]:
    return await core.api_sketch_delete(project, ep, grp, name)


@api.get("/wechat/status", tags=["wechat"])
async def wechat_status() -> dict[str, Any]:
    return await wechat.api_wechat_status()


@api.post("/wechat/bind", tags=["wechat"])
async def wechat_bind(body: dict[str, Any]) -> dict[str, Any]:
    return await wechat.api_wechat_bind_start()


@api.get("/wechat/bind", tags=["wechat"])
async def wechat_bind_poll(qrcode: str = "") -> dict[str, Any]:
    return await wechat.api_wechat_bind_poll(qrcode)


@api.post("/wechat/unbind", tags=["wechat"])
async def wechat_unbind(body: dict[str, Any]) -> dict[str, Any]:
    return await wechat.api_wechat_unbind()


@api.get("/events", tags=["events"])
async def events() -> StreamingResponse:
    queue = core.HUB.subscribe()

    async def stream():
        try:
            yield 'data: {"type":"hello"}\n\n'
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            core.HUB.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


app.include_router(api)


@app.api_route(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def unknown_api(path: str) -> JSONResponse:
    return JSONResponse({"detail": f"Unknown API endpoint: /api/{path}"}, status_code=404)


def create_app() -> FastAPI:
    return app
