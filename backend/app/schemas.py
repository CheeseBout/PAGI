"""Request/response bodies for the REST API (SPEC §2)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class AgentCreate(BaseModel):
    name: str
    system_prompt: str = ""
    provider: str
    model: str
    tools_allowed: list[str] = Field(default_factory=list)
    tool_policy: dict[str, str] = Field(default_factory=dict)
    temperature: float = 0.7
    max_tokens: int = 4096
    is_default: bool = False
    vision_enabled: bool = True
    kb_collection_ids: list[str] = Field(default_factory=list)
    rag_config: dict = Field(default_factory=dict)
    is_delegatable: bool = False
    delegate_description: str = ""
    orchestration: dict = Field(default_factory=dict)
    avatar_config: dict = Field(default_factory=dict)


class AgentUpdate(BaseModel):
    name: str | None = None
    system_prompt: str | None = None
    provider: str | None = None
    model: str | None = None
    tools_allowed: list[str] | None = None
    tool_policy: dict[str, str] | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    is_default: bool | None = None
    vision_enabled: bool | None = None
    kb_collection_ids: list[str] | None = None
    rag_config: dict | None = None
    is_delegatable: bool | None = None
    delegate_description: str | None = None
    orchestration: dict | None = None
    avatar_config: dict | None = None


# ── RAG / Knowledge Base (Phase 12, SPEC §2.9) ────────────────────────
class KbCollectionCreate(BaseModel):
    name: str
    description: str = ""
    embedding_model: str | None = None
    config: dict = Field(default_factory=dict)


class KbCollectionPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    config: dict | None = None


class KbDocFromUrl(BaseModel):
    url: str
    title: str | None = None
    meta: dict = Field(default_factory=dict)


class KbDocFromText(BaseModel):
    title: str
    text: str
    meta: dict = Field(default_factory=dict)


class KbReingest(BaseModel):
    force: bool = False


class KbSearchRequest(BaseModel):
    query: str
    collection_ids: list[str]
    config: dict | None = None
    explain: bool = False


class KbEvalGenerate(BaseModel):
    n: int = Field(default=30, ge=1, le=200)
    model: str | None = None


class KbEvalCaseIn(BaseModel):
    question: str
    ground_truth: str | None = None


class KbEvalRunCreate(BaseModel):
    collection_id: str
    name: str = ""
    agent_id: str | None = None
    cases: list[KbEvalCaseIn]
    config: dict | None = None
    judge_model: str | None = None


# ── Agent evaluation (Phase 16) ──────────────────────────────────────
class AgentEvalCaseIn(BaseModel):
    prompt: str
    expected_outcome: str | None = None
    optimal_steps: int | None = None
    forbidden_tools: list[str] = Field(default_factory=list)
    # held out of Weakness Miner's input pool (Phase 17) — a fair regression
    # check needs cases the miner never saw.
    held_out: bool = False


class AgentEvalRunCreate(BaseModel):
    name: str = ""
    agent_id: str
    pattern: str | None = None  # override the agent's own pattern for this run
    judge_model: str | None = None
    cases: list[AgentEvalCaseIn]


class AgentEvalSynth(BaseModel):
    agent_id: str
    n: int = Field(default=8, ge=1, le=30)
    model: str | None = None


class ConversationCreate(BaseModel):
    agent_id: str


class OverlaySessionRequest(BaseModel):
    agent_id: str | None = None  # default: the is_default agent


class ConversationPatch(BaseModel):
    title: str | None = None
    archived: bool | None = None


class MessageCreate(BaseModel):
    content: str = ""
    attachment_ids: list[str] = Field(default_factory=list)


class McpServerCreate(BaseModel):
    name: str
    transport: str
    config: dict


class McpServerPatch(BaseModel):
    name: str | None = None
    transport: str | None = None
    config: dict | None = None
    enabled: bool | None = None


class CronJobCreate(BaseModel):
    agent_id: str
    name: str
    schedule: str
    prompt: str
    unattended_allowed_tools: list[str] = Field(default_factory=list)
    enabled: bool = True


class CronJobPatch(BaseModel):
    agent_id: str | None = None
    name: str | None = None
    schedule: str | None = None
    prompt: str | None = None
    unattended_allowed_tools: list[str] | None = None
    enabled: bool | None = None


# ── Multi-day project development loop (Phase 18) ─────────────────────
class ProjectRunCreate(BaseModel):
    name: str
    planner_agent_id: str
    developer_agent_id: str
    qa_agent_id: str
    max_iterations: int = Field(default=10, ge=1, le=1000)
    budget_usd: float = Field(default=0.0, ge=0)
    schedule: str | None = None  # cron expr; None = manual-trigger-only


class ProjectRunPatch(BaseModel):
    max_iterations: int | None = Field(default=None, ge=1, le=1000)
    budget_usd: float | None = Field(default=None, ge=0)
    schedule: str | None = None
