from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(ApiModel):
    status: Literal["ok"] = "ok"
    version: str
    api_version: Literal["v1"] = "v1"


class CapabilityResponse(ApiModel):
    local_backend: bool
    desktop_actions: bool = False
    events: Literal["sse"] = "sse"
    project_storage: Literal["filesystem"] = "filesystem"
    operational_storage: Literal["sqlite"] = "sqlite"


class ProjectCreate(ApiModel):
    name: str = Field(min_length=1, max_length=80)
    novel: str = ""
    brief: str = ""
    style: str = ""
    # "auto" = 每集时长由剧本结构自动决定(不设固定预算)
    episode_minutes: Annotated[float, Field(gt=0, le=240)] | Literal["auto"] = 10
    shot_min_s: float = Field(default=1, gt=0, le=60)
    shot_max_s: float = Field(default=10, gt=0, le=60)


class ProjectDelete(ApiModel):
    confirmation: str


class BriefUpdate(ApiModel):
    brief: str = ""
    style: str = ""


class GenerationConfigUpdate(BaseModel):
    """Provider configuration is extensible by design."""

    model_config = ConfigDict(extra="allow")
    project: str | None = None


class ProjectConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")


class AgentModelUpdate(ApiModel):
    agent: str
    config: dict[str, Any] | None = None
    reset: bool = False


class GlobalModelUpdate(ApiModel):
    engine: Literal["", "claude", "codex", "kimi", "pi", "opencode", "grok", "deepagents"] = ""
    model: str = ""


class PluginUpdate(ApiModel):
    enabled: bool


class RunCreate(ApiModel):
    agent: str
    message: str = Field(min_length=1)
    project: str = "demo"
    engine: Literal["claude", "codex", "kimi", "pi", "opencode", "grok", "deepagents"] | None = None
    model: str | None = None
    force: bool = False
    source: str = "user"
    parent: str | None = None


class RunCreated(ApiModel):
    run_id: str


class RunProgress(ApiModel):
    note: str = Field(max_length=300)


class ApprovalCreate(ApiModel):
    question: str = Field(min_length=1, max_length=500)
    options: list[str] | None = None
    default: str | None = None
    timeout: int | None = Field(default=None, ge=5, le=14400)
    kind: Literal["confirm", "sign"] = "confirm"
    parent: str | None = None


class ApprovalCreated(ApiModel):
    approval_id: str


class ApprovalAnswer(ApiModel):
    answer: str = Field(min_length=1, max_length=40)


class ReferenceNote(ApiModel):
    path: str
    note: str = Field(max_length=4000)


class ReferenceDelete(ApiModel):
    path: str


class SketchNote(ApiModel):
    project: str
    ep: str
    grp: str
    text: str = ""


class DrawSessionCreate(ApiModel):
    project: str
    ep: str
    grp: str


class DrawSubmit(ApiModel):
    image: str
    text: str = ""


class ProviderProbe(BaseModel):
    model_config = ConfigDict(extra="allow")


class WatchdogUpdate(ApiModel):
    enabled: bool


class WatchdogPolicyUpdate(ApiModel):
    threshold: int | None = Field(default=None, ge=1, le=100)
    idle_minutes: int | None = Field(default=None, ge=1, le=720)


class VersionClone(ApiModel):
    snapshot_commit: str = Field(pattern=r"^[0-9a-fA-F]{40}$")
    task_id: str = ""


class ResourceConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claude_probe: bool | None = None
    codex_probe: bool | None = None
    kimi_probe: bool | None = None
    kimi_api_key: str | None = Field(default=None, max_length=500)
    openrouter_key: str | None = Field(default=None, max_length=500)
    volc_enabled: bool | None = None
    volc_ak: str | None = Field(default=None, max_length=500)
    volc_sk: str | None = Field(default=None, max_length=500)
