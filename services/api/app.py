from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from services.runtime import core

from . import __version__
from .runtime_bridge import install_runtime_store
from .schemas import (
    AgentModelUpdate,
    ApprovalAnswer,
    ApprovalCreate,
    ApprovalCreated,
    BriefUpdate,
    CapabilityResponse,
    DrawSessionCreate,
    DrawSubmit,
    GenerationConfigUpdate,
    GlobalModelUpdate,
    HealthResponse,
    ProjectConfigUpdate,
    ProjectCreate,
    ProjectDelete,
    PluginUpdate,
    ProviderProbe,
    ReferenceDelete,
    ReferenceNote,
    ResourceConfigUpdate,
    RunCreate,
    RunCreated,
    RunProgress,
    SketchNote,
    VersionClone,
    WatchdogPolicyUpdate,
    WatchdogUpdate,
)
from .state import RuntimeStore


ROOT = Path(__file__).resolve().parents[2]
STORE = RuntimeStore(core.RUNTIME_DIR / "runtime.sqlite3")
_bridge_installed = False


def _body(model: Any, **extra: Any) -> dict[str, Any]:
    return {**model.model_dump(exclude_none=True), **extra}


def _is_secret_key(key: str) -> bool:
    normalized = key.lower()
    return (any(token in normalized for token in ("key", "token", "secret", "password", "credential"))
            or normalized.endswith(("_ak", "_sk")))


def _redact(value: Any, key: str = "") -> Any:
    secret = _is_secret_key(key)
    if secret and isinstance(value, str):
        return ""
    if isinstance(value, dict):
        return {k: _redact(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


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


def _preserve_secrets(value: Any, key: str = "") -> Any:
    """Omit blank secret fields so editing redacted configuration is non-destructive."""
    secret = _is_secret_key(key)
    if secret and value == "":
        return None
    if isinstance(value, dict):
        return {k: cleaned for k, item in value.items()
                if (cleaned := _preserve_secrets(item, k)) is not None}
    if isinstance(value, list):
        return [_preserve_secrets(item) for item in value]
    return value


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _bridge_installed
    if not _bridge_installed:
        install_runtime_store(STORE)
        _bridge_installed = True
    watchdog = asyncio.create_task(core.idle_watchdog())
    try:
        yield
    finally:
        watchdog.cancel()


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


@api.post("/projects", status_code=201, tags=["projects"])
async def create_project(body: ProjectCreate) -> dict[str, Any]:
    payload = _body(body)
    payload["settings"] = {
        "duration": {
            "episode_minutes": payload.pop("episode_minutes"),
            "shot_min_s": payload.pop("shot_min_s"),
            "shot_max_s": payload.pop("shot_max_s"),
        }
    }
    return await core.api_projects_create(payload)


@api.delete("/projects/{project}", tags=["projects"])
async def delete_project(project: str, body: ProjectDelete) -> dict[str, Any]:
    return await core.api_projects_delete({"project": project, "confirm": body.confirmation})


@api.get("/projects/{project}/brief", tags=["projects"])
async def get_brief(project: str) -> dict[str, Any]:
    return await core.api_brief_get(project)


@api.put("/projects/{project}/brief", tags=["projects"])
async def set_brief(project: str, body: BriefUpdate) -> dict[str, Any]:
    return await core.api_brief_set(_body(body, project=project))


@api.get("/projects/{project}/config", tags=["projects"])
async def get_project_config(project: str) -> dict[str, Any]:
    return await core.api_projconfig_get(project)


@api.patch("/projects/{project}/config", tags=["projects"])
async def set_project_config(project: str, body: ProjectConfigUpdate) -> dict[str, Any]:
    return await core.api_projconfig_set({**body.model_dump(exclude_none=True), "project": project})


@api.get("/projects/{project}/references", tags=["artifacts"])
async def references(project: str) -> dict[str, Any]:
    return _artifact_urls(await core.api_refs_list(project), project)


@api.post("/projects/{project}/references", status_code=201, tags=["artifacts"])
async def upload_reference(
    project: str,
    request: Request,
    category: str = "style",
    name: str = "",
    subdir: str = "",
) -> dict[str, Any]:
    data = await request.body()
    return _artifact_urls(
        await core.api_refs_upload(data, project, category, subdir, name), project
    )


@api.put("/projects/{project}/references/note", tags=["artifacts"])
async def note_reference(project: str, body: ReferenceNote) -> dict[str, Any]:
    return await core.api_refs_note(_body(body, project=project))


@api.delete("/projects/{project}/references", tags=["artifacts"])
async def delete_reference(project: str, body: ReferenceDelete) -> dict[str, Any]:
    return await core.api_refs_delete(_body(body, project=project))


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


@api.post("/projects/{project}/versions/clone", status_code=202, tags=["versions"])
async def clone_version(project: str, body: VersionClone) -> dict[str, Any]:
    return await core.api_versions_clone(_body(body, project=project))


@api.get("/projects/{project}/versions/clone", tags=["versions"])
async def clone_status(project: str) -> dict[str, Any]:
    return await core.api_versions_clone_status(project)


@api.get("/agents", tags=["agents"])
async def agents(refresh: bool = False) -> list[dict[str, Any]]:
    return await core.api_agents(refresh)


@api.get("/plugins", tags=["plugins"])
async def plugins() -> list[dict[str, Any]]:
    return await core.api_plugins()


@api.post("/plugins", status_code=201, tags=["plugins"])
async def install_plugin(request: Request) -> dict[str, Any]:
    return await core.api_plugins_upload(await request.body())


@api.put("/plugins/{name}", tags=["plugins"])
async def update_plugin(name: str, body: PluginUpdate) -> dict[str, Any]:
    return await core.api_plugins_toggle({"name": name, "enabled": body.enabled})


@api.delete("/plugins/{name}", tags=["plugins"])
async def delete_plugin(name: str) -> dict[str, Any]:
    return await core.api_plugins_delete({"name": name})


@api.get("/agents/models", tags=["agents"])
async def agent_models() -> dict[str, Any]:
    return await core.api_agentmodels()


@api.put("/agents/models", tags=["agents"])
async def set_agent_model(body: AgentModelUpdate) -> dict[str, Any]:
    return await core.api_agentmodels_set(_body(body))


@api.get("/agents/{agent:path}/soul", tags=["agents"])
async def agent_soul(agent: str) -> dict[str, Any]:
    return await core.api_soul(agent)


@api.get("/projects/{project}/agents/{agent:path}/messages", tags=["runs"])
async def history(project: str, agent: str, limit: int = 200) -> list[dict[str, Any]]:
    return await core.api_history(agent, project, limit)


@api.get("/runs", tags=["runs"])
async def runs() -> list[dict[str, Any]]:
    return await core.api_runs()


@api.post("/runs", response_model=RunCreated, status_code=202, tags=["runs"])
async def create_run(body: RunCreate) -> RunCreated:
    result = await core.api_chat(_body(body))
    return RunCreated(run_id=result["run_id"])


@api.get("/runs/{run_id}", tags=["runs"])
async def run(run_id: str) -> dict[str, Any]:
    return await core.api_run(run_id)


@api.put("/runs/{run_id}/progress", tags=["runs"])
async def run_progress(run_id: str, body: RunProgress) -> dict[str, Any]:
    return await core.api_run_progress(run_id, _body(body))


@api.post("/runs/cancel", tags=["runs"])
async def cancel_runs() -> dict[str, Any]:
    return await core.api_stop_all()


@api.get("/approvals", tags=["approvals"])
async def approvals() -> list[dict[str, Any]]:
    return await core.api_confirms()


@api.post("/approvals", response_model=ApprovalCreated, status_code=201, tags=["approvals"])
async def create_approval(body: ApprovalCreate) -> ApprovalCreated:
    result = await core.api_confirm_create(_body(body))
    return ApprovalCreated(approval_id=result["confirm_id"])


@api.get("/approvals/{approval_id}", tags=["approvals"])
async def approval(approval_id: str) -> dict[str, Any]:
    return await core.api_confirm_get(approval_id)


@api.post("/approvals/{approval_id}/answer", tags=["approvals"])
async def answer_approval(approval_id: str, body: ApprovalAnswer) -> dict[str, Any]:
    return await core.api_confirm_answer(approval_id, _body(body))


@api.get("/config/generation", tags=["configuration"])
async def generation_config() -> dict[str, Any]:
    return _redact(await core.api_genconfig_get())


@api.get("/config/global-model", tags=["configuration"])
async def global_model() -> dict[str, Any]:
    return await core.api_globalmodel_get()


@api.put("/config/global-model", tags=["configuration"])
async def set_global_model(body: GlobalModelUpdate) -> dict[str, Any]:
    return await core.api_globalmodel_set(_body(body))


@api.patch("/config/generation", tags=["configuration"])
async def set_generation_config(body: GenerationConfigUpdate) -> dict[str, Any]:
    payload = _preserve_secrets(body.model_dump(exclude_none=True))
    return _redact(await core.api_genconfig_set(payload))


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
    return _redact(await core.api_resources_config_get())


@api.patch("/resources/config", tags=["resources"])
async def set_resource_config(body: ResourceConfigUpdate) -> dict[str, Any]:
    payload = _preserve_secrets(body.model_dump(exclude_none=True))
    return _redact(await core.api_resources_config_set(payload))


@api.get("/watchdog/policy", tags=["automation"])
async def watchdog_policy() -> dict[str, Any]:
    return await core.api_watchdog_threshold_get()


@api.patch("/watchdog/policy", tags=["automation"])
async def set_watchdog_policy(body: WatchdogPolicyUpdate) -> dict[str, Any]:
    return await core.api_watchdog_threshold_set(_body(body))


@api.get("/projects/{project}/watchdog", tags=["automation"])
async def watchdog(project: str) -> dict[str, Any]:
    return await core.api_watchdog_get(project)


@api.put("/projects/{project}/watchdog", tags=["automation"])
async def set_watchdog(project: str, body: WatchdogUpdate) -> dict[str, Any]:
    return await core.api_watchdog_set(_body(body, project=project))


@api.post("/draw-sessions", status_code=201, tags=["storyboard"])
async def draw_session(body: DrawSessionCreate) -> dict[str, Any]:
    return await core.api_draw_session(_body(body))


@api.get("/draw-sessions/{token}", tags=["storyboard"])
async def draw_info(token: str) -> dict[str, Any]:
    return await core.api_draw_info(token)


@api.post("/draw-sessions/{token}/submissions", tags=["storyboard"])
async def submit_draw(token: str, body: DrawSubmit) -> dict[str, Any]:
    return await core.api_draw_submit(token, _body(body))


@api.put("/storyboard/notes", tags=["storyboard"])
async def storyboard_note(body: SketchNote) -> dict[str, Any]:
    return await core.api_grpnote_set(_body(body))


@api.get("/projects/{project}/storyboard/{ep}/{grp}/sketches", tags=["storyboard"])
async def sketches(project: str, ep: str, grp: str) -> list[dict[str, Any]]:
    return await core.api_sketches(project, ep, grp)


@api.delete("/projects/{project}/storyboard/{ep}/{grp}/sketches/{name}", tags=["storyboard"])
async def delete_sketch(project: str, ep: str, grp: str, name: str) -> dict[str, Any]:
    return await core.api_sketch_delete(project, ep, grp, name)


@api.get("/events", tags=["events"])
async def events(
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    try:
        cursor = int(last_event_id or request.query_params.get("after", "0"))
    except ValueError:
        cursor = 0
    queue = core.HUB.subscribe()

    async def stream():
        nonlocal cursor
        try:
            for event in STORE.events_after(cursor):
                cursor = int(event["sequence"])
                yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                    sequence = int(event.get("sequence") or 0)
                    if sequence <= cursor:
                        continue
                    cursor = sequence
                    yield f"id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
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
