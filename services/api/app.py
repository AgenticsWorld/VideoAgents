from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from services.runtime import core, feishu, media_push, netcheck, wechat, whatsapp

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
_shutdown_event: asyncio.Event | None = None


def request_shutdown() -> None:
    """Wake long-lived responses before Uvicorn starts draining connections."""
    if _shutdown_event is not None:
        _shutdown_event.set()


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
    global _bridge_installed, _shutdown_event
    _shutdown_event = asyncio.Event()
    if not _bridge_installed:
        install_runtime_store(STORE)
        _bridge_installed = True
    # 旧版升级一次性迁移:顶栏「语言模型」默认智能分配,按当前引擎完成各 Agent 设置
    try:
        core.migrate_agentmodel_smart_default()
    except Exception as error:  # noqa: BLE001
        print(f"[migrate] 智能分配默认策略迁移失败(忽略):{error}", flush=True)
    # 旧版升级一次性迁移:≤v1.0.20 的对话记忆布尔开关统一重置为缺省额度 16KB
    try:
        core.migrate_agent_memory_default()
    except Exception as error:  # noqa: BLE001
        print(f"[migrate] 对话记忆缺省额度迁移失败(忽略):{error}", flush=True)
    # 服务停机期间 DAG 可能被外部 Agent 更新；启动即补核对一次，不依赖自动运行开关。
    # ensure 只创建持久签字单，不会把人工 gate 自动置为 passed。
    for project_dir in sorted(core.PROJECTS_DIR.iterdir()):
        if not project_dir.is_dir() or project_dir.name.startswith("."):
            continue
        try:
            await core.ensure_human_gate_approvals(project_dir.name)
        except Exception as error:  # noqa: BLE001
            print(f"[approval] 启动核对 {project_dir.name} 失败(忽略):{error}", flush=True)
    watchdog = asyncio.create_task(core.idle_watchdog())
    relays = [asyncio.create_task(m.relay_loop())
              for m in (wechat, feishu, whatsapp, media_push)]
    try:
        yield
    finally:
        request_shutdown()
        watchdog.cancel()
        for task in relays:
            task.cancel()
        await asyncio.gather(watchdog, *relays, return_exceptions=True)
        await core.shutdown_runtime()


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


@api.post("/projects/{project}/copy", tags=["projects"])
async def copy_project(project: str, body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_projects_copy(body)


@api.get("/projects/{project}/copy", tags=["projects"])
async def copy_project_status(project: str) -> dict[str, Any]:
    return await core.api_projects_copy_status(project)


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


@api.get("/projects/{project}/prompt-skill", tags=["projects"])
async def get_prompt_skill(project: str) -> dict[str, Any]:
    return await core.api_prompt_skill_get(project)


@api.post("/projects/{project}/prompt-skill", tags=["projects"])
async def set_prompt_skill(project: str, body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_prompt_skill_set({**body, "project": project})


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
        "creatures": lambda: core.api_preview_creatures(project),
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


@api.get("/engines/pi/models", tags=["configuration"])
async def pi_models(refresh: bool = False) -> dict[str, Any]:
    return await core.api_pi_models(refresh)


@api.get("/engines/opencode/models", tags=["configuration"])
async def opencode_models(refresh: bool = False) -> dict[str, Any]:
    return await core.api_opencode_models(refresh)


@api.get("/engines/grok/models", tags=["configuration"])
async def grok_models(refresh: bool = False) -> dict[str, Any]:
    return await core.api_grok_models(refresh)


@api.get("/providers/openrouter/models", tags=["providers"])
async def openrouter_models(modality: str = "image", refresh: bool = False) -> dict[str, Any]:
    return await core.api_openrouter_models(modality, refresh)


@api.post("/providers/openrouter/test", tags=["providers"])
async def test_openrouter(body: ProviderProbe) -> dict[str, Any]:
    return await core.api_test_openrouter(body.model_dump())


@api.post("/providers/comfyui/test", tags=["providers"])
async def test_comfyui(body: ProviderProbe) -> dict[str, Any]:
    return await core.api_test_comfyui(body.model_dump())


@api.post("/providers/digital-human/test", tags=["providers"])
async def test_digital_human(body: ProviderProbe) -> dict[str, Any]:
    return await core.api_test_digitalhuman(body.model_dump())


@api.post("/providers/runninghub/workflow", tags=["providers"])
async def rh_workflow_verify(body: ProviderProbe) -> dict[str, Any]:
    return await core.api_rh_workflow_verify(body.model_dump())


@api.get("/comfy/workflows", tags=["providers"])
async def comfy_workflows() -> dict[str, Any]:
    return await core.api_comfy_workflows()


@api.get("/comfy/workflows/doc", tags=["providers"])
async def comfy_workflow_doc(name: str) -> dict[str, Any]:
    return await core.api_comfy_workflow_doc(name)


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


@api.post("/avatar-assets/list", tags=["avatar-assets"])
async def avatar_assets_list(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_avatar_list(body)


@api.post("/avatar-assets/delete", tags=["avatar-assets"])
async def avatar_assets_delete(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_avatar_delete(body)


@api.post("/avatar-assets/clear", tags=["avatar-assets"])
async def avatar_assets_clear(body: dict[str, Any]) -> dict[str, Any]:
    """清空虚拟人像库全部素材(不可恢复)。"""
    return await core.api_avatar_clear(body)


@api.post("/avatar-assets/upload", tags=["avatar-assets"])
async def avatar_assets_upload(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_avatar_upload(body)


@api.post("/avatar-assets/status", tags=["avatar-assets"])
async def avatar_assets_status(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_avatar_status(body)


@api.get("/avatar-assets/ready", tags=["avatar-assets"])
async def avatar_assets_ready() -> dict[str, Any]:
    """入库前置自检:文件托管配置 + 存储 SDK 可导入(方舟 CreateAsset 只收公网 URL)。"""
    return await core.api_avatar_ready()


@api.post("/providers/minimax/voices", tags=["providers"])
async def minimax_voices(body: ProviderProbe) -> dict[str, Any]:
    return await core.api_minimax_voices(body.model_dump())


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


@api.get("/config/agent-advanced", tags=["automation"])
async def agent_advanced_get() -> dict[str, Any]:
    return await core.api_agent_advanced_get()


@api.post("/config/agent-advanced", tags=["automation"])
async def agent_advanced_set(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_agent_advanced_set(body)


@api.get("/config/skills", tags=["automation"])
async def skills_get(refresh: bool = False) -> dict[str, Any]:
    return await core.api_skills_get(refresh)


@api.get("/config/skills/text", tags=["automation"])
async def skills_text(id: str) -> dict[str, Any]:
    return await core.api_skills_text(id)


@api.post("/config/skills", tags=["automation"])
async def skills_set(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_skills_set(body)


@api.post("/config/skills/upload", tags=["automation"])
async def skills_upload(request: Request, agent: str, filename: str = "") -> dict[str, Any]:
    return await core.api_skills_upload(agent, await request.body(), filename)


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


@api.get("/diagnostics", tags=["diagnostics"])
async def diagnostics_summary() -> dict[str, Any]:
    return await core.api_diagnostics_get()


@api.post("/config/diagnostics", tags=["diagnostics"])
async def set_diagnostics(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_diagnostics_set(body)


@api.get("/diagnostics/lessons", tags=["diagnostics"])
async def diagnostics_lessons() -> dict[str, Any]:
    return await core.api_diagnostics_lessons()


@api.post("/diagnostics/clear", tags=["diagnostics"])
async def diagnostics_clear() -> dict[str, Any]:
    return await core.api_diagnostics_clear()


@api.post("/diagnostics/export", tags=["diagnostics"])
async def diagnostics_export(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_diagnostics_export(body)


@api.get("/diagnostics/export/{name}", tags=["diagnostics"])
async def diagnostics_export_download(name: str) -> FileResponse:
    # 仓库首个出站文件下载端点:文件名格式白名单 + 仅限 telemetry/export 目录
    path = core.diagnostics_export_path(name)
    return FileResponse(path, media_type="application/zip", filename=name)


@api.get("/network/proxy", tags=["network"])
async def network_proxy() -> dict[str, Any]:
    return await asyncio.to_thread(netcheck.api_proxy)


@api.post("/network/probe", tags=["network"])
async def network_probe(body: dict[str, Any]) -> dict[str, Any]:
    return await asyncio.to_thread(netcheck.api_probe, body)


@api.post("/network/ip", tags=["network"])
async def network_ip(body: dict[str, Any]) -> dict[str, Any]:
    return await asyncio.to_thread(netcheck.api_ip, body)


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


@api.get("/storyboard/sketchgen", tags=["storyboard"])
async def sketchgen_status(project: str = "", ep: str = "", grp: str = "") -> dict[str, Any]:
    return await core.api_sketchgen_status(project, ep, grp)


@api.post("/storyboard/notes", tags=["storyboard"])
async def storyboard_note(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_grpnote_set(body)


@api.post("/storyboard/prompt", tags=["storyboard"])
async def storyboard_prompt(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_grpprompt_set(body)


@api.post("/storyboard/refs", tags=["storyboard"])
async def storyboard_ref(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_grpref_add(body)


@api.post("/storyboard/refs/delete", tags=["storyboard"])
async def storyboard_ref_delete(body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_grpref_delete(body)


@api.post("/storyboard/refs/upload", tags=["storyboard"])
async def storyboard_ref_upload(
    request: Request,
    project: str = "",
    ep: str = "",
    grp: str = "",
    filename: str = "",
) -> dict[str, Any]:
    data = await request.body()
    return await core.api_grpref_upload(data, project, ep, grp, filename)


@api.get("/projects/{project}/storyboard/{ep}/{grp}/settings", tags=["storyboard"])
async def storyboard_group_settings_get(project: str, ep: str, grp: str) -> dict[str, Any]:
    """组级视频模型/提示词技能覆盖(分镜预览「🎛 模型」弹窗)。"""
    return await core.api_grpsettings_get(project, ep, grp)


@api.post("/projects/{project}/storyboard/{ep}/{grp}/settings", tags=["storyboard"])
async def storyboard_group_settings_set(project: str, ep: str, grp: str, body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_grpsettings_set({**(body or {}), "project": project, "ep": ep, "grp": grp})


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


@api.get("/feishu/status", tags=["feishu"])
async def feishu_status() -> dict[str, Any]:
    return await feishu.api_feishu_status()


@api.post("/feishu/bind", tags=["feishu"])
async def feishu_bind(body: dict[str, Any]) -> dict[str, Any]:
    return await feishu.api_feishu_bind(body)


@api.post("/feishu/unbind", tags=["feishu"])
async def feishu_unbind(body: dict[str, Any]) -> dict[str, Any]:
    return await feishu.api_feishu_unbind()


@api.get("/whatsapp/status", tags=["whatsapp"])
async def whatsapp_status() -> dict[str, Any]:
    return await whatsapp.api_whatsapp_status()


@api.post("/whatsapp/bind", tags=["whatsapp"])
async def whatsapp_bind(body: dict[str, Any]) -> dict[str, Any]:
    return await whatsapp.api_whatsapp_bind()


@api.post("/whatsapp/unbind", tags=["whatsapp"])
async def whatsapp_unbind(body: dict[str, Any]) -> dict[str, Any]:
    return await whatsapp.api_whatsapp_unbind()


@api.get("/events", tags=["events"])
async def events() -> StreamingResponse:
    queue = core.HUB.subscribe()
    shutdown = _shutdown_event or asyncio.Event()

    async def stream():
        try:
            yield 'data: {"type":"hello"}\n\n'
            while not shutdown.is_set():
                event_task = asyncio.create_task(queue.get())
                stop_task = asyncio.create_task(shutdown.wait())
                waiters = {event_task, stop_task}
                try:
                    done, _ = await asyncio.wait(
                        waiters, timeout=15,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    pending = {task for task in waiters if not task.done()}
                    for task in pending:
                        task.cancel()
                    if pending:
                        await asyncio.gather(*pending, return_exceptions=True)
                if stop_task in done:
                    break
                if event_task in done:
                    event = event_task.result()
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                else:
                    yield ": ping\n\n"
        finally:
            core.HUB.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------- 素材库(设置→高级→素材库;data/footage/<name>/) ----------------

@api.get("/footage/projects", tags=["footage"])
async def footage_list() -> dict[str, Any]:
    return await core.api_footage_list()


@api.post("/footage/projects", tags=["footage"])
async def footage_create(body: dict[str, Any] | None = None) -> dict[str, Any]:
    return await core.api_footage_create(body or {})


@api.get("/footage/projects/{name}", tags=["footage"])
async def footage_get(name: str) -> dict[str, Any]:
    return await core.api_footage_get(name)


@api.delete("/footage/projects/{name}", tags=["footage"])
async def footage_delete(name: str) -> dict[str, Any]:
    return await core.api_footage_delete(name)


@api.post("/footage/projects/{name}/upload", tags=["footage"])
async def footage_upload(
    name: str, request: Request, upload_id: str = "", index: int = 0, total: int = 1,
    filename: str = "video.mp4",
) -> dict[str, Any]:
    """分块上传:请求体即分块原始字节(与参考文件上传同款,避免 multipart 依赖)。"""
    data = await request.body()
    return await core.api_footage_upload(name, data, upload_id, index, total, filename)


@api.post("/footage/projects/{name}/settings", tags=["footage"])
async def footage_settings_set(name: str, body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_footage_settings_set(name, body)


@api.post("/footage/projects/{name}/download", tags=["footage"])
async def footage_download(name: str, body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_footage_download(name, body)


@api.post("/footage/projects/{name}/reprocess", tags=["footage"])
async def footage_reprocess(name: str) -> dict[str, Any]:
    return await core.api_footage_reprocess(name)


@api.post("/footage/projects/{name}/transcribe", tags=["footage"])
async def footage_transcribe(name: str) -> dict[str, Any]:
    return await core.api_footage_retranscribe(name)


@api.post("/footage/projects/{name}/cancel", tags=["footage"])
async def footage_cancel(name: str) -> dict[str, Any]:
    return await core.api_footage_cancel(name)


@api.post("/footage/projects/{name}/analyze-all", tags=["footage"])
async def footage_analyze_all(name: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    return await core.api_footage_analyze_all(name, body or {})


@api.post("/footage/projects/{name}/clips/{clip_id}", tags=["footage"])
async def footage_clip_update(name: str, clip_id: str, body: dict[str, Any]) -> dict[str, Any]:
    return await core.api_footage_clip_update(name, clip_id, body)


@api.post("/footage/projects/{name}/clips/{clip_id}/analyze", tags=["footage"])
async def footage_clip_analyze(name: str, clip_id: str) -> dict[str, Any]:
    return await core.api_footage_clip_analyze(name, clip_id)


@api.get("/footage/projects/{name}/files/{file_path:path}", tags=["footage"])
async def footage_file(name: str, file_path: str) -> FileResponse:
    return FileResponse(core.footage_file_path(name, file_path))


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
